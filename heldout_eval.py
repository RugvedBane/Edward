import json
import random
from typing import Dict, List

from benchmark import (
    SCENARIOS,
    run_trial,
    compute_metrics,
)
from ablation import run_ablation, compute_metrics as compute_ablation_metrics


DEV_SEED = 42
TEST_SEED = 137


def run_heldout(n_dev: int = 30, n_test: int = 50):
    print("Agent Control Plane — Development / Held-out Evaluation")
    print("=" * 80)

    print(f"\nPhase 1: Development set (seed={DEV_SEED}, {n_dev} trials/scenario)")
    print("  [Used for trigger threshold tuning — already done]")

    dev_rng = random.Random(DEV_SEED)
    for scenario in SCENARIOS:
        for i in range(n_dev):
            run_trial(scenario, i, dev_rng)

    print(f"  Dev set: {n_dev} × {len(SCENARIOS)} = {n_dev * len(SCENARIOS)} trials generated")

    print(f"\nPhase 2: Frozen configuration (no further tuning)")
    print("  Trigger rules and thresholds from triggers.py [FROZEN]")

    print(f"\nPhase 3: Held-out evaluation (seed={TEST_SEED}, {n_test} trials/scenario)")

    test_rng = random.Random(TEST_SEED)
    all_results = {}
    for scenario in SCENARIOS:
        results = [run_trial(scenario, i, test_rng) for i in range(n_test)]
        all_results[scenario] = results

    metrics = compute_metrics(all_results)

    print(f"\n{'Scenario':<20} {'Type':<10} {'N':>5} {'Detection':>10} {'FP Rate':>10} {'Latency':>10}")
    print("-" * 80)
    for scenario, m in metrics.items():
        if m["type"] == "abnormal":
            print(f"{scenario:<20} {'abnormal':<10} {m['n']:>5} {m['detection_rate']:>9.1%} {'—':>10} {m['avg_tool_calls_to_intervention']:>10.1f}")
        else:
            print(f"{scenario:<20} {'normal':<10} {m['n']:>5} {'—':>10} {m['false_intervention_rate']:>9.1%} {'—':>10}")

    total_tp = sum(m.get("tp", 0) for m in metrics.values())
    total_fn = sum(m.get("fn", 0) for m in metrics.values())
    total_fp = sum(m.get("fp", 0) for m in metrics.values())
    total_tn = sum(m.get("tn", 0) for m in metrics.values())

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0

    print(f"\nHELD-OUT RESULTS:")
    print(f"  Precision: {precision:.1%}")
    print(f"  Recall:    {recall:.1%}")
    print(f"  FP count:  {total_fp} / {total_tn + total_fp} normal trials")

    print(f"\nPhase 4: Ablation on held-out set (watchdog vs State Engine → MSS)")
    ablation_results = run_ablation(n_trials=20)
    ablation_metrics = compute_ablation_metrics(ablation_results)

    print(f"\n{'Scenario':<20} {'Method':<20} {'Type':<10} {'Rate':>8} {'Latency':>8} {'Ctx':>8}")
    print("-" * 80)
    for (sc, m), v in sorted(ablation_metrics.items()):
        if v["type"] == "abnormal":
            print(f"{sc:<20} {m:<20} {'detect':<10} {v['detection_rate']:>7.1%} {v['avg_latency']:>8.1f} {v['avg_context']:>8}")
        else:
            print(f"{sc:<20} {m:<20} {'FP rate':<10} {v['fp_rate']:>7.1%} {'—':>8} {v['avg_context']:>8}")

    return metrics, ablation_metrics


if __name__ == "__main__":
    run_heldout()
