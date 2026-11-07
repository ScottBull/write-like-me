# write-like-me

A Claude Code plugin that learns how **you** write from text you've actually sent, then drafts chat replies, emails and docs that sound like you instead of like an AI assistant.

It builds a small **voice pack** on your machine: a bank of your real messages, measured habits (length, openers, punctuation, how polished you are), and editable rules. When you ask Claude to "reply to Dana saying Thursday works", it pulls your most relevant past messages, drafts, checks the draft against your habits, has a separate critic point out what gives it away, and revises once. You get back just the text.

Built for Claude Code (the CLI). Core scripts are Python 3.9+ standard library only.

## Install

In Claude Code:

```
/plugin marketplace add ScottBull/write-like-me
/plugin install write-like-me@write-like-me
```

Then restart Claude Code or run `/reload-plugins`.

## Quickstart

1. **Build your voice pack** (once, about 5 minutes):
   ```
   /write-like-me:voice-setup
   ```
   Claude looks for writing sources on your machine, asks before using each one, extracts only what you sent, and shows you the rules it measured so you can correct them.

2. **Draft something:**
   ```
   /write-like-me:write-as-me reply to Dana: Thursday works, I'll bring the slides
   ```
   or just say "write this in my voice: ..." or "draft an email to the team about ...".

3. **Teach it** when you edit a draft before sending: paste what you actually sent and say "here's what I sent" (or run `/write-like-me:voice-learn`). It diffs the two, proposes notes or rules, and asks before saving anything.

4. **Optional, test it:** `/write-like-me:voice-check` runs a blind test on **your real messages**. It holds out some of them, has Claude write each one from a content-only summary, and gives you a page where you pick your real message from each pair. It also scores stylometric closeness and runs an LLM judge. The page says at the top whether it holds your real messages or demo data: anything built from the repo's synthetic test fixtures (`tests/fixtures/`, invented people like "Jordan Example") is marked with a yellow **DEMO** banner and its score means nothing about your voice.

## Sources

| source | register | platform | notes |
|---|---|---|---|
| Claude Code history (`~/.claude`, or `$CLAUDE_CONFIG_DIR`) | prompt | any | Only what you typed. Pastes, slash commands, hook output and text Claude wrote are removed. Good for punctuation and capitalisation, weak for conversational word choice, so it supplements other sources rather than standing alone. |
| Microsoft Teams (new client, local cache) | chat | macOS | Needs the optional open-source parser [ccl_chromium_reader](https://github.com/cclgroupltd/ccl_chromium_reader) (MIT, not bundled; upstream targets Python 3.10+, but it also loads on macOS's stock 3.9). Setup offers to install it into the pack folder at pinned commits, showing the exact commands first (`wlm vendor-install teams`, then `--yes`). The cache covers recent history only. |
| Outlook for Mac (legacy) sent mail | email | macOS | Reads a copy of the local cache plus AppleScript, read-only. New Outlook has no local store, so use exported `.eml` files instead. |
| Folder of `.txt` / `.md` / `.eml` / `.mbox` | doc / email | any | Email is kept only when it's from your address. A Gmail Takeout of your Sent label works. Text files are read as documents (`> ` quoted blocks dropped); a file of chat messages separated by `---` lines goes in as pasted samples instead. |
| Pasted samples | any | any | Paste a few messages into the chat. |
| iMessage via [imessage-exporter](https://github.com/ReagentX/imessage-exporter) | chat | macOS | Optional. Needs `brew install imessage-exporter` and Full Disk Access for your terminal. |

On other platforms, the macOS-only sources report themselves as unavailable and setup carries on with the rest.

## Privacy model

- **Everything stays local.** The voice pack lives in `~/.claude/voice-pack/`, or wherever `WRITE_LIKE_ME_PACK` points. Files are readable only by you: the CLI creates every folder 0700 and every file 0600, and each `build` re-applies that to the whole pack. Nothing is uploaded, committed or synced by this plugin. By default, email addresses and phone numbers are masked in the exemplar bank, including recipient fields and email subjects (a bare address is reduced to its local part and organisation, e.g. `orders (millstoneflour)`, or just the local part for free-mail domains).
- **Only your own sent text** goes in the pack. Quoted replies, forwarded text, signatures and other people's messages are stripped. The message you were replying to is kept as a short context excerpt so examples make sense.
- **What gets sent to a model:** when you draft, about a dozen of your real messages plus your rules go into the prompt. They go to whatever model and account your Claude Code uses, the same as anything else you paste into Claude Code. `voice-check` makes extra calls through your own `claude` login; the prompt goes on stdin, not the command line, so other local users can't see it in `ps`.
- **Workplace data:** Teams, Outlook and work documents belong to your employer. Check your employer's policy on using work messages with AI tools before using those sources. Setup reminds you and asks first.
- **To delete everything:** remove the pack folder.

## How it works

```
voice-setup   extract -> clean -> de-duplicate -> tag register + recipient -> measure -> write pack
write-as-me   pick register -> retrieve exemplars -> draft -> lint -> critique (subagent) -> revise once
voice-learn   diff draft vs. sent -> propose notes/rules -> (you confirm) -> update pack
voice-check   hold out real messages -> briefs -> scripted drafts -> blind-test page + scores
```

**The voice pack**

| file | what |
|---|---|
| `profile.md` | human-readable summary of your habits per register |
| `rules.md` | what drafting follows: never/always rules, length targets, frequency-calibrated tendencies, and your measured level of imperfection. Edit it freely. Anything under `<!-- KEEP BELOW -->` survives rebuilds. |
| `exemplars.jsonl` | your cleaned messages, tagged by register (chat / email / doc / prompt), recipient and context |
| `stats.json` | the numbers behind the rules |
| `learned.md` | notes from your edits. Never overwritten. |

**Design choices that came out of testing** (see [docs/research.md](docs/research.md)):

- **Real examples over descriptions.** Each draft sees 6 fixed exemplars plus 3 retrieved ones. The fixed six are chosen so that, as a set, your optional habits (acknowledgment openers, hedges, questions, "!", spaced hyphens and slashes, parentheses, no final period) appear at about their real rates, across short / medium / long messages and different people, with no links, attachment lead-ins or group-chat posts. Research run 2 showed why: an earlier version that picked by length and recipient only got flat, habit-free messages, and its drafts scored clearly lower on every style-embedding metric. Retrieval prefers the same recipient, then the closest situation, using a stdlib TF-IDF.
- **Rules are frequencies, not commands.** "Opens with an acknowledgment word in about 1 in 4 messages" instead of "Open with 'Sure,'". Command-style rules made models overuse signature habits until the output read like a caricature. Only habits that are truly absent from a decent sample (for example, semicolons for someone who never uses them) become hard rules.
- **Permitted imperfection.** Real messages have fragments, missing final punctuation, lowercase names and unexplained context. The pack measures your level and tells the drafter not to tidy past it. It never adds typos on purpose.
- **Length targets** come from your real median and spread, because AI drafts run long.
- **A deterministic lint** checks hard rules, AI-isms you never use, length, and habit stacking. It also watches the last 20 drafts, so a habit you use 25% of the time doesn't show up in 70% of them.
- **One critique-and-revise pass.** A separate critic sees the draft next to 5 more of your real messages and names at most 4 tells. It judges style only and never suggests adding content.

## Results (research run 1)

The pipeline was chosen in an experiment on one author's 1:1 work-chat messages. There were 20 held-out messages and 8 prompting strategies, all on Claude Opus 5.5. Each held-out message was turned into a content-only brief, rewritten by each strategy, and then compared with the real message. All numbers are aggregate.

| strategy | judge 1 fooled* | judge 2 fooled* | stylometric closeness** |
|---|---|---|---|
| no help (default Claude) | 8% | 2% | 0.29 |
| style description only | 8% | 8% | 0.73 |
| 6 fixed examples | 10% | 5% | 0.69 |
| examples + rules + avoid list | 8% | 8% | 0.78 |
| + retrieved same-recipient examples | 12% | 5% | 0.84 |
| **+ critique-and-revise (this plugin)** | **18%** | **12%** | **0.92** |
| examples from AI-prompt history only | 0% | 0% | 0.75 |
| half chat, half prompt examples | 8% | 5% | 0.87 |

\* Forced-choice judge, n = 40 trials per strategy (both orders). Judge 1 was an OpenAI GPT model and judge 2 was Claude. The fooling rate is how often the judge picked the AI version as the real one: 50% means indistinguishable, 0% means always caught. For calibration, the same judges were fooled 32% of the time by *other people's* real messages and 0-2% by default Claude. 95% CIs are wide (about ±12 points).
\*\* 0 = like other people's messages, 1 = like the author's own held-out messages, measured as mean |z| over ~20 habits.

What we took from it:

- The full pipeline was best on every robust signal.
- Examples from AI-prompt history alone were the worst. They carry mechanics, not conversational vocabulary.
- The main failure modes were:
  - **caricature:** an acknowledgment opener in 70% of drafts vs. about 26% of real messages
  - **too polished:** judges caught fakes for being tidier than the person
  - **too long**

**Run 2 (this plugin as shipped, same 20 held-out messages):** judge 1 fooled 38% [24-53], judge 2 fooled 18% [9-32], stylometric closeness 0.96. The same eval run scored the run-1 winner at 18% and 12%. Run 2 also caught a regression: automatically chosen fixed exemplars that were too flat and too short. That was fixed before release (see the exemplar note above).

The current rules format, imperfection section, length targets and lint were designed against these three. One author and one register is a small sample, so expect your mileage to vary. Use `/write-like-me:voice-check` to measure your own.

## Commands reference

The skills drive a single CLI, which you can also run yourself:

```
python3 -I ~/.claude/plugins/.../write-like-me/scripts/wlm.py --help
```

`discover`, `extract`, `vendor-install teams`, `add-samples`, `build`, `status`, `kit`, `lint`, `critique-prompt`, `critique`, `log-draft`, `learn diff|add|note|rule`, `check prepare|generate|page|score`.

## Development

```
python3 -m unittest discover -s tests      # offline, synthetic fixtures only (invented people and messages)
claude plugin validate .
claude --plugin-dir .                      # try the plugin without installing
```

## License

MIT. See [LICENSE](LICENSE).
