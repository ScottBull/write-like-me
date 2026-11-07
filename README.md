# write-like-me

A Claude Code plugin that learns how **you** write from text you've actually sent, then drafts chat replies, emails, posts and docs that sound like you instead of like an AI assistant.

It builds a local **voice pack**: an archive of your own messages, each with what it replied to, in date order. When you ask Claude to "reply to Dana saying Thursday works", the plugin picks a budgeted selection of that archive, puts it in front of the model framed as *"this is my archive; write the next entry"*, and makes one call. No style rules in the prompt (just a short note after the archive on how your messages vary: typical length, how often they end without punctuation or open with "Yea,"), no critic, no rewrite. A short lint catches the few things you never do (a dash, a sign-off, a phrase you'd never use), and you get back just the text.

Optionally, it also distils a short, cited **person file** (what you know, do and think) from the same archive, so drafts from thin notes fill the gaps with things you would actually say rather than generic filler.

Built for Claude Code (the CLI). Core scripts are Python 3.9+ standard library only.

## Install

In Claude Code:

```
/plugin marketplace add ScottBull/write-like-me
/plugin install write-like-me@write-like-me
```

Then restart Claude Code or run `/reload-plugins`.

**Upgrading from 0.1:** your pack still works for reading, but drafting needs the new archive format. Run `/write-like-me:voice-setup` (or `wlm build`) once; it rebuilds the pack in place from your extracted sources, keeps your own rules, learned notes and logged drafts, and deletes the old example bank. `wlm status` tells you when a pack needs this.

## Quickstart

1. **Build your voice pack** (once, about 5 minutes):
   ```
   /write-like-me:voice-setup
   ```
   Claude looks for writing sources on your machine, asks before using each one, extracts only what you sent, builds the archive, and tells you in plain words how much of it each draft will see ("about 60 of your chat messages"). It then offers the person file as a separate choice, personal and work data asked about separately, with a dry run showing the number of model calls first, and lets you read and prune what it wrote.

2. **Draft something:**
   ```
   /write-like-me:write-as-me reply to Dana: Thursday works, I'll bring the slides
   ```
   or just say "write this in my voice: ..." or "draft an email to the team about ...". The draft runs in a `voice-writer` subagent by default, so your archive never lands in your main conversation.

3. **Teach it** when you edit a draft before sending: paste what you actually sent and say "here's what I sent" (or run `/write-like-me:voice-learn`). It diffs the two and proposes, with your OK, adding the sent message to the archive (the strongest fix), a claim for the person file, a soft note, or a hard rule.

4. **Optional, test it:** `/write-like-me:voice-check` runs a blind test on **your real messages**. It holds out some of them, has the plugin write each one from a content-only summary, and gives you a page in two parts: pairs (pick your real message) and lineups (four messages, three yours, spot the one you didn't write). It also scores stylometric closeness and runs an LLM judge on both. The page says at the top whether it holds your real messages or demo data: anything built from the repo's synthetic test fixtures (`tests/fixtures/`, invented people like "Jordan Example") is marked with a yellow **DEMO** banner and its score means nothing about your voice.

## Sources

| source | register | platform | notes |
|---|---|---|---|
| Claude Code history (`~/.claude`, or `$CLAUDE_CONFIG_DIR`) | prompt | any | Only what you typed. Pastes, slash commands, hook output and text Claude wrote are removed. Good for punctuation and capitalisation, weak for conversational word choice, so it supplements other sources rather than standing alone. |
| Microsoft Teams (new client, local cache) | chat | macOS | Needs the optional open-source parser [ccl_chromium_reader](https://github.com/cclgroupltd/ccl_chromium_reader) (MIT, not bundled; upstream targets Python 3.10+, but it also loads on macOS's stock 3.9). Setup offers to install it into the pack folder at pinned commits, showing the exact commands first (`wlm vendor-install teams`, then `--yes`). The cache covers recent history only. |
| Outlook for Mac (legacy) sent mail | email | macOS | Reads a copy of the local cache plus AppleScript, read-only. New Outlook has no local store, so use exported `.eml` files instead. |
| Folder of `.txt` / `.md` / `.eml` / `.mbox` | doc / email | any | Email is kept only when it's from your address. A Gmail Takeout of your Sent label works. Text files are read as documents (`> ` quoted blocks dropped); a file of chat messages separated by `---` lines goes in as pasted samples instead. |
| Pasted samples | any | any | Paste a few messages into the chat. Public posts and comments (Reddit, forums) go in as register `post`, with the subreddit or forum as the recipient. |
| iMessage via [imessage-exporter](https://github.com/ReagentX/imessage-exporter) | chat | macOS | Optional. Needs `brew install imessage-exporter` and Full Disk Access for your terminal. |

On other platforms, the macOS-only sources report themselves as unavailable and setup carries on with the rest.

## Privacy model

- **Everything stays local.** The voice pack lives in `~/.claude/voice-pack/`, or wherever `WRITE_LIKE_ME_PACK` points. Files are readable only by you: the CLI creates every folder 0700 and every file 0600, and each `build` re-applies that to the whole pack. Extraction and `build` make no network calls, and the plugin uploads, commits or syncs nothing; the only data that leaves your machine is the model calls described below, made through your own Claude Code login. By default, email addresses and phone numbers are masked in the archive, including recipient fields and email subjects (a bare address is reduced to its local part and organisation, e.g. `orders (millstoneflour)`, or just the local part for free-mail domains).
- **Only your own sent text** goes in the pack. Quoted replies, forwarded text, signatures and other people's messages are stripped. The message you were replying to is kept as a short context excerpt so entries make sense.
- **What gets sent to a model when you draft:** a selection of your real messages up to the pack's token budget (`wlm status` prints it; setup says it in messages), plus your person file only if you turn it on (`--person core`, or `person_default` in `stats.json`; it is off by default), plus the `learned.md` notes that apply to that register and recipient, plus your notes for the new message. They go to whatever model and account your Claude Code uses, the same as anything else you paste into Claude Code. Both person files, when you have both, go into every draft whatever its register (list the ones to use in `stats.json` as `"person_files": ["person.md"]` to choose), and a person file that doesn't fit the budget is left out of that draft. By default the draft runs in a subagent, so the archive is not in your main conversation's context. The `wlm draft` fallback and `voice-check` make calls through your own `claude` login with the archive as a system-prompt file and the task on stdin, not the command line, so other local users can't see it in `ps`; the child process never sees `ANTHROPIC_API_KEY`, so the calls go on your Claude Code login (a Bedrock, Vertex, Foundry or gateway setup is passed through unchanged); if your Claude Code itself runs on an API key, set `WRITE_LIKE_ME_KEEP_API_KEY=1` to keep it.
- **The person file is a profile of you on disk**, and building it makes model calls over the sources you choose: your posts and comments and your iMessage chats for `person.md` (documents and chat from `extract files`, pasted samples and `learn add` messages may be work material, so they go in only when you say they're personal, `--include-unsorted`), your whole work archive (Teams, Outlook, Claude Code prompts) for `person.work.md`, in ~40k-token chunks, once. It is optional, asked about separately from the archive, personal and work separately, and the number of calls is shown as a dry run first. Every claim in it cites the entries it came from (`person.citations.json`), you can read exactly what drafts will see (`wlm person show`), and `wlm person forget` deletes a claim and blacklists it so a rebuild never brings it back.
- **What `voice-check` sends:** the held-out messages (to write content-only summaries of them), one draft call per held-out message with the same budget of your other messages as a normal draft, a plain baseline call, and, for the LLM judge, the held-out and generated messages with a few of your other messages as references. `prepare` prints the number of calls first.
- **Workplace data:** Teams, Outlook and work documents belong to your employer. Check your employer's policy on using work messages with AI tools before using those sources. Setup reminds you and asks first, and again before a work person file.
- **To delete everything:** remove the pack folder.

## How it works

```
voice-setup   extract -> clean -> de-duplicate -> tag register + recipient -> measure
              -> archive/<register>.jsonl + rules.md (hard rules) + profile.md
              -> optional: person build (model calls, consent) -> person.md / person.work.md
write-as-me   wlm context (select entries within the budget, frame as "write the next entry")
              -> one draft (voice-writer subagent; fallback `wlm draft`) -> lint hard rules -> log
voice-learn   diff draft vs. sent -> propose: add to archive | person claim | note | rule -> (you confirm)
voice-check   hold out real messages -> briefs -> scripted drafts -> pairs + lineups page -> scores
```

**The voice pack (format v2)**

| file | what |
|---|---|
| `sources/*.jsonl` | what the extractors found: the ground truth everything else is rebuilt from |
| `archive/<register>.jsonl` | your cleaned, masked messages in date order, each with its date, recipient, thread, the message it replied to, and an estimated token cost. This is what the drafter reads. |
| `stats.json` | the measured habits, archive sizes, and the pack's defaults: `budget_default` (tokens of archive + person file + learned notes + habits block per draft), `strategy_default` (how entries are picked), and, if you add them, `person_default` (which person-file tier goes in; `none` when unset), `habits_default` (`on` or `off`: the "How my messages vary" block; `off` when unset) and `person_files` (which person files drafts use; both when unset) |
| `rules.md` | hard rules only: things your real messages never do, the AI-isms you never use, spelling preferences. Lint enforces them. Edit it freely; anything under `<!-- KEEP BELOW -->` survives rebuilds. |
| `profile.md` | human-readable summary of your habits per register, including the tendencies ("hedges in about half of messages"). For you to read. Drafts see none of these numbers unless the habits block (below) is turned on. |
| `person.md`, `person.work.md` | optional: a cited description of what you know, do and think, in a core tier (~3k tokens) and a full tier (~10k). Built by `wlm person build`, edited by `wlm person add` / `forget`. |
| `person.citations.json`, `person.blacklist.json` | the citations behind every claim; what you've forgotten (its `terms` list is hand-editable: a rebuild drops any claim containing one) |
| `learned.md` | notes from your edits (`wlm learn note`). The ones for the draft's register and recipient go into its context as a short "notes from my edits" block after the archive, counted in the budget. Never overwritten. |
| `drafts.jsonl` | every final draft, for lint's caricature counter and for voice-learn |

**The habits block.** After the archive and the learned notes, the context ends with a short "How my messages vary" note built from `stats.json` for the draft's register: the typical length (median and the middle half, in words), how often your messages end without final punctuation, how often they open with an acknowledgment word and which ones, how often they have "!" and a hedge, and any mark you never use that is already a hard rule. It is phrased as frequencies over the whole archive ("about 1 in 5 has no final punctuation"), never as instructions, and ends by saying that any single message has few of them. It is counted in the budget. **It is off by default.** It was built because on Teams chat the archive alone left drafts ending every message with a period, running long and over-using the "Yea," opener, but in the final held-out test it did not fix that (drafts still ended every message with a period) and it made them easier to spot: on Teams the lineup judge spotted 45% of drafts with it against 32% without (25% is chance), and on Reddit all three style-embedding bundle scores were lower with it. `--habits on` (on `wlm context` and `wlm check generate`) or `"habits_default": "on"` in `stats.json` puts it in.

**Design choices that came out of testing** (see [docs/research.md](docs/research.md)):

- **The archive, not rules.** The earlier version of this plugin was a lint: a few examples, a list of counted habits stated as frequencies, a critic naming tells, a rewrite. It won on what you can count (dashes, openers, length) and had nothing to say about the texture of the prose, which is most of what makes writing recognisably one person's. A pilot put the whole archive in context framed as "write the next entry" and roughly doubled every style-embedding score on long-form writing, with the same number of calls. Hundreds of examples are a different regime from the few-shot the literature warns about.
- **A budget, selected to cover situations.** The default selection strategy fills the budget across recipients, length and years, newest first within each stratum, so a small budget still shows the model how you write to different people at different lengths. Other strategies exist (`wlm context --strategy`), including matching entries to the request, which the literature says copies content; the default is the one the ablation picked.
- **The person file is an input, never a judge.** A profile judged against itself always wins, so it is never used to score anything. Every claim needs two independent citations (a plainly stated biographical fact may have one), uncited sentences are dropped rather than fixed, and you can prune it.
- **No deliberate imperfection.** Real messages have typos and doubled words; the plugin never adds them, and that is where most of a paired judge's remaining catches live. Which is why the test reports a lineup rate too.
- **Hard rules only, as a lint.** Frequency rules in the prompt caused caricature (an acknowledgment opener in 70% of drafts against 26% of real messages). They now live in `profile.md` for you to read, and lint keeps a caricature counter across your recent drafts.
- **One draft call.** The critique-and-revise step cut 4-9% of the content the notes asked for and trimmed long drafts to about 60% of real length. Content now comes through intact.

## Results

All numbers are aggregates from one author's own messages; no message text is in this repository. Methods and the full tables are in [docs/research.md](docs/research.md).

**The pilot** (long-form: 20 held-out forum comments, median 54 words; Claude Opus 5.5; 0 = like other people's writing, 1 = like the author's own held-out writing on three style-embedding models):

| | LUAR | StyleDistance | Wegmann | length vs real |
|---|---|---|---|---|
| default Claude | -0.49 | -1.29 | -0.42 | 1.24x |
| 0.1 pipeline (examples + rules + critic), 2 samples | 0.26 / 0.39 | -0.09 / 0.13 | 0.00 / 0.02 | 0.89x |
| **whole archive in context (257k tokens)**, 2 samples | **0.65 / 0.61** | **0.41 / 0.23** | **0.50 / 0.33** | 1.05x |

On LUAR paired detectability the archive drafts were nearly as hard to tell from the author's real comments as his own other comments are (0.80 vs 0.75). On short work chat (median 20 words) the archive tied the best engineered condition on embeddings without beating it; the mechanics that dominate short chat were already won by the rules.

**The budget ablation** decided how much archive a draft needs and how to pick it:

| (held-out Reddit comments, 2 samples) | LUAR bundle | SD bundle | Wegmann bundle | LUAR detect (0.5 best) | lineup: judge spots the fake (chance 25%) |
|---|---|---|---|---|---|
| 0.1 pipeline | 0.33 | 0.02 | 0.01 | 0.95 | 50% |
| whole archive (257k tokens) | 0.63 | 0.32 | 0.42 | 0.80 | 40% |
| **0.2 default: compact selection, 10k tokens** | **0.90** | **0.94** | **0.91** | **0.78** | **40%** |

- **Less is more.** 5-10k tokens of archive (about 60-100 messages) beat 25k, 50k and the 257k pilot on every style score. Past about 10k, extra archive made drafts more generic, not more like the person. The default is strategy C (situations covered, replied-to text cut short, so more of the person's own messages fit) at 10,000 tokens.
- **Work chat:** on 20 held-out Teams messages, 0.2 is level with 0.1 within noise (lineup: judge spotted 32% vs 38%; paired: fooled 30% vs 40%). 0.1's rules still win on surface habits like leaving off the final period. A measured-habits block meant to fix that made drafts worse, so it is off by default (`--habits on` to try it).
- **The person file lost at every budget:** its tokens buy less than the same tokens of real messages. It is off by default and stays available (`--person core`).
- **Judges catch long text by its polish.** Paired judges still pick the real Reddit comment 95-100% of the time; about half their stated reasons are typos and roughness the plugin never adds on purpose. The lineup test (is one of these four not like the others?) is closer to how a reader meets a message, and is what `voice-check` now reports.

One author and a few registers is a small sample, so expect your mileage to vary. Use `/write-like-me:voice-check` to measure your own.

## Commands reference

The skills drive a single CLI, which you can also run yourself:

```
python3 -I ~/.claude/plugins/.../write-like-me/scripts/wlm.py --help
```

| command | what |
|---|---|
| `discover`, `extract <source>`, `vendor-install teams`, `add-samples` | find and extract your sent writing |
| `build` | clean, measure, write the archive and the pack files; upgrades a 0.1 pack in place |
| `status` | what's in the pack (counts, archive sizes, defaults, pack format version) |
| `context --register R --notes ... [--context ...] [--recipient X] [--subject S] [--budget N] [--strategy R\|D\|M\|T\|C\|P] [--person core\|full\|none] [--habits on\|off]` | select entries within the budget and write the draft's context files (`<pack>/work/ctx-<hash>/system.txt`, `task.txt`); prints the paths and counts |
| `draft --system-file F --task-file T [--model M]` | one `claude -p` draft from those files (the fallback when the subagent is unavailable) |
| `lint --register R --file F` | hard-rule checks; the first line gives the word count against your median for the register |
| `log-draft --register R --file F [--context-dir D]` | remember a final draft |
| `learn diff\|add\|note\|rule` | learn from what you actually sent |
| `person build [--which personal\|work\|both] [--include-unsorted] [--yes]`, `person show [--tier core\|full] [--ids]`, `person add --text ...`, `person forget <id or text>` | the person file; `build` is a dry run until `--yes` |
| `check prepare\|generate\|page\|score` | blind test (pairs and lineups) and scoring |

## Development

```
python3 -m unittest discover -s tests      # offline, synthetic fixtures only (invented people and messages)
claude plugin validate .
claude --plugin-dir .                      # try the plugin without installing
```

## License

MIT. See [LICENSE](LICENSE).
