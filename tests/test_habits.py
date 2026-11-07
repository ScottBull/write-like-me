"""The "How my messages vary" block (habits.py) and its --habits flag. Synthetic stats only."""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
FIX = os.path.join(HERE, "fixtures")

from wlmlib import build, check, cli_context as CC, compose, habits as H, lint, select as S  # noqa: E402
from wlmlib.common import read_json, read_jsonl  # noqa: E402

F = {"messages": 400, "len_median": 21, "len_p25": 12.2, "len_p75": 34.6, "len_p90": 60,
     "share_end_none": 0.21, "share_msgs_ack_opener": 0.27, "share_msgs_exclaim": 0.16, "share_msgs_hedge": 0.33,
     "hedges": {"just": 9.1, "i think": 6.0, "maybe": 4.2, "probably": 1.0},
     "top_openers": [["yea", 0.09], ["thanks", 0.05], ["sure", 0.04], ["so", 0.03], ["oh", 0.02]],
     "per100w_em_dash": 0, "per100w_en_dash": 0, "per100w_double_hyphen": 0, "share_msgs_semicolon": 0,
     "share_msgs_line_break": 0.1, "share_msgs_greeting": 0.1, "share_msgs_signoff": 0.1,
     "share_msgs_closer_offer": 0.1, "share_msgs_bullets": 0.05, "share_msgs_emoji": 0.05,
     "per100w_repeated_bang": 0.1, "variants": {"please": 3}}
STATS = {"registers": {"chat": {"features": F}}}
IMPERATIVE = re.compile(r"\b(?:always|never|don'?t|do not|must|should|avoid|make sure|try to)\b", re.I)


class TestBlock(unittest.TestCase):
    def test_renders_frequencies_from_stats(self):
        text = H.habits_text(STATS, "chat")
        lines = text.splitlines()
        self.assertTrue(4 <= len(lines) <= 6, lines)
        self.assertEqual(lines[0], "Across my real chat messages, about half are 12-35 words long (the median is 21).")
        self.assertIn("About 1 in 5 has no final punctuation.", lines)
        self.assertIn("About 1 in 4 opens with a short acknowledgment (Yea, Thanks, Sure) and the rest get straight "
                      "to the point.", lines)
        self.assertIn("About 1 in 6 has an exclamation mark, and about 1 in 3 has a hedge (just, I think, maybe).", lines)
        self.assertIn("None of them has a dash (em, en or double hyphen) or a semicolon.", lines)   # hard rules
        self.assertEqual(lines[-1], H.CLOSING)
        self.assertNotRegex(text, IMPERATIVE)            # frequencies only, nothing addressed to the drafter
        self.assertNotRegex(text, r"[—–]")
        self.assertNotIn("Oh", text)                     # only the top three acknowledgment openers

    def test_marks_follow_rules_md_and_ranges(self):
        rules = "## all\n\n## chat\n\n- Never use semicolons. <!-- lint:semicolon -->\n"
        self.assertEqual(H.hard_marks(STATS, "chat", rules), ["semicolon"])     # the user deleted the dash rule
        self.assertEqual(H.hard_marks(STATS, "chat"), ["dash", "semicolon"])    # as build would write them
        self.assertNotIn("dash", H.habits_text(STATS, "chat", rules))
        g = dict(F, share_end_none=0.0, share_msgs_ack_opener=0.5, share_msgs_exclaim=0.0, share_msgs_hedge=0.01,
                 top_openers=[])
        lines = H.habit_lines(g, "chat", ["exclaim"])
        self.assertFalse(any("punctuation" in l or "hedge" in l for l in lines), lines)
        self.assertIn("About half open with a short acknowledgment and the rest get straight to the point.", lines)
        self.assertIn("None of them has an exclamation mark.", lines)
        self.assertEqual(H.habit_lines(dict(F, messages=12), "chat"), [])      # too few messages to say anything
        self.assertEqual(H.habits_text({}, "chat"), "")
        self.assertIn("Almost all of them have no final punctuation.", H.habit_lines(dict(F, share_end_none=0.96)))
        self.assertIn("About 1 in 6 has an exclamation mark, and about half have a hedge (just, I think, maybe).",
                      H.habit_lines(dict(F, share_msgs_hedge=0.5)))

    def test_block_placed_after_archive_and_notes(self):
        text = H.habits_text(STATS, "chat")
        sys_text, _ = S.assemble("", [{"id": "a", "register": "chat", "timestamp": "2026-01-02", "text": "ok"}],
                                 "chat", "task", learned_text="- shorter", habits_text=text)
        i_end, i_notes = sys_text.index("=== END OF ARCHIVE ==="), sys_text.index("=== NOTES FROM MY EDITS ===")
        i_hab = sys_text.index("=== HOW MY MESSAGES VARY ===")
        self.assertLess(i_end, i_notes)
        self.assertLess(i_notes, i_hab)
        self.assertIn("=== HOW MY MESSAGES VARY ===\n%s\n=== END OF HOW MY MESSAGES VARY ===" % text, sys_text)
        bare, _ = S.assemble("", [{"id": "a", "register": "chat", "timestamp": "2026-01-02", "text": "ok"}],
                             "chat", "task", learned_text="- shorter")
        self.assertEqual(len(sys_text) - len(bare), len(S.habits_block_text(text)))
        self.assertEqual(S.habits_tokens(text), S.est_tokens(len(S.habits_block_text(text))))
        self.assertEqual(S.habits_tokens(""), 0)
        self.assertEqual(compose.habits_block(""), "")

    def test_default_resolution(self):
        self.assertEqual(build.habits_default({}), build.HABITS_DEFAULT)
        self.assertEqual(build.habits_default({"habits_default": "off"}), "off")
        self.assertEqual(build.habits_default({"habits_default": False}), "off")
        self.assertEqual(build.habits_default({"habits_default": "off"}, "on"), "on")
        with self.assertRaises(ValueError):
            build.habits_default({"habits_default": "maybe"})


class TestFlag(unittest.TestCase):
    def setUp(self):
        self.pack = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.pack, "sources"))
        shutil.copy(os.path.join(FIX, "chat_records.jsonl"), os.path.join(self.pack, "sources", "synthetic.jsonl"))
        self.stats = build.build(self.pack)

    def tearDown(self):
        shutil.rmtree(self.pack, ignore_errors=True)

    def test_counted_in_budget_and_flag_turns_it_off(self):
        on = CC.build_context(self.pack, "chat", "ok", budget=600, strategy="D", today="2026-03-05", habits="on")
        off = CC.build_context(self.pack, "chat", "ok", budget=600, strategy="D", today="2026-03-05", habits="off")
        with open(on["system"], encoding="utf-8") as f:
            sys_on = f.read()
        with open(off["system"], encoding="utf-8") as f:
            sys_off = f.read()
        text = H.habits_text(self.stats, "chat", CC.load_rules(self.pack))
        self.assertTrue(text)
        self.assertIn(text, sys_on)
        self.assertNotIn("HOW MY MESSAGES VARY", sys_off)
        self.assertEqual((on["habits"], off["habits"]), ("on", "off"))
        self.assertEqual(on["habits_tokens"], S.habits_tokens(text))
        self.assertEqual(off["habits_tokens"], 0)
        self.assertEqual(on["tokens"], on["archive_tokens"] + on["habits_tokens"])
        self.assertLessEqual(on["tokens"], 600)
        self.assertLessEqual(on["archive_tokens"], 600 - on["habits_tokens"])
        self.assertLessEqual(on["archive_tokens"], off["archive_tokens"])
        # the default follows stats.json, then HABITS_DEFAULT
        self.assertEqual(CC.build_context(self.pack, "chat", "ok", budget=600, today="2026-03-05")["habits"],
                         build.HABITS_DEFAULT)
        st = read_json(os.path.join(self.pack, "stats.json"))
        st["habits_default"] = "off"
        with open(os.path.join(self.pack, "stats.json"), "w") as f:
            json.dump(st, f)
        self.assertEqual(CC.build_context(self.pack, "chat", "ok", budget=600, today="2026-03-05")["habits"], "off")
        # strategy P takes it off the budget too
        p = CC.build_context(self.pack, "chat", "ok", budget=600, strategy="P", today="2026-03-05", habits="on")
        self.assertLessEqual(p["tokens"], 600)
        self.assertEqual(p["habits_tokens"], on["habits_tokens"])

    def test_too_small_a_budget_drops_it_with_a_note(self):
        info = CC.build_context(self.pack, "chat", "ok", budget=50, today="2026-03-05", habits="on")
        self.assertEqual((info["habits"], info["habits_tokens"]), ("off", 0))
        self.assertTrue(any("habits block" in n for n in info["notes"]))

    def test_cli_flags(self):
        ap = argparse.ArgumentParser()
        sp = ap.add_subparsers(dest="cmd", required=True)
        CC.add_parser(sp)
        check.add_parser(sp)
        self.assertEqual(ap.parse_args(["context", "--register", "chat", "--habits", "off"]).habits, "off")
        self.assertIsNone(ap.parse_args(["context", "--register", "chat"]).habits)
        self.assertEqual(ap.parse_args(["check", "generate", "--habits", "on"]).habits, "on")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ap.parse_args(["context", "--register", "chat", "--habits", "maybe"])

    def test_check_voice_context_and_heldout_numbers(self):
        entries = read_jsonl(os.path.join(self.pack, "archive", "chat.jsonl"))
        items = entries[:3]
        text = check.heldout_habits(entries, items, "chat", self.stats)
        self.assertTrue(text.startswith("Across my real chat messages"))
        sys_on, _, picked_on = check.voice_context(entries, items[0], items, "notes", "chat", 600, "D",
                                                   habits_text=text)
        sys_off, _, picked_off = check.voice_context(entries, items[0], items, "notes", "chat", 600, "D")
        self.assertIn(text, sys_on)
        self.assertNotIn("HOW MY MESSAGES VARY", sys_off)
        self.assertLessEqual(S.total_tokens(picked_on) + S.habits_tokens(text), 600)

    def test_lint_reports_length_against_the_median(self):
        rules = CC.load_rules(self.pack)
        med = round(self.stats["registers"]["chat"]["features"]["len_median"])
        res = lint.lint(" ".join(["word"] * 40), "chat", rules, self.stats)
        msg = [f for f in res["findings"] if f["code"] == "length"][0]
        self.assertEqual(msg["level"], "info")
        self.assertIn("40 words against your median of %d" % med, msg["message"])
        self.assertIn("(your median %d)" % med, lint.format_report(res).splitlines()[0])
        short = lint.lint("ok", "chat", rules, self.stats)
        self.assertNotIn("length", {f["code"] for f in short["findings"]})


if __name__ == "__main__":
    unittest.main()
