from agentguard.scenarios import (  # noqa: F401
    SCENARIOS,
    TrialResult,
    compute_metrics,
    generate_budget_bleed_trial,
    generate_dangerous_trial,
    generate_infinite_loop_trial,
    generate_normal_trial,
    generate_stall_trial,
    generate_transient_failure_trial,
    run_benchmark,
    run_trial,
)

if __name__ == "__main__":
    print("Running Agent Control Plane Benchmark...")
    print(f"Trials per scenario: 50\n")
    results = run_benchmark(n_trials=50)
    metrics = compute_metrics(results)
    print(f"{'Scenario':<20} {'Type':<10} {'N':>5} {'Detection':>10} {'FP Rate':>10} {'Avg Latency':>12}")
    print("-" * 75)
    for scenario, m in metrics.items():
        if m["type"] == "abnormal":
            print(f"{scenario:<20} {'abnormal':<10} {m['n']:>5} {m['detection_rate']:>9.1%} {'—':>10} {m['avg_tool_calls_to_intervention']:>12.1f}")
        else:
            print(f"{scenario:<20} {'normal':<10} {m['n']:>5} {'—':>10} {m['false_intervention_rate']:>9.1%} {'—':>12}")
    total_tp = sum(m.get("tp", 0) for m in metrics.values())
    total_fn = sum(m.get("fn", 0) for m in metrics.values())
    total_fp = sum(m.get("fp", 0) for m in metrics.values())
    total_tn = sum(m.get("tn", 0) for m in metrics.values())
    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    print(f"\n{'='*75}")
    print(f"OVERALL:")
    print(f"  Precision: {overall_precision:.1%} ({total_tp} TP / {total_tp + total_fp} interventions)")
    print(f"  Recall:    {overall_recall:.1%} ({total_tp} TP / {total_tp + total_fn} actual abnormal)")
    print(f"  FP count:  {total_fp} out of {total_tn + total_fp} normal trials")
