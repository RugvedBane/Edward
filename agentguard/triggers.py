from dataclasses import dataclass
from typing import Optional

from .state_engine import AgentState


TRIGGER_DEFAULTS = {
    "error_rate": 0.4,
    "error_rate_window": 8,
    "retry_count": 3,
    "convergence_seconds": 600,
    "convergence_turns": 5,
    "passive_read_streak": 12,
    "unverified_write_streak": 10,
    "budget_pct": 0.8,
}


@dataclass
class TriggerResult:
    should_fire: bool
    decision_type: str
    reason: str


def check_triggers(state: AgentState, policy: Optional[dict] = None) -> Optional[TriggerResult]:
    t = {**TRIGGER_DEFAULTS, **(policy or {})}

    if state.scope_violations:
        return TriggerResult(
            should_fire=True,
            decision_type="request_permission",
            reason=f"Scope violation: agent wrote to {state.scope_violations[-1]} outside allowed paths",
        )

    if state.dangerous_commands:
        return TriggerResult(
            should_fire=True,
            decision_type="request_permission",
            reason=f"Dangerous command detected: {state.dangerous_commands[-1]}",
        )

    if state.elapsed_seconds > t["convergence_seconds"] and state.turn_count > t["convergence_turns"]:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Convergence stall: {state.elapsed_seconds:.0f}s elapsed, {state.turn_count} turns, no completion signal",
        )

    if state.error_rate > t["error_rate"] and len(state.recent_tool_calls) >= t["error_rate_window"] and not state.recovery_signal:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"High error rate: {state.error_rate:.1%} over last {min(10, len(state.recent_tool_calls))} tool calls",
        )

    if state.error_trend_rising and state.error_rate > 0.5 and len(state.recent_tool_calls) >= t["error_rate_window"] and not state.recovery_signal:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Diverging: error trend rising ({state.error_rate:.1%}), no recovery signal",
        )

    if state.consecutive_unverified_writes >= t["unverified_write_streak"]:
        return TriggerResult(
            should_fire=True,
            decision_type="request_permission",
            reason=f"Silent corruption risk: {state.consecutive_unverified_writes} consecutive writes without any shell verification",
        )

    if len(state.recent_tool_calls) >= t["passive_read_streak"]:
        recent = state.recent_tool_calls[-t["passive_read_streak"]:]
        reads_only = all(tc.tool_name == "read" for tc in recent)
        if reads_only and len(state.files_modified) == 0:
            return TriggerResult(
                should_fire=True,
                decision_type="should_continue",
                reason=f"Passive stall: {len(recent)} consecutive reads, zero file modifications",
            )

    if state.retry_count >= t["retry_count"]:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Retry count {state.retry_count} >= {t['retry_count']}",
        )

    if state.budget_pct > t["budget_pct"]:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Token budget {state.budget_pct:.0%} used",
        )

    return None
