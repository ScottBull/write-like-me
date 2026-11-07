"""Offline tests for the pure functions. All fixtures are synthetic (tests/fixtures/, invented).

Run:  python3 -m unittest discover -s tests      (or: python3 -m pytest tests)
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from wlmlib import build, check, clean, compose, learn, lint, retrieve, rules, stylo, textutil  # noqa: E402
from wlmlib.common import make_record, read_jsonl  # noqa: E402
from wlmlib.sources import claude_code, files, imessage, paste, teams  # noqa: E402


def chat_records():
    return read_jsonl(os.path.join(FIX, "chat_records.jsonl"))


class TempPack(unittest.TestCase):
    def setUp(self):
        self.pack = tempfile.mkdtemp(prefix="wlm_test_")
        os.makedirs(os.path.join(self.pack, "sources"))
        shutil.copy(os.path.join(FIX, "chat_records.jsonl"), os.path.join(self.pack, "sources", "synthetic.jsonl"))
        self._env = os.environ.get("WRITE_LIKE_ME_PACK")
        os.environ["WRITE_LIKE_ME_PACK"] = self.pack

    def tearDown(self):
        shutil.rmtree(self.pack, ignore_errors=True)
        if self._env is None:
            os.environ.pop("WRITE_LIKE_ME_PACK", None)
        else:
            os.environ["WRITE_LIKE_ME_PACK"] = self._env


class TestText(unittest.TestCase):
    def test_normalise_and_words(self):
        t = textutil.normalise("see https://example.com and `code` from a@b.co in ./src/app/main.py")
        self.assertNotIn("example.com", t)
        self.assertEqual(textutil.words("It's fine, isn't it?"), ["it's", "fine", "isn't", "it"])

    def test_sentences(self):
        self.assertEqual(len(textutil.sentences("One. two? Three!\nfour")), 4)

    def test_mask(self):
        m = textutil.mask_pii("mail me at someone@example.org or 555-010-0199")
        self.assertIn("[email]", m)
        self.assertIn("[phone]", m)


class TestClean(unittest.TestCase):
    def test_split_parent(self):
        body = "Sounds good.\n\nOn Mon, Mar 2, 2026 at 4:01 PM Someone <x@example.org> wrote:\n> old text"
        a, q = clean.split_parent(body)
        self.assertEqual(a.strip(), "Sounds good.")
        self.assertEqual(clean.parent_excerpt(q), "old text")

    def test_from_prose_not_cut(self):
        a, _ = clean.split_parent("From: my side it's fine.\nMore text")
        self.assertIn("More text", a)

    def test_signature_block_removed(self):
        body = "Hi Jordan,\n\nFriday is fine.\n\nThanks,\nPat\nRobin Sample | Planner\nExample Co.\n+1 555 010 0199"
        c = clean.clean_email_body(body, ["Robin"])
        self.assertTrue(c["text"].endswith("Thanks,\nPat"))
        self.assertEqual(c["greeting"], "Hi Jordan,")
        self.assertIn("Thanks", c["signoff"])

    def test_trailing_url_kept(self):
        body = "Two examples:\nhttps://example.com/a\n\nSecond one:\nhttps://example.com/b"
        self.assertTrue(clean.clean_email_body(body)["text"].endswith("https://example.com/b"))
        sig = "Fine by me.\n\nRobin Sample | Planner\nhttps://example.com"
        self.assertEqual(clean.clean_email_body(sig)["text"], "Fine by me.")

    def test_html_skips_quotes(self):
        h = "<p>new words</p><blockquote>quoted</blockquote><div class='gmail_quote'>old</div>"
        self.assertEqual(clean.html_to_text(h), "new words")

    def test_paste_flags(self):
        self.assertIn("possible_inline_paste", clean.paste_flags("look\n```\ncode\n```"))
        self.assertEqual(clean.paste_flags("just a normal sentence"), set())


class TestStylo(unittest.TestCase):
    def test_markers(self):
        m = stylo.message_markers("Yep, sounds good - see you then")
        self.assertTrue(m["ack_opener"])
        self.assertTrue(m["spaced_hyphen"])
        self.assertTrue(m["no_end_punct"])
        self.assertFalse(m["exclaim"])

    def test_features_shape(self):
        F = stylo.features([r["text"] for r in chat_records()])
        self.assertEqual(F["messages"], 40)
        self.assertGreater(F["len_p90"], F["len_median"])
        self.assertEqual(F["per100w_em_dash"], 0)
        self.assertTrue(0 < F["share_msgs_ack_opener"] < 0.5)
        self.assertIn(F["imperfection_level"], ("low", "medium", "high"))
        self.assertGreater(F["share_end_none"], 0.5)

    def test_missing_apostrophe(self):
        F = stylo.features(["dont worry about it", "I don't know", "thats fine"])
        self.assertAlmostEqual(F["share_contractions_missing_apostrophe"], 2 / 3.0, places=3)


class TestRules(unittest.TestCase):
    def test_freq_phrase(self):
        self.assertEqual(rules.freq_phrase(0.26), "about 1 in 4")
        self.assertEqual(rules.freq_of(0.97), "almost all messages")
        self.assertEqual(rules.freq_of(0), "no messages")
        self.assertTrue(rules.freq_phrase(0.7).startswith("most"))

    def test_rules_are_frequencies_not_commands(self):
        F = stylo.features([r["text"] for r in chat_records()])
        never, length, tend, imperf = rules.register_rules("chat", F)
        self.assertTrue(any("lint:dash" in x for x in never))
        ack = [t for t in tend if "acknowledgment" in t][0]
        self.assertIn("1 in", ack)
        self.assertIn("don't add one by default", ack)
        self.assertTrue(any("Median" in x for x in length))
        self.assertTrue(any("imperfection" in x for x in imperf))

    def test_no_never_rules_with_little_data(self):
        F = stylo.features(["ok", "sure thing"])
        never, _, _, _ = rules.register_rules("chat", F)
        self.assertEqual(never, [])

    def test_keep_section_survives(self):
        F = {"chat": stylo.features([r["text"] for r in chat_records()])}
        md = rules.build_rules_md(F, "now", keep_text="## chat\n- Never say synergy <!-- lint:avoid=synergy -->")
        _, keep = rules.split_keep(md)
        self.assertIn("synergy", keep)
        self.assertIn(("avoid", "synergy"), rules.parse_rules(md, "chat"))
        self.assertNotIn(("avoid", "synergy"), rules.parse_rules(md, "email"))
        self.assertNotIn("<!--", rules.rules_section(md, "chat"))

    def test_choose_fixed_keeps_habit_frequency(self):
        recs = chat_records()
        ids = rules.choose_fixed(recs, k=6)
        self.assertEqual(len(ids), 6)
        by = {r["id"]: r for r in recs}
        acks = sum(stylo.message_markers(by[i]["text"])["ack_opener"] for i in ids)
        self.assertLessEqual(acks, 2)
        self.assertGreaterEqual(len({by[i]["recipient"] for i in ids}), 3)

    def test_choose_fixed_shows_habits_not_flat_or_link_messages(self):
        # Synthetic: 30 flat habit-free messages, 30 with the person's hedges / acks / slashes,
        # and link-only, attachment lead-in and group-chat messages that should never be picked.
        recs = []
        for i in range(30):
            recs.append(make_record("s", "chat", "I sent the file to the vendor %d today." % i, rid="flat%d" % i,
                                    recipient="P%d" % (i % 7), context="any news?"))
            recs.append(make_record("s", "chat", ["Yea, probably fine to ship the v%d build / notes then" % i,
                                                  "I think it's just the export step %d, maybe rerun it?" % i,
                                                  "Pretty sure the deck %d is done (mostly) - will check" % i][i % 3],
                                    rid="voice%d" % i, recipient="Q%d" % (i % 7), context="status?"))
            recs.append(make_record("s", "chat", "Here's the link for the board %d: https://example.com/b%d" % (i, i),
                                    rid="link%d" % i, recipient="R", context="link?"))
            recs.append(make_record("s", "chat", "Here's the updated checklist for round %d:" % i, rid="lead%d" % i,
                                    recipient="R", context="list?"))
            r = make_record("s", "chat", "Probably fine, I think we can wait on batch %d" % i, rid="grp%d" % i,
                            recipient="Team room", context="wait?")
            r["chat_type"] = "group"
            recs.append(r)
        ids = rules.choose_fixed(recs, k=6)
        self.assertEqual(len(ids), 6)
        self.assertFalse([i for i in ids if i.startswith(("link", "lead", "grp"))], ids)
        by = {r["id"]: r for r in recs}
        hedged = sum(stylo.message_markers(by[i]["text"])["hedge"] for i in ids)
        self.assertGreaterEqual(hedged, 2)   # the habits appear in the set, not only flat messages

    def test_possessive_not_contraction(self):
        F = stylo.features(["Sam's laptop and the team's deck are in Robin's office.", "It is done and that is it."])
        self.assertEqual(F["per100w_contractions"], 0)
        self.assertEqual(F["share_uncontracted"], 1.0)
        self.assertTrue(stylo.is_contraction("it's") and stylo.is_contraction("we'd") and stylo.is_contraction("don't"))
        self.assertFalse(stylo.is_contraction("sam's") or stylo.is_contraction("team's"))


class TestRetrieve(unittest.TestCase):
    def test_same_recipient_first(self):
        R = retrieve.retrieve(chat_records(), "chat", "is the report ready for the client", recipient="Morgan", k=3)
        self.assertEqual(R["same_recipient"], 3)
        self.assertTrue(all("Morgan" in r["recipient"] for r in R["retrieved"]))
        ids = {r["id"] for r in R["retrieved"]}
        self.assertFalse(ids & {r["id"] for r in R["critic"]})

    def test_leak_rule(self):
        recs = chat_records()
        held = recs[0]
        R = retrieve.retrieve(recs, "chat", held["context"], recipient=held["recipient"], k=5, n_critic=30,
                              heldout=held)
        for r in R["retrieved"] + R["critic"]:
            self.assertNotEqual(r["id"], held["id"])
            self.assertFalse(retrieve.leaks(r, held))

    def test_format_examples(self):
        s = retrieve.format_examples(chat_records()[:2], "chat")
        self.assertIn("They said:", s)
        self.assertIn("I sent:", s)


class TestLint(TempPack):
    def test_lint_findings(self):
        stats = build.build(self.pack)
        _, _, rtext = build.load_pack(self.pack)
        bad = "Absolutely! Happy to help — please let me know if you need anything else.\nThanks,\nPat"
        res = lint.lint(bad, "chat", rtext, stats)
        codes = {f["code"] for f in res["findings"]}
        for c in ("dash", "please", "closer_offer", "avoid", "line_break"):
            self.assertIn(c, codes)
        good = lint.lint("still on. bring the adapter", "chat", rtext, stats)
        self.assertEqual([f for f in good["findings"] if f["level"] == "fix"], [])

    def test_rate_checks(self):
        F = {"share_sentences_lowercase_start": 0.03, "len_mean": 20, "hedges": {"i think": 4.0}}
        res = lint._rate_checks("two came. the third is late i think", F, "chat", [])
        self.assertIn("capitalisation", {f["code"] for f in res})
        recent = [{"register": "chat", "text": "Done, i think"} for _ in range(6)]
        res = lint._rate_checks("Works for me i think", F, "chat", recent)
        self.assertIn("overuse:i think", {f["code"] for f in res})
        self.assertEqual(lint._rate_checks("Works for me. Thanks", F, "chat", recent), [])

    def test_caricature_across_drafts(self):
        stats = build.build(self.pack)
        _, _, rtext = build.load_pack(self.pack)
        recent = [{"register": "chat", "text": "Yep, done"} for _ in range(8)]
        res = lint.lint("Yep, on it", "chat", rtext, stats, recent)
        self.assertIn("overuse:ack_opener", {f["code"] for f in res["findings"]})


class TestLearn(unittest.TestCase):
    def test_report(self):
        rep = learn.report("Absolutely! I'll send the deck tomorrow, let me know if that works.",
                           "I'll send the deck tomorrow", "chat")
        kinds = {p["kind"] for p in rep["proposals"]}
        self.assertIn("avoid", kinds)
        self.assertLess(rep["words_sent"], rep["words_draft"])
        self.assertTrue(any("exclamation" in c for c in rep["marker_changes"]))

    def test_case_only_edit(self):
        edits, _ = learn.diff("Sounds good", "sounds good")
        self.assertTrue(edits and edits[0].get("case_only"))


class TestSources(unittest.TestCase):
    def test_paste(self):
        recs = paste.parse("to: Robin Fake\ncontext: you free?\nyep after 3\n---\nsecond one", "chat")
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["recipient"], "Robin Fake")
        self.assertEqual(recs[0]["context"], "you free?")
        self.assertEqual(recs[0]["text"], "yep after 3")

    def test_files_email_only_mine(self):
        st = {}
        recs = files.extract(os.path.join(FIX, "mail"), addresses=["robin@example.com"], names=["Robin"], stats=st)
        self.assertEqual(st["skipped_not_from_you"], 1)
        texts = [r["text"] for r in recs]
        self.assertEqual(len(recs), 3)
        self.assertFalse(any("wrote:" in t or "Original Message" in t or "Sent from my" in t for t in texts))
        self.assertFalse(any("555" in t or "Senior Widget" in t for t in texts))
        reply = [r for r in recs if r["subject"].startswith("Re: Quokka")][0]
        self.assertIn("north site", reply["context"])
        self.assertEqual(reply["thread"], "Quokka report timing")

    def test_doc_chunks(self):
        ch = files.chunk_doc("---\ntitle: x\n---\n# Head\n\n" + ("word " * 120 + "\n\n") * 3)
        self.assertEqual(len(ch), 3)

    def test_long_paragraph_split(self):
        para = " ".join("Sentence number %d has a few plain words in it." % i for i in range(120))  # ~1,100 words
        ch = files.chunk_doc(para)
        self.assertGreater(len(ch), 5)
        self.assertTrue(all(len(c.split()) <= files.CHUNK_WORDS for c in ch))
        self.assertEqual(sum(len(c.split()) for c in ch), len(para.split()))
        self.assertTrue(all(c.rstrip().endswith(".") for c in ch))

    def test_eml_ids_unique_across_folders(self):
        d = tempfile.mkdtemp(prefix="wlm_eml_")
        try:
            for sub, mid in (("Sent", "<a1@example.com>"), ("Archive", "<b2@example.com>")):
                os.makedirs(os.path.join(d, sub))
                with open(os.path.join(d, sub, "1.eml"), "w") as f:
                    f.write("From: Robin <robin@example.com>\nTo: Robin <robin@example.org>\nSubject: hi\n"
                            "Message-ID: %s\nDate: Mon, 2 Mar 2026 10:00:00 +0000\n\nNote from %s folder.\n" % (mid, sub))
            recs = files.extract(d, addresses=["robin@example.com"])
            self.assertEqual(len(recs), 2)
            self.assertEqual(len({r["id"] for r in recs}), 2)
        finally:
            shutil.rmtree(d)

    def test_imessage_parse(self):
        with open(os.path.join(FIX, "imessage_export", "Jordan Example.txt")) as f:
            rows = imessage.parse_export_file(f.read(), "Jordan Example")
        self.assertEqual([r[1] for r in rows], ["yep, and the long cable", "also grabbing ice on the way"])
        self.assertEqual(rows[0][2], "are you bringing the speaker")

    def test_claude_code(self):
        st = __import__("collections").defaultdict(__import__("collections").Counter)
        recs = claude_code.extract(os.path.join(FIX, "claude_home"), stats=st)
        texts = sorted(r["text"] for r in recs)
        # excluded: a history entry whose transcript record is meta-injected, and an unmatched
        # history entry that starts with a non-user marker (compact summary)
        self.assertEqual(texts, ["can you make the chart axis start at zero, it looks misleading otherwise",
                                 "looks good. ship it"])
        self.assertEqual(st["h_excluded"]["transcript_says_meta_injected"], 1)
        self.assertEqual(st["h_excluded"]["prefix_compact_summary"], 1)
        self.assertTrue(all(r["register"] == "prompt" for r in recs))
        self.assertFalse(any("/" in r.get("project", "") for r in recs))

    def test_teams_thread_records(self):
        me = "8:orgid:me"
        items = [(1, "8:orgid:them", "Robin Fake", "you around?"), (2, me, "", "yep what's up"),
                 (3, me, "", ""), (4, "8:orgid:them", "Robin Fake", "never mind")]
        recs = teams.thread_records(items, me, "19:abc@unq.gbl.spaces", "1:1", "")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["recipient"], "Robin Fake")
        self.assertEqual(recs[0]["context"], "you around?")
        self.assertEqual(teams.chat_type("19:abc@unq.gbl.spaces", None), "1:1")

    def test_teams_degrades(self):
        ok, why = teams.available()
        self.assertIsInstance(ok, bool)
        self.assertTrue(why)


class TestBuildCompose(TempPack):
    def test_build_and_kit(self):
        stats = build.build(self.pack)
        for f in ("profile.md", "rules.md", "stats.json", "exemplars.jsonl", "learned.md"):
            self.assertTrue(os.path.exists(os.path.join(self.pack, f)), f)
        self.assertEqual(stats["registers"]["chat"]["n"], 40)
        self.assertEqual(len(stats["registers"]["chat"]["fixed_ids"]), 6)
        s, bank, rtext = build.load_pack(self.pack)
        kit = compose.build_kit(bank, s, rtext, "", "chat", "yes still on; bring the adapter",
                                context="is the demo still on", recipient="Riley")
        self.assertIn("<examples>", kit["user"])
        self.assertIn("Never use em dashes", kit["user"])
        self.assertNotIn("{", kit["user"].replace("{\"brief\"", ""))
        self.assertEqual(len(kit["critic"]), 5)
        cs, cu = compose.critique_prompt(kit, "draft text", s)
        self.assertIn("draft text", cu)
        rs, ru = compose.revise_prompt(kit, "draft text", "No clear tells.", [])
        self.assertIn("No clear tells.", ru)

    def test_post_register_and_prose_supplement(self):
        topics = ["the inverter", "panel tilt", "battery sizing", "the charge controller", "grounding"]
        recs = [make_record("reddit", "post", "I went with %s in the end. It was probably overkill for a "
                            "small cabin, but it's been solid for %d months." % (topics[i % 5], i + 2),
                            rid="post%d" % i, thread="t%d" % i, context="what did you end up using?")
                for i in range(45)]
        recs += [make_record("x", "email", "Thursday works for the review, I'll bring the deck %d." % i,
                             rid="mail%d" % i, recipient="Dana") for i in range(6)]
        with open(os.path.join(self.pack, "sources", "more.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
        stats = build.build(self.pack)
        self.assertEqual(stats["registers"]["post"]["n"], 45)
        s, bank, rtext = build.load_pack(self.pack)
        kit = compose.build_kit(bank, s, rtext, "", "post", "used a bigger battery; would size up again",
                                context="what battery did you go with?")
        self.assertIn("Post or comment I'm replying to", kit["user"])
        self.assertIn("They posted: what did you end up using?", kit["user"])
        self.assertIn("I posted: ", kit["user"])
        # a thin email register borrows public posts (the person's longer prose) ...
        kit = compose.build_kit(bank, s, rtext, "", "email", "review moved to Friday", recipient="Dana")
        self.assertIn("I posted (on a public forum): ", kit["user"])
        # ... but chat, with plenty of its own samples, borrows nothing
        kit = compose.build_kit(bank, s, rtext, "", "chat", "yes still on", context="is the demo still on")
        self.assertNotIn("I posted", kit["user"])

    def test_rebuild_keeps_user_rules(self):
        build.build(self.pack)
        rp = os.path.join(self.pack, "rules.md")
        with open(rp, "a") as f:
            f.write("\n## chat\n- Never mention the weather\n")
        build.build(self.pack)
        with open(rp) as f:
            self.assertIn("Never mention the weather", f.read())
        self.assertTrue(os.path.exists(os.path.join(self.pack, "rules.prev.md")))

    def test_recipient_address_masked(self):
        recs = [make_record("x", "email", "Thursday works for the review.", recipient="dana.lee@example.com"),
                make_record("x", "email", "Sending the deck now.", recipient="Robin Fake <robin@example.org>")]
        bank, _ = build.clean_bank(recs)
        self.assertEqual([r["recipient"] for r in bank], ["dana.lee (example)", "Robin Fake"])
        self.assertNotIn("@", retrieve.format_examples(bank, "email"))
        self.assertTrue(retrieve.recipient_match(bank[0], "Dana"))
        keep, _ = build.clean_bank(recs, mask=False)
        self.assertEqual(keep[0]["recipient"], "dana.lee@example.com")

    def test_non_object_json_lines(self):
        p = os.path.join(self.pack, "sources", "junk.jsonl")
        with open(p, "w") as f:
            f.write('null\n[]\n"x"\n42\n{"id": "j1", "source": "x", "register": "chat", "text": "fine by me"}\n')
        self.assertEqual(len(read_jsonl(p)), 1)
        stats = build.build(self.pack)
        self.assertEqual(stats["registers"]["chat"]["n"], 41)
        bank, dropped = build.clean_bank([None, "x", {"register": "chat", "id": "a", "text": "ok then"}])
        self.assertEqual((len(bank), dropped["malformed"]), (1, 2))

    def test_pack_permissions(self):
        build.build(self.pack)
        os.chmod(os.path.join(self.pack, "sources"), 0o755)
        build.build(self.pack)
        self.assertEqual(os.stat(self.pack).st_mode & 0o777, 0o700)
        for root, dirs, fs in os.walk(self.pack):
            for d in dirs:
                self.assertEqual(os.stat(os.path.join(root, d)).st_mode & 0o777, 0o700, d)
            for f in fs:
                self.assertEqual(os.stat(os.path.join(root, f)).st_mode & 0o777, 0o600, f)

    def test_learned_notes_scoped(self):
        learned = (build.LEARNED_HEADER + "\n## 2026-03-01 chat (to Dana)\n- shorter replies to Dana\n"
                   "\n## 2026-03-02 email\n- no exclamation marks in email\n"
                   "\n## 2026-03-03 all\n- write okay, not ok\n")
        build.build(self.pack)
        s, bank, rtext = build.load_pack(self.pack)
        to_dana = compose.build_kit(bank, s, rtext, learned, "chat", "yes", recipient="Dana")["user"]
        to_sam = compose.build_kit(bank, s, rtext, learned, "chat", "yes", recipient="Sam")["user"]
        self.assertIn("shorter replies to Dana", to_dana)
        self.assertNotIn("shorter replies to Dana", to_sam)
        self.assertNotIn("no exclamation marks in email", to_dana)
        self.assertIn("write okay, not ok", to_sam)
        self.assertNotIn("Notes from comparing drafts", to_dana)   # the file's own header is never sent
        empty = compose.build_kit(bank, s, rtext, build.LEARNED_HEADER, "chat", "yes")["user"]
        self.assertNotIn("Learned from my edits", empty)

    def test_dedupe_and_mask(self):
        recs = [make_record("x", "chat", "Call me at 555-010-0199"), make_record("x", "chat", "call me at 555 010 0199!"),
                make_record("x", "chat", "pasted\n```\ncode\n```", flags=["possible_inline_paste"])]
        bank, dropped = build.clean_bank(recs)
        self.assertEqual(len(bank), 1)
        self.assertIn("[phone]", bank[0]["text"])
        self.assertEqual(dropped["duplicate"], 1)


class TestCheck(TempPack):
    def test_wilson(self):
        lo, hi = check.wilson(5, 20)
        self.assertTrue(0.08 < lo < 0.12 and 0.45 < hi < 0.5)

    def test_prepare_page_score_offline(self):
        stats = build.build(self.pack)
        s, bank, _ = build.load_pack(self.pack)
        items = check.select_items(bank, s, "chat", n=8)
        self.assertGreaterEqual(len(items), 4)
        fixed = set(s["registers"]["chat"]["fixed_ids"])
        self.assertFalse({i["id"] for i in items} & fixed)
        gens = [{"item_id": i["id"], "condition": "voice", "text": i["text"] + " ok", "brief": "b"} for i in items]
        pairs = check.page_data(items, gens)
        self.assertEqual(len(pairs), len(items))
        self.assertNotIn("real", json.dumps(pairs))
        trials = check.make_trials(items, gens, "voice")
        self.assertEqual(len(trials), 2 * len(items))
        for b in check.batches(trials, 6):
            self.assertEqual(len({t["item"] for t in b}), len(b))
        self.assertEqual(check.parse_judge('noise [{"id": "x", "real": "A"}] tail')[0]["real"], "A")
        z, zs = check.habit_z([i["text"] for i in items], [r["text"] for r in check.pool_for(bank, "chat", items)],
                              n_samples=20)
        self.assertIsNotNone(z)
        run = os.path.join(self.pack, "check", "r1")
        os.makedirs(run)
        from wlmlib.common import write_jsonl as wj
        wj(os.path.join(run, "items.jsonl"), items)
        wj(os.path.join(run, "generations.jsonl"), gens)
        out, n = check.build_page(run)
        with open(out) as f:
            self.assertIn("your real messages", f.read())
        with open(os.path.join(run, "answers.json")) as f:
            akey = json.load(f)
        key = akey["answers"]
        p0, p1 = sorted(key)[:2]
        wrong = {"A": "B", "B": "A"}
        # the export's own "correct" field is ignored; the run's answer key decides
        picks = {"run": "r1", "key": akey["key"], "picks": [{"pid": p0, "picked": key[p0]["real"], "correct": False},
                                        {"pid": p1, "picked": wrong[key[p1]["real"]], "correct": True},
                                        {"pid": "p999", "picked": "A", "correct": False}]}
        res = check.score_picks(picks, run)
        self.assertEqual((res["voice"]["n"], res["voice"]["fooled"]), (2, 1))
        with self.assertRaises(check.PicksMismatch):
            check.score_picks(dict(picks, run="another-run"), run)
        for f in ("blind_test.html", "answers.json"):
            self.assertEqual(os.stat(os.path.join(run, f)).st_mode & 0o777, 0o600)

    def test_closeness_bounded(self):
        self.assertEqual(check.closeness(2.0, 1.0, 0.5)[0], 2 / 3.0)
        self.assertEqual(check.closeness(2.0, 0.2, 0.5), (1.0, 1.2))
        self.assertEqual(check.closeness(2.0, 3.0, 0.5)[0], 0.0)
        self.assertEqual(check.closeness(0.4, 0.3, 0.5), (None, None))   # plain closer than real: undefined


class TestCLI(TempPack):
    def run_cli(self, *args):
        import wlm
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wlm.main(list(args))
        return buf.getvalue()

    def test_cli_flow(self):
        out = self.run_cli("build")
        self.assertIn("chat", out)
        out = self.run_cli("kit", "--register", "chat", "--notes", "yes; 3pm", "--recipient", "Casey")
        kit = out.split("kit: ", 1)[1].split("\n", 1)[0].strip()
        d = os.path.join(kit, "draft.txt")
        with open(d, "w") as f:
            f.write("Sure, 3pm works")
        self.assertIn("lint:", self.run_cli("lint", "--register", "chat", "--file", d))
        self.assertTrue(self.run_cli("critique-prompt", "--kit", kit, "--draft-file", d).strip().endswith(".md"))
        self.run_cli("log-draft", "--kit", kit, "--file", d)
        self.assertIn("similarity", self.run_cli("learn", "diff", "--register", "chat", "--sent", "3 works"))
        self.run_cli("learn", "note", "--register", "chat", "--text", "shorter replies to Casey")
        with open(os.path.join(self.pack, "learned.md")) as f:
            self.assertIn("shorter replies to Casey", f.read())
        disc = json.loads(self.run_cli("discover"))
        self.assertIn("claude_code", disc["sources"])
        # a check run built from the synthetic fixtures is labelled as a demo, everywhere it shows
        prep = json.loads(self.run_cli("check", "prepare", "--n", "6"))
        self.assertTrue(prep["data"].startswith("DEMO: "))
        with open(os.path.join(prep["run"], "meta.json")) as f:
            self.assertTrue(json.load(f)["demo"])
        self.assertIn("dry run", self.run_cli("vendor-install", "teams"))
        self.assertFalse(os.path.exists(os.path.join(self.pack, "vendor", "ccl_chromium_reader")))


class TestFixes2(TempPack):
    """Synthetic cases for the second pre-release review (invented people and addresses)."""

    def run_cli(self, *args):
        import wlm
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                wlm.main(list(args))
            except SystemExit as e:
                return buf.getvalue(), e.code
        return buf.getvalue(), 0

    def test_final_punct_ignores_signoff(self):
        t = "Hi,\n\nCan we move it to Friday?\n\nThanks,\nJordan"
        self.assertFalse(stylo.message_markers(t)["no_end_punct"])
        self.assertEqual(stylo.body_end("ok\nSounds Good"), "ok\nSounds Good")
        F = stylo.features([t, "Hello,\n\nAll done here.\n\nJordan"])
        self.assertEqual(F["share_end_none"], 0.0)

    def test_fixed_exemplars_allow_multiline_email(self):
        recs = [make_record("email_file", "email", "Hi Casey,\n\nThe order for week %d is fine by me, ship as usual.\n\nThanks,\nJordan" % i,
                            rid="e%d" % i, recipient="Casey %d" % i) for i in range(8)]
        self.assertEqual(len(rules.choose_fixed(recs, k=4, n_sets=50)), 4)

    def test_intl_phone_and_subject_masked(self):
        self.assertNotIn("7946", textutil.mask_pii("call me on +44 20 7946 0958"))
        self.assertNotIn("900123", textutil.mask_pii("mobile 07700 900123"))
        self.assertIn("2026-11-06", textutil.mask_pii("due 2026-11-06, about 1,200 units"))
        r = make_record("email_file", "email", "Can you call them about the order today please", rid="s1")
        r["subject"] = "Call casey@example.com"
        bank, _ = build.clean_bank([r])
        self.assertNotIn("casey@example.com", json.dumps(bank))

    def test_attached_message_not_taken_as_body(self):
        from email.message import EmailMessage
        inner = EmailMessage()
        inner["From"] = "casey@example.com"
        inner.set_content("words written by someone else entirely")
        outer = EmailMessage()
        outer["From"] = "jordan@example.com"
        outer.set_content("my own words")
        outer.add_alternative("<p>my own words</p>", subtype="html")
        outer.add_attachment(inner)
        body = files._body_text(outer)
        self.assertIn("my own words", body)
        self.assertNotIn("someone else", body)

    def test_doc_blockquotes_dropped(self):
        chunks = files.chunk_doc("My plan for the spring is below.\n\n> Casey: the budget is fixed\n\nWe start in March.")
        self.assertNotIn("Casey", " ".join(chunks))
        self.assertIn("We start in March.", " ".join(chunks))

    def test_empty_extract_keeps_earlier_source(self):
        d = tempfile.mkdtemp(prefix="wlm_eml_")
        try:
            with open(os.path.join(d, "1.eml"), "w") as f:
                f.write("From: jordan@example.com\nTo: casey@example.com\nSubject: hi\n\nsee you thursday then\n")
            out, code = self.run_cli("extract", "files", d, "--address", "jordan@example.com")
            self.assertEqual(code, 0)
            out, code = self.run_cli("extract", "files", d, "--address", "jordna@example.com")   # typo
            self.assertEqual(code, 2)
            self.assertIn("kept the 1", out)
            src = [f for f in os.listdir(os.path.join(self.pack, "sources")) if f.startswith("files-")][0]
            self.assertEqual(len(read_jsonl(os.path.join(self.pack, "sources", src))), 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_claude_config_dir_respected(self):
        from wlmlib import common
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.pack
        try:
            self.assertEqual(common.claude_dir(), os.path.abspath(self.pack))
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old


class TestFixes3(TempPack):
    """Regressions for codex-review-2 Medium items and the install-test Low item (synthetic data)."""

    def test_picks_bound_to_page_version(self):
        build.build(self.pack)
        s, bank, _ = build.load_pack(self.pack)
        items = check.select_items(bank, s, "chat", n=6)
        gens = [{"item_id": i["id"], "condition": c, "text": i["text"] + " " + c, "brief": "b"}
                for i in items for c in ("voice", "plain")]
        run = os.path.join(self.pack, "check", "r2")
        os.makedirs(run)
        from wlmlib.common import write_jsonl as wj
        wj(os.path.join(run, "items.jsonl"), items)
        wj(os.path.join(run, "generations.jsonl"), gens)
        with open(os.path.join(run, "meta.json"), "w") as f:
            json.dump({"register": "chat"}, f)

        def export():
            with open(os.path.join(run, "answers.json")) as f:
                key = json.load(f)
            return {"run": "r2", "key": key["key"],
                    "picks": [{"pid": pid, "picked": a["real"]} for pid, a in key["answers"].items()]}
        check.build_page(run)
        old = export()
        with open(os.path.join(run, "blind_test.html")) as f:
            self.assertIn('"key": "%s"' % old["key"], f.read())
        self.assertEqual(check.score_picks(old, run)["voice"]["n"], len(items))
        check.build_page(run, conditions=("voice", "plain"))  # same run name, pair ids reassigned
        with self.assertRaises(check.PicksMismatch):
            check.score_picks(old, run)
        with self.assertRaises(check.PicksMismatch):
            check.score_picks(dict(old, key=None), run)
        new = export()
        self.assertNotEqual(new["key"], old["key"])
        self.assertEqual(check.score_picks(new, run)["plain"]["n"], len(items))
        # stale picks.json saved earlier don't crash `score`
        with open(os.path.join(run, "picks.json"), "w") as f:
            json.dump(old, f)
        res = check.score(run, bank, s, judge=False, log=lambda m: None)
        self.assertNotIn("human", res)

    def test_imessage_reactions_vs_real_messages(self):
        text = ("Mar 05, 2026  5:31:10 PM\nMe\nLoved your presentation today!\nTapbacks:\n"
                "    Loved by Jordan Example\n\n"
                "Mar 05, 2026  5:32:10 PM\nMe\nLiked it a lot, nice work\n\n"
                "Mar 05, 2026  5:33:10 PM\nMe\nLoved \u201cyep, see you at 6\u201d\n\n"
                "Mar 05, 2026  5:34:10 PM\nMe\nReacted \U0001F44D to \"ok\"\n\n"
                "Mar 05, 2026  5:35:10 PM\nMe\nLaughed at \u201cthe cat\nwas on the roof\u201d\n")
        rows = imessage.parse_export_file(text, "Jordan Example")
        self.assertEqual([r[1] for r in rows], ["Loved your presentation today!", "Liked it a lot, nice work"])

    def test_midbody_thanks_not_cut(self):
        body = ("Hi Dana,\n\nThe spring menu draft is attached.\n\nThanks for taking a look.\n\n"
                "One more thing: the Friday order needs doubling, can you confirm by noon?\n"
                "Also the oven technician comes Tuesday.")
        out = clean.clean_email_body(body, own_names=["Theo"])
        self.assertIn("oven technician comes Tuesday", out["text"])
        # a real sign-off with a signature block is still cut
        sig = ("Hi Dana,\n\nThe draft is attached.\n\nThanks,\nTheo\nHead Baker, Example Bakery\n"
               "+1 555 010 0199\nwww.example.com")
        out = clean.clean_email_body(sig, own_names=["Theo"])
        self.assertTrue(out["text"].endswith("Thanks,\nTheo"))
        self.assertNotIn("555", out["text"])

    def test_teams_partial_cache_is_runtime_error(self):
        uid = "00000000-0000-0000-0000-00000000000%d"
        full = ["Teams:replychain-manager:react-web-client:%s:%s" % (uid % 1, uid % 2),
                "Teams:conversation-manager:react-web-client:%s:%s" % (uid % 1, uid % 2)]
        self.assertEqual(teams.manager_dbs(full)[2], "8:orgid:" + uid % 2)
        for names in (full[:1], full[1:], [], ["Teams:replychain-manager:react-web-client:bad", full[1]]):
            with self.assertRaises(RuntimeError):
                teams.manager_dbs(names)

    def test_bare_address_recipient_matches_org_name(self):
        recs = [make_record("x", "email", "Can we move the delivery to Thursday?",
                            recipient="orders@millstoneflour.example"),
                make_record("x", "email", "Thursday works for the review.", recipient="dana.lee@gmail.com")]
        bank, _ = build.clean_bank(recs)
        self.assertEqual([r["recipient"] for r in bank], ["orders (millstoneflour)", "dana.lee"])
        self.assertNotIn("@", json.dumps(bank))
        self.assertTrue(retrieve.recipient_match(bank[0], "Millstone Flour"))
        self.assertTrue(retrieve.recipient_match(bank[1], "Dana Lee"))
        self.assertFalse(retrieve.recipient_match(bank[1], "Millstone Flour"))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".claude-plugin", "icon.png")))


if __name__ == "__main__":
    unittest.main()
