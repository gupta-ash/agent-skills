"""Exceptional Claude print transport; shares only finite IPC mechanics with Codex."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import queue
import re
import sys
import uuid
from urllib.parse import urlsplit

_path = Path(__file__).resolve().parents[1] / "codex" / "adapter.py"
_spec = importlib.util.spec_from_file_location("_workflow_fallback_transport", _path)
_transport = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _transport
_spec.loader.exec_module(_transport)
AdapterBase = _transport.Adapter
ProtocolError = _transport.ProtocolError
absolute = _transport.absolute

def _permission_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise ProtocolError("absolute filesystem permission root required")
    text = str(path).replace("\\", "/")
    if path.drive:
        if len(path.drive) != 2 or path.drive[1] != ":":
            raise ProtocolError("local filesystem permission root required")
        text = path.drive[0].lower() + text[2:]
    return "//" + text.lstrip("/")



class Adapter(AdapterBase):
    backend = "claude"

    def __init__(self, context):
        super().__init__(context)
        # Windows file-rule matching needs the same spelling as semantic paths.
        self.cwd = Path(context["cwd"])

    def validate_workspace(self, candidate, writes):
        if self.cwd != candidate:
            raise ProtocolError("Claude execution cwd must equal semantic candidate directory")
        if any(_transport.contained(self.cwd, root) or _transport.contained(root, self.cwd) for root in self.private):
            raise ProtocolError("Claude execution cwd intersects private state")
        # Native --safe-mode suppresses custom project instructions/definitions;
        # ordinary Git/AGENTS ancestors are not a reason to refuse this task.
        self.check_task_cli()

    def check_task_cli(self):
        # Help/parser acceptance is deliberately not filesystem-enforcement proof.
        self.check_sources()
        argv = self.argv(session_id=str(uuid.uuid4()))
        flags = sorted({value for value in argv[1:] if value.startswith("-")})
        try:
            result = _transport.subprocess.run([*argv, "--help"], cwd=str(self.cwd),
                                               stdin=_transport.subprocess.DEVNULL, capture_output=True,
                                               text=True, encoding="utf-8", timeout=20)
        except (_transport.subprocess.SubprocessError, OSError) as exc:
            raise ProtocolError("Claude task CLI capability unavailable") from exc
        advertised = set()
        for line in result.stdout.splitlines():
            if re.match(r"^\s+-", line):
                declaration = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
                advertised.update(re.findall(r"(?<!\S)--?[A-Za-z][A-Za-z0-9-]*(?=[,\s=<\[]|$)", declaration))
        missing = sorted(set(flags) - advertised)
        if result.returncode != 0 or missing:
            raise ProtocolError("Claude task CLI flags unavailable" + (": " + ", ".join(missing) if missing else ""))
        self.record("native_task_cli_capability", {"executable": str(self.executable), "flags": flags,
                                                  "basis": "task argv --help and advertised options",
                                                  "filesystem_enforcement_observed": False})

    def reserve(self):
        if self.review:
            return super().reserve()
        if self.state != "prepared":
            raise ProtocolError("launch requires prepared attempt")
        self.check_sources()
        self.binding = {"backend": "claude", "session_id": str(uuid.uuid4()), "attempt_id": self.key[1]}
        self.record("reservation", self.binding)
        self.state = "reserved"
        return self.binding

    def argv(self, *, session_id=None):
        readonly = not self.task.write_roots
        allowed = ["Read(" + _permission_path(root) + "/**)" for root in self.task.read_roots]
        if not readonly:
            # Native Write/Edit share Edit rules; cwd is the exact attempt candidate.
            allowed.append("Edit(./**)")
        denied = [name + "(" + _permission_path(root) + suffix + ")"
                  for root in self.private for name in ("Read", "Edit") for suffix in ("", "/**")]
        denied += ["Bash", "PowerShell", "Agent", "Task", "WebFetch", "WebSearch"]
        argv = [str(self.executable), "--safe-mode", "--restricted", "-p", "--session-id",
                session_id if session_id is not None else self.binding["session_id"],
                "--output-format", "json", "--json-schema", _transport.dump(self.api.task_result_schema()),
                "--permission-mode", "dontAsk", "--permission-prompts", "none", "--tools",
                "Read" if readonly else "Read,Write,Edit", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--allowedTools", *allowed, "--disallowedTools", *denied]
        for root in self.task.read_roots:
            argv += ["--add-dir", str(Path(root))]
        proxy = self.context.get("proxy_url")
        if proxy is not None:
            if not isinstance(proxy, str):
                raise ProtocolError("nonsecret loopback proxy URL required")
            parsed = urlsplit(proxy)
            if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                    or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
                raise ProtocolError("nonsecret loopback proxy URL required")
            # Existing fleet proxy convention; never copy OAuth/user settings or ambient grants.
            argv += ["--settings", _transport.dump({"env": {"ANTHROPIC_AUTH_TOKEN": "dummy", "ANTHROPIC_BASE_URL": proxy}})]
        if self.context.get("model"):
            argv += ["--model", self.context["model"]]
        return argv

    def launch(self):
        if self.review:
            return super().launch()
        if self.state != "reserved":
            raise ProtocolError("reservation missing")
        self.check_sources()
        argv = self.argv()
        self.submitted = True
        self.state = "running"
        self.spawn(argv)
        self.process.stdin.write(_transport.dump(self.api.task_to_dict(self.task)))
        self.process.stdin.close()
        self.output = []

    def observe(self):
        if self.review:
            return super().observe()
        if self.state not in {"running", "cancelling"}:
            return self.snapshot()
        while True:
            try:
                line = self.inbox.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                self.output.append(line)
            else:
                self.output_eof = True
        if self.process.poll() is None:
            return self.snapshot()
        # Reap before reading final bytes; reader EOF is the output settlement boundary.
        if not getattr(self, "output_eof", False):
            while True:
                try:
                    line = self.inbox.get(timeout=1)
                except queue.Empty:
                    self.state = "unknown"
                    return self.snapshot()
                if line is None:
                    self.output_eof = True
                    break
                self.output.append(line)
        self.record("process_settled", {"exit_code": self.process.returncode})
        if self.cancel_requested:
            self.state = "cancelled"
            return self.snapshot()
        native = json.loads("".join(self.output))
        self.record("native_result", native)
        self.identity = {"session_id": native.get("session_id"), "model_usage": native.get("modelUsage")}
        if self.process.returncode != 0 or native.get("subtype") != "success" or native.get("is_error") is not False:
            self.state = "failed"
        elif native.get("session_id") != self.binding["session_id"]:
            raise ProtocolError("native session mismatch")
        elif native.get("permission_denials"):
            self.denied = True
            self.state = "blocked"
        else:
            self.result = self.validate(native["structured_output"])
            self.state = "completed"
        return self.snapshot()


if __name__ == "__main__":
    _transport.main(Adapter)
