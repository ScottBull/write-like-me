"""Fixes from the 0.2.0 privacy audit and cross-model review (privacy-audit-3 / codex-review-3).

Synthetic data only (invented people and words); no model is called.
"""
import contextlib
import io
import json
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import test_lineup as TL  # noqa: E402
import test_person as TP  # noqa: E402
from wlmlib import check, cli_context as CC, person as P  # noqa: E402
from wlmlib.common import read_json, write_json, write_jsonl  # noqa: E402

DOCS = [
    {"id": "file:d01", "source": "file", "register": "doc", "timestamp": "2025-08-01T10:00:00Z", "recipient": "",
     "thread": "notes.md", "context": "", "text": "Synthetic planning notes about the quokka picnic.", "tokens_est": 25},
    {"id": "pc:c01", "source": "pasted", "register": "chat", "timestamp": "2025-08-02T10:00:00Z",
     "recipient": "Someone Example", "thread": "s", "context": "", "text": "pasted chat about the zebra export",
     "tokens_est": 20},
]


class TestUnsortedSourcesStayOut(TP.PackCase):
    def setUp(self):
        super().setUp()
        write_jsonl(os.path.join(self.pack, "archive", "doc.jsonl"), DOCS[:1])
        rows = TP.CHATS + TP.TEAMS + DOCS[1:]
        write_jsonl(os.path.join(self.pack, "archive", "chat.jsonl"), sorted(rows, key=lambda e: e["timestamp"]))

    def test_left_out_unless_asked(self):
        out = self.cli("build")
        self.assertIn("left out of person.md because they may be work material", out)
        self.assertEqual(P.count_unsorted(self.pack), {"file": 1, "pasted": 1})
        self.cli("build", "--yes")
        prompts = "".join(c.args[1] for c in self.mock_call.call_args_list)
        self.assertNotIn("file:d01", prompts)
        self.assertNotIn("pc:c01", prompts)
        self.mock_call.reset_mock()
        out = self.cli("build", "--include-unsorted")
        self.assertIn("included as personal (--include-unsorted)", out)
        self.cli("build", "--include-unsorted", "--yes")
        prompts = "".join(c.args[1] for c in self.mock_call.call_args_list)
        self.assertIn("file:d01", prompts)
        self.assertNotIn("teams:", prompts)


class TestUncitedHeadings(unittest.TestCase):
    def test_heading_not_in_sections_is_dropped(self):
        claims = [{"cid": "c1", "kind": "stance", "claim": "x", "ids": ["a", "b"]}]
        text = ("## Stances\n\nThey prefer trains [c1].\n\n## They have a secret illness\n\nThey like maps [c1].\n\n"
                "### Trains\n\nMore [c1].\n\n### This topic heading is really a whole claim about them\n\nYes [c1].")
        clean, _, st, _ = P.check(text, claims, headings=["## Stances", "## Opinions by topic"])
        self.assertNotIn("secret illness", clean)
        self.assertNotIn("whole claim", clean)
        self.assertIn("### Trains", clean)
        self.assertEqual(st["dropped_heading"], 2)
        clean, _, _, _ = P.check(text, claims)          # without a list, headings are kept (old behaviour)
        self.assertIn("secret illness", clean)


class TestBudgetCoversPersonFile(TL.PackCase):
    def test_person_file_left_out_when_over_budget(self):
        with open(os.path.join(self.pack, "person.md"), "w") as f:
            f.write("# About me\n\n<!-- tier: core -->\n" + "They like invented maps of Brindle. " * 200)
        big = CC.build_context(self.pack, "chat", "thursday works", budget=1000, person="core")
        self.assertEqual(big["person"], "none")
        self.assertLessEqual(big["tokens"], 1000)
        self.assertTrue(any("left out" in n for n in big["notes"]))
        ok = CC.build_context(self.pack, "chat", "thursday works", budget=10000, person="core")
        self.assertEqual(CC.build_context(self.pack, "chat", "thursday works", budget=10000)["person"], "none")  # off by default
        self.assertEqual(ok["person"], "core")
        with self.assertRaises(ValueError):
            CC.fit_budget(5, "", "a learned note that is longer than five tokens of budget")


class TestHeldOutPersonSentences(TL.PackCase):
    def test_sentences_citing_held_out_items_are_removed(self):
        run, items, _ = self.run_dir()
        held = items[0]["id"]
        with open(os.path.join(self.pack, "person.md"), "w") as f:
            f.write("# About me\n\n<!-- tier: core -->\n## Stances\n\nThey like quokkas. They avoid zebras.\n")
        write_json(os.path.join(self.pack, P.SIDECAR), {"version": 1, "files": {"person.md": {"sentences": {
            "core": [{"sentence": "They like quokkas.", "claims": ["c1"], "ids": [held, "syn:0-00"]},
                     {"sentence": "They avoid zebras.", "claims": ["c2"], "ids": ["syn:1-01", "syn:2-02"]}],
            "extension": []}}}})
        text, used, removed = check.heldout_person(self.pack, "core", self.stats, {i["id"] for i in items})
        self.assertEqual((used, removed), (["person.md"], 1))
        self.assertNotIn("quokkas", text)
        self.assertIn("They avoid zebras.", text)


class TestPageAndPicks(TL.PackCase):
    def setUp(self):
        super().setUp()
        self.run, self.items, self.gens = self.run_dir()

    def test_no_real_message_twice_with_plain(self):
        check.write_page(self.run, conditions=("voice", "plain"), bank=self.bank)
        key = read_json(os.path.join(self.run, "answers.json"))
        shown = [a["item"] for a in key["answers"].values()]
        self.assertEqual(len(shown), len(set(shown)))

    def test_fingerprint_follows_the_texts(self):
        check.write_page(self.run, bank=self.bank)
        old = read_json(os.path.join(self.run, "answers.json"))["key"]
        write_jsonl(os.path.join(self.run, "generations.jsonl"),
                    [dict(g, text=g["text"] + " regenerated") for g in self.gens])
        check.write_page(self.run, bank=self.bank)
        self.assertNotEqual(read_json(os.path.join(self.run, "answers.json"))["key"], old)

    def test_payload_cannot_close_the_script(self):
        write_jsonl(os.path.join(self.run, "generations.jsonl"),
                    [dict(g, text="<!-- <script> </script> x") for g in self.gens])
        page = check.write_page(self.run, bank=self.bank)["page"]
        with open(page) as f:
            html = f.read()
        data = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
        self.assertNotIn("<", data)
        self.assertEqual(json.loads(data)["pairs"][0]["a"].count("<!--") + json.loads(data)["pairs"][0]["b"].count("<!--"), 1)

    def test_imported_picks_are_private(self):
        check.write_page(self.run, bank=self.bank)
        key = read_json(os.path.join(self.run, "answers.json"))
        picks = {"run": key["run"], "key": key["key"],
                 "picks": [{"pid": pid, "picked": "A", "note": "invented note"} for pid in key["answers"]]}
        dl = os.path.join(self.pack, "download.json")
        with open(dl, "w") as f:
            json.dump(picks, f)
        os.chmod(dl, 0o644)
        import argparse
        p = argparse.ArgumentParser()
        check.add_parser(p.add_subparsers(dest="cmd"))
        a = p.parse_args(["check", "score", "--run", self.run, "--picks", dl, "--no-judge"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            a.fn(a)
        mode = stat.S_IMODE(os.stat(os.path.join(self.run, "picks.json")).st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
