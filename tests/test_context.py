"""Context assembly (select.assemble) and `wlm context` (cli_context). Synthetic fixtures only."""
import argparse
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
FIX = os.path.join(HERE, "fixtures")

from wlmlib import build, cli_context as CC, select as S  # noqa: E402
from wlmlib.common import read_json, read_jsonl, write_jsonl  # noqa: E402

E = [
    {"id": "x2", "register": "chat", "timestamp": "2026-02-03T10:00:00Z", "recipient": "Riley Sample",
     "thread": "r", "context": "is the {kettle}\nfixed?", "text": "yes, new one on the counter"},
    {"id": "x1", "register": "chat", "timestamp": "2025-11-20T10:00:00Z", "recipient": "Jordan Example",
     "thread": "j", "context": "", "text": "heading out early today"},
]
PERSON = ("I manage an invented bakery's front office.\n<!-- note for me -->\n\n"
          "<!-- tier: full -->\nOpinions: sourdough over rye.\n")


class TestAssemble(unittest.TestCase):
    def test_system_file_format(self):
        sys_text, task = S.assemble("", E, "chat", {"notes": "it's done", "context": "is it done", "recipient": "Riley Sample",
                                                    "timestamp": "2026-03-01"})
        self.assertTrue(sys_text.startswith("This is a selection of my chat messages from 2025 to 2026, in the order I sent them."))
        for marker in ("=== ARCHIVE ===", "=== END OF ARCHIVE ===", "Write that message so it reads exactly like the next entry"):
            self.assertEqual(sys_text.count(marker), 1, marker)
        self.assertNotIn("ABOUT ME", sys_text)
        # date order, pilot format, braces in real text kept as is
        self.assertLess(sys_text.index("### to Jordan Example, 2025-11-20"), sys_text.index("### to Riley Sample, 2026-02-03"))
        self.assertIn("### to Jordan Example, 2025-11-20\nReplying to: (nothing: I started this one)\nMy message:\n"
                      "heading out early today", sys_text)
        self.assertIn("Replying to: is the {kettle} fixed?\nMy message:\nyes, new one", sys_text)
        # task: the framing once, then the new entry with the notes and an empty body
        self.assertEqual(task.count("as the next entry in the archive"), 1)
        self.assertTrue(task.endswith("### to Riley Sample, 2026-03-01\nReplying to: is it done\n"
                                      "What I want to say (my notes, not my wording): it's done\nMy message:"))
        self.assertNotIn("=== USER ===", task)

    def test_person_block_and_complete(self):
        sys_text, _ = S.assemble("I bake.", E, "post", "already rendered task", complete=True)
        self.assertTrue(sys_text.startswith("This is the complete archive of my posts and comments"))
        self.assertEqual(sys_text.count("=== ABOUT ME ===\nI bake.\n=== END OF ABOUT ME ==="), 1)
        self.assertLess(sys_text.index("ABOUT ME"), sys_text.index("=== ARCHIVE ==="))
        self.assertEqual(S.person_tokens("I bake."), S.est_tokens(len(S.person_block_text("I bake."))))
        self.assertEqual(S.person_tokens(""), 0)

    def test_register_wording(self):
        self.assertIn("Replying to: (nothing: this is a new post)",
                      S.render_task({"notes": "n", "recipient": "r/bikes"}, "post"))
        self.assertTrue(S.render_task({"notes": "n"}, "post").endswith("My comment:"))
        e = dict(E[0], register="email", subject="Kettle")
        self.assertIn("### to Riley Sample, 2026-02-03, subject: Kettle\nReplying to:", S.render_entry(e))
        self.assertTrue(S.render_entry(dict(E[0], register="prompt")).startswith("### 2026-02-03\n"))
        self.assertIn("Following: (nothing: this is where it starts)", S.render_entry(dict(E[1], register="doc")))
        self.assertEqual(S.render_task({"notes": "n"}, "chat").splitlines()[0], "### undated")


class TestPersonTiers(unittest.TestCase):
    def test_tiers(self):
        core = CC.person_tier(PERSON, "core")
        full = CC.person_tier(PERSON, "full")
        self.assertEqual(core, "I manage an invented bakery's front office.")
        self.assertIn("Opinions: sourdough over rye.", full)
        self.assertTrue(full.startswith(core))
        self.assertNotIn("<!--", full)
        self.assertEqual(CC.person_tier(PERSON, "none"), "")
        self.assertEqual(CC.person_tier("All core.\n", "full"), "All core.")
        self.assertEqual(CC.person_tier("All core.\n", "core"), "All core.")

    def test_tier_marker_spellings(self):
        # person.py writes "<!-- tier: core -->" / "<!-- tier: full -->"; the research build wrote
        # "<!-- tier:core --> ... <!-- /tier:core -->" then "<!-- tier:full --> ... <!-- /tier:full -->"
        for full_mark, extra in (("<!-- tier: full -->", ""), ("<!-- tier:full -->", "<!-- /tier:core -->\n"),
                                 ("<!-- TIER:FULL -->", "")):
            text = "# Me\n<!-- tier: core -->\nCore.\n%s\n%s\nFull.\n<!-- /tier:full -->\n" % (extra, full_mark)
            self.assertEqual(CC.person_tier(text, "core"), "# Me\n\nCore.", full_mark)
            self.assertEqual(CC.person_tier(text, "full"), "# Me\n\nCore.\n\nFull.", full_mark)
        self.assertEqual(CC.person_tier("Core.\n<!-- /tier:full -->\nStill core.\n", "core"), "Core.\n\nStill core.")


class PackCase(unittest.TestCase):
    def setUp(self):
        self.pack = tempfile.mkdtemp(prefix="wlm_ctx_")
        os.makedirs(os.path.join(self.pack, "sources"))
        shutil.copy(os.path.join(FIX, "chat_records.jsonl"), os.path.join(self.pack, "sources", "synthetic.jsonl"))
        build.build(self.pack)
        self.old_env = os.environ.get("WRITE_LIKE_ME_PACK")
        os.environ["WRITE_LIKE_ME_PACK"] = self.pack

    def tearDown(self):
        if self.old_env is None:
            os.environ.pop("WRITE_LIKE_ME_PACK", None)
        else:
            os.environ["WRITE_LIKE_ME_PACK"] = self.old_env
        shutil.rmtree(self.pack, ignore_errors=True)

    def write(self, name, text):
        with open(os.path.join(self.pack, name), "w", encoding="utf-8") as f:
            f.write(text)

    def run_cli(self, *argv):
        ap = argparse.ArgumentParser()
        sp = ap.add_subparsers(dest="cmd", required=True)
        CC.add_parser(sp)
        a = ap.parse_args(["context"] + list(argv))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            a.fn(a)
        return out.getvalue()


class TestContextCommand(PackCase):
    def test_writes_private_files_and_counts(self):
        self.write("person.md", PERSON)
        out = self.run_cli("--register", "chat", "--notes", "yes, still on", "--context", "is the demo still on",
                           "--recipient", "Jordan Example", "--budget", "600", "--person", "core")
        lines = dict(l.split(": ", 1) for l in out.strip().splitlines() if ": " in l)
        sp, tp = lines["system"], lines["task"]
        d = os.path.dirname(sp)
        self.assertEqual(os.path.dirname(d), os.path.join(self.pack, "work"))
        self.assertTrue(os.path.basename(d).startswith("ctx-"))
        for p in (sp, tp, os.path.join(d, "context.json")):
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
        info = read_json(os.path.join(d, "context.json"))
        self.assertEqual((info["strategy"], info["budget"], info["person"]), (build.STRATEGY_DEFAULT, 600, "core"))
        self.assertLessEqual(info["tokens"], 600)
        self.assertGreater(info["entries"], 0)
        self.assertIn("person file: core (person.md)", out)
        self.assertIn("entries: %d of 40" % info["entries"], out)
        with open(sp, encoding="utf-8") as f:
            system = f.read()
        self.assertIn("I manage an invented bakery's front office.", system)
        self.assertNotIn("sourdough", system)
        self.assertEqual(system.count("### "), info["entries"])
        with open(tp, encoding="utf-8") as f:
            self.assertIn("What I want to say (my notes, not my wording): yes, still on", f.read())
        self.assertNotIn("yes, still on", json.dumps(info))           # context.json holds no message text

    def test_defaults_from_stats_and_same_inputs_same_dir(self):
        st = read_json(os.path.join(self.pack, "stats.json"))
        st.update(budget_default=400, strategy_default="T", person_default="none")
        with open(os.path.join(self.pack, "stats.json"), "w") as f:
            json.dump(st, f)
        self.write("person.work.md", PERSON)
        a = CC.build_context(self.pack, "chat", "notes here", today="2026-03-05")
        b = CC.build_context(self.pack, "chat", "notes here", today="2026-03-05")
        self.assertEqual((a["strategy"], a["budget"], a["person"]), ("T", 400, "none"))
        self.assertEqual(a["dir"], b["dir"])
        self.assertEqual(a["ids"], b["ids"])
        full = CC.build_context(self.pack, "chat", "notes here", person="full", strategy="P", today="2026-03-05")
        self.assertEqual((full["person"], full["person_files"]), ("full", ["person.work.md"]))
        self.assertLessEqual(full["tokens"], 400)
        self.assertNotEqual(full["dir"], a["dir"])

    def test_fallback_register_and_no_person(self):
        out = self.run_cli("--register", "email", "--notes", "review moved to friday", "--person", "core")
        self.assertIn("note: no email samples in the pack; using chat samples instead", out)
        self.assertIn("person file: none", out)
        sp = [l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("system: ")][0]
        with open(sp, encoding="utf-8") as f:
            self.assertIn("the complete archive of my chat messages from 2026", f.read())   # 40 entries fit 50k

    def test_learned_notes_in_the_system_file_and_the_budget(self):
        with open(os.path.join(self.pack, "learned.md"), "a", encoding="utf-8") as f:
            f.write("\n## 2026-03-01 chat (to Jordan Example)\n- shorter replies to Jordan\n"
                    "\n## 2026-03-02 email\n- no exclamation marks in email\n"
                    "\n## 2026-03-03 all\n- write okay, not ok\n")
        plain = CC.build_context(self.pack, "chat", "ok", recipient="Riley Sample", budget=600, strategy="D",
                                 person="none", today="2026-03-05", habits="off")
        info = CC.build_context(self.pack, "chat", "ok", recipient="Jordan Example", budget=600, strategy="D",
                                person="none", today="2026-03-05", habits="off")
        with open(info["system"], encoding="utf-8") as f:
            system = f.read()
        self.assertEqual(system.count("=== NOTES FROM MY EDITS ==="), 1)
        self.assertLess(system.index("=== END OF ARCHIVE ==="), system.index("=== NOTES FROM MY EDITS ==="))
        self.assertIn("- shorter replies to Jordan\n- write okay, not ok\n=== END OF NOTES ===", system)
        for absent in ("no exclamation marks", "## 2026", "Notes from comparing drafts", "{learned"):
            self.assertNotIn(absent, system)
        notes = "- shorter replies to Jordan\n- write okay, not ok"
        self.assertEqual(info["learned_tokens"], S.learned_tokens(notes))
        self.assertGreater(info["learned_tokens"], 0)
        self.assertEqual(info["tokens"], info["archive_tokens"] + info["learned_tokens"])
        self.assertLessEqual(info["tokens"], 600)
        self.assertLessEqual(info["archive_tokens"], 600 - info["learned_tokens"])
        # what is counted is what is sent: the system file is the same one without the block
        by_id = {e["id"]: e for e in read_jsonl(os.path.join(self.pack, "archive", "chat.jsonl"))}
        picked = [by_id[i] for i in info["ids"]]
        task = {"notes": "ok", "recipient": "Jordan Example", "timestamp": "2026-03-05"}
        complete = len(picked) == len(by_id)
        bare, _ = S.assemble("", picked, "chat", task, complete=complete)
        full, _ = S.assemble("", picked, "chat", task, complete=complete, learned_text=notes)
        self.assertEqual(full, system)
        self.assertEqual(len(full) - len(bare), len(S.learned_block_text(notes)))
        with open(plain["system"], encoding="utf-8") as f:
            other = f.read()
        self.assertIn("- write okay, not ok", other)                  # "all" applies to everyone
        self.assertNotIn("shorter replies to Jordan", other)          # a note for Jordan only reaches Jordan
        # strategy P takes the notes off the budget as well
        p = CC.build_context(self.pack, "chat", "ok", recipient="Jordan Example", budget=600, strategy="P",
                             person="none", today="2026-03-05", habits="off")
        self.assertLessEqual(p["tokens"], 600)
        self.assertEqual(p["learned_tokens"], info["learned_tokens"])

    def test_p_without_person_file_notes_it(self):
        info = CC.build_context(self.pack, "chat", "ok", strategy="P", budget=500)
        self.assertTrue(any("strategy P needs a person file" in n for n in info["notes"]))

    def test_errors(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--register", "chat")                         # no notes
        shutil.rmtree(os.path.join(self.pack, "archive"))
        with self.assertRaises(ValueError):
            CC.build_context(self.pack, "chat", "x")                    # v1 pack
        os.makedirs(os.path.join(self.pack, "archive"))
        write_jsonl(os.path.join(self.pack, "archive", "chat.jsonl"), [])
        with self.assertRaises(ValueError):
            CC.build_context(self.pack, "chat", "x")                    # nothing at all


if __name__ == "__main__":
    unittest.main()
