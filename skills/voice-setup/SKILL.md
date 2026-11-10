---
name: voice-setup
description: Build or rebuild the user's local voice pack (an archive of their own sent messages, measured habits, hard rules, and an optional distilled person file) so Claude can write like them. Use when the user wants to set up write-like-me, "learn my writing style", "make a voice pack", add a new writing source (Teams, Outlook, iMessage, Claude Code history, Reddit or forum posts, a folder of emails or docs, pasted samples), rebuild after adding samples, upgrade a pack from an older version, or build or redo their person file.
argument-hint: "[optional: source to add, e.g. 'teams' or a folder path]"
---

# Voice setup

Goal: a local voice pack built ONLY from text the user wrote and sent, at `$WRITE_LIKE_ME_PACK` or `~/.claude/voice-pack/`. Every command below is the plugin CLI:

```
python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/wlm.py <command>
```

(Written as `wlm <command>` below. Always run the full form, one command per Bash call. `-I` keeps Python isolated; the CLI needs only the standard library, Python 3.9+.)

## Ground rules

- **Consent per source.** Never extract from a source the user hasn't said yes to in this conversation. Ask about each source separately.
- **Workplace data.** Before Teams, Outlook or work email/docs, say once: "This is work data. Everything stays on this machine, but when you later draft with it, a selection of these messages is sent to the model you use in Claude Code, and if you build a work person file, all of them are. Check your employer's policy on using work messages with AI tools." Continue only if they say it's fine.
- **Only their own sent text.** The extractors already keep only messages the user sent; don't add anyone else's writing.
- **Don't read the archive into the conversation.** Use the counts the CLI prints. Only show sample text if the user asks, and keep it to a few lines.
- **The person file is a separate decision** with its own consent (step 7). Building it makes model calls over the user's archive. Never build it because setup "should".
- Never commit, upload or copy anything from the pack anywhere.

## Steps

1. **Discover.** Run `wlm discover` and summarise for the user, as a short list: which sources were found (with counts) and which are missing or need a permission or an install. If `pack_exists` is true, run `wlm status` too. If it says the pack uses an old format, tell the user that `wlm build` upgrades it in place (the sources are kept; the old example bank is replaced by the archive; their own rules under `<!-- KEEP BELOW -->`, learned notes and logged drafts are untouched) and offer to run it now, with or without adding sources. Otherwise ask whether they want to add sources, rebuild from scratch, or work on the person file.

2. **Ask which sources to use**, one question per available source, saying what register it gives:
   - `claude_code`: what they type to Claude Code ("prompt" register). Good for mechanics (punctuation, capitalisation, hedging) but research showed it does NOT transfer conversational vocabulary, so it's a supplement, never the only source for chat or email.
   - `teams` (macOS, new Teams): chat messages they sent, from the local cache (recent weeks/months only). If `needs_install` is true, explain it needs the open-source `ccl_chromium_reader` parser (MIT, by CCL Solutions Group, not bundled). Installing it means downloading and later running third-party code from GitHub, so say that plainly. Run `wlm vendor-install teams` (a dry run: it prints the repos, the pinned commits and the exact commands, and the vendor path, which already respects `WRITE_LIKE_ME_PACK`) and show the user that output. Only if they agree, run `wlm vendor-install teams --yes`. Then re-run `wlm discover`; if Teams still says it failed to load and their `python3` is older than 3.10, suggest a newer Python (e.g. `brew install python`) or move on.
   - `outlook` (macOS, legacy Outlook for Mac): sent email via the local cache + AppleScript (macOS will ask to allow Automation; Outlook must be running). Ask for the first name / initials they sign emails with and pass each as `--name`. If they use New Outlook, there is no local store: suggest dragging Sent Items to a folder as .eml files and using `files`.
   - `imessage` (macOS, optional): needs `imessage-exporter` (`brew install imessage-exporter`) and Full Disk Access for the terminal app. These are personal messages; ask explicitly.
   - `files`: any folder or file of .txt/.md (documents, register "doc"; pass `--register chat`, `email` or `post` if that fits better) or .eml/.mbox (email). For email files ask for their own address(es) and pass `--address` (repeatable); use `--all-mine` only if they confirm every message there is one they sent (e.g. a Sent-folder export). Gmail: Google Takeout of the Sent label gives an .mbox.
   - `proofhq`: proof comments and replies they wrote in ProofHQ / Workfront Proof ("review" register: their feedback voice, used when drafting proof comments and replies to reviewers). Worth it for people who comment on proofs a lot. This is work data, like Teams and Outlook. Workfront copies most proof comments into its own comment store, not every one, and proofs outside Workfront (standalone ProofHQ accounts) aren't covered; say so. The plugin never sees their Workfront sign-in: they open links in their own browser and save the pages.
   - `paste`: they can paste samples into the chat. Write them to a temp file inside `<pack>/work/` (the private pack folder; `wlm status` prints its path), never in the user's project folder, separated by lines containing only `---` (optional first lines `to: Name` and `context: what they replied to`), then `wlm add-samples --register <chat|email|doc|post|prompt|review> --file <tmpfile>`, then delete the temp file. Ask which register they are. Public posts and comments (Reddit, forums) are register `post`, with the subreddit or forum as the recipient.

3. **Extract** each agreed source:
   - `wlm extract claude_code` (add `--no-ai-check` if it's very slow on a huge history)
   - `wlm extract teams`
   - `wlm extract outlook --name <Name> [--address <addr>]`
   - `wlm extract imessage [--since YYYY-MM-DD]`
   - `wlm extract files <path> [--address <addr>] [--all-mine] [--name <Name>] [--register doc]`
   - `proofhq`, in two steps:
     1. Ask them to open any proof in Workfront and paste its link (`https://<something>.my.workfront.adobe.com/proof/review/...`). An experience.adobe.com link won't work: it doesn't show their Workfront address. Run `wlm extract proofhq --link "<link>"`. It keeps only the host and prints `signin`, `count` and `pages` links.
     2. Give them the steps: open the `signin` link and let it finish (it may bounce to experience.adobe.com; that's fine, it puts a session on the Workfront site itself, and that session doesn't last long), then right away open each `pages` link and save it with Cmd+S / Ctrl+S (Chrome names it `search.json`; later pages `search (1).json` and so on). Or select all, copy, and use `pbpaste | wlm extract proofhq --file -`. If they comment a lot, have them open the `count` link first; if it's over 2000, re-run step 1 with `--pages <count / 2000, rounded up>`. Then run `wlm extract proofhq --file <page1> [--file <page2> ...]` with every page at once (a re-run replaces the earlier import). If the result says `maybe_more`, a page was full, so ask for the next page. If it says "not signed in", have them open the `signin` link again and then the export link straight away. Afterwards, offer to delete the saved pages: they contain other people's comments.
   Each prints JSON with counts. If one fails (`"ok": false`), explain the error in plain words, suggest the fix, and carry on with the others. Platform-specific sources simply report unavailable on non-macOS systems.

4. **Build:** `wlm build`. It cleans, de-duplicates, masks email addresses and phone numbers (`--no-mask` to keep them), writes the archive (`archive/<register>.jsonl`: the cleaned messages in date order, each with what it replied to), measures habits per register, and writes `stats.json` (with the pack's default draft budget and selection strategy), `rules.md` (hard rules only), `profile.md` (the readable habit summary, including the tendencies that used to be rules) and `learned.md` (never overwritten). Rebuilding keeps anything under the `<!-- KEEP BELOW -->` line of `rules.md`, and keeps any budget or strategy default the user set in `stats.json` (the plugin's own defaults follow plugin updates).

5. **Report** in a few lines: messages per register with the archive size (entries and tokens), median length, and any register marked LOW confidence (under 20 samples; suggest more samples for it). Recommend at least ~50 messages for a register they care about, and that chat/email be backed by real chat/email rather than prompts alone. More messages help here more than in the old version: the drafter learns the voice from the archive itself, so a bigger archive means a wider choice of entries for each draft.

6. **Explain the budget in plain words.** `wlm status` prints `defaults: budget B tokens, strategy S` and, per register, the archive's entries and tokens. Each draft sends about B tokens of their real messages (plus the person file, if any) to the model; turn that into messages for them: `B ÷ (tokens ÷ entries)` for their main register, e.g. "each draft shows the model about 60 of your chat messages, chosen to cover different people, lengths and years". If the budget is larger than a register's whole archive, say so: every message goes in. The default came from the research ablation, where a few thousand tokens matched much larger contexts, so don't suggest raising it. If the user wants it changed anyway, `budget_default` in `stats.json` is the place (`wlm build` keeps a value they set), and `wlm context --budget N` overrides one draft.

7. **Person file (optional; ask).** The archive carries how they write; the person file is a short, cited description of what they know, do and think, distilled from the archive by model calls, so drafts from thin notes fill gaps with things they would actually say. Two files, asked about separately, because the data has different owners:
   - **personal** (`person.md`): from their posts and comments and iMessage chats. Documents and chat from `extract files`, pasted chat or doc samples and `learn add` docs may be work material, so they are left out unless the user says all of them are personal: the dry run lists them, so ask, and add `--include-unsorted` only on a clear yes (give the workplace reminder if any are work).
   - **work** (`person.work.md`): from Teams, Outlook and Claude Code prompts. Give the workplace reminder again if it applies: this sends the whole work archive to the model, in chunks, once.
   Say plainly what it is: a profile of them, on disk in the pack (owner-only permissions), and every claim in it cites the messages it came from (`person.citations.json`). Then:
   - `wlm person build --which personal|work|both [--include-unsorted]` is a **dry run**: it prints the entries and chunks per file and the number of model calls (about one per 40k tokens of archive, plus about a dozen). Show the user that estimate and ask.
   - Only on a yes: `wlm person build --which <same> --yes`. It takes a few minutes; finished calls are cached, so a failed build resumes cheaply. It prints counts: claims kept, dropped for having fewer than two citations, sentences kept.
   - **Audit.** Run `wlm person show` (the core tier, both files, exactly as drafts will see it) and show the user the text, section by section if long. Ask them to point at anything wrong or anything they don't want a draft to know. For each: `wlm person forget "<the sentence or claim text>"` (add `--work` for the work file), which deletes it and every sentence resting on it and blacklists it so a rebuild never brings it back; `wlm person show --ids` lists claim ids if the text is ambiguous. Something true that the archive doesn't show: `wlm person add --text "..."` (`--work` for the work file).
   - Which tier drafts use: by default the core (about 3k tokens) goes into every draft when a person file exists, inside the budget (both files when both exist, whatever the register: a work file reaches personal posts and a personal file reaches work email; if the user wants only one, set `"person_files": ["person.md"]` or `["person.work.md"]` in `stats.json`); `wlm context --person full|none` changes one draft. If they decide against the person file, build nothing: with no file, nothing is sent.
   Terms the user can hand-edit later: `person.blacklist.json` (`terms`: a list of words or phrases a rebuild drops any claim for).

8. **Review rules.** Read `rules.md` (from the pack dir) and show the user the never/always lines for their main register, in a compact list, and tell them `profile.md` has the habit frequencies for reading. Ask if anything is wrong. Edit lines they disagree with (delete a line to stop it being enforced; their own rules go under `<!-- KEEP BELOW -->`, scoped with `## chat` / `## email` / `## all`). Only hard rules belong here, things their real messages never do; tendencies are not sent to the drafter at all, because the archive carries them and stating them as rules caused caricature in testing.

9. **Next steps** (one line each): draft with `/write-like-me:write-as-me` or just ask "write this in my voice"; after sending an edited draft, share what you actually sent with `/write-like-me:voice-learn`; optionally run `/write-like-me:voice-check` for a blind test.

## Privacy recap to give the user (short)

Pack location; file permissions are user-only (folders 0700, files 0600); nothing leaves the machine during extraction and build. When drafting, about the budget's worth of their real messages (say it in messages, from step 6) plus the person file's core, if they built one, are sent to the model and account their Claude Code uses. Building a person file sends the chosen archive to that model once, in chunks. To delete everything: remove the pack folder.
