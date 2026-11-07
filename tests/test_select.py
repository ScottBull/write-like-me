"""Archive selection strategies (select.py). Synthetic entries only (invented people and topics)."""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from wlmlib import select as S  # noqa: E402
from wlmlib.common import est_tokens  # noqa: E402

PEOPLE = ["Jordan Example", "Riley Sample", "Avery Placeholder", "Casey Fixture"]
TOPICS = ["the quokka deck", "the bike rack", "lunch on friday", "the printer on floor three",
          "the zebra invoice", "the offsite agenda", "the parking passes", "the kettle"]


def synth(n=120):
    """n invented chat entries over 2024-2026, four people, mixed lengths and contexts."""
    out = []
    for i in range(n):
        y = 2024 + (i % 3)
        m = 1 + (i * 7) % 12
        d = 1 + (i * 11) % 28
        who = PEOPLE[i % len(PEOPLE)]
        topic = TOPICS[(i * 5) % len(TOPICS)]
        words = 3 + (i * 13) % 60
        text = ("about %s: " % topic) + " ".join("word%d" % ((i + k) % 17) for k in range(words))
        ctx = "" if i % 9 == 0 else "did you sort out %s yet, and what about %s" % (topic, " ".join(["the next one"] * (i % 5)))
        out.append({"id": "syn:%03d" % i, "register": "chat", "timestamp": "%d-%02d-%02dT09:%02d:00Z" % (y, m, d, i % 60),
                    "recipient": who, "thread": "chat-" + who.split()[0].lower(), "context": ctx, "text": text,
                    "word_count": len(text.split())})
    return out


def ids(es):
    return [e["id"] for e in es]


class TestBudget(unittest.TestCase):
    def setUp(self):
        self.E = synth()
        self.req = {"notes": "the zebra invoice is paid", "context": "is the zebra invoice sorted",
                    "person_text": "I run the office for an invented bakery."}

    def test_budget_respected_to_the_token(self):
        for k in S.STRATEGIES:
            for b in (1, 25, 60, 300, 1000, 2500, 10 ** 6):
                with self.subTest(strategy=k, budget=b):
                    got = S.select(self.E, b, self.req, 3, k)
                    self.assertLessEqual(S.total_tokens(got), b)
                    self.assertLessEqual(est_tokens(len(S.render_entries(got, "chat"))), b)
                    if k == "P" and got:
                        self.assertLessEqual(S.total_tokens(got) + S.person_tokens(self.req["person_text"]), b)

    def test_budget_mostly_used(self):
        """Greedy fill skips an entry that does not fit and goes on, so it stops within one entry."""
        big = max(S.entry_tokens(e) for e in self.E)
        for k in ("R", "D", "M", "T"):
            got = S.select(self.E, 1500, self.req, 0, k)
            self.assertGreater(S.total_tokens(got), 1500 - big, k)

    def test_whole_pool_fits(self):
        total = S.total_tokens(self.E)
        for k in ("R", "D", "M", "T"):
            self.assertEqual(len(S.select(self.E, total, self.req, 0, k)), len(self.E), k)

    def test_zero_budget_and_unknown_strategy(self):
        self.assertEqual(S.select(self.E, 0, self.req, 0, "D"), [])
        self.assertEqual(S.select(self.E, -5, self.req, 0, "R"), [])
        with self.assertRaises(ValueError):
            S.select(self.E, 100, self.req, 0, "X")
        self.assertEqual(ids(S.select(self.E, 500, self.req, 0, "d")), ids(S.select(self.E, 500, self.req, 0, "D")))

    def test_cost_tracks_archive_estimate(self):
        """The renderer stays close to archive.py's tokens_est overhead (60 chars)."""
        from wlmlib import archive
        for e in self.E:
            self.assertLessEqual(abs(S.entry_tokens(e) - archive.tokens_est(e)), 3)


class TestDeterminismAndOrder(unittest.TestCase):
    def setUp(self):
        self.E = synth()
        self.req = {"notes": "the kettle is fixed", "context": "who broke the kettle"}

    def test_same_seed_same_selection(self):
        for k in S.STRATEGIES:
            a = S.select(self.E, 800, self.req, 7, k)
            b = S.select(copy.deepcopy(self.E), 800, dict(self.req), 7, k)
            self.assertEqual(ids(a), ids(b), k)

    def test_random_depends_on_seed(self):
        self.assertNotEqual(ids(S.select(self.E, 800, None, 1, "R")), ids(S.select(self.E, 800, None, 2, "R")))

    def test_sorted_by_date(self):
        shuffled = list(reversed(self.E))
        for k in S.STRATEGIES:
            got = S.select(shuffled, 900, self.req, 1, k)
            keys = [(e["timestamp"], e["id"]) for e in got]
            self.assertEqual(keys, sorted(keys), k)

    def test_input_not_mutated(self):
        before = copy.deepcopy(self.E)
        for k in S.STRATEGIES:
            S.select(self.E, 900, self.req, 1, k)
        self.assertEqual(self.E, before)

    def test_recency_takes_newest(self):
        got = S.select(self.E, 400, None, 0, "T")
        self.assertIn(max(self.E, key=lambda e: e["timestamp"])["id"], ids(got))
        self.assertTrue(all(e["timestamp"] >= "2026" for e in got))

    def test_stratified_covers_people(self):
        got = S.select(self.E, 3000, None, 0, "D")
        self.assertEqual(len({e["recipient"] for e in got}), 4)
        self.assertEqual(len({e["timestamp"][:4] for e in got}), 3)


class TestLeakRule(unittest.TestCase):
    def setUp(self):
        self.E = [
            {"id": "a", "thread": "t1", "timestamp": "2026-03-01T10:00:00Z", "text": "alpha one", "context": ""},
            {"id": "b", "thread": "t1", "timestamp": "2026-03-02T09:00:00Z", "text": "alpha two", "context": ""},
            {"id": "c", "thread": "t1", "timestamp": "2026-03-10T09:00:00Z", "text": "alpha three", "context": ""},
            {"id": "d", "thread": "t2", "timestamp": "2026-03-01T11:00:00Z", "text": "beta one", "context": ""},
            {"id": "e", "thread": "t1", "timestamp": "", "text": "alpha undated", "context": ""},
        ]
        self.item = {"id": "a", "thread": "t1", "timestamp": "2026-03-01T10:00:00Z"}

    def test_48h_same_thread(self):
        for k in S.STRATEGIES:
            got = set(ids(S.select(self.E, 10 ** 6, {"heldout": self.item}, 0, k)))
            self.assertEqual(got, {"c", "d"}, k)      # a itself, b (23 h), e (undated, same thread) are out

    def test_whole_thread_and_lists(self):
        got = ids(S.select(self.E, 10 ** 6, {"heldout": [self.item], "leak_hours": None}, 0, "D"))
        self.assertEqual(got, ["d"])
        got = ids(S.select(self.E, 10 ** 6, {"heldout": [self.item, {"id": "d", "thread": "t2"}],
                                             "leak_hours": None}, 0, "T"))
        self.assertEqual(got, [])

    def test_exclude_ids_and_no_option(self):
        self.assertEqual(len(S.select(self.E, 10 ** 6, None, 0, "D")), 5)
        self.assertNotIn("d", ids(S.select(self.E, 10 ** 6, {"exclude_ids": ["d"]}, 0, "R")))


class TestMatched(unittest.TestCase):
    def setUp(self):
        self.E = synth()

    def test_prefers_similar(self):
        req = {"context": "the zebra invoice", "notes": "zebra invoice paid"}
        def share(es):
            return sum("zebra" in e["text"] for e in es) / float(len(es))
        got = S.select(self.E, 1000, req, 0, "M")
        self.assertGreaterEqual(share(got), 0.7)
        self.assertGreater(share(got), share(S.select(self.E, 1000, req, 0, "D")))

    def test_falls_back_to_d(self):
        d = ids(S.select(self.E, 700, None, 4, "D"))
        self.assertEqual(ids(S.select(self.E, 700, {"notes": "", "context": ""}, 4, "M")), d)
        self.assertEqual(ids(S.select(self.E, 700, None, 4, "M")), d)
        self.assertEqual(ids(S.select(self.E, 700, {"notes": "xylophone qwerty"}, 4, "M")), d)
        self.assertEqual(S.select([], 700, {"notes": "zebra"}, 4, "M"), [])

    def test_unmatched_remainder_filled_by_d(self):
        req = {"notes": "zebra"}
        n_zebra = sum("zebra" in e["text"] for e in self.E)
        got = S.select(self.E, 10 ** 6, req, 0, "M")
        self.assertEqual(len(got), len(self.E))
        self.assertGreater(len(self.E), n_zebra)


class TestCompactAndPerson(unittest.TestCase):
    def setUp(self):
        self.E = synth()
        for e in self.E:
            e["context"] = "a long message from them " + " ".join("filler%d" % k for k in range(40))

    def test_compact_cuts_context_and_fits_more(self):
        c = S.select(self.E, 1500, None, 0, "C")
        d = S.select(self.E, 1500, None, 0, "D")
        self.assertGreater(len(c), len(d))
        for e in c:
            self.assertLessEqual(len(e["context"].split()), S.COMPACT_CONTEXT_WORDS + 1)
            self.assertTrue(e["context"].endswith(" ..."))
        self.assertLessEqual(S.total_tokens(c), 1500)

    def test_compact_handles_every_register(self):
        """C over each register's own fields (Teams/chat entries have no subreddit; prompt entries a
        project; some have no context, recipient or timestamp): no KeyError, context cut everywhere."""
        long_ctx = " ".join("w%d" % k for k in range(40))
        for reg in ("chat", "email", "post", "doc", "prompt"):
            es = []
            for i in range(30):
                e = {"id": "%s:%02d" % (reg, i), "register": reg, "text": "entry %d " % i + "x " * (i % 9),
                     "context": long_ctx if i % 3 else ""}
                if i % 4:
                    e["timestamp"] = "2025-%02d-01T10:00:00Z" % (1 + i % 12)
                if reg in ("chat", "email") and i % 2:
                    e["recipient"] = "Person %d" % (i % 3)
                if reg == "post":
                    e["recipient"] = "r/sub%d" % (i % 2)
                if reg == "email":
                    e["subject"] = "re: thing %d" % i
                if reg == "prompt":
                    e["project"] = "proj%d" % (i % 2)
                es.append(e)
            picked = S.select(es, 400, None, 0, "C")
            self.assertTrue(picked, reg)
            self.assertLessEqual(S.total_tokens(picked), 400, reg)
            for e in picked:
                n = len(e["context"].split())
                if e["context"]:
                    self.assertEqual((n, e["context"][-4:]), (S.COMPACT_CONTEXT_WORDS + 1, " ..."), reg)
                self.assertNotIn("w39", e["context"])
            system, _ = S.assemble("", picked, reg, {"notes": "n"})
            self.assertNotIn("w39", system, reg)
            self.assertEqual(system.count("### "), len(picked), reg)
            self.assertEqual(es[1]["context"], long_ctx)          # the archive itself is untouched

    def test_person_takes_its_share(self):
        person = "I run an invented bakery. " * 40
        p = S.select(self.E, 2000, {"person_text": person}, 0, "P")
        self.assertLessEqual(S.total_tokens(p) + S.person_tokens(person), 2000)
        self.assertEqual(ids(p), ids(S.select(self.E, 2000 - S.person_tokens(person), None, 0, "D")))
        self.assertEqual(ids(S.select(self.E, 2000, {}, 0, "P")), ids(S.select(self.E, 2000, None, 0, "D")))
        self.assertEqual(S.select(self.E, 10, {"person_text": person}, 0, "P"), [])


if __name__ == "__main__":
    unittest.main()
