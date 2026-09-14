"""Structural checks on the mirror workflow.

The login / notify-once / gating steps only work together: a renamed step,
a missing `if:`, or a step-level env that shadows $GITHUB_ENV silently
brings back repeated failure emails or a stale cookie. Pin them here.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify_once  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "kidsnote-to-notion.yml"


@unittest.skipIf(yaml is None, "PyYAML not installed")
class MirrorWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.steps = cls.doc["jobs"]["mirror"]["steps"]
        cls.names = [s.get("name") or s.get("uses") for s in cls.steps]

    def step(self, name):
        return self.steps[self.names.index(name)]

    def test_workflow_file_name_matches_notify_default(self):
        self.assertEqual(WORKFLOW.name, notify_once.DEFAULT_WORKFLOW_FILE)

    def test_keeps_schedule_and_actions_write_permission(self):
        triggers = self.doc.get("on") or self.doc.get(True)  # YAML 1.1 reads `on` as True
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(self.doc["permissions"].get("actions"), "write")

    def test_login_runs_first_with_all_credential_secrets(self):
        auth = self.step("Kidsnote login")
        self.assertEqual(self.names.index("Kidsnote login"), 1, "login must run right after checkout")
        self.assertEqual(auth.get("id"), "auth")
        self.assertIn("kidsnote_auth.py --github", auth["run"])
        self.assertNotIn("if", auth)
        for secret in ("KIDSNOTE_USERNAME", "KIDSNOTE_PASSWORD", "KIDSNOTE_SESSION_COOKIE"):
            self.assertEqual(auth["env"][secret], "${{ secrets.%s }}" % secret)

    def test_notify_step_name_and_condition(self):
        self.assertIn(notify_once.STEP_NAME, self.names,
                      "notify_once.STEP_NAME must equal the workflow step name")
        notify = self.step(notify_once.STEP_NAME)
        self.assertEqual(self.names.index(notify_once.STEP_NAME), 2)
        self.assertIn("notify_once.py", notify["run"])
        self.assertIn("!cancelled()", notify["if"])
        self.assertIn("steps.auth.outputs.ok != 'true'", notify["if"])
        self.assertEqual(notify["env"]["GITHUB_TOKEN"], "${{ github.token }}")

    def test_every_later_step_is_gated_on_login(self):
        for step in self.steps[3:]:
            name = step.get("name") or step.get("uses")
            cond = str(step.get("if", ""))
            if name == "Keep cron schedule alive":
                self.assertEqual(cond, "always()")
                continue
            with self.subTest(step=name):
                gated = "steps.auth.outputs.ok == 'true'" in cond or "steps.ai.outputs.value" in cond
                self.assertTrue(gated, f"step {name!r} must be skipped when login failed (if: {cond!r})")

    def test_mirror_step_does_not_shadow_fresh_session(self):
        mirror = self.step("Mirror Kidsnote → Notion")
        self.assertNotIn("KIDSNOTE_SESSION_COOKIE", mirror.get("env", {}),
                         "a step-level env would override the fresh sessionid from $GITHUB_ENV")
        self.assertNotIn("KIDSNOTE_PASSWORD", mirror.get("env", {}))

    def test_relabel_input_is_off_by_default_and_wired(self):
        triggers = self.doc.get("on") or self.doc.get(True)
        relabel_input = triggers["workflow_dispatch"]["inputs"]["relabel"]
        self.assertEqual(relabel_input["default"], "off")
        self.assertEqual(relabel_input["options"], ["off", "on"])
        self.assertIn("--relabel-existing", self.step("Mirror Kidsnote → Notion")["run"])

    def test_scripts_referenced_by_workflow_exist(self):
        root = WORKFLOW.parents[2]
        for script in ("tools/kidsnote_fetch/kidsnote_auth.py", "tools/kidsnote_fetch/notify_once.py",
                       "tools/kidsnote_fetch/fetch.py"):
            self.assertTrue((root / script).is_file(), script)


if __name__ == "__main__":
    unittest.main()
