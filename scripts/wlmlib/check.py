"""voice-check: does the pack actually sound like you?

  prepare   hold out real messages from the archive (never shown to the drafter): `--n` for the
            paired section of the page and `--lineups` more for the lineup section
  generate  a content-only brief of each, then
              voice  the write-as-me pipeline: select archive entries within the budget (the item,
                     every other held-out item, its thread within 48 h and near-duplicates
                     excluded) -> assemble the context file -> one `claude -p` draft (draft.py)
              plain  the same task with no voice pack (baseline.md)
  page      self-contained blind-test HTML in two parts: pairs (pick your real message) and
            lineups (four messages, three yours, pick the one you didn't write)
  score     stylometric closeness, the LLM judge (paired and lineup trials) and your picks

Two trial types, as in the research eval (docs/research.md):
  paired  your real held-out message beside a generated one: "which one did you write?". Reported
          as a fooling rate; 50% = indistinguishable, 0% = always caught.
  lineup  one generated message among three of your real messages of the same register, each
          within 0.75-1.33x the length of the item's real message (widened to the 12 nearest when
          fewer than 6 qualify), from three different threads, never the item's own message or
          thread: "which one didn't you write?". Reported as a spotted rate; 25% = chance. This is
          how a recipient meets a message (no real one beside it), and it stops rewarding the
          typo contest of the paired test.
Every reason the judge gives for a catch (and every note you type on the page) is tagged
polish-type (typos, capitals, apostrophes, "too polished") or voice-type (everything else); the
voice-type catch rate is the one that says something the pack can fix.

On the page, paired and lineup items are different held-out messages: a message seen in a pair
would otherwise give away the fake in its lineup. The judge sees every item in both trial types,
in separate prompts.
"""
from .build import PERSON_DEFAULT
import base64
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from . import draft as D
from . import habits as H
from . import llm
from . import select as S
from .build import HABITS_CHOICES, HABITS_DEFAULT, defaults, habits_default
from .cli_context import fit_budget, fit_habits
from .common import (REGISTERS, now_iso, pack_dir, private_open, read_json,
                     read_jsonl, short_hash, write_json, write_jsonl)
from .compose import KIND, TEMPLATES, learned_notes, render_task, template
from .lint import lint
from .stylo import features
from .textutil import ngrams

HABITS = ["len_median", "len_mean", "share_msgs_ack_opener", "share_msgs_greeting", "share_msgs_signoff",
          "share_msgs_exclaim", "share_msgs_question", "per1kw_hedges", "share_msgs_ellipsis",
          "share_msgs_dash", "share_msgs_spaced_hyphen", "share_msgs_spaced_slash", "share_msgs_parentheses",
          "share_msgs_semicolon", "share_msgs_emoji", "share_msgs_bullets", "share_msgs_line_break",
          "share_sentences_lowercase_start", "share_end_none", "share_uncontracted",
          "share_contractions_missing_apostrophe", "share_msgs_closer_offer"]
SEED = 20261106
LABELS = "ABCD"
PAIRED_CHANCE = 0.5
LINEUP_CHANCE = 0.25
LEN_LO, LEN_HI = 0.75, 4 / 3.0     # filler length window, as a multiple of the item's real message
NEAR_MIN, NEAR_WIDEN = 6, 12        # fewer than NEAR_MIN in the window: take the NEAR_WIDEN nearest
JUDGE_REPS = 2                      # judge lineups per (item, condition), the fake in different positions
CTX_CHARS = 300


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / float(n)
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


# ------------------------------------------------------------------ reason tagging

# Ported from the research eval's list (eval/judge.py POLISH_TERMS, from the moonshot RUN_NOTES):
# typos and misspelling, doubled words, apostrophes and quote style, capitals, grammar slips, stray
# spacing, and the polish axis itself. A reason matching any of them rewards mistakes and roughness
# the plugin never produces by design. Every other stated reason (content, detail, length,
# structure, word choice, hedging, openers) is voice-type.
POLISH_TERMS = [r"typo", r"misspell", r"spelling", r"doubled?\s+(?:word|[“\"'‘])",
                r"(?:repeated|duplicated?)\s+(?:words?\b|[“\"'‘]\w+[”\"'’])",
                r"[“\"'‘](\w+)\s+\1[”\"'’]", r"apostrophe", r"curly", r"quote style",
                r"lower[- ]?case", r"capitali[sz]", r"polish", r"\bun-?edited", r"\bedited", r"smooth",
                r"\btid(?:y|ied|ier)", r"awkward", r"grammar", r"grammatical", r"\bslips?\b", r"\berrors?\b",
                r"\bmistakes?\b", r"missing (?:comma|apostrophe|possessive|period|word|punctuation)",
                r"dropped word", r"(?:double|extra|trailing)[- ]spac", r"whitespace", r"escaped"]
POLISH_RE = re.compile(r"(?i)(?:" + "|".join(POLISH_TERMS) + r")")


def tag_reason(reason):
    """'polish' | 'voice' | None (no reason given)."""
    reason = reason.strip() if isinstance(reason, str) else str(reason or "").strip()
    if not reason:
        return None
    return "polish" if POLISH_RE.search(reason) else "voice"


def reasons(caught, n):
    """Polish/voice split of the reasons on the catches, and the voice-type catch rate over all n trials."""
    tags = [r.get("tag") if "tag" in r else tag_reason(r.get("tell")) for r in caught]
    v = tags.count("voice")
    lo, hi = wilson(v, n)
    return {"catches": len(caught), "polish": tags.count("polish"), "voice": v, "untagged": tags.count(None),
            "voice_catch_rate": v / float(n) if n else float("nan"), "voice_catch_ci": [lo, hi]}


# ------------------------------------------------------------------ prepare

def _words(r):
    return r.get("word_count") or len((r.get("text") or "").split())


def select_items(bank, stats, register, n=12, seed=SEED, lineups=0):
    """Hold out n + lineups messages of `register` from the archive: typical length (the register's
    p10-p90), replies first, at most two per thread, none within 48 h in the thread of another. The
    first n (in pick order, alternating) are section "paired", the others "lineup"."""
    reg = (stats.get("registers") or {}).get(register) or {}
    F = reg.get("features") or {}
    lo = max(4, int(F.get("len_p10", 4)))
    hi = max(lo + 4, int(math.ceil(F.get("len_p90", 60))))
    cand = [r for r in bank if r.get("register") == register and r.get("id") and lo <= _words(r) <= hi]
    rng = random.Random(seed)
    rng.shuffle(cand)
    cand.sort(key=lambda r: 0 if r.get("context") else 1)   # prefer replies (have context)
    want = n + max(0, lineups)
    picked, threads = [], {}
    for r in cand:
        t = r.get("thread") or r["id"]
        if threads.get(t, 0) >= 2:
            continue
        if any(S.leaks(r, p) or S.leaks(p, r) for p in picked):
            continue
        picked.append(r)
        threads[t] = threads.get(t, 0) + 1
        if len(picked) >= want:
            break
    # alternate sections so both spread over threads and lengths; when fewer than n + lineups were
    # found, the paired section fills first
    m = max(0, min(lineups, len(picked) - n))
    lin = set(range(1, len(picked), 2)[:m])
    k = len(picked) - 1
    while len(lin) < m:
        lin.add(k)
        k -= 1
    out = []
    for k, r in enumerate(picked):
        r = dict(r)
        r["section"] = "lineup" if k in lin else "paired"
        out.append(r)
    return out


def pool_for(bank, register, items, hours=S.LEAK_HOURS):
    """The register's archive minus the held-out items and anything in their threads within `hours`."""
    held = {i["id"] for i in items}
    return [r for r in bank if r.get("register") == register and r.get("id") not in held
            and not any(S.leaks(r, i, hours) for i in items)]


def lineup_capacity(pool, items):
    """How many held-out items could get a lineup (three pool messages from three other threads)."""
    threads = {}
    for r in pool:
        threads.setdefault(r.get("thread") or r["id"], 0)
    return sum(1 for it in items if len([t for t in threads if t != (it.get("thread") or it["id"])]) >= 3)


def call_estimate(n_items, judge="both", conditions=2, batch_size=6):
    """Model calls: brief + voice draft + plain draft per item, plus judge batches."""
    calls = 3 * n_items
    if judge in ("paired", "both"):
        calls += int(math.ceil(2.0 * n_items * conditions / batch_size))
    if judge in ("lineup", "both"):
        calls += int(math.ceil(float(JUDGE_REPS) * n_items * conditions / batch_size))
    return calls


# ------------------------------------------------------------------ generate

def parse_brief(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        brief = json.loads(m.group(0))["brief"] if m else raw
    except (ValueError, KeyError, TypeError):
        brief = raw
    return (brief or "").strip().rstrip(".").lower()


def near_duplicates(entries, item):
    """Ids of entries sharing a 6-word run with the item's text (quoted or re-sent wording)."""
    h = ngrams(item.get("text") or "")
    if not h:
        return set()
    return {e["id"] for e in entries if e.get("id") and ngrams(e.get("text") or "") & h}


def voice_context(entries, item, items, brief, register, budget, strategy, person_text="", seed=0,
                  today=None, learned_text="", habits_text=""):
    """(system text, task text, selected entries) for one held-out item: the leak rule around the item,
    every other held-out item and near-duplicates excluded, then select -> assemble. `learned_text` is
    the learned notes for the item's recipient (compose.learned_notes) and `habits_text` the habits
    block (heldout_habits); like the person file, their cost comes off the budget."""
    excl = {i["id"] for i in items} | near_duplicates(entries, item)
    habits_text, _ = fit_habits(budget, learned_text, habits_text)
    h_tok = S.habits_tokens(habits_text)
    budget -= h_tok
    person_text, _ = fit_budget(budget, person_text, learned_text)
    request = {"context": item.get("context") or "", "notes": brief, "subject": item.get("subject") or "",
               "recipient": item.get("recipient") or "", "heldout": item, "leak_hours": S.LEAK_HOURS,
               "exclude_ids": sorted(excl)}
    if strategy == "P":   # select_P takes person and notes off what is left after the habits block
        picked = S.select(entries, budget, dict(request, person_text=person_text, learned_text=learned_text),
                          seed, "P")
    else:
        picked = S.select(entries, budget - S.person_tokens(person_text) - S.learned_tokens(learned_text),
                          request, seed, strategy)
    task = {"notes": brief, "context": item.get("context") or "", "recipient": item.get("recipient") or "",
            "subject": item.get("subject") or "", "timestamp": today or now_iso()[:10]}
    system, task_text = S.assemble(person_text, picked, register, task, learned_text=learned_text,
                                   habits_text=habits_text)
    return system, task_text, picked


def heldout_habits(entries, items, register, stats, rules_text=None):
    """The habits block's text as `wlm context` would send it, measured on the register's archive minus
    the held-out items, so no held-out message moves a number its own draft sees."""
    held = {i["id"] for i in items}
    F = features([e.get("text") or "" for e in entries if e.get("id") not in held])
    return "\n".join(H.habit_lines(F, register, H.hard_marks(stats, register, rules_text)))


def heldout_person(pack, tier, stats, held_ids):
    """(person text, files used, sentences removed): the person file tier as `wlm context` sends it,
    minus every sentence that cites a held-out message (person.citations.json), so a claim distilled
    from a held-out message cannot reach its draft. A file with no citations (hand-written) is used
    as it is."""
    from . import person as P
    from .cli_context import PERSON_FILES, person_tier
    if tier == "none":
        return "", [], 0
    sc = P.load_sidecar(pack)
    out, used, removed = [], [], 0
    for n in [os.path.basename(x) for x in ((stats or {}).get("person_files") or PERSON_FILES)]:
        path = os.path.join(pack, n)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        recs = [r for t in ("core", "extension") for r in ((sc["files"].get(n) or {}).get("sentences") or {}).get(t, [])]
        drop = [r["sentence"] for r in recs if held_ids & set(r.get("ids") or [])]
        if drop:
            text = P._remove_sentences(text, drop)
            removed += len(drop)
        t = person_tier(text, tier)
        if t:
            out.append(t)
            used.append(n)
    return "\n\n".join(out), used, removed


def generate(run_dir, bank, stats, rules_text="", learned_text=None, model=None, workers=4, log=print,
             pack=None, budget=None, strategy=None, person=None, seed=0, habits=None):
    """Generate the voice and plain versions of every held-out item; writes generations.jsonl.
    The voice context is what `wlm context` writes: archive selection, person file and the learned
    notes for each item's recipient (`learned_text` is learned.md's text; None reads the pack's).
    Budget / strategy / person / habits default to the pack's stats.json (habits: build.HABITS_DEFAULT)."""
    pack = pack or os.path.dirname(os.path.dirname(os.path.abspath(run_dir)))
    meta = read_json(os.path.join(run_dir, "meta.json"), {}) or {}
    register = meta["register"]
    items = read_jsonl(os.path.join(run_dir, "items.jsonl"))
    d_budget, d_strategy = defaults(stats)
    budget = int(budget if budget is not None else d_budget)
    strategy = (strategy or d_strategy).upper()
    if strategy not in S.STRATEGIES:
        raise ValueError("unknown strategy %r" % strategy)
    person = person or stats.get("person_default") or PERSON_DEFAULT
    person_text, person_used, person_dropped = heldout_person(pack, person, stats, {i["id"] for i in items})
    if learned_text is None:
        lp = os.path.join(pack, "learned.md")
        learned_text = ""
        if os.path.isfile(lp):
            with open(lp, encoding="utf-8") as f:
                learned_text = f.read()
    entries = [e for e in bank if e.get("register") == register]
    habits = habits_default(stats, habits)
    habits_text = heldout_habits(entries, items, register, stats, rules_text or None) if habits == "on" else ""
    kind = KIND[register][0]
    cache = os.path.join(run_dir, "calls")
    bsys, buser = template("brief.md")
    plsys, pluser = template("baseline.md")
    today = now_iso()[:10]

    def job(it):
        raw = llm.call(bsys, llm.fill(buser, kind=kind, context=it.get("context") or "(none)", text=it["text"]),
                       cache_dir=cache, model=model)
        brief = parse_brief(raw)
        learned = learned_notes(learned_text, register, it.get("recipient") or "")
        system, task_text, picked = voice_context(entries, it, items, brief, register, budget, strategy,
                                                  person_text, seed, today, learned_text=learned,
                                                  habits_text=habits_text)
        d = os.path.join(run_dir, "ctx", short_hash(it["id"], n=12))
        os.makedirs(d, mode=0o700, exist_ok=True)
        sp = os.path.join(d, "system.txt")
        with private_open(sp) as f:
            f.write(system)
        voice = D.draft(sp, task_text, model=model, cache_dir=cache)
        lr = lint(voice, register, rules_text, stats)
        plain_task = render_task(it.get("context") or "", brief, it.get("recipient") or "", register,
                                 it.get("subject") or "")
        plain = llm.call(plsys, llm.fill(pluser, task=plain_task, kind=kind), cache_dir=cache, model=model)
        a_tok = S.total_tokens(picked)
        h_text = fit_habits(budget, learned, habits_text)[0]
        h_tok = S.habits_tokens(h_text)
        return [{"item_id": it["id"], "condition": "voice", "text": voice, "brief": brief, "entries": len(picked),
                 "archive_tokens": a_tok, "habits_tokens": h_tok,
                 "tokens": (a_tok + S.person_tokens(fit_budget(budget - h_tok, person_text, learned)[0])
                            + S.learned_tokens(learned) + h_tok),
                 "lint_fix": sorted({f["code"] for f in lr["findings"] if f["level"] == "fix"})},
                {"item_id": it["id"], "condition": "plain", "text": plain, "brief": brief}]

    rows = []
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for k, res in enumerate(ex.map(job, items), 1):
            rows += res
            if log:
                log("  generated %d/%d" % (k, len(items)))
    write_jsonl(os.path.join(run_dir, "generations.jsonl"), rows)
    meta["generate"] = {"at": now_iso(), "budget": budget, "strategy": strategy,
                        "person": person if person_text else "none", "person_files": person_used,
                        "person_sentences_held_out": person_dropped, "habits": habits,
                        "habits_tokens": S.habits_tokens(habits_text),
                        "seed": seed, "model": model or os.environ.get("WRITE_LIKE_ME_MODEL") or "default"}
    write_json(os.path.join(run_dir, "meta.json"), meta)
    return rows


# ------------------------------------------------------------------ lineups

def make_lineups(items, gens, pool, condition="voice", reps=1, seed=SEED, avoid_reuse=False):
    """Lineups of one generated message plus three real fillers (the research eval's design).

    Fillers: from `pool` (the same register, held-out items already removed), never the item's own
    message, never from the item's thread, three different threads, word count within LEN_LO-LEN_HI
    of the item's real message (widened to the NEAR_WIDEN nearest when fewer than NEAR_MIN
    qualify). Reps put the fake in different positions (pos0 + 2*rep mod 4). Fillers this item
    already used, then fillers other lineups used most, are drawn last; with `avoid_reuse` (the
    page, where a message seen twice would be known to be real) a lineup that can only be filled
    by reusing a message is skipped. An item with no generation, or fewer than three usable
    threads, gets no lineup."""
    by = {g["item_id"]: g for g in gens if g["condition"] == condition}
    out, uses = [], {}
    for it in items:
        g = by.get(it["id"])
        if not g:
            continue
        key = "%s|%s" % (condition, it["id"])
        rng = random.Random("%s|%s" % (seed, key))
        target = max(1, _words(it))
        th = it.get("thread") or it["id"]
        elig = [r for r in pool if r.get("id") != it["id"] and (r.get("thread") or r.get("id")) != th
                and r.get("register", it.get("register")) == it.get("register") and _words(r) > 0]

        def dist(r):
            return abs(math.log(_words(r) / float(target)))
        near = [r for r in elig if LEN_LO <= _words(r) / float(target) <= LEN_HI]
        if len(near) < NEAR_MIN:
            near = sorted(elig, key=lambda r: (dist(r), r["id"]))[:NEAR_WIDEN]
        near.sort(key=lambda r: r["id"])
        rng.shuffle(near)
        pos0 = rng.randrange(4)
        used = set()
        for rep in range(reps):
            fillers, threads = [], set()
            for r in sorted(near, key=lambda r: (r["id"] in used, uses.get(r["id"], 0))):
                if avoid_reuse and uses.get(r["id"]):
                    continue
                t = r.get("thread") or r["id"]
                if t not in threads:
                    fillers.append(r)
                    threads.add(t)
                if len(fillers) == 3:
                    break
            if len(fillers) < 3:
                continue
            used |= {r["id"] for r in fillers}
            for r in fillers:
                uses[r["id"]] = uses.get(r["id"], 0) + 1
            pos = (pos0 + 2 * rep) % 4
            cands = [{"text": r["text"], "context": r.get("context") or ""} for r in fillers]
            cands.insert(pos, {"text": g["text"], "context": it.get("context") or ""})
            lid = "l" + hashlib.sha256(("%s|%d" % (key, rep)).encode("utf-8")).hexdigest()[:8]
            out.append({"lid": lid, "item": it["id"], "condition": condition, "rep": rep, "item_thread": th,
                        "cands": cands, "fake": LABELS[pos], "filler_ids": [r["id"] for r in fillers],
                        "filler_threads": [r.get("thread") or r["id"] for r in fillers]})
    return out


# ------------------------------------------------------------------ LLM judge

TRIAL = """<trial id="{id}">
<replying_to>{ctx}</replying_to>
<summary>{brief}</summary>
<candidate_A>{a}</candidate_A>
<candidate_B>{b}</candidate_B>
</trial>"""

LINEUP_CAND = """<candidate_{L}>
<replying_to>{ctx}</replying_to>
<message>{text}</message>
</candidate_{L}>"""


def _esc(s):
    return (s or "").replace("<", "‹").replace(">", "›").strip()


def _fill(tmpl, **kw):
    """One-pass {key} fill, so substituted text (real messages may hold braces) is never re-searched."""
    return re.sub(r"\{(%s)\}" % "|".join(map(re.escape, kw)), lambda m: str(kw[m.group(1)]), tmpl)


def judge_templates():
    """(system, paired user, lineup user) from templates/judge.md."""
    with open(os.path.join(TEMPLATES, "judge.md"), encoding="utf-8") as f:
        t = f.read()
    head, lineup = t.split("=== LINEUP ===", 1)
    sys_part, user = head.split("=== USER ===", 1)
    return sys_part.replace("=== SYSTEM ===", "", 1).strip(), user.strip(), lineup.strip()


def make_trials(items, gens, condition):
    by = {g["item_id"]: g for g in gens if g["condition"] == condition}
    trials = []
    for it in items:
        g = by.get(it["id"])
        if not g:
            continue
        for order in ("real_first", "fake_first"):
            a, b = (it["text"], g["text"]) if order == "real_first" else (g["text"], it["text"])
            trials.append({"kind": "paired", "id": "%s|%s|%s" % (condition, it["id"], order), "item": it["id"],
                           "condition": condition, "a": a, "b": b, "real": "A" if order == "real_first" else "B",
                           "ctx": it.get("context", ""), "brief": g.get("brief", ""),
                           "threads": [it.get("thread") or it["id"]], "keys": [it["id"]]})
    return trials


def lineup_trials(lineups):
    return [{"kind": "lineup", "id": "%s|%s" % (l["condition"], l["lid"]), "item": l["item"],
             "condition": l["condition"], "cands": l["cands"], "fake": l["fake"],
             "threads": [l["item_thread"]] + l["filler_threads"], "keys": [l["item"]] + l["filler_ids"]}
            for l in lineups]


def _keys(t):
    return set(t.get("keys") or [t["item"]])


def _first_fit(trials, size, seed):
    rng = random.Random(seed)
    t = list(trials)
    rng.shuffle(t)
    out = []
    for tr in t:
        k = _keys(tr)
        for b in out:
            if len(b) < size and all(not (_keys(x) & k) for x in b):
                b.append(tr)
                break
        else:
            out.append([tr])
    return out


def batches(trials, size, seed=SEED):
    """A batch never shows a message twice: both orders of an item never share one, and lineups
    sharing an item or a filler never share one. Lineups take the tightest of 20 shuffles."""
    if any(t.get("kind") == "lineup" for t in trials):
        return min((_first_fit(trials, size, seed + k) for k in range(20)), key=len)
    return _first_fit(trials, size, seed)


def judge_refs(pool, batch, n_refs, seed):
    """Reference messages: pool messages of 4-120 words outside every thread the batch touches and
    never a message the batch shows."""
    threads = {t for tr in batch for t in tr["threads"]}
    shown = {k for tr in batch for k in _keys(tr)}
    cands = [r for r in pool if 4 <= _words(r) <= 120 and (r.get("thread") or r["id"]) not in threads
             and r["id"] not in shown]
    cands.sort(key=lambda r: r["id"])
    return [r["text"] for r in random.Random(seed).sample(cands, min(n_refs, len(cands)))]


def judge_prompt(batch, refs, kinds):
    sysm, paired, lineup = judge_templates()
    ref_txt = "\n\n".join("[%d] %s" % (i + 1, _esc(r)) for i, r in enumerate(refs))
    if batch and batch[0]["kind"] == "lineup":
        trials = "\n\n".join(
            '<lineup id="%s">\n%s\n</lineup>' % (t["id"], "\n".join(
                LINEUP_CAND.format(L=LABELS[j], ctx=_esc(c.get("context"))[:CTX_CHARS] or "(none)",
                                   text=_esc(c["text"])) for j, c in enumerate(t["cands"])))
            for t in batch)
        return sysm, _fill(lineup, kinds=kinds, refs=ref_txt, n=len(batch), trials=trials)
    trials = "\n\n".join(TRIAL.format(id=t["id"], ctx=_esc(t["ctx"])[:CTX_CHARS] or "(none)", brief=_esc(t["brief"]),
                                      a=_esc(t["a"]), b=_esc(t["b"])) for t in batch)
    return sysm, _fill(paired, kinds=kinds, refs=ref_txt, n=len(batch), trials=trials)


def _parse_array(txt):
    m = re.search(r"\[.*\]", txt or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        return []
    return [a for a in arr if isinstance(a, dict) and "id" in a] if isinstance(arr, list) else []


def parse_judge(txt):
    return [a for a in _parse_array(txt) if a.get("real") in ("A", "B")]


def parse_lineup_judge(txt):
    out = []
    for a in _parse_array(txt):
        p = str(a.get("not_p") or a.get("not_P") or a.get("answer") or "").strip().upper()[:1]
        p = {"1": "A", "2": "B", "3": "C", "4": "D"}.get(p, p)
        if p and p in LABELS:
            out.append(dict(a, not_p=p))
    return out


def judge_row(t, a):
    """One scored trial (None if the judge gave no valid answer for it)."""
    if not a:
        return None
    if t["kind"] == "lineup":
        tell = a.get("reason") or a.get("tell") or ""
        spotted = a["not_p"] == t["fake"]
        return {"id": t["id"], "kind": "lineup", "condition": t["condition"], "item": t["item"],
                "pick": a["not_p"], "fake": t["fake"], "spotted": spotted, "fooled": not spotted,
                "confidence": a.get("confidence"), "tell": tell, "tag": tag_reason(tell)}
    tell = a.get("tell") or a.get("reason") or ""
    return {"id": t["id"], "kind": "paired", "condition": t["condition"], "item": t["item"],
            "fooled": a["real"] != t["real"], "confidence": a.get("confidence"), "tell": tell,
            "tag": tag_reason(tell)}


def judge_trials(items, gens, pool, mode="both", conditions=None):
    conds = conditions or sorted({g["condition"] for g in gens})
    trials = []
    for cond in conds:
        if mode in ("paired", "both"):
            trials += make_trials(items, gens, cond)
        if mode in ("lineup", "both"):
            trials += lineup_trials(make_lineups(items, gens, pool, cond, reps=JUDGE_REPS))
    return trials


def run_judge(run_dir, items, gens, pool, register, model=None, batch_size=6, n_refs=8, workers=3,
              mode="both", log=print):
    kinds = KIND[register][1]
    trials = judge_trials(items, gens, pool, mode)
    bs = []
    for kind in ("paired", "lineup"):
        bs += batches([t for t in trials if t["kind"] == kind], batch_size)
    cache = os.path.join(run_dir, "calls")

    def job(ib):
        i, b = ib
        s, u = judge_prompt(b, judge_refs(pool, b, n_refs, SEED + i), kinds)
        raw = llm.call(s, u, cache_dir=cache, model=model)
        return b, (parse_lineup_judge(raw) if b[0]["kind"] == "lineup" else parse_judge(raw))

    results = []
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for b, ans in ex.map(job, enumerate(bs)):
            got = {a["id"]: a for a in ans}
            for t in b:
                row = judge_row(t, got.get(t["id"]))
                if row:
                    results.append(row)
    write_jsonl(os.path.join(run_dir, "judge.jsonl"), results)
    return results


def fooling(rows):
    """Paired: per condition, how often the generated message was taken for the real one."""
    out = {}
    for cond in sorted({r["condition"] for r in rows}):
        rr = [r for r in rows if r["condition"] == cond]
        k = sum(1 for r in rr if r["fooled"])
        lo, hi = wilson(k, len(rr))
        out[cond] = {"n": len(rr), "fooled": k, "rate": k / float(len(rr)) if rr else 0, "ci": [lo, hi],
                     "chance": PAIRED_CHANCE}
        if any("tell" in r for r in rr):
            out[cond]["reasons"] = reasons([r for r in rr if not r["fooled"]], len(rr))
    return out


def spotted(rows):
    """Lineup: per condition, how often the generated message was picked out of four (25% = chance)."""
    out = {}
    for cond in sorted({r["condition"] for r in rows}):
        rr = [r for r in rows if r["condition"] == cond]
        k = sum(1 for r in rr if r["spotted"])
        lo, hi = wilson(k, len(rr))
        out[cond] = {"n": len(rr), "spotted": k, "rate": k / float(len(rr)) if rr else 0, "ci": [lo, hi],
                     "chance": LINEUP_CHANCE}
        if any("pick" in r for r in rr):
            out[cond]["pick_positions"] = {L: sum(1 for r in rr if r.get("pick") == L) for L in LABELS}
        if any("tell" in r for r in rr):
            out[cond]["reasons"] = reasons([r for r in rr if r["spotted"]], len(rr))
    return out


def summarize_judge(rows):
    return {"paired": fooling([r for r in rows if r["kind"] == "paired"]),
            "lineup": spotted([r for r in rows if r["kind"] == "lineup"])}


# ------------------------------------------------------------------ stylometric closeness

def habit_z(texts, pool_texts, n_samples=200, seed=SEED):
    """Mean |z| of each habit of `texts` vs same-size random samples of the pool (lower = closer)."""
    rng = random.Random(seed)
    k = len(texts)
    if k == 0 or len(pool_texts) < k + 2:
        return None, {}
    samples = [features(rng.sample(pool_texts, k)) for _ in range(n_samples)]
    F = features(texts)
    zs = {}
    for h in HABITS:
        vals = [s.get(h, 0.0) for s in samples]
        mu = statistics.mean(vals)
        sd = statistics.pstdev(vals)
        floor = 0.1 if not h.startswith("len_") else 1.0
        floor = 0.01 if h.startswith("share_") else floor
        sd = max(sd, floor)
        zs[h] = max(-10.0, min(10.0, (F.get(h, 0.0) - mu) / sd))
    return statistics.mean(abs(z) for z in zs.values()), zs


def closeness(plain, voice, real):
    """0-1 stylometric closeness of the voice drafts: 0 = as far as plain Claude, 1 = as close as the
    real held-out messages. Only defined when plain Claude is further from the pool than the real
    messages (otherwise the scale has no direction). Returns (clamped, raw)."""
    if plain is None or voice is None or real is None or plain <= real:
        return None, None
    raw = (plain - voice) / (plain - real)
    return max(0.0, min(1.0, raw)), raw


# ------------------------------------------------------------------ score

def score(run_dir, bank, stats, model=None, judge=True, log=print, judge_mode="both", workers=3):
    meta = read_json(os.path.join(run_dir, "meta.json"), {}) or {}
    register = meta["register"]
    items = read_jsonl(os.path.join(run_dir, "items.jsonl"))
    gens = read_jsonl(os.path.join(run_dir, "generations.jsonl"))
    pool = pool_for(bank, register, items)
    pool_texts = [r["text"] for r in pool]
    res = {"scored": now_iso(), "register": register, "n_items": len(items), "pool": len(pool),
           "generate": meta.get("generate")}
    real_z, _ = habit_z([i["text"] for i in items], pool_texts)
    res["stylo"] = {"real_heldout": real_z}
    for cond in sorted({g["condition"] for g in gens}):
        z, zs = habit_z([g["text"] for g in gens if g["condition"] == cond], pool_texts)
        res["stylo"][cond] = z
        res.setdefault("habit_z", {})[cond] = {k: round(v, 2) for k, v in zs.items()}
    s = res["stylo"]
    res["stylo_closeness_voice"], res["stylo_closeness_voice_raw"] = closeness(s.get("plain"), s.get("voice"), real_z)
    if items:
        res["lengths"] = {"real_median": statistics.median(_words(i) for i in items)}
    for cond in ("voice", "plain"):
        L = [len(g["text"].split()) for g in gens if g["condition"] == cond]
        if L:
            res.setdefault("lengths", {})[cond + "_median"] = statistics.median(L)
    voice = [g for g in gens if g["condition"] == "voice"]
    if voice and any("lint_fix" in g for g in voice):
        res["lint"] = {"drafts": len(voice), "with_fix": sum(1 for g in voice if g.get("lint_fix"))}
    if judge:
        rows = run_judge(run_dir, items, gens, pool, register, model=model, mode=judge_mode, workers=workers,
                         log=log)
        res["judge"] = summarize_judge(rows)
    picks_p = os.path.join(run_dir, "picks.json")
    if os.path.exists(picks_p):
        try:
            res["human"] = score_human(read_json(picks_p), run_dir)
        except PicksMismatch as e:  # saved picks predate a rebuilt page
            if log:
                log("saved blind-test picks not used: %s" % e)
    write_json(os.path.join(run_dir, "scores.json"), res)
    return res


# ------------------------------------------------------------------ blind test page

def _b64(s):
    return base64.b64encode(s.encode()).decode()


def page_data(items, gens, conditions=("voice",), seed=SEED):
    """Pairs (real, generated) for `items`, real side random, in a random order. With several
    conditions each item goes to one of them in turn, so no real message is shown twice (a message
    seen in two pairs would be known to be the real one)."""
    rng = random.Random(seed)
    by_item = {i["id"]: i for i in items}
    conds = [c for c in conditions]
    assign = {i["id"]: conds[k % len(conds)] for k, i in enumerate(sorted(items, key=lambda i: i["id"]))}
    pairs = []
    for g in gens:
        if g["item_id"] not in by_item or g["condition"] != assign.get(g["item_id"]):
            continue
        it = by_item[g["item_id"]]
        real_first = rng.random() < 0.5
        pairs.append({"pid": "p%03d" % len(pairs), "item": it["id"], "condition": g["condition"],
                      "context": it.get("context", ""), "a": it["text"] if real_first else g["text"],
                      "b": g["text"] if real_first else it["text"], "k": _b64("A" if real_first else "B")})
    rng.shuffle(pairs)
    return pairs


def page_lineups(items, gens, pool, seed=SEED, tries=20):
    """Lineups for the page: the "lineup" section's items, voice only, one each, no message twice.
    Fillers are drawn greedily item by item, so the item order decides how many lineups fit a small
    pool; the first of `tries` seeded orders with the most lineups wins."""
    li = [i for i in items if i.get("section") == "lineup"]
    best = []
    for t in range(max(1, tries)):
        order = list(li)
        if t:
            random.Random(seed + t).shuffle(order)
        lin = make_lineups(order, gens, pool, "voice", reps=1, seed=seed, avoid_reuse=True)
        if len(lin) > len(best):
            best = lin
        if len(best) == len(li):
            break
    random.Random(seed + 1).shuffle(best)
    return [{"lid": "l%03d" % k, "item": l["item"], "condition": l["condition"],
             "cands": [{"text": c["text"], "context": c["context"]} for c in l["cands"]],
             "filler_ids": l["filler_ids"], "k": _b64(l["fake"])} for k, l in enumerate(best)]


def answer_fingerprint(answers, lineup_answers=None):
    """Short hash of a page's answer key (pairs and lineups). A rebuilt page (e.g. with
    --include-plain) reuses ids for different trials; the fingerprint ties an export to the page
    version it was taken on."""
    obj = answers if not lineup_answers else {"pairs": answers, "lineups": lineup_answers}
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def _write_private(path, text):
    with private_open(path) as f:
        f.write(text)


def build_page(run_dir, conditions=("voice",), bank=None):
    """(page path, number of pairs); see write_page."""
    info = write_page(run_dir, conditions, bank)
    return info["page"], info["pairs"]


def write_page(run_dir, conditions=("voice",), bank=None):
    """Write blind_test.html and answers.json. Returns {page, pairs, lineups}. Paired-section
    items make the pairs (all items when no item has a section), lineup-section items the lineups,
    with fillers from the pack's archive (`bank`, default: the pack holding the run)."""
    items = read_jsonl(os.path.join(run_dir, "items.jsonl"))
    gens = read_jsonl(os.path.join(run_dir, "generations.jsonl"))
    meta = read_json(os.path.join(run_dir, "meta.json"), {}) or {}
    paired_items = [i for i in items if i.get("section", "paired") == "paired"]
    pairs = page_data(paired_items, gens, conditions)
    lineups = []
    if any(i.get("section") == "lineup" for i in items):
        if bank is None:
            from .build import load_pack
            bank = load_pack(os.path.dirname(os.path.dirname(os.path.abspath(run_dir))))[1]
        register = meta.get("register") or (items[0].get("register") if items else "chat")
        lineups = page_lineups(items, gens, pool_for(bank, register, items))
    with open(os.path.join(TEMPLATES, "blind_test.html"), encoding="utf-8") as f:
        html = f.read()
    run_id = os.path.basename(os.path.normpath(run_dir))
    label = meta.get("label") or "Your real messages (from your voice pack)"
    # `h` hashes what the trial shows, so the fingerprint changes when the texts do (a regenerated run)
    answers = {p["pid"]: {"item": p["item"], "condition": p["condition"],
                          "real": base64.b64decode(p["k"]).decode(),
                          "h": short_hash(p["context"], p["a"], p["b"])} for p in pairs}
    lineup_answers = {l["lid"]: {"item": l["item"], "condition": l["condition"],
                                 "fake": base64.b64decode(l["k"]).decode(), "filler_ids": l["filler_ids"],
                                 "h": short_hash(*[c["context"] + "\x00" + c["text"] for c in l["cands"]])}
                      for l in lineups}
    fp = answer_fingerprint(answers, lineup_answers)
    page_lin = [{k: v for k, v in l.items() if k != "filler_ids"} for l in lineups]
    payload = json.dumps({"run": run_id, "key": fp, "label": label, "demo": bool(meta.get("demo")),
                          "pairs": pairs, "lineups": page_lin},
                         ensure_ascii=False).replace("<", "\\u003c")   # no "</script>" or "<!--" in the script
    html = html.replace("/*__DATA__*/null", payload)
    out = os.path.join(run_dir, "blind_test.html")
    _write_private(out, html)
    # the answer key, used by `score` instead of trusting the page's own "correct" fields
    _write_private(os.path.join(run_dir, "answers.json"),
                   json.dumps({"run": run_id, "key": fp, "answers": answers, "lineups": lineup_answers}))
    info = {"page": out, "pairs": len(pairs), "lineups": len(lineups)}
    n_li = sum(1 for i in items if i.get("section") == "lineup")
    if len(lineups) < n_li:
        info["note"] = ("%d of %d lineup items left off the page: too few of your other messages of a similar "
                        "length in other conversations to fill them without showing a message twice"
                        % (n_li - len(lineups), n_li))
    return info


class PicksMismatch(ValueError):
    pass


def check_picks(picks, run_dir):
    """The run's answer key, if `picks` (the page's export) was taken on this run's current page;
    raises PicksMismatch otherwise."""
    key = read_json(os.path.join(run_dir, "answers.json"))
    if not key:
        raise PicksMismatch("no answers.json in %s: run `check page` for this run first" % run_dir)
    if not isinstance(picks, dict) or picks.get("run") != key["run"]:
        raise PicksMismatch("these picks are from run %r, not %r"
                            % ((picks or {}).get("run") if isinstance(picks, dict) else None, key["run"]))
    if key.get("key") and picks.get("key") != key["key"]:
        raise PicksMismatch("these picks were made on an older version of this run's page (it has been rebuilt "
                            "since, so the trials differ): retake the test on the current page")
    return key


def score_human(picks, run_dir):
    """{"paired": fooling per condition, "lineup": spotted per condition, "notes": polish/voice tags of
    the notes typed on the catches}. Correctness comes from the run's answer key (answers.json); the
    export's own "correct" fields are ignored. Raises PicksMismatch for another run or page."""
    key = check_picks(picks, run_dir)
    prow, lrow, seen = [], [], set()
    for p in picks.get("picks") or []:
        a = (key.get("answers") or {}).get(p.get("pid"))
        if not a or p.get("pid") in seen or p.get("picked") not in ("A", "B"):
            continue
        seen.add(p["pid"])
        prow.append({"condition": a["condition"], "fooled": p["picked"] != a["real"], "tell": p.get("note") or ""})
    for p in picks.get("lineups") or []:
        a = (key.get("lineups") or {}).get(p.get("lid"))
        if not a or p.get("lid") in seen or p.get("picked") not in tuple(LABELS):
            continue
        seen.add(p["lid"])
        lrow.append({"condition": a["condition"], "spotted": p["picked"] == a["fake"], "pick": p["picked"],
                     "tell": p.get("note") or ""})
    for rows in (prow, lrow):
        for r in rows:
            r["tag"] = tag_reason(r["tell"])
    caught = [r for r in prow if not r["fooled"]] + [r for r in lrow if r["spotted"]]
    return {"paired": fooling(prow), "lineup": spotted(lrow), "notes": reasons(caught, len(prow) + len(lrow))}


def score_picks(picks, run_dir):
    """The paired part of score_human (per condition), for callers of the paired-only API."""
    return score_human(picks, run_dir)["paired"]


# ------------------------------------------------------------------ report

def _pct(x):
    return "%.0f%%" % (100 * x)


def _reason_line(d):
    r = d.get("reasons")
    if not r or not (r["voice"] or r["polish"]):
        return ""
    return "; reasons on catches: %d voice, %d polish (voice-type catch rate %s [%s-%s])" % (
        r["voice"], r["polish"], _pct(r["voice_catch_rate"]), _pct(r["voice_catch_ci"][0]),
        _pct(r["voice_catch_ci"][1]))


def _rate_lines(label, block):
    L = []
    for cond, d in sorted(((block or {}).get("paired") or {}).items()):
        L.append("  %s, pairs, %s: fooled %d/%d = %s [95%% CI %s-%s] (50%% = indistinguishable)%s"
                 % (label, cond, d["fooled"], d["n"], _pct(d["rate"]), _pct(d["ci"][0]), _pct(d["ci"][1]),
                    _reason_line(d)))
    for cond, d in sorted(((block or {}).get("lineup") or {}).items()):
        L.append("  %s, lineups, %s: spotted %d/%d = %s [95%% CI %s-%s] (25%% = chance)%s"
                 % (label, cond, d["spotted"], d["n"], _pct(d["rate"]), _pct(d["ci"][0]), _pct(d["ci"][1]),
                    _reason_line(d)))
    return L


def format_scores(res):
    L = ["voice-check (%s, %d held-out messages, pool %d)" % (res["register"], res["n_items"], res["pool"])]
    g = res.get("generate") or {}
    if g:
        L.append("  voice drafts: strategy %s, budget %s tokens, person file %s, habits block %s"
                 % (g.get("strategy"), g.get("budget"), g.get("person"), g.get("habits", "off")))
    s = res.get("stylo", {})
    if s.get("real_heldout") is not None:
        L.append("  stylometric distance (mean |z|, lower = closer): real held-out %.2f | voice %s | plain %s"
                 % (s["real_heldout"], "%.2f" % s["voice"] if s.get("voice") is not None else "-",
                    "%.2f" % s["plain"] if s.get("plain") is not None else "-"))
    if res.get("stylo_closeness_voice") is not None:
        L.append("  closeness: %.2f (raw %.2f; 0 = like plain Claude, 1 = like your real held-out messages)"
                 % (res["stylo_closeness_voice"], res["stylo_closeness_voice_raw"]))
    elif s.get("plain") is not None and s.get("voice") is not None:
        L.append("  closeness: n/a (plain Claude was not further from your habits than your real messages)")
    lg = res.get("lengths", {})
    if lg:
        L.append("  median words: real %s | voice %s | plain %s" % (lg.get("real_median"), lg.get("voice_median"),
                                                                     lg.get("plain_median")))
    if res.get("lint"):
        L.append("  hard-rule breaks (lint fix level): %d of %d voice drafts" % (res["lint"]["with_fix"],
                                                                                res["lint"]["drafts"]))
    L += _rate_lines("LLM judge", res.get("judge"))
    h = res.get("human")
    if h:
        L += _rate_lines("your blind test", h)
        n = h.get("notes") or {}
        if n.get("catches"):
            L.append("  your notes on catches: %d voice, %d polish, %d blank" % (n["voice"], n["polish"],
                                                                             n["untagged"]))
    return "\n".join(L)


# ------------------------------------------------------------------ CLI (`wlm check ...`)

def _latest_run(pack):
    base = os.path.join(pack, "check")
    runs = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
    if not runs:
        sys.exit("no check runs yet; run `check prepare` first")
    return os.path.join(base, runs[-1])


def cmd_check(a):
    from .build import load_pack
    pack = pack_dir()
    if not os.path.exists(os.path.join(pack, "stats.json")):
        sys.exit("No voice pack at %s. Run /write-like-me:voice-setup first." % pack)
    stats, bank, rules = load_pack(pack)
    if a.action == "prepare":
        if not any(e.get("register") == a.register for e in bank):
            sys.exit("no %s entries in the archive (a v1 pack? run `wlm build`)" % a.register)
        items = select_items(bank, stats, a.register, n=a.n, lineups=a.lineups)
        n_pairs = sum(1 for i in items if i["section"] == "paired")
        if n_pairs < 4:
            sys.exit("only %d usable %s messages; need more samples for a meaningful test" % (n_pairs, a.register))
        pool = pool_for(bank, a.register, items)
        notes = []
        n_lin = sum(1 for i in items if i["section"] == "lineup")
        if n_lin < a.lineups:
            notes.append("only %d of %d lineups: not enough usable messages" % (n_lin, a.lineups))
        cap = lineup_capacity(pool, [i for i in items if i["section"] == "lineup"])
        if cap < n_lin:
            notes.append("%d lineup item(s) have fewer than three other conversations to draw from and will be "
                         "left off the page" % (n_lin - cap))
        run = os.path.join(pack, "check", now_iso()[:19].replace(":", "") + "-" + a.register)
        os.makedirs(run, mode=0o700, exist_ok=True)
        write_jsonl(os.path.join(run, "items.jsonl"), items)
        demo = a.demo or any(i.get("source") == "synthetic" for i in items)
        label = ("synthetic fixture data, not anyone's real messages" if demo
                 else "your real messages from the voice pack at %s" % pack)
        write_json(os.path.join(run, "meta.json"), {"register": a.register, "created": now_iso(), "n": len(items),
                                                    "pairs": n_pairs, "lineups": n_lin, "demo": demo,
                                                    "label": label})
        print(json.dumps({"run": run, "items": len(items), "pairs": n_pairs, "lineups": n_lin, "pool": len(pool),
                          "data": ("DEMO: " if demo else "REAL: ") + label,
                          "model_calls_estimate": call_estimate(len(items)), "notes": notes}))
        return
    run = a.run or _latest_run(pack)
    if not os.path.exists(os.path.join(run, "items.jsonl")):
        sys.exit("no check run at %s" % run)
    if a.action == "generate":
        if not llm.available():
            sys.exit("the claude CLI is not on PATH")
        try:
            rows = generate(run, bank, stats, rules, model=a.model, workers=a.workers, budget=a.budget,
                            strategy=a.strategy, person=a.person, seed=a.seed, habits=a.habits, pack=pack,
                            log=lambda s: print(s, file=sys.stderr))
        except ValueError as e:
            sys.exit(str(e))
        g = (read_json(os.path.join(run, "meta.json"), {}) or {}).get("generate") or {}
        print(json.dumps({"run": run, "generations": len(rows), "strategy": g.get("strategy"),
                          "budget": g.get("budget"), "person": g.get("person"), "habits": g.get("habits")}))
    elif a.action == "page":
        info = write_page(run, conditions=("voice", "plain") if a.include_plain else ("voice",), bank=bank)
        out = info["page"]
        print(json.dumps(info))
        if a.open:
            if sys.platform == "darwin":
                subprocess.run(["open", out])
            else:
                import webbrowser
                if not webbrowser.open("file://" + os.path.abspath(out)):
                    print("could not open a browser; open this file manually: %s" % out)
    elif a.action == "score":
        if a.picks:
            picks = read_json(os.path.expanduser(a.picks))
            try:
                check_picks(picks, run)  # validate before copying and before any judge calls
            except PicksMismatch as e:
                sys.exit("picks not used: %s" % e)
            # write a private copy: shutil.copy would carry over the download's mode (often 0644)
            write_json(os.path.join(run, "picks.json"), picks)
        res = score(run, bank, stats, model=a.model, judge=not a.no_judge, judge_mode=a.judge_mode,
                    workers=a.workers, log=lambda s: print(s, file=sys.stderr))
        print(format_scores(res))


def add_parser(sp):
    p = sp.add_parser("check", help="blind test + scoring: pairs and lineups")
    p.add_argument("action", choices=["prepare", "generate", "page", "score"])
    p.add_argument("--register", default="chat", choices=REGISTERS)
    p.add_argument("--n", type=int, default=10, help="prepare: held-out messages for the paired section")
    p.add_argument("--lineups", type=int, default=10, help="prepare: more held-out messages for the lineup section")
    p.add_argument("--run", help="run dir (default: the latest)")
    p.add_argument("--budget", type=int,
                   help="generate: tokens of archive + person file + learned notes + habits block (default: stats.json)")
    p.add_argument("--strategy", type=str.upper, choices=sorted(S.STRATEGIES),
                   help="generate: selection strategy (default: stats.json)")
    p.add_argument("--person", choices=["core", "full", "none"],
                   help="generate: person file tier (default: stats.json, else core)")
    p.add_argument("--habits", choices=HABITS_CHOICES,
                   help="generate: the 'How my messages vary' block (default: stats.json habits_default, else %s)"
                        % HABITS_DEFAULT)
    p.add_argument("--seed", type=int, default=0, help="generate: selection seed")
    p.add_argument("--model")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--include-plain", action="store_true", help="page: also pair your messages with plain Claude")
    p.add_argument("--open", action="store_true")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--judge-mode", choices=["paired", "lineup", "both"], default="both")
    p.add_argument("--picks", help="score: exported picks JSON from the blind-test page")
    p.add_argument("--demo", action="store_true",
                   help="label the run as a demo (automatic when the items are synthetic fixtures)")
    p.set_defaults(fn=cmd_check)
    return p
