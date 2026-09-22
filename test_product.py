"""Product test suite for the agentguard package (offline, stdlib unittest).

Run: python3 -m unittest test_product -v
Covers: policy loading/validation, FROZEN regression, audit, circuit
breaker, control-plane decisions (incl. disagreement-conservative),
cooldown, CLI helpers, demo/eval gates.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agentguard.audit import AuditLog, summarize
from agentguard.config import PolicyError, TRIGGER_DEFAULTS, load_policy, policy_toml
from agentguard.engine import ControlPlane
from agentguard.cli import (_extract_pi_prompt, _split_cmd, build_pi_command,
                            _cmd_for_resume, EXIT_PAUSED, EXIT_TERMINATED)
from agentguard.scorer import Scorer
from agentguard.triggers import check_triggers
from agentguard.state_engine import StateEngine
from agentguard.scenarios import SCENARIOS, run_trial
from agentguard.evalcmd import eval_policy, verdict


def quiet_policy(**overrides):
    from agentguard.config import load_policy as lp
    p = lp("balanced")
    p.stderr_banner = False
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


class FakeScorer:
    def __init__(self, choice="CONTINUE", confidence=0.99):
        self.choice = choice
        self.confidence = confidence
        self.calls = 0

    def consult(self, mss, reason=""):
        self.calls += 1
        return {"choice": self.choice, "confidence": self.confidence,
                "probabilities": {self.choice: self.confidence}}


class BrokenScorer:
    def consult(self, mss, reason=""):
        raise RuntimeError("network down")


def feed(plane, events):
    decision = None
    for ev in events:
        decision = plane.process_event(ev)
        if decision:
            break
    return decision


def read_events(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


class TestPolicy(unittest.TestCase):
    def test_presets_load(self):
        for name in ("conservative", "balanced", "aggressive"):
            p = load_policy(name)
            self.assertEqual(p.preset, name)
        self.assertEqual(load_policy().triggers, TRIGGER_DEFAULTS)

    def test_toml_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text(policy_toml(load_policy("conservative")), encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.preset, "conservative")
            self.assertEqual(p.triggers["passive_read_streak"], 10)

    def test_toml_partial_override(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text(
                'preset = "balanced"\n[triggers]\nretry_count = 7\n[scorer]\nbase_url = "http://x:1"\n',
                encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.triggers["retry_count"], 7)
            self.assertEqual(p.triggers["error_rate"], TRIGGER_DEFAULTS["error_rate"])
            self.assertEqual(p.scorer_base_url, "http://x:1")

    def test_json_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.json")
            Path(path).write_text(json.dumps({"allowed_paths": ["/tmp/x"], "token_budget": 1000}), encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.allowed_paths, ["/tmp/x"])
            self.assertEqual(p.token_budget, 1000)

    def test_unknown_key_warns_not_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text('frobnicate = 1\n', encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.token_budget, 200_000)

    def test_bad_types_raise(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text('token_budget = "lots"', encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)
            Path(path).write_text('allowed_paths = "src"', encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_missing_file_raises(self):
        with self.assertRaises(PolicyError):
            load_policy("/nonexistent/policy.toml")


class TestFrozenRegression(unittest.TestCase):
    def test_default_thresholds_unchanged(self):
        self.assertEqual(TRIGGER_DEFAULTS, {
            "error_rate": 0.4, "error_rate_window": 8, "retry_count": 3,
            "convergence_seconds": 600, "convergence_turns": 5,
            "passive_read_streak": 12, "unverified_write_streak": 10,
            "budget_pct": 0.8,
        })

    def test_balanced_suite_gate(self):
        p = quiet_policy()
        metrics = eval_policy(p, n_trials=5, seed=7)
        self.assertTrue(verdict(metrics), f"balanced gate failed: {metrics}")


class TestAudit(unittest.TestCase):
    def test_write_and_summarize(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audit.jsonl")
            log = AuditLog(path)
            log.session_start("s1", "balanced", ["pi", "task"])
            log.intervention("s1", "loop", "should_continue", "PAUSE", "rule",
                             "soft_decision", {"token_usage": 900, "cost_usd": 0.02})
            log.session_end("s1", "exit 75", 75)
            recs = read_events(path)
            self.assertEqual(len(recs), 3)
            self.assertEqual(recs[0]["type"], "session_start")
            s = summarize(path)
            self.assertEqual(s["interventions"], 1)
            self.assertEqual(s["sessions"], 1)
            self.assertEqual(s["by_action"], {"PAUSE": 1})

    def test_degrades_on_bad_path(self):
        log = AuditLog("/proc/definitely/not/writable/audit.jsonl")
        log.emit("intervention", session="s", action="PAUSE")  # must not raise
        self.assertTrue(log._degraded)


class FakeBreakerClient:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def ask_continue(self, mss, trigger_reason=""):
        self.calls += 1
        if self.calls <= self.fail_times:
            return None
        return {"choice": "PAUSE", "confidence": 0.9,
                "probabilities": {"PAUSE": 0.9}}

    def health(self):
        return {"status": "ok", "ready": True, "model": "fake"}


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_two_failures(self):
        s = Scorer("http://fake:1")
        s.client = FakeBreakerClient(fail_times=2)
        self.assertIsNone(s.consult({}, "r"))
        self.assertIsNone(s.consult({}, "r"))
        calls_after_open = s.client.calls
        self.assertIsNone(s.consult({}, "r"))  # breaker open: no call
        self.assertEqual(s.client.calls, calls_after_open)

    def test_recovers_on_success(self):
        s = Scorer("http://fake:1")
        s.client = FakeBreakerClient(fail_times=2)
        s.consult({}, "r")
        s.consult({}, "r")
        r = s.consult({}, "r")  # breaker expired? cooldown 60s — force:
        s.open_until = 0.0
        r = s.consult({}, "r")
        self.assertIsNotNone(r)
        self.assertEqual(s.fail_streak, 0)


def stall_events(n_reads=14):
    events = [{"type": "agent_start"}]
    for _ in range(n_reads):
        events.append({"type": "turn_start"})
        events.append({"type": "tool_execution_end", "toolName": "read",
                       "args": {"path": "f.txt"}, "isError": False})
    return events


def danger_events():
    return [{"type": "agent_start"},
            {"type": "tool_execution_end", "toolName": "bash",
             "args": {"command": "rm -rf /var/lib/postgresql"}, "isError": False}]


class TestControlPlane(unittest.TestCase):
    def test_passive_stall_pauses_rule_only(self):
        with tempfile.TemporaryDirectory() as td:
            audit = AuditLog(os.path.join(td, "a.jsonl"))
            plane = ControlPlane(quiet_policy(), session="t", audit=audit)
            d = feed(plane, stall_events())
            self.assertIsNotNone(d)
            self.assertEqual(d.action, "PAUSE")
            self.assertIn("rule", d.source)
            recs = read_events(os.path.join(td, "a.jsonl"))
            self.assertEqual(recs[-1]["type"], "intervention")
            self.assertEqual(recs[-1]["action"], "PAUSE")

    def test_dangerous_is_hard_constraint_no_scorer(self):
        scorer = FakeScorer(choice="CONTINUE", confidence=0.99)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, danger_events())
        self.assertEqual(d.action, "REQUEST_HUMAN_APPROVAL")
        self.assertEqual(d.authority, "hard_constraint")
        self.assertEqual(scorer.calls, 0)

    def test_disagreement_conservative(self):
        scorer = FakeScorer(choice="CONTINUE", confidence=0.99)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "PAUSE")
        self.assertIn("disagreement", d.source)

    def test_scorer_confirms_intervention(self):
        scorer = FakeScorer(choice="CANCEL", confidence=0.9)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "CANCEL")
        self.assertIn("jev", d.source)

    def test_low_confidence_falls_back_to_rule(self):
        scorer = FakeScorer(choice="CANCEL", confidence=0.3)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "PAUSE")
        self.assertIn("rule", d.source)

    def test_scorer_crash_degrades_to_rule(self):
        plane = ControlPlane(quiet_policy(), scorer=BrokenScorer())
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "PAUSE")

    def test_cooldown_suppresses_second_trigger(self):
        plane = ControlPlane(quiet_policy())
        d1 = feed(plane, stall_events())
        self.assertIsNotNone(d1)
        d2 = feed(plane, stall_events(2))
        self.assertIsNone(d2)

    def test_policy_changes_firing_point(self):
        p = quiet_policy()
        p.triggers["passive_read_streak"] = 5
        plane = ControlPlane(p)
        d = feed(plane, stall_events(8))
        self.assertIsNotNone(d)

    def test_scope_violation_requires_allowed_paths(self):
        p = quiet_policy()
        p.allowed_paths = ["/allowed"]
        plane = ControlPlane(p)
        events = [{"type": "agent_start"},
                  {"type": "tool_execution_end", "toolName": "write",
                   "args": {"path": "/etc/evil.txt"}, "isError": False}]
        d = feed(plane, events)
        self.assertEqual(d.action, "REQUEST_HUMAN_APPROVAL")
        self.assertIn("Scope violation", d.reason)

    def test_event_ingestion_error_ignored(self):
        plane = ControlPlane(quiet_policy())
        self.assertIsNone(plane.process_event(None))
        self.assertIsNone(plane.process_event({"type": "tool_execution_end"}))


class TestCliHelpers(unittest.TestCase):
    def test_split_cmd(self):
        pre, cmd = _split_cmd(["wrap", "--policy", "balanced", "--", "pi", "task"])
        self.assertEqual(pre, ["wrap", "--policy", "balanced"])
        self.assertEqual(cmd, ["pi", "task"])

    def test_build_pi_command_fresh(self):
        c = build_pi_command(["pi", "do it"], session_id="abc", resumable=False, fresh_session=True)
        self.assertIn("--mode", c)
        self.assertIn("--no-session", c)
        self.assertNotIn("--session-id", c)

    def test_build_pi_command_resumable(self):
        c = build_pi_command(["pi", "do it"], session_id="abc", resumable=True, fresh_session=False)
        self.assertNotIn("--no-session", c)
        self.assertIn("agentguard-abc", c)

    def test_build_pi_command_keeps_explicit_mode(self):
        c = build_pi_command(["pi", "--mode", "rpc", "x"], "abc", False, True)
        self.assertEqual(c.count("--mode"), 1)

    def test_extract_pi_prompt(self):
        prompt = _extract_pi_prompt(["pi", "--mode", "rpc", "--provider", "p",
                                     "--no-session", "fix", "the", "bug"])
        self.assertEqual(prompt, "fix the bug")

    def test_resume_cmd(self):
        c = _cmd_for_resume(["pi", "--mode", "rpc"], "abc")
        self.assertIn("--continue", c)
        self.assertIn("agentguard-abc", c)
        self.assertIsNone(_cmd_for_resume(["python", "x"], "abc"))

    def test_exit_code_semantics(self):
        self.assertEqual(EXIT_PAUSED, 75)
        self.assertEqual(EXIT_TERMINATED, 76)


class TestScenarios(unittest.TestCase):
    def test_expectations_hold(self):
        for scenario, (_, should_not_trigger) in SCENARIOS.items():
            rng = __import__("random").Random(3)
            for i in range(3):
                t = run_trial(scenario, i, rng)
                if should_not_trigger:
                    self.assertFalse(t.triggered, f"{scenario} #{i} should be clean")
                else:
                    self.assertTrue(t.triggered, f"{scenario} #{i} should fire")


if __name__ == "__main__":
    unittest.main(verbosity=2)
