"""Finite native task transport. Controller ACKs reservations; no scheduling policy here."""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid


def contracts_path():
    root = Path(__file__).resolve().parents[2]
    path = root / "contracts.py"
    return path if path.is_file() else root / "src" / "contracts.py"


def contracts():
    path = contracts_path()
    spec = importlib.util.spec_from_file_location("_workflow_contracts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("absolute path required")
    return path.resolve()


def contained(path, root):
    return path == root or root in path.parents


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_lines(stream, inbox):
    try:
        for line in stream:
            inbox.put(line)
    finally:
        inbox.put(None)


class ProtocolError(RuntimeError):
    pass


class NativeBackendUnavailable(ProtocolError):
    code = "NATIVE_BACKEND_UNAVAILABLE"


class Adapter:
    backend = "codex"

    def __init__(self, context):
        if context.get("schema_version") != 1 or context.get("backend") != self.backend:
            raise ValueError("execution context schema/backend mismatch")
        if {"capability_receipt_path", "qualification_receipt_path"} & set(context):
            raise ProtocolError("certification receipt fields are retired")
        self.context = context
        if self.backend == "codex" and type(context.get("task_enabled", False)) is not bool:
            raise ValueError("task_enabled must be a boolean")
        self.executable = absolute(context["executable"])
        if not self.executable.is_file() or self.executable.suffix.lower() in {".cmd", ".bat", ".js"}:
            raise ValueError("declared native executable required, not shell shim")
        references = context.get("source_refs")
        if not isinstance(references, list) or not references:
            raise ProtocolError("nonempty hash-bound source_refs required")
        self.source_refs = tuple(dict(value) if isinstance(value, dict) else value for value in references)
        required = [self.executable, Path(__file__).resolve(), contracts_path(),
                    Path(sys.modules[type(self).__module__].__file__).resolve()]
        if "declaration_path" in context:
            required.append(absolute(context["declaration_path"]))
        self.check_sources(*required)
        self.cwd = absolute(context["cwd"])
        self.evidence = absolute(context["evidence_dir"])
        if not self.cwd.is_dir() or not self.evidence.is_dir():
            raise ValueError("controller-created workspace/evidence directories required")
        if context["caller_host"] not in {"omp", "codex", "claude"} or context["caller_family"] not in {"gpt", "claude"}:
            raise ValueError("explicit caller host/family required")
        self.private = [absolute(p) for p in context["private_state_roots"]]
        self.api = contracts()
        self.key = None
        self.task = None
        self.review = None
        self.binding = None
        self.process = None
        self.inbox = queue.Queue()
        self.serial = 0
        self.pending = {}
        self.notifications = []
        self.native = []
        self.state = "new"
        self.cancel_requested = False
        self.submitted = False
        self.result = None
        self.turn = None
        self.agent_items = {}
        self.identity = None
        self.denied = False
        self.native_policy = {}

    def check_sources(self, *required):
        observed = set()
        for ref in self.source_refs:
            if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "media_type"}:
                raise ProtocolError("closed source InputRef required")
            if (not isinstance(ref["path"], str) or not ref["path"] or "\0" in ref["path"]
                    or ".." in ref["path"].replace("\\", "/").split("/")
                    or not isinstance(ref["sha256"], str) or len(ref["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in ref["sha256"])
                    or not isinstance(ref["media_type"], str) or not ref["media_type"] or "\0" in ref["media_type"]):
                raise ProtocolError("valid source InputRef required")
            path = absolute(ref["path"])
            if path in observed or not path.is_file() or sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
                raise ProtocolError("duplicate, missing or changed execution source")
            observed.add(path)
        if any(absolute(path) not in observed for path in required):
            raise ProtocolError("execution dependency missing from source_refs")
        if "declaration_path" in self.context:
            declaration_path = absolute(self.context["declaration_path"])
            if declaration_path not in observed:
                raise ProtocolError("execution declaration missing from source_refs")
            declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
            routes = declaration.get("workflow_native") if isinstance(declaration, dict) else None
            route = routes.get(self.backend) if isinstance(routes, dict) else None
            if (not isinstance(route, dict) or not isinstance(route.get("executable"), str)
                    or absolute(route["executable"]) != self.executable
                    or ("model" in self.context) != ("model" in route)
                    or self.context.get("model") != route.get("model")
                    or ("model" in route and (not isinstance(route["model"], str) or not route["model"].strip()))
                    or {"capability_receipt_path", "qualification_receipt_path"} & set(route)):
                raise ProtocolError("native executable/model including absence differs from declaration")

    def record(self, kind, value):
        if self.key is None:
            return
        path = self.evidence / (self.key[1] + ".adapter.jsonl")
        with path.open("a", encoding="utf-8", newline="\n") as out:
            out.write(dump({"time_ns": time.time_ns(), "kind": kind, "value": value}) + "\n")
            out.flush()
            os.fsync(out.fileno())

    def correlate(self, request):
        if set(request) != {"request_id", "run_id", "attempt_id", "owner_epoch", "operation", "payload"}:
            raise ProtocolError("closed request envelope required")
        if not all(isinstance(request[k], str) and request[k] for k in ("request_id", "run_id", "attempt_id", "operation")):
            raise ProtocolError("invalid correlation")
        if type(request["owner_epoch"]) is not int or request["owner_epoch"] < 1 or not isinstance(request["payload"], dict):
            raise ProtocolError("invalid epoch/payload")
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in request["attempt_id"]):
            raise ProtocolError("unsafe attempt identity")
        key = (request["run_id"], request["attempt_id"], request["owner_epoch"])
        if self.key is not None and key != self.key:
            raise ProtocolError("attempt/epoch mismatch")
        self.key = key

    def prepare(self, payload):
        if self.state != "new":
            raise ProtocolError("attempt already prepared")
        if set(payload) == {"task"} and self.backend == "codex":
            raise NativeBackendUnavailable("Codex task execution is unavailable by approved scope")
        self.check_sources()
        if set(payload) == {"task"}:
            self.task = self.api.task_from_dict(payload["task"])
            if (self.task.run_id, self.task.attempt_id) != self.key[:2]:
                raise ProtocolError("task identity mismatch")
            candidate = absolute(self.task.candidate_dir)
            writes = [absolute(p) for p in self.task.write_roots]
            if any(not contained(p, candidate) for p in writes):
                raise ProtocolError("writes must be confined to attempt candidate")
            reads = [absolute(p) for p in self.task.read_roots]
            if any(contained(p, secret) or contained(secret, p) for p in reads + writes for secret in self.private):
                raise ProtocolError("private state intersects task authority")
            self.validate_workspace(candidate, writes)
            if self.backend == "codex":
                self.snapshot_native_policy()
        elif set(payload) == {"review"}:
            self.review = payload["review"]
            if set(self.review) != {"caller_family", "contract", "source", "focus"}:
                raise ProtocolError("closed review request required")
            if self.review["caller_family"] != self.context["caller_family"]:
                raise ProtocolError("original family changed")
            if self.context["caller_host"] == "omp":
                raise ProtocolError("OMP reviews require actual OMP native tool")
            expected = {"codex": "gpt", "claude": "claude"}[self.context["caller_host"]]
            if self.context["caller_family"] != expected:
                raise ProtocolError("CLI discriminator caller/family declaration incompatible")
            for field in ("source", "focus"):
                if self.review[field] is not None:
                    absolute(self.review[field])
        else:
            raise ProtocolError("prepare requires task or review, no overrides")
        self.check_sources()
        version = subprocess.run([str(self.executable), "--version"], cwd=str(self.cwd),
                                 capture_output=True, text=True, encoding="utf-8", timeout=20)
        if version.returncode != 0 or not version.stdout.strip():
            raise ProtocolError("native version identity unavailable")
        self.record("native_version", {"backend": self.backend, "executable": str(self.executable),
                                       "version": version.stdout.strip()})
        self.check_sources()
        self.state = "prepared"
        self.record("prepared", payload)
        return {"backend": self.backend, "task_sha256": self.task.semantic_sha256 if self.task else None}

    def snapshot_native_policy(self):
        declaration_path = absolute(self.context["declaration_path"])
        declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        userprofile = absolute(declaration["tokens"]["__USERPROFILE__"])
        policy = userprofile / ".codex" / "AGENTS.md"
        self.native_policy = {}
        if policy.is_file():
            self.native_policy[os.path.normcase(str(policy))] = sha256(policy.read_bytes()).hexdigest()
        self.record("native_policy_snapshot", {"declaration_path": str(declaration_path), "sources": self.native_policy})

    def validate_workspace(self, candidate, writes):
        if contained(self.cwd, candidate) or contained(candidate, self.cwd):
            raise ProtocolError("Codex execution cwd must be separate from semantic candidates")
        if any(self.cwd.iterdir()):
            raise ProtocolError("Codex execution cwd must be controller-created empty scratch")
        if any((root / name).exists() for root in (self.cwd, *self.cwd.parents)
               for name in (".codex", ".agents", ".git", "AGENTS.md")):
            raise ProtocolError("Codex execution scratch has project discovery ancestors")
        if any(contained(self.cwd, root) or contained(root, self.cwd) for root in self.private):
            raise ProtocolError("Codex execution scratch intersects private state")

    def spawn(self, argv, env=None):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = subprocess.Popen(argv, cwd=str(self.cwd), env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="strict", bufsize=1,
                                        creationflags=flags, start_new_session=os.name != "nt")
        threading.Thread(target=read_lines, args=(self.process.stdout, self.inbox), daemon=True).start()
        threading.Thread(target=self.stderr, daemon=True).start()
        self.record("process", {"pid": self.process.pid, "argv": argv})

    def stderr(self):
        for line in self.process.stderr:
            self.record("stderr", line)

    def send(self, frame):
        self.record("outbound", frame)
        self.process.stdin.write(dump(frame) + "\n")
        self.process.stdin.flush()

    def frame(self, timeout=0):
        try:
            line = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            self.state = "unknown" if self.submitted else "blocked"
            raise ProtocolError("native EOF; execution must be reconciled")
        frame = json.loads(line)
        self.record("native", frame)
        self.native.append(frame)
        if "method" in frame and "id" in frame:
            self.denied = True
            self.send({"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32000, "message": "Unexpected server request denied"}})
        elif "id" in frame:
            self.pending[frame["id"]] = frame
        else:
            self.notifications.append(frame)
        return frame

    def call(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while request_id not in self.pending:
            if time.monotonic() >= deadline:
                self.state = "unknown" if self.submitted else "blocked"
                raise ProtocolError("native request timeout: " + method)
            self.frame(.1)
        response = self.pending.pop(request_id)
        if "error" in response:
            raise ProtocolError(dump(response["error"]))
        return response["result"]

    def settings(self):
        # Named profiles are mutually exclusive with the legacy sandbox argument.
        config = {"project_doc_max_bytes": 0}
        # :workspace inherits broad disk reads and temp writes; explicitly narrow both.
        filesystem = {":root": "deny", ":minimal": "read", ":tmpdir": "deny", ":slash_tmp": "deny",
                      ":workspace_roots": {".": "read"}}
        filesystem.update({str(absolute(p)): "read" for p in self.task.read_roots})
        filesystem.update({str(absolute(p)): "write" for p in self.task.write_roots})
        filesystem.update({str(p): "deny" for p in self.private})
        filesystem[str(self.cwd)] = "read"
        config["permissions.workflow-attempt"] = {"extends": ":workspace", "filesystem": filesystem, "network": {"enabled": False}}
        config["default_permissions"] = "workflow-attempt"
        if os.name == "nt":
            if self.context.get("windows_sandbox") not in {"elevated", "unelevated", "mxc"}:
                raise ProtocolError("declared existing Windows sandbox route required; no provisioning")
            config["windows.sandbox"] = self.context["windows_sandbox"]
        params = {"cwd": str(self.cwd), "approvalPolicy": "never", "approvalsReviewer": "user",
                  "permissions": "workflow-attempt", "config": config}
        if self.context.get("model"):
            params["model"] = self.context["model"]
        return params

    def check_echo(self, response):
        params = self.settings()
        if response.get("cwd") != params["cwd"] or response.get("approvalPolicy") != "never":
            raise ProtocolError("native permission/cwd echo mismatch")
        profile = response.get("activePermissionProfile")
        if not isinstance(profile, dict) or profile.get("id") != "workflow-attempt":
            raise ProtocolError("native named permission profile mismatch")
        sources = response.get("instructionSources")
        if not isinstance(sources, list) or any(type(source) is not str or not Path(source).is_absolute() for source in sources):
            raise ProtocolError("unobserved native instruction sources")
        observed = {os.path.normcase(str(absolute(source))) for source in sources}
        if len(sources) != len(self.native_policy) or observed != set(self.native_policy):
            raise ProtocolError("unexpected native instruction sources")
        for source, digest in self.native_policy.items():
            if sha256(Path(source).read_bytes()).hexdigest() != digest:
                raise ProtocolError("native global policy changed after preparation")
        # Profile provenance is not enforcement proof. Native positive/negative probes
        # must establish candidate-only writes and explicit private-state denials.

    def reserve(self):
        if self.state != "prepared":
            raise ProtocolError("launch requires prepared attempt")
        self.check_sources()
        if self.review:
            self.binding = {"backend": "discriminator", "reservation_id": str(uuid.uuid4()),
                            "caller_host": self.context["caller_host"], "caller_family": self.context["caller_family"]}
        else:
            self.spawn([str(self.executable), "app-server", "--listen", "stdio://"])
            self.identity = self.call("initialize", {"clientInfo": {"name": "wiki_workflow", "version": "1"},
                                                     "capabilities": {"experimentalApi": True}})
            self.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            response = self.call("thread/start", self.settings())
            self.check_echo(response)
            thread_id = response["thread"]["id"]
            self.binding = {"backend": "codex", "thread_id": thread_id, "client_user_message_id": self.key[1],
                            "server_identity": self.identity, "model": response.get("model")}
        self.state = "reserved"
        self.record("reservation", self.binding)
        return self.binding

    def launch(self):
        if self.state != "reserved":
            raise ProtocolError("reservation missing")
        self.check_sources()
        if self.review:
            self.launch_review()
            return
        self.submitted = True
        self.state = "running"
        response = self.call("turn/start", {"threadId": self.binding["thread_id"],
                             "input": [{"type": "text", "text": dump(self.api.task_to_dict(self.task))}],
                             "outputSchema": self.api.task_result_schema(),
                             "clientUserMessageId": self.key[1], "cwd": str(self.cwd)})
        self.turn = response["turn"]["id"]
        self.binding = dict(self.binding, turn_id=self.turn)
        self.record("binding_updated", self.binding)

    def launch_review(self):
        declaration = self.context["discriminator"]
        python = absolute(declaration["python"])
        request = absolute(declaration["request"])
        self.check_sources(python, request)
        argv = [str(python), str(request), "--contract", self.review["contract"], "--source", self.review["source"]]
        if self.review["focus"]:
            argv += ["--prompt-file", self.review["focus"]]
        env = os.environ.copy()
        host = self.context["caller_host"]
        env["GAN_DISCRIMINATOR_CALLER_FAMILY"] = host
        env["GAN_DISCRIMINATOR_CLI"] = "claude" if host == "codex" else "codex"
        self.review_env = env
        self.submitted = True
        self.state = "running"
        self.spawn(argv, env)
        self.process.stdin.close()

    def validate(self, wire):
        result = self.api.result_from_dict(wire)
        validation = self.api.validate_result(result, self.task)
        if not validation.valid:
            raise ProtocolError(dump([dict(issue) for issue in validation.errors]))
        return wire

    def observe_review(self):
        if self.process.poll() is None:
            return self.snapshot()
        lines = []
        deadline = time.monotonic() + 5
        while True:
            try:
                line = self.inbox.get(timeout=max(.001, deadline - time.monotonic()))
            except queue.Empty:
                self.state = "unknown"
                return self.snapshot()
            if line is None:
                break
            lines.append(line)
        text = "".join(lines).strip()
        self.record("review_launch_exit", {"exit_code": self.process.returncode, "stdout": text})
        if text.startswith("disc-"):
            handle = text.splitlines()[0]
            declaration = self.context["discriminator"]
            self.check_sources(absolute(declaration["python"]), absolute(declaration["request"]))
            collected = subprocess.run([str(absolute(declaration["python"])), str(absolute(declaration["request"])), "--collect", handle],
                                       cwd=str(self.cwd), env=self.review_env, capture_output=True, text=True, encoding="utf-8")
            self.result = json.loads(collected.stdout)
            self.record("review_collected", {"exit_code": collected.returncode, "result": self.result})
            if collected.returncode != self.process.returncode:
                raise ProtocolError("native review launch/collect exit codes disagree")
        elif text.startswith("{"):
            self.result = json.loads(text)
        else:
            self.state = "unknown"
            return self.snapshot()
        self.state = "completed" if (isinstance(self.result, dict) and self.result.get("run_state") is None
                                     and self.process.returncode in (0, 1, 2, 4)) else "unknown"
        return self.snapshot()

    def observe(self):
        if self.review and self.state in {"running", "cancelling"}:
            return self.observe_review()
        if self.state not in {"running", "cancelling"}:
            return self.snapshot()
        while not self.inbox.empty():
            self.frame()
        for frame in self.notifications:
            params = frame.get("params", {})
            if params.get("threadId") not in (None, self.binding["thread_id"]):
                raise ProtocolError("foreign native thread notification")
            if params.get("turnId") not in (None, self.turn):
                raise ProtocolError("foreign native turn notification")
            if frame.get("method") == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                    self.agent_items[item["id"]] = item.get("text", "")
            if frame.get("method") == "turn/completed":
                turn = params["turn"]
                if turn["id"] != self.turn:
                    raise ProtocolError("wrong completed turn")
                self.record("turn_settled", turn)
                if self.cancel_requested:
                    self.state = "cancelled"
                elif self.denied:
                    self.state = "blocked"
                elif turn.get("status") == "completed":
                    values = list(self.agent_items.values())
                    if len(values) != 1:
                        raise ProtocolError("exactly one strict native result required")
                    self.result = self.validate(json.loads(values[0]))
                    self.state = "completed"
                else:
                    self.state = "failed"
        self.notifications.clear()
        return self.snapshot()

    def cancel(self):
        self.cancel_requested = True
        self.record("cancel_requested", self.binding)
        self.observe()
        if self.state in {"completed", "cancelled", "failed", "blocked"}:
            return self.snapshot()
        if self.process and self.process.poll() is None:
            if self.backend == "codex" and self.turn and not self.review:
                try:
                    self.call("turn/interrupt", {"threadId": self.binding["thread_id"], "turnId": self.turn})
                except ProtocolError as exc:
                    self.record("interrupt_refused", {"thread_id": self.binding["thread_id"], "turn_id": self.turn, "message": str(exc)})
                    # A submitted turn may still be queued behind native startup. Stop its owned process, not a guessed active turn.
                    self.close()
                    self.state = "cancelled" if self.process.poll() is not None else "unknown"
                    return self.snapshot()
                self.state = "cancelling"
            else:
                self.interrupt()
                self.state = "cancelling"
        elif self.state in {"prepared", "reserved"}:
            self.state = "cancelled"
        return self.snapshot()

    def interrupt(self):
        if os.name == "nt":
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(self.process.pid, signal.SIGINT)
        self.record("interrupt_sent", {"pid": self.process.pid})

    def close(self):
        if self.process and self.process.poll() is None:
            self.record("forced_tree_termination_requested", {"pid": self.process.pid})
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
            try:
                self.process.wait(timeout=10)
                self.record("process_settled", {"exit_code": self.process.returncode})
            except subprocess.TimeoutExpired:
                self.record("settlement_unknown", {"pid": self.process.pid})

    def reconcile(self, payload):
        binding = payload.get("binding")
        if self.binding is None:
            if not isinstance(binding, dict):
                raise ProtocolError("known native binding required")
            self.binding = binding
            self.state = "unknown"
            if binding.get("backend") == "codex" and binding.get("thread_id") and self.task:
                self.spawn([str(self.executable), "app-server", "--listen", "stdio://"])
                self.identity = self.call("initialize", {"clientInfo": {"name": "wiki_workflow", "version": "1"},
                                                         "capabilities": {"experimentalApi": True}})
                self.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                response = self.call("thread/resume", dict(self.settings(), threadId=binding["thread_id"]))
                self.check_echo(response)
                thread = response["thread"]
                if thread["id"] != binding["thread_id"]:
                    raise ProtocolError("resumed thread mismatch")
                self.turn = binding.get("turn_id")
                turns = [turn for turn in thread.get("turns", []) if turn.get("id") == self.turn]
                self.record("reconciled_thread", response)
                if len(turns) == 1 and turns[0].get("status") == "completed":
                    messages = [item["text"] for item in turns[0].get("items", []) if item.get("type") == "agentMessage" and item.get("phase") == "final_answer"]
                    if len(messages) == 1:
                        self.result = self.validate(json.loads(messages[0]))
                        self.state = "completed"
                elif len(turns) == 1 and turns[0].get("status") in {"failed", "interrupted"}:
                    self.state = "cancelled" if turns[0]["status"] == "interrupted" else "failed"
                # A running/missing turn is retained unknown; never turn/start on recovery.
        elif binding != self.binding:
            raise ProtocolError("binding mismatch")
        return self.observe()

    def snapshot(self):
        return {"binding": self.binding, "native": {"identity": self.identity, "state": self.state,
                "process_exit_code": self.process.poll() if self.process else None,
                "cancel_requested": self.cancel_requested, "approval_denied": self.denied},
                "result": self.result, "settled": self.state in {"completed", "failed", "cancelled", "blocked"}}


def main(adapter_type=Adapter):
    # JSON-lines IPC is UTF-8 regardless of the Windows console/pipe code page.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    args = parser.parse_args()
    context = json.loads(absolute(args.context).read_text(encoding="utf-8"))
    adapter = adapter_type(context)
    incoming = queue.Queue()
    threading.Thread(target=read_lines, args=(sys.stdin, incoming), daemon=True).start()

    def reply(request, status, payload=None, error=None):
        result = {k: request[k] for k in ("request_id", "run_id", "attempt_id", "owner_epoch")}
        result.update(status=status, payload=payload or {}, error=error)
        print(dump(result), flush=True)

    try:
        while True:
            line = incoming.get()
            if line is None:
                break
            request = json.loads(line)
            try:
                adapter.correlate(request)
                op, payload = request["operation"], request["payload"]
                if op == "prepare":
                    reply(request, "prepared", adapter.prepare(payload))
                elif op == "launch_with_binding_barrier":
                    binding = adapter.reserve()
                    reply(request, "binding_required", {"binding": binding})
                    line = incoming.get(timeout=5)
                    if line is None:
                        raise ProtocolError("EOF before binding ACK")
                    ack = json.loads(line)
                    adapter.correlate(ack)
                    if any(ack[k] != request[k] for k in ("request_id", "run_id", "attempt_id", "owner_epoch")) or ack["operation"] != "binding_ack" or ack["payload"].get("committed") is not True or ack["payload"] != {"committed": True, "binding": binding}:
                        raise ProtocolError("durable binding ACK mismatch")
                    adapter.record("binding_ack", ack)
                    adapter.launch()
                    reply(request, "binding_updated", {"binding": adapter.binding})
                    reply(request, adapter.state, adapter.snapshot())
                elif op in {"observe", "cancel", "reconcile"}:
                    value = adapter.reconcile(payload) if op == "reconcile" else getattr(adapter, op)()
                    reply(request, adapter.state, value)
                else:
                    raise ProtocolError("unknown operation")
            except Exception as exc:
                adapter.state = "unknown" if adapter.submitted else "blocked"
                adapter.record("adapter_error", {"type": type(exc).__name__, "message": str(exc)})
                reply(request, adapter.state, adapter.snapshot(), {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc), "details": {}})
                if line is None:
                    break
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
