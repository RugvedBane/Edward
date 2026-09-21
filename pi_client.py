import json
import subprocess
import threading
from typing import Callable, Optional, List


class PiRpcClient:
    def __init__(self, command: Optional[List[str]] = None):
        self.command = command or [
            "pi", "--mode", "rpc", "--no-session",
            "--provider", "custom-proxy", "--model", "glm-5.3-flash"
        ]
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._event_handlers: list[Callable[[dict], None]] = []
        self._request_id = 0

    def on_event(self, handler: Callable[[dict], None]) -> None:
        self._event_handlers.append(handler)

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=5)

    def send(self, command: dict) -> dict:
        self._request_id += 1
        command["id"] = f"req-{self._request_id}"
        line = json.dumps(command) + "\n"
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()
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

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for raw_line in self._proc.stdout:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                for handler in self._event_handlers:
                    handler(event)
            except json.JSONDecodeError:
                pass
