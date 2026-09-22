# Contributing to Edward

## Setup

```bash
git clone <repo> && cd edward
python -m pip install -e . --no-build-isolation
python -m unittest test_product test_stepshield -v
```

No virtual environment requirements beyond Python >= 3.11; the runtime has
zero third-party dependencies (stdlib only).

## Ground rules

1. **Deterministic layer stays deterministic.** Trigger defaults in
   `edward/triggers.py` (TRIGGER_DEFAULTS) are FROZEN; changes require a
   re-run of `python benchmark.py` (expect 100% detection / 0 FP) and a
   version bump of the defaults dict.
2. **Benchmarks are contracts.** Any change touching detection, scoring, or
   policy defaults must re-run `edward demo` and, for scorer-affecting
   changes, the StepShield suite (`edward eval --suite stepshield`). Append
   results to BENCHMARK.md; raw logs go in `results/raw/` with SHA256SUMS.
3. **Robustness over cleverness.** The scorer is always advisory; every
   failure path must degrade to the deterministic rule action. Network,
   disk, and parser failures must never crash the monitoring loop.
4. **Tests are offline by default.** Never require network or GPU for
   `python -m unittest`; use the provided fakes (FakeScorer, fixtures).

## PR checklist

- [ ] `python -m unittest test_product test_stepshield` passes
- [ ] `edward demo` PASS
- [ ] BENCHMARK.md updated if detection behavior changed
- [ ] No new dependencies without discussion (stdlib-first policy)
