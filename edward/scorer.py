"""Semantic scorer client with a circuit breaker.

Wraps ScorerClient (POST /v1/score against an OpenAI-compatible internal
scorer). After 2 consecutive failures the breaker opens for 60s: calls
return None immediately and the control plane runs rule-only. Breaker
recovers on first success. The scorer is always advisory — every None
path degrades to the deterministic rule action.
"""

import time

from .scorer_client import ScorerClient


BREAKER_THRESHOLD = 2
BREAKER_COOLDOWN_SECONDS = 60.0


class Scorer:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.client = ScorerClient(base_url=base_url, timeout=timeout)
        self.fail_streak = 0
        self.open_until = 0.0
        self.total_calls = 0
        self.total_failures = 0

    def health(self):
        return self.client.health()

    def consult(self, mss: dict, trigger_reason: str = ""):
        now = time.time()
        if now < self.open_until:
            return None
        self.total_calls += 1
        try:
            result = self.client.ask_continue(mss, trigger_reason)
        except Exception:
            result = None
        if result is None:
            self.total_failures += 1
            self.fail_streak += 1
            if self.fail_streak >= BREAKER_THRESHOLD:
                self.open_until = now + BREAKER_COOLDOWN_SECONDS
            return None
        self.fail_streak = 0
        self.open_until = 0.0
        return result
