"""Exemplar retrieval: pure-Python TF-IDF over the bank, same recipient first.

Research note: retrieving purely by topic can make the model copy content instead of voice, so
recipient / register come first and similarity only orders within them. Fixed exemplars give the
stable core of the voice; retrieved ones add the relationship-specific register.
"""
import math
import re
from collections import Counter

from .common import parse_iso
from .textutil import ngrams, toks, word_count

LEAK_HOURS = 48


def tfidf_index(docs):
    df = Counter()
    tdocs = [toks(d) for d in docs]
    for t in tdocs:
        df.update(set(t))
    N = len(docs)
    idf = {w: math.log((N + 1.0) / (c + 1.0)) + 1 for w, c in df.items()}
    default_idf = math.log(N + 1.0) + 1

    def vec(text):
        tf = Counter(toks(text))
        v = {w: (1 + math.log(c)) * idf.get(w, default_idf) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}
    return vec, [vec(d) for d in docs]


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def recipient_match(rec, recipient):
    if not recipient:
        return False
    q = recipient.strip().lower()
    for field in (rec.get("recipient") or "", rec.get("thread") or ""):
        f = field.lower()
        if not f:
            continue
        if q == f or q in f.split() or (len(q) >= 3 and q in f):
            return True
        # spacing/punctuation-insensitive: "Dana Lee" ~ "dana.lee", "Millstone Flour" ~ "orders (millstoneflour)"
        qc, fc = re.sub(r"[\W_]+", "", q), re.sub(r"[\W_]+", "", f)
        if len(qc) >= 4 and qc in fc:
            return True
    return False


def leaks(rec, item):
    """Same thread within LEAK_HOURS of a held-out item (used by voice-check)."""
    if not item or rec.get("thread") != item.get("thread") or not rec.get("thread"):
        return False
    a, b = parse_iso(rec.get("timestamp")), parse_iso(item.get("timestamp"))
    if not a or not b:
        return False
    return abs((a - b).total_seconds()) <= LEAK_HOURS * 3600


def retrieve(bank, register, query, recipient="", k=3, n_critic=5, fixed_ids=(), exclude_ids=(),
             heldout=None, min_words=4):
    """Return dict(fixed=[...], retrieved=[...], critic=[...], same_recipient=int).

    heldout: optional record being imitated in an evaluation; anything in its thread within 48h,
    and anything sharing a 6-word run with it, is excluded.
    """
    exclude = set(exclude_ids)
    hgrams = ngrams(heldout["text"]) if heldout else set()

    def ok(r):
        if r["id"] in exclude or r.get("flags"):
            return False
        if heldout and (r["id"] == heldout["id"] or leaks(r, heldout) or (ngrams(r["text"]) & hgrams)):
            return False
        return True

    reg = [r for r in bank if r.get("register") == register and ok(r)]
    by_id = {r["id"]: r for r in reg}
    fixed = [by_id[i] for i in fixed_ids if i in by_id]
    fixed_set = {r["id"] for r in fixed}
    cand = [r for r in reg if r["id"] not in fixed_set
            and (r.get("word_count") or word_count(r["text"])) >= min_words]
    if not cand:
        return {"fixed": fixed, "retrieved": [], "critic": [], "same_recipient": 0}
    vec, V = tfidf_index([(r.get("context") or "") + " " + r["text"] for r in cand])
    q = vec(query or "")
    scored = sorted(range(len(cand)), key=lambda i: (-cosine(q, V[i]), cand[i]["id"]))
    same = [cand[i] for i in scored if recipient_match(cand[i], recipient)]
    other = [cand[i] for i in scored if not recipient_match(cand[i], recipient)]
    pick = same[:k] + other[:max(0, k - len(same[:k]))]
    picked = {r["id"] for r in pick}
    rest = [r for r in same if r["id"] not in picked] + [r for r in other if r["id"] not in picked]
    critic = rest[:n_critic]
    return {"fixed": fixed, "retrieved": pick, "critic": critic,
            "same_recipient": sum(1 for r in pick if recipient_match(r, recipient))}


def format_examples(recs, register, start=1):
    """Render exemplars the way run 1 did: 'They said: ... / I sent: ...'."""
    out = []
    for k, r in enumerate(recs):
        ctx = (r.get("context") or "").strip()
        lab = "I wrote" if register in ("doc", "prompt") else ("I posted" if register == "post" else "I sent")
        if r.get("register") == "prompt" and register != "prompt":
            lab = "I wrote (to an AI assistant)"
        elif r.get("register") == "post" and register != "post":
            lab = "I posted (on a public forum)"
        head = "Example %d" % (start + k)
        if r.get("recipient") and register in ("chat", "email"):
            head += " (to %s)" % r["recipient"]
        if r.get("subject"):
            head += " [subject: %s]" % r["subject"]
        if ctx and register in ("chat", "email", "post"):
            said = "They posted" if r.get("register") == "post" else "They said"
            out.append("%s\n%s: %s\n%s: %s" % (head, said, ctx, lab, r["text"]))
        else:
            out.append("%s\n%s: %s" % (head, lab, r["text"]))
    return "\n\n".join(out)
