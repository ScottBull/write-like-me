"""voice-check: does the pack actually sound like you?

  prepare   hold out N real messages (never shown to the drafter)
  generate  content-only brief of each -> draft with the full pipeline ("voice") and with no
            voice pack ("plain") via `claude -p`
  page      self-contained blind-test HTML: pick your real message from each pair
  score     stylometric closeness + forced-choice LLM judge (+ your blind-test picks if imported)

Metrics follow run 1 of the research (docs/research.md): stylometric habit deltas against
same-size samples of your own messages, and a forced-choice "which one is real" judge reported as
a fooling rate (50% = indistinguishable, 0% = always caught). Single-message embedding scores were
weak in run 1 and are omitted.
"""
import base64
import hashlib
import json
import math
import os
import random
import re
import statistics
from concurrent.futures import ThreadPoolExecutor

from . import llm
from .common import now_iso, read_json, read_jsonl, write_json, write_jsonl
from .compose import KIND, build_kit, critique_prompt, revise_prompt, template
from .lint import lint
from .retrieve import leaks
from .stylo import features

HABITS = ["len_median", "len_mean", "share_msgs_ack_opener", "share_msgs_greeting", "share_msgs_signoff",
          "share_msgs_exclaim", "share_msgs_question", "per1kw_hedges", "share_msgs_ellipsis",
          "share_msgs_dash", "share_msgs_spaced_hyphen", "share_msgs_spaced_slash", "share_msgs_parentheses",
          "share_msgs_semicolon", "share_msgs_emoji", "share_msgs_bullets", "share_msgs_line_break",
          "share_sentences_lowercase_start", "share_end_none", "share_uncontracted",
          "share_contractions_missing_apostrophe", "share_msgs_closer_offer"]
SEED = 20261106


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / float(n)
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def select_items(bank, stats, register, n=12, seed=SEED):
    """Pick held-out messages: typical length, not fixed exemplars, spread over threads."""
    reg = (stats.get("registers") or {}).get(register) or {}
    F = reg.get("features") or {}
    fixed = set(reg.get("fixed_ids") or [])
    lo = max(4, int(F.get("len_p10", 4)))
    hi = max(lo + 4, int(math.ceil(F.get("len_p90", 60))))
    cand = [r for r in bank if r.get("register") == register and r["id"] not in fixed
            and lo <= r.get("word_count", 0) <= hi]
    rng = random.Random(seed)
    rng.shuffle(cand)
    cand.sort(key=lambda r: 0 if r.get("context") else 1)   # prefer replies (have context)
    picked, threads = [], {}
    for r in cand:
        t = r.get("thread") or r["id"]
        if threads.get(t, 0) >= 2:
            continue
        if any(leaks(r, p) for p in picked):
            continue
        picked.append(r)
        threads[t] = threads.get(t, 0) + 1
        if len(picked) >= n:
            break
    return picked


def pool_for(bank, register, items):
    held = {i["id"] for i in items}
    return [r for r in bank if r.get("register") == register and r["id"] not in held
            and not any(leaks(r, i) for i in items)]


def generate(run_dir, bank, stats, rules_text, learned_text, model=None, workers=4, log=print):
    meta = read_json(os.path.join(run_dir, "meta.json"))
    register = meta["register"]
    items = read_jsonl(os.path.join(run_dir, "items.jsonl"))
    held_ids = [i["id"] for i in items]
    kind = KIND[register][0]
    cache = os.path.join(run_dir, "calls")
    bsys, buser = template("brief.md")
    plsys, pluser = template("baseline.md")

    def job(it):
        raw = llm.call(bsys, llm.fill(buser, kind=kind, context=it.get("context") or "(none)", text=it["text"]),
                       cache_dir=cache, model=model)
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            brief = json.loads(m.group(0))["brief"] if m else raw
        except ValueError:
            brief = raw
        brief = brief.strip().rstrip(".").lower()
        kit = build_kit(bank, stats, rules_text, learned_text, register, brief, context=it.get("context", ""),
                        recipient=it.get("recipient", ""), subject=it.get("subject", ""),
                        exclude_ids=held_ids, heldout=it)
        draft = llm.call(kit["system"], kit["user"], cache_dir=cache, model=model)
        lr = lint(draft, register, rules_text, stats)
        csys, cuser = critique_prompt(kit, draft, stats)
        crit = llm.call(csys, cuser, cache_dir=cache, model=model)
        rsys, ruser = revise_prompt(kit, draft, crit, lr["findings"])
        final = llm.call(rsys, ruser, cache_dir=cache, model=model)
        plain = llm.call(plsys, llm.fill(pluser, task=kit["task"], kind=kind), cache_dir=cache, model=model)
        return [{"item_id": it["id"], "condition": "voice", "text": final, "draft": draft, "critique": crit,
                 "brief": brief},
                {"item_id": it["id"], "condition": "plain", "text": plain, "brief": brief}]

    rows = []
    with ThreadPoolExecutor(workers) as ex:
        for k, res in enumerate(ex.map(job, items), 1):
            rows += res
            log("  generated %d/%d" % (k, len(items)))
    write_jsonl(os.path.join(run_dir, "generations.jsonl"), rows)
    return rows


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


# ------------------------------------------------------------------ LLM judge

TRIAL = """<trial id="{id}">
<replying_to>{ctx}</replying_to>
<summary>{brief}</summary>
<candidate_A>{a}</candidate_A>
<candidate_B>{b}</candidate_B>
</trial>"""


def _esc(s):
    return (s or "").replace("<", "‹").replace(">", "›").strip()


def make_trials(items, gens, condition):
    by = {g["item_id"]: g for g in gens if g["condition"] == condition}
    trials = []
    for it in items:
        g = by.get(it["id"])
        if not g:
            continue
        for order in ("real_first", "fake_first"):
            a, b = (it["text"], g["text"]) if order == "real_first" else (g["text"], it["text"])
            trials.append({"id": "%s|%s|%s" % (condition, it["id"], order), "item": it["id"], "a": a, "b": b,
                           "real": "A" if order == "real_first" else "B", "ctx": it.get("context", ""),
                           "brief": g.get("brief", "")})
    return trials


def batches(trials, size, seed=SEED):
    """Both orders of an item never share a batch, and a batch never repeats a real message."""
    rng = random.Random(seed)
    t = list(trials)
    rng.shuffle(t)
    out = []
    for tr in t:
        for b in out:
            if len(b) < size and all(x["item"] != tr["item"] for x in b):
                b.append(tr)
                break
        else:
            out.append([tr])
    return out


def judge_prompt(batch, refs, kinds):
    sysm, user = template("judge.md")
    ref_txt = "\n\n".join("[%d] %s" % (i + 1, _esc(r)) for i, r in enumerate(refs))
    trials = "\n\n".join(TRIAL.format(id=t["id"], ctx=_esc(t["ctx"])[:300] or "(none)", brief=_esc(t["brief"]),
                                      a=_esc(t["a"]), b=_esc(t["b"])) for t in batch)
    return sysm, llm.fill(user, kinds=kinds, refs=ref_txt, n=len(batch), trials=trials)


def parse_judge(txt):
    m = re.search(r"\[.*\]", txt or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        return []
    return [a for a in arr if isinstance(a, dict) and "id" in a and a.get("real") in ("A", "B")]


def run_judge(run_dir, items, gens, pool, register, model=None, batch_size=6, n_refs=8, workers=3, log=print):
    kinds = KIND[register][1]
    rng = random.Random(SEED)
    all_trials = []
    for cond in sorted({g["condition"] for g in gens}):
        all_trials += make_trials(items, gens, cond)
    bs = batches(all_trials, batch_size)
    cache = os.path.join(run_dir, "calls")
    pool_texts = [r["text"] for r in pool if 4 <= r.get("word_count", 0) <= 120]

    def job(b):
        refs = rng.sample(pool_texts, min(n_refs, len(pool_texts)))
        s, u = judge_prompt(b, refs, kinds)
        return b, parse_judge(llm.call(s, u, cache_dir=cache, model=model))

    results = []
    with ThreadPoolExecutor(workers) as ex:
        for b, ans in ex.map(job, bs):
            got = {a["id"]: a for a in ans}
            for t in b:
                a = got.get(t["id"])
                if a:
                    results.append({"id": t["id"], "condition": t["id"].split("|")[0], "fooled": a["real"] != t["real"],
                                    "confidence": a.get("confidence"), "tell": a.get("tell", "")})
    write_jsonl(os.path.join(run_dir, "judge.jsonl"), results)
    return results


def fooling(rows):
    out = {}
    for cond in sorted({r["condition"] for r in rows}):
        rr = [r for r in rows if r["condition"] == cond]
        k = sum(1 for r in rr if r["fooled"])
        lo, hi = wilson(k, len(rr))
        out[cond] = {"n": len(rr), "fooled": k, "rate": k / float(len(rr)) if rr else 0, "ci": [lo, hi]}
    return out


def closeness(plain, voice, real):
    """0-1 stylometric closeness of the voice drafts: 0 = as far as plain Claude, 1 = as close as the
    real held-out messages. Only defined when plain Claude is further from the pool than the real
    messages (otherwise the scale has no direction). Returns (clamped, raw)."""
    if plain is None or voice is None or real is None or plain <= real:
        return None, None
    raw = (plain - voice) / (plain - real)
    return max(0.0, min(1.0, raw)), raw


def score(run_dir, bank, stats, model=None, judge=True, log=print):
    meta = read_json(os.path.join(run_dir, "meta.json"))
    register = meta["register"]
    items = read_jsonl(os.path.join(run_dir, "items.jsonl"))
    gens = read_jsonl(os.path.join(run_dir, "generations.jsonl"))
    pool = pool_for(bank, register, items)
    pool_texts = [r["text"] for r in pool]
    res = {"scored": now_iso(), "register": register, "n_items": len(items), "pool": len(pool)}
    real_z, _ = habit_z([i["text"] for i in items], pool_texts)
    res["stylo"] = {"real_heldout": real_z}
    for cond in sorted({g["condition"] for g in gens}):
        z, zs = habit_z([g["text"] for g in gens if g["condition"] == cond], pool_texts)
        res["stylo"][cond] = z
        res.setdefault("habit_z", {})[cond] = {k: round(v, 2) for k, v in zs.items()}
    s = res["stylo"]
    res["stylo_closeness_voice"], res["stylo_closeness_voice_raw"] = closeness(s.get("plain"), s.get("voice"), real_z)
    res["lengths"] = {"real_median": statistics.median([features([i["text"]]).get("len_median", 0) for i in items])}
    for cond in ("voice", "plain"):
        L = [features([g["text"]]).get("len_median", 0) for g in gens if g["condition"] == cond]
        if L:
            res["lengths"][cond + "_median"] = statistics.median(L)
    if judge:
        rows = run_judge(run_dir, items, gens, pool, register, model=model, log=log)
        res["judge"] = fooling(rows)
    picks_p = os.path.join(run_dir, "picks.json")
    if os.path.exists(picks_p):
        try:
            res["human"] = score_picks(read_json(picks_p), run_dir)
        except PicksMismatch as e:  # saved picks predate a rebuilt page
            if log:
                log("saved blind-test picks not used: %s" % e)
    write_json(os.path.join(run_dir, "scores.json"), res)
    return res


# ------------------------------------------------------------------ blind test page

def page_data(items, gens, conditions=("voice",), seed=SEED):
    rng = random.Random(seed)
    by_item = {i["id"]: i for i in items}
    pairs = []
    for g in gens:
        if g["condition"] not in conditions or g["item_id"] not in by_item:
            continue
        it = by_item[g["item_id"]]
        real_first = rng.random() < 0.5
        pairs.append({"pid": "p%03d" % len(pairs), "item": it["id"], "condition": g["condition"],
                      "context": it.get("context", ""), "a": it["text"] if real_first else g["text"],
                      "b": g["text"] if real_first else it["text"],
                      "k": base64.b64encode(("A" if real_first else "B").encode()).decode()})
    rng.shuffle(pairs)
    return pairs


def build_page(run_dir, conditions=("voice",)):
    items = read_jsonl(os.path.join(run_dir, "items.jsonl"))
    gens = read_jsonl(os.path.join(run_dir, "generations.jsonl"))
    pairs = page_data(items, gens, conditions)
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = os.path.join(os.path.dirname(os.path.dirname(here)), "templates", "blind_test.html")
    with open(tpl, encoding="utf-8") as f:
        html = f.read()
    run_id = os.path.basename(os.path.normpath(run_dir))
    meta = read_json(os.path.join(run_dir, "meta.json"), {}) or {}
    label = meta.get("label") or "Your real messages (from your voice pack)"
    answers = {p["pid"]: {"item": p["item"], "condition": p["condition"],
                          "real": base64.b64decode(p["k"]).decode()} for p in pairs}
    fp = answer_fingerprint(answers)
    payload = json.dumps({"run": run_id, "key": fp, "label": label, "demo": bool(meta.get("demo")),
                          "pairs": pairs},
                         ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", payload)
    out = os.path.join(run_dir, "blind_test.html")
    _write_private(out, html)
    # answer key, used by `score` instead of trusting the page's own "correct" field
    _write_private(os.path.join(run_dir, "answers.json"), json.dumps({"run": run_id, "key": fp, "answers": answers}))
    return out, len(pairs)


def answer_fingerprint(answers):
    """Short hash of a page's answer key. A rebuilt page (e.g. with --include-plain) reuses pair ids
    for different pairs; the fingerprint ties an export to the page version it was taken on."""
    return hashlib.sha256(json.dumps(answers, sort_keys=True).encode()).hexdigest()[:16]


def _write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


class PicksMismatch(ValueError):
    pass


def score_picks(picks, run_dir):
    """picks: the page's export {"run": ..., "picks": [{"pid", "picked", ...}]}. Correctness is
    worked out from the run's own answer key (answers.json, written by `page`); the export's
    "correct" field is ignored. Raises PicksMismatch if the export is from another run."""
    key = read_json(os.path.join(run_dir, "answers.json"))
    if not key:
        raise PicksMismatch("no answers.json in %s: run `check page` for this run first" % run_dir)
    if (picks or {}).get("run") != key["run"]:
        raise PicksMismatch("these picks are from run %r, not %r" % ((picks or {}).get("run"), key["run"]))
    if key.get("key") and (picks or {}).get("key") != key["key"]:
        raise PicksMismatch("these picks were made on an older version of this run's page (it has been rebuilt "
                            "since, so the pairs differ): retake the test on the current page")
    ans = key["answers"]
    rows, seen = [], set()
    for p in (picks or {}).get("picks", []):
        a = ans.get(p.get("pid"))
        if not a or p.get("pid") in seen or p.get("picked") not in ("A", "B"):
            continue
        seen.add(p["pid"])
        rows.append({"condition": a["condition"], "fooled": p["picked"] != a["real"]})
    return fooling(rows)


def format_scores(res):
    L = ["voice-check (%s, %d held-out messages, pool %d)" % (res["register"], res["n_items"], res["pool"])]
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
    for label, key in (("LLM judge", "judge"), ("your blind test", "human")):
        for cond, d in (res.get(key) or {}).items():
            L.append("  %s fooled on %s: %d/%d = %.0f%% [95%% CI %.0f-%.0f%%] (50%% = indistinguishable)"
                     % (label, cond, d["fooled"], d["n"], 100 * d["rate"], 100 * d["ci"][0], 100 * d["ci"][1]))
    return "\n".join(L)
