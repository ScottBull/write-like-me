# write-like-me

A Claude Code plugin that learns how **you** write from text you've actually sent, then drafts chat replies, emails, posts and docs that sound like you instead of like an AI assistant.

It builds a local **voice pack**: an archive of your own messages, each with what it replied to, in date order. When you ask Claude to "reply to Dana saying Thursday works", the plugin picks 60-100 of those messages (a 10,000-token budget, chosen to cover different people, lengths and years), shows them to the model as *"this is my archive; write the next entry"*, and makes one call. There are no style rules in the prompt, no critic and no rewrite. A short lint checks the few things you never do (a dash, a sign-off, a phrase you'd never use), and you get back just the text.

Built for Claude Code (the CLI). Core scripts are Python 3.9+ standard library only.

## Install

In Claude Code:

```
/plugin marketplace add ScottBull/write-like-me
/plugin install write-like-me@write-like-me
```

Then restart Claude Code or run `/reload-plugins`.

**Upgrading from 0.1:** run `/write-like-me:voice-setup` (or `wlm build`) once. It rebuilds the pack in the new archive format from your extracted sources and keeps your own rules, learned notes and logged drafts. `wlm status` tells you when a pack needs this.

## Quickstart

1. **Build your voice pack** (once, about 5 minutes):
   ```
   /write-like-me:voice-setup
   ```
   Claude looks for writing sources on your machine, asks before using each one, extracts only what you sent, and tells you in plain words how much of it each draft will see ("about 100 of your chat messages").

2. **Draft something:**
   ```
   /write-like-me:write-as-me reply to Dana: Thursday works, I'll bring the slides
   ```
   or just say "write this in my voice: ..." or "draft an email to the team about ...". The draft runs in a `voice-writer` subagent, so your archive never lands in your main conversation.

3. **Teach it** when you edit a draft before sending: paste what you actually sent and say "here's what I sent" (or run `/write-like-me:voice-learn`). It proposes, with your OK, adding the sent message to the archive (the strongest fix), a soft note, or a hard rule.

4. **Optional, test it:** `/write-like-me:voice-check` holds out some of your real messages, has the plugin rewrite each one from a content-only summary, and gives you a blind-test page: pairs (pick your real message) and lineups (four messages, three yours: spot the one you didn't write). Pages built from the repo's synthetic test fixtures carry a yellow **DEMO** banner.

## Sources

| source | register | platform | notes |
|---|---|---|---|
| Claude Code history (`~/.claude`, or `$CLAUDE_CONFIG_DIR`) | prompt | any | Only what you typed: pastes, slash commands, hook output and Claude's text are removed. Good for mechanics, weak for conversational word choice. |
| Microsoft Teams (new client, local cache) | chat | macOS | Needs the optional open-source parser [ccl_chromium_reader](https://github.com/cclgroupltd/ccl_chromium_reader) (MIT, not bundled). Setup offers to install it at pinned commits and shows the commands first. The cache covers recent history only. |
| Outlook for Mac (legacy) sent mail | email | macOS | Reads a copy of the local cache plus AppleScript, read-only. The cache holds about a month; for more, export your Sent folder as `.eml` and use the folder source. |
| Folder of `.txt` / `.md` / `.eml` / `.mbox` | doc / email | any | Email is kept only when it's from your address. A Gmail Takeout of your Sent label works. |
| Pasted samples | any | any | Paste messages into the chat. Public posts and comments (Reddit, forums) go in as register `post`. |
| iMessage via [imessage-exporter](https://github.com/ReagentX/imessage-exporter) | chat | macOS | Optional. Needs `brew install imessage-exporter` and Full Disk Access for your terminal. |

Drafts use the archive of the register you're writing in. A register with no samples borrows the closest one and says so (documents borrow your posts). **More samples help most where you have few:** with a couple of dozen emails, email drafts lean on generic email habits.

## Privacy model

- **Everything stays local.** The pack lives in `~/.claude/voice-pack/` (or `$WRITE_LIKE_ME_PACK`), with every folder 0700 and every file 0600. Extraction and `build` make no network calls, and the plugin uploads, commits or syncs nothing. Email addresses and phone numbers are masked in the archive by default.
- **Only your own sent text** goes in the pack. Quoted replies, forwards, signatures and other people's messages are stripped; the message you replied to is kept as a short excerpt so entries make sense.
- **What a draft sends to the model:** about 10,000 tokens of your real messages, any `learned.md` notes for that register and recipient, and your notes for the new message. It goes to whatever model and account your Claude Code uses, like anything else you paste into Claude Code. The `wlm draft` fallback and `voice-check` call your own `claude` login with the prompt on stdin, never the command line; the child process never sees `ANTHROPIC_API_KEY`, so calls stay on your Claude Code login (set `WRITE_LIKE_ME_KEEP_API_KEY=1` if your Claude Code itself runs on an API key).
- **`voice-check`** sends the held-out messages (to summarise them), one draft call and one plain baseline per held-out message, and, for the optional LLM judge, the held-out and generated messages. `prepare` prints the number of calls first.
- **The optional person file** (below) is a profile of you on disk, built by model calls over the sources you choose, in ~40k-token chunks, after a dry run that shows the number of calls.
- **Workplace data:** Teams, Outlook and work documents belong to your employer. Check your employer's policy on using work messages with AI tools before using those sources. Setup reminds you and asks first.
- **To delete everything:** remove the pack folder.

## How it works

```
voice-setup   extract -> clean -> de-duplicate -> tag register + recipient -> measure
              -> archive/<register>.jsonl + rules.md (hard rules) + profile.md
write-as-me   wlm context (select entries within the budget, frame as "write the next entry")
              -> one draft (voice-writer subagent; fallback `wlm draft`) -> lint hard rules -> log
voice-learn   diff draft vs. sent -> propose: add to archive | note | rule -> (you confirm)
voice-check   hold out real messages -> briefs -> scripted drafts -> pairs + lineups page -> scores
```

| pack file | what |
|---|---|
| `sources/*.jsonl` | what the extractors found; everything else is rebuilt from it |
| `archive/<register>.jsonl` | your cleaned, masked messages in date order, each with its date, recipient, thread and the message it replied to. This is what the drafter reads. |
| `stats.json` | measured habits, archive sizes and the pack's defaults (`budget_default`, `strategy_default`, and optionally `person_default`, `habits_default`) |
| `rules.md` | hard rules only: things your messages never do, AI-isms you never use, spelling preferences. Lint enforces them. Anything under `<!-- KEEP BELOW -->` survives rebuilds. |
| `profile.md` | a human-readable summary of your habits, for you to read |
| `learned.md` | notes from your edits; the ones for the draft's register and recipient go into its context. Never overwritten. |
| `drafts.jsonl` | your final drafts, for lint's overuse counter and for voice-learn |
| `person.md`, `person.work.md` | optional person file (below) |

**Why it works this way** (details in [docs/research.md](docs/research.md)):

- **The archive, not rules.** Version 0.1 was a lint: a few examples, counted habits stated as frequencies, a critic and a rewrite. It won on what you can count (dashes, openers, length) and missed the texture of the prose, which is most of what makes writing recognisably yours. Showing the model a large slice of your own writing as an archive to continue roughly tripled the style scores on long-form writing.
- **Less is more.** 5-10k tokens of archive beat 25k, 50k and the whole archive (257k). Past about 10k, extra writing made drafts more average, not more like the person. The default selection covers different recipients, lengths and years, and cuts the replied-to text short so more of your own messages fit.
- **One call.** The 0.1 critique-and-revise step cut 4-9% of the points a draft was asked to make and trimmed long drafts. Drafts now keep their content.
- **No deliberate imperfection.** Real messages have typos and doubled words. The plugin never adds them.

**Optional extras, off by default** because neither helped in testing: a **person file** (`wlm person build`: a cited description of what you know, do and think, with `show` / `add` / `forget` to read and prune it; turn it on per draft with `--person core`), and a **habits note** (`--habits on`: your measured frequencies, such as typical length and how often you skip a final period).

## Results

One author's own held-out messages, Claude Opus 5.5; aggregates only, no message text is in this repository. Methods and full tables: [docs/research.md](docs/research.md).

| | style match, Reddit comments (0 = other people, 1 = the author) | judge spots the draft in a lineup (chance 25%) |
|---|---|---|
| default Claude | -0.49 | n/a |
| 0.1 (examples + rules + critic) | 0.33 | Reddit 50%, Teams 38% |
| **0.2 (archive, 10k tokens)** | **0.90** | **Reddit 40%, Teams 32%** |

- **Long-form writing is where 0.2 is a clear step up.** On short work chat it is level with 0.1 within noise; 0.1's rules still win on small surface habits like leaving off the final period.
- **Paired judges still catch long text by its polish:** shown the real comment beside the draft, they pick the real one almost every time, mostly citing typos and roughness the plugin never adds. The lineup test is closer to how a reader meets a message, and it is what `voice-check` reports.

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
| `status` | what's in the pack (counts, archive sizes, defaults, format version) |
| `context --register R --notes ... [--context ...] [--recipient X] [--subject S] [--budget N] [--strategy R\|D\|M\|T\|C\|P] [--person core\|full\|none] [--habits on\|off]` | select entries and write the draft's context files; prints their paths and counts |
| `draft --system-file F --task-file T [--model M]` | one `claude -p` draft from those files (fallback when the subagent is unavailable) |
| `lint --register R --file F` | hard-rule checks, plus the word count against your median |
| `log-draft --register R --file F [--context-dir D]` | remember a final draft |
| `learn diff\|add\|note\|rule` | learn from what you actually sent |
| `person build [--which personal\|work\|both] [--include-unsorted] [--yes]`, `person show`, `person add`, `person forget` | the optional person file; `build` is a dry run until `--yes` |
| `check prepare\|generate\|page\|score` | blind test (pairs and lineups) and scoring |

## Development

```
python3 -m unittest discover -s tests      # offline, synthetic fixtures only (invented people and messages)
claude plugin validate .
claude --plugin-dir .                      # try the plugin without installing
```

## License

MIT. See [LICENSE](LICENSE).
