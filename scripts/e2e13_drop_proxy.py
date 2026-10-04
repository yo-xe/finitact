"""STDIO MCP proxy for E2E-13: loses the first run_windows response and reconnects to a fresh server.

Racing a kill against the response lost every time (a Notepad fill run returns about 1 s after its fill), so the
proxy withholds that response instead. The run has then delivered its fill and written its ledger row; the proxy kills
the server tree, answers the call the way a client sees a dead stdio server, and replays the handshake to a new server
so the outer agent can retry.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import threading
import time

KILL_SERVER_PS = r"""
$since = [DateTimeOffset]::FromUnixTimeSeconds(__SINCE__).LocalDateTime
$procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
  $_.CommandLine -like '*finitact.mcp_server*' -and $_.CreationDate -ge $since })
foreach ($p in $procs) { taskkill /F /T /PID $p.ProcessId | Out-Null }
@{ pids = @($procs | ForEach-Object { $_.ProcessId }) } | ConvertTo-Json -Compress
"""


class Proxy:
    def __init__(self, server: list[str], record: str) -> None:
        self.server = server
        self.record = record
        self.lock = threading.Lock()
        self.out_lock = threading.Lock()
        self.handshake: list[dict] = []
        self.drop_id = None
        self.dropped = False
        self.ready = threading.Event()
        self.child = None
        self.generation = 0

    def log(self, event: str, **fields) -> None:
        with open(self.record, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": round(time.time(), 3), "event": event, **fields}, ensure_ascii=False) + "\n")

    def emit(self, message: dict) -> None:
        with self.out_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def start(self, replay: bool) -> None:
        self.generation += 1
        started = int(time.time()) - 1
        child = subprocess.Popen(
            self.server, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
        )
        self.child, self.child_started = child, started
        threading.Thread(target=self.pump, args=(child, self.generation, replay), daemon=True).start()
        if replay:
            for message in self.handshake:
                if "id" in message:
                    message = {**message, "id": f"proxy-replay-{message['id']}"}
                self.send(child, message)
        else:
            self.ready.set()

    @staticmethod
    def send(child, message: dict) -> None:
        child.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        child.stdin.flush()

    def pump(self, child, generation: int, replay: bool) -> None:
        for line in child.stdout:
            if generation != self.generation:
                return
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if replay and str(message.get("id", "")).startswith("proxy-replay-"):
                self.log("reconnected", generation=generation)
                self.ready.set()
                continue
            if self.drop_id is not None and message.get("id") == self.drop_id:
                self.lose(message)
                continue
            self.emit(message)

    def lose(self, message: dict) -> None:
        self.dropped = True
        run_id = None
        for block in (message.get("result") or {}).get("content") or []:
            try:
                run_id = json.loads(block.get("text") or "").get("run_id") or run_id
            except (ValueError, AttributeError):
                pass
        self.ready.clear()
        self.generation += 1  # the old server's remaining output is never delivered
        script = KILL_SERVER_PS.replace("__SINCE__", str(self.child_started))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        killed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )  # fmt: skip
        lines = killed.stdout.strip().splitlines()
        pids = json.loads(lines[-1])["pids"] if lines else []
        self.child.kill()
        self.log("dropped", request_id=self.drop_id, run_id=run_id, pids=pids,
                 is_error=bool((message.get("result") or {}).get("isError")))
        self.emit({"jsonrpc": "2.0", "id": self.drop_id, "error": {"code": -32000, "message": "Connection closed"}})
        self.drop_id = None
        self.start(replay=True)

    def run(self) -> None:
        self.start(replay=False)
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            method = message.get("method")
            if method in ("initialize", "notifications/initialized"):
                self.handshake.append(message)
            elif (
                method == "tools/call"
                and not self.dropped
                and self.drop_id is None
                and str((message.get("params") or {}).get("name", "")).endswith("run_windows")
            ):
                self.drop_id = message.get("id")
                self.log("withholding", request_id=self.drop_id)
            self.ready.wait()
            self.send(self.child, message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("server", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    server = args.server[1:] if args.server[:1] == ["--"] else args.server
    Proxy(server, args.record).run()


if __name__ == "__main__":
    main()
