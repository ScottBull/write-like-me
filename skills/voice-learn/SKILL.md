---
name: voice-learn
description: Learn from what the user actually sent. Use when the user shares the final version of a message after editing a write-like-me draft ("here's what I actually sent", "I changed it to...", "learn from this"), wants to add a real message to their voice pack, states a fact or opinion drafts should know about them, or says a draft didn't sound like them and explains why.
argument-hint: "<paste the text you actually sent>"
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py learn diff *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py status*)
---

# Voice learn

The user's own edits are the best evidence of what a draft got wrong. The CLI is `python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py` (written `wlm`; one command per Bash call). Nothing is written to the pack without the user's OK.

Only the read-only commands (`learn diff`, `status`) are pre-approved. The commands that write (`learn add`, `learn note`, `learn rule`, `person add`, `build`) deliberately trigger Claude Code's permission prompt even after the user has agreed in chat. When you get to step 5, tell the user in one line that they'll see a permission prompt for each write and should approve it.

Where a lesson goes, in the new pipeline:
- **The sent message itself** is the strongest fix. The drafter learns the voice from the archive of real messages, so adding what they sent (`learn add`) teaches it exactly the thing the draft missed, in their own words. Default to this whenever the sent text is really theirs.
- **A fact or opinion** about them ("I don't do Friday meetings", "I think X about Y") goes in the person file (`person add`), which drafts read for what they know and would say.
- **A hard rule** (`learn rule`) is only for something their real messages never do; lint enforces it on every draft.
- **A soft note** (`learn note`) goes in `learned.md`, which write-as-me includes with the archive. Use it for a preference about a recipient or situation that is neither a message nor a rule ("shorter with Dana").

1. **Get both versions.** The sent text comes from the user. The draft is the last one you produced in this conversation, or the last logged one (`<pack>/drafts.jsonl`, used automatically when `--draft-file` is omitted). Confirm the register (chat / email / doc / post / prompt) and recipient. Write both texts to temp files inside `<pack>/work/` (the private pack folder; `wlm status` prints its path), never in the user's project folder.

2. **Diff:** `wlm learn diff --register <reg> --draft-file <draft.txt> --sent-file <sent.txt>`. It shows word-level edits, which habits changed (added/removed exclamation, greeting, final punctuation...), AI-isms removed, length change, and numbered proposals.

3. **Think about the proposals** before showing them. One edit is weak evidence: a habit change is a soft note, not a rule, unless the user says it's always true. Drop proposals that are just content changes, unless the content reveals something about them that belongs in the person file (a fact they corrected, a stance they added). Turn concrete banned words into avoid entries.

4. **Ask the user**, in one compact message: whether to add the sent text to their archive (default yes when it's their real sent message), the 0-3 notes, rules or person-file claims you suggest, each labelled with where it would go.

5. **Apply only what they approved:**
   - add to the archive: `wlm learn add --register <reg> --sent-file <sent.txt> [--recipient "<name>"] [--context-file <ctx.txt>]`, then `wlm build` to refresh the archive and stats (quick; it keeps their rules, notes, defaults and person file)
   - person-file claim: `wlm person add --text "<claim, in the third person or first, as they said it>"` (add `--work` if it is about them at work: role, projects, how they work with the team). It is cited as stated by the user and survives rebuilds of the person file.
   - soft note: `wlm learn note --register <reg> [--recipient "<name>"] --text "<note>"` (repeat `--text` per line) -> `learned.md`
   - hard rule: `wlm learn rule --register <reg> --text "<rule line>"`; to have lint enforce a banned phrase, end the line with an HTML comment marker of the form `lint:avoid=phrase1|phrase2` (see rules.md for examples). These go under the KEEP section of `rules.md`, which survives rebuilds.
   Then delete the temp files.

6. Confirm in one line what changed.

If the user just says a draft "doesn't sound like me" without a sent version, ask what they'd change, or ask them to rewrite it the way they'd send it, then do the steps above. If they state a fact or opinion about themselves with no draft involved, skip to the `person add` line in step 5 (after asking).
