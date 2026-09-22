from enum import Enum

from .pi_client import PiRpcClient


class DecisionAuthority(Enum):
    HARD_CONSTRAINT = "hard_constraint"
    SOFT_DECISION = "soft_decision"


class ControlKernel:
    def __init__(self, pi_client: PiRpcClient):
        self.pi = pi_client
        self.pending_actions = []

    @staticmethod
    def resolve_action(decision: dict) -> tuple:
        """Resolve a decision dict to (action, source).

        Authority hierarchy:
        - HARD_CONSTRAINT: rule wins, not overridable
        - scorer absent / low confidence (<0.5): rule wins
        - disagreement (scorer says CONTINUE against a rule intervention): rule wins
        - otherwise: scorer action (may only escalate severity)
        """
        rule_action = decision.get("rule_action", "CONTINUE")
        jev_action = decision.get("jev_action")
        jev_confidence = decision.get("jev_confidence", 0.0)
        authority = decision.get("authority", DecisionAuthority.SOFT_DECISION)

        if authority == DecisionAuthority.HARD_CONSTRAINT:
            return rule_action, "hard_constraint"

        if jev_action is None:
            return rule_action, "rule"
        if jev_confidence < 0.5:
            return rule_action, f"rule (jev conf {jev_confidence:.2f} < 0.5)"
        if jev_action == "CONTINUE" and rule_action != "CONTINUE":
            return rule_action, f"rule (jev said CONTINUE at conf {jev_confidence:.2f}; disagreement -> conservative)"
        return jev_action, f"jev (conf {jev_confidence:.2f})"

    def execute(self, decision: dict, mss: dict) -> str:
        rule_action = decision.get("rule_action", "CONTINUE")
        authority = decision.get("authority", DecisionAuthority.SOFT_DECISION)
        reason = decision.get("reason", "")

        action, source = self.resolve_action(decision)

        if authority == DecisionAuthority.HARD_CONSTRAINT:
            self.pi.abort()
            return f"[KERNEL] HARD CONSTRAINT → {rule_action} (not overridable): {reason}"

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
