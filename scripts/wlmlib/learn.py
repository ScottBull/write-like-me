"""voice-learn: compare a draft with the version the person actually sent.

The person's own edits are the best source of new rules (CIPHER-style learning from edits; see
docs/research.md). This module only *proposes*; the skill asks before anything is written.
"""
import difflib
import re
from collections import Counter

from .common import now_iso
from .stylo import AIISMS, message_markers
from .textutil import words

TOKEN = re.compile(r"[A-Za-z0-9']+|[^\sA-Za-z0-9']")
MARKER_LABEL = {
    "ack_opener": "acknowledgment opener", "greeting": "greeting", "exclaim": "exclamation mark",
    "ellipsis": "ellipsis", "hedge": "hedge", "dash": "em/en dash", "semicolon": "semicolon",
    "emoji": "emoji", "line_break": "line breaks", "no_end_punct": "no final punctuation",
    "lowercase_start": "lowercase start", "parentheses": "parenthetical",
}


def _tok(s):
    return TOKEN.findall(s or "")


def _join(toks):
    s = " ".join(toks)
    return re.sub(r"\s+([,.!?;:)])", r"\1", s).replace("( ", "(")


def diff(draft, sent):
    """Word-level edit list: [{"op": "delete"|"insert"|"replace", "draft": str, "sent": str}]."""
    a, b = _tok(draft), _tok(sent)
    sm = difflib.SequenceMatcher(a=[x.lower() for x in a], b=[x.lower() for x in b], autojunk=False)
    edits = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            # case-only changes still matter (capitalisation habits)
            for x, y in zip(a[i1:i2], b[j1:j2]):
                if x != y:
                    edits.append({"op": "replace", "draft": x, "sent": y, "case_only": True})
            continue
        edits.append({"op": op, "draft": _join(a[i1:i2]), "sent": _join(b[j1:j2])})
    return edits, sm.ratio()


def report(draft, sent, register="chat", stats=None, rules_text=""):
    edits, ratio = diff(draft, sent)
    wd, ws = len(words(draft)), len(words(sent))
    md, ms = message_markers(draft), message_markers(sent)
    changed = []
    for k, lab in MARKER_LABEL.items():
        if md.get(k) != ms.get(k):
            changed.append(("removed " if md.get(k) else "added ") + lab)
    low_d, low_s = draft.lower(), sent.lower()
    ai_removed = [k for k, p in AIISMS.items() if re.search(p, low_d) and not re.search(p, low_s)]

    proposals = []
    have = (rules_text or "").lower()
    for k in ai_removed:
        if ("lint:avoid=" in have and re.search(r"lint:avoid=[^>]*\b%s\b" % re.escape(k.lower()), have)):
            continue
        proposals.append({"kind": "avoid", "text": k,
                          "rule": "- Never \"%s\" <!-- lint:avoid=%s -->" % (k, k)})
    deleted = [e["draft"].strip(" ,.;:!?") for e in edits if e["op"] == "delete" and len(e["draft"].split()) >= 2]
    for phrase in deleted[:5]:
        if phrase.lower() not in low_s and not any(phrase.lower() == p["text"].lower() for p in proposals):
            proposals.append({"kind": "cut", "text": phrase,
                              "rule": "- Cut phrases like \"%s\" (you deleted it from a draft)." % phrase})
    repl = Counter((e["draft"].lower(), e["sent"].lower()) for e in edits
                   if e["op"] == "replace" and not e.get("case_only")
                   and 1 <= len(e["draft"].split()) <= 3 and 1 <= len(e["sent"].split()) <= 3)
    for (a, b), c in repl.most_common(5):
        if re.search(r"[A-Za-z]", a) and re.search(r"[A-Za-z]", b):
            proposals.append({"kind": "prefer", "text": "%s -> %s" % (a, b),
                              "rule": "- Write \"%s\" rather than \"%s\"." % (b, a)})
    if wd and ws < wd * 0.75 and wd - ws >= 4:
        target = ""
        F = ((stats or {}).get("registers", {}).get(register) or {}).get("features") or {}
        if F.get("len_median"):
            target = " Real median is %d words." % round(F["len_median"])
        proposals.append({"kind": "length", "text": "cut %d%%" % round(100 * (wd - ws) / float(wd)),
                          "rule": "- Drafts run long: you cut this one from %d to %d words.%s" % (wd, ws, target)})
    for c in changed:
        proposals.append({"kind": "habit", "text": c,
                          "rule": "- When editing a draft you %s." % c})
    return {"similarity": round(ratio, 3), "words_draft": wd, "words_sent": ws, "edits": edits[:40],
            "marker_changes": changed, "ai_isms_removed": ai_removed, "proposals": proposals}


def format_report(rep):
    L = ["similarity %.0f%%, %d -> %d words" % (rep["similarity"] * 100, rep["words_draft"], rep["words_sent"])]
    for e in rep["edits"][:20]:
        if e["op"] == "delete":
            L.append("  - removed: %s" % e["draft"])
        elif e["op"] == "insert":
            L.append("  + added:   %s" % e["sent"])
        else:
            L.append("  ~ changed: %s -> %s" % (e["draft"], e["sent"]))
    if rep["proposals"]:
        L.append("proposed notes:")
        for i, p in enumerate(rep["proposals"], 1):
            L.append("  %d. %s" % (i, p["rule"]))
    return "\n".join(L)


def note_block(lines, register, recipient=""):
    head = "## %s %s%s" % (now_iso()[:10], register, (" (to %s)" % recipient) if recipient else "")
    return "\n" + head + "\n" + "\n".join(l if l.startswith("- ") else "- " + l for l in lines) + "\n"
