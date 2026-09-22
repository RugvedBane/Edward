# Deploying Edward (team-LAN topology)

Three deployment tiers; this directory ships tier B.

| Tier | Where | Scorer | Notes |
|---|---|---|---|
| A Laptop | developer machine | Ollama / any local endpoint | zero infra |
| **B Team LAN** (this template) | GPU box on the internal network | dedicated scoring service | the reference production shape |
| C Cloud fleet | + cloud control plane | same, telemetry up only | paid tier, later |

## Tier B quick start

1. **Scorer service** — any HTTP service exposing:
   - `GET /health` → `{"status":"ok","model":"...","ready":true}`
   - `POST /v1/score` → `{"option_ids":[...], "probabilities":[...], ...}`

   Contract: `id` non-empty string; `state` string/JSON object/array
   (max ~4096 tokens, 422 otherwise); `question` non-empty; 2–16 options
   with unique ids; serial scoring (~60ms/decision) is fine — the control
   plane calls at most once per intervention.

2. **Wire it up**
   ```bash
   EDWARD_SCORER_URL=http://<gpu-box>:8000 edward doctor
   EDWARD_SCORER_URL=http://<gpu-box>:8000 edward wrap -- pi "task"
   ```

3. **Docker (optional)** — `docker compose up` builds the edward image and
   starts a scorer placeholder; see comments in `docker-compose.yml`.

## Rules of the road

- **No auth by design**: both services are LAN-only. Never port-forward the
  scorer to the internet.
- **Degraded is safe**: if the scorer is unreachable, `edward wrap` logs a
  warning and runs rule-only — deterministic triggers still protect the run.
- **Audit is local**: `~/.edward/audit.jsonl` on the machine running edward;
  point your backup job at it.
