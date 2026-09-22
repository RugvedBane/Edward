# Changelog

All notable changes to Edward are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is semver.

## [Unreleased]

### Changed
- StepShield contract probe default is now `v1b` (evidence enrichment:
  temporal context, deterministic counters, keyword-guided excerpts);
  holdout: recall 57.4%→58.3%, FPR 20.4%→17.6%, precision 72.9%→76.8%.
  Probe styles v1c/v2a–v2d available for study; see BENCHMARK.md.

## [0.1.1] - 2026-09-22

### Added
- $ saved estimation: interventions record est. avoided spend (burn-to-budget
  model); `edward audit` aggregates it
- `EDWARD_SCORER_URL` environment override for the scorer endpoint
- Deployment templates for team-LAN topology (`deploy/`: Dockerfile,
  docker-compose.yml, runbook)

### Changed
- PyPI distribution name is `edward-guard` (the `edward` name is taken);
  brand, package import, and CLI remain `edward`

## [0.1.0] - 2026-09-22

First public release.

### Added
- `edward` CLI: `wrap` / `demo` / `eval` / `audit` / `doctor` / `policy-template`
- External control plane: canonical event schema (pi / Codex / custom agents),
  cross-turn StateEngine, 8 trigger rules (FROZEN defaults, policy-parameterized)
- Semantic scorer client for any OpenAI-compatible local endpoint, with a
  circuit breaker (rule-only fallback when the scorer is down)
- Evidence-grounded task-contract probe mode (StepShield integration)
- Policy packs: TOML/JSON, three presets (conservative / balanced / aggressive)
- Audit JSONL with rotation; PAUSE/resume semantics (exit 75, session pinning,
  `--continue` from audit); exit codes 0 / 75 / 76 / 130
- Scenario suite + policy evaluation gate (`edward demo`, `edward eval`)
- StepShield (NeurIPS 2026) benchmark adapter with paper-aligned EIR metrics
- Product test suite (42 offline tests) and CI matrix (linux/windows/macos)
