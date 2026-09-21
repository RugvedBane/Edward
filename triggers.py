from dataclasses import dataclass
from typing import Optional

from state_engine import AgentState


@dataclass
class TriggerResult:
    should_fire: bool
    decision_type: str
    reason: str


def check_triggers(state: AgentState) -> Optional[TriggerResult]:
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

    if state.elapsed_seconds > 600 and state.turn_count > 5:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Convergence stall: {state.elapsed_seconds:.0f}s elapsed, {state.turn_count} turns, no completion signal",
        )

    if state.error_rate > 0.4 and len(state.recent_tool_calls) >= 8 and not state.recovery_signal:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"High error rate: {state.error_rate:.1%} over last {min(10, len(state.recent_tool_calls))} tool calls",
        )

    if state.error_trend_rising and state.error_rate > 0.5 and len(state.recent_tool_calls) >= 8 and not state.recovery_signal:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Diverging: error trend rising ({state.error_rate:.1%}), no recovery signal",
        )

    if state.consecutive_unverified_writes >= 10:
        return TriggerResult(
            should_fire=True,
            decision_type="request_permission",
            reason=f"Silent corruption risk: {state.consecutive_unverified_writes} consecutive writes without any shell verification",
        )

    if len(state.recent_tool_calls) >= 12:
        recent = state.recent_tool_calls[-12:]
        reads_only = all(tc.tool_name == "read" for tc in recent)
        if reads_only and len(state.files_modified) == 0:
            return TriggerResult(
                should_fire=True,
                decision_type="should_continue",
                reason=f"Passive stall: {len(recent)} consecutive reads, zero file modifications",
            )

    if state.retry_count >= 3:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Retry count {state.retry_count} >= 3",
        )

    if state.budget_pct > 0.8:
        return TriggerResult(
            should_fire=True,
            decision_type="should_continue",
            reason=f"Token budget {state.budget_pct:.0%} used",
        )

    return None
