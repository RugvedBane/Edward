"""Notifications: Slack-compatible webhook + stderr banner.

Every notify path is best-effort and fail-silent: an unreachable webhook
must never take down monitoring.
"""

import json
import sys
import urllib.request


def notify_stderr(action: str, reason: str, hint: str = "") -> None:
    banner = f"\n{'=' * 60}\n[EDWARD] {action}: {reason}\n"
    if hint:
        banner += f"[EDWARD] {hint}\n"
    banner += f"{'=' * 60}\n"
    try:
        print(banner, file=sys.stderr, flush=True)
    except OSError:
        pass


def notify_webhook(url: str, text: str, timeout: float = 3.0) -> bool:
    if not url:
        return False
    payload = json.dumps({"text": text}).encode()
    try:
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False
