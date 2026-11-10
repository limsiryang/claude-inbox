#!/usr/bin/env python3
"""Inbox: lets a Claude Code agent collect the decisions it needs from you on one web page,
and delivers your answers straight back to the agent.

Standard library only (Python 3.9+). Data lives in $CLAUDE_INBOX_HOME (default ~/.claude-inbox).
"""
import argparse
import ipaddress
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:  # File locking: flock on POSIX; Windows has no fcntl, so fall back to msvcrt
    import fcntl
    msvcrt = None
except ImportError:  # Windows
    fcntl = None
    import msvcrt


def force_utf8():
    """On Windows, stdout follows the system ANSI codepage, so any non-ASCII text
    (question titles, answers) raises UnicodeEncodeError the moment stdout is a file
    or a pipe -- and the hook is always a pipe."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


HERE = Path(__file__).resolve().parent
PAGE = HERE / 'page.html'
DEFAULT_PORT = 7799
MAX_BODY = 64 * 1024
MAX_TEXT = 4000
ORPHAN_SECONDS = 30 * 60  # If the asking session hasn't collected in 30 min, other sessions in the same project may


# ---------- File locking ----------

class FileLock:
    """Cross-platform exclusive lock. Locks a separate .lock file rather than the data file
    itself: Windows locks are mandatory, so locking the data file would block even ordinary
    reads and writes."""

    def __init__(self, path):
        self.path = Path(path)
        self.fd = None

    def acquire(self):
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        if fcntl is not None:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return
        while True:
            try:
                msvcrt.locking(self.fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:  # LK_LOCK only waits ten seconds on its own; queue up again
                time.sleep(0.1)

    def release(self):
        if self.fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            else:
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


# ---------- Paths and config ----------

def home():
    root = Path(os.environ.get('CLAUDE_INBOX_HOME') or Path.home() / '.claude-inbox')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / 'questions').mkdir(mode=0o700, exist_ok=True)
    return root


def read_config(path):
    config = {'host': '127.0.0.1', 'port': DEFAULT_PORT, 'token': ''}
    if path.exists():
        config.update(json.loads(path.read_text(encoding='utf-8')))
    return config


def load_config():
    path = home() / 'config.json'
    config = read_config(path)
    if not config.get('token'):
        with FileLock(home() / 'config.lock'):
            config = read_config(path)  # Re-read once we hold the lock: someone may have generated it
            if not config.get('token'):
                config['token'] = secrets.token_urlsafe(24)
                write_json(path, config)
    return config


def is_loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def write_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.')  # Mode 600 by default
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2))
    for attempt in range(10):  # Windows: replace fails while someone else has the target open
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                os.unlink(tmp)
                raise
            time.sleep(0.05)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def project_of(cwd):
    try:
        out = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.realpath(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.realpath(str(cwd))


def base_url(config):
    host = 'localhost' if config['host'] == '127.0.0.1' else config['host']
    return 'http://%s:%s/' % (host, config['port'])


def page_url(config):
    return base_url(config) + ('?t=' + config['token'] if config.get('token') else '')


# ---------- Questions and events ----------

def load_questions():
    items = []
    for path in sorted((home() / 'questions').glob('*.json')):
        try:
            items.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
    return items


def events_path():
    return home() / 'events.jsonl'


class EventLog:
    """All reads and writes of events.jsonl, wrapped in one file lock."""

    def __enter__(self):
        self.lock = FileLock(home() / 'events.lock')
        self.lock.acquire()
        try:
            fd = os.open(events_path(), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
            self.fh = os.fdopen(fd, 'a+', encoding='utf-8', newline='\n')  # Same file format on both platforms
        except OSError:
            self.lock.release()  # Don't hold the lock until the process exits
            raise
        return self

    def __exit__(self, *exc):
        self.fh.close()
        self.lock.release()

    def read(self):
        self.fh.seek(0)
        out = []
        for line in self.fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def append(self, events):
        for event in events:
            self.fh.write(json.dumps(event, ensure_ascii=False) + '\n')
        self.fh.flush()
        os.fsync(self.fh.fileno())


def read_events():
    if not events_path().exists():
        return []
    with EventLog() as log:
        return log.read()


def build_state(events=None):
    questions = {q['id']: dict(q, answers=[], closed=False) for q in load_questions()}
    delivered = set()
    if events is None:
        events = read_events()
    for e in events:
        if e.get('kind') == 'delivered':
            delivered.add(e.get('ref'))
    for e in events:
        q = questions.get(e.get('question_id'))
        if not q:
            continue
        if e.get('kind') == 'answer':
            q['answers'].append(dict(e, delivered=e['event_id'] in delivered))
        elif e.get('kind') == 'closed':
            q['closed'] = True
    items = sorted(questions.values(), key=lambda q: q['created'])
    return {'questions': items}


def deliverable(session_id, cwd, events=None):
    """Answers this session should get and hasn't collected yet."""
    project = project_of(cwd)
    now = time.time()
    out = []
    for q in build_state(events)['questions']:
        asker = q.get('session')
        own = bool(asker) and asker == session_id  # This session asked it: whatever folder it's in now
        if not own and q.get('project') != project:
            continue
        for a in q['answers']:
            if a['delivered']:
                continue
            age = now - datetime.fromisoformat(a['at']).timestamp()
            if own or not asker or age > ORPHAN_SECONDS:
                out.append((q, a))
    return out


def format_answers(pairs):
    lines = ['Inbox: the user replied to %d item(s).' % len(pairs)]
    for q, a in pairs:
        lines.append('')
        lines.append('[%s] %s' % (q['id'], q['title']))
        if a.get('choice') == 'ack':
            lines.append('User: acknowledged')
        elif a.get('choice'):
            lines.append('User chose: %s %s' % (a['choice'], a.get('choice_label', '')))
        if a.get('text'):
            lines.append('Notes: ' + a['text'])
    lines.append('')
    lines.append('Act on the reply; once the item is done, run inbox close <id> to clear it.')
    return '\n'.join(lines)


def take(session_id, cwd):
    """Collect answers and mark them delivered; done under one lock, so two sessions never get the same one."""
    with EventLog() as log:
        pairs = deliverable(session_id, cwd, log.read())
        log.append([{'event_id': str(uuid.uuid4()), 'kind': 'delivered', 'ref': a['event_id'],
                     'session': session_id, 'at': now_iso()} for _, a in pairs])
    return pairs


def validate_answers(payload, questions):
    answers = payload.get('answers') if isinstance(payload, dict) else None
    if not isinstance(answers, list) or not answers:
        raise ValueError('answers must be a non-empty array')
    events = []
    for item in answers:
        q = questions.get(item.get('question_id')) if isinstance(item, dict) else None
        if not q:
            raise ValueError('no such question')
        choice = item.get('choice') or ''
        text = item.get('text') or ''
        if not isinstance(choice, str) or not isinstance(text, str) or len(text) > MAX_TEXT:
            raise ValueError('bad field format')
        labels = {o['key']: o['label'] for o in q.get('options', [])}
        if q.get('kind') == 'info':
            if choice not in ('', 'ack'):
                raise ValueError('an FYI item can only be acknowledged')
            choice = 'ack'
        elif choice and choice not in labels:
            raise ValueError('no such option')
        if not choice and not text.strip():
            raise ValueError('nothing chosen and no notes')
        events.append({'event_id': str(uuid.uuid4()), 'kind': 'answer', 'question_id': q['id'],
                       'choice': choice, 'choice_label': labels.get(choice, ''),
                       'text': text.strip(), 'at': now_iso()})
    return events


# ---------- Web server ----------

class Handler(BaseHTTPRequestHandler):
    config = None

    def log_message(self, fmt, *args):
        pass

    def allowed_hosts(self):
        port = self.config['port']
        hosts = {'%s:%s' % (self.config['host'], port)}
        if is_loopback(self.config['host']):
            hosts |= {'localhost:%s' % port, '127.0.0.1:%s' % port}
        return hosts

    def check(self, api):
        host = self.headers.get('Host', '')
        if host not in self.allowed_hosts():
            return self.send_text(403, 'Host mismatch')
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            return self.send_text(403, 'Origin mismatch')
        token = self.config.get('token')
        if api and token and not secrets.compare_digest(self.headers.get('X-Inbox-Token', ''), token):
            return self.send_text(401, 'token required')
        return True

    def send_text(self, code, text, ctype='text/plain; charset=utf-8'):
        body = text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/':
            if self.check(api=False):
                self.send_text(200, PAGE.read_text(encoding='utf-8'), 'text/html; charset=utf-8')
        elif path == '/api/state':
            if self.check(api=True):
                self.send_text(200, json.dumps(build_state(), ensure_ascii=False),
                               'application/json; charset=utf-8')
        else:
            self.send_text(404, 'not found')

    def do_POST(self):
        if self.path != '/api/answer':
            return self.send_text(404, 'not found')
        if not self.check(api=True):
            return
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            return self.send_text(415, 'JSON only')
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            return self.send_text(413, 'body too large or empty')
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            questions = {q['id']: q for q in load_questions()}
            events = validate_answers(payload, questions)
        except ValueError as exc:
            return self.send_text(400, str(exc))
        with EventLog() as log:
            log.append(events)
        self.send_text(200, json.dumps({'stored': len(events)}), 'application/json')


def serve(config):
    Handler.config = config
    server = ThreadingHTTPServer((config['host'], config['port']), Handler)
    (home() / 'server.pid').write_text(str(os.getpid()))  # Only after the bind succeeds: a loser won't clobber a live one
    print('Inbox server started: ' + base_url(config), flush=True)  # The server log never holds the token
    server.serve_forever()


def server_alive(config):
    import urllib.request
    host = 'localhost' if is_loopback(config['host']) else config['host']
    req = urllib.request.Request('http://%s:%s/api/state' % (host, config['port']),
                                 headers={'X-Inbox-Token': config.get('token', '')})
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            return resp.status == 200 and isinstance(body, dict) and 'questions' in body
    except Exception:
        return False


def ensure_server(config):
    if server_alive(config):
        return True
    log = open(home() / 'server.log', 'a', encoding='utf-8')
    detach = {'start_new_session': True}
    if os.name == 'nt':  # No setsid on Windows: DETACHED_PROCESS takes the server off this console
        detach = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'serve'],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, **detach)
    for _ in range(20):
        time.sleep(0.2)
        if server_alive(config):
            return True
    return False


# ---------- Commands ----------

def cmd_ask(args):
    options = []
    for raw in args.option or []:
        key, sep, label = raw.partition('=')
        if not sep or not key.strip() or not label.strip():
            sys.exit('Option format is A=description: ' + raw)
        options.append({'key': key.strip(), 'label': label.strip(),
                        'recommended': key.strip() == args.recommend})
    if args.recommend and not any(o['recommended'] for o in options):
        sys.exit('--recommend names an option that does not exist')
    body = Path(args.body_file).read_text(encoding='utf-8') if args.body_file else (args.body or '')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    q = {'id': 'q-%s-%s' % (stamp, secrets.token_hex(2)), 'kind': 'info' if args.info else 'ask',
         'title': args.title, 'body': body, 'options': options, 'deadline': args.deadline or '',
         'from': args.source or os.path.basename(project_of(os.getcwd())),
         'project': project_of(os.getcwd()), 'session': os.environ.get('CLAUDE_CODE_SESSION_ID', ''),
         'created': now_iso()}
    write_json(home() / 'questions' / (q['id'] + '.json'), q)
    config = load_config()
    up = ensure_server(config)
    print('Added to inbox: %s' % q['id'])
    print('URL: ' + page_url(config) if up else 'Server did not start, see %s' % (home() / 'server.log'))


def cmd_close(args):
    ids = {q['id'] for q in load_questions()}
    missing = [i for i in args.ids if i not in ids]
    if missing:
        sys.exit('Not found: ' + ' '.join(missing))
    with EventLog() as log:
        log.append([{'event_id': str(uuid.uuid4()), 'kind': 'closed', 'question_id': i,
                     'at': now_iso()} for i in args.ids])
    print('Closed %d item(s)' % len(args.ids))


def cmd_take(args):
    pairs = take(os.environ.get('CLAUDE_CODE_SESSION_ID', ''), os.getcwd())
    print(format_answers(pairs) if pairs else 'Inbox: no new replies.')


def cmd_wait(args):
    deadline = time.time() + args.timeout
    session = os.environ.get('CLAUDE_CODE_SESSION_ID', '')
    while time.time() < deadline:
        if deliverable(session, os.getcwd()):
            pairs = take(session, os.getcwd())
            if pairs:
                print(format_answers(pairs))
                return
        time.sleep(3)
    print('Inbox: waited %d seconds, no new replies.' % args.timeout)
    sys.exit(2)


def cmd_hook(args):
    """For the Claude Code hook: stdin is the hook JSON, stdout goes into the agent's context."""
    try:
        data = json.loads(sys.stdin.read() or '{}')
    except ValueError:
        data = {}
    pairs = take(data.get('session_id', ''), data.get('cwd') or os.getcwd())
    if pairs:
        print(format_answers(pairs))


def cmd_list(args):
    for q in build_state()['questions']:
        if q['closed'] and not args.all:
            continue
        status = 'closed' if q['closed'] else ('answered' if q['answers'] else 'waiting')
        print('%s  %s  %s  (%s)' % (q['id'], status, q['title'], q['from']))


def cmd_stop(args):
    pidfile = home() / 'server.pid'
    if not pidfile.exists():
        return print('No server on record')
    try:
        os.kill(int(pidfile.read_text()), signal.SIGTERM)
        print('Stopped')
    except (OSError, ValueError):  # Windows raises OSError for a missing process, not ProcessLookupError
        print('Server was not running')
    pidfile.unlink()


def main(argv=None):
    force_utf8()
    parser = argparse.ArgumentParser(prog='inbox', description='Claude Code inbox')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('ask', help='put a question in the inbox')
    p.add_argument('--title', required=True)
    p.add_argument('--body')
    p.add_argument('--body-file')
    p.add_argument('--option', action='append', help='A=description, repeatable')
    p.add_argument('--recommend', help='key of the recommended option')
    p.add_argument('--info', action='store_true', help='FYI only; the user just acknowledges it')
    p.add_argument('--deadline')
    p.add_argument('--from', dest='source', help='source name to show, defaults to the project folder name')
    p.set_defaults(func=cmd_ask)
    p = sub.add_parser('take', help="collect this session's new replies")
    p.set_defaults(func=cmd_take)
    p = sub.add_parser('wait', help='block until a new reply arrives')
    p.add_argument('--timeout', type=int, default=1800)
    p.set_defaults(func=cmd_wait)
    p = sub.add_parser('close', help='clear questions that are done')
    p.add_argument('ids', nargs='+')
    p.set_defaults(func=cmd_close)
    p = sub.add_parser('list', help='list questions')
    p.add_argument('--all', action='store_true')
    p.set_defaults(func=cmd_list)
    sub.add_parser('hook', help='for the Claude Code hook').set_defaults(func=cmd_hook)
    sub.add_parser('serve', help='run the web server in the foreground').set_defaults(func=lambda a: serve(load_config()))
    sub.add_parser('start', help='start the web server in the background').set_defaults(
        func=lambda a: print(page_url(load_config()) if ensure_server(load_config()) else 'Failed to start'))
    sub.add_parser('stop', help='stop the background server').set_defaults(func=cmd_stop)
    sub.add_parser('url', help='print the page URL').set_defaults(func=lambda a: print(page_url(load_config())))
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
