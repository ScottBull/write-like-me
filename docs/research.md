# Research behind write-like-me

This page summarises the literature survey and the experiments that shaped the plugin: the first version (examples, rules and a critic), what it topped out on, the archive pilot that replaced it, and the ablation that set the defaults. It describes methods and aggregate results only; no message text, briefs or judge reasons appear here.

## What the literature says (survey, late 2026)

1. **Exemplars beat descriptions.** Real samples of a person's writing do far more than a description of their style. In Jemama & Kumar's comparison, zero-shot prompting with style statistics did far worse than one-shot or few-shot prompting, with up to 23.5x higher style-match accuracy for few-shot ([Jemama & Kumar 2025](https://arxiv.org/abs/2509.24930)).
2. **Few-shot prompting has a ceiling, and it is lowest for informal writing.** With 5 random exemplars, frontier models come close on news and email but not on blogs or forums. Going from 2 to 10 exemplars adds little ([Wang et al., EMNLP Findings 2025](https://arxiv.org/abs/2509.14543)). In a calibrated authorship-embedding (LUAR) evaluation, every inference-time method tested produced text *less* similar to the author than random other humans are ([Sawant 2026](https://arxiv.org/abs/2604.26460)). We read this as "examples don't scale" and built the first version around a dozen of them. That was the wrong reading: it is a statement about few-shot. Hundreds to thousands of examples are a different regime, and it is one known to be able to override a model's trained-in defaults (the pilot below).
3. **Retrieve by register and recipient, not by topic.** Personal retrieval helps a lot overall ([LaMP](https://arxiv.org/abs/2304.11406), [LongLaMP](https://arxiv.org/abs/2407.11016); RAG beat per-user fine-tuning for users with little data in [Salemi & Zamani 2024](https://arxiv.org/abs/2409.09510)). But choosing *content-similar* exemplars cut authorship attribution sharply, because the model copies content and drops style ([Wang et al. 2025](https://arxiv.org/abs/2509.14543)). The ablation tested a request-matched selection strategy against a coverage one for exactly this reason.
4. **The main failure is regression to the default AI voice.** Fine-tuning on an author's work flipped expert preferences mostly by removing model tics such as cliché density ([Chakrabarty, Ginsburg & Dhillon 2025](https://arxiv.org/abs/2510.13939)). Over-represented "slop" patterns can be measured and suppressed ([Antislop, Paech et al. 2025](https://arxiv.org/abs/2510.15061)). Practitioner catalogues of AI tells: [Wikipedia: Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing), [Pangram](https://www.pangram.com/signs-of-ai-writing). Avoid lists work better as a post-draft lint than as instructions alone, and must exclude anything the person actually uses.
5. **Beware circular judges.** An LLM judge that grades against an LLM-extracted style profile rewards performing the listed traits, which is caricature. In one study it correlated with LUAR at r = 0.013 ([Sawant 2026](https://arxiv.org/abs/2604.26460)). Use forced choice ("which one is real?") with real references, both orders, ideally from a different model family. LLMs are reasonable zero-shot authorship verifiers when pointed at linguistic features ([Huang, Chen & Shu 2024](https://arxiv.org/abs/2403.08213)). The same trap applies to the person file: a profile judged against itself always wins, so it is an input only, never a rubric.
6. **Learn from edits.** Inferring preferences from the user's edits to drafts gave the lowest edit cost in CIPHER ([Gao et al., NeurIPS 2024](https://arxiv.org/abs/2404.15269)). Editing an AI draft alone still leaves text closer to the LLM's style than to the person's, even though people feel it represents them ([Baumler et al., ACL 2026](https://arxiv.org/abs/2604.24444)). So drafts should start as close to the person as possible, and edits should feed back, here into the archive and the person file rather than a rules list.
7. **What identifies a writer.** The signals people don't consciously control matter most:
   - function words and character n-grams
   - punctuation, capitalisation and contraction habits
   - greetings and sign-offs, length, and structure (prose vs. bullets)
   - sentence-length variance (the "burstiness" that LLM text lacks)

   Sources: [Stamatatos 2009](https://doi.org/10.1002/asi.21001), [Abbasi & Chen 2008](https://doi.org/10.1145/1344411.1344413).
8. **Fine-tuning is the only method shown to clearly beat the ceiling** ([Chakrabarty et al. 2025](https://arxiv.org/abs/2510.13939); [OPPU](https://arxiv.org/abs/2402.04401)). It needs far more text than most people have in one register, and it isn't available for hosted Claude models. Activation steering ([StyleVector](https://arxiv.org/abs/2503.05213)) needs open weights. This plugin therefore stays at inference time.

Evaluation tools referenced: LUAR ([Rivera-Soto et al. 2021](https://arxiv.org/abs/2105.07263)), content-independent style embeddings ([Wegmann et al. 2022](https://aclanthology.org/2022.repl4nlp-1.26)), StyleDistance ([Patel et al.](https://arxiv.org/abs/2410.12757)), and STEL ([Wegmann & Nguyen 2021](https://aclanthology.org/2021.emnlp-main.569)).

## Four layers of "sounds like me"

The experiments are easier to read against this ladder, from the easiest for a model to get right to the hardest.

1. **Mechanics.** Punctuation, capitalisation, greetings and sign-offs, spelling choices, typical length. Countable, so rules can state them. Every version of the plugin scored 0.7-0.9 (on a 0-1 scale) on stylometric closeness here.
2. **The texture of the prose.** Which small words go where, how clauses chain, how long a thought runs, what is left unsaid, how a point is softened or pushed. Readers feel it and cannot name it; the style-embedding models measure it. It cannot be written as rules because it is not a list of features but a distribution over everything, and a dozen examples against a model trained on trillions of tokens is a hint, not a prior.
3. **Judgment and content.** What the person would include and leave out, what they know, what they would push back on, what they find funny. The content-only briefs in the eval hide this layer; in real use with thin notes it is the loudest tell. The person file exists for it.
4. **Imperfection.** Typos, doubled words, run-ons, phone-typed apostrophes. The plugin never adds these by design, and it is where most of a paired judge's catches live.

## The first version (runs 1-3): examples, rules, a critic

- **Data:** one author's own 1:1 work-chat messages (median 20 words), later also his public forum comments (median 54 words) as a `post` register. Each held-out message was paired with the message it replied to. 20 messages per register were held out for testing and 10 for tuning. No pool message from the same conversation within 48 hours of a held-out message was used anywhere, and near-duplicates (a shared 6-word run) were removed.
- **Briefs:** each held-out message was turned into a content-only brief: terse first-person notes with no 3-word runs copied from the message, no tone or confidence cues, and no greeting or name cues. Briefs were capped at the message's length and lowercased.
- **Strategies (run 1, chat):** A default Claude; B a style description extracted from 60 messages; C 6 fixed exemplars; D C plus hard rules and an AI-ism avoid list; E D plus 3 retrieved exemplars, same conversation first; F E's draft, then a critic that sees 5 more real messages and lists at most 4 tells, then one revision; G 6 exemplars from the author's AI-prompt history only; H 3 chat plus 3 prompt-history exemplars. Each ran once per brief on Claude Opus 5.5 through isolated `claude -p` calls (no tools, memory or project context).
- **Metrics:** (1) forced-choice judges, an OpenAI GPT model and Claude, each seeing 8 reference messages, the context, and the real and generated message in both orders, reported as the fooling rate with a Wilson 95% CI (50% = indistinguishable; the judges were fooled 32% by other people's real messages and 0-2% by default Claude); (2) stylometric closeness, mean |z| over ~20 habits against same-size samples of the author's pool, normalised between other people (0) and the author's own held-out messages (1); (3) calibrated style embeddings (LUAR, StyleDistance, Wegmann), reported as bundle scores on the same 0-1 normalisation, and LUAR paired detectability (0.5 = the real and generated message are indistinguishable to the embedding).

| run 1, chat | judge 1 fooled | judge 2 fooled | stylometric closeness | median words (real 20-24) |
|---|---|---|---|---|
| A default Claude | 8% | 2% | 0.29 | 29.5 |
| B style description | 8% | 8% | 0.73 | 27.5 |
| C 6 fixed examples | 10% | 5% | 0.69 | 28 |
| D + rules + avoid list | 8% | 8% | 0.78 | 25.5 |
| E + retrieved examples | 12% | 5% | 0.84 | 25.5 |
| **F + critique and revise** | **18%** | **12%** | **0.92** | **20** |
| G prompt-history examples only | 0% | 0% | 0.75 | 33.5 |
| H half chat, half prompt | 8% | 5% | 0.87 | 27.5 |

n = 40 judge trials per strategy, CIs of roughly ±12 points. F shipped as 0.1.0. Run 2 (the shipped plugin, same items) scored judge 1 fooled 38% [24-53], judge 2 18% [9-32], closeness 0.96; later samples of near-identical prompts put the LUAR bundle at 0.68-0.72 against run 2's single 0.93, so run 2's embedding headline was a high draw.

**What run 1 found, and what the first version did about it:** caricature (strategies with rules opened with an acknowledgment word in 70% of messages against about 26% real; fixed by phrasing tendencies as frequencies and linting habit rates across drafts), too polished (fixed by measuring the person's imperfection level and stating it as permitted, never adding errors), too long (length targets from the real distribution), and prompt history isn't conversation (used only as a supplement).

**Run 3 (forum comments) showed the ceiling.** On 20 held-out comments, the shipped pipeline with a `post` register beat its chat form and default Claude on every embedding metric, but only reached the level of *other* forum writers (LUAR bundle 0.26-0.39, StyleDistance and Wegmann near 0), and both judges caught nearly every draft. The critique-and-revise step lost 4-9% of the briefs' points and cut long drafts to about 60% of real length; drafting alone lost about 0%. On short chat the first version did respectably because mechanics are most of the job there; on anything longer it barely beat default Claude. A lint can only fix what it can count, and layer 2 is uncountable by construction.

## The archive pilot

The simplest alternative: put the person's writing archive in the model's context, frame the job as "write the next entry in this archive", and make one call. No rules, no hand-picked examples, no critic, no revision.

- **Setup:** one `claude -p` call per item, Claude Opus 5.5, the archive as the system prompt via `--system-prompt-file` (so the CLI's prompt cache covers it), the task on stdin. Two arms. *Forum:* all 1,425 of the author's 2021+ comments outside the held-out threads, in posting order, each with a shortened version of what it replied to (about 257k tokens at the measured 2.77 characters per token). *Chat:* the forum archive plus every chat message in the pool not within 48 hours of a test item in the same conversation (272 of 457), numbered anonymously by conversation. Same items, briefs, model and eval run as runs 2-3.

| forum comments (median 54 words) | LUAR bundle | StyleDistance | Wegmann | LUAR detect (own withheld: 0.75) | length vs real | fooled GPT / Claude |
|---|---|---|---|---|---|---|
| default Claude | -0.49 | -1.29 | -0.42 | 1.00 | 1.24x | 0% / 0% |
| 0.1 pipeline (run 3), 2 samples | 0.26 / 0.39 | -0.09 / 0.13 | 0.00 / 0.02 | 0.95 | 0.89x | 2% / 0% |
| **archive in context**, 2 samples | **0.65 / 0.61** | **0.41 / 0.23** | **0.50 / 0.33** | **0.80** | 1.05x | 5% / 0% |

Paragraph count matched the author's (1.8 per comment vs 1.7 real; the pipeline gave 1.3). Sample-to-sample noise is 0.1-0.2 on these scales, so the 0.3-0.4 gap to the pipeline, on every model and both samples, is real. On LUAR the archive drafts were nearly as hard to tell from the real comments as the author's own other comments are.

| work chat (median 20 words) | LUAR / SD / Wegmann bundle | LUAR detect (own: 0.35-0.50) | hedges per 1k words (real: 30) | fooled GPT |
|---|---|---|---|---|
| 0.1 pipeline (run 2) | 0.93 / 0.73 / 0.93 | 0.60 | 22 | 28-38% across four samples |
| best run-1 condition (F) | 0.99 / 1.02 / 1.10 | 0.70 | 14 | 18% |
| **archive in context** | 0.90 / 1.00 / 1.09 | 0.65 | 12.6 | 18% [9-32] |

A tie on embeddings, not a win on the judge, and it under-hedged: the forum archive (13 hedges per 1k words) pulled the chat voice away from the chat rate. On short chat the mechanics dominate and the rules already won them; on longer writing the archive roughly doubled the score. Cost: at the pilot's size, one cached call per message returned in about 11 seconds; the cache write is paid once per hour of use.

## Measurement: the paired judge was grading the wrong thing

On the forum comments, the judges' stated reasons for picking the real message were a typo, a doubled word, an apostrophe style, lowercase, or "unpolished / awkward phrasing" in 39 of 76 GPT catches and 65 of 80 Claude catches. The rest were run-ons, repetition, and specific detail the brief had compressed away. With the real message beside it, the judge asks "which one is less polished?", and the answer is always the human. That is a humanness test, which a tool that refuses to fake typos cannot pass, and a harder bar than deployment: nobody who receives a message has the real one next to it.

Three changes, all in the plugin's `voice-check`:

1. **A lineup trial.** One generated message among three of the person's real messages from the same register, each within 0.75-1.33x the length of the item's real message, from three different conversations, never the item's own message or thread. The judge (and the person) picks the one not written by them. Chance is 25%; the spotted rate with a Wilson CI is the number.
2. **Reasons tagged polish or voice.** Every reason a judge gives for a catch (and every note the person types on the page) is matched against a list of polish terms (typo, misspelling, doubled word, apostrophe, capitals, grammar, "polished", "awkward"...). The voice-type catch rate is reported separately: it is the only part the pack can do anything about.
3. **The person's own blind test** as the actual goal, pairs and lineups on the same page.

The embeddings stay; LUAR detectability is the most honest automatic number available.

## The budget ablation

The pilot's 257k tokens is more than most people have and more than a plan comfortably spends per message, so the question was how much archive a draft needs and how to choose it. Six selection strategies, each a pure function of the archive, a token budget and the request, deterministic under a seed, were run at budgets from 5k to 50k tokens on the forum and chat test sets, two samples each, scored in the same eval run as the pilot and run 3:

| strategy | what it picks |
|---|---|
| R | random, then date order (the no-skill baseline) |
| D | stratified over where-it-went x length tercile x year, newest first within each stratum (coverage of situations) |
| M | TF-IDF match of the request against each entry's context and text (the "similar situations" idea the literature warns copies content); falls back to D |
| T | the newest entries that fit (recency) |
| C | D over compact entries (the replied-to excerpt cut to 12 words: about 1.6x more entries per token) |
| P | the person file's core tier, then D for the budget it leaves |

The deciding metric was LUAR paired detectability with the LUAR bundle second, the two content-independent models breaking ties, judges on the frontier only with reasons tagged; the recommended budget is the smallest whose best strategy is within 0.05 detect and 0.10 bundle of the 257k point. Resolution at n = 20 with two samples is about 0.10 on bundle scores, so neighbouring cells are rarely distinguishable.

Reddit (20 held-out comments, two samples, means; detect 0.5 = indistinguishable; bundles 0 = other people, 1 = the author's own held-out comments):

| budget | best strategy | LUAR detect | LUAR / SD / Wegmann bundle |
|---|---|---|---|
| 5k | C | 0.78 | 0.91 / 0.93 / 0.91 |
| 10k | C | 0.78 | 0.87 / 0.96 / 0.93 |
| 25k | C | 0.85 | 0.83 / 0.88 / 0.86 |
| 50k | T | 0.85 | 0.73 / 0.66 / 0.72 |
| 257k (pilot, whole archive) | - | 0.80 | 0.63 / 0.32 / 0.42 |

- **The knee is at 5-10k tokens**, and the curve falls after it: more archive made drafts more average. Random selection was worst at every budget; stratified (D), recency (T) and compact (C) led, with C best because cutting the replied-to text fits about 1.6x more of the person's own messages.
- **M (retrieval-matched) did not copy content** (1-2 of 40 drafts shared an 8-word run with the archive, the same as D and T) but did not help either, and it can't share a cached prompt, so it costs more.
- **P (person file + D) lost to D at every budget.**
- **Teams:** the whole Teams pool (~30k tokens) was easier to tell from the real messages than C at 10k (detect 0.85 vs 0.80); C at 5k was too little (bundle 0.76).
- **Defaults:** `strategy_default` C, `budget_default` 10000, person file off, one global default (5k is as good for posts; 10k is safer for chat).
- **Final head-to-head (the 0.2 code, habits block off):** Teams lineup spotted 32% [20-48] vs the 0.1 pipeline's 38% [24-53]; Reddit lineup 40% [26-55] vs 50% [35-65], with LUAR bundle 0.90 vs 0.33. A measured-habits block (typical length, final punctuation, openers as frequencies) made both worse (Teams lineup 45%, Reddit 59%) and is off by default.

## The person file

The archive carries layers 1 and 2 by example and layer 3 only by implication. The person file is a distilled description of the person for layer 3: what they know and do, what they think, how they argue, what they joke about, how they treat people, what they never say. It never describes style; the archive does that, and the prompt says so.

- **Two files, two owners.** `person.md` from posts and comments and personal chat (documents, pasted samples and other unsorted chat only when the person says they are personal); `person.work.md` from the work sources (Teams, Outlook, Claude Code prompts). Each is built only on request, with a dry run showing the number of model calls first.
- **Distillation** (`wlm person build`, `claude -p` on the person's own login): *map*, one call per ~40k-token window of the archive in date order, extracting claims as JSON (`fact | stance | knowledge | habit-of-argument | humour | relationship | reference`, with confidence and the entry ids behind each) and forbidding anything about style or about a named third party beyond "a coworker"; *reduce*, a deterministic merge by normalised text, then one call per kind merging near-duplicates and noting change over time, keeping the union of ids; *the citation rule*, two distinct entry ids or the claim is dropped, with one exception for a plainly stated biographical fact (kept, marked single-source); *write*, a draft and up to two tightening passes per tier (a core of about 3k tokens, a full tier of about 10k), every sentence ending in a claim marker; *check*, deterministic: a sentence without a marker naming a surviving claim is dropped, not fixed, and the markers go to a sidecar (`person.citations.json`) so the prompt copy carries none. On the author's archives this produced about 300 personal and 700 work claims after the citation rule dropped 7-13% of them, most of those stances with a single source.
- **The person prunes it.** `wlm person show` prints a tier exactly as the drafter sees it; `wlm person forget` deletes a claim and every sentence resting on it and blacklists it so a rebuild cannot bring it back; `wlm person add` records a claim the person states, cited as user-stated. A hand-editable `terms` list in the blacklist drops any claim containing a word or phrase.
- **How it is measured.** Two ways, and never as a judge rubric. First, the ablation's P cells: does the core plus a smaller archive beat the same budget of archive alone? Second, a thin-notes track: held-out items with their briefs cut to at most eight words, generated with and without the person file; a checker lists each claim the draft makes that the notes did not contain and whether the archive supports it, and the person rates the same list ("would I have said this?"). The person file wins if what it adds is both more frequent and more often supported, and loses if it adds unsupported claims at all.

P cells (person-file core of about 3k tokens + D for the rest): detect / LUAR bundle 0.95 / 0.40 at 5k, 0.90 / 0.62 at 10k, 0.82 / 0.49 at 25k, below D and C at every budget. The tokens the file takes buy less than the same tokens of real messages. `person_default` is therefore `none`. The thin-notes track was not run for 0.2.0, because the P cells already decided the default; it is the measurement to run before turning the person file on for drafts whose notes are thin.

## Restyle: rewriting Claude's answer in the person's voice

A common request is to paste an answer Claude already wrote and ask for it "in my voice". This was tested on 40 held-out items (20 Teams replies, register chat; 20 Reddit comments, register post): one fresh assistant answer per item (the message replied to plus the brief, "write the reply for them as a helpful assistant would"), the plugin's 0.2 defaults, two samples per condition, the codex judge on sample 1. The conditions: R-DIRECT ("Rewrite this in my own words, the way I would write it myself. Keep the useful content." and the answer), R-SHORT (a line saying where the reply goes, then "Below is an AI-written answer with more detail than I would send. Reply the way I actually would: as long as I would really write it, keeping only what I would bother to mention. Don't add anything that isn't in it." and the answer), R-POINTS (R-SHORT's wording over terse points taken from the answer instead of the answer itself), R-FRESH (where it goes, no answer and no brief), NEWNH (a normal 0.2 draft from the person's own brief) and ANSWER (the assistant answer as is). The natural answers were short and plain (Teams median 48.5 words, 2.1x the real message, no markdown; Reddit median 89, 1.7x), which limits how much a scope instruction can cut.

Teams (chat), 20 items:

| cond | lineup spotted (codex, chance 25%) | paired fooled | LUAR detect | LUAR bundle | stylo norm | length ratio | median words | brief points kept | msgs keeping all | 4-gram share of answer | 8-word runs / msg |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R-DIRECT | 80% [65-90] | 0% | 1.00 | 0.87 | 0.66 | 1.67 | 38.5 | 94% | 90% | 35% | 0.95 |
| R-SHORT | **60% [45-74]** | 2% | 0.93 | 0.96 | 0.73 | 1.35 | 29.0 | 89% (s1 91%, s2 86%) | 75% | 32% | 0.65 |
| R-POINTS | 82% [68-91] | 2% | 0.97 | 0.99 | 0.68 | 1.67 | 36.5 | 91% | 80% | 18% | 0.65 |
| R-FRESH | 82% [68-91] | 2% | 0.77 | 1.06 | 0.69 | 0.55 | 14.5 | 11% (guess rate) | 2% | n/a | n/a |
| NEWNH (from brief) | 42% [29-58] | 15% | 0.68 | 0.87 | 0.89 | 1.13 | 26.0 | 100% | 100% | 24% | 0.55 |
| ANSWER | | | 1.00 | 0.06 | -0.55 | 2.28 | 48.5 | 100% | 100% | 100% | 1.00 |
| (real) | | | | | | 1.00 | 23.5 | | | | |

Reddit (post), 20 items:

| cond | lineup spotted (codex, chance 25%) | paired fooled | LUAR detect | LUAR bundle | stylo norm | length ratio | median words | brief points kept | msgs keeping all | 4-gram share of answer | 8-word runs / msg |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R-DIRECT | **75% [60-86]** | 2% | 0.85 | 0.83 | -0.08 | 1.36 | 89.5 | 99% | 92% | 39% | 3.15 |
| R-SHORT | 78% [62-88] | 0% | 0.90 | 0.69 | 0.38 | 1.20 | 73.5 | 96% | 80% | 41% | 2.58 |
| R-POINTS | 85% [71-93] | 0% | 0.88 | 0.63 | 0.19 | 1.26 | 79.0 | 97% | 80% | 23% | 1.55 |
| R-FRESH | 95% [83-99] | 0% | 0.95 | 0.56 | 0.90 | 0.91 | 50.0 | 10% (guess rate) | 0% | n/a | n/a |
| NEWNH (from brief) | 38% [24-53] | 0% | 0.78 | 0.90 | 0.73 | 1.05 | 55.0 | 100% | 100% | 19% | 0.88 |
| ANSWER | | | 1.00 | -0.44 | -0.43 | 1.39 | 89.0 | 99% | 95% | 100% | 1.00 |
| (real) | | | | | | 1.00 | 52.0 | | | | |

Lineup and paired n = 40; LUAR detect 0.5 = indistinguishable; brief points kept is points-weighted (Teams 132 points, Reddit 274); the 4-gram share and 8-word runs are measured against the answer, so NEWNH's row is the floor the shared brief produces.

- **Every restyle is spotted about twice as often as a draft from the brief:** 60-82% (Teams) and 75-85% (Reddit) in the lineup, against 42% and 38%.
- **It isn't the wording.** R-POINTS cut the 4-gram share to near NEWNH's floor and was spotted the most. What gives a restyle away is probably Claude's choice of content and how much of it there is.
- **The scope wording helps on chat.** R-SHORT was 20 points better than R-DIRECT on the Teams lineup, the only clear gain, with the shortest output (1.35x), and kept 89% of the brief's points there (96% on Reddit, where it was level with R-DIRECT). Pooled over both domains it was spotted least (69% vs R-DIRECT's 78%) and kept 93% of the points. Every other gap sits inside overlapping intervals. The pre-set rule (lowest lineup rate among arms keeping at least 90% of the points, per domain) picked R-DIRECT, because R-SHORT missed the Teams floor by about a point; the plugin uses R-SHORT for its Teams gain, its shorter output and its pooled result.
- **R-FRESH does worst** (Teams 82%, Reddit 95%) and guesses only 10-11% of the points: knowing what the person meant to say helps the style as well as the content.

**In the plugin:** `wlm context --restyle-file F` builds the notes from pasted text with R-SHORT's wording by default (`--keep mine`; the opening line names the register, the recipient and, when the archive comes from one platform, the platform), R-DIRECT's with `--keep all`, and `--point TEXT` puts "The point I want to make: TEXT" before the pasted text. Since no restyle came near a draft from the person's own brief, the command always prints a tip saying so, and the skill passes it on.

## How the plugin maps to this

| finding | plugin feature |
|---|---|
| many examples beat few examples plus rules | every draft sees a budgeted selection of the person's own archive, framed as "write the next entry"; no style rules in the prompt, and by default no "How my messages vary" note either (`--habits on` adds one, of measured rates after the archive; in the final held-out test it made Teams drafts easier to spot, 45% vs 32% in the lineup, and lowered all three Reddit bundle scores, so it is off) |
| content-matched retrieval copies content | the default selection covers situations (recipient, length, year); request matching is an opt-in strategy |
| caricature from frequency rules | tendencies live in `profile.md` for reading only; lint keeps a caricature counter across recent drafts |
| critique-and-revise lost content and length | one draft call; lint fixes only hard rules, with minimal edits |
| layer 3 is the loudest tell with thin notes | the optional, cited, prunable person file, an input never a judge |
| the paired judge rewards typos | `voice-check` reports lineup spotted rates and tags reasons polish or voice |
| learn from edits | `voice-learn` proposes adding the sent message to the archive, a person-file claim, a note or a hard rule |
| a 250k-token call per message is too much | the budget ablation; defaults in `stats.json`, overridable per draft |
| restyling Claude's answer is spotted far more often than drafting from the person's own notes; content and amount give it away, not wording | `--restyle-file` uses the shortest measured wording (`--keep mine`) and always suggests adding the person's own point (`--point`) |
| privacy | everything local; drafts run in a subagent or a `claude -p` child with the archive as a system-prompt file and no API key in its environment |
