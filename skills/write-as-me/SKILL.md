---
name: write-as-me
description: Draft text in the user's own voice using their write-like-me voice pack - chat replies, emails, posts, docs. Use whenever the user asks to write, draft or reply "as me", "in my voice", "like I would", "sound like me", "draft a reply to <person>", "write an email to <person> about ...", or "write this in my voice". The skill checks for a voice pack first and points to voice-setup if there is none.
argument-hint: "<what to write, e.g. reply to Dana saying Thursday works>"
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py status*), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py kit *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py lint *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py critique-prompt *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py log-draft *)
---

# Write as me

This is the pipeline that tested best in the research (fixed exemplars + rules + avoid list + retrieved same-recipient exemplars + one critique-and-revise pass against real messages). The CLI is `python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py` (written `wlm` below; always run the full form, with the path unquoted, one command per Bash call with no shell variables or `&&` chains, so the pre-approved commands match).

**Run every step, 0 to 7, for every request, including one-line replies.** The critique pass (step 5) and the revision (step 6) are what made the drafts sound like the person in testing; skipping them "because it's short" is the most common way this goes wrong. A short message is cheap to critique. The status line in step 7 reports the lint and critic results, so a skipped step shows.

## 0. Check the pack first

Run `wlm status` before anything else. If it says there is no pack (`"built": false` or "No voice pack"), tell the user to run `/write-like-me:voice-setup` and stop.

## 1. Understand the request

Work out, asking only if it's genuinely unclear:
- **register**: `chat` (Teams/Slack/iMessage/SMS), `email`, `doc` (longer prose: documents, notes), `post` (public posts and comments: Reddit, forums), or `prompt` (to an AI). Default to chat for short replies, email when they say email, post when they mention Reddit or a forum.
- **recipient** name, if any.
- **context**: the message they're replying to (for `post`, the post or comment), if they pasted or described it.
- **notes**: what the message must say. Keep it to the user's content; don't add points.
- **subject** for a new email, if given.

## 2. Build the kit

Pass short notes and context inline (`--notes "..."`, `--context "..."`). If they are long or multi-line, write them to temp files inside `<pack>/work/` (the private pack folder; `wlm status` prints its path), never in the user's project folder, run the command, and delete those temp files as soon as it has printed (the kit keeps its own copy, inside the private pack folder):

```
wlm kit --register <reg> --notes-file <notes.txt> [--context-file <ctx.txt> | --context "<short>"] [--recipient "<name>"] [--subject "<subject>"]
```

It prints a `kit:` directory and the full drafting prompt: fixed exemplars, 3 retrieved exemplars (same recipient first), the rules for that register (never/always, length target, frequency tendencies, permitted imperfection), learned notes, and the task. If it says it fell back to another register, mention that in your final note.

## 3. Draft

Write the draft yourself by following that prompt exactly, as if it were the whole instruction. Key points it encodes, which matter most:
- match the examples' voice, length, punctuation and level of polish, not their topics
- never/always rules are hard; **tendencies are frequencies**: a habit seen in 1 of 4 real messages should usually be absent
- stay near the median length; real messages are short and leave context unexplained
- don't be tidier than they are, and never insert typos on purpose
- no content beyond the notes: no reasons, reassurance, offers, next steps or sign-offs they wouldn't use

Save the draft to `<kit>/draft.txt` with the Write tool (not a shell redirect, which needs an extra permission prompt).

## 4. Lint

`wlm lint --register <reg> --file <kit>/draft.txt`

Findings marked `fix` must be fixed. `consider` items (habit stacking, recent overuse of a habit, too polished) are strong hints. Keep the list for step 6.

## 5. Critique (one pass, ideally in a subagent)

`wlm critique-prompt --kit <kit> --draft-file <kit>/draft.txt` prints the path of a critique prompt: the draft, the situation, and 5 more of the user's real messages (not the ones used as exemplars).

Dispatch the plugin's `voice-critic` agent (subagent type `write-like-me:voice-critic`; if that isn't available, a general-purpose subagent) with the prompt: "Read <critique_prompt path> and do exactly what it asks." It returns at most 4 tells or "No clear tells."

If you can't spawn subagents, read the critique prompt yourself and answer it as a strict outside critic before revising.

## 6. Revise once

Revise the draft to fix the tells and the lint findings. Keep the same content; ignore any suggestion that conflicts with the examples or rules; never add content or deliberate mistakes. If there were no tells and lint was clean, keep the draft (that is still a completed step 6). Save it to `<kit>/final.txt`, re-run `wlm lint --register <reg> --file <kit>/final.txt`, and fix any remaining `fix` items with minimal edits (no second critique round).

## 7. Output

Run `wlm log-draft --kit <kit> --file <kit>/final.txt` (this tracks habit frequency across drafts to prevent caricature, and lets voice-learn compare later). Delete any temp files you made outside the kit.

Before replying, check that you have: a lint result for the draft (step 4), the critic's answer (step 5), and a lint result for the final (step 6). If any is missing, go back and do it now.

Reply with **only**:
1. the final text, in a plain code block (so it copies cleanly), and
2. one short status line that states the exemplar count, the critic result and the lint result, e.g. `Drafted from 9 of your messages (3 to Dana); critic: 2 tells, fixed; lint: clean. If you edit before sending, paste what you sent and I'll learn from it.` or `... critic: no clear tells; lint: 1 fix applied.`

No preamble, no alternatives, no explanation of the pipeline unless asked. If the user asks for changes, revise directly (keep the rules in mind) and log the new final with `log-draft`.
