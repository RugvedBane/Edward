# Agent Control Plane

Agents fail quietly. They retry the same broken test 40 times, burn $8 in tokens on a loop, run `rm -rf` on a database directory, and write to files they were never supposed to touch. The agent doesn't know it's failing. From its perspective, it's still trying.

This is an external control layer that sits between the agent and its runtime. It watches the event stream, builds a picture of what the agent is actually doing across turns, and intervenes when the picture stops looking right.

```
Agent (Pi / Codex / custom)
    │ events
    ▼
Canonical Event Schema   ← normalizes tool names to capabilities
    │
    ▼
State Engine             ← materializes cross-turn agent state
    │
    ▼
Trigger Rules [FROZEN]   ← deterministic safety + convergence checks
    │
    ├─ HARD_CONSTRAINT ──→ Kernel: BLOCK (Jev cannot override)
    │
    └─ SOFT_DECISION ──→ Jev (TypeSafe) ──→ Policy Resolver
                              │                │
                              └────────────────┘
                                       │
                                       ▼
                               Control Kernel
                                       │
                                       ▼
                                PAUSE / CANCEL / RESUME
```

## Why not just if/else?

A simple watchdog (`error_count > 5 → stop`) looks at individual events. The failure modes that actually kill long-running agents don't show up in any single event. They emerge from the shape of the trajectory over time.

We tested this directly.

### Watchdog vs State Engine → MSS

Held-out evaluation, 20 trials × 6 scenarios, frozen config:

| Scenario | Watchdog detection | State Engine detection | Watchdog ctx | State ctx |
|---|---|---|---|---|
| budget bleed | 20% | **100%** | 2,322 | 409 |
| infinite loop | 0% | **100%** | 6,084 | 409 |
| convergence stall | 0% | **100%** | 1,442 | 409 |
| dangerous command | 75% | **100%** | 522 | 434 |

The watchdog misses infinite loops and stalls entirely because it has no memory across turns. The State Engine maintains a sliding window of recent tool calls, tracks file modifications, and computes elapsed time — none of which fit in a single-event check.

## What it detects

Six trigger rules, tuned on a development set (seed 42) and evaluated frozen on held-out data (seed 137):

| Signal | Threshold | Fires when |
|---|---|---|
| Error rate | > 40% over last 8+ calls | Agent is failing repeatedly, with no recovery signal |
| Retry count | ≥ 3 | Agent keeps retrying the same thing |
| Token budget | > 80% consumed | Agent is burning resources |
| Convergence stall | > 600s + > 5 turns | Agent is running but not completing |
| Passive stall | 12 consecutive reads, 0 writes | Agent is going in circles |
| Dangerous command | regex match | Agent executes `rm -rf`, `sudo`, `git push --force`, etc. |

Plus two post-hoc additions from robustness testing:

| Signal | Fires when |
|---|---|
| Scope violation | Agent writes to a path outside the allowed prefix list |
| Silent corruption risk | ≥ 10 consecutive file writes with zero shell verification |

## Held-out benchmark

50 trials × 6 scenarios, frozen configuration (no post-hoc tuning):

```
                   Detection    FP Rate    Latency (tool calls)
infinite_loop      100%         —          8.0
budget_bleed       100%         —          11.7
dangerous          100%         —          5.0
stall              100%         —          4.0
normal             —            0%         —
transient_failure  —            0%         —

Precision: 100%    Recall: 100%    FPR: 0/100
```

## Robustness: where it breaks

We attacked the architecture along four dimensions. Results after P0+P1 fixes:

**Agent heterogeneity.** Three event schemas (Pi, Codex-style, custom) normalized through the canonical schema layer. Custom agents use tool names like `execute` and `terminal` instead of `bash`. Before the fix, dangerous command detection on custom agents was 0%. After: 100% across all six combinations.

**Task distribution shift.** Three unseen normal task profiles (heavy-write, error-then-recover, mixed-signal). The error-then-recover profile initially produced 60% false positives because the error-rate trigger couldn't distinguish "temporarily failing but recovering" from "systematically diverging." Added a `recovery_signal` property: if the tail of the sliding window shows ≥3 consecutive successes after errors, the trigger holds. Post-fix FPR: 0% on all three profiles.

**Controller ablation.** Jev (TypeSafe's System One model) acts as a probabilistic second opinion on soft decisions. On dangerous commands, rules fire deterministically as HARD_CONSTRAINT — Jev cannot override. On soft decisions, Jev confirms ~100% of infinite-loop interventions but overrides ~97% of budget-bleed and ~100% of stall interventions back to CONTINUE. This tension is a design feature: the policy resolver lets deterministic safety guards override probabilistic judgments, but not vice versa.

**Unseen failure modes.** Three novel failure scenarios not in dev or test:

| Scenario | Detection | Mechanism |
|---|---|---|
| Permission escalation | 100% | Dangerous command regex |
| Cascading failure | 100% | Retry count trigger |
| Silent corruption | 76.7% | Unverified-write streak detector |

Silent corruption — the agent writes 15 files in a row without running any test — was invisible to every original trigger. The streak detector catches most cases but not all (some trials have only 8-9 writes, below the threshold). Full coverage would require World State tracking: comparing what the agent wrote against what the task expected.

## The four things we learned

1. **Execution telemetry ≠ world state.** The agent's own view ("I'm still trying") diverges from what an external observer sees (error rate rising, budget depleting, no progress). This gap is the entire reason a control plane exists.

2. **Event-level checks cannot detect temporal failure modes.** Every trigger that requires memory across turns (stall, convergence, recovery) needs the State Engine. Single-event or simple-counter approaches score 0% on these.

3. **Safety and intelligence need different authority levels.** Dangerous-command blocking is deterministic and non-overridable. Everything else benefits from a probabilistic second opinion. Conflating the two makes the safety guard probabilistic, which defeats the purpose.

4. **Agent-execution monitoring alone cannot detect world-state corruption.** If the agent writes destructive content without producing any error, no amount of execution telemetry will catch it. This requires comparing actual filesystem state against a task contract. It's the clearest motivation for v0.2.

## Running it

### Product quickstart (agentguard CLI)

```bash
pip install -e .            # zero dependencies, stdlib only (Python >= 3.11)

# optional: the pi coding agent (full monitoring + intervention on pi tasks)
npm install -g --ignore-scripts @earendil-works/pi-coding-agent

agentguard doctor           # environment checks: scorer, pi, audit dir
agentguard demo             # self-running proof: 6 scenarios, PASS/FAIL gate
agentguard wrap -- pi "Fix the bug in utils.py so that the test passes"
agentguard wrap --scope ./src --no-scorer -- python agent_script.py   # any command, rule-only
agentguard eval --policy conservative --trials 30   # tune a policy before live use
agentguard audit            # intervention summary ($ saved evidence)
agentguard policy-template --preset balanced > agentguard.toml
```

The semantic scorer is an internal HTTP endpoint (LAN only, no auth):
`GET /health` + `POST /v1/score` on Qwen/Qwen3.5-4B — see `agentguard/jev_client.py`.
Without it, agentguard runs in rule-only mode and stays fully protective.

Exit codes: `0` completed, `75` PAUSED (resumable: `agentguard wrap --continue -- ...`),
`76` terminated by control plane, `130` interrupted. Audit JSONL lands in
`~/.agentguard/audit.jsonl`. Policy packs are TOML/JSON with three presets
(`conservative` / `balanced` = FROZEN defaults / `aggressive`).

### Research scripts (pre-packaging, still work)

```bash
python main.py "Fix the bug in utils.py so that the test passes"

# Run the benchmark
python benchmark.py

# Run the ablation
python ablation.py

# Run held-out evaluation
python heldout_eval.py

# Run robustness evaluation
python robustness_eval.py
```

Pi uses `--mode rpc` for headless operation. The control plane spawns it as a subprocess, reads JSONL events from stdout, and sends control commands (abort, steer) via stdin. No Pi source code is modified.

## Project structure

```
agentguard/            Product package (pip install -e .)
  cli.py               agentguard CLI: wrap / demo / eval / audit / doctor
  engine.py            ControlPlane: events -> triggers -> scorer -> decision -> audit
  config.py            Policy packs (TOML/JSON, 3 presets, strict validation)
  audit.py             Append-only JSONL audit log (never blocks monitoring)
  scorer.py            Semantic scorer client with circuit breaker
  scenarios.py         Failure scenario suite (single source for demo/eval)
  evalcmd.py           Policy evaluation gate (detection / FPR / timing)
  pi_client.py         Pi RPC client (cwd, provider/model, stderr capture)
  jev_client.py        /v1/score client (internal endpoint, Qwen3.5-4B)
  state_engine.py      Materialize AgentState from event stream
  triggers.py          8 trigger rules, policy-parameterized (defaults FROZEN)
  kernel.py            Decision authority hierarchy
  canonical_events.py  Normalize Pi/Codex/custom events to canonical schema
  notify.py            Slack webhook + stderr banners (fail-silent)
main.py                Legacy entry -> agentguard wrap
benchmark.py           300-trial held-out benchmark (re-exports scenarios)
ablation.py            Watchdog vs State Engine comparison
heldout_eval.py        Dev/test split + frozen config evaluation
robustness_eval.py     4-dimension robustness attack
extreme_tests.py       4 extreme scenario demos
test_product.py        Product test suite (unittest, offline)
```

## What's next

The silent-corruption gap points at the next layer: a Task Contract that defines which files the agent should touch and what the expected end-state looks like. When the agent's actual filesystem mutations diverge from the contract, that's a scope violation — regardless of whether any tool call returned an error. This moves the State Engine from "what is the agent doing" to "what is the agent doing to the world, and is that still allowed."
