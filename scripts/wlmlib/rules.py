"""Turn measured stats into rules.md (frequency-calibrated) and profile.md (human summary).

Design rules from run 1 of the research (see docs/research.md):
- Only habits that are truly absent (or near-universal) in a decent sample become never/always rules.
- Everything else is written as a frequency ("about 1 in 4 messages"), because command-style rules
  made models overuse signature habits (caricature).
- The person's measured level of imperfection is stated as *permitted*, so drafts aren't tidier
  than the real person.
- Length targets come from the person's real distribution (median / IQR / p90).
"""
import random
import re

from .stylo import AIISMS, message_markers

MIN_N_NEVER = 25      # messages needed before an absence becomes a "never" rule
MIN_N_NEVER_SOLO = 40  # below this, a never/always rule must also hold in a larger register
MIN_N_OK = 20         # below this a register is marked low-confidence
KEEP_MARK = "<!-- KEEP BELOW -->"
LINT_RE = re.compile(r"<!--\s*lint:([a-z_]+)(?:=(.*?))?\s*-->")


def freq_phrase(p):
    """0.26 -> 'about 1 in 4'; 0.7 -> 'most (about 7 in 10)'."""
    if p is None:
        return "unknown"
    if p <= 0:
        return "never"
    if p >= 0.95:
        return "almost always"
    if p >= 0.6:
        return "most (about %d in 10)" % round(p * 10)
    if p >= 0.4:
        return "about half"
    n = max(2, int(round(1.0 / p)))
    if p < 0.05:
        return "rarely (about 1 in %d)" % n
    return "about 1 in %d" % n


def freq_of(p, unit="messages"):
    """0.26 -> 'about 1 in 4 messages'; 0.97 -> 'almost all messages'."""
    if p is None:
        return "unknown"
    if p <= 0:
        return "no %s" % unit
    if p >= 0.95:
        return "almost all %s" % unit
    if p >= 0.6:
        return "most %s (about %d in 10)" % (unit, round(p * 10))
    if p >= 0.4:
        return "about half of %s" % unit
    n = max(2, int(round(1.0 / p)))
    if p < 0.05:
        return "rarely (about 1 in %d %s)" % (n, unit)
    return "about 1 in %d %s" % (n, unit)


def _fmt_words(x):
    return str(int(round(x)))


def _variant_prefs(variants):
    groups = [("yeah", "yea", "ya"), ("yep", "yup"), ("ok", "okay", "k"), ("haha", "lol", "lmao"),
              ("thanks", "thank you", "thx", "ty")]
    out = []
    for g in groups:
        counts = [(v, variants.get(v, 0)) for v in g]
        tot = sum(c for _, c in counts)
        if tot < 5:
            continue
        best, bc = max(counts, key=lambda x: x[1])
        others = [v for v, c in counts if v != best]
        if bc / tot >= 0.8:
            out.append((best, others, bc, tot))
    return out


def avoid_list(all_stats):
    """AI-isms the person never (or almost never) uses across every register."""
    used = {}
    for F in all_stats.values():
        for k, v in (F.get("aiisms_used") or {}).items():
            used[k] = used.get(k, 0) + v
    return [k for k in AIISMS if used.get(k, 0) < 2]


def register_rules(reg, F):
    """Lines (markdown bullets with lint markers) for one register."""
    n = F.get("messages", 0)
    never, length, tend, imperf = [], [], [], []
    can_never = n >= MIN_N_NEVER
    g = lambda k: F.get(k, 0.0)

    if can_never:
        if g("per100w_em_dash") + g("per100w_en_dash") + g("per100w_double_hyphen") == 0:
            never.append("Never use em dashes, en dashes or `--`. <!-- lint:dash -->")
        if g("share_msgs_semicolon") == 0:
            never.append("Never use semicolons. <!-- lint:semicolon -->")
        if g("share_msgs_bullets") == 0:
            never.append("No bullet or numbered lists. <!-- lint:bullets -->")
        if g("share_msgs_emoji") == 0:
            never.append("No emoji or emoticons. <!-- lint:emoji -->")
        if g("per100w_repeated_bang") == 0:
            never.append("Never `!!`, `?!` or `??`. <!-- lint:repeated_bang -->")
        if g("share_msgs_exclaim") == 0:
            never.append("No exclamation marks. <!-- lint:exclaim -->")
        if g("share_msgs_greeting") < 0.02:
            never.append("No greeting line (\"Hi X,\" on its own line or to open). <!-- lint:greeting -->")
        if g("share_msgs_signoff") < 0.02:
            never.append("No sign-off (\"Thanks,\" / name at the end). <!-- lint:signoff -->")
        if (F.get("variants") or {}).get("please", 0) == 0:
            never.append("Never write \"please\". <!-- lint:please -->")
        if g("share_msgs_closer_offer") < 0.02:
            never.append("Never close with an offer (\"let me know if...\", \"hope this helps\", "
                         "\"feel free to...\"). <!-- lint:closer_offer -->")
        if g("share_msgs_line_break") == 0:
            never.append("One paragraph, no line breaks. <!-- lint:line_break -->")
        if g("per100w_contractions") > 0 and g("share_uncontracted") < 0.03:
            never.append("Always use contractions (it's, don't, I'd); never \"do not\" / \"it is\". "
                         "<!-- lint:uncontracted -->")
        if g("share_msgs_all_lowercase") > 0.9:
            never.append("Always all lowercase. <!-- lint:all_lowercase -->")
        elif g("share_lowercase_i") > 0.9:
            never.append("Always a lowercase \"i\" for the pronoun.")
        if g("share_msgs_greeting") > 0.9:
            never.append("Always open with a greeting line.")
        if g("share_msgs_signoff") > 0.9:
            never.append("Always end with a sign-off.")

    length.append("Median %s words. Half of real messages are %s-%s words; only 1 in 10 is over %s. "
                  "Aim near the median unless the content truly needs more. <!-- lint:length -->"
                  % (_fmt_words(g("len_median")), _fmt_words(g("len_p25")), _fmt_words(g("len_p75")),
                     _fmt_words(g("len_p90"))))
    if g("sentences_per_msg_median"):
        length.append("Typically %s sentence(s) per message, %s words per sentence on average."
                      % (_fmt_words(g("sentences_per_msg_median")), _fmt_words(g("sentence_len_mean"))))

    def tendency(key, label, marker=""):
        p = g(key)
        if 0.02 <= p <= 0.95:
            tend.append("%s: %s.%s" % (label, freq_of(p), (" " + marker) if marker else ""))

    ops = [w for w, _ in (F.get("top_openers") or [])][:5]
    extra = (" (yours: %s)" % ", ".join('"%s,"' % w.capitalize() for w in ops)) if ops else ""
    if 0.02 <= g("share_msgs_ack_opener") <= 0.95:
        tend.append("Opens with a short acknowledgment word and a comma: %s%s. The rest get "
                    "straight to the point, so don't add one by default. <!-- lint:habit=ack_opener -->"
                    % (freq_of(g("share_msgs_ack_opener")), extra))
    if 0.02 <= g("share_msgs_exclaim") <= 0.95:
        tend.append("Exclamation mark: %s, at most one. <!-- lint:habit=exclaim -->"
                    % freq_of(g("share_msgs_exclaim")))
    tendency("share_msgs_question", "Contains a question")
    if 0.02 <= g("share_msgs_hedge") <= 0.95:
        top = sorted((F.get("hedges") or {}).items(), key=lambda kv: -kv[1])[:5]
        tend.append("Hedges (%s): %s. <!-- lint:habit=hedge -->"
                    % (", ".join('"%s"' % k for k, _ in top) or "softeners", freq_of(g("share_msgs_hedge"))))
    tendency("share_msgs_ellipsis", "Ellipsis (...)", "<!-- lint:habit=ellipsis -->")
    tendency("share_msgs_spaced_hyphen", "Spaced hyphen as a dash (\" - \")")
    tendency("share_msgs_dash", "Em/en dash")
    tendency("share_msgs_spaced_slash", "Spaced slash for alternatives (\"a / b\")")
    tendency("share_msgs_parentheses", "Parenthetical aside")
    tendency("share_msgs_laugh", "Laughter word (haha / lol)", "<!-- lint:habit=laugh -->")
    tendency("share_msgs_emoji", "Emoji", "<!-- lint:habit=emoji -->")
    tendency("share_msgs_greeting", "Greeting")
    tendency("share_msgs_signoff", "Sign-off")
    tendency("share_msgs_line_break", "Line breaks")
    for best, others, bc, tot in _variant_prefs(F.get("variants") or {}):
        tend.append("Spelling: you write \"%s\" (%d of %d times), not %s. <!-- lint:prefer=%s:%s -->"
                    % (best, bc, tot, " / ".join('"%s"' % o for o in others), best, "|".join(others)))
    if g("flourishes_per_msg_mean"):
        tend.append("Signature habits per message: %.1f on average, rarely more than %s. Don't stack "
                    "several in one message. <!-- lint:density -->"
                    % (g("flourishes_per_msg_mean"), _fmt_words(max(1, g("flourishes_per_msg_p90")))))

    lvl = F.get("imperfection_level", "low")
    imperf.append("Measured imperfection level: **%s**. Real messages are not tidy; match this level, "
                  "don't polish it away and don't exaggerate it. <!-- lint:polish -->" % lvl)
    if g("share_end_none") >= 0.05:
        imperf.append("No final punctuation: %s." % freq_of(g("share_end_none")))
    if g("share_sentences_lowercase_start") >= 0.03:
        imperf.append("Sentence starts in lowercase: %s." % freq_of(g("share_sentences_lowercase_start"), "sentences"))
    if g("share_contractions_missing_apostrophe") >= 0.03:
        imperf.append("Contraction without its apostrophe (dont, thats): %s."
                      % freq_of(g("share_contractions_missing_apostrophe"), "contractions"))
    if g("share_sentences_subjectless") >= 0.05:
        imperf.append("Fragments that drop the subject (\"Looks good\", \"Will check tomorrow\"): %s."
                      % freq_of(g("share_sentences_subjectless"), "sentences"))
    if g("share_names_sometimes_lowercase") >= 0.1:
        imperf.append("Names and product names are sometimes typed in lowercase (%s you capitalise "
                      "also appear lowercase)." % freq_of(g("share_names_sometimes_lowercase"), "words"))
    if g("per1kw_typo_proxy") >= 2:
        imperf.append("Occasional typos or nonstandard spellings (about %.0f per 1,000 words). Never invent "
                      "them on purpose; just don't 'fix' the register into textbook prose." % g("per1kw_typo_proxy"))
    imperf.append("Real messages leave context unexplained, skip pleasantries and don't wrap up neatly.")
    return never, length, tend, imperf


def build_rules_md(all_stats, built_at, keep_text=""):
    L = ["# Voice rules", "",
         "<!-- Generated by write-like-me on %s. Everything above the KEEP BELOW line is regenerated on "
         "rebuild (the previous file is saved as rules.prev.md). Lines with lint markers are checked by "
         "`wlm.py lint`; delete a line to stop enforcing it. -->" % built_at, "",
         "How to use these: **Never / always** rules are hard. **Tendencies** are frequencies across many "
         "messages, not instructions for every message: if a habit shows up in 1 of 4 real messages, most "
         "drafts should not have it. **Permitted imperfection** describes how unpolished the real text is.",
         ""]
    av = avoid_list(all_stats)
    L += ["## all", "", "### Avoid list (default-assistant phrasing you don't use)", "",
          "- %s <!-- lint:avoid=%s -->" % (", ".join('"%s"' % a for a in av), "|".join(av)), ""]
    per_reg = {reg: register_rules(reg, F) for reg, F in all_stats.items()}
    solid = set()
    for reg, F in all_stats.items():
        if F.get("messages", 0) >= MIN_N_NEVER_SOLO:
            solid |= set(per_reg[reg][0])
    for reg, F in all_stats.items():
        n = F.get("messages", 0)
        conf = "ok" if n >= MIN_N_OK else "LOW (few samples; treat rules as rough)"
        never, length, tend, imperf = per_reg[reg]
        if n < MIN_N_NEVER_SOLO and never:
            # an absence in a few dozen messages is weak evidence (a 5%-habit is missing from 26
            # messages about 1 time in 4); keep only rules a larger register corroborates
            weak = [x for x in never if x not in solid]
            never = [x for x in never if x in solid]
            if weak:
                tend = tend + ["Probably true, but %d messages are too few to enforce as hard rules: %s." % (n, "; ".join(re.sub(r"\s*<!--.*?-->", "", x).rstrip(".")
                                                              for x in weak))]
        L += ["## %s" % reg, "", "_%d messages, confidence: %s_" % (n, conf), ""]
        if never:
            L += ["### Never / always", ""] + ["- " + x for x in never] + [""]
        L += ["### Length", ""] + ["- " + x for x in length] + [""]
        if tend:
            L += ["### Tendencies (match the frequency; never all at once)", ""] + ["- " + x for x in tend] + [""]
        L += ["### Permitted imperfection", ""] + ["- " + x for x in imperf] + [""]
    L += [KEEP_MARK, ""]
    if keep_text.strip():
        L.append(keep_text.strip())
    else:
        L += ["<!-- Your own rules go below; they survive rebuilds. Scope them with a heading such as "
              "\"## chat\" or \"## email\" (\"## all\" applies everywhere). To have lint enforce a banned "
              "phrase, end the line with a lint marker: an HTML comment containing lint:avoid=phrase1|phrase2 -->",
              "", "## all", ""]
    return "\n".join(L).rstrip() + "\n"


def split_keep(text):
    if KEEP_MARK in text:
        return text.split(KEEP_MARK, 1)
    return text, ""


def parse_rules(text, register):
    """Return list of (key, value) lint markers that apply to `register` ("all" + its own section)."""
    out = []
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+([\w-]+)\s*$", line)
        if m:
            current = m.group(1).lower()
            continue
        if current in ("all", register):
            for k, v in LINT_RE.findall(line):
                out.append((k, v))
    return out


def build_profile_md(all_stats, sources_summary, built_at):
    L = ["# Voice profile", "",
         "_Generated %s. Human-readable summary of your measured habits; `rules.md` is what the drafting "
         "skill follows, and you can edit it._" % built_at, "",
         "## Sources", ""]
    for src, c in sorted(sources_summary.items()):
        L.append("- %s: %d samples" % (src, c))
    L.append("")
    for reg, F in all_stats.items():
        n = F.get("messages", 0)
        if not n:
            continue
        g = lambda k: F.get(k, 0.0)
        L += ["## %s (%d messages, %d words)" % (reg, n, F.get("words", 0)), "",
              "| habit | you |", "|---|---|",
              "| words per message (median / p90) | %s / %s |" % (_fmt_words(g("len_median")), _fmt_words(g("len_p90"))),
              "| opens with ack word + comma | %s |" % freq_phrase(g("share_msgs_ack_opener")),
              "| greeting | %s |" % freq_phrase(g("share_msgs_greeting")),
              "| sign-off | %s |" % freq_phrase(g("share_msgs_signoff")),
              "| exclamation mark | %s |" % freq_phrase(g("share_msgs_exclaim")),
              "| question | %s |" % freq_phrase(g("share_msgs_question")),
              "| hedge | %s |" % freq_phrase(g("share_msgs_hedge")),
              "| ellipsis | %s |" % freq_phrase(g("share_msgs_ellipsis")),
              "| em/en dash | %s |" % freq_phrase(g("share_msgs_dash")),
              "| semicolon | %s |" % freq_phrase(g("share_msgs_semicolon")),
              "| emoji | %s |" % freq_phrase(g("share_msgs_emoji")),
              "| bullets | %s |" % freq_phrase(g("share_msgs_bullets")),
              "| ends with no punctuation | %s |" % freq_phrase(g("share_end_none")),
              "| lowercase sentence starts | %s |" % freq_phrase(g("share_sentences_lowercase_start")),
              "| missing apostrophes | %s |" % freq_phrase(g("share_contractions_missing_apostrophe")),
              "| imperfection level | %s |" % F.get("imperfection_level", "?"), ""]
        ops = F.get("top_openers") or []
        if ops:
            L.append("Most common openers: " + ", ".join("%s (%s)" % (w, freq_phrase(p)) for w, p in ops[:6]))
            L.append("")
        var = F.get("variants") or {}
        if var:
            top = sorted(var.items(), key=lambda kv: -kv[1])[:12]
            L.append("Word choices seen: " + ", ".join("%s x%d" % kv for kv in top))
            L.append("")
    L += ["## Notes", "",
          "Typical AI-assistant output is longer, tidier and more polite than real messages: greetings, "
          "sign-offs, closing offers, bullet lists, em dashes, \"please\", exclamation marks and full "
          "punctuation. The numbers above are what make you different from that default.", ""]
    return "\n".join(L)


FIXED_HABITS = ("ack_opener", "hedge", "question", "exclaim", "spaced_hyphen", "spaced_slash",
                "parentheses", "no_end_punct", "laugh", "ellipsis")
GROUP_CHAT_TYPES = {"group", "meeting", "channel"}


def _fixed_eligible(records, max_words):
    """Messages fit to show as stable voice samples: own prose (no URLs, code, paths, emails or
    long quotes), one paragraph (unless most of the register is multi-line, as in email), not a lead-in to an attachment ("Here's the file:"), and from a
    1:1 conversation when the source says so."""
    from .textutil import normalise, word_count
    out = []
    for r in records:
        if r.get("flags"):
            continue
        text = (r.get("text") or "").strip()
        wc = r.get("word_count") or word_count(text)
        if wc < 5 or (max_words and wc > max_words):
            continue
        n = normalise(text)
        if re.search(r"\b(?:URL|CODE|PATH|EMAIL|QUOTE)\b|\[(?:email|phone)\]", n) or text.endswith(":"):
            continue
        out.append((wc, r))
    # one paragraph, unless multi-line is how this register normally looks (email, docs)
    single = [x for x in out if "\n" not in x[1]["text"].strip()]
    if len(single) >= len(out) * 0.5:
        out = single
    one2one = [x for x in out if x[1].get("chat_type") not in GROUP_CHAT_TYPES]
    if len(one2one) >= 30 or len(one2one) >= len(out) * 0.5:
        out = one2one
    with_ctx = [x for x in out if x[1].get("context")]
    if len(with_ctx) >= 30:
        out = with_ctx
    return out


def choose_fixed(records, k=6, seed=20261106, max_words=None, n_sets=4000, mean_words=None):
    """Pick k exemplars for a register: the stable core of the voice shown in every draft.

    The set as a whole should look like the person on an average day: each optional habit
    (acknowledgment opener, hedges, questions, '!', spaced hyphen / slash, parentheses, no final
    punctuation, laughter, ellipsis) appears in the set at about its measured rate, lengths span
    short / medium / long with the set's mean near the register's mean, recipients differ, and no
    single message stacks habits. Run 2 showed
    why this matters: a set of flat, habit-free messages (picked only by length and recipient)
    made drafts noticeably less like the person on every style-embedding score, while keeping
    habit rates in check avoids the opposite failure (caricature).
    """
    elig = _fixed_eligible(records, max_words)
    if not elig:
        return []
    k = min(k, len(elig))
    elig.sort(key=lambda x: (x[0], x[1]["id"]))
    n = len(elig)
    marks = [message_markers(r["text"]) for _, r in elig]
    base = {h: sum(m[h] for m in marks) / float(n) for h in FIXED_HABITS}
    # target the register's MEAN length (pulled up by the long tail), not its median: a set of
    # median-length messages carries too little of the voice (run 2: 117 vs 184 words per set)
    target_mean = (mean_words or sum(wc for wc, _ in elig) / float(n))
    terciles = [[], [], []]
    for i in range(n):
        terciles[min(2, i * 3 // n)].append(i)
    per_t = [k // 3 + (1 if t < k % 3 else 0) for t in range(3)]
    rng = random.Random(seed)
    calm = [i for i in range(n) if sum(marks[i][h] for h in FIXED_HABITS if h != "hedge") <= 2]
    calm_set = set(calm)
    pools = [[i for i in t if i in calm_set] or t for t in terciles]

    def cost(ix):
        c = sum(abs(sum(marks[i][h] for i in ix) / float(len(ix)) - base[h]) for h in FIXED_HABITS)
        rc = [elig[i][1].get("recipient") or "" for i in ix]
        c += 0.15 * (len(rc) - len(set(rc)))
        c += 0.5 * abs(sum(elig[i][0] for i in ix) / float(len(ix)) - target_mean) / max(target_mean, 1)
        return c

    best, best_c = None, None
    for _ in range(n_sets):
        ix = []
        for t, m in zip(pools, per_t):
            if t:
                ix += rng.sample(t, min(m, len(t)))
        if len(ix) < k:
            rest = [i for i in range(n) if i not in ix]
            ix += rng.sample(rest, k - len(ix))
        c = cost(ix)
        if best_c is None or c < best_c - 1e-12:
            best, best_c = ix, c
    best.sort(key=lambda i: elig[i][0])
    return [elig[i][1]["id"] for i in best]


def rules_section(text, register):
    """The rules that apply to `register` ("all" + its own sections, generated and KEEP part),
    with HTML comments stripped, for pasting into a prompt."""
    out, current = [], None
    for line in (text or "").splitlines():
        m = re.match(r"^##\s+([\w-]+)\s*$", line)
        if m:
            current = m.group(1).lower()
            if current in ("all", register):
                out.append(line)
            continue
        if line.startswith("# "):
            continue
        if current in ("all", register):
            out.append(line)
    s = re.sub(r"<!--.*?-->", "", "\n".join(out), flags=re.S)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()
