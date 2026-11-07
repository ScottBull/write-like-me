"""Pack format v2: the archive builder, hard-rules-only rules.md, and the v1 -> v2 upgrade.
Synthetic fixtures only (invented people, numbers and addresses)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
FIX = os.path.join(HERE, "fixtures")

from wlmlib import archive, build, rules  # noqa: E402
from wlmlib.common import CHARS_PER_TOKEN, PACK_VERSION, make_record, read_json, read_jsonl  # noqa: E402

KEEP_RULE = "## chat\n- Never mention the quokka budget <!-- lint:avoid=quokka budget -->"
LEARNED = build.LEARNED_HEADER + "\n## 2026-03-01 chat (to Dana)\n- shorter replies to Dana\n"


class PackCase(unittest.TestCase):
    def setUp(self):
        self.pack = tempfile.mkdtemp(prefix="wlm_arch_")
        os.makedirs(os.path.join(self.pack, "sources"))
        shutil.copy(os.path.join(FIX, "chat_records.jsonl"), os.path.join(self.pack, "sources", "synthetic.jsonl"))

    def tearDown(self):
        shutil.rmtree(self.pack, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.pack, *parts)

    def add_source(self, name, recs):
        with open(self.p("sources", name + ".jsonl"), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))

    def read(self, *parts):
        with open(self.p(*parts), encoding="utf-8") as f:
            return f.read()


class TestArchiveBuild(PackCase):
    def test_build_writes_archive_v2(self):
        stats = build.build(self.pack)
        self.assertTrue(os.path.exists(self.p("archive", "chat.jsonl")))
        self.assertFalse(os.path.exists(self.p("exemplars.jsonl")))
        entries = read_jsonl(self.p("archive", "chat.jsonl"))
        self.assertEqual(len(entries), 40)
        self.assertEqual(stats["pack_version"], PACK_VERSION)
        self.assertEqual(stats["archive"]["chat"],
                         {"entries": 40, "tokens": sum(e["tokens_est"] for e in entries)})
        self.assertEqual((stats["budget_default"], stats["strategy_default"]),
                         (build.BUDGET_DEFAULT, build.STRATEGY_DEFAULT))
        self.assertEqual(build.defaults(stats), (build.BUDGET_DEFAULT, build.STRATEGY_DEFAULT))
        self.assertEqual(build.defaults({}), (build.BUDGET_DEFAULT, build.STRATEGY_DEFAULT))
        self.assertNotIn("fixed_ids", stats["registers"]["chat"])
        self.assertNotIn("upgraded_from", stats)                         # a fresh pack is not an upgrade
        self.assertEqual(read_json(self.p("stats.json"))["pack_version"], 2)
        for e in entries:
            for k in ("id", "register", "timestamp", "text", "context", "recipient", "thread", "tokens_est"):
                self.assertIn(k, e)
            self.assertNotIn("flags", e)
        # load_pack serves the archive as the bank
        _, bank, _ = build.load_pack(self.pack)
        self.assertEqual([e["id"] for e in bank], [e["id"] for e in entries])
        self.assertIn("archive 40 entries", build.summary(stats))

    def test_masks_pii(self):
        self.add_source("more", [
            make_record("x", "chat", "ring me on 555-010-0199 or write to robin.fake@example.org",
                        rid="m1", timestamp="2026-04-01T10:00:00Z", context="what's your number? mine is 555 010 0123",
                        recipient="Robin Fake <robin.fake@example.org>"),
            make_record("x", "email", "Agenda attached, reply to casey@example.com", rid="m2",
                        timestamp="2026-04-02T10:00:00Z", subject="Call 555-010-0144 re: deck",
                        thread="Call 555-010-0144 re: deck", recipient="dana.lee@example.com"),
        ])
        build.build(self.pack)
        raw = self.read("archive", "chat.jsonl") + self.read("archive", "email.jsonl")
        self.assertNotIn("@", raw)
        for digits in ("0199", "0123", "0144"):
            self.assertNotIn(digits, raw)
        e = {x["id"]: x for x in archive.load_archive(self.pack)}
        self.assertIn("[phone]", e["m1"]["text"])
        self.assertIn("[email]", e["m1"]["text"])
        self.assertIn("[phone]", e["m1"]["context"])
        self.assertEqual(e["m1"]["recipient"], "Robin Fake")
        self.assertEqual(e["m2"]["recipient"], "dana.lee (example)")
        self.assertIn("[phone]", e["m2"]["subject"])

    def test_token_estimate(self):
        stats = build.build(self.pack)
        for e in archive.load_archive(self.pack, "chat"):
            chars = len(e["text"]) + len(e["context"] or "x" * archive.NO_CONTEXT_CHARS) + archive.ENTRY_OVERHEAD_CHARS
            self.assertEqual(e["tokens_est"], -(-chars // CHARS_PER_TOKEN))   # ceil(chars / 2.77)
            self.assertGreater(e["tokens_est"], len(e["text"]) / CHARS_PER_TOKEN)
        self.assertEqual(archive.tokens_est({"text": "x" * 277, "context": "y" * 277}),
                         -(-(554 + archive.ENTRY_OVERHEAD_CHARS) // 2.77))
        self.assertGreater(stats["archive"]["chat"]["tokens"], 0)

    def test_date_order(self):
        recs = read_jsonl(os.path.join(FIX, "chat_records.jsonl"))
        recs.reverse()
        recs.append(make_record("x", "chat", "no idea when I sent this one", rid="undated"))
        os.remove(self.p("sources", "synthetic.jsonl"))
        self.add_source("shuffled", recs)
        build.build(self.pack)
        ents = archive.load_archive(self.pack, "chat")
        self.assertEqual(ents[0]["id"], "undated")                       # unknown dates sort first, never "latest"
        ts = [e["timestamp"] for e in ents[1:]]
        self.assertEqual(ts, sorted(ts))
        self.assertLess(ts[0], ts[-1])

    def test_stale_register_removed(self):
        self.add_source("mail", [make_record("x", "email", "Thursday works for the review.", rid="e1",
                                             timestamp="2026-04-01T09:00:00Z")])
        build.build(self.pack)
        self.assertTrue(os.path.exists(self.p("archive", "email.jsonl")))
        os.remove(self.p("sources", "mail.jsonl"))
        stats = build.build(self.pack)
        self.assertFalse(os.path.exists(self.p("archive", "email.jsonl")))
        self.assertEqual(set(stats["archive"]), {"chat"})


class TestHardRules(PackCase):
    def test_rules_hard_only_profile_has_tendencies(self):
        stats = build.build(self.pack)
        rmd, pmd = self.read("rules.md"), self.read("profile.md")
        self.assertIn("Never use em dashes", rmd)
        self.assertIn("<!-- lint:length -->", rmd)
        self.assertIn("lint:avoid=", rmd)
        for gone in ("Tendencies", "Permitted imperfection", "lint:habit", "lint:density", "lint:polish",
                     "acknowledgment word"):
            self.assertNotIn(gone, rmd)
        self.assertIn("Tendencies", pmd)
        self.assertIn("acknowledgment word", pmd)
        self.assertIn("Permitted imperfection", pmd)
        self.assertNotIn("<!-- lint:", pmd)
        self.assertIn("## Archive", pmd)
        # lint's caricature counter reads these from stats.json now
        self.assertIn("ack_opener", stats["registers"]["chat"]["caricature_habits"])
        self.assertIsInstance(stats["registers"]["chat"]["spelling_prefs"], list)
        self.assertFalse(hasattr(rules, "choose_fixed"))

    def test_spelling_prefs_are_hard_rules(self):
        F = {"messages": 60, "variants": {"yeah": 9, "yea": 1, "ok": 2, "okay": 2}}
        self.assertEqual(rules.spelling_prefs(F), [{"use": "yeah", "not": ["yea", "ya"], "seen": 9, "of": 10}])
        md = rules.build_rules_md({"chat": F}, "now")
        self.assertIn(("prefer", "yeah:yea|ya"), rules.parse_rules(md, "chat"))


class TestMigration(PackCase):
    def make_v1(self):
        """A pack as plugin 0.1.x left it: no pack_version, exemplars.jsonl, fixed_ids, tendencies."""
        recs = read_jsonl(self.p("sources", "synthetic.jsonl"))
        with open(self.p("exemplars.jsonl"), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))
        with open(self.p("stats.json"), "w") as f:
            json.dump({"built": "2026-03-01T00:00:00Z", "version": "0.1.0", "masked_pii": True,
                       "registers": {"chat": {"n": 40, "sources": {"synthetic": 40}, "recipients": 3,
                                              "features": {"len_median": 8}, "fixed_ids": ["syn:000", "syn:001"]}}}, f)
        with open(self.p("rules.md"), "w") as f:
            f.write("# Voice rules\n\n## chat\n\n### Tendencies (match the frequency; never all at once)\n\n"
                    "- Ellipsis: about 1 in 9 messages. <!-- lint:habit=ellipsis -->\n\n"
                    + rules.KEEP_MARK + "\n\n" + KEEP_RULE + "\n")
        with open(self.p("learned.md"), "w") as f:
            f.write(LEARNED)
        with open(self.p("drafts.jsonl"), "w") as f:
            f.write(json.dumps({"register": "chat", "text": "Yep, on it", "ts": "2026-03-02T00:00:00Z"}) + "\n")
        os.chmod(self.p("sources"), 0o755)
        os.chmod(self.p("learned.md"), 0o644)

    def test_v1_to_v2(self):
        self.make_v1()
        self.assertTrue(build.needs_upgrade(read_json(self.p("stats.json"))))
        self.assertIn("run `wlm build` to upgrade", build.summary(read_json(self.p("stats.json"))))
        _, bank, _ = build.load_pack(self.pack)                          # v1 still loads before the upgrade
        self.assertEqual(len(bank), 40)
        drafts_before = self.read("drafts.jsonl")

        stats = build.build(self.pack)
        self.assertEqual(stats["pack_version"], 2)
        self.assertEqual(stats["upgraded_from"], 1)
        self.assertFalse(build.needs_upgrade(stats))
        self.assertFalse(os.path.exists(self.p("exemplars.jsonl")))
        self.assertEqual(len(read_jsonl(self.p("archive", "chat.jsonl"))), 40)
        self.assertNotIn("fixed_ids", json.dumps(read_json(self.p("stats.json"))))
        rmd = self.read("rules.md")
        self.assertIn("Never mention the quokka budget", rmd)          # KEEP section survives
        self.assertIn(("avoid", "quokka budget"), rules.parse_rules(rmd, "chat"))
        self.assertNotIn("lint:habit", rmd)                              # the v1 tendencies are gone
        self.assertIn("lint:habit=ellipsis", self.read("rules.prev.md"))
        self.assertEqual(self.read("learned.md"), LEARNED)
        self.assertEqual(self.read("drafts.jsonl"), drafts_before)
        self.assertIn("upgraded from pack format v1", build.summary(stats))
        # owner-only permissions are re-applied over the whole pack
        self.assertEqual(os.stat(self.pack).st_mode & 0o777, 0o700)
        for root, dirs, fs in os.walk(self.pack):
            for d in dirs:
                self.assertEqual(os.stat(os.path.join(root, d)).st_mode & 0o777, 0o700, d)
            for f in fs:
                self.assertEqual(os.stat(os.path.join(root, f)).st_mode & 0o777, 0o600, f)
        # a second build is a plain v2 rebuild: defaults kept, no longer reported as an upgrade
        stats2 = build.build(self.pack, budget_default=20000)
        self.assertNotIn("upgraded_from", stats2)
        self.assertEqual(build.build(self.pack)["budget_default"], 20000)

    def test_defaults_follow_the_constants_unless_the_user_set_them(self):
        from unittest import mock
        build.build(self.pack)
        with mock.patch.object(build, "BUDGET_DEFAULT", 7000), mock.patch.object(build, "STRATEGY_DEFAULT", "M"):
            st = build.build(self.pack)          # the build's own values follow a plugin update
            self.assertEqual((st["budget_default"], st["strategy_default"]), (7000, "M"))
        st = read_json(self.p("stats.json"))
        st["strategy_default"] = "T"            # the user edits stats.json by hand
        with open(self.p("stats.json"), "w") as f:
            json.dump(st, f)
        st = build.build(self.pack)
        self.assertEqual((st["budget_default"], st["strategy_default"]), (build.BUDGET_DEFAULT, "T"))
        self.assertEqual(build.build(self.pack)["strategy_default"], "T")
        # a 0.2.0 development pack (no defaults_auto) with the old placeholder values is moved on
        st.pop("defaults_auto")
        st.update(budget_default=50000, strategy_default="D")
        with open(self.p("stats.json"), "w") as f:
            json.dump(st, f)
        st = build.build(self.pack)
        self.assertEqual((st["budget_default"], st["strategy_default"]),
                         (build.BUDGET_DEFAULT, build.STRATEGY_DEFAULT))


if __name__ == "__main__":
    unittest.main()
