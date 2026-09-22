# Changelog

All notable changes to Edward are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is semver.

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
