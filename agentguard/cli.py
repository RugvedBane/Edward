"""agentguard command line interface.

Subcommands:
  wrap     Run an agent command under the control plane (the product entry).
  demo     Self-running proof with built-in failure scenarios.
  eval     Evaluate a policy pack against the scenario suite.
  audit    Inspect the audit JSONL (summary / tail).
  doctor   Environment checks (scorer, pi, audit dir).
"""

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

from .audit import AuditLog, summarize
from .config import load_policy, policy_toml
from .engine import RESUMABLE_ACTIONS, ControlPlane
from .notify import notify_stderr
from .pi_client import PiRpcClient
from .scorer import Scorer

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PAUSED = 75
EXIT_TERMINATED = 76
EXIT_SIGINT = 130

DEFAULT_AUDIT_PATH = os.path.join(os.path.expanduser("~"), ".agentguard", "audit.jsonl")
PI_VALUE_FLAGS = {"--provider", "--model", "--mode", "--session", "--session-id",
                  "--name", "--thinking", "--models", "--tools", "--exclude-tools",
                  "--session-dir", "--system-prompt", "--append-system-prompt"}
MAX_RESUMES = 5
INACTIVITY_CAP_SECONDS = 180


def log_line(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _split_cmd(argv):
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, None


def _is_pi_rpc(cmd) -> bool:
    return bool(cmd) and os.path.basename(cmd[0]) == "pi"


def _extract_pi_prompt(cmd) -> str:
    parts = []
    skip_next = False
    for arg in cmd[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in PI_VALUE_FLAGS:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        parts.append(arg)
    return " ".join(parts)


def build_pi_command(cmd, session_id: str, resumable: bool, fresh_session: bool) -> list:
    base = list(cmd)
    if "--mode" not in base:
        base = [base[0], "--mode", "rpc"] + base[1:]
    if "--no-session" in base:
        base.remove("--no-session")
    if fresh_session and not resumable:
        base.append("--no-session")
    elif session_id:
        base += ["--session-id", f"agentguard-{session_id}"]
    return base


class Killer:
    """Universal intervention executor for arbitrary child processes."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc

    def abort(self) -> None:
        self.terminate()

    def terminate(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                return
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.kill()
                except OSError:
                    pass


def _cmd_for_resume(cmd, session_id: str) -> list:
    if not _is_pi_rpc(cmd):
        return None
    base = [c for c in cmd if c != "--no-session"]
    if "--mode" not in base:
        base = [base[0], "--mode", "rpc"] + base[1:]
    if "--session-id" not in base and session_id:
        base += ["--session-id", f"agentguard-{session_id}"]
    if "--continue" in base or "-c" in base:
        return base
    base += ["--continue"]
    return base


def cmd_wrap(args, cmd) -> int:
    if not cmd:
        print("wrap requires a command after '--': agentguard wrap -- <agent command>", file=sys.stderr)
        return EXIT_ERROR

    policy = load_policy(args.policy)
    if args.scope:
        policy.allowed_paths = sorted(set(policy.allowed_paths) | {os.path.abspath(p) for p in args.scope})
    if args.scorer:
        policy.scorer_base_url = args.scorer
    if args.no_scorer:
        policy.scorer_enabled = False
    if args.webhook:
        policy.webhook_url = args.webhook
    if args.auto_resume is not None:
        policy.auto_resume_seconds = args.auto_resume
    session_id = uuid.uuid4().hex[:8]

    audit = AuditLog(None if args.no_audit else (args.audit or DEFAULT_AUDIT_PATH))
    scorer = None
    if policy.scorer_enabled:
        scorer = Scorer(policy.scorer_base_url, policy.scorer_timeout_seconds)
        health = scorer.health()
        if health and health.get("ready"):
            log_line(f"scorer ready: {health.get('model')}")
        else:
            log_line("scorer unreachable — rule-only mode (degraded, still protective)")
            scorer = None

    resumes = 0
    current_cmd = list(cmd)
    fresh_session = True
    outcome = None
    while True:
        run_cmd = current_cmd
        if _is_pi_rpc(run_cmd):
            run_cmd = build_pi_command(run_cmd, session_id, resumable=policy.auto_resume_seconds > 0 or args.continue_session, fresh_session=fresh_session)
        outcome = _run_under_control(run_cmd, policy, scorer, audit, session_id,
                                     max_seconds=args.max_seconds, is_pi=_is_pi_rpc(run_cmd))
        fresh_session = False
        if outcome == EXIT_PAUSED and policy.auto_resume_seconds > 0 and resumes < MAX_RESUMES and _is_pi_rpc(current_cmd):
            resumes += 1
            log_line(f"auto-resume {resumes}/{MAX_RESUMES} in {policy.auto_resume_seconds}s "
                     f"(session agentguard-{session_id})")
            time.sleep(policy.auto_resume_seconds)
            resumed = _cmd_for_resume(current_cmd, session_id)
            if resumed:
                current_cmd = resumed
                continue
        break

    if audit:
        audit.session_end(session=session_id, reason=f"exit {outcome}", exit_code=outcome)
    return outcome


def _run_under_control(cmd, policy: Policy, scorer, audit: AuditLog, session: str,
                       max_seconds=None, is_pi: bool = False) -> int:
    """Run one child process under the control plane; returns the exit code."""
    plane = ControlPlane(policy, session=session, audit=audit,
                         scorer=scorer, log=log_line)
    audit.session_start(session=session, policy_preset=policy.preset, command=cmd)

    if is_pi:
        return _run_pi_rpc(cmd, plane, policy, max_seconds)
    return _run_generic(cmd, plane, policy, max_seconds)


def _handle_decision(plane: ControlPlane, decision, killer, label: str) -> int:
    log_line(f"TRIGGER: {decision.reason}")
    log_line(f"decision: {decision.action} ({decision.source})")
    tokens = plane.state.token_usage
    cost = plane.state.cost_usd
    if tokens:
        log_line(f"stopped at {tokens} tokens (~${cost:.2f} spent this session)")
    if decision.action == "CONTINUE":
        return None
    killer.abort()
    time.sleep(0.5)
    if decision.action in RESUMABLE_ACTIONS:
        notify_stderr("PAUSED", decision.reason,
                      f"exit code {EXIT_PAUSED}. Resume: agentguard wrap --continue -- <command>")
        return EXIT_PAUSED
    notify_stderr("TERMINATED", decision.reason, f"exit code {EXIT_TERMINATED}")
    return EXIT_TERMINATED


def _run_pi_rpc(cmd, plane: ControlPlane, policy: Policy, max_seconds) -> int:
    client = PiRpcClient(command=cmd, cwd=os.getcwd())
    outcome_box = {"code": None}

    def on_event(event: dict) -> None:
        etype = event.get("type", "")
        if etype == "tool_execution_end":
            status = "ERROR" if event.get("isError") else "OK"
            log_line(f"tool: {event.get('toolName', '')} [{status}]")
        decision = plane.process_event(event)
        if decision and outcome_box["code"] is None:
            code = _handle_decision(plane, decision, client, "pi")
            if code is not None:
                outcome_box["code"] = code

    client.on_event(on_event)
    try:
        client.start()
    except FileNotFoundError:
        print("error: 'pi' executable not found on PATH. Install: "
              "npm install -g --ignore-scripts @earendil-works/pi-coding-agent", file=sys.stderr)
        return EXIT_ERROR

    prompt = _extract_pi_prompt(cmd)
    time.sleep(1.0)
    if prompt:
        log_line(f"task: {prompt[:120]}{'…' if len(prompt) > 120 else ''}")
        client.prompt(prompt)
    else:
        log_line("no prompt arguments detected; monitoring idle RPC session")

    started = time.time()
    settled = False
    try:
        while outcome_box["code"] is None:
            if not client.alive:
                rc = client._proc.poll() if client._proc else 0
                if rc not in (0, None) and not settled:
                    tail = client.stderr_tail()
                    if tail:
                        log_line(f"agent stderr tail:\n{tail}")
                outcome_box["code"] = rc if rc is not None else EXIT_OK
                break
            if not settled and plane.state.agent_status == "settled":
                settled = True
                log_line("agent settled")
                summary = plane.summary()
                log_line("summary: " + json.dumps(summary))
                outcome_box["code"] = EXIT_OK
                break
            if max_seconds and time.time() - started > max_seconds:
                log_line(f"wall-clock cap {max_seconds}s reached")
                client.abort()
                outcome_box["code"] = EXIT_TERMINATED
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        outcome_box["code"] = EXIT_SIGINT
    finally:
        client.stop()
    return outcome_box["code"]


def _run_generic(cmd, plane: ControlPlane, policy: Policy, max_seconds) -> int:
    """Universal path: run any command; auto-detect JSONL events, else
    watchdog on wall clock + output inactivity."""
    try:
        proc = subprocess.Popen(cmd, cwd=os.getcwd(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
    except FileNotFoundError as exc:
        print(f"error: cannot spawn {cmd[0]!r}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    killer = Killer(proc)
    lines = queue.Queue()
    threading.Thread(target=_pump, args=(proc, lines), daemon=True).start()

    started = time.time()
    last_output = time.time()
    saw_json_events = False
    wall_cap = max_seconds if max_seconds else 3600
    outcome = None
    try:
        while outcome is None:
            try:
                raw = lines.get(timeout=1.0)
            except queue.Empty:
                if proc.poll() is not None:
                    outcome = proc.returncode or EXIT_OK
                    break
                if time.time() - started > wall_cap:
                    log_line(f"wall-clock cap {wall_cap}s reached — terminating")
                    outcome = EXIT_TERMINATED
                    break
                if time.time() - last_output > INACTIVITY_CAP_SECONDS:
                    log_line(f"no output for {INACTIVITY_CAP_SECONDS}s — treating as stall")
                    plane.state.agent_status = "running"
                    decision = _make_inactivity_decision(plane)
                    if decision:
                        outcome = _handle_decision(plane, decision, killer, "generic")
                    else:
                        killer.terminate()
                        outcome = EXIT_TERMINATED
                    break
                continue

            last_output = time.time()
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                if proc.poll() is not None and lines.empty():
                    outcome = proc.returncode or EXIT_OK
                continue
            print(line, flush=True)

            event = None
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict) and parsed.get("type"):
                    event = parsed
                    saw_json_events = True
            except json.JSONDecodeError:
                pass

            if event:
                decision = plane.process_event(event)
                if decision:
                    code = _handle_decision(plane, decision, killer, "generic")
                    if code is not None:
                        outcome = code
            elif proc.poll() is not None and lines.empty():
                outcome = proc.returncode or EXIT_OK
    except KeyboardInterrupt:
        killer.terminate()
        outcome = EXIT_SIGINT
    finally:
        if not saw_json_events and outcome == EXIT_OK:
            log_line(f"process exited rc={proc.returncode} (plain mode: wall/inactivity watchdog only)")
    return outcome if outcome is not None else (proc.returncode or EXIT_OK)


def _make_inactivity_decision(plane: ControlPlane):
    from .engine import Decision
    from .triggers import TriggerResult
    trigger = TriggerResult(True, "should_continue",
                            f"Output inactivity: no stdout for {INACTIVITY_CAP_SECONDS}s")
    decision = Decision(action="PAUSE", source="rule (inactivity watchdog)",
                        authority="soft_decision", decision_type="should_continue",
                        reason=trigger.reason, trigger=trigger)
    plane.interventions += 1
    if plane.audit:
        plane.audit.intervention(session=plane.session, trigger_reason=trigger.reason,
                                 decision_type="should_continue", action="PAUSE",
                                 source=decision.source, authority=decision.authority,
                                 mss=plane.state.to_mss(), jev={})
    return decision


def _pump(proc, lines: queue.Queue) -> None:
    try:
        for raw in proc.stdout:
            lines.put(raw)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(b"")


def cmd_demo(args) -> int:
    from .demo import run_demo
    return run_demo(n_trials=args.trials, policy_source=args.policy, use_scorer=args.live_scorer)


def cmd_eval(args) -> int:
    from .evalcmd import eval_policy
    policy = load_policy(args.policy)
    scorer = None
    if args.scorer:
        scorer = Scorer(policy.scorer_base_url, policy.scorer_timeout_seconds)
        if not scorer.health():
            print("scorer unreachable — evaluating rule-only", file=sys.stderr)
            scorer = None
    metrics = eval_policy(policy, n_trials=args.trials, scorer=scorer, seed=args.seed)
    print(f"{'scenario':<20} {'type':<10} {'n':>4}  {'result':<18}")
    print("-" * 60)
    for scenario, m in metrics.items():
        if m["type"] == "abnormal":
            print(f"{scenario:<20} {'abnormal':<10} {m['n']:>4}  "
                  f"detect {m['detection_rate']:>6.1%}  lat {m['avg_latency']:.1f}")
        else:
            print(f"{scenario:<20} {'normal':<10} {m['n']:>4}  "
                  f"FP {m['fp']} ({m['fpr']:.1%})")
    from .evalcmd import verdict
    ok = verdict(metrics)
    print(f"\nverdict: {'PASS' if ok else 'FAIL'} (policy: {policy.preset}, seed {args.seed})")
    return 0 if ok else 1


def cmd_audit(args) -> int:
    path = args.file or DEFAULT_AUDIT_PATH
    if args.tail:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                records = fh.readlines()
            for raw in records[-args.tail:]:
                print(raw.rstrip())
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK
    s = summarize(path)
    print(f"file: {s['file']}")
    print(f"sessions: {s['sessions']}   interventions: {s['interventions']}")
    if s["interventions"]:
        print(f"by action: {s['by_action']}")
        print(f"by decision type: {s['by_decision_type']}")
        if s["tokens_at_intervention"]:
            avg = sum(s['tokens_at_intervention']) / len(s['tokens_at_intervention'])
            print(f"avg tokens at intervention: {avg:.0f}")
        if s["cost_usd_at_intervention"]:
            total = sum(s['cost_usd_at_intervention'])
            print(f"recorded spend at interventions: ${total:.2f}")
    if s["first_ts"]:
        print(f"window: {s['first_ts']} .. {s['last_ts']}")
    return EXIT_OK


def cmd_doctor(args) -> int:
    checks = []
    checks.append(("python", f"{sys.version.split()[0]} ({sys.platform})", True))
    pi = shutil.which("pi")
    checks.append(("pi CLI", pi or "not found (npm install -g --ignore-scripts @earendil-works/pi-coding-agent)", bool(pi)))
    audit_dir = os.path.dirname(DEFAULT_AUDIT_PATH)
    try:
        os.makedirs(audit_dir, exist_ok=True)
        probe = os.path.join(audit_dir, ".probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        checks.append(("audit dir", audit_dir + " writable", True))
    except OSError as exc:
        checks.append(("audit dir", f"{audit_dir} not writable: {exc}", False))

    policy = load_policy(args.policy)
    scorer = Scorer(policy.scorer_base_url, policy.scorer_timeout_seconds)
    health = scorer.health()
    if health and health.get("ready"):
        checks.append(("scorer", f"{health.get('model')} @ {policy.scorer_base_url} ready", True))
    else:
        checks.append(("scorer", f"unreachable @ {policy.scorer_base_url} (rule-only mode works without it)", False))

    ok = True
    for name, detail, good in checks:
        ok = ok and good
        mark = "ok " if good else "WARN"
        print(f"[{mark}] {name:<10} {detail}")
    print(f"\nverdict: {'READY' if ok else 'READY (degraded)'} — "
          f"control plane is protective even with WARN items")
    return EXIT_OK


def cmd_policy_template(args) -> int:
    policy = load_policy(args.preset)
    print(policy_toml(policy), end="")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentguard",
        description="External control plane for AI coding agents: deterministic rules + semantic scorer, audit trail, human-resumable interventions.",
    )
    parser.add_argument("--version", action="version", version=f"agentguard {__import__('agentguard', fromlist=['__version__']).__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--policy", help="policy preset (conservative|balanced|aggressive) or path to .toml/.json")
        p.add_argument("--scorer", help="override scorer base URL")
        p.add_argument("--webhook", help="Slack-compatible webhook URL for intervention alerts")

    p_wrap = sub.add_parser("wrap", help="run an agent command under the control plane")
    add_common(p_wrap)
    p_wrap.add_argument("--scope", action="append", help="allowed path prefix (repeatable; enables scope-violation trigger)")
    p_wrap.add_argument("--cwd", help="working directory for the agent")
    p_wrap.add_argument("--audit", help=f"audit JSONL path (default {DEFAULT_AUDIT_PATH})")
    p_wrap.add_argument("--no-audit", action="store_true", help="disable audit log")
    p_wrap.add_argument("--no-scorer", action="store_true", help="rule-only mode")
    p_wrap.add_argument("--max-seconds", type=int, help="wall-clock cap for the run")
    p_wrap.add_argument("--auto-resume", type=int, metavar="SECONDS", help="auto-resume paused pi sessions after N seconds (max 5 resumes)")
    p_wrap.add_argument("--continue", dest="continue_session", action="store_true", help="resume the previous pi session instead of a fresh one")
    p_wrap.add_argument("--", dest="cmd", nargs=argparse.REMAINDER, help="agent command to run")

    p_demo = sub.add_parser("demo", help="self-running scenario demo")
    add_common(p_demo)
    p_demo.add_argument("--trials", type=int, default=3)
    p_demo.add_argument("--live-scorer", action="store_true", help="include live scorer calls")

    p_eval = sub.add_parser("eval", help="evaluate a policy against the scenario suite")
    add_common(p_eval)
    p_eval.add_argument("--trials", type=int, default=30)
    p_eval.add_argument("--seed", type=int, default=137)

    p_audit = sub.add_parser("audit", help="inspect audit log")
    p_audit.add_argument("file", nargs="?", help=f"audit JSONL path (default {DEFAULT_AUDIT_PATH})")
    p_audit.add_argument("--tail", type=int, metavar="N", help="show last N records")

    p_doc = sub.add_parser("doctor", help="environment checks")
    add_common(p_doc)

    p_tpl = sub.add_parser("policy-template", help="print a TOML policy template")
    p_tpl.add_argument("--preset", default="balanced", choices=["conservative", "balanced", "aggressive"])

    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pre, cmd = _split_cmd(argv)
    parser = build_parser()
    args = parser.parse_args(pre)

    if args.command == "wrap":
        if not cmd and getattr(args, "cmd", None):
            cmd = [c for c in args.cmd if c != "--"]
        if args.cwd:
            os.chdir(args.cwd)
        return cmd_wrap(args, cmd)
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "eval":
        return cmd_eval(args)
    if args.command == "audit":
        return cmd_audit(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "policy-template":
        return cmd_policy_template(args)
    parser.print_help()
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
