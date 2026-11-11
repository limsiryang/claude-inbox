"""Inbox unit tests: python3 -m unittest discover tests"""
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'inbox'))
import inbox  # noqa: E402

POSIX_MODES = os.name != 'nt'  # Windows ignores POSIX mode bits; st_mode is always 0o666/0o777
REAL_ENSURE_SERVER = inbox.ensure_server  # Base swaps in a stub, so keep the real one here


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / 'proj'
        self.project.mkdir()
        self.env = mock.patch.dict(os.environ, {'CLAUDE_INBOX_HOME': str(Path(self.tmp.name) / 'home'),
                                                'CLAUDE_CODE_SESSION_ID': 'sess-1'})
        self.env.start()
        self.cwd = os.getcwd()
        os.chdir(self.project)
        self.no_server = mock.patch.object(inbox, 'ensure_server', return_value=True)
        self.no_server.start()

    def tearDown(self):
        self.no_server.stop()
        os.chdir(self.cwd)
        self.env.stop()
        self.tmp.cleanup()

    def assertMode(self, path, expected):
        if POSIX_MODES:
            self.assertEqual(oct(Path(path).stat().st_mode & 0o777), expected)

    def ask(self, *argv):
        with redirect_stdout(io.StringIO()) as out:
            inbox.main(['ask'] + list(argv))
        return out.getvalue().split('Added to inbox: ')[1].split()[0]

    def answer(self, qid, choice='A', text=''):
        questions = {q['id']: q for q in inbox.load_questions()}
        events = inbox.validate_answers({'answers': [{'question_id': qid, 'choice': choice, 'text': text}]}, questions)
        with inbox.EventLog() as log:
            log.append(events)
        return events[0]


class AskAndAnswer(Base):
    def test_ask_writes_question_with_session_and_project(self):
        qid = self.ask('--title', 'Which one?', '--option', 'A=first', '--option', 'B=second', '--recommend', 'B')
        q = inbox.load_questions()[0]
        self.assertEqual(q['id'], qid)
        self.assertEqual(q['session'], 'sess-1')
        self.assertEqual(q['project'], os.path.realpath(str(self.project)))
        self.assertEqual([o['recommended'] for o in q['options']], [False, True])

    def test_bad_option_and_bad_recommend_rejected(self):
        with self.assertRaises(SystemExit):
            self.ask('--title', 't', '--option', 'A')
        with self.assertRaises(SystemExit):
            self.ask('--title', 't', '--option', 'A=first', '--recommend', 'Z')

    def test_validate_rejects_unknown_choice_empty_answer_and_long_text(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        qs = {q['id']: q for q in inbox.load_questions()}
        for bad in ({'question_id': qid, 'choice': 'Z'}, {'question_id': qid},
                    {'question_id': qid, 'text': 'x' * 4001}, {'question_id': 'nope', 'choice': 'A'}):
            with self.assertRaises(ValueError):
                inbox.validate_answers({'answers': [bad]}, qs)

    def test_info_question_only_accepts_ack(self):
        qid = self.ask('--info', '--title', 'heads up')
        qs = {q['id']: q for q in inbox.load_questions()}
        self.assertEqual(inbox.validate_answers({'answers': [{'question_id': qid}]}, qs)[0]['choice'], 'ack')
        with self.assertRaises(ValueError):
            inbox.validate_answers({'answers': [{'question_id': qid, 'choice': 'A'}]}, qs)


class Delivery(Base):
    def test_answer_goes_to_asking_session_once(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        self.answer(qid, 'A', 'some notes')
        self.assertEqual(inbox.take('sess-2', str(self.project)), [])  # another session can't have it
        pairs = inbox.take('sess-1', str(self.project))
        self.assertEqual(len(pairs), 1)
        self.assertIn('User chose: A first', inbox.format_answers(pairs))
        self.assertIn('Notes: some notes', inbox.format_answers(pairs))
        self.assertEqual(inbox.take('sess-1', str(self.project)), [])  # delivered only once

    def test_asking_session_gets_answer_even_from_another_directory(self):
        sub_dir = self.project / 'a' / 'b'
        sub_dir.mkdir(parents=True)
        os.chdir(sub_dir)  # Not a git folder: ask and hook run from different cwds, so different project roots
        qid = self.ask('--title', 't', '--option', 'A=first')
        self.answer(qid)
        self.assertEqual(len(inbox.take('sess-1', str(self.project))), 1)

    def test_other_project_cannot_take_over_even_old_answers_or_empty_sessions(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        event = self.answer(qid)
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        later = datetime.fromisoformat(event['at']).timestamp() + inbox.ORPHAN_SECONDS + 1
        with mock.patch.object(inbox.time, 'time', return_value=later):
            self.assertEqual(inbox.take('sess-2', str(other)), [])  # even after 30 min, same project only
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': ''}):
            anon = self.ask('--title', 'anonymous', '--option', 'A=first')
        self.answer(anon)
        self.assertEqual(inbox.take('', str(other)), [])  # empty session != empty session

    def test_other_project_never_gets_it(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        self.answer(qid)
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        self.assertEqual(inbox.take('sess-2', str(other)), [])  # different project, different session

    def test_orphan_answer_goes_to_same_project_after_30_minutes(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        event = self.answer(qid)
        self.assertEqual(inbox.take('sess-2', str(self.project)), [])
        later = datetime.fromisoformat(event['at']).timestamp() + inbox.ORPHAN_SECONDS + 1
        with mock.patch.object(inbox.time, 'time', return_value=later):
            self.assertEqual(len(inbox.take('sess-2', str(self.project))), 1)

    def test_hook_reads_stdin_and_prints_answers(self):
        qid = self.ask('--title', 'question title', '--option', 'A=first')
        self.answer(qid)
        stdin = io.StringIO(json.dumps({'session_id': 'sess-1', 'cwd': str(self.project)}))
        with mock.patch.object(sys, 'stdin', stdin), redirect_stdout(io.StringIO()) as out:
            inbox.main(['hook'])
        self.assertIn('question title', out.getvalue())
        with mock.patch.object(sys, 'stdin', io.StringIO('{}')), redirect_stdout(io.StringIO()) as out:
            inbox.main(['hook'])
        self.assertEqual(out.getvalue(), '')  # prints nothing when there are no new replies

    def test_close_hides_question_from_list(self):
        qid = self.ask('--title', 'item to close')
        with redirect_stdout(io.StringIO()):
            inbox.main(['close', qid])
        with redirect_stdout(io.StringIO()) as out:
            inbox.main(['list'])
        self.assertNotIn('item to close', out.getvalue())
        self.assertTrue(inbox.build_state()['questions'][0]['closed'])


class Server(Base):
    def start(self, host='127.0.0.1', token=''):
        port = free_port()
        inbox.Handler.config = {'host': host, 'port': port, 'token': token}
        httpd = ThreadingHTTPServer((host, port), inbox.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        return port

    def req(self, port, path, data=None, headers=None):
        h = {'Content-Type': 'application/json'}
        h.update(headers or {})
        r = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
                                   data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_answer_round_trip(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        port = self.start()
        self.assertEqual(self.req(port, '/')[0], 200)
        code, body = self.req(port, '/api/answer', {'answers': [{'question_id': qid, 'choice': 'A'}]})
        self.assertEqual((code, json.loads(body)), (200, {'stored': 1}))
        state = json.loads(self.req(port, '/api/state')[1])
        self.assertEqual(state['questions'][0]['answers'][0]['choice'], 'A')

    def test_bad_host_origin_and_invalid_answer_rejected(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        port = self.start()
        self.assertEqual(self.req(port, '/api/state', headers={'Host': 'evil.example:80'})[0], 403)
        self.assertEqual(self.req(port, '/api/answer', {'answers': [{'question_id': qid, 'choice': 'A'}]},
                                  headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.req(port, '/api/answer', {'answers': [{'question_id': qid, 'choice': 'Z'}]})[0], 400)
        self.assertEqual(inbox.read_events(), [])

    def test_public_host_allowed_over_https_only(self):
        qid = self.ask('--title', 't', '--option', 'A=first')
        port = self.start(token='secret-token')
        inbox.Handler.config['public_hosts'] = ['inbox.example.org']
        tok = {'X-Inbox-Token': 'secret-token'}
        pub = dict(tok, Host='inbox.example.org')
        self.assertEqual(self.req(port, '/', headers={'Host': 'inbox.example.org'})[0], 200)
        self.assertEqual(self.req(port, '/api/state', headers={'Host': 'inbox.example.org'})[0], 401)
        self.assertEqual(self.req(port, '/api/state', headers={'Host': 'other.example.org'})[0], 403)
        self.assertEqual(self.req(port, '/api/answer', {'answers': [{'question_id': qid, 'choice': 'A'}]},
                                  headers=dict(pub, Origin='http://inbox.example.org'))[0], 403)
        self.assertEqual(self.req(port, '/api/answer', {'answers': [{'question_id': qid, 'choice': 'A'}]},
                                  headers=dict(pub, Origin='https://evil.example'))[0], 403)
        self.assertEqual(self.req(port, '/api/answer', {'answers': [{'question_id': qid, 'choice': 'A'}]},
                                  headers=dict(pub, Origin='https://inbox.example.org'))[0], 200)
        # The local address keeps working alongside it
        self.assertEqual(self.req(port, '/api/state', headers=tok)[0], 200)

    def test_public_host_not_allowed_unless_configured(self):
        port = self.start()
        self.assertEqual(self.req(port, '/', headers={'Host': 'inbox.example.org'})[0], 403)

    def test_token_required_when_configured(self):
        port = self.start(token='secret-token')
        self.assertEqual(self.req(port, '/api/state')[0], 401)
        self.assertEqual(self.req(port, '/api/state', headers={'X-Inbox-Token': 'secret-token'})[0], 200)

    def test_other_program_returning_200_is_not_taken_for_inbox(self):
        from http.server import BaseHTTPRequestHandler

        class Fake(BaseHTTPRequestHandler):
            body = b'<html>spa fallback</html>'

            def do_GET(self):
                body = Fake.body
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        port = free_port()
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Fake)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        self.assertFalse(inbox.server_alive({'host': '127.0.0.1', 'port': port, 'token': ''}))
        for body in (b'{}', b'["questions"]', b'"questions"'):
            Fake.body = body
            self.assertFalse(inbox.server_alive({'host': '127.0.0.1', 'port': port, 'token': ''}))
        Fake.body = b'<html>spa fallback</html>'
        real = self.start()
        self.assertTrue(inbox.server_alive({'host': '127.0.0.1', 'port': real, 'token': ''}))

    def test_background_server_log_never_contains_token(self):
        import signal
        port = free_port()
        inbox.home()
        (Path(os.environ['CLAUDE_INBOX_HOME']) / 'config.json').write_text(json.dumps({'port': port}))
        config = inbox.load_config()
        pid_file = Path(os.environ['CLAUDE_INBOX_HOME']) / 'server.pid'

        try:
            with mock.patch.object(inbox, 'ensure_server', REAL_ENSURE_SERVER):
                self.assertTrue(inbox.ensure_server(config))
            log = (Path(os.environ['CLAUDE_INBOX_HOME']) / 'server.log').read_text(encoding='utf-8')
            self.assertIn('Inbox server started', log)
            self.assertNotIn(config['token'], log)
        finally:  # Must clean up inside the test body: tearDown deletes the temp dir and PID file first
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGTERM)
                except (OSError, ValueError):
                    pass

    def test_loopback_default_also_gets_private_token_and_folder(self):
        old = os.umask(0o022)  # Test under a permissive umask, so loosened permissions actually fail
        self.addCleanup(os.umask, old)
        config = inbox.load_config()
        path = Path(os.environ['CLAUDE_INBOX_HOME']) / 'config.json'
        self.assertTrue(config['token'])
        self.assertMode(path, '0o600')
        self.assertMode(path.parent, '0o700')
        self.assertEqual(inbox.load_config()['token'], config['token'])  # not regenerated the second time
        qid = self.ask('--title', 't')
        self.answer(qid, choice='', text='x')
        self.assertMode(inbox.events_path(), '0o600')
        self.assertMode(path.parent / 'questions', '0o700')

    def test_non_loopback_host_gets_token_generated(self):
        path = Path(os.environ['CLAUDE_INBOX_HOME']) / 'config.json'
        inbox.home()
        path.write_text(json.dumps({'host': '100.64.0.1'}))
        config = inbox.load_config()
        self.assertTrue(config['token'])
        self.assertMode(path, '0o600')
        self.assertIn('?t=' + config['token'], inbox.page_url(config))


if __name__ == '__main__':
    unittest.main()
