"""voice-check lineups, answer key, scoring and the scripted generate path (check.py).

Synthetic data only: invented people, threads and words. No model calls: llm.call and draft.draft
are replaced by stubs.
"""
import json
import os
import random
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from wlmlib import build, check, draft, llm  # noqa: E402
from wlmlib.common import make_record, read_json, write_json, write_jsonl  # noqa: E402

PEOPLE = ["Jordan Example", "Riley Sample", "Avery Placeholder", "Casey Fixture", "Morgan Testcase",
          "Quinn Mockup", "Sky Dummy", "Rowan Stub"]
VOCAB = ("quokka deck printer kettle invoice offsite parking rack lunch agenda zebra export wifi "
         "sounds good thursday later tomorrow maybe sure fine send check ping again after before noon "
         "the a it is was will can should we they you i ok yep nope cool thanks").split()


def synth_records(per_thread=12, seed=7):
    """Invented chat records: eight threads, 3-30 words, a few days apart (never within 48 h)."""
    rng = random.Random(seed)
    out = []
    for t, who in enumerate(PEOPLE):
        for k in range(per_thread):
            n = rng.randint(3, 30)
            text = " ".join(rng.choice(VOCAB) for _ in range(n)) + " %s%d" % ("x" * (t + 1), k)
            day = 1 + (k * 3 + t) % 27
            month = 1 + k // 3
            ts = "2026-%02d-%02dT%02d:00:00Z" % (month, day, 9 + t)
            out.append(make_record("synthetic", "chat", text, rid="syn:%d-%02d" % (t, k), timestamp=ts,
                                   recipient=who, thread="chat-%d" % t,
                                   context="did you look at the %s yet" % rng.choice(VOCAB)))
    return out


class PackCase(unittest.TestCase):
    def setUp(self):
        self.pack = tempfile.mkdtemp(prefix="wlm_lineup_")
        write_jsonl(os.path.join(self.pack, "sources", "synthetic.jsonl"), synth_records())
        self._env = os.environ.get("WRITE_LIKE_ME_PACK")
        os.environ["WRITE_LIKE_ME_PACK"] = self.pack
        build.build(self.pack)
        self.stats, self.bank, self.rules = build.load_pack(self.pack)
        self._call, self._draft = llm.call, draft.draft

    def tearDown(self):
        llm.call, draft.draft = self._call, self._draft
        shutil.rmtree(self.pack, ignore_errors=True)
        if self._env is None:
            os.environ.pop("WRITE_LIKE_ME_PACK", None)
        else:
            os.environ["WRITE_LIKE_ME_PACK"] = self._env

    def run_dir(self, n=4, lineups=4, name="r1"):
        items = check.select_items(self.bank, self.stats, "chat", n=n, lineups=lineups)
        run = os.path.join(self.pack, "check", name)
        os.makedirs(run)
        write_jsonl(os.path.join(run, "items.jsonl"), items)
        write_json(os.path.join(run, "meta.json"), {"register": "chat", "demo": True,
                                                    "label": "synthetic fixture data"})
        gens = [{"item_id": i["id"], "condition": c, "text": "generated %s %s" % (c, i["id"]), "brief": "b"}
                for i in items for c in ("voice", "plain")]
        write_jsonl(os.path.join(run, "generations.jsonl"), gens)
        return run, items, gens


class TestSelectItems(PackCase):
    def test_sections(self):
        items = check.select_items(self.bank, self.stats, "chat", n=5, lineups=4)
        self.assertEqual(len(items), 9)
        self.assertEqual(sum(i["section"] == "paired" for i in items), 5)
        self.assertEqual(sum(i["section"] == "lineup" for i in items), 4)
        self.assertEqual(len({i["id"] for i in items}), 9)
        # no lineups asked for: every item is paired (the old single-section run)
        self.assertTrue(all(i["section"] == "paired" for i in check.select_items(self.bank, self.stats, "chat", n=6)))
        # too few messages: the paired section fills first
        few = check.select_items(self.bank, self.stats, "chat", n=15, lineups=10)
        self.assertEqual(sum(i["section"] == "paired" for i in few), min(15, len(few)))


class TestLineups(PackCase):
    def test_design(self):
        run, items, gens = self.run_dir(n=4, lineups=4)
        pool = check.pool_for(self.bank, "chat", items)
        held = {i["id"] for i in items}
        by_id = {r["id"]: r for r in self.bank}
        lin = check.make_lineups(items, gens, pool, "voice", reps=2)
        self.assertEqual(len(lin), 2 * len(items))
        for l in lin:
            it = next(i for i in items if i["id"] == l["item"])
            self.assertEqual(len(l["cands"]), 4)
            fake = l["cands"]["ABCD".index(l["fake"])]["text"]
            self.assertEqual(fake, "generated voice %s" % it["id"])
            self.assertEqual(len(l["filler_ids"]), 3)
            self.assertEqual(len(set(l["filler_threads"])), 3)        # three different threads
            for fid in l["filler_ids"]:
                f = by_id[fid]
                self.assertNotEqual(fid, it["id"])                    # never the item's own message
                self.assertNotEqual(f["thread"], it["thread"])        # never its thread
                self.assertNotIn(fid, held)                           # never another held-out message
                self.assertEqual(f["register"], "chat")
            texts = [c["text"] for c in l["cands"]]
            self.assertNotIn(it["text"], texts)
            self.assertEqual(sum(t.startswith("generated") for t in texts), 1)
        # reps of one item put the fake in different positions
        for it in items:
            pos = [l["fake"] for l in lin if l["item"] == it["id"]]
            self.assertEqual(len(set(pos)), 2)

    def test_length_window(self):
        pool = [{"id": "f%d" % k, "register": "chat", "thread": "t%d" % k, "text": " ".join(["w"] * n)}
                for k, n in enumerate([10, 9, 11, 12, 8, 13, 10, 30, 2, 3])]
        item = {"id": "i", "register": "chat", "thread": "ti", "text": " ".join(["w"] * 10), "context": "c"}
        gens = [{"item_id": "i", "condition": "voice", "text": "g"}]
        for rep in range(5):
            l = check.make_lineups([item], gens, pool, reps=1, seed=rep)[0]
            for fid in l["filler_ids"]:
                n = len(next(r for r in pool if r["id"] == fid)["text"].split())
                self.assertTrue(7.5 <= n <= 13.4, n)
        # fewer than six in the window: the twelve nearest (here all ten) are allowed
        l = check.make_lineups([dict(item, text="w w w")], gens, pool, reps=1)[0]
        self.assertEqual(len(l["filler_ids"]), 3)
        # fewer than three other threads: no lineup
        thin = [dict(r, thread="ti" if k else "t0") for k, r in enumerate(pool)]
        self.assertEqual(check.make_lineups([item], gens, thin), [])

    def test_page_never_repeats_a_message(self):
        run, items, gens = self.run_dir(n=4, lineups=4)
        info = check.write_page(run, bank=self.bank)
        self.assertEqual(info["pairs"], 4)
        self.assertGreaterEqual(info["lineups"], 3)
        with open(info["page"]) as f:
            html = f.read()
        data = json.loads(html.split("const DATA = ", 1)[1].split(";\nconst KEY", 1)[0])
        shown = [c["text"] for l in data["lineups"] for c in l["cands"]]
        shown += [t for p in data["pairs"] for t in (p["a"], p["b"])]
        self.assertEqual(len(shown), len(set(shown)))
        paired = {p["item"] for p in data["pairs"]}
        lined = {l["item"] for l in data["lineups"]}
        self.assertFalse(paired & lined)
        self.assertNotIn("fake", json.dumps(data))
        self.assertNotIn("filler_ids", json.dumps(data))
        self.assertTrue(data["demo"])


class TestAnswerKeyAndScoring(PackCase):
    def setUp(self):
        super().setUp()
        self.run, self.items, self.gens = self.run_dir(n=4, lineups=4)
        check.write_page(self.run, bank=self.bank)
        self.key = read_json(os.path.join(self.run, "answers.json"))

    def export(self, pair_right=(), lineup_right=()):
        k = self.key
        flip = {"A": "B", "B": "A"}
        picks = [{"pid": pid, "picked": a["real"] if pid in pair_right else flip[a["real"]], "correct": None}
                 for pid, a in sorted(k["answers"].items())]
        lines = [{"lid": lid, "picked": a["fake"] if lid in lineup_right else "ABCD"[("ABCD".index(a["fake"]) + 1) % 4],
                  "note": "typo in it" if lid in lineup_right else ""}
                 for lid, a in sorted(k["lineups"].items())]
        return {"run": k["run"], "key": k["key"], "picks": picks, "lineups": lines}

    def test_answer_key_covers_both(self):
        k = self.key
        self.assertEqual(k["run"], "r1")
        self.assertEqual(len(k["answers"]), 4)
        self.assertGreaterEqual(len(k["lineups"]), 3)
        for a in k["answers"].values():
            self.assertIn(a["real"], ("A", "B"))
        for a in k["lineups"].values():
            self.assertIn(a["fake"], tuple("ABCD"))
            self.assertEqual(len(a["filler_ids"]), 3)
        self.assertEqual(k["key"], check.answer_fingerprint(k["answers"], k["lineups"]))
        self.assertEqual(os.stat(os.path.join(self.run, "answers.json")).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.join(self.run, "blind_test.html")).st_mode & 0o777, 0o600)

    def test_scoring(self):
        pids, lids = sorted(self.key["answers"]), sorted(self.key["lineups"])
        ex = self.export(pair_right=pids[:3], lineup_right=lids[:2])
        # the export's own "correct" field, unknown ids and repeats are ignored
        ex["picks"][0]["correct"] = False
        ex["picks"].append({"pid": "p999", "picked": "A"})
        ex["lineups"].append(dict(ex["lineups"][0], picked="A"))
        res = check.score_human(ex, self.run)
        p, l = res["paired"]["voice"], res["lineup"]["voice"]
        self.assertEqual((p["n"], p["fooled"]), (4, 1))
        self.assertEqual((l["n"], l["spotted"]), (len(lids), 2))
        self.assertEqual(l["chance"], 0.25)
        self.assertAlmostEqual(l["rate"], 2.0 / len(lids))
        self.assertEqual(list(l["ci"]), list(check.wilson(2, len(lids))))
        self.assertEqual(res["lineup"]["voice"]["reasons"]["polish"], 2)    # "typo in it" on both catches
        self.assertEqual(res["notes"]["catches"], 5)
        self.assertEqual(check.score_picks(ex, self.run), res["paired"])
        # score() picks up a saved picks.json
        write_json(os.path.join(self.run, "picks.json"), ex)
        out = check.score(self.run, self.bank, self.stats, judge=False, log=None)
        self.assertEqual(out["human"]["lineup"]["voice"]["spotted"], 2)
        self.assertIn("lineups, voice: spotted 2/", check.format_scores(out))
        self.assertIn("pairs, voice: fooled 1/4", check.format_scores(out))

    def test_refuses_foreign_picks(self):
        ex = self.export()
        with self.assertRaises(check.PicksMismatch):
            check.score_human(dict(ex, run="2026-01-01T000000-chat"), self.run)
        with self.assertRaises(check.PicksMismatch):
            check.score_human(dict(ex, key="0" * 16), self.run)
        with self.assertRaises(check.PicksMismatch):
            check.score_human(dict(ex, key=None), self.run)
        with self.assertRaises(check.PicksMismatch):
            check.score_human(None, self.run)
        other, _, _ = self.run_dir(name="r2")
        with self.assertRaises(check.PicksMismatch):     # no page built for that run yet
            check.score_human(dict(ex, run="r2"), other)
        # a stale saved picks.json is reported, not scored
        write_json(os.path.join(self.run, "picks.json"), dict(ex, key="stale"))
        msgs = []
        self.assertNotIn("human", check.score(self.run, self.bank, self.stats, judge=False, log=msgs.append))
        self.assertTrue(msgs)

    def test_rebuilt_page_changes_key(self):
        old = self.key["key"]
        check.write_page(self.run, conditions=("voice", "plain"), bank=self.bank)
        self.assertNotEqual(read_json(os.path.join(self.run, "answers.json"))["key"], old)


class TestStats(unittest.TestCase):
    def test_wilson(self):
        lo, hi = check.wilson(5, 20)
        self.assertTrue(0.10 < lo < 0.12 and 0.46 < hi < 0.48)
        self.assertEqual(check.wilson(0, 10)[0], 0.0)
        self.assertEqual(check.wilson(10, 10)[1], 1.0)
        lo, hi = check.wilson(3, 12)          # 25% of 12 lineups: chance sits inside
        self.assertTrue(lo < 0.25 < hi)

    def test_rates(self):
        rows = [{"condition": "voice", "spotted": k < 3, "pick": "A", "tell": t, "tag": check.tag_reason(t)}
                for k, t in enumerate(["too formal for him", "a typo", "", "x", "y", "z", "w", "v"])]
        d = check.spotted(rows)["voice"]
        self.assertEqual((d["n"], d["spotted"], d["chance"]), (8, 3, 0.25))
        self.assertEqual(d["pick_positions"]["A"], 8)
        self.assertEqual((d["reasons"]["voice"], d["reasons"]["polish"], d["reasons"]["untagged"]), (1, 1, 1))
        self.assertAlmostEqual(d["reasons"]["voice_catch_rate"], 1 / 8.0)
        f = check.fooling([{"condition": "plain", "fooled": k % 2 == 0} for k in range(6)])["plain"]
        self.assertEqual((f["n"], f["fooled"], f["chance"]), (6, 3, 0.5))

    def test_tag_reason(self):
        for r in ("typo in the second sentence", "Lowercase start", "too polished", "apostrophe style",
                  "doubled word 'the the'", "missing comma"):
            self.assertEqual(check.tag_reason(r), "polish", r)
        for r in ("too long for a chat reply", "explains more than he would", "generic opener"):
            self.assertEqual(check.tag_reason(r), "voice", r)
        self.assertIsNone(check.tag_reason(""))
        self.assertIsNone(check.tag_reason(None))


class TestJudge(PackCase):
    def test_lineup_judge_offline(self):
        run, items, gens = self.run_dir(n=4, lineups=4)
        pool = check.pool_for(self.bank, "chat", items)
        prompts = []

        def fake_call(system, user, cache_dir=None, model=None, **kw):
            prompts.append(user)
            # answer every trial: lineups pick the generated text, pairs pick A
            out = []
            for chunk in user.split("<lineup id=\"")[1:]:
                lid = chunk.split("\"", 1)[0]
                cands = chunk.split("<candidate_")[1:]
                L = next(c[0] for c in cands if "<message>generated" in c)
                out.append({"id": lid, "not_p": L, "confidence": 80, "reason": "too tidy"})
            for chunk in user.split("<trial id=\"")[1:]:
                out.append({"id": chunk.split("\"", 1)[0], "real": "A", "tell": "length"})
            return json.dumps(out)
        llm.call = fake_call
        rows = check.run_judge(run, items, gens, pool, "chat", mode="both", workers=1, log=None)
        s = check.summarize_judge(rows)
        for cond in ("voice", "plain"):
            self.assertEqual(s["lineup"][cond]["n"], 2 * len(items))
            self.assertEqual(s["lineup"][cond]["rate"], 1.0)
            self.assertEqual(s["lineup"][cond]["reasons"]["polish"], 2 * len(items))
            self.assertEqual(s["paired"][cond]["n"], 2 * len(items))
            self.assertEqual(s["paired"][cond]["fooled"], len(items))
        lineup_prompts = [p for p in prompts if "<lineup id=" in p]
        self.assertTrue(lineup_prompts)
        self.assertIn("did NOT write", lineup_prompts[0])
        # a batch never shows a message twice, and no reference is a message the batch shows
        for t in check.batches(check.lineup_trials(check.make_lineups(items, gens, pool, "voice", reps=2)), 6):
            keys = [k for tr in t for k in tr["keys"]]
            self.assertEqual(len(keys), len(set(keys)))
            refs = check.judge_refs(pool, t, 8, 1)
            shown = {c["text"] for tr in t for c in tr["cands"]}
            self.assertFalse(shown & set(refs))

    def test_parse(self):
        got = check.parse_lineup_judge('x [{"id": "a", "not_p": "c"}, {"id": "b", "not_p": "2"}, {"id": "c", "not_p": "E"}]')
        self.assertEqual([(g["id"], g["not_p"]) for g in got], [("a", "C"), ("b", "B")])
        self.assertEqual(check.parse_judge('noise [{"id": "x", "real": "A"}] tail')[0]["real"], "A")
        self.assertEqual(check.parse_lineup_judge("not json"), [])


class TestGenerate(PackCase):
    def test_generate_pipeline_offline(self):
        run, items, _ = self.run_dir(n=4, lineups=2)
        os.remove(os.path.join(run, "generations.jsonl"))
        seen = []

        def fake_call(system, user, cache_dir=None, model=None, **kw):
            if '{"brief"' in user:
                return '{"brief": "say the export is done"}'
            return "plain answer"

        def fake_draft(system_file, task, model=None, **kw):
            with open(system_file) as f:
                seen.append((f.read(), task))
            return "voice answer"
        llm.call, draft.draft = fake_call, fake_draft
        rows = check.generate(run, self.bank, self.stats, self.rules, budget=3000, strategy="d", log=None)
        self.assertEqual(len(rows), 2 * len(items))
        self.assertEqual({r["condition"] for r in rows}, {"voice", "plain"})
        v = [r for r in rows if r["condition"] == "voice"]
        self.assertTrue(all(r["text"] == "voice answer" and r["tokens"] <= 3000 for r in v))
        self.assertTrue(all(r["entries"] > 0 for r in v))
        meta = read_json(os.path.join(run, "meta.json"))
        self.assertEqual((meta["generate"]["strategy"], meta["generate"]["budget"]), ("D", 3000))
        self.assertEqual(len(seen), len(items))
        for system, task in seen:
            self.assertIn("say the export is done", task)
            for it in items:                                   # no held-out message reaches the drafter
                self.assertNotIn(it["text"], system)
        # pack defaults when no flags are given
        stats = dict(self.stats, budget_default=2000, strategy_default="T")
        check.generate(run, self.bank, stats, self.rules, log=None)
        meta = read_json(os.path.join(run, "meta.json"))
        self.assertEqual((meta["generate"]["strategy"], meta["generate"]["budget"]), ("T", 2000))

    def test_voice_context_leak_rule(self):
        items = check.select_items(self.bank, self.stats, "chat", n=4, lineups=0)
        entries = [e for e in self.bank if e["register"] == "chat"]
        it = items[0]
        _, _, picked = check.voice_context(entries, it, items, "notes", "chat", 10 ** 6, "D")
        ids = {e["id"] for e in picked}
        self.assertFalse(ids & {i["id"] for i in items})
        from wlmlib import select as S
        self.assertFalse(any(S.leaks(e, it) for e in picked))
        self.assertTrue(picked)


if __name__ == "__main__":
    unittest.main()
