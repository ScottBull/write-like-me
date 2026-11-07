---
name: voice-check
description: Optional evaluation of the user's voice pack. Holds out some of their real messages, has Claude write each one from a content-only summary, then builds a blind-test page where the user picks their real message from each pair, and scores stylometric closeness plus a forced-choice LLM judge. Use when the user asks "how good is my voice pack", "test it", "does it sound like me", or wants a blind test.
argument-hint: "[register: chat|email|doc|prompt] [number of messages, default 12]"
---

# Voice check

The CLI is `python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py` (written `wlm`). This runs model calls through the user's own Claude Code login (`claude -p`, all tools disabled, from an empty temp dir), so it costs usage: roughly 5 calls per held-out message plus a few judge calls. Tell the user the estimate printed by `prepare` and get an OK before `generate`.

**Real data vs demo data.** A voice check is only meaningful on the user's own messages from their own voice pack. `prepare` prints `"data": "REAL: ..."` or `"data": "DEMO: ..."`, and the page shows the same label at the top (a yellow DEMO banner for synthetic data). Runs built from the plugin's synthetic test fixtures (`tests/fixtures/`, invented names like "Jordan Example") are always labelled DEMO. Never present a DEMO page or score as a test of the user's voice; if you build one (e.g. to show how the page works), say "this is a demo with made-up messages, not your writing" when you give it to them. Before a real test, run `wlm status` and confirm the pack is the user's (sources like teams / outlook / claude_code, not `synthetic`).

**Important: don't contaminate the test.** You (this session) must not draft the test messages yourself or look at the held-out texts before the user takes the blind test; the scripted pipeline does the drafting in isolated calls. Don't print `items.jsonl` or `generations.jsonl`.

1. **Prepare:** `wlm check prepare --register <reg> --n <N>` (default chat, 12; minimum 4 usable messages). It picks typical-length real messages, prefers replies, spreads them over conversations, and excludes the fixed exemplars. It prints the run dir, whether the data is REAL or DEMO, and a call estimate. Tell the user which it is.

2. **Generate** (after the user agrees): `wlm check generate [--run <dir>] [--model <id>]`. For each held-out message: a content-only brief, then the full write-as-me pipeline (draft -> lint -> critique -> revise) with that message, anything in the same thread within 48 hours, and near-duplicates excluded from retrieval; plus a "plain" Claude version with no voice pack as a baseline. Takes a few minutes.

3. **Blind test page:** `wlm check page [--run <dir>] --open` (add `--include-plain` to also test the baseline). It writes a self-contained `blind_test.html` in the run dir (local only; it contains real messages). The user picks which message in each pair they really wrote; picks are kept in the browser and can be exported as JSON at the end. Tell them to answer before reading the review at the end, then export and give you the file path.

4. **Score:** `wlm check score [--run <dir>] [--picks <exported.json>]` (`--no-judge` skips the LLM judge). Picks are checked against the run's own answer key (`answers.json`, written by `page`); an export from a different run, or from an older version of the page (rebuilt since, e.g. with `--include-plain`), is refused. Reports:
   - stylometric distance (mean |z| over ~20 habits vs same-size samples of their own messages; lower is closer) for real held-out messages, the voice pipeline and plain Claude, and a 0-1 closeness score (0 = plain Claude, 1 = as close as their own held-out messages; clamped, with the raw value beside it, and "n/a" when plain Claude happens to be closer than the real messages)
   - median lengths
   - LLM judge fooling rate per condition with a 95% CI (forced choice, both orders; 50% = indistinguishable, 0% = always caught). Same-family judges are lenient-to-harsh in unknown ways; treat it as relative, not absolute.
   - their own blind-test fooling rate, if picks were given.

5. **Explain** in a few lines what the numbers mean. If the judge's tells (in `judge.jsonl`) or the user's notes from the page point to a repeatable giveaway, offer to turn it into a rule or learned note via `/write-like-me:voice-learn` steps (with their OK). Typical findings: drafts too long, too tidy, or overusing one signature habit.

With 12 messages (24 judge trials per condition), confidence intervals are wide; differences under ~20 points aren't meaningful.
