"""Budgeted archive selection and context assembly.

The drafter sees a selection of the person's own archive entries (archive/<register>.jsonl), as
many as fit a token budget, in date order, framed as "write the next entry" (templates/context.md).
This module picks those entries and renders the context.

Strategies (ported from the context-budget ablation; each is a pure function
`select_X(entries, budget_tokens, request, seed) -> entries in date order`):

  R  random (seeded)                                          the no-skill baseline
  D  stratified over where x length tercile x year,           coverage of situations (the default)
     newest-first inside each stratum
  M  TF-IDF match of the request (context + notes + subject)  "similar situations"; falls back to D
     against entry context + text                             when nothing matches
  T  the newest entries that fit                              recency
  C  D over compact entries (context cut to 12 words)         more entries per token
  P  person file + D for what the person file leaves          the budget covers both

Every strategy fills greedily in its own priority order, skipping an entry that would overflow and
going on with the next, so the sum of entry costs never exceeds `budget_tokens`. An entry's cost is
its rendered text plus the blank line after it at CHARS_PER_TOKEN, rounded up (`entry_tokens`), so
the rendered archive section is within the budget to the token. The ablation harness counted
fractional tokens; rounding each entry up makes this module a little more conservative.

`request` is a dict, all keys optional:
  context, notes, subject   what the new message replies to and says (M reads them)
  person_text               the person file text (P subtracts its cost from the budget)
  learned_text              the learned notes (P subtracts their cost too; for the other strategies
                            the caller passes a budget with person and notes already taken off)
  habits_text               the "How my messages vary" block's text (P subtracts it as well)
  heldout                   a record, or a list of records, being imitated (voice-check): the record
                            itself and everything in its thread within `leak_hours` are excluded
  leak_hours                48 by default; None excludes the whole thread
  exclude_ids               ids never to select

Standard library only.
"""
import heapq
import random

from . import compose
from .archive import date_key
from .common import est_tokens, parse_iso
from .retrieve import cosine, tfidf_index

SEP = "\n\n"
LEAK_HOURS = 48
COMPACT_CONTEXT_WORDS = 12
SUBJECT_MAX = 80
NOTES_LABEL = "What I want to say (my notes, not my wording):"

# The "Replying to" line of an entry, and what it says when there was nothing before it.
CONTEXT_LINE = {"chat": ("Replying to", "(nothing: I started this one)"),
                "email": ("Replying to", "(nothing: I started this one)"),
                "prompt": ("Replying to", "(nothing: I started this one)"),
                "post": ("Replying to", "(nothing: this is a new post)"),
                "review": ("Replying to", "(nothing: this is a new comment on the proof)"),
                "doc": ("Following", "(nothing: this is where it starts)")}


# ----------------------------------------------------------------------------- rendering

def _flat(s):
    return " ".join((s or "").split())


def _kind(register):
    return compose.CONTEXT_WORDS.get(register, compose.CONTEXT_WORDS["chat"])["kind"]


def entry_header(e, register=None):
    """'### to Dana, 2026-03-01' (chat, email), '### r/bikes, 2026-03-01' (post), '### 2026-03-01'."""
    reg = register or e.get("register") or "chat"
    date = (e.get("timestamp") or "")[:10] or "undated"
    who = _flat(e.get("recipient"))
    parts = []
    if reg in ("chat", "email", "review") and who:
        parts.append("to %s" % who)
    elif reg == "post" and who:
        parts.append(who)
    parts.append(date)
    subj = _flat(e.get("subject"))
    if reg == "email" and subj:
        parts.append("subject: %s" % subj[:SUBJECT_MAX])
    return "### " + ", ".join(parts)


def render_entry(e, register=None, body_label=None, body=None):
    """One entry in the pilot's format: header, 'Replying to: <context>', 'My <kind>:', the text."""
    reg = register or e.get("register") or "chat"
    lab, none = CONTEXT_LINE.get(reg, CONTEXT_LINE["chat"])
    ctx = _flat(e.get("context"))
    if body_label is None:
        body_label = "My %s:" % _kind(reg)
    text = (e.get("text") or "").strip() if body is None else body
    return "%s\n%s: %s\n%s\n%s" % (entry_header(e, reg), lab, ctx or none, body_label, text)


def render_task(task, register):
    """The new entry with the notes in place of the body (the pilot's user prompt). `task` is a dict:
    notes, context, recipient, subject, timestamp (default: blank, so 'undated'; the CLI passes today)."""
    t = dict(task or {})
    label = "%s %s\nMy %s:" % (NOTES_LABEL, (t.get("notes") or "").strip(), _kind(register))
    return render_entry(t, register, body_label=label, body="").rstrip()


def entry_tokens(e, register=None):
    """Prompt cost of one rendered entry plus its separator, in tokens, rounded up."""
    return est_tokens(len(render_entry(e, register)) + len(SEP))


def person_block_text(person_text):
    """The person block exactly as render_context inserts it ('' for no person text)."""
    if not (person_text or "").strip():
        return ""
    _, block, _ = compose.context_template()
    return compose._fill_once(block, {"person_text": person_text.strip()}) + SEP


def person_tokens(person_text):
    t = person_block_text(person_text)
    return est_tokens(len(t)) if t else 0


def learned_block_text(learned_text):
    """The learned-notes block exactly as render_context inserts it ('' for no notes): `learned_text`
    is compose.learned_notes() for this draft."""
    t = compose.learned_block(learned_text)
    return "\n\n" + t if t else ""


def learned_tokens(learned_text):
    t = learned_block_text(learned_text)
    return est_tokens(len(t)) if t else 0


def habits_block_text(habits_text):
    """The "How my messages vary" block exactly as render_context inserts it ('' for none):
    `habits_text` is habits.habits_text() for the draft's register."""
    t = compose.habits_block(habits_text)
    return "\n\n" + t if t else ""


def habits_tokens(habits_text):
    t = habits_block_text(habits_text)
    return est_tokens(len(t)) if t else 0


# ----------------------------------------------------------------------------- leak rule

def _as_list(x):
    if not x:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def leaks(e, item, hours=LEAK_HOURS):
    """True if `e` is `item` itself, or is in the same thread within `hours` of it (hours=None: anywhere
    in the thread). Same thread with a missing timestamp counts as a leak (conservative)."""
    if not item:
        return False
    if e.get("id") and e.get("id") == item.get("id"):
        return True
    th = e.get("thread")
    if not th or th != item.get("thread"):
        return False
    if hours is None:
        return True
    a, b = parse_iso(e.get("timestamp")), parse_iso(item.get("timestamp"))
    if not a or not b:
        return True
    return abs((a - b).total_seconds()) <= hours * 3600


def eligible(entries, request=None):
    """Entries left after the request's exclude_ids and leak rule, in their given order."""
    r = request or {}
    excl = set(r.get("exclude_ids") or ())
    held = _as_list(r.get("heldout"))
    hours = r.get("leak_hours", LEAK_HOURS)
    return [e for e in entries
            if e.get("id") not in excl and not any(leaks(e, h, hours) for h in held)]


# ----------------------------------------------------------------------------- helpers

def by_date(es):
    return sorted(es, key=date_key)


def _fill(order, cost, budget):
    """Greedy fill of entry indices in `order`; an entry that would overflow is skipped."""
    out, used = [], 0
    for i in order:
        if used + cost[i] <= budget:
            out.append(i)
            used += cost[i]
    return out


def _costs(entries):
    return [entry_tokens(e) for e in entries]


def total_tokens(entries):
    return sum(entry_tokens(e) for e in entries)


def _words(e):
    return e.get("word_count") or len((e.get("text") or "").split())


def where(e):
    """D's first stratum axis: where it went (subreddit, recipient, project, file)."""
    if e.get("register") == "prompt":
        return e.get("project") or ""
    return e.get("recipient") or e.get("thread") or ""


def terciles(entries):
    w = sorted(_words(e) for e in entries)
    if not w:
        return 0, 0
    return w[len(w) // 3], w[2 * len(w) // 3]


def stratum(e, cuts):
    lo, hi = cuts
    n = _words(e)
    t = 0 if n < lo else (1 if n < hi else 2)
    return (where(e), t, (e.get("timestamp") or "")[:4])


def _pick(entries, idx):
    return by_date([entries[i] for i in idx])


# ----------------------------------------------------------------------------- strategies

def select_R(entries, budget_tokens, request=None, seed=0):
    es = eligible(entries, request)
    order = list(range(len(es)))
    random.Random(seed).shuffle(order)
    return _pick(es, _fill(order, _costs(es), budget_tokens))


def select_T(entries, budget_tokens, request=None, seed=0):
    es = eligible(entries, request)
    order = sorted(range(len(es)), key=lambda i: date_key(es[i]), reverse=True)
    return _pick(es, _fill(order, _costs(es), budget_tokens))


def _stratified(es, cost, budget, seed):
    """Indices D picks. The next pick comes from the stratum with the lowest taken/size share
    (proportional quotas), ties to the larger stratum, then a seeded coin; newest first inside a
    stratum, skipping an entry that does not fit."""
    cuts = terciles(es)
    groups = {}
    for i, e in enumerate(es):
        groups.setdefault(stratum(e, cuts), []).append(i)
    rng = random.Random(seed)
    heap = []
    for k in sorted(groups):          # sorted so the seeded coins land on the same strata every run
        ix = groups[k]
        ix.sort(key=lambda i: date_key(es[i]), reverse=True)
        heap.append((0.0, -len(ix), rng.random(), k))
    heapq.heapify(heap)
    ptr, taken = dict.fromkeys(groups, 0), dict.fromkeys(groups, 0)
    out, used = [], 0
    while heap:
        _, negsz, tie, k = heapq.heappop(heap)
        ix = groups[k]
        while ptr[k] < len(ix) and used + cost[ix[ptr[k]]] > budget:
            ptr[k] += 1
        if ptr[k] >= len(ix):
            continue
        i = ix[ptr[k]]
        ptr[k] += 1
        out.append(i)
        used += cost[i]
        taken[k] += 1
        if ptr[k] < len(ix):
            heapq.heappush(heap, (taken[k] / float(len(ix)), negsz, tie, k))
    return out


def select_D(entries, budget_tokens, request=None, seed=0):
    es = eligible(entries, request)
    return _pick(es, _stratified(es, _costs(es), budget_tokens, seed))


def select_M(entries, budget_tokens, request=None, seed=0):
    """Top cosine (TF-IDF) of the request's context + notes + subject against each entry's context +
    text; entries with no overlap at all are left to D for the remaining budget. With no entries, an
    empty query or nothing matching, this is D."""
    r = request or {}
    es = eligible(entries, r)
    query = " ".join((r.get(k) or "") for k in ("context", "notes", "subject")).strip()
    if not es or not query:
        return select_D(es, budget_tokens, None, seed)
    vec, V = tfidf_index([(e.get("context") or "") + " " + (e.get("text") or "") for e in es])
    q = vec(query)
    sims = [cosine(q, v) for v in V]
    matched = sorted((i for i in range(len(es)) if sims[i] > 0), key=lambda i: (-sims[i], es[i].get("id") or ""))
    if not matched:
        return select_D(es, budget_tokens, None, seed)
    cost = _costs(es)
    top = _fill(matched, cost, budget_tokens)
    left = budget_tokens - sum(cost[i] for i in top)
    rest = [es[i] for i in range(len(es)) if sims[i] <= 0]
    return by_date([es[i] for i in top] + select_D(rest, left, None, seed))


def compact(e, n=COMPACT_CONTEXT_WORDS):
    """A copy of `e` with its context cut to `n` words (' ...' when cut)."""
    w = (e.get("context") or "").split()
    c = dict(e)
    c["context"] = " ".join(w[:n]) + (" ..." if len(w) > n else "")
    c["compact"] = True
    return c


def select_C(entries, budget_tokens, request=None, seed=0):
    """D over compact copies; the returned entries are the compact copies (render them as given)."""
    es = [e if e.get("compact") else compact(e) for e in eligible(entries, request)]
    return _pick(es, _stratified(es, _costs(es), budget_tokens, seed))


def select_P(entries, budget_tokens, request=None, seed=0):
    """D over what the person file (request['person_text']), the learned notes
    (request['learned_text']) and the habits block (request['habits_text']) leave of the budget."""
    r = request or {}
    left = (budget_tokens - person_tokens(r.get("person_text")) - learned_tokens(r.get("learned_text"))
            - habits_tokens(r.get("habits_text")))
    return select_D(entries, max(0, left), request, seed)


STRATEGIES = {"R": select_R, "D": select_D, "M": select_M, "T": select_T, "C": select_C, "P": select_P}


def select(entries, budget_tokens, request=None, seed=0, strategy="D"):
    """Run one strategy by its letter (case-insensitive)."""
    s = (strategy or "D").upper()
    if s not in STRATEGIES:
        raise ValueError("unknown strategy %r (one of %s)" % (strategy, ", ".join(STRATEGIES)))
    if budget_tokens <= 0:
        return []
    return STRATEGIES[s](entries, budget_tokens, request, seed)


# ----------------------------------------------------------------------------- assembly

def render_entries(entries, register):
    return SEP.join(render_entry(e, register) for e in by_date(entries))


def assemble(person_text, entries, register, task, complete=False, learned_text="", habits_text=""):
    """(system_file_text, task_text). `entries` are rendered in date order through
    compose.render_context (templates/context.md); `task` is a dict for render_task, or text already
    rendered; `person_text` is the person file tier, or '' for none; `learned_text` the learned.md
    notes for this draft (compose.learned_notes), or '' for none, rendered as a short "notes from my
    edits" block after the archive (`learned_tokens` is its cost; callers take it off the budget);
    `habits_text` the register's measured habits (habits.habits_text), or '' for none, rendered after
    the notes as a "How my messages vary" block (`habits_tokens`, also taken off the budget).
    `complete` says the entries are the whole archive ('the complete archive of ...' rather than
    'a selection of ...')."""
    years = [(e.get("timestamp") or "")[:4] for e in entries if (e.get("timestamp") or "")[:4]]
    first, last = (min(years), max(years)) if years else ("", "")
    task_text = task if isinstance(task, str) else render_task(task, register)
    return compose.render_context(register, render_entries(entries, register), task_text,
                                  person_text=person_text or "", first=first, last=last, complete=complete,
                                  learned_text=learned_text or "", habits_text=habits_text or "")
