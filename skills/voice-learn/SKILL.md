---
name: voice-learn
description: Learn from what the user actually sent. Use when the user shares the final version of a message after editing a write-like-me draft ("here's what I actually sent", "I changed it to...", "learn from this"), or wants to add a real message to their voice pack, or says a draft didn't sound like them and explains why.
argument-hint: "<paste the text you actually sent>"
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py learn diff *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py status*)
---

# Voice learn

The user's own edits are the best source of new rules. The CLI is `python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py` (written `wlm`). Nothing is written to the pack without the user's OK.

Only the read-only commands (`learn diff`, `status`) are pre-approved. The commands that write (`learn note`, `learn rule`, `learn add`, `build`) deliberately trigger Claude Code's permission prompt even after the user has agreed in chat. When you get to step 5, tell the user in one line that they'll see a permission prompt for each write and should approve it.

1. **Get both versions.** The sent text comes from the user. The draft is the last one you produced in this conversation, or the last logged one (`<pack>/drafts.jsonl`, used automatically when `--draft-file` is omitted). Confirm the register (chat / email / doc / prompt) and recipient. Write both texts to temp files inside `<pack>/work/` (the private pack folder; `wlm status` prints its path), never in the user's project folder.

2. **Diff:** `wlm learn diff --register <reg> --draft-file <draft.txt> --sent-file <sent.txt>`. It shows word-level edits, which habits changed (added/removed exclamation, greeting, final punctuation...), AI-isms removed, length change, and numbered proposals.

3. **Think about the proposals** before showing them. Keep only ones that look like a real, repeatable preference. One edit is weak evidence: phrase a habit change as a tendency ("leans shorter for Dana", "often drops the final period") rather than an absolute rule, unless the user says it's always true. Drop proposals that are just content changes. Turn concrete banned words into avoid entries.

4. **Ask the user**, in one compact message: the 1-4 notes you suggest, which ones should become enforced rules (lint) vs. soft notes, and whether to add the sent text to their exemplar bank (default yes when it's their real sent message).

5. **Apply only what they approved:**
   - soft note: `wlm learn note --register <reg> [--recipient "<name>"] --text "<note>"` (repeat `--text` per line) -> `learned.md`, read by every future draft
   - enforced rule: `wlm learn rule --register <reg> --text "<rule line>"`; to have lint enforce a banned phrase, end the line with an HTML comment marker of the form `lint:avoid=phrase1|phrase2` (see rules.md for examples). These go under the KEEP section of `rules.md`, which survives rebuilds.
   - add to bank: `wlm learn add --register <reg> --sent-file <sent.txt> [--recipient "<name>"] [--context-file <ctx.txt>]`, then `wlm build` to refresh stats (quick).
   Then delete the temp files.

6. Confirm in one line what changed.

If the user just says a draft "doesn't sound like me" without a sent version, ask what they'd change, or ask them to rewrite it the way they'd send it, then do the steps above.
