# write-like-me

A Claude Code plugin that learns how **you** write from text you’ve actually sent, then drafts chat replies, emails, posts and docs that sound like you and not like an AI assistant.

It builds a local **voice pack**, which is an archive of your own messages in date order, each one paired with what it was replying to. When you ask Claude to “reply to Dana saying Thursday works”, the plugin picks 60-100 of those messages (a 10,000-token budget, chosen to cover different people, lengths and years), shows them to the model as *"this is my archive; write the next entry"*, and makes a single call. No style rules in the prompt, no critic, no rewrite. A short lint checks for the handful of things you never do (a dash, a sign-off, a phrase you’d never use), and you get back just the text.

Built for Claude Code (the CLI). The core scripts only use the Python 3.9+ standard library.

## What it looks like

Same question, three answers: "What's the best way to get rid of fruit flies in my kitchen?" The two voice answers come from the same person's archive in two registers, so they're different lengths and shapes, the way the author's real Teams messages and Reddit comments are.

**Plain Claude:** 328 words, with bold numbered headings and bullets.

<details><summary>Show the plain Claude answer</summary>

> The quickest fix is to get rid of what they're breeding in. Traps catch the adults, but more will keep hatching until the source is gone.
>
> **1. Find and remove the source**
> - Throw out or refrigerate overripe fruit and vegetables. Check potatoes and onions for any that have gone soft or are rotting.
> - Take out the trash and recycling, and rinse bottles, cans and the bin itself.
> - Clean sink drains and the garbage disposal. Fruit flies often breed in the gunk inside. Pour boiling water down the drain, or use baking soda followed by vinegar, then scrub with a drain brush.
> - Look for hidden spots: spills under the fridge or stove, damp sponges and mops, pet food bowls, and houseplant soil that stays wet.
>
> **2. Trap the adults**
> - **Vinegar trap:** Put apple cider vinegar in a small jar with a drop of dish soap, which breaks the surface tension so the flies sink. Leave it open, or cover it with plastic wrap and poke a few small holes in it.
> - **Wine or beer trap:** A little leftover red wine or beer in a bottle works well too.
> - **Fruit trap:** Put a piece of overripe banana in a jar with a paper funnel in the top. Flies get in but can't find their way out.
> - Put traps where you see the most flies and refresh them every few days.
>
> **3. Keep them from coming back**
> - Wash produce when you bring it home, since eggs often come in on fruit.
> - Store ripe fruit in the fridge.
> - Wipe counters and empty the trash often, especially in warm weather.
>
> If you clear out the source and keep traps going, you should be fly-free in about a week. Their life cycle is roughly 8 to 10 days, so a few stragglers may show up before then. If they keep coming back, look for a drain or a piece of produce you missed.

</details>

**In the author's Teams voice** (a coworker asked, and the draft saw about 100 of his Teams messages), 47 words:

> Apple cider vinegar in a small cup with a drop of dish soap, then cover it with plastic wrap and poke a few holes in the top. Works surprisingly well. Also worth checking for any overripe fruit or the sink drain, that's usually where they're coming from.

**In the author's Reddit voice** (a reply to a Reddit post asking the same thing, from about 66 of his Reddit comments), 140 words:

> Apple cider vinegar trap works great. Small cup of ACV, a drop or two of dish soap to break the surface tension, and cover it with plastic wrap with a few small holes poked in it. They go in and can't get back out.
>
> But the trap only handles the adults, you'll want to find the source or they'll just keep coming back. Usual suspects are overripe fruit on the counter, the trash or compost bin, and the sink drain. Drains are the one people miss most often. Try pouring boiling water down it followed by some baking soda and vinegar, or tape a clear plastic bag over the drain overnight and see if anything's in it by morning.
>
> Once the source is gone and the trap is out, they should be pretty much gone within a week or so.

To get one yourself: `/write-like-me:write-as-me reply to this: any tips for getting rid of fruit flies?`

Each of these is a single sample from one author's pack, so your own pack gives you your own voice, and a re-run comes out somewhat different.

## Install

In Claude Code:

```
/plugin marketplace add ScottBull/write-like-me
/plugin install write-like-me@write-like-me
```

Then restart Claude Code or run `/reload-plugins`.

**Upgrading from 0.1:** Run `/write-like-me:voice-setup` (or `wlm build`) once. It rebuilds your pack in the new archive format from your extracted sources, and keeps your own rules, learned notes and logged drafts. `wlm status` will let you know when a pack needs this.

## Quickstart

1. **Build your voice pack** (one time, takes about 5 minutes):
   ```
   /write-like-me:voice-setup
   ```
   Claude looks for writing sources on your machine and asks before using each one. It only extracts what you sent, and tells you in plain terms how much of it each draft will see (“about 100 of your chat messages”).

2. **Draft something:**
   ```
   /write-like-me:write-as-me reply to Dana: Thursday works, I'll bring the slides
   ```
   Or just say “write this in my voice: ...” or “draft an email to the team about ...”. Drafting happens in a `voice-writer` subagent, so your archive never ends up in your main conversation.

3. **Teach it** when you edit a draft before sending. Paste what you actually sent and say “here’s what I sent” (or run `/write-like-me:voice-learn`). It’ll propose one of three things, and only with your OK: adding the sent message to the archive (the strongest fix), a soft note, or a hard rule.

4. **Optional, test it:** `/write-like-me:voice-check` holds out some of your real messages and has the plugin rewrite each one from a summary of just the content. Then it gives you a blind test page with pairs (pick your real message) and lineups (four messages, three are yours, spot the one you didn’t write). Any page built from the repo’s synthetic test fixtures gets a yellow **DEMO** banner.

## Sources

| source | register | platform | notes |
|---|---|---|---|
| Claude Code history (`~/.claude`, or `$CLAUDE_CONFIG_DIR`) | prompt | any | Only what you typed: pastes, slash commands, hook output and Claude's text are removed. Good for mechanics, weak for conversational word choice. |
| Microsoft Teams (new client, local cache) | chat | macOS | Needs the optional open-source parser [ccl_chromium_reader](https://github.com/cclgroupltd/ccl_chromium_reader) (MIT, not bundled). Setup offers to install it at pinned commits and shows the commands first. The cache covers recent history only. |
| Outlook for Mac (legacy) sent mail | email | macOS | Reads a copy of the local cache plus AppleScript, read-only. The cache holds about a month; for more, export your Sent folder as `.eml` and use the folder source. |
| Folder of `.txt` / `.md` / `.eml` / `.mbox` | doc / email | any | Email is kept only when it's from your address. A Gmail Takeout of your Sent label works. |
| Pasted samples | any | any | Paste messages into the chat. Public posts and comments (Reddit, forums) go in as register `post`. |
| iMessage via [imessage-exporter](https://github.com/ReagentX/imessage-exporter) | chat | macOS | Optional. Needs `brew install imessage-exporter` and Full Disk Access for your terminal. |

Each draft uses the archive for the register you’re writing in. If a register has no samples, it borrows the closest one and tells you (documents borrow from your posts). **More samples help most where you have the fewest.** If you only have a couple dozen emails, email drafts end up leaning on generic email habits.

## Privacy model

- **Everything stays local.** The pack lives in `~/.claude/voice-pack/` (or `$WRITE_LIKE_ME_PACK`). Every folder is 0700 and every file is 0600. Extraction and `build` don’t make any network calls, and the plugin never uploads, commits or syncs anything. Email addresses and phone numbers are masked in the archive by default.
- **Only text you sent yourself** goes in the pack. Quoted replies, forwards, signatures and other people’s messages all get stripped out. The message you were replying to is kept as a short excerpt so each entry still makes sense.
- **What a draft sends to the model:** about 10,000 tokens of your real messages, any `learned.md` notes for that register and recipient, and your notes for the new message. That goes to whatever model and account your Claude Code uses, same as anything else you paste into Claude Code. The `wlm draft` fallback and `voice-check` call your own `claude` login with the prompt on stdin, never on the command line. The child process never sees `ANTHROPIC_API_KEY`, so calls stay on your Claude Code login (set `WRITE_LIKE_ME_KEEP_API_KEY=1` if your Claude Code itself runs on an API key). If you start Claude Code through a wrapper script, point `WRITE_LIKE_ME_CLAUDE` at it and these calls will use it too.
- **`voice-check`** sends the held-out messages (to summarise them), one draft call and one plain baseline per held-out message, and, if you use the optional LLM judge, the held-out and generated messages. `prepare` prints how many calls it’ll make before it starts.
- **The optional person file** (below) is a profile of you saved on disk. It’s built by model calls over the sources you pick, in ~40k-token chunks, after a dry run that shows you the number of calls.
- **Workplace data:** Teams, Outlook and work documents belong to your employer. Check your employer’s policy on using work messages with AI tools before using any of those sources. Setup will remind you and ask first.
- **To delete everything:** just remove the pack folder.

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

**Why it works this way** (more detail in [docs/research.md](docs/research.md)):

- **The archive, not rules.** Version 0.1 was basically a lint: a few examples, counted habits written out as frequencies, a critic and a rewrite. It did well on the stuff you can count (dashes, openers, length) but missed the texture of the writing, which is most of what makes it recognisably yours. Showing the model a big slice of your own writing as an archive to continue roughly tripled the style scores on long-form writing.
- **Less is more.** 5-10k tokens of archive beat 25k, 50k and even the whole archive (257k). Past about 10k, adding more writing made drafts more average, not more like the person. The default selection covers different recipients, lengths and years, and trims the replied-to text short so more of your own messages fit.
- **One call.** The critique-and-revise step in 0.1 dropped 4-9% of the points a draft was supposed to make and trimmed down long drafts. Now drafts keep their content.
- **No deliberate imperfection.** Real messages have typos and doubled words. The plugin never adds them in.

**Optional extras, off by default** since neither helped in testing: a **person file** (`wlm person build`, a cited description of what you know, do and think, with `show` / `add` / `forget` to read and prune it; turn it on per draft with `--person core`), and a **habits note** (`--habits on`, your measured frequencies, like typical length and how often you skip the final period).

## Results

These are from one author’s own held-out messages, using Claude Opus 5.5. Aggregates only, and none of the message text is in this repository. Methods and full tables are in [docs/research.md](docs/research.md).

| | style match, Reddit comments (0 = other people, 1 = the author) | judge spots the draft in a lineup (chance 25%) |
|---|---|---|
| default Claude | -0.49 | n/a |
| 0.1 (examples + rules + critic) | 0.33 | Reddit 50%, Teams 38% |
| **0.2 (archive, 10k tokens)** | **0.90** | **Reddit 40%, Teams 32%** |

- **Long-form writing is where 0.2 is a clear step up.** On short work chat it’s even with 0.1 within noise, and 0.1’s rules still win on small surface habits like leaving off the final period.
- **Paired judges still catch long text by how polished it is.** When they’re shown the real comment next to the draft, they pick the real one almost every time, mostly pointing to typos and roughness that the plugin never adds. The lineup test is closer to how a reader actually comes across a message, so that’s what `voice-check` reports.

One author and a few registers is a pretty small sample, so your mileage may vary. Use `/write-like-me:voice-check` to measure your own.

## Commands reference

All the skills run through a single CLI, which you can also run yourself:

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
