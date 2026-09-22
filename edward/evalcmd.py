"""Policy evaluation under the ControlPlane (with triggers/policy/scorer),
replacing the raw state-engine sweep used by the research benchmark.
"""

import random
import time

from .engine import ControlPlane
from .scenarios import SCENARIOS


def eval_policy(policy, n_trials: int = 30, scorer=None, seed: int = 137) -> dict:
    policy.stderr_banner = False
    results = {}
    for scenario, (gen_func, should_not_trigger) in SCENARIOS.items():
        rng = random.Random(seed)
        n = n_trials
        detected = 0
        latencies = []
        for i in range(n):
            generated = gen_func(i, rng)
            if isinstance(generated, tuple):
                events, duration = generated
            else:
                events, duration = generated, 0
            plane = ControlPlane(policy, session=f"eval-{scenario}-{i}", scorer=scorer)
            if duration:
                plane.state.started_at = time.time() - duration
            decision = None
            tool_calls = 0
            for event in events:
                if event.get("type") == "tool_execution_end":
                    tool_calls += 1
                decision = plane.process_event(event)
                if decision:
                    break
            if decision:
                detected += 1
                latencies.append(tool_calls)
        if should_not_trigger:
            results[scenario] = {"type": "normal", "n": n, "fp": detected,
                                 "fpr": detected / n if n else 0.0}
        else:
            results[scenario] = {
                "type": "abnormal", "n": n, "detected": detected,
                "detection_rate": detected / n if n else 0.0,
                "avg_latency": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            }
    return results


def verdict(metrics: dict) -> bool:
    """Product gate: every abnormal scenario detected, zero false positives."""
    for scenario, m in metrics.items():
        if m["type"] == "abnormal" and m["detection_rate"] < 1.0:
            return False
        if m["type"] == "normal" and m["fp"] > 0:
            return False
    return True
