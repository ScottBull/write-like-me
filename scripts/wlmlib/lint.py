"""Deterministic lint of a draft against the person's rules and measured stats.

Three kinds of finding:
  fix       a hard rule is broken, an AI-ism slipped in, or the draft is longer than ~all real ones
  consider  caricature risk (habits stacked, or recent drafts overusing a habit) or too polished
  info      context numbers for the reviser
"""
import re

from .rules import parse_rules
from .stylo import (CLOSER_OFFERS, GREET, HEDGES, SIGNOFF_LINE, UNCONTRACTED, body_end, flourish_count, message_markers)
from .textutil import RE_EMOJI, RE_EMOTICON, first_letter, normalise, sentences, words

HABIT_LABEL = {
    "ack_opener": "opening with an acknowledgment word + comma",
    "exclaim": "exclamation marks",
    "hedge": "hedges",
    "ellipsis": "ellipses",
    "laugh": "laughter words",
    "emoji": "emoji",
}


def _finding(level, code, msg):
    return {"level": level, "code": code, "message": msg}


def lint(text, register, rules_text, stats, recent_drafts=()):
    """Return dict(findings=[...], words=int, target=dict)."""
    F = (stats.get("registers", {}).get(register) or {}).get("features") or {}
    markers = parse_rules(rules_text or "", register)
    keys = {k for k, _ in markers}
    t = normalise(text)
    low = t.lower()
    w = words(t)
    out = []
    m = message_markers(text)
    lines = [l for l in t.split("\n") if l.strip()]

    if "dash" in keys and re.search(r"[—–]|(?<=\w) ?-- ?(?=\w)", t):
        out.append(_finding("fix", "dash", "Uses an em/en dash or '--'; you never do."))
    if "semicolon" in keys and ";" in t:
        out.append(_finding("fix", "semicolon", "Uses a semicolon; you never do."))
    if "bullets" in keys and any(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+\S", l) for l in lines):
        out.append(_finding("fix", "bullets", "Uses a list; you never do in this register."))
    if "emoji" in keys and (RE_EMOJI.search(text) or RE_EMOTICON.search(t)):
        out.append(_finding("fix", "emoji", "Uses emoji/emoticons; you never do here."))
    if "repeated_bang" in keys and re.search(r"[!?]{2,}", t):
        out.append(_finding("fix", "repeated_bang", "Repeated !/? punctuation."))
    if "exclaim" in keys and "!" in t:
        out.append(_finding("fix", "exclaim", "Exclamation mark; you never use them here."))
    if "greeting" in keys and GREET.match(t):
        out.append(_finding("fix", "greeting", "Opens with a greeting; you don't in this register."))
    if "signoff" in keys and len(lines) > 1 and (SIGNOFF_LINE.match(lines[-1]) or SIGNOFF_LINE.match(lines[-2])):
        out.append(_finding("fix", "signoff", "Ends with a sign-off; you don't in this register."))
    if "please" in keys and re.search(r"\bplease\b|\bpls\b", low):
        out.append(_finding("fix", "please", "Says 'please'; you never do here."))
    if "closer_offer" in keys and CLOSER_OFFERS.search(t):
        out.append(_finding("fix", "closer_offer", "Closing offer ('let me know if...', 'feel free...')."))
    if "line_break" in keys and len(lines) > 1:
        out.append(_finding("fix", "line_break", "Has line breaks; your messages here are one paragraph."))
    if "uncontracted" in keys:
        hits = sorted(set(x.lower() for x in UNCONTRACTED.findall(t)))
        if hits:
            out.append(_finding("fix", "uncontracted", "Uncontracted: %s. You contract." % ", ".join(hits)))
    if "all_lowercase" in keys and re.search(r"[A-Z]", re.sub(r"\b(?:CODE|URL|PATH|QUOTE|EMAIL)\b", "", t)):
        out.append(_finding("fix", "all_lowercase", "Has capitals; you write all lowercase."))

    for k, v in markers:
        if k == "avoid" and v:
            for phrase in [p.strip() for p in v.split("|") if p.strip()]:
                pat = r"\b" + re.escape(phrase.lower()).replace(r"\ ", r"\s+") + (r"\b" if phrase[-1].isalnum() else "")
                if re.search(pat, low):
                    out.append(_finding("fix", "avoid", "Avoid-list phrase: \"%s\"." % phrase))
        elif k == "prefer" and v and ":" in v:
            best, others = v.split(":", 1)
            for o in [x for x in others.split("|") if x]:
                if re.search(r"\b%s\b" % re.escape(o), low):
                    out.append(_finding("fix", "prefer", "You write \"%s\", not \"%s\"." % (best, o)))

    target = {}
    if F and "length" in keys:
        target = {"median": F.get("len_median"), "p25": F.get("len_p25"), "p75": F.get("len_p75"),
                  "p90": F.get("len_p90")}
        n = len(w)
        if F.get("len_p90") and n > F["len_p90"] * 1.15 + 2:
            out.append(_finding("fix", "length", "%d words; only 1 in 10 of your real messages is over %d. Cut."
                                % (n, round(F["len_p90"]))))
        elif F.get("len_p75") and n > F["len_p75"] + 2:
            out.append(_finding("consider", "length", "%d words; longer than 3 in 4 of your real messages "
                                "(median %d). Cut if the content allows." % (n, round(F.get("len_median", 0)))))

    if F and "density" in keys:
        fc = flourish_count(m)
        p90 = max(1.0, F.get("flourishes_per_msg_p90", 1.0))
        if fc > p90:
            used = [k for k in ("ack_opener", "exclaim", "ellipsis", "spaced_hyphen", "spaced_slash",
                                "parentheses", "emoji", "laugh", "dash") if m.get(k)]
            out.append(_finding("consider", "density", "Stacks %d signature habits (%s); real messages rarely "
                                "have more than %d. Drop the least natural one(s)." % (fc, ", ".join(used), p90)))

    # caricature across recent drafts: habit rate in the last drafts vs the real rate
    habits = [v for k, v in markers if k == "habit"]
    recent = [d for d in recent_drafts if d.get("register") == register][-20:]
    if F and len(recent) >= 5:
        for h in habits:
            real = F.get("share_msgs_" + h)
            if real is None:
                continue
            drafted = sum(1 for d in recent if message_markers(d.get("text", "")).get(h)) / float(len(recent))
            if m.get(h) and drafted > real * 1.6 + 0.1:
                out.append(_finding("consider", "overuse:" + h, "Your last %d drafts used %s %d%% of the time vs "
                                    "%d%% in your real messages. Skip it this time."
                                    % (len(recent), HABIT_LABEL.get(h, h), round(drafted * 100), round(real * 100))))

    # capitalisation and phrase frequency vs measured rates (catches a drafter or reviser that
    # lowercases everything or leans on one hedge, which per-message habit flags don't see)
    if F:
        out += _rate_checks(t, F, register, recent)

    if F and "polish" in keys and F.get("imperfection_level") in ("medium", "high"):
        tidy = []
        e = body_end(t)
        if F.get("share_end_none", 0) >= 0.3 and e and not e[-1].isalnum():
            tidy.append("final punctuation (you skip it in %d%% of messages)" % round(F["share_end_none"] * 100))
        ss = sentences(t)
        if len(ss) >= 3 and F.get("sentence_len_cv", 0) > 0.5:
            lens = [len(words(s)) for s in ss]
            mean = sum(lens) / float(len(lens))
            var = sum((x - mean) ** 2 for x in lens) / len(lens)
            if mean and (var ** 0.5) / mean < 0.25:
                tidy.append("evenly sized sentences (yours vary a lot)")
        if tidy:
            out.append(_finding("consider", "polish", "Tidier than you usually are: " + "; ".join(tidy)
                                + ". Permitted, not required; don't add mistakes on purpose."))
    return {"findings": out, "words": len(w), "target": target}


def _lower_starts(text):
    fl = [first_letter(x) for x in sentences(normalise(text))]
    fl = [c for c in fl if c]
    return sum(1 for c in fl if c.islower()), len(fl)


def _rate_checks(t, F, register, recent):
    out = []
    real_low = F.get("share_sentences_lowercase_start", 0.0)
    lo, n = _lower_starts(t)
    if lo and real_low < 0.5:
        if lo >= 2 and lo / float(n) >= 0.5 and real_low < 0.15:
            out.append(_finding("fix", "capitalisation", "%d of %d sentences start lowercase; only %d%% of yours do. "
                                "Capitalise sentence starts as you normally would." % (lo, n, round(real_low * 100))))
        elif len(recent) >= 5:
            rl = rn = 0
            for d in recent:
                a, b = _lower_starts(d.get("text", ""))
                rl, rn = rl + a, rn + b
            rl, rn = rl + lo, rn + n
            if rn and rl / float(rn) > real_low * 2 + 0.1:
                out.append(_finding("consider", "overuse:lowercase_start", "Your recent drafts start %d%% of sentences "
                                    "lowercase vs %d%% in your real messages. Capitalise this one."
                                    % (round(100.0 * rl / rn), round(real_low * 100))))
    per_msg = F.get("len_mean", 0) / 1000.0
    low = t.lower()
    for ph, pat in HEDGES.items():
        c = len(re.findall(pat, low))
        if not c:
            continue
        real = (F.get("hedges") or {}).get(ph, 0.0) * per_msg   # expected uses per message
        if c >= 2 and real < 0.3:
            out.append(_finding("consider", "phrase:" + ph, "\"%s\" %d times in one message; you use it about "
                                "%.2f times per message." % (ph, c, real)))
        elif len(recent) >= 5:
            used = sum(len(re.findall(pat, normalise(d.get("text", "")).lower())) for d in recent) + c
            rate = used / float(len(recent) + 1)
            if rate > real * 2 + 0.1:
                out.append(_finding("consider", "overuse:" + ph, "Your recent drafts use \"%s\" %.2f times per "
                                    "message vs %.2f in your real messages. Drop it here." % (ph, rate, real)))
    return out


def format_report(res):
    if not res["findings"]:
        return "lint: clean (%d words)" % res["words"]
    L = ["lint: %d finding(s), %d words" % (len(res["findings"]), res["words"])]
    for f in res["findings"]:
        L.append("  [%s] %s: %s" % (f["level"], f["code"], f["message"]))
    return "\n".join(L)
