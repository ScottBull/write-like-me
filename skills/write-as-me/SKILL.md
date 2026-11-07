---
name: write-as-me
description: Draft text in the user's own voice using their write-like-me voice pack - chat replies, emails, posts, docs. Use whenever the user asks to write, draft or reply "as me", "in my voice", "like I would", "sound like me", "draft a reply to <person>", "write an email to <person> about ...", "write this in my voice", or pastes text (often Claude's) and asks to "rewrite this in my voice" or "make this sound like me". The skill checks for a voice pack first and points to voice-setup if there is none.
argument-hint: "<what to write, e.g. reply to Dana saying Thursday works>"
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py status*), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py context *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py draft *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py lint *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py log-draft *)
---

# Write as me

The draft is written as the next entry in an archive of the user's own messages. `wlm context` picks a budgeted selection of those messages (plus the user's optional person file, and, only when turned on, a short note of measured habits after it) and writes a context file framed as "write the next entry"; one model call reads that file and writes the message; a lint checks the few hard rules; the draft is logged. No style rules in the prompt (the habits note gives rates over the whole archive, not instructions), no critic, no revision pass: in testing, the archive alone roughly doubled every style score the old rules-and-critic pipeline reached ([docs/research.md](../../docs/research.md)).

The CLI is `python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py` (written `wlm` below; always run the full form, with the path unquoted, one command per Bash call with no shell variables or `&&` chains, so the pre-approved commands match).

**Run every step, 0 to 5, for every request, including one-line replies.** The status line in step 5 reports the entry count and the lint result, so a skipped step shows.

## 0. Check the pack first

Run `wlm status` before anything else. If it says there is no pack (`"built": false` or "No voice pack"), tell the user to run `/write-like-me:voice-setup` and stop. If it says the pack uses an old format, tell the user to run `/write-like-me:voice-setup` (which runs `wlm build` to upgrade; nothing is lost) and stop. Note the pack path it prints: temp files go under `<pack>/work/`.

## 1. Understand the request

Work out, asking only if it's genuinely unclear:
- **register**: `chat` (Teams/Slack/iMessage/SMS), `email`, `doc` (longer prose: documents, notes), `post` (public posts and comments: Reddit, forums), or `prompt` (to an AI). Default to chat for short replies, email when they say email, post when they mention Reddit or a forum.
- **recipient** name, if any (for `post`, the subreddit or forum).
- **context**: the message they're replying to (for `post`, the post or comment), if they pasted or described it.
- **notes**: what the message must say. Keep it to the user's content; don't add points.
- **subject** for a new email, if given.
- **restyle or not**: if the user pastes text (often something Claude wrote) and asks to rewrite it in their voice ("make this sound like me", "put this in my words"), this is a restyle: the pasted text takes the place of the notes (step 2, restyle). Note whether they said to keep everything, and whether they gave their own point in a phrase ("basically I want to say it's fine").

## 2. Build the context

Pass short notes and context inline (`--notes "..."`, `--context "..."`). If they are long or multi-line, write them to temp files inside `<pack>/work/` (never in the user's project folder), run the command, and delete those temp files as soon as it has printed (the context directory keeps its own copy, inside the private pack folder):

```
wlm context --register <reg> --notes-file <notes.txt> [--context-file <ctx.txt> | --context "<short>"] [--recipient "<name>"] [--subject "<subject>"]
```

**Restyle.** Write the pasted text, exactly as given, to a temp file in `<pack>/work/` and pass it instead of the notes:

```
wlm context --register <reg> --restyle-file <pasted.txt> [--keep all] [--point "<their point>"] [--context-file <ctx.txt> | --context "<short>"] [--recipient "<name>"] [--subject "<subject>"]
```

It builds the notes from the text with the wording that tested best: by default (`--keep mine`) it tells the drafter the text is an AI-written answer with more detail than the user would send and to reply at their real length with only what they'd bother to mention. Use `--keep all` only when the user says to keep everything. If they gave their point in a phrase, pass it as `--point`, in their words. It prints a tip line about adding a point; don't repeat the tip as is (see step 5). Delete the temp file once the command has printed, like the other temp files.

It selects the user's own messages within the pack's default budget (`--budget`, `--strategy`, `--person core|full|none` and `--habits on|off` override the defaults in `stats.json`; don't pass them unless the user asks), writes `<ctx>/system.txt` (the archive, framed as "write the next entry", with the person file before it only when it is turned on (off by default), the user's `learned.md` notes for this register and recipient after it, and, only when it is turned on (off by default), a "How my messages vary" block: a few measured rates for the register, such as typical length and how often messages end without punctuation), `<ctx>/task.txt` (the new entry with the notes) and `<ctx>/context.json` (counts, no text), and prints the two paths plus a counts line: entries used of the archive, tokens of the budget (archive, person file, learned notes and habits block), the strategy, and whether a person file was included. Keep those numbers for step 5. If it prints a note that it fell back to another register's archive, or that the person file or the habits block was left out because it did not fit the budget, mention that in your final note.

## 3. Draft, once

Exactly one draft, in this order of preference:

1. **The `voice-writer` subagent (default).** Dispatch the plugin's agent (subagent type `write-like-me:voice-writer`) with this prompt and nothing else: "Context file: `<ctx>/system.txt`. Task file: `<ctx>/task.txt`. Read both and do what the context file says." It reads the archive in its own context, writes the one message, and returns only the text. Don't read `system.txt` yourself: it holds tens of thousands of tokens of the user's real messages and the subagent exists to keep them out of this conversation.
2. **`wlm draft` (fallback when subagents aren't available).** `wlm draft --system-file <ctx>/system.txt --task-file <ctx>/task.txt` makes a single `claude -p` call through the user's own Claude Code login (the archive as the system prompt file, the task on stdin, tools off, `ANTHROPIC_API_KEY` stripped from the child's environment) and prints the message text. It takes about ten seconds.
3. **In this session (last resort, only if both of the above fail).** Tell the user first, in one line, that the draft will put the whole context file (the token count from step 2) into this conversation. Then Read `system.txt` and `task.txt` and follow the instructions at the top of `system.txt` exactly: the message is the next entry in that archive, saying what is in the notes and nothing the user didn't say, with no deliberate typos.

Whichever path ran, save the returned text to `<ctx>/draft.txt` with the Write tool (not a shell redirect, which needs an extra permission prompt). If the text came back with a preamble, quotes or a code fence around it, strip only that wrapping; change nothing else.

## 4. Lint and fix

`wlm lint --register <reg> --file <ctx>/draft.txt`

It checks the hard rules from `rules.md` only (things the user's real messages never do: a dash, a sign-off, an avoid-list phrase, a spelling they never use) and reports `fix` and `info` items. Fix each `fix` item with the smallest edit that removes it; change nothing else, and never add content or deliberate mistakes. `info` items (longer than the middle half of their real messages, with the word count against their median; a habit the recent drafts overuse) are for the status line, not for a rewrite. Save the result to `<ctx>/final.txt` (the unchanged draft if lint was clean: that still counts as a completed step 4). If you made a fix, run `wlm lint --register <reg> --file <ctx>/final.txt` once more and apply any remaining `fix` items the same way; no third pass.

## 5. Log and output

Run `wlm log-draft --register <reg> [--recipient "<name>"] --file <ctx>/final.txt` (this tracks habit frequency across drafts for lint's caricature counter, and lets voice-learn compare the draft with what the user actually sends). Delete any temp files you made outside the context directory.

Before replying, check that you have: the counts line from step 2, exactly one draft from step 3, and a lint result for the final text. If any is missing, go back and do it now.

Reply with **only**:
1. the final text, in a plain code block (so it copies cleanly), and
2. one short status line with the entries used, the budget, whether the person file was in the context, and the lint result, e.g. `Drafted as the next entry after 61 of your chat messages (10,000-token budget, no person file); lint: clean. If you edit before sending, paste what you sent and I'll learn from it.` or `... (10,000-token budget, person file: core); lint: 1 fix applied.` Add the register fallback note from step 2 if there was one, and the `info` items from lint in a few words if any. For a restyle, add once, in a few words, that giving their own point usually makes it sound more like them (e.g. `Tip: your own one-line point usually sounds more like you.`); leave it out if they already gave one with `--point`.

No preamble, no alternatives, no explanation of the pipeline unless asked. If the user asks for changes, revise the text directly (keep it as the next entry: their length, their punctuation, no added content), re-run `wlm lint` on it, and log the new final with `log-draft`.
