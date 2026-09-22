"""RPC client for the Pi coding agent (and compatible runtimes).

Spawned as a subprocess in RPC mode; JSONL events on stdout, control
commands on stdin. Improvements over the prototype: configurable cwd /
provider / model, stderr capture for crash diagnostics, robust stop
ladder, liveness checks.
"""

import json
import os
import subprocess
import sys
import threading
from typing import Callable, List, Optional


STDERR_TAIL_LINES = 40


class PiRpcClient:
    def __init__(self, command: Optional[List[str]] = None, cwd: Optional[str] = None,
                 provider: Optional[str] = None, model: Optional[str] = None):
        if command is None:
            provider = provider or os.environ.get("AGENTGUARD_PI_PROVIDER", "custom-proxy")
            model = model or os.environ.get("AGENTGUARD_PI_MODEL", "glm-5.3-flash")
            command = ["pi", "--mode", "rpc", "--no-session", "--provider", provider, "--model", model]
        self.command = [str(c) for c in command]
        self.cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_tail: List[str] = []
        self._event_handlers: list[Callable[[dict], None]] = []
        self._request_id = 0
        self._closed = False

    def on_event(self, handler: Callable[[dict], None]) -> None:
        self._event_handlers.append(handler)

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-STDERR_TAIL_LINES:])

    def stop(self) -> None:
        """Best-effort stop ladder: close stdin -> terminate -> kill."""
        if self._proc is None or self._closed:
            return
        self._closed = True
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            self._proc.kill()
            self._proc.wait(timeout=3)
        except OSError:
            pass

    def send(self, command: dict) -> dict:
        self._request_id += 1
        command["id"] = f"req-{self._request_id}"
        if self._proc and self._proc.stdin and not self._proc.stdin.closed:
            try:
                self._proc.stdin.write((json.dumps(command) + "\n").encode())
                self._proc.stdin.flush()
            except (OSError, ValueError):
                pass
        return command

    def prompt(self, message: str) -> dict:
        return self.send({"type": "prompt", "message": message})

    def abort(self) -> dict:
        return self.send({"type": "abort"})

    def steer(self, message: str) -> dict:
        return self.send({"type": "steer", "message": message})

    def follow_up(self, message: str) -> dict:
        return self.send({"type": "follow_up", "message": message})

    def get_state(self) -> dict:
        return self.send({"type": "get_state"})

    def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            for raw in self._proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_tail.append(line)
                    if len(self._stderr_tail) > STDERR_TAIL_LINES:
                        self._stderr_tail.pop(0)
        except (OSError, ValueError):
            pass

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            for raw_line in self._proc.stdout:
                if self._closed:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                for handler in self._event_handlers:
                    try:
                        handler(event)
                    except Exception as exc:
                        print(f"[agentguard] event handler error (ignored): {exc}", file=sys.stderr)
        except (OSError, ValueError):
            pass
