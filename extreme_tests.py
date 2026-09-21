import time
from state_engine import StateEngine
from triggers import check_triggers
from pi_client import PiRpcClient
from kernel import ControlKernel


def simulate_loop_se():
    """Agent edits the same file back and forth repeatedly."""
    se = StateEngine(token_budget=200_000)
    se.process_event({"type": "agent_start"})
    for i in range(12):
        se.process_event({"type": "turn_start"})
        se.process_event({
            "type": "tool_execution_end",
            "toolName": "edit",
            "args": {"path": "src/handler.py"},
            "isError": False,
        })
        se.process_event({
            "type": "tool_execution_end",
            "toolName": "bash",
            "args": {"command": "python -m pytest"},
            "isError": True,
        })
    return se


def simulate_budget_bleed():
    """Agent burns tokens on repeated read without progress."""
    se = StateEngine(token_budget=50_000)
    se.process_event({"type": "agent_start"})
    for i in range(20):
        se.process_event({"type": "turn_start"})
        se.process_event({
            "type": "message_update",
            "usage": {"totalTokens": 3000 * (i + 1), "cost": {"total": 0.15 * (i + 1)}},
        })
        se.process_event({
            "type": "tool_execution_end",
            "toolName": "read",
            "args": {"path": "README.md"},
            "isError": False,
        })
    return se


def simulate_dangerous():
    """Agent executes rm -rf on a directory."""
    se = StateEngine(token_budget=200_000)
    se.process_event({"type": "agent_start"})
    se.process_event({"type": "tool_execution_end",
        "toolName": "bash", "args": {"command": "rm -rf /var/lib/postgresql"}, "isError": False})
    return se


def simulate_stalled():
    """Agent runs for a long time with no tool calls, no progress."""
    se = StateEngine(token_budget=200_000)
    se.process_event({"type": "agent_start"})
    se.state.started_at = time.time() - 900
    se.state.turn_count = 15
    se.state.token_usage = 80_000
    return se


def run_test(name: str, se: StateEngine, description: str):
    print(f"\n{'='*60}")
    print(f"SCENARIO: {name}")
    print(f"  {description}")
    print(f"{'='*60}")

    trigger = check_triggers(se.state)
    if trigger:
        print(f"  [TRIGGER FIRED] {trigger.decision_type}: {trigger.reason}")
        mss = se.state.to_mss()
        print(f"  [MSS] {mss}")
        return True
    else:
        print(f"  [NO TRIGGER] State looks normal to external observer.")
        return False


if __name__ == "__main__":
    print("Agent Control Plane — Extreme Scenario Tests")

    run_test(
        "Infinite Loop",
        simulate_loop_se(),
        "Agent edits the same file 12 times, test fails every time."
    )

    run_test(
        "Budget Bleed",
        simulate_budget_bleed(),
        "Agent reads the same file 20 times, burns 60K tokens, makes no changes."
    )

    run_test(
        "Dangerous Command",
        simulate_dangerous(),
        "Agent executes rm -rf on a database directory."
    )

    run_test(
        "Convergence Stall",
        simulate_stalled(),
        "Agent has been running 15 minutes, 15 turns, no progress."
    )
