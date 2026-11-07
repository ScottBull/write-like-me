"""TF-IDF index (pure Python) and recipient matching.

`tfidf_index` / `cosine` back selection strategy M (select.py). `recipient_match` is used by
compose.learned_for. The leak rule lives in select.py (`leaks`).
"""
import math
import re
from collections import Counter

from .textutil import toks


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
