"""The child `claude` processes never see ANTHROPIC_API_KEY (unless the person opts in) or the parent
session's messaging socket, keep cloud-provider settings, and get real text on stdin, never argv.
subprocess.run is mocked: no model is called."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from wlmlib import compose, draft, llm  # noqa: E402

SECRET_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-test-not-real",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/x.sock",
    "CLAUDE_CODE_MESSAGING_TOKEN": "msg-test",
}
# How Claude Code reaches a cloud provider or a gateway: that is the person's login, so it stays.
PROVIDER_ENV = {
    "ANTHROPIC_AUTH_TOKEN": "tok-test",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CODE_USE_VERTEX": "1",
    "CLAUDE_CODE_USE_FOUNDRY": "1",
    "ANTHROPIC_VERTEX_PROJECT_ID": "proj-test",
    "AWS_BEARER_TOKEN_BEDROCK": "bedrock-test",
}
TASK = "Replying to: is the demo still on\nWhat I want to say (my notes, not my wording): yes, bring the adapter"


def ok(*_a, **_kw):
    return mock.Mock(returncode=0, stdout=json.dumps({"result": "still on, bring the adapter", "usage": {},
                                                     "total_cost_usd": 0.0}), stderr="")


class TestChildEnv(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wlm_env_test_")
        self.system_file = os.path.join(self.dir, "system.txt")
        with open(self.system_file, "w") as f:
            f.write("ARCHIVE TEXT THAT MUST NOT BE IN ARGV\n")
        self.env = mock.patch.dict(os.environ, dict(SECRET_ENV, HOME=os.environ.get("HOME", "/tmp"),
                                                    KEEP_ME="1", **PROVIDER_ENV))
        self.env.start()
        os.environ.pop(llm.KEEP_API_KEY, None)      # restored by the patch on stop
        self.which = mock.patch("shutil.which", return_value="/usr/local/bin/claude")
        self.which.start()

    def tearDown(self):
        self.which.stop()
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def check_env(self, env):
        for k in SECRET_ENV:
            self.assertNotIn(k, env)
        self.assertEqual(env.get("KEEP_ME"), "1")   # the rest of the environment is passed through
        for k, v in PROVIDER_ENV.items():
            self.assertEqual(env.get(k), v, k)
        for k, v in llm.ENV_OVERRIDES.items():
            self.assertEqual(env.get(k), v)

    def test_child_env(self):
        self.check_env(llm.child_env())
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], SECRET_ENV["ANTHROPIC_API_KEY"])  # parent untouched
        self.assertNotIn("ANTHROPIC_API_KEY", llm.child_env({"ANTHROPIC_API_KEY": "x", "PATH": "/bin"}))

    def test_keep_api_key_opt_in(self):
        kept = llm.child_env({"ANTHROPIC_API_KEY": "x", llm.KEEP_API_KEY: "1",
                              "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/x.sock"})
        self.assertEqual(kept["ANTHROPIC_API_KEY"], "x")
        self.assertNotIn("CLAUDE_CODE_MESSAGING_SOCKET", kept)      # the opt-in covers the key only
        for v in ("0", "", "yes", "true"):
            self.assertNotIn("ANTHROPIC_API_KEY", llm.child_env({"ANTHROPIC_API_KEY": "x", llm.KEEP_API_KEY: v}))
        with mock.patch.dict(os.environ, {llm.KEEP_API_KEY: "1"}), mock.patch("subprocess.run", side_effect=ok) as run:
            draft.draft(self.system_file, TASK)
        self.assertEqual(run.call_args[1]["env"]["ANTHROPIC_API_KEY"], SECRET_ENV["ANTHROPIC_API_KEY"])

    def test_draft_env_and_stdin(self):
        with mock.patch("subprocess.run", side_effect=ok) as run:
            text = draft.draft(self.system_file, TASK, model="some-model")
        self.assertEqual(text, "still on, bring the adapter")
        args, kw = run.call_args
        argv = args[0]
        self.check_env(kw["env"])
        self.assertEqual(kw["input"], TASK)
        self.assertFalse(any("adapter" in a or "ARCHIVE TEXT" in a for a in argv), argv)
        self.assertEqual(argv[argv.index("--system-prompt-file") + 1], os.path.abspath(self.system_file))
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "some-model")
        self.assertNotIn("--system-prompt", argv)
        self.assertNotEqual(os.path.realpath(kw["cwd"]), os.path.realpath(os.getcwd()))
        self.assertFalse(os.path.exists(kw["cwd"]))   # the empty temp cwd is removed afterwards

    def test_draft_cache_and_error(self):
        cache = os.path.join(self.dir, "calls")
        with mock.patch("subprocess.run", side_effect=ok) as run:
            a = draft.run(self.system_file, TASK, cache_dir=cache)
            b = draft.run(self.system_file, TASK, cache_dir=cache)
            draft.run(self.system_file, TASK, cache_dir=cache, sample=1)
        self.assertEqual((a["cached"], b["cached"], run.call_count), (False, True, 2))
        bad = mock.Mock(returncode=0, stdout=json.dumps({"result": "x", "is_error": True}), stderr="")
        with mock.patch("subprocess.run", return_value=bad), mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                draft.draft(self.system_file, TASK, retries=1)
        with self.assertRaises(RuntimeError):
            draft.draft(os.path.join(self.dir, "missing.txt"), TASK)

    def test_llm_call_env_and_stdin(self):
        with mock.patch("subprocess.run", side_effect=ok) as run:
            llm.call("fixed system text", TASK)
        args, kw = run.call_args
        self.check_env(kw["env"])
        self.assertEqual(kw["input"], TASK)
        self.assertFalse(any("adapter" in a for a in args[0]))


class TestContextTemplate(unittest.TestCase):
    def test_render(self):
        entries = "### r/test, 2025-01-01\nReplying to: what about {task}?\nMy comment:\nnot sure"
        sys_text, task = compose.render_context("post", entries, "### r/test, 2026-01-01\nMy comment:",
                                                first="2021", last="2026")
        self.assertTrue(sys_text.startswith("This is a selection of my posts and comments from 2021 to 2026, "
                                            "in the order I posted them."))
        self.assertIn("reads exactly like the next entry in this archive", sys_text)
        self.assertEqual(sys_text.count("=== ARCHIVE ==="), 1)
        self.assertIn("what about {task}?", sys_text)   # entry text is never filled as a template
        self.assertNotIn("ABOUT ME", sys_text)
        self.assertTrue(task.startswith("Here is the new one. Write my comment from my notes"))
        self.assertNotIn("{", task)
        with_person, _ = compose.render_context("chat", "E", "T", person_text="I repair boats.")
        self.assertLess(with_person.index("I repair boats."), with_person.index("=== ARCHIVE ==="))
        for reg in compose.CONTEXT_WORDS:
            s, u = compose.render_context(reg, "E", "T", person_text="P")
            self.assertNotRegex(s + u, r"\{[a-z_]+\}")


if __name__ == "__main__":
    unittest.main()
