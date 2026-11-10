"""ProofHQ / Workfront Proof source and the review register. Fixtures are synthetic (tests/fixtures/proofhq/,
an invented host and invented people)."""
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
PH = os.path.join(FIX, "proofhq")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from wlmlib import build, cli_context as CC, person as P, select as S  # noqa: E402
from wlmlib.sources import proofhq  # noqa: E402


class TestParser(unittest.TestCase):
    def test_host_from_link(self):
        h = proofhq.host_from_link("https://example.my.workfront.adobe.com/proof/review/ab-TOKEN-cd?tcmsTenantId=x#token:TOKEN/")
        self.assertEqual(h, "example.my.workfront.adobe.com")
        self.assertEqual(proofhq.host_from_link("example.my.workfront.com/home"), "example.my.workfront.com")
        for bad in ("https://experience.adobe.com/#/@org/workfront/home", "https://evil.example.com/x.my.workfront.adobe.com",
                    "https://my.workfront.adobe.com.evil.example/"):
            with self.assertRaises(ValueError):
                proofhq.host_from_link(bad)

    def test_links_use_only_the_host(self):
        L = proofhq.links("example.my.workfront.adobe.com", pages=2)
        self.assertEqual(L["signin"], "https://example.my.workfront.adobe.com/")
        self.assertEqual(len(L["pages"]), 2)
        self.assertIn("$$FIRST=2000", L["pages"][1])
        self.assertIn("ownerID=$$USER.ID", L["pages"][0])
        self.assertTrue(all(u.startswith("https://example.my.workfront.adobe.com/attask/api/v20.0/note/")
                            for u in [L["count"]] + L["pages"]))

    def test_dates_and_text(self):
        self.assertEqual(proofhq.wf_date("2026-01-13T11:27:14:902-1000"), "2026-01-13T21:27:14Z")
        self.assertEqual(proofhq.wf_date("2026-01-13T11:27:14Z"), "2026-01-13T11:27:14Z")
        self.assertEqual(proofhq.wf_date("nonsense"), "")
        self.assertEqual(proofhq.clean_text("Proof Comment:\n Looks good.  (Edited by Riley Sample)"), "Looks good.")
        self.assertEqual(proofhq.clean_text("Proof Comment: STET\n\nKeeping it."), "STET\n\nKeeping it.")

    def test_records_from_pages(self):
        st = {}
        recs = proofhq.extract_files([os.path.join(PH, "search.json"), os.path.join(PH, "search (1).html")], stats=st)
        by = {r["id"]: r for r in recs}
        self.assertEqual(sorted(by), ["proofhq:n01", "proofhq:n02", "proofhq:n03", "proofhq:n04", "proofhq:n08"])
        self.assertTrue(all(r["register"] == "review" and r["source"] == "proofhq" for r in recs))
        self.assertEqual(by["proofhq:n01"]["text"], "STET\n\nThe weight matches the design system, so it stays as is.")
        self.assertEqual(by["proofhq:n01"]["recipient"], "Riley Sample")
        self.assertEqual(by["proofhq:n01"]["context"], "Can we make the headline bolder here?")
        self.assertNotIn("Edited by", by["proofhq:n02"]["text"])
        self.assertEqual(by["proofhq:n04"]["recipient"], "")          # reply in their own thread
        self.assertEqual(by["proofhq:n03"]["context"], "")            # top-level comment
        self.assertIn("proof_comment", by["proofhq:n03"]["flags"])
        self.assertIn("proof_reply", by["proofhq:n04"]["flags"])
        self.assertEqual((st["deleted"], st["not_proof_comment"], st["empty"], st["duplicates"]), (1, 1, 1, 1))
        self.assertFalse(st.get("maybe_more"))
        self.assertNotIn("doc-a", json.dumps([r["text"] + r["context"] + r["recipient"] for r in recs]))

    def test_errors_are_readable(self):
        with self.assertRaises(RuntimeError) as e:
            proofhq.extract_files([os.path.join(PH, "not_signed_in.json")])
        self.assertIn("sign-in link", str(e.exception))
        with self.assertRaises(RuntimeError):
            proofhq.parse_page("<html><body>nothing here</body></html>")

    def test_full_page_flags_maybe_more(self):
        d = tempfile.mkdtemp(prefix="wlm_ph_")
        try:
            notes = [{"ID": "x%d" % i, "noteText": "Proof Comment: ok %d" % i, "entryDate": ""} for i in range(proofhq.PAGE)]
            p = os.path.join(d, "page.json")
            with open(p, "w") as f:
                json.dump({"data": notes}, f)
            st = {}
            proofhq.extract_files([p], st)
            self.assertTrue(st["maybe_more"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestReviewRegister(unittest.TestCase):
    def setUp(self):
        self.pack = tempfile.mkdtemp(prefix="wlm_test_")
        os.makedirs(os.path.join(self.pack, "sources"))
        shutil.copy(os.path.join(FIX, "chat_records.jsonl"), os.path.join(self.pack, "sources", "synthetic.jsonl"))
        self.old_env = os.environ.get("WRITE_LIKE_ME_PACK")
        os.environ["WRITE_LIKE_ME_PACK"] = self.pack

    def tearDown(self):
        if self.old_env is None:
            os.environ.pop("WRITE_LIKE_ME_PACK", None)
        else:
            os.environ["WRITE_LIKE_ME_PACK"] = self.old_env
        shutil.rmtree(self.pack, ignore_errors=True)

    def wlm(self, *argv):
        import wlm
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wlm.main(list(argv))
        return out.getvalue()

    def test_entry_wording(self):
        e = {"register": "review", "timestamp": "2026-03-02T19:15:30Z", "recipient": "Riley Sample",
             "context": "Can we make the headline bolder?", "text": "STET, it matches the style guide."}
        self.assertTrue(S.render_entry(e).startswith("### to Riley Sample, 2026-03-02\nReplying to: Can we make"))
        self.assertIn("Replying to: (nothing: this is a new comment on the proof)", S.render_task({"notes": "n"}, "review"))
        self.assertEqual(CC.restyle_where("review", "Riley Sample", platform="ProofHQ"),
                         "This is a ProofHQ proof comment replying to Riley Sample.")
        self.assertEqual(P.classify({"source": "proofhq", "register": "review"}), "work")

    def test_link_step_writes_nothing(self):
        out = json.loads(self.wlm("extract", "proofhq", "--link", "https://example.my.workfront.adobe.com/proof/review/a-T-b"))
        self.assertEqual(out["step"], "open_links")
        self.assertFalse(os.path.exists(os.path.join(self.pack, "sources", "proofhq.jsonl")))
        self.assertIn("proofhq", json.loads(self.wlm("discover"))["sources"])

    def test_import_build_and_context(self):
        self.assertEqual(json.loads(self.wlm("extract", "proofhq", "--file", os.path.join(PH, "search.json")))["records"], 4)
        stats = build.build(self.pack)
        self.assertEqual(stats["registers"]["review"]["n"], 4)
        info = CC.build_context(self.pack, "review", "keeping it; matches the style guide",
                                context="Can the subhead be larger?", recipient="Riley Sample",
                                person="none", habits="off", today="2026-03-10")
        self.assertEqual(info["archive_register"], "review")
        with open(info["system"], encoding="utf-8") as f:
            system = f.read()
        self.assertIn("comments on proofs under review", system)
        self.assertIn("### to Riley Sample, 2026-03-02", system)
        with open(info["task"], encoding="utf-8") as f:
            task = f.read()
        self.assertIn("Replying to: Can the subhead be larger?", task)

    def test_falls_back_when_no_review_samples(self):
        build.build(self.pack)
        info = CC.build_context(self.pack, "review", "stet; it's the brand color", person="none", habits="off")
        self.assertEqual(info["archive_register"], "chat")


if __name__ == "__main__":
    unittest.main()
