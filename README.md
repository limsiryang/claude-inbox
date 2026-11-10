<div align="center">

# 📥 Inbox

### Stop your agent from sitting there waiting for you to say "yeah, do it"

A decision mailbox for Claude Code: everything the agent needs you to call goes onto one web page.<br>
Read them all at once when you have time, answer them all at once, and **the answers go straight back to the agent**.

![license MIT](https://img.shields.io/badge/license-MIT-green) ![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue) ![Claude Code plugin](https://img.shields.io/badge/Claude_Code-plugin-d97757)

<img src="docs/demo.gif" alt="Demo: pick an answer on the inbox page, add a note, send, and the agent gets it immediately" width="760">

</div>

---

## Sound familiar?

- You start the agent and go to lunch, and come back to find it stopped 20 minutes ago on "should I use A or B?"
- You have three conversations open and every one of them is asking you something, so you switch between them one by one
- You type the answer into one conversation, another conversation never hears about it, and the next day you explain it all over again

**The inbox takes "asking you" out of the conversation.** The agent keeps working on the parts that don't need you, the things that do need you queue up, and you handle them when it suits you.

<div align="center">
<img src="docs/flow.png" alt="Flow: the agent asks, you answer on the web page, the answer goes back to the agent" width="900">
</div>

## What it looks like

<div align="center">
<img src="docs/shot-answering.png" alt="The inbox page: question, options, recommended marker and a notes field" width="760">
</div>

Every item comes with its **source** (which project asked), a **deadline**, a **recommended option**, and a notes field. Pick one, write a line or two, hit "Send all answered".

<table>
<tr>
<td width="62%" valign="top">
<img src="docs/shot-received.png" alt="After sending, each item shows 'agent received it'">
<p align="center"><sub>After sending, you can see whether the agent has picked each one up</sub></p>
</td>
<td width="38%" valign="top">
<img src="docs/shot-mobile.png" alt="Mobile view">
<p align="center"><sub>Mobile (an actual 390px-wide screen)</sub></p>
</td>
</tr>
</table>

> Note: the screenshots above were taken from the original Traditional Chinese build; the interface now ships in English.

## Why it's useful

| | |
|---|---|
| **Everything in one place** | Questions from every conversation on one page — source, deadline and recommended option at a glance, no window switching |
| **Answer in one pass** | Send item by item, or everything at once with "Send all answered". Drafts save themselves and survive a refresh |
| **Answers go back by themselves** | Via a Claude Code hook: once you send, the answer is already in the agent's context when its next turn starts. No pasting |
| **No AI calls, no cloud** | Standard library Python only. The question is exactly what the agent wrote, and all the data stays on your own machine |
| **Secure by default** | Bound to localhost only, every operation needs a token, and question text is always rendered as plain text, never executed |
| **Answer from your phone** | Point the address at your Tailscale or LAN address and you can answer from a phone (see the advanced section below) |

## 30 seconds to start

In Claude Code, type:

```
/plugin marketplace add chenjackle45/claude-inbox
/plugin install inbox@claude-inbox
```

Restart Claude Code once after installing, then tell the agent:

> If there's anything you need me to decide, put it in the inbox.

That's it. The agent will use `inbox ask` on its own and print the page URL. To make it stick, put that sentence in your project's `CLAUDE.md`.

**Here's what the agent sees after you send:**

```
Inbox: the user replied to 2 item(s).

[q-20261107-144835-480d] Keep password login on the sign-in page?
User chose: A Keep password login, run both side by side
Notes: Leave it for now, check next month whether anyone still uses it.

Act on the reply; once the item is done, run inbox close <id> to clear it.
```

Want to try asking one yourself:

```bash
inbox ask --title "Ship the new version?" --option "A=Ship it" --option "B=Hold" --recommend A
```

> The `inbox` command is meant for the agent's Bash. To try it from your own terminal, use `python3 <plugin folder>/inbox/inbox.py` instead, with the same arguments.

## Already have your own development rules? Layer this on, don't swap it in

The inbox doesn't touch your `CLAUDE.md`, your `AGENTS.md`, or any existing process. It adds exactly three things: an `inbox` command, a web page, and a hook that feeds answers to the agent at the start of each turn.

So your existing rules stand: what has to be asked and what the agent may decide alone are both unchanged — only the place where you get asked moves from the conversation to the inbox. To get the agent using it this way, paste this into your `CLAUDE.md`:

```markdown
## Inbox
Anything you need to ask me (the things the rules above already say to ask me about) goes into the inbox
via `inbox ask`. Don't stop in the conversation to wait.
Anything you don't need to ask me about, decide yourself as before. Don't ask more just because an inbox exists.
```

If you don't have rules of your own, the defaults are fine as they are.

## 🎁 Bonus: lead PM mode

One long-running conversation drives several work streams, writes no code itself, and only assigns work, settles questions and tracks progress — while you **look at nothing but the inbox**. Type `/inbox:pm` for the full instructions, including how the state file is written, what should be asked of you, and what must always be asked first.

This is a default for people who don't have a process yet. If you already have your own lead PM, state file, or review process, the agent will follow yours and only fill in the inbox part, not build a second system alongside it.

## Works without Claude Code too (Codex and other agents)

At its core the inbox is one Python command plus a web page; it isn't tied to Claude Code. The plugin is just the Claude Code packaging. Other agents can run the same command straight from a shell:

```bash
git clone https://github.com/chenjackle45/claude-inbox
python3 claude-inbox/inbox/inbox.py ask --title "Ship the new version?" --option "A=Ship it" --option "B=Hold"
python3 claude-inbox/inbox/inbox.py wait --timeout 1800   # prints the answer and exits when a reply arrives
```

Other agents don't have the Claude Code hook, so answers won't appear in their context by themselves — they have to run `wait` or `take` to fetch them. Paste this into their rules file (for example `AGENTS.md`), with the path changed to wherever you put it:

```markdown
## Inbox
Anything you need to ask me goes into the inbox with the command below. Don't stop in the conversation to wait:
python3 /path/to/claude-inbox/inbox/inbox.py ask --title "..." --option "A=..." --option "B=..."
Work on whatever this question doesn't block first. When you really must have the answer,
run `... inbox.py wait --timeout 1800` in the background.
Once an item is handled, run `... inbox.py close <id>`.
```

Two limitations:
- The program identifies the asking conversation through the `CLAUDE_CODE_SESSION_ID` environment variable. Other agents don't have it, so questions become anonymous and any agent in the same project folder can collect the answer. Two agents asking from the same project at the same time may get each other's answers.
- This usage hasn't actually been run against Codex or anything similar; it's written from how the program behaves.

## Find it useful?

Leave a ⭐ **Star** so more people see it, and feel free to open an issue telling me where you got stuck or what could be better.

---

<details>
<summary><b>Advanced: viewing from a phone, where the data lives, which conversation gets the answer</b></summary>

### Viewing from a phone

By default it only binds to localhost. To use it from a phone, edit `~/.claude-inbox/config.json`:

```json
{ "host": "your Tailscale or LAN address", "port": 7799 }
```

Only IPv4 addresses are supported. The token is the same whether you're local or remote; it's generated on first use and written to the same file (file mode 600, folder mode 700). After changing the address or the token, restart the server: `inbox stop` then `inbox start`. A running server does not pick up new settings on its own, and the old token stays valid until it restarts. `inbox url` prints the full URL including the token.

### Where the data lives

`~/.claude-inbox/` (change it with the `CLAUDE_INBOX_HOME` environment variable):

| File | Contents |
|---|---|
| `questions/*.json` | Questions, one file each |
| `events.jsonl` | Your answers, delivery records and close records; append-only |
| `config.json` | Address, port, token |

Questions and answers are both plaintext. Don't put passwords or keys in them.

### Which conversation gets the answer

The answer goes back to the conversation that asked, whatever folder it's in now. If that conversation hasn't collected within 30 minutes, other conversations in the same project (the same git root) can collect it too. Each answer is delivered exactly once.

### Security

- Only accepts requests whose `Host` and `Origin` match, which blocks other web pages from poking at your local server.
- Every API call needs the token, local ones included: answers go into the agent's context, so other programs on the same machine must not be able to write to it freely.
- The page renders question text as plain text; only URLs become links.
- Answers only ever move around on your own machine. Nothing is sent anywhere.

### Requirements

Python 3.9 or newer. Getting answers back to the agent automatically requires Claude Code; see above for other agents. Runs on macOS, Linux, WSL and native Windows. No other dependencies.

### Development

```bash
python3 -m unittest discover tests
```

</details>

## License

MIT
