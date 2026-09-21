import sys
import time

from pi_client import PiRpcClient
from state_engine import StateEngine
from triggers import check_triggers
from jev_client import JevClient
from kernel import ControlKernel, DecisionAuthority


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py '<task prompt>'")
        sys.exit(1)

    task_prompt = sys.argv[1]
    pi = PiRpcClient()
    state_engine = StateEngine(token_budget=200_000)
    kernel = ControlKernel(pi)

    jev = JevClient()
    health = jev.health()
    if health and health.get("ready"):
        log(f"Jev endpoint ready: {health.get('model')} @ {str(health.get('revision', ''))[:7]}")
    else:
        log("WARNING: Jev endpoint unreachable, decisions default to rule.")

    last_fired_at = 0.0
    cooldown_seconds = 10.0

    def on_event(event: dict) -> None:
        nonlocal last_fired_at
        event_type = event.get("type", "")

        if event_type == "tool_execution_end":
            tool = event.get("toolName", "")
            is_error = event.get("isError", False)
            status = "ERROR" if is_error else "OK"
            log(f"tool: {tool} [{status}]")

        elif event_type == "auto_retry_start":
            log("auto_retry started")

        elif event_type == "agent_settled":
            log("agent settled")

        state_engine.process_event(event)

        now = time.time()
        if now - last_fired_at < cooldown_seconds:
            return

        trigger = check_triggers(state_engine.state)
        if not trigger:
            return

        last_fired_at = now
        mss = state_engine.state.to_mss()
        log(f"TRIGGER: {trigger.reason}")
        log(f"MSS: {mss}")

        if not jev:
            log("Jev disabled, logging only.")
            return

        if trigger.decision_type == "request_permission":
            decision = {
                "rule_action": "REQUEST_HUMAN_APPROVAL",
                "authority": DecisionAuthority.HARD_CONSTRAINT,
                "reason": trigger.reason,
            }
            kernel_msg = kernel.execute(decision, mss)
            log(kernel_msg)
            return

        jev_action = None
        jev_confidence = 0.0
        if jev:
            result = jev.ask_continue(mss, trigger.reason)
            if result:
                log(f"JEV RESULT: {result['choice']} (conf {result['confidence']:.3f})")
                jev_action = result.get("choice", "CONTINUE")
                jev_confidence = result.get("confidence", 0.0)
            else:
                log("Jev API call failed, defaulting to rule.")

        decision = {
            "rule_action": "PAUSE",
            "jev_action": jev_action,
            "jev_confidence": jev_confidence,
            "authority": DecisionAuthority.SOFT_DECISION,
            "reason": trigger.reason,
        }
        kernel_msg = kernel.execute(decision, mss)
        log(kernel_msg)

    pi.on_event(on_event)
    pi.start()
    time.sleep(1)

    log(f"Sending task: {task_prompt}")
    pi.prompt(task_prompt)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Shutting down.")
        pi.stop()


if __name__ == "__main__":
    main()
