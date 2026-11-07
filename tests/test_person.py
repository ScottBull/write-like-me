"""The person file (person.py, cli_person.py). llm.call is mocked: no model is called. Synthetic
fixtures only: an invented person in an invented town."""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from wlmlib import cli_context as CC, cli_person, llm, person as P  # noqa: E402
from wlmlib.common import read_json, write_json, write_jsonl  # noqa: E402

POSTS = [
    {"id": "pasted:p%02d" % i, "source": "pasted", "register": "post", "timestamp": "2025-0%d-10T10:00:00Z" % (i + 1),
     "recipient": "r/trains", "thread": "t%d" % i, "context": "what do you think of {night trains}?",
     "text": "Synthetic post %d about trains and the invented town of Brindle." % i, "tokens_est": 30}
    for i in range(4)
]
CHATS = [
    {"id": "imessage:m01", "source": "imessage", "register": "chat", "timestamp": "2025-06-01T10:00:00Z",
     "recipient": "Friend Example", "thread": "f", "context": "", "text": "my soup exploded again", "tokens_est": 20},
    {"id": "learned:l01", "source": "learned", "register": "chat", "timestamp": "2025-06-02T10:00:00Z",
     "recipient": "Someone", "thread": "g", "context": "", "text": "ambiguous learned chat", "tokens_est": 20},
]
TEAMS = [
    {"id": "teams:w%02d" % i, "source": "teams", "register": "chat", "timestamp": "2025-07-0%dT10:00:00Z" % (i + 1),
     "recipient": "Coworker Example", "thread": "w", "context": "", "text": "review before merge, always (%d)" % i,
     "tokens_est": 20}
    for i in range(3)
]


def fake_call(system, user, **_kw):
    """A stand-in for the model, keyed on each template's opening words."""
    if system.startswith("You extract claims"):
        ids = re.findall(r"^\[(\S+)\] ", user, re.M)
        if any(i.startswith("teams:") for i in ids):
            return json.dumps([{"kind": "stance", "claim": "They insist on code review before a merge.",
                                "confidence": "high", "years": "2025", "ids": ids[:2]}])
        posts = [i for i in ids if i.startswith("pasted:")]
        if not posts:  # the personal chat window
            return json.dumps([{"kind": "humour", "claim": "They joke about their own cooking.",
                                "confidence": "medium", "years": "2025", "ids": ids[:1]}])
        bare = posts[2].split(":", 1)[1]  # the model drops the source prefix: repaired
        return "Here you go:\n```json\n" + json.dumps([
            {"kind": "stance", "claim": "They prefer trains over planes.", "confidence": "high",
             "years": "2025", "ids": [posts[0], posts[1]]},
            {"kind": "stance", "claim": "They prefer trains over planes", "confidence": "medium",
             "years": "2025", "ids": [bare]},
            {"kind": "fact", "claim": "They live in the town of Brindle.", "confidence": "high",
             "years": "2025", "ids": [posts[0]]},
            {"kind": "knowledge", "claim": "They repair bicycles.", "confidence": "medium",
             "years": "2025", "ids": [posts[1]]},
            {"kind": "relationship", "claim": "They are patient with strangers.", "confidence": "high",
             "years": "2025", "ids": ["pasted:nope", "made-up"]},
            {"kind": "vibe", "claim": "Not a kind.", "ids": [posts[0]]},
        ]) + "\n```"
    if system.startswith("You merge a list of claims"):
        rows = re.findall(r"^(\d+)\. \[[^\]]*\] (.*)$", user, re.M)
        return json.dumps({"claims": [{"claim": t, "from": [int(n)], "evolution": None} for n, t in rows],
                           "dropped": []})
    if system.startswith("You write a short description"):
        cids = re.findall(r"^(c\d+) \[[^\]]*\] (.*?)(?: \(single source\))?$", user, re.M)
        body = "\n".join("- %s [%s]" % (t.rstrip("."), c) + "." for c, t in cids)
        return ("## Who they are\nThis sentence has no marker. %s\n\n## Stances\n%s\n- Ghost claim [c999].\n\n"
                "## Humour\nNothing cited here at all.\n" % (body.split("\n")[0][2:], body))
    if system.startswith("You extend a short description"):
        c = re.findall(r"^(c\d+) ", user, re.M)[0]
        return "## Opinions by topic\n### Travel\nThey would rather sit on a train than in an airport [%s]." % c
    if system.startswith("You revise a cited description"):
        return user.split("\n\nExtra claims available", 1)[0]
    raise AssertionError("unexpected prompt")


class PackCase(unittest.TestCase):
    def setUp(self):
        self.pack = tempfile.mkdtemp(prefix="wlm_person_")
        write_json(os.path.join(self.pack, "stats.json"), {"pack_version": 2})
        write_jsonl(os.path.join(self.pack, "archive", "post.jsonl"), POSTS)
        write_jsonl(os.path.join(self.pack, "archive", "chat.jsonl"), sorted(CHATS + TEAMS, key=lambda e: e["timestamp"]))
        self.old_env = os.environ.get("WRITE_LIKE_ME_PACK")
        os.environ["WRITE_LIKE_ME_PACK"] = self.pack
        self.llm = mock.patch.object(llm, "call", side_effect=fake_call)
        self.mock_call = self.llm.start()

    def tearDown(self):
        self.llm.stop()
        if self.old_env is None:
            os.environ.pop("WRITE_LIKE_ME_PACK", None)
        else:
            os.environ["WRITE_LIKE_ME_PACK"] = self.old_env
        shutil.rmtree(self.pack, ignore_errors=True)

    def read(self, name):
        with open(os.path.join(self.pack, name), encoding="utf-8") as f:
            return f.read()

    def cli(self, *argv):
        ap = argparse.ArgumentParser()
        sp = ap.add_subparsers(dest="cmd", required=True)
        cli_person.add_parser(sp)
        a = ap.parse_args(["person"] + list(argv))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            a.fn(a)
        return out.getvalue()


class TestClaimParsing(unittest.TestCase):
    def test_parse_json_tolerates_fences_and_prose(self):
        self.assertEqual(P.parse_json('```json\n[{"a": 1}]\n```', list), [{"a": 1}])
        self.assertEqual(P.parse_json('Sure:\n{"claims": []}\nDone.', dict), {"claims": []})
        with self.assertRaises(ValueError):
            P.parse_json("no json here", list)
        with self.assertRaises(ValueError):
            P.parse_json('{"a": 1}', list)

    def test_parse_claims(self):
        valid = {"pasted:p00", "pasted:p01", "teams:abcdef123456:99"}
        claims, dropped = P.parse_claims([
            {"kind": "Stance", "claim": "  They like  trains. ", "confidence": "HIGH", "ids": ["pasted:p00", "p01", "p01"]},
            {"kind": "fact", "claim": "They live in Brindle.", "ids": "abcdef123456:99"},
            {"kind": "humour", "claim": "Invented ids only.", "ids": ["x1", "pasted:zz"]},
            {"kind": "style", "claim": "They write short sentences.", "ids": ["pasted:p00"]},
            {"kind": "fact", "claim": "", "ids": ["pasted:p00"]},
            "not an object",
        ], valid, "post")
        self.assertEqual([c["kind"] for c in claims], ["stance", "fact"])
        self.assertEqual(claims[0]["claim"], "They like trains.")
        self.assertEqual(claims[0]["ids"], ["pasted:p00", "pasted:p01"])   # prefix repaired, de-duplicated
        self.assertEqual(claims[0]["confidence"], "high")
        self.assertEqual(claims[1]["ids"], ["teams:abcdef123456:99"])      # suffix match
        self.assertEqual(claims[1]["confidence"], "medium")
        self.assertEqual((dropped["no_valid_ids"], dropped["bad_kind_or_empty"], dropped["not_an_object"]), (1, 2, 1))
        with self.assertRaises(ValueError):
            P.parse_claims({"claims": []}, valid)

    def test_merge_and_reduce_union_ids(self):
        merged = P.merge_deterministic([
            {"kind": "stance", "claim": "They prefer trains.", "ids": ["a"], "confidence": "low", "years": "2021", "source": "post"},
            {"kind": "stance", "claim": "they PREFER trains", "ids": ["b", "a"], "confidence": "high", "years": "2024", "source": "chat"},
            {"kind": "fact", "claim": "They prefer trains.", "ids": ["c"], "confidence": "high", "years": "", "source": "post"},
        ])
        self.assertEqual(len(merged), 2)
        self.assertEqual((merged[0]["ids"], merged[0]["confidence"], merged[0]["years"]), (["a", "b"], "high", "2021-2024"))
        out, dropped = P.parse_reduce({"claims": [{"claim": "Merged.", "from": [1, 2, "9", "x"]},
                                                  {"claim": "No sources.", "from": []}],
                                       "dropped": [{"from": 2, "reason": "third party"}]}, merged, "stance")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ids"], ["a", "b", "c"])
        self.assertEqual((dropped["reduce_no_sources"], dropped["reduce_dropped_third_party"]), (1, 1))

    def test_two_citation_rule_and_biography_exception(self):
        kept, dropped = P.apply_citation_rule([
            {"kind": "stance", "claim": "two", "ids": ["a", "b"], "confidence": "low"},
            {"kind": "stance", "claim": "one", "ids": ["a", "a"], "confidence": "high"},
            {"kind": "fact", "claim": "stated once", "ids": ["a"], "confidence": "high"},
            {"kind": "fact", "claim": "weak once", "ids": ["a"], "confidence": "medium"},
        ])
        self.assertEqual([(c["claim"], c["single_source"]) for c in kept], [("two", False), ("stated once", True)])
        self.assertEqual(dict(dropped), {"stance": 1, "fact": 1})


CLAIMS = [{"cid": "c1", "ids": ["a", "b"]}, {"cid": "c2", "ids": ["b", "c"]}]


class TestCheck(unittest.TestCase):
    def test_strip_markers(self):
        self.assertEqual(P.strip_markers("They like trains [c1]. And buses [c2, c3]!"), "They like trains. And buses!")
        self.assertEqual(P.cited_ids("x [c1] y [ c2 , c3 ]"), ["c1", "c2", "c3"])

    def test_uncited_sentence_is_dropped(self):
        text = ("## Who\nThey live in Brindle [c1]. This one has no marker. They ride trains [c2].\n"
                "No marker on this line.\n- A bullet with a marker [c1, c2].\n- Unknown claim [c9].\n"
                "Trailing after a marker [c1]. And an uncited tail.\n\n## Empty\nAll uncited here.\n")
        clean, recs, stats, marked = P.check(text, CLAIMS)
        self.assertNotIn("no marker", clean.lower())
        self.assertNotIn("Unknown claim", clean)
        self.assertNotIn("uncited", clean)
        self.assertNotIn("## Empty", clean)           # a heading left with no content goes too
        self.assertNotIn("[c", clean)
        self.assertIn("They live in Brindle. They ride trains.", clean)
        self.assertIn("- A bullet with a marker.", clean)
        self.assertEqual(stats["kept"], 4)
        self.assertEqual(stats["dropped_unmarked"], 4)
        self.assertEqual(stats["dropped_unknown_marker"], 1)
        self.assertEqual(recs[2], {"sentence": "A bullet with a marker.", "claims": ["c1", "c2"], "ids": ["a", "b", "c"]})
        self.assertIn("They live in Brindle [c1].", marked)

    def test_abbreviations_and_bare_markers(self):
        clean, recs, _, _ = P.check("They use tools, e.g. Python, daily [c1]. They moved [c1][c2].", CLAIMS)
        self.assertEqual(clean.strip(), "They use tools, e.g. Python, daily. They moved.")
        self.assertEqual(recs[1]["claims"], ["c1", "c2"])


class TestTiers(unittest.TestCase):
    def test_round_trip(self):
        text = P.render_file("personal", "## Who they are\nThey live in Brindle.",
                             "## Opinions by topic\nTrains over planes.", {"u1": {"claim": "I keep bees."}})
        core, full = CC.person_tier(text, "core"), CC.person_tier(text, "full")
        self.assertTrue(core.startswith("# About me\n"))
        self.assertIn("They live in Brindle.", core)
        self.assertIn("## Stated by me\n- I keep bees.", core)
        self.assertNotIn("Trains over planes", core)
        self.assertTrue(full.startswith(core))
        self.assertIn("Trains over planes.", full)
        self.assertNotIn("<!--", full)

    def test_both_spellings_and_no_marker(self):
        research = ("# Someone\n\n<!-- tier:core -->\nCore line.\n<!-- /tier:core -->\n\n"
                    "<!-- tier:full -->\nFull line.\n<!-- /tier:full -->\n")
        self.assertEqual(CC.person_tier(research, "core"), "# Someone\n\nCore line.")
        self.assertEqual(CC.person_tier(research, "full"), "# Someone\n\nCore line.\n\nFull line.")
        spaced = research.replace("tier:full", "tier: full").replace("tier:core", "tier: core")
        self.assertEqual(CC.person_tier(spaced, "core"), "# Someone\n\nCore line.")
        self.assertEqual(CC.person_tier("Just one block.\n", "core"), "Just one block.")
        self.assertEqual(CC.person_tier("Just one block.\n", "full"), "Just one block.")
        only_core = "<!-- tier: core -->\nAll of it.\n"
        self.assertEqual(CC.person_tier(only_core, "core"), "All of it.")


class TestSplit(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(P.classify({"source": "teams", "register": "chat"}), "work")
        self.assertEqual(P.classify({"source": "outlook", "register": "email"}), "work")
        self.assertEqual(P.classify({"source": "claude_code", "register": "prompt"}), "work")
        self.assertEqual(P.classify({"source": "imessage", "register": "chat"}), "personal")
        self.assertEqual(P.classify({"source": "pasted", "register": "post"}), "personal")
        # docs and chat that may be work material are personal only when the user says so
        self.assertIsNone(P.classify({"source": "file", "register": "doc"}))
        self.assertEqual(P.classify({"source": "file", "register": "doc"}, include_unsorted=True), "personal")
        self.assertIsNone(P.classify({"source": "pasted", "register": "chat"}))
        self.assertIsNone(P.classify({"source": "learned", "register": "doc"}))
        self.assertEqual(P.classify({"source": "file", "register": "post"}), "personal")
        self.assertIsNone(P.classify({"source": "learned", "register": "chat"}, include_unsorted=True))
        self.assertIsNone(P.classify({"source": "learned", "register": "chat"}))
        self.assertIsNone(P.classify({"source": "email_file", "register": "email"}))


class TestBuild(PackCase):
    def test_dry_run_without_yes(self):
        out = self.cli("build", "--which", "both")
        self.mock_call.assert_not_called()
        self.assertIn("dry run", out)
        self.assertIn("personal -> person.md: 5 entries (posts 4, personal chat 1) in 2 chunks", out)
        self.assertIn("work -> person.work.md: 3 entries (Teams 3) in 1 chunks", out)
        self.assertRegex(out, r"total: about \d+ to \d+ model calls")
        for f in (P.FILES["personal"], P.FILES["work"], P.SIDECAR):
            self.assertFalse(os.path.exists(os.path.join(self.pack, f)), f)
        out = self.cli("build")
        self.assertIn("work -> person.work.md: not requested", out)

    def test_build_personal(self):
        out = self.cli("build", "--yes")
        self.assertTrue(self.mock_call.called)
        self.assertIn("person.md: 2 claims (1 single-source facts, 0 stated by you)", out)
        self.assertFalse(os.path.exists(os.path.join(self.pack, P.FILES["work"])))  # not asked for
        text = self.read("person.md")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.pack, "person.md")).st_mode), 0o600)
        self.assertNotIn("no marker", text)
        self.assertNotIn("Ghost claim", text)
        self.assertNotIn("## Humour", text)
        self.assertNotIn("[c", text)
        self.assertNotIn("bicycles", text)                 # one citation, not a fact: dropped
        self.assertNotIn("cooking", text)                  # one citation: dropped
        self.assertIn("They prefer trains over planes.", CC.person_tier(text, "core"))
        self.assertIn("airport", CC.person_tier(text, "full"))
        self.assertNotIn("airport", CC.person_tier(text, "core"))
        sc = read_json(os.path.join(self.pack, P.SIDECAR))
        fe = sc["files"]["person.md"]
        trains = next(c for c in fe["claims"].values() if "trains" in c["claim"])
        self.assertEqual(trains["ids"], ["pasted:p00", "pasted:p01", "pasted:p02"])  # merged + repaired
        town = next(c for c in fe["claims"].values() if c["kind"] == "fact")
        self.assertTrue(town["single_source"])
        self.assertTrue(all(r["ids"] for r in fe["sentences"]["core"]))
        # personal never sees work entries, and the ambiguous learned chat is in neither
        prompts = "".join(c.args[1] for c in self.mock_call.call_args_list)
        self.assertNotIn("teams:", prompts)
        self.assertNotIn("learned:", prompts)

    def test_work_file_uses_work_sources_only(self):
        self.cli("build", "--which", "work", "--yes")
        fe = read_json(os.path.join(self.pack, P.SIDECAR))["files"]["person.work.md"]
        ids = {i for c in fe["claims"].values() for i in c["ids"]}
        self.assertTrue(ids and all(i.startswith("teams:") for i in ids))
        self.assertTrue(self.read("person.work.md").startswith("# About me at work\n"))
        self.assertFalse(os.path.exists(os.path.join(self.pack, "person.md")))

    def test_blacklist_survives_rebuild(self):
        self.cli("build", "--yes")
        fe = read_json(os.path.join(self.pack, P.SIDECAR))["files"]["person.md"]
        cid = next(k for k, c in fe["claims"].items() if "trains" in c["claim"])
        out = self.cli("forget", cid)
        self.assertIn("forgot %s" % cid, out)
        self.assertNotIn("trains over planes", self.read("person.md"))
        bl = read_json(os.path.join(self.pack, P.BLACKLIST))
        self.assertEqual(bl["claims"][0]["text"], "they prefer trains over planes")
        self.cli("add", "--text", "I keep bees on the roof.")
        self.cli("build", "--yes")                          # rebuild: the model offers it again
        text = self.read("person.md")
        self.assertNotIn("trains over planes", text)
        self.assertIn("## Stated by me\n- I keep bees on the roof.", text)  # user claims survive too
        fe = read_json(os.path.join(self.pack, P.SIDECAR))["files"]["person.md"]
        self.assertFalse(any("trains" in c["claim"] for c in fe["claims"].values()))
        self.assertEqual(fe["stats"]["dropped_by_blacklist"], 2)  # both map copies
        # a reworded copy is caught too; an unrelated claim is not
        self.assertTrue(P.is_blacklisted("They prefer trains to planes.", "stance", P.load_blacklist(self.pack)))
        self.assertFalse(P.is_blacklisted("They live in Brindle.", "fact", P.load_blacklist(self.pack)))

    def test_forget_by_text_and_unknown(self):
        self.cli("build", "--yes")
        out = self.cli("forget", "They live in the town of Brindle")
        self.assertIn("matched by claim text", out)
        self.assertNotIn("Brindle", self.read("person.md"))
        out = self.cli("forget", "They hate opera.")
        self.assertIn("blacklisted the text", out)
        self.assertTrue(P.is_blacklisted("They hate opera", "stance", P.load_blacklist(self.pack)))
        with self.assertRaises(SystemExit):
            self.cli("forget", "c404")


class TestAddShowForget(PackCase):
    def test_add_show_forget_user_claim(self):
        out = self.cli("add", "--text", "I volunteer at the <!-- tier: full --> library [c1].")
        self.assertIn("added u1 to person.md", out)
        self.assertIn("already has", self.cli("add", "--text", "i volunteer at the library"))
        text = self.read("person.md")
        self.assertIn("- I volunteer at the library.", text)   # comment and marker syntax removed
        self.assertNotIn("<!-- tier: full -->\n", text)
        core = self.cli("show")
        self.assertIn("== person.md (core,", core)
        self.assertIn("I volunteer at the library.", core)
        self.assertIn("== person.work.md: none ==", core)
        self.assertIn("[u1] I volunteer at the library. (stated by you)", self.cli("show", "--ids", "--which", "personal"))
        self.cli("add", "--text", "I lead the platform guild.", "--work")
        self.assertIn("platform guild", self.read("person.work.md"))
        out = self.cli("forget", "u1")
        self.assertIn("1 stated claims removed", out)
        self.assertNotIn("library", self.read("person.md"))
        self.assertNotIn("## Stated by me", self.read("person.md"))
        sc = read_json(os.path.join(self.pack, P.SIDECAR))
        self.assertEqual(sc["files"]["person.md"]["user_claims"], {})
        # an explicit add overrides the earlier forget
        self.cli("add", "--text", "I volunteer at the library.")
        self.assertEqual(P.load_blacklist(self.pack)["claims"], [])

    def test_add_into_built_file_lands_in_core(self):
        self.cli("build", "--yes")
        self.cli("add", "--text", "I keep bees.")
        text = self.read("person.md")
        self.assertIn("I keep bees.", CC.person_tier(text, "core"))
        self.assertLess(text.index("I keep bees."), text.index("<!-- tier: full -->"))


class TestCallPath(unittest.TestCase):
    def test_calls_go_through_llm_call_without_api_key(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(env=kw["env"], input=kw["input"], cmd=cmd)
            return mock.Mock(returncode=0, stdout=json.dumps({"result": "[]"}), stderr="")

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-not-real"}), \
                mock.patch("shutil.which", return_value="/usr/local/bin/claude"), \
                mock.patch("subprocess.run", side_effect=run):
            out = P.Caller()("map", "SYSTEM", "REAL ENTRY TEXT", parse=lambda t: P.parse_json(t, list))
        self.assertEqual(out, [])
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertEqual(seen["input"], "REAL ENTRY TEXT")
        self.assertNotIn("REAL ENTRY TEXT", " ".join(seen["cmd"]))


if __name__ == "__main__":
    unittest.main()
