# Research behind write-like-me

This page summarises the literature survey and the first experiment that shaped the plugin. It describes methods and aggregate results only.

## What the literature says (survey, late 2026)

1. **Exemplars beat descriptions.** Real samples of a person's writing do far more than a description of their style. In Jemama & Kumar's comparison, zero-shot prompting with style statistics did far worse than one-shot or few-shot prompting, with up to 23.5x higher style-match accuracy for few-shot ([Jemama & Kumar 2025](https://arxiv.org/abs/2509.24930)).
2. **Prompting alone has a ceiling, and it is lowest for informal writing.** With 5 random exemplars, frontier models come close on news and email but not on blogs or forums. Going from 2 to 10 exemplars adds little ([Wang et al., EMNLP Findings 2025](https://arxiv.org/abs/2509.14543)). In a calibrated authorship-embedding (LUAR) evaluation, every inference-time method tested produced text *less* similar to the author than random other humans are ([Sawant 2026](https://arxiv.org/abs/2604.26460)).
3. **Retrieve by register and recipient, not by topic.** Personal retrieval helps a lot overall ([LaMP](https://arxiv.org/abs/2304.11406), [LongLaMP](https://arxiv.org/abs/2407.11016); RAG beat per-user fine-tuning for users with little data in [Salemi & Zamani 2024](https://arxiv.org/abs/2409.09510)). But choosing *content-similar* exemplars cut authorship attribution sharply, because the model copies content and drops style ([Wang et al. 2025](https://arxiv.org/abs/2509.14543)). Hence: a fixed core set, then same-recipient retrieval.
4. **The main failure is regression to the default AI voice.** Fine-tuning on an author's work flipped expert preferences mostly by removing model tics such as cliché density ([Chakrabarty, Ginsburg & Dhillon 2025](https://arxiv.org/abs/2510.13939)). Over-represented "slop" patterns can be measured and suppressed ([Antislop, Paech et al. 2025](https://arxiv.org/abs/2510.15061)). Practitioner catalogues of AI tells: [Wikipedia: Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing), [Pangram](https://www.pangram.com/signs-of-ai-writing). Avoid lists work better as a post-draft lint than as instructions alone, and must exclude anything the person actually uses.
5. **Beware circular judges.** An LLM judge that grades against an LLM-extracted style profile rewards performing the listed traits, which is caricature. In one study it correlated with LUAR at r = 0.013 ([Sawant 2026](https://arxiv.org/abs/2604.26460)). Use forced choice ("which one is real?") with real references, both orders, ideally from a different model family. LLMs are reasonable zero-shot authorship verifiers when pointed at linguistic features ([Huang, Chen & Shu 2024](https://arxiv.org/abs/2403.08213)).
6. **Learn from edits.** Inferring preferences from the user's edits to drafts gave the lowest edit cost in CIPHER ([Gao et al., NeurIPS 2024](https://arxiv.org/abs/2404.15269)). Editing an AI draft alone still leaves text closer to the LLM's style than to the person's, even though people feel it represents them ([Baumler et al., ACL 2026](https://arxiv.org/abs/2604.24444)). So drafts should start as close to the person as possible, and edits should feed back into the rules.
7. **What identifies a writer.** The signals people don't consciously control matter most:
   - function words and character n-grams
   - punctuation, capitalisation and contraction habits
   - greetings and sign-offs, length, and structure (prose vs. bullets)
   - sentence-length variance (the "burstiness" that LLM text lacks)

   Sources: [Stamatatos 2009](https://doi.org/10.1002/asi.21001), [Abbasi & Chen 2008](https://doi.org/10.1145/1344411.1344413).
8. **Fine-tuning is the only method shown to clearly beat the ceiling** ([Chakrabarty et al. 2025](https://arxiv.org/abs/2510.13939); [OPPU](https://arxiv.org/abs/2402.04401)). It needs far more text than most people have in one register, and it isn't available for hosted Claude models. Activation steering ([StyleVector](https://arxiv.org/abs/2503.05213)) needs open weights. This plugin therefore stays at inference time.

Evaluation tools referenced: LUAR ([Rivera-Soto et al. 2021](https://arxiv.org/abs/2105.07263)), content-independent style embeddings ([Wegmann et al. 2022](https://aclanthology.org/2022.repl4nlp-1.26)), StyleDistance ([Patel et al.](https://arxiv.org/abs/2410.12757)), and STEL ([Wegmann & Nguyen 2021](https://aclanthology.org/2021.emnlp-main.569)).

## Run 1: method

- **Data:** one author's own 1:1 work-chat messages. Each held-out message was paired with the message it replied to. 20 messages were held out for testing and 10 for prompt tuning. The rest formed the exemplar pool. No pool message from the same conversation within 48 hours of a held-out message was used anywhere, and near-duplicates (a shared 6-word run) were removed.
- **Briefs:** each held-out message was turned into a content-only brief: terse first-person notes with no 3-word runs copied from the message, no tone or confidence cues, and no greeting or name cues. Briefs were capped at the message's length and lowercased.
- **Strategies:** each was run once per brief on Claude Opus 5.5 through isolated `claude -p` calls (no tools, memory or project context).
  - **A:** default Claude, with no help.
  - **B:** a style description extracted from 60 messages, in the manner of a "custom style" feature.
  - **C:** 6 fixed exemplars, hand-picked for diversity.
  - **D:** C plus hard rules and an AI-ism avoid list.
  - **E:** D plus 3 retrieved exemplars, same conversation first, then nearest by TF-IDF.
  - **F:** E's draft, then a critic that sees 5 more real messages and lists at most 4 tells, then one revision.
  - **G:** 6 exemplars from the author's AI-prompt history only.
  - **H:** 3 chat exemplars plus 3 prompt-history exemplars.
- **Critic tuning (F):** the first critic prompt made claims from too little evidence ("they never say X" based on 5 samples). It also pushed for added reasons and next steps, so revisions got longer. The tuned version judges style only, never suggests content, makes no "never uses" claims from small samples, and lists at most 4 tells. With it, revisions got shorter and added nothing.
- **Metrics:**
  1. Forced-choice judges: an OpenAI GPT model and Claude. Each saw 8 reference messages, the context, and the real and generated message in both orders. The score is the fooling rate with a Wilson 95% CI. Judges were calibrated on other people's messages (fooled 32%) and default Claude (fooled 0-2%).
  2. Stylometric deltas: about 20 habits, each z-scored against same-size samples of the author's pool. The score is mean |z|, normalised between other people's messages (0) and the author's own held-out messages (1).
  3. AI-ism rate.
  4. Calibrated style embeddings (LUAR, StyleDistance, Wegmann). These were weak on single short messages and are omitted from the plugin.

## Run 1: results (aggregate)

| strategy | judge 1 fooled | judge 2 fooled | stylometric closeness | median words (real ≈ 20-24) |
|---|---|---|---|---|
| A | 8% | 2% | 0.29 | 29.5 |
| B | 8% | 8% | 0.73 | 27.5 |
| C | 10% | 5% | 0.69 | 28 |
| D | 8% | 8% | 0.78 | 25.5 |
| E | 12% | 5% | 0.84 | 25.5 |
| **F** | **18%** | **12%** | **0.92** | **20** |
| G | 0% | 0% | 0.75 | 33.5 |
| H | 8% | 5% | 0.87 | 27.5 |

n = 40 judge trials per strategy, with CIs of roughly ±12 points. The differences between neighbouring rows are not individually significant. The ordering was consistent across both judges and the stylometric metric.

**Failure modes found by the judges and the habit table:**

1. **Caricature.**
   - Strategies with rules or a style description opened with an acknowledgment word in 70% of messages, against about 26% in the author's real messages.
   - The habits themselves were correct, but stating them as commands made the model apply them to every message.
   - The fix is to express tendencies as frequencies, keep the fixed exemplar set at real habit frequencies, and lint habit rates across recent drafts.
2. **Too polished.**
   - Judges caught fakes for being tidier than the person.
   - Real messages had fragments, dropped words, occasional missing apostrophes, lowercase proper nouns and unexplained context.
   - The fix is to measure the person's imperfection level and state it as permitted, never adding errors on purpose.
3. **Too long.** Every strategy except F ran longer than the real median. The fix is length targets from the person's own distribution, plus lint.
4. **Prompt history isn't conversation.** AI-prompt history transferred mechanics such as punctuation, capitalisation and hedging, but not conversational vocabulary. It is used only as a supplement.

## How the plugin maps to this

| finding | plugin feature |
|---|---|
| exemplars > descriptions | every draft gets 6 fixed + 3 retrieved real messages; `profile.md` is for humans |
| register/recipient retrieval | bank tagged by register and recipient; same recipient first |
| caricature | frequency-phrased tendencies; fixed exemplars keep real habit rates; lint tracks habit rates across drafts |
| too polished | measured imperfection level in `rules.md`; lint flags "tidier than usual" as a hint, never adds typos |
| too long | median / IQR / p90 length targets; lint flags drafts longer than ~90% of real ones |
| AI voice | avoid list minus what you use; lint |
| critique-and-revise | `voice-critic` subagent with the tuned prompt; one pass |
| learn from edits | `voice-learn` diffs draft vs. sent and proposes notes or rules |
| measure it | `voice-check`: blind-test page, forced-choice judge, stylometric closeness |
