---
name: voice-setup
description: Build or rebuild the user's local voice pack (their own writing samples, measured habits and rules) so Claude can write like them. Use when the user wants to set up write-like-me, "learn my writing style", "make a voice pack", add a new writing source (Teams, Outlook, iMessage, Claude Code history, a folder of emails or docs, pasted samples), or rebuild after adding samples.
argument-hint: "[optional: source to add, e.g. 'teams' or a folder path]"
---

# Voice setup

Goal: a local voice pack built ONLY from text the user wrote and sent, at `$WRITE_LIKE_ME_PACK` or `~/.claude/voice-pack/`. Every command below is the plugin CLI:

```
python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py <command>
```

(Written as `wlm <command>` below. Always run the full form. `-I` keeps Python isolated; the CLI needs only the standard library, Python 3.9+.)

## Ground rules

- **Consent per source.** Never extract from a source the user hasn't said yes to in this conversation. Ask about each source separately.
- **Workplace data.** Before Teams, Outlook or work email/docs, say once: "This is work data. Everything stays on this machine, but when you later draft with it, samples are sent to the model you use in Claude Code. Check your employer's policy on using work messages with AI tools." Continue only if they say it's fine.
- **Only their own sent text.** The extractors already keep only messages the user sent; don't add anyone else's writing.
- **Don't read the corpus into the conversation.** Use the counts the CLI prints. Only show sample text if the user asks, and keep it to a few lines.
- Never commit, upload or copy anything from the pack anywhere.

## Steps

1. **Discover.** Run `wlm discover` and summarise for the user, as a short list: which sources were found (with counts) and which are missing or need a permission or an install. If `pack_exists` is true, say so and ask whether they want to add sources or rebuild from scratch.

2. **Ask which sources to use**, one question per available source, saying what register it gives:
   - `claude_code`: what they type to Claude Code ("prompt" register). Good for mechanics (punctuation, capitalisation, hedging) but research showed it does NOT transfer conversational vocabulary, so it's a supplement, never the only source for chat or email.
   - `teams` (macOS, new Teams): chat messages they sent, from the local cache (recent weeks/months only). If `needs_install` is true, explain it needs the open-source `ccl_chromium_reader` parser (MIT, by CCL Solutions Group, not bundled). Installing it means downloading and later running third-party code from GitHub, so say that plainly. Run `wlm vendor-install teams` (a dry run: it prints the repos, the pinned commits and the exact commands, and the vendor path, which already respects `WRITE_LIKE_ME_PACK`) and show the user that output. Only if they agree, run `wlm vendor-install teams --yes`. Then re-run `wlm discover`; if Teams still says it failed to load and their `python3` is older than 3.10, suggest a newer Python (e.g. `brew install python`) or move on.
   - `outlook` (macOS, legacy Outlook for Mac): sent email via the local cache + AppleScript (macOS will ask to allow Automation; Outlook must be running). Ask for the first name / initials they sign emails with and pass each as `--name`. If they use New Outlook, there is no local store: suggest dragging Sent Items to a folder as .eml files and using `files`.
   - `imessage` (macOS, optional): needs `imessage-exporter` (`brew install imessage-exporter`) and Full Disk Access for the terminal app. These are personal messages; ask explicitly.
   - `files`: any folder or file of .txt/.md (documents, register "doc"; pass `--register chat` or `email` if that fits better) or .eml/.mbox (email). For email files ask for their own address(es) and pass `--address` (repeatable); use `--all-mine` only if they confirm every message there is one they sent (e.g. a Sent-folder export). Gmail: Google Takeout of the Sent label gives an .mbox.
   - `paste`: they can paste samples into the chat. Write them to a temp file inside `<pack>/work/` (the private pack folder; `wlm status` prints its path), never in the user's project folder, separated by lines containing only `---` (optional first lines `to: Name` and `context: what they replied to`), then `wlm add-samples --register <chat|email|doc|post|prompt> --file <tmpfile>`, then delete the temp file. Ask which register they are.

3. **Extract** each agreed source:
   - `wlm extract claude_code` (add `--no-ai-check` if it's very slow on a huge history)
   - `wlm extract teams`
   - `wlm extract outlook --name <Name> [--address <addr>]`
   - `wlm extract imessage [--since YYYY-MM-DD]`
   - `wlm extract files <path> [--address <addr>] [--all-mine] [--name <Name>] [--register doc]`
   Each prints JSON with counts. If one fails (`"ok": false`), explain the error in plain words, suggest the fix, and carry on with the others. Platform-specific sources simply report unavailable on non-macOS systems.

4. **Build:** `wlm build`. It cleans, de-duplicates, masks email addresses and phone numbers in the exemplar bank (`--no-mask` to keep them), measures habits per register, and writes `profile.md`, `rules.md`, `stats.json`, `exemplars.jsonl` and `learned.md` (never overwritten). Rebuilding keeps anything under the `<!-- KEEP BELOW -->` line of `rules.md`.

5. **Report** in a few lines: messages per register, median length, imperfection level, and any register marked LOW confidence (under 20 samples; suggest more samples for it). Recommend at least ~50 messages for a register they care about, and that chat/email be backed by real chat/email rather than prompts alone.

6. **Review rules.** Read `rules.md` (from the pack dir) and show the user the never/always lines and the top tendencies for their main register, in a compact list. Ask if anything is wrong. Edit lines they disagree with (delete a line to stop it being enforced; their own rules go under `<!-- KEEP BELOW -->`, scoped with `## chat` / `## email` / `## all`). Keep tendencies as frequencies ("about 1 in 4 messages"); don't turn them into commands, because that causes caricature.

7. **Next steps** (one line each): draft with `/write-like-me:write-as-me` or just ask "write this in my voice"; after sending an edited draft, share what you actually sent with `/write-like-me:voice-learn`; optionally run `/write-like-me:voice-check` for a blind test.

## Privacy recap to give the user (short)

Pack location, file permissions are user-only (folders 0700, files 0600), nothing leaves the machine during setup; when drafting, a handful of their real messages plus the rules are sent to the model as examples. To delete everything: remove the pack folder.
