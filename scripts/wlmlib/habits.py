"""The "How my messages vary" block: a few measured habits of the draft's register, as a reference note.

On Teams chat the archive alone left drafts with surface habits the person does not have: a final
period on every message, 13-19% too long, and an acknowledgment opener in half of them (the
context-budget ablation, section 5). The old pipeline fixed that with frequency rules, which read as
commands and caused caricature. This block states the same numbers as frequencies over many messages,
placed after the archive as a note about the archive, never as instructions for this message:

    Across my real chat messages, about half are 12-35 words long (the median is 21).
    About 1 in 5 has no final punctuation.
    About 1 in 4 opens with a short acknowledgment (Yea, Sure, Oh) and the rest get straight to the point.
    About 1 in 6 has an exclamation mark, and about 1 in 3 has a hedge (just, I think, maybe).
    None of them has a dash (em, en or double hyphen) or a semicolon.
    These describe the archive as a whole; any single message usually has few of them.

Every number is one `build` already measures (stylo.features, in stats.json). The "none of them" line
lists only marks that are already hard rules in rules.md (lint enforces them anyway). A habit outside
2-98% of messages is left out (it is a rule or not a habit), and a register with fewer than
MIN_MESSAGES messages gets no block (its rates are noise). Standard library only.
"""
from .rules import LINT_RE, _per_register, freq_phrase, parse_rules
from .stylo import ACK

MIN_MESSAGES = 20
LO, HI = 0.02, 0.98
MAX_OPENERS = 3
MAX_HEDGES = 3
CLOSING = "These describe the archive as a whole; any single message usually has few of them."
# hard-rule lint keys -> how the "none of them" line names the mark, in this order
MARKS = (("dash", "a dash (em, en or double hyphen)"), ("semicolon", "a semicolon"),
         ("exclaim", "an exclamation mark"))
KINDS = {"chat": "chat messages", "email": "emails", "post": "posts and comments", "doc": "passages",
         "prompt": "prompts", "review": "review comments"}


def _in_range(p):
    return p is not None and LO <= p <= HI


def _how_many(p):
    """0.21 -> 'about 1 in 5', 0.5 -> 'about half', 0.03 -> 'only about 1 in 33', 0.96 -> 'almost all of them'."""
    s = freq_phrase(p)
    if s == "almost always":
        return "almost all of them"
    return ("only " + s.replace("rarely (", "").rstrip(")")) if s.startswith("rarely") else s


def _verb(subject, singular, plural):
    return singular if " 1 in " in " " + subject else plural


def _cap(s):
    return s[:1].upper() + s[1:]


def hard_marks(stats, register, rules_text=None):
    """The lint keys among MARKS that are hard rules for `register`: from rules.md's lint markers when
    `rules_text` is given (so a rule the user deleted is not claimed), else as `build` would write them
    from stats.json."""
    if rules_text is not None:
        keys = {k for k, _ in parse_rules(rules_text, register)}
    else:
        feats = {r: d.get("features") or {} for r, d in ((stats or {}).get("registers") or {}).items()}
        if register not in feats:
            return []
        keys = {k for line in _per_register(feats)[register][0] for k, _ in LINT_RE.findall(line)}
    return [k for k, _ in MARKS if k in keys]


def ack_words(F):
    """The person's most common acknowledgment openers, capitalised: ['Yea', 'Sure', 'Oh']."""
    return [w.capitalize() for w, _ in (F.get("top_openers") or []) if w in ACK][:MAX_OPENERS]


def habit_lines(F, register="chat", marks=()):
    """The block's lines (without the heading) from one register's stats.json features; [] when the
    register is too small to say anything."""
    if not F or F.get("messages", 0) < MIN_MESSAGES or not F.get("len_median"):
        return []
    g = lambda k: F.get(k)
    L = ["Across my real %s, about half are %d-%d words long (the median is %d)."
         % (KINDS.get(register, "messages"), round(g("len_p25") or 0), round(g("len_p75") or 0),
            round(g("len_median")))]
    p = g("share_end_none")
    if _in_range(p):
        s = _how_many(p)
        L.append("%s %s no final punctuation." % (_cap(s), _verb(s, "has", "have")))
    p = g("share_msgs_ack_opener")
    if _in_range(p):
        s = _how_many(p)
        words = ack_words(F)
        L.append("%s %s with a short acknowledgment%s and the rest get straight to the point."
                 % (_cap(s), _verb(s, "opens", "open"), (" (%s)" % ", ".join(words)) if words else ""))
    parts = []
    p = g("share_msgs_exclaim")
    if "exclaim" not in marks and _in_range(p):
        parts.append(("an exclamation mark", _how_many(p)))
    p = g("share_msgs_hedge")
    if _in_range(p):
        top = [k for k, _ in sorted((F.get("hedges") or {}).items(), key=lambda kv: (-kv[1], kv[0]))][:MAX_HEDGES]
        top = [("I" + k[1:]) if k.startswith("i ") else k for k in top]
        parts.append(("a hedge" + (" (%s)" % ", ".join(top) if top else ""), _how_many(p)))
    if parts:
        (what, s), rest = parts[0], parts[1:]
        line = "%s %s %s" % (_cap(s), _verb(s, "has", "have"), what)
        if rest:
            what2, s2 = rest[0]
            line += ", and %s %s %s" % (s2, _verb(s2, "has", "have"), what2)
        L.append(line + ".")
    names = [n for k, n in MARKS if k in marks]
    if names:
        L.append("None of them has %s." % (names[0] if len(names) == 1 else
                                           ", ".join(names[:-1]) + " or " + names[-1]))
    L.append(CLOSING)
    return L


def habits_text(stats, register, rules_text=None):
    """The block's text for `register` from a pack's stats.json ('' when there is nothing to say)."""
    F = (((stats or {}).get("registers") or {}).get(register) or {}).get("features") or {}
    return "\n".join(habit_lines(F, register, hard_marks(stats, register, rules_text)))
