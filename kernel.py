from enum import Enum

from pi_client import PiRpcClient


class DecisionAuthority(Enum):
    HARD_CONSTRAINT = "hard_constraint"
    SOFT_DECISION = "soft_decision"


class ControlKernel:
    def __init__(self, pi_client: PiRpcClient):
        self.pi = pi_client
        self.pending_actions = []

    def execute(self, decision: dict, mss: dict) -> str:
        rule_action = decision.get("rule_action", "CONTINUE")
        jev_action = decision.get("jev_action")
        jev_confidence = decision.get("jev_confidence", 0.0)
        authority = decision.get("authority", DecisionAuthority.SOFT_DECISION)
        reason = decision.get("reason", "")

        if authority == DecisionAuthority.HARD_CONSTRAINT:
            self.pi.abort()
            return f"[KERNEL] HARD CONSTRAINT → {rule_action} (not overridable): {reason}"

        if jev_action is None:
            action = rule_action
            source = "rule"
        elif jev_confidence < 0.5:
            action = rule_action
            source = f"rule (jev conf {jev_confidence:.2f} < 0.5)"
        elif jev_action == "CONTINUE" and rule_action != "CONTINUE":
            action = rule_action
            source = f"rule (jev said CONTINUE at conf {jev_confidence:.2f}; disagreement -> conservative)"
        else:
            action = jev_action
            source = f"jev (conf {jev_confidence:.2f})"

        if action == "CONTINUE":
            return f"[KERNEL] {source} → CONTINUE: {reason}"
        elif action in ("PAUSE", "CANCEL", "ESCALATE", "REQUEST_HUMAN_APPROVAL"):
            self.pi.abort()
            return f"[KERNEL] {source} → {action}: {reason}"
        elif action == "BLOCK":
            self.pi.abort()
            return f"[KERNEL] {source} → BLOCK: {reason}"

        return f"[KERNEL] {source} → unknown action: {action}"

    def resume(self) -> str:
        return "[KERNEL] RESUME → agent ready for next prompt"
