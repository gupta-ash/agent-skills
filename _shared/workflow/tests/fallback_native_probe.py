"""Explicit opt-in native probe. Parent supplies synthetic task and declared context.

Run separately for positive, inaccessible-input/permission-negative, and cancellation tasks.
Never makes a task or receipt into evidence of publication/business acceptance.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def pump(stream, inbox):
    for line in stream:
        inbox.put(line)
    inbox.put(None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=("codex", "claude"))
    parser.add_argument("--context", required=True)
    parser.add_argument("--adapter-command", required=True, help="JSON argv for the exact source or installed adapter")
    parser.add_argument("--task", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--expect", required=True, choices=("completed", "blocked", "failed", "cancelled"))
    parser.add_argument("--cancel", action="store_true")
    parser.add_argument("--result-status", choices=("completed", "blocked", "failed", "cancelled"))
    args = parser.parse_args()
    command = json.loads(args.adapter_command)
    if (not isinstance(command, list) or not command or any(type(arg) is not str or not arg or "\0" in arg for arg in command)
            or not Path(command[0]).is_absolute() or not Path(command[0]).is_file() or "--context" in command):
        parser.error("--adapter-command must be explicit JSON argv with an absolute native interpreter and no context override")
    adapters = [Path(arg) for arg in command if Path(arg).name == "adapter.py"]
    if len(adapters) != 1 or not adapters[0].is_absolute() or not adapters[0].is_file() or adapters[0].is_symlink() or adapters[0].parent.name != args.backend:
        parser.error("--adapter-command must select exactly the declared backend's regular adapter.py")
    if Path(command[-1]) != adapters[0] or any(arg not in ("-I", "-B", "-S") for arg in command[1:-1]):
        parser.error("--adapter-command must execute that adapter directly, not -c/-m or another script")
    if args.backend == "codex":
        parser.error("Codex task probes are unavailable; inspect its pre-dispatch refusal without native execution")
    context_path, task_path, evidence = map(lambda p: Path(p).resolve(), (args.context, args.task, args.evidence))
    task = json.loads(task_path.read_text(encoding="utf-8"))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context["backend"] != args.backend:
        raise ValueError("backend/context mismatch")
    evidence.mkdir(parents=True, exist_ok=False)
    adapter = adapters[0]
    runtime = adapter.parents[2]
    contracts = runtime / "contracts.py"
    if not contracts.is_file():
        contracts = runtime / "src" / "contracts.py"
    loaded = [adapter, runtime / "harness" / "codex" / "adapter.py", contracts, Path(command[0]),
              Path(context["executable"]), context_path, task_path, Path(__file__).resolve()]
    if "declaration_path" in context:
        loaded.append(Path(context["declaration_path"]))
    loaded = list(dict.fromkeys(path.resolve() for path in loaded))
    media = {".json": "application/json", ".py": "text/x-python", ".ts": "text/typescript", ".md": "text/markdown"}
    loaded_refs = [{"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
                    "media_type": media.get(path.suffix, "application/octet-stream")} for path in loaded]
    sources = context.get("source_refs", [])
    if not isinstance(sources, list):
        raise ValueError("probe source_refs must be an array")
    sources = list(sources)
    for ref in loaded_refs:
        existing = [value for value in sources if isinstance(value, dict) and value.get("path") == ref["path"]]
        if existing and existing != [ref]:
            raise ValueError("probe source binding differs from the declared context")
        if not existing:
            sources.append(ref)
    context["source_refs"] = sources
    execution_context = evidence / "adapter-context.json"
    execution_context.write_text(json.dumps(context, indent=2), encoding="utf-8")
    loaded.append(execution_context)
    loaded_refs.append({"path": str(execution_context), "sha256": sha256(execution_context.read_bytes()).hexdigest(),
                        "media_type": "application/json"})
    child = subprocess.Popen([*command, "--context", str(execution_context)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8")
    inbox = queue.Queue()
    threading.Thread(target=pump, args=(child.stdout, inbox), daemon=True).start()
    errors = []
    threading.Thread(target=lambda: errors.extend(child.stderr), daemon=True).start()
    trace = []
    serial = 0

    def send(operation, payload, request_id=None):
        nonlocal serial
        serial += 1
        request = {"request_id": request_id or str(serial), "run_id": task["run_id"],
                   "attempt_id": task["attempt_id"], "owner_epoch": 1, "operation": operation, "payload": payload}
        trace.append({"direction": "request", "value": request})
        child.stdin.write(json.dumps(request) + "\n")
        child.stdin.flush()
        return request["request_id"]

    def receive():
        line = inbox.get(timeout=40)
        if line is None:
            raise RuntimeError("adapter EOF")
        response = json.loads(line)
        trace.append({"direction": "reply", "value": response})
        if response["status"] == "binding_required":
            # Disposable durable binding record, flushed BEFORE the ACK.
            path = evidence / "binding.json"
            with path.open("w", encoding="utf-8") as handle:
                json.dump(response["payload"]["binding"], handle)
                handle.flush()
                import os
                os.fsync(handle.fileno())
            send("binding_ack", {"committed": True, "binding": response["payload"]["binding"]}, response["request_id"])
        return response

    final = None
    try:
        send("prepare", {"task": task})
        response = receive()
        if response["status"] != "prepared":
            raise RuntimeError("prepare refused: " + json.dumps(response))
        send("launch_with_binding_barrier", {})
        while True:
            response = receive()
            if response["status"] not in {"binding_required", "binding_updated"}:
                break
        if args.cancel and response["status"] == "running":
            send("cancel", {})
            response = receive()
        deadline = time.monotonic() + 600
        while response["status"] in {"running", "cancelling"}:
            if time.monotonic() >= deadline:
                raise TimeoutError("probe deadline; no retry authority")
            time.sleep(.25)
            send("observe", {})
            response = receive()
        final = response
        if response["status"] != args.expect:
            raise AssertionError("unexpected native transport outcome: " + json.dumps(response))
        if args.result_status and response["payload"].get("result", {}).get("status") != args.result_status:
            raise AssertionError("unexpected TaskResult status")
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        (evidence / "protocol.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
        (evidence / "stderr.txt").write_text("".join(errors), encoding="utf-8")
        runtime_unchanged = all(path.is_file() and sha256(path.read_bytes()).hexdigest() == ref["sha256"] for path, ref in zip(loaded, loaded_refs))
        (evidence / "outcome.json").write_text(json.dumps({"adapter_exit": child.returncode, "final": final, "adapter_command": command,
                                                       "loaded_refs": loaded_refs, "runtime_unchanged": runtime_unchanged}, indent=2), encoding="utf-8")
        if not runtime_unchanged:
            raise RuntimeError("Bound native probe code or inputs changed during execution")


if __name__ == "__main__":
    main()
