---
name: inbox
description: When the user needs to decide, confirm, or know something but may not be at the conversation, put the question on the inbox web page instead of waiting in the conversation. Also use it when the user says "put it in the inbox", "throw it in the inbox", "I'll look when I'm back", or asks whether the inbox has new replies.
---

# Inbox

The user is not necessarily watching every conversation. Collect the things they have to decide on the inbox page, so they can read and answer them in one pass when they have time. Once they answer, the answers appear in your context automatically on your next turn, starting with "Inbox: the user replied to N item(s)".

## When to ask

- They have to decide, and guessing wrong costs more than waiting: direction changes, irreversible actions, changes to behavior other people can see.
- They need to know something but don't have to decide: use `--info`, and they just press "Got it".
- Anything you could answer yourself from the code, the tests, or the docs: don't ask.

## How to ask

```bash
inbox ask --title "Keep password login on the sign-in page?" \
  --body "The new version moves to Google sign-in only. 3 accounts still use password login." \
  --option "A=Keep password login, run both side by side" \
  --option "B=Google only, tell those 3 people to switch" \
  --recommend A \
  --deadline "by 18:00 today"
```

- Write `--title` as a question that makes sense without the body.
- Give `--body` just enough background to decide, two or three sentences; use `--body-file` for longer content. Blank-line paragraphs, `**bold**`, `` `code` `` and URLs are supported.
- Write each option as "the action and its consequence". Put the recommended one first and mark it with `--recommend`.
- Questions with no options are fine too; the user just writes an answer.
- FYI only: `inbox ask --info --title "..." --body "..."`.

After asking, the command prints the question id and the URL. The first question automatically starts the web server in the background.

If the `inbox` command isn't found, run `"${CLAUDE_PLUGIN_ROOT}/inbox/inbox.py"` directly with the same arguments, using the first Python that works: `python3`, then `python`, then `py -3`. On Windows, `python`/`python3` can be Microsoft Store placeholders that only print "Python was not found"; skip those.

## When the project already has its own rules

The inbox is only a channel for asking the user. It does not change what should be asked or who decides.

- If the project's `CLAUDE.md`, `AGENTS.md`, or other rules say which things to ask the user first, keep asking them; just ask in the inbox instead of stopping in the conversation to wait.
- If the rules say which things you can decide yourself, keep deciding them yourself. Don't ask more just because an inbox exists.
- When the rules conflict with this document, follow the project's rules.

## After you ask

- Don't stop in the conversation to wait. Do the work this question doesn't block first.
- When you genuinely can't proceed without the answer, run `inbox wait --timeout 1800` as a background Bash command. It prints the answer and exits when a reply arrives, which wakes you up.
- To check for new replies by hand: `inbox take`.
- Do what the user chose. Their notes take priority over the option text.
- When an item is done, run `inbox close <id>` to clear it from the user's screen.

## Asking in both places (first come, first served)

Use this when the user may be at the conversation or away from it, or when their rules say to ask in both places.

1. Run `inbox ask ...` and note the id it prints.
2. Start `inbox wait --id <id> --timeout 3600` as a background Bash command. It returns only for that item.
3. Ask the same question in the conversation and mention the id, for example "(also in the inbox as q-…)". Then end your turn.
4. Whichever answer reaches you first is the decision:
   - **The background wait returns with an answer.** Act on it. Say in the conversation that it was answered in the inbox, then `inbox close <id>` once it's done. If the user replies in the conversation later, treat that as a new instruction, not as a second answer to compare.
   - **The user answers in the conversation first.** Stop the background wait, then run `inbox close <id> --resolved-in conversation --note "<what was decided>"`. The page then locks the item and shows it as settled in the conversation, and any later page answer is refused.
5. If `close` prints `Note: … was also answered on the inbox page at <time>`, the page answer got in before your close. Compare that time with when the user's conversation message arrived:
   - **The page answer is earlier:** it is the decision.
   - **The chat answer is earlier:** the chat answer stands.
   - **The two answers differ:** tell the user in one line which one you went with.
6. If the wait times out (exit 2) and the item is still open, start it again, at most twice. After that the reply arrives through the hook the next time the user types. If the wait exits with code 3, the item was closed some other way; stop waiting.

## Which conversation gets the answer

Answers go back only to the conversation that asked. If that conversation hasn't collected within 30 minutes, other conversations in the same project can collect it too, so nothing is lost when the asking conversation ends. Each answer is delivered exactly once.
