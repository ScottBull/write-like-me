"""Stylometric profile of a set of messages (one register at a time).

Everything is computed from the person's own corpus; nothing here encodes any particular
person's habits. Rates are expressed per message (share of messages, 0-1), per 100 words
or per 1,000 words, as the key name says.
"""
import math
import os
import re
import statistics
from collections import Counter

from .textutil import (RE_EMOJI, RE_EMOTICON, RE_WORD, first_letter, normalise, raw_words,
                       sentences, words)

HEDGES = {
    "i think": r"\bi think\b", "maybe": r"\bmaybe\b", "probably": r"\bprobably\b", "just": r"\bjust\b",
    "might": r"\bmight\b", "perhaps": r"\bperhaps\b", "kind of": r"\bkind of\b|\bkinda\b",
    "sort of": r"\bsort of\b|\bsorta\b", "a bit": r"\ba (?:little )?bit\b|\ba little\b",
    "pretty": r"\bpretty (?!much\b)[a-z]+", "i guess": r"\bi guess\b", "seems like": r"\b(?:seems|sounds) like\b",
    "not sure": r"\bnot sure\b", "i feel": r"\bi feel\b", "possibly": r"\bpossibly\b",
    "i believe": r"\bi believe\b", "likely": r"\blikely\b", "i suspect": r"\bi suspect\b",
}
# spelling variants people are consistent about; reported as counts, never assumed
VARIANTS = {
    "yeah": r"\byeah\b", "yea": r"\byea\b", "ya": r"\bya\b", "yep": r"\byep\b", "yup": r"\byup\b",
    "ok": r"\bok\b", "okay": r"\bokay\b", "k": r"(?:^|\s)kk?(?=$|\s|[.!])",
    "haha": r"\b(?:ha){2,}h?\b", "lol": r"\blol\b", "lmao": r"\blmao\b",
    "thanks": r"\bthanks\b", "thank you": r"\bthank you\b", "thx": r"\bthx\b", "ty": r"\bty\b",
    "gonna": r"\bgonna\b", "wanna": r"\bwanna\b", "gotta": r"\bgotta\b",
    "btw": r"\bbtw\b", "fyi": r"\bfyi\b", "tbh": r"\btbh\b", "idk": r"\bidk\b", "imo": r"\bimh?o\b",
    "cool": r"\bcool\b", "awesome": r"\bawesome\b", "nice": r"\bnice\b", "great": r"\bgreat\b",
    "sure": r"\bsure\b", "totally": r"\btotally\b", "super": r"\bsuper\b", "honestly": r"\bhonestly\b",
    "basically": r"\bbasically\b", "actually": r"\bactually\b", "definitely": r"\bdefinitely\b",
    "please": r"\bplease\b|\bpls\b|\bplz\b", "sorry": r"\bsorry\b", "no worries": r"\bno worries\b",
    "can you": r"\bcan you\b", "could you": r"\bcould you\b", "would you": r"\bwould you\b",
    "let me know": r"\blet me know\b", "hope": r"\bhope\b",
}
# Phrases that mark default assistant prose. A phrase the person actually uses is dropped from
# their avoid list at build time.
AIISMS = {
    "I hope this finds you": r"\bi hope (?:this|you)\b", "please let me know": r"\bplease let me know\b",
    "don't hesitate": r"\bdon'?t hesitate\b", "happy to help": r"\b(?:happy to help|i'?d be happy to)\b",
    "great question": r"\bgreat question\b", "best regards": r"\b(?:best|kind|warm) regards\b",
    "looking forward": r"\blooking forward\b", "thank you for": r"\bthank you for\b",
    "it's worth noting": r"\bworth noting\b", "absolutely": r"\babsolutely\b", "certainly": r"\bcertainly\b",
    "additionally": r"\badditionally\b", "furthermore": r"\bfurthermore\b", "moreover": r"\bmoreover\b",
    "streamline": r"\bstreamlin\w*\b", "leverage": r"\bleverag\w*\b", "robust": r"\brobust\b",
    "seamless": r"\bseamless\w*\b", "delve": r"\bdelv\w*\b", "tapestry": r"\btapestry\b",
    "testament": r"\btestament\b", "ensure": r"\bensur\w*\b", "overall,": r"\boverall,",
    "I wanted to": r"\bi wanted to\b", "feel free": r"\bfeel free\b", "hope this helps": r"\bhope this helps\b",
    "just a quick": r"\bjust a quick\b", "circle back": r"\bcircle back\b", "touch base": r"\btouch base\b",
    "navigate": r"\bnavigat\w*\b", "crucial": r"\bcrucial\b", "pivotal": r"\bpivotal\b",
    "game-changer": r"\bgame[- ]changer\b", "not just X, but": r"\bnot just\b[^.]{1,40}\bbut\b",
    "I believe": r"\bi believe\b", "perhaps": r"\bperhaps\b", "kindly": r"\bkindly\b",
    "rest assured": r"\brest assured\b", "at your earliest convenience": r"\bearliest convenience\b",
    "in order to": r"\bin order to\b", "a great way to": r"\ba great way to\b",
}
CLOSER_OFFERS = re.compile(r"(let me know if|hope (?:this|that) helps|happy to (?:help|chat|discuss)|"
                           r"feel free to|don'?t hesitate|any questions)", re.I)
ACK = set("""yea yeah ya yep yup ok okay k kk great cool nice perfect awesome sure sounds haha hahaha lol lmao
oh ah hmm hm thanks thank alright gotcha right no yes wow interesting oops whoops ugh agreed good excellent
fantastic hey totally true fair word bet sweet""".split())
GREET = re.compile(r"^\s*(?:hi|hey|hello|dear|hiya|good (?:morning|afternoon|evening)|morning)\b", re.I)
SIGNOFF_LINE = re.compile(r"^\s*(?:thanks|thank you|thx|best|cheers|regards|best regards|kind regards|"
                          r"warm regards|talk soon|take care|sincerely|-\s*\w+)\s*[,!.]?\s*$", re.I)
NAME_LINE = re.compile(r"^\s*[A-Z][\w.'-]*(?:\s+[A-Z&][\w.'-]*){0,3}\s*$")
TRAIL_NOISE = re.compile(r"(?:\s|URL|PATH|CODE|QUOTE|EMAIL)+$")


def body_end(t):
    """Text with trailing placeholders and, in multi-line messages, a closing sign-off / name line
    removed ("Thanks,\nTheo"), so final punctuation is measured on the last real sentence."""
    lines = (t or "").rstrip().split("\n")
    real = [i for i, l in enumerate(lines) if l.strip()]
    if len(real) > 1 and NAME_LINE.match(lines[real[-1]]):
        prev = real[-2]
        if SIGNOFF_LINE.match(lines[prev]) or prev < real[-1] - 1:   # after a sign-off or a blank line
            lines = lines[:real[-1]]
            real = real[:-1]
    if len(real) > 1 and SIGNOFF_LINE.match(lines[real[-1]]):
        lines = lines[:real[-1]]
    return TRAIL_NOISE.sub("", "\n".join(lines))


SUBJECTLESS_START = set("""got saw was were looks look sounds sound seems seem will would should could can can't
cant won't wont didn't didnt doesn't doesnt don't dont isn't wasn't have has had need needs think thinking going
gonna wanted want trying tried heard found figured guess hoping just working looking getting sent made""".split())
CONTR = {w.replace("'", ""): w for w in """don't doesn't didn't isn't wasn't aren't weren't can't couldn't wouldn't
shouldn't haven't hasn't hadn't won't i'm i've you're you've they're they've that's what's there's here's
who's it'll you'll they'll we've we're""".split()}
for _w in ("were", "well", "its", "id", "ill", "shell", "wed", "hell", "whos"):
    CONTR.pop(_w, None)
UNCONTRACTED = re.compile(r"\b(?:do not|does not|did not|is not|was not|are not|cannot|can not|could not|"
                          r"would not|should not|i am|i have|it is|that is|we are|you are|they are|i will|"
                          r"we will|have not|has not)\b", re.I)

# 's and 'd are contractions only after these words; elsewhere ('s) they are possessives
# ("Sam's", "team's") or rare forms. 't / 're / 've / 'll / 'm are always contractions.
S_BASES = set("""it that what there here he she who where how let when why everyone everybody someone somebody
nobody something everything nothing one""".split())
D_BASES = set("i you he she we they it that who there what".split())


def is_contraction(w):
    """w: a lowercased word token with a straight apostrophe."""
    if "'" not in w:
        return False
    base, _, suf = w.rpartition("'")
    if suf in ("t", "re", "ve", "ll", "m"):
        return True
    if suf == "s":
        return base in S_BASES
    if suf == "d":
        return base in D_BASES
    return False


_DICT = None


def load_dict():
    """System word list as a typo proxy. Empty set if unavailable (typo rate then omitted)."""
    global _DICT
    if _DICT is None:
        d = set()
        for p in ("/usr/share/dict/words", "/usr/share/dict/web2", "/usr/dict/words"):
            if os.path.exists(p):
                with open(p, errors="ignore") as f:
                    d |= {l.strip().lower() for l in f}
        _DICT = d
    return _DICT


def _known(w, dic, cfreq):
    w = w.replace("'", "")
    if w in dic or cfreq.get(w, 0) >= 3 or len(w) <= 2:
        return True
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""),
                     ("ing", "e"), ("ly", ""), ("er", ""), ("est", ""), ("d", ""), ("ied", "y"), ("n", "")):
        if w.endswith(suf) and (w[: -len(suf)] + rep) in dic:
            return True
    return w in CONTR


def _edits1(w):
    a = "abcdefghijklmnopqrstuvwxyz"
    sp = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    return ({l + r[1:] for l, r in sp if r} | {l + r[1] + r[0] + r[2:] for l, r in sp if len(r) > 1}
            | {l + c + r[1:] for l, r in sp if r for c in a} | {l + c + r for l, r in sp for c in a})


def _likely_typo(w, dic, cfreq, capped):
    """A typo proxy that doesn't count names, product names or jargon missing from the system word
    list: a rare out-of-dictionary token, never written capitalised, that is one edit away from a
    word the person uses often (e.g. "teh" when they write "the" a lot)."""
    if len(w) < 3 or cfreq.get(w, 0) >= 2 or w in capped or _known(w, dic, cfreq):
        return False
    return any(cfreq.get(e, 0) >= 3 and (e in dic or e in CONTR) for e in _edits1(w) if e != w)


def pct(x, n):
    return float(x) / n if n else 0.0


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    i = (len(xs) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def opener_word(t):
    """First word of a message if it is followed by a comma/!/end, e.g. 'Sure,' -> 'sure'."""
    m = re.match(r"^\s*([A-Za-z']+)\s*(,|!|\.|$)", t or "")
    return m.group(1).lower() if m else None


def message_markers(text):
    """Booleans for one message: the optional habits that make up a person's signature.
    Used both for corpus rates and to spot caricature (a draft stacking several at once)."""
    t = normalise(text)
    low = t.lower()
    w = words(t)
    ss = sentences(t)
    end = body_end(t)
    ow = opener_word(t)
    fl = first_letter(ss[0]) if ss else None
    return {
        "ack_opener": bool(ow and ow in ACK and len(w) > 1),
        "greeting": bool(GREET.match(t)),
        "exclaim": "!" in t,
        "question": "?" in t,
        "ellipsis": bool(re.search(r"\.{3,}|…", t)),
        "hedge": any(re.search(p, low) for p in HEDGES.values()),
        "spaced_hyphen": bool(re.search(r"(?<=\w) - (?=\w)", t)),
        "dash": bool(re.search(r"[—–]|(?<=\w)--(?=\w)", t)),
        "spaced_slash": bool(re.search(r"\w / \w", t)),
        "parentheses": "(" in t,
        "emoji": bool(RE_EMOJI.search(text or "")) or bool(RE_EMOTICON.search(t)),
        "laugh": bool(re.search(r"\b(?:ha){2,}h?\b|\blol\b|\blmao\b", low)),
        "lowercase_start": bool(fl and fl.islower()),
        "no_end_punct": bool(end) and end[-1].isalnum(),
        "line_break": "\n" in t.strip(),
        "semicolon": ";" in t,
    }


FLOURISHES = ("ack_opener", "exclaim", "ellipsis", "spaced_hyphen", "spaced_slash", "parentheses",
              "emoji", "laugh", "dash")


def flourish_count(markers):
    return sum(1 for k in FLOURISHES if markers.get(k))


def features(texts):
    """Profile of a list of message texts. Returns a JSON-serialisable dict."""
    recs = []
    for t in texts:
        n = normalise(t)
        w = words(n)
        if w:
            recs.append((t, n, w))
    F = {"messages": len(recs)}
    if not recs:
        return F
    n = len(recs)
    wc = [len(w) for _, _, w in recs]
    tw = sum(wc) or 1
    F["words"] = tw
    F["len_median"] = statistics.median(wc)
    F["len_mean"] = statistics.mean(wc)
    for q in (10, 25, 75, 90):
        F["len_p%d" % q] = quantile(wc, q / 100.0)

    sl, n_sent, low_start, let_start, frag = [], 0, 0, 0, 0
    sents_per = []
    for _, t, _ in recs:
        ss = sentences(t)
        sents_per.append(len(ss))
        for s in ss:
            sw = words(s)
            sl.append(len(sw))
            n_sent += 1
            fl = first_letter(s)
            if fl:
                let_start += 1
                low_start += fl.islower()
            if sw and sw[0] in SUBJECTLESS_START:
                frag += 1
    F["sentences_per_msg_median"] = statistics.median(sents_per)
    F["sentence_len_mean"] = statistics.mean(sl) if sl else 0
    F["sentence_len_cv"] = (statistics.pstdev(sl) / F["sentence_len_mean"]) if sl and F["sentence_len_mean"] else 0
    F["share_sentences_lowercase_start"] = pct(low_start, let_start)
    F["share_sentences_subjectless"] = pct(frag, n_sent)

    # per-message markers
    M = [message_markers(raw) for raw, _, _ in recs]
    for k in M[0]:
        F["share_msgs_" + k] = pct(sum(1 for m in M if m[k]), n)
    fc = [flourish_count(m) for m in M]
    F["flourishes_per_msg_mean"] = statistics.mean(fc)
    F["flourishes_per_msg_p90"] = quantile(fc, 0.9)

    all_lower = elig = 0
    for raw, t, w in recs:
        prose = re.sub(r"\b(?:CODE|URL|PATH|QUOTE|EMAIL)\b", "", t)
        if len(w) >= 3 and re.search(r"[A-Za-z]", prose):
            elig += 1
            all_lower += not re.search(r"[A-Z]", prose)
    F["share_msgs_all_lowercase"] = pct(all_lower, elig)

    i_low = i_up = 0
    for _, t, _ in recs:
        for tok in RE_WORD.findall(t):
            t2 = tok.replace("’", "'")
            if t2.lower() in ("i", "i'm", "i've", "i'll", "i'd"):
                if t2[0] == "i":
                    i_low += 1
                else:
                    i_up += 1
    F["share_lowercase_i"] = pct(i_low, i_low + i_up)

    alltext = "\n".join(t for _, t, _ in recs)
    per100 = lambda c: 100.0 * c / tw
    P = {
        "comma": alltext.count(","),
        "exclamation": alltext.count("!"),
        "question": alltext.count("?"),
        "repeated_bang": len(re.findall(r"[!?]{2,}", alltext)),
        "ellipsis": len(re.findall(r"\.{3,}|…", alltext)),
        "em_dash": alltext.count("—"),
        "en_dash": alltext.count("–"),
        "double_hyphen": len(re.findall(r"(?<=\w) ?-- ?(?=\w)", alltext)),
        "spaced_hyphen": len(re.findall(r"(?<=\w) - (?=\w)", alltext)),
        "semicolon": alltext.count(";"),
        "colon": len(re.findall(r":(?!//)", alltext)),
        "parentheses": alltext.count("("),
        "spaced_slash": len(re.findall(r"\w / \w", alltext)),
        "ampersand": alltext.count("&"),
        "tilde_approx": len(re.findall(r"~\s?\d", alltext)),
    }
    for k, v in P.items():
        F["per100w_" + k] = per100(v)
    F["emoji_per_msg"] = pct(sum(len(RE_EMOJI.findall(raw)) for raw, _, _ in recs), n)

    ends = Counter()
    for _, t, _ in recs:
        e = body_end(t)
        c = e[-1:] if e else ""
        if e.endswith("...") or e.endswith("…"):
            ends["ellipsis"] += 1
        elif c in ".?!":
            ends[{".": "period", "?": "question", "!": "exclamation"}[c]] += 1
        elif c and RE_EMOJI.match(c):
            ends["emoji"] += 1
        elif c.isalnum():
            ends["none"] += 1
        else:
            ends["other"] += 1
    for k in ("period", "none", "question", "exclamation", "ellipsis", "emoji", "other"):
        F["share_end_" + k] = pct(ends[k], n)

    allw = [x for _, _, w in recs for x in w]
    n_contr = sum(1 for x in allw if is_contraction(x))
    missing = sum(1 for x in allw if x in CONTR)
    present = sum(1 for x in allw if x in CONTR.values())
    F["per100w_contractions"] = per100(n_contr)
    F["share_contractions_missing_apostrophe"] = pct(missing, missing + present)
    expanded = len(UNCONTRACTED.findall(alltext))
    F["share_uncontracted"] = pct(expanded, expanded + n_contr)

    # inconsistent capitalisation: word types seen both Capitalised mid-sentence and all-lowercase
    caps, lows = Counter(), Counter()
    for _, t, _ in recs:
        for s in sentences(t):
            toks_ = raw_words(s)
            for tok in toks_[1:]:
                if tok[0].isupper() and tok[1:].islower() and len(tok) > 2:
                    caps[tok.lower()] += 1
                elif tok.islower():
                    lows[tok] += 1
    # only words that are normally capitalised (names, products); otherwise common words that
    # happen to be capitalised after a colon or in a heading ("The", "And") dominate on big samples
    namey = [k for k, v in caps.items() if v >= 2 and v >= 2 * lows.get(k, 0)]
    F["share_names_sometimes_lowercase"] = pct(sum(1 for k in namey if lows.get(k)), len(namey))

    low = alltext.lower()
    hedge = {k: len(re.findall(p, low)) for k, p in HEDGES.items()}
    F["per1kw_hedges"] = 1000.0 * sum(hedge.values()) / tw
    F["hedges"] = {k: round(1000.0 * v / tw, 2) for k, v in hedge.items() if v}
    var = {k: len(re.findall(p, low)) for k, p in VARIANTS.items()}
    F["variants"] = {k: v for k, v in var.items() if v}
    ai = {k: len(re.findall(p, low)) for k, p in AIISMS.items()}
    F["aiisms_used"] = {k: v for k, v in ai.items() if v}
    F["per1kw_aiisms"] = 1000.0 * sum(ai.values()) / tw
    F["share_msgs_closer_offer"] = pct(sum(1 for _, t, _ in recs if CLOSER_OFFERS.search(t)), n)

    so = bullets = 0
    for _, t, _ in recs:
        lines = [l for l in t.split("\n") if l.strip()]
        if len(lines) > 1 and (SIGNOFF_LINE.match(lines[-1]) or SIGNOFF_LINE.match(lines[-2])):
            so += 1
        if any(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+\S", l) for l in lines):
            bullets += 1
    F["share_msgs_signoff"] = pct(so, n)
    F["share_msgs_bullets"] = pct(bullets, n)
    F["share_msgs_multi_paragraph"] = pct(sum(1 for _, t, _ in recs if re.search(r"\n\s*\n", t)), n)

    op = Counter(opener_word(raw) for raw, _, _ in recs)
    op.pop(None, None)
    F["top_openers"] = [[k, round(v / n, 3)] for k, v in op.most_common(8) if v >= 2]
    fw = Counter((words(t)[0] if words(t) else "") for _, t, _ in recs)
    F["top_first_words"] = [[k, round(v / n, 3)] for k, v in fw.most_common(8) if v >= 2]

    dic = load_dict()
    if dic:
        cfreq = Counter(x.replace("'", "") for x in allw)
        capped = {x.replace("'", "").lower() for _, t, _ in recs for x in raw_words(t) if x[:1].isupper()}
        oov = sum(1 for x in allw if _likely_typo(x.replace("'", ""), dic, cfreq, capped))
        F["per1kw_typo_proxy"] = 1000.0 * oov / tw
    F["imperfection_level"] = imperfection_level(F)
    return F


def imperfection_level(F):
    """Coarse label (low / medium / high) for how unpolished the person's text is, from the
    shares of messages with no final punctuation, lowercase starts, missing apostrophes,
    subjectless sentences and typos."""
    score = 0.0
    score += min(F.get("share_end_none", 0) / 0.4, 1.0)
    score += min(F.get("share_sentences_lowercase_start", 0) / 0.3, 1.0)
    score += min(F.get("share_contractions_missing_apostrophe", 0) / 0.2, 1.0)
    score += min(F.get("share_sentences_subjectless", 0) / 0.25, 1.0)
    score += min(F.get("per1kw_typo_proxy", 0) / 5.0, 1.0)  # proxy counts likely misspellings only
    score /= 5.0
    return "low" if score < 0.2 else ("medium" if score < 0.45 else "high")
