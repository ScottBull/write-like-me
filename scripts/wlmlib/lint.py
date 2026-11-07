"""Deterministic lint of a draft against the person's hard rules.

Two levels:
  fix   a never/always rule in rules.md (its lint marker) is broken, or an avoid-list / preferred-
        spelling phrase is used. Fix these with minimal edits; nothing else is rewritten.
  info  for the person, not for a rewrite: the draft is longer than the middle half of real messages
        (the line gives the word count against the register's median, and says so when it is longer
        than ~all of them; the report's first line always gives the median), or recent
        drafts overuse a habit compared with the real rate in stats.json (the caricature counter).
Tendencies, habit density and polish are not checked: the archive in the draft's context carries
them, and rewriting toward a measured average is what made the old drafts read as imitations.
"""
import re

from .rules import parse_rules
from .stylo import CLOSER_OFFERS, GREET, HEDGES, SIGNOFF_LINE, UNCONTRACTED, message_markers
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
    if F:
        target = {"median": F.get("len_median"), "p25": F.get("len_p25"), "p75": F.get("len_p75"),
                  "p90": F.get("len_p90")}
        if F.get("len_median") and F.get("len_p75") and len(w) > F["len_p75"]:
            msg = "%d words against your median of %d; half of your real messages are %d-%d words" % (
                len(w), round(F["len_median"]), round(F.get("len_p25") or 0), round(F["len_p75"]))
            if F.get("len_p90") and len(w) > F["len_p90"] * 1.15 + 2:
                msg += ", and only 1 in 10 is over %d" % round(F["len_p90"])
            out.append(_finding("info", "length", msg + "."))
        out += _overuse(t, m, F, register, recent_drafts)
    return {"findings": out, "words": len(w), "target": target}


def _lower_starts(text):
    fl = [first_letter(x) for x in sentences(normalise(text))]
    fl = [c for c in fl if c]
    return sum(1 for c in fl if c.islower()), len(fl)


def _overuse(t, m, F, register, recent_drafts):
    """Caricature counter (info only): a habit this draft uses that the last drafts in this register
    use far more often than the person's real messages do. Rates come from stats.json."""
    out = []
    recent = [d for d in recent_drafts if d.get("register") == register][-20:]
    if len(recent) < 5:
        return out
    for h in HABIT_LABEL:
        real = F.get("share_msgs_" + h)
        if real is None or not m.get(h):
            continue
        drafted = sum(1 for d in recent if message_markers(d.get("text", "")).get(h)) / float(len(recent))
        if drafted > real * 1.6 + 0.1:
            out.append(_finding("info", "overuse:" + h, "Your last %d drafts used %s %d%% of the time vs "
                                "%d%% in your real messages." % (len(recent), HABIT_LABEL[h], round(drafted * 100),
                                                                 round(real * 100))))
    real_low = F.get("share_sentences_lowercase_start", 0.0)
    lo, n = _lower_starts(t)
    if lo and real_low < 0.5:
        rl, rn = lo, n
        for d in recent:
            a, b = _lower_starts(d.get("text", ""))
            rl, rn = rl + a, rn + b
        if rn and rl / float(rn) > real_low * 2 + 0.1:
            out.append(_finding("info", "overuse:lowercase_start", "Your recent drafts start %d%% of sentences "
                                "lowercase vs %d%% in your real messages." % (round(100.0 * rl / rn),
                                                                               round(real_low * 100))))
    per_msg = F.get("len_mean", 0) / 1000.0
    low = t.lower()
    for ph, pat in HEDGES.items():
        c = len(re.findall(pat, low))
        if not c:
            continue
        real = (F.get("hedges") or {}).get(ph, 0.0) * per_msg   # expected uses per message
        used = sum(len(re.findall(pat, normalise(d.get("text", "")).lower())) for d in recent) + c
        rate = used / float(len(recent) + 1)
        if rate > real * 2 + 0.1:
            out.append(_finding("info", "overuse:" + ph, "Your recent drafts use \"%s\" %.2f times per "
                                "message vs %.2f in your real messages." % (ph, rate, real)))
    return out


def format_report(res):
    fixes = [f for f in res["findings"] if f["level"] == "fix"]
    med = (res.get("target") or {}).get("median")
    vs = " (your median %d)" % round(med) if med else ""
    L = ["lint: %s, %d words%s" % ("%d to fix" % len(fixes) if fixes else "clean", res["words"], vs)]
    for f in fixes + [f for f in res["findings"] if f["level"] != "fix"]:
        L.append("  [%s] %s: %s" % (f["level"], f["code"], f["message"]))
    return "\n".join(L)
