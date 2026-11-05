"""Source-only adapter protocol tests; native behavior is a separate probe."""
import importlib.util
import io
from hashlib import sha256
import json
from pathlib import Path
import queue
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(backend):
    name = "_test_fallback_" + backend
    spec = importlib.util.spec_from_file_location(name, ROOT / "harness" / backend / "adapter.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def bound_context(module, context):
    paths = [Path(module.__file__).resolve(), ROOT / "harness/codex/adapter.py", ROOT / "src/contracts.py",
             Path(context["executable"]).resolve()]
    if "declaration_path" in context:
        paths.append(Path(context["declaration_path"]).resolve())
    media = {".json": "application/json", ".py": "text/x-python", ".ts": "text/typescript", ".md": "text/markdown"}
    return {**context, "source_refs": [{"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
                                       "media_type": media.get(path.suffix, "application/octet-stream")}
                                      for path in dict.fromkeys(paths)]}

CLAUDE_TASK_OPTIONS = ("--safe-mode", "--restricted", "-p", "--session-id", "--output-format", "--json-schema",
                       "--permission-mode", "--permission-prompts", "--tools", "--strict-mcp-config", "--mcp-config",
                       "--allowedTools", "--disallowedTools", "--add-dir", "--settings", "--model")


def claude_task(module, root, *, writable=False, route=False):
    candidate, inputs, private = (root / name for name in ("candidate", "inputs", "private"))
    for directory in (candidate, inputs, private):
        directory.mkdir()
    context = {"schema_version": 1, "backend": "claude", "executable": sys.executable,
               "cwd": str(candidate), "evidence_dir": str(root), "caller_host": "omp", "caller_family": "gpt",
               "private_state_roots": [str(private)]}
    if route:
        context.update(proxy_url="http://127.0.0.1:8181", model="declared-task-model")
    adapter = module.Adapter(bound_context(module, context))
    adapter.correlate({"request_id": "prepare-task", "run_id": "run", "attempt_id": "attempt",
                       "owner_epoch": 1, "operation": "prepare", "payload": {}})
    wire = {"schema_version": 1, "run_id": "run", "step_id": "verify", "item_id": "one", "attempt_id": "attempt",
            "duty": "verify", "instructions": "Return only the assigned verdict.", "inputs": [], "resources": [],
            "candidate_dir": str(candidate), "outputs": [], "allowed_actions": ["read"], "read_roots": [str(inputs)],
            "write_roots": [str(candidate)] if writable else [], "result_schema": adapter.api.task_result_schema(),
            "artifact_id": "one", "review_contract": None, "artifact_tier": "B", "semantic_sha256": ""}
    wire["semantic_sha256"] = adapter.api.semantic_hash(wire)
    return adapter, {"task": wire}


class ProtocolTests(unittest.TestCase):
    def test_cli_source_refs_refuse_before_contract_loading(self):
        import tempfile
        for backend in ("claude", "codex"):
            module = load(backend)
            transport = module._transport if backend == "claude" else module
            with tempfile.TemporaryDirectory() as tmp:
                context = {"schema_version": 1, "backend": backend, "executable": sys.executable, "cwd": tmp,
                           "evidence_dir": tmp, "caller_host": "codex", "caller_family": "gpt", "private_state_roots": []}
                bound = bound_context(module, context)
                malformed = [None, [], {}, [bound["source_refs"][0], bound["source_refs"][0]],
                             [{**bound["source_refs"][0], "extra": True}],
                             [{**bound["source_refs"][0], "sha256": "0" * 64}],
                             [{**bound["source_refs"][0], "media_type": ""}],
                             [{**bound["source_refs"][0], "path": "relative.py"}],
                             [ref for ref in bound["source_refs"] if ref["path"] != str(Path(sys.executable).resolve())]]
                for refs in malformed:
                    with self.subTest(backend=backend, refs=refs), patch.object(transport, "contracts", side_effect=AssertionError(
                            "No contract load before source binding")), self.assertRaises((module.ProtocolError, ValueError)):
                        module.Adapter({**context, "source_refs": refs})

    def test_cli_declaration_binds_executable_and_model_including_absence(self):
        import tempfile
        for backend in ("claude", "codex"):
            module = load(backend)
            for declared, selected, matches in ((None, None, True), ("exact", "exact", True),
                                                 (None, "exact", False), ("exact", None, False),
                                                 ("exact", "different", False)):
                with self.subTest(backend=backend, declared=declared, selected=selected), tempfile.TemporaryDirectory() as tmp:
                    declaration = Path(tmp).resolve() / "runtime.json"
                    route = {"executable": sys.executable}
                    if declared is not None:
                        route["model"] = declared
                    declaration.write_text(json.dumps({"workflow_native": {backend: route}}), encoding="utf-8")
                    context = {"schema_version": 1, "backend": backend, "executable": sys.executable, "cwd": tmp,
                               "evidence_dir": tmp, "caller_host": "codex", "caller_family": "gpt", "private_state_roots": [],
                               "declaration_path": str(declaration)}
                    if selected is not None:
                        context["model"] = selected
                    context = bound_context(module, context)
                    if matches:
                        adapter = module.Adapter(context)
                        adapter.context["model"] = "tampered-after-load"
                        with self.assertRaises(module.ProtocolError):
                            adapter.check_sources()
                        if selected is None:
                            context.pop("model")
                        else:
                            context["model"] = selected
                    else:
                        with self.assertRaises(module.ProtocolError):
                            module.Adapter(context)
                    route["executable"] = str(Path(tmp).resolve() / "other-native.exe")
                    declaration.write_text(json.dumps({"workflow_native": {backend: route}}), encoding="utf-8")
                    with self.assertRaises(module.ProtocolError):
                        module.Adapter(bound_context(module, context))
                    for field in ("capability_receipt_path", "qualification_receipt_path"):
                        with self.subTest(field=field), self.assertRaises(module.ProtocolError):
                            module.Adapter({**context, field: str(Path(tmp) / "absent.json")})

    def test_claude_source_drift_refuses_prepare_reservation_and_submission(self):
        import tempfile
        module = load("claude")
        for phase in ("prepare", "reserve", "launch"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                adapter, payload = claude_task(module, root)
                source = root / "runtime.json"
                source.write_text("{}", encoding="utf-8")
                adapter.source_refs += ({"path": str(source), "sha256": sha256(source.read_bytes()).hexdigest(),
                                         "media_type": "application/json"},)
                source.write_text('{"changed":true}', encoding="utf-8")
                if phase != "prepare":
                    adapter.task = adapter.api.task_from_dict(payload["task"])
                    adapter.state = "prepared" if phase == "reserve" else "reserved"
                    if phase == "launch":
                        adapter.binding = {"session_id": "bound", "attempt_id": "attempt"}
                with patch.object(module._transport.subprocess, "run", side_effect=AssertionError("No native identity/parser call")), \
                        patch.object(adapter, "spawn", side_effect=AssertionError("No native submission")), \
                        self.assertRaises(module.ProtocolError):
                    adapter.prepare(payload) if phase == "prepare" else getattr(adapter, phase)()
                self.assertFalse(adapter.submitted)
                self.assertIsNone(adapter.process)

    def test_caller_review_source_drift_refuses_launch_and_collect(self):
        import tempfile
        from types import SimpleNamespace
        module = load("codex")
        for phase in ("launch", "collect"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                declaration = root / "runtime.json"
                declaration.write_text(json.dumps({"workflow_native": {"codex": {"executable": sys.executable}}}), encoding="utf-8")
                request = root / "request.py"
                request.write_text("raise AssertionError('No reviewer dispatch')", encoding="utf-8")
                context = {"schema_version": 1, "backend": "codex", "executable": sys.executable, "cwd": tmp,
                           "evidence_dir": tmp, "caller_host": "codex", "caller_family": "gpt", "private_state_roots": [],
                           "declaration_path": str(declaration), "discriminator": {"python": sys.executable, "request": str(request)}}
                context = bound_context(module, context)
                context["source_refs"].append({"path": str(request), "sha256": sha256(request.read_bytes()).hexdigest(),
                                               "media_type": "text/x-python"})
                adapter = module.Adapter(context)
                adapter.review = {"caller_family": "gpt", "contract": "code-fleet", "source": str(root / "source"), "focus": None}
                declaration.write_text('{"changed":true}', encoding="utf-8")
                if phase == "collect":
                    adapter.process = SimpleNamespace(poll=lambda: 0, returncode=0)
                    adapter.inbox.put("disc-synthetic-handle\n")
                    adapter.inbox.put(None)
                with patch.object(module.subprocess, "run", side_effect=AssertionError("No changed-code collection")), \
                        patch.object(adapter, "spawn", side_effect=AssertionError("No changed-code review")), \
                        self.assertRaises(module.ProtocolError):
                    adapter.launch_review() if phase == "launch" else adapter.observe_review()
                self.assertFalse(adapter.submitted)


    def test_claude_task_prepares_only_after_complete_no_inference_parser_check(self):
        import tempfile
        from types import SimpleNamespace
        module = load("claude")
        help_text = "Options:\n" + "".join("  " + flag + "  Synthetic option.\n" for flag in CLAUDE_TASK_OPTIONS)
        for writable in (False, True):
            with self.subTest(writable=writable), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                adapter, payload = claude_task(module, root, writable=writable, route=True)
                with patch.object(module._transport.subprocess, "run", side_effect=[
                        SimpleNamespace(returncode=0, stdout=help_text),
                        SimpleNamespace(returncode=0, stdout="synthetic-version")]) as run:
                    result = adapter.prepare(payload)
                self.assertEqual(adapter.state, "prepared")
                self.assertEqual(result["task_sha256"], payload["task"]["semantic_sha256"])
                self.assertFalse(adapter.submitted)
                self.assertIsNone(adapter.binding)
                self.assertIsNone(adapter.process)
                probe = run.call_args_list[0]
                argv = probe.args[0]
                self.assertEqual(argv[0], str(Path(sys.executable).resolve()))
                self.assertEqual(argv[-1], "--help")
                self.assertTrue(set(CLAUDE_TASK_OPTIONS) <= set(argv))
                self.assertEqual(probe.kwargs["stdin"], module._transport.subprocess.DEVNULL)
                self.assertNotIn("input", probe.kwargs)
                self.assertEqual(probe.kwargs["cwd"], payload["task"]["candidate_dir"])
                self.assertIn("Read,Write,Edit" if writable else "Read", argv)
                self.assertEqual("Edit(./**)" in argv, writable)
                self.assertTrue(any(value.startswith("Read(//") for value in argv))
                self.assertTrue(any(value.startswith("Edit(//") for value in argv))
                self.assertEqual(run.call_args_list[1].args[0], [argv[0], "--version"])
                records = [json.loads(line) for line in (root / "attempt.adapter.jsonl").read_text(encoding="utf-8").splitlines()]
                capability, = [row["value"] for row in records if row["kind"] == "native_task_cli_capability"]
                self.assertEqual(capability["flags"], sorted(CLAUDE_TASK_OPTIONS))
                self.assertIs(capability["filesystem_enforcement_observed"], False)

    def test_claude_task_refuses_any_unadvertised_flag_even_when_help_succeeds(self):
        import tempfile
        from types import SimpleNamespace
        module = load("claude")
        for missing in CLAUDE_TASK_OPTIONS:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                adapter, payload = claude_task(module, Path(tmp).resolve(), route=True)
                help_text = "Options:\n" + "".join("  " + flag + "  Synthetic option.\n"
                                                  for flag in CLAUDE_TASK_OPTIONS if flag != missing)
                help_text += "\nProse mentioning " + missing + " is not an option declaration.\n"
                with patch.object(module._transport.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0, stdout=help_text)) as run, self.assertRaises(module.ProtocolError) as error:
                    adapter.prepare(payload)
                self.assertIn(missing, str(error.exception))
                self.assertEqual(run.call_count, 1)
                self.assertEqual(adapter.state, "new")
                self.assertFalse(adapter.submitted)
                self.assertIsNone(adapter.process)
                self.assertIsNone(adapter.binding)

    def test_claude_task_rejected_or_unavailable_parser_never_prepares(self):
        import tempfile
        from types import SimpleNamespace
        module = load("claude")
        help_text = "Options:\n" + "".join("  " + flag + "  Synthetic option.\n" for flag in CLAUDE_TASK_OPTIONS)
        for failure in (SimpleNamespace(returncode=2, stdout=help_text), OSError("Synthetic missing executable"),
                        module._transport.subprocess.TimeoutExpired("synthetic-help", 20)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                adapter, payload = claude_task(module, Path(tmp).resolve(), route=True)
                options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(module._transport.subprocess, "run", **options), self.assertRaises(module.ProtocolError):
                    adapter.prepare(payload)
                self.assertEqual(adapter.state, "new")
                self.assertFalse(adapter.submitted)
                self.assertIsNone(adapter.process)

    def test_claude_caller_review_does_not_require_task_cli_flags(self):
        import tempfile
        from types import SimpleNamespace
        module = load("claude")
        with tempfile.TemporaryDirectory() as tmp:
            context = {"schema_version": 1, "backend": "claude", "executable": sys.executable, "cwd": tmp,
                       "evidence_dir": tmp, "caller_host": "claude", "caller_family": "claude", "private_state_roots": []}
            adapter = module.Adapter(bound_context(module, context))
            adapter.correlate({"request_id": "prepare-review", "run_id": "run", "attempt_id": "review",
                               "owner_epoch": 1, "operation": "prepare", "payload": {}})
            with patch.object(module._transport.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout="synthetic-version")) as run:
                adapter.prepare({"review": {"caller_family": "claude", "contract": "vault-wiki",
                                             "source": str(Path(tmp) / "note.md"), "focus": None}})
            self.assertEqual(adapter.state, "prepared")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], [str(Path(sys.executable).resolve()), "--version"])
            self.assertIsNone(adapter.task)
            self.assertIsNone(adapter.process)

    def test_disabled_codex_task_refuses_before_version_or_submission_but_review_prepares(self):
        import tempfile
        from types import SimpleNamespace
        module = load("codex")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            base = {"schema_version": 1, "backend": "codex", "executable": sys.executable, "cwd": str(root),
                    "evidence_dir": str(root), "caller_host": "codex", "caller_family": "gpt", "private_state_roots": []}
            for enabled in (None, False, True):
                context = dict(base)
                if enabled is not None:
                    context["task_enabled"] = enabled
                adapter = module.Adapter(bound_context(module, context))
                with patch.object(module.subprocess, "run", side_effect=AssertionError("No native identity call for disabled task")), \
                        self.assertRaises(module.NativeBackendUnavailable) as error:
                    adapter.prepare({"task": {}})
                self.assertEqual(error.exception.code, "NATIVE_BACKEND_UNAVAILABLE")
                self.assertIsNone(adapter.task)
                self.assertIsNone(adapter.process)
                self.assertFalse(adapter.submitted)
            for enabled in (False, True):
                with self.subTest(review_task_enabled=enabled):
                    adapter = module.Adapter(bound_context(module, {**base, "task_enabled": enabled}))
                    adapter.correlate({"request_id": "prepare-review", "run_id": "run", "attempt_id": "review",
                                       "owner_epoch": 1, "operation": "prepare", "payload": {}})
                    with patch.object(module.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="synthetic-version")):
                        adapter.prepare({"review": {"caller_family": "gpt", "contract": "vault-wiki",
                                                   "source": str(root / "note.md"), "focus": str(root / "focus.md")}})
                    self.assertEqual(adapter.state, "prepared")
                    self.assertIsNone(adapter.task)
                    self.assertIsNone(adapter.process)
            for enabled in (0, 1, "false", None):
                with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                    module.Adapter(bound_context(module, {**base, "task_enabled": enabled}))

    def test_cancelling_caller_review_keeps_opaque_result_and_unknown_run_unsettled(self):
        import tempfile
        from types import SimpleNamespace
        module = load("codex")
        with tempfile.TemporaryDirectory() as tmp:
            context = {"schema_version": 1, "backend": "codex", "executable": sys.executable, "cwd": tmp,
                       "evidence_dir": tmp, "caller_host": "codex", "caller_family": "gpt", "private_state_roots": [], "task_enabled": False}
            for value, expected in (({"status": "reviewed", "verdict": "fail", "review_conforming": True}, "completed"),
                                    ({"status": "tooling-failure", "run_state": "in-flight"}, "unknown")):
                adapter = module.Adapter(bound_context(module, context))
                adapter.review = {"caller_family": "gpt"}
                adapter.state = "cancelling"
                adapter.process = SimpleNamespace(poll=lambda: 4, returncode=4)
                adapter.inbox.put(json.dumps(value) + "\n")
                adapter.inbox.put(None)
                result = adapter.observe()
                self.assertEqual(result["result"], value)
                self.assertEqual(adapter.state, expected)
                self.assertEqual(result["settled"], expected == "completed")

    def test_project_candidates_use_backend_specific_execution_cwd(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            candidate = project / "docs" / "tmp" / "pipeline-runs" / "run" / "attempts" / "attempt"
            candidate.mkdir(parents=True)
            (project / ".git").mkdir()
            (project / "AGENTS.md").write_text("Synthetic project instructions", encoding="utf-8")
            claude = load("claude")
            adapter = object.__new__(claude.Adapter)
            adapter.cwd = candidate.resolve()
            adapter.private = []
            adapter.check_task_cli = lambda: None
            adapter.validate_workspace(candidate.resolve(), [candidate.resolve()])
            codex = load("codex")
            adapter = object.__new__(codex.Adapter)
            adapter.private = []
            adapter.cwd = candidate.resolve()
            with self.assertRaises(codex.ProtocolError):
                adapter.validate_workspace(candidate.resolve(), [candidate.resolve()])
            scratch = root / "scratch"
            scratch.mkdir()
            adapter.cwd = scratch.resolve()
            adapter.validate_workspace(candidate.resolve(), [candidate.resolve()])
            (scratch / "AGENTS.md").write_text("unexpected instructions", encoding="utf-8")
            with self.assertRaises(codex.ProtocolError):
                adapter.validate_workspace(candidate.resolve(), [candidate.resolve()])

    def test_readonly_candidate_cannot_grant_implicit_private_directory_access(self):
        import tempfile
        module = load("claude")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            nested = private / "candidate"
            nested.mkdir(parents=True)
            for candidate in (root, nested):
                context = {"schema_version": 1, "backend": "claude", "executable": sys.executable,
                           "cwd": str(candidate), "evidence_dir": str(root), "caller_host": "omp",
                           "caller_family": "gpt", "private_state_roots": [str(private)]}
                adapter = module.Adapter(bound_context(module, context))
                with self.subTest(candidate=candidate), self.assertRaises(module.ProtocolError):
                    adapter.validate_workspace(candidate.resolve(), [])
                self.assertIsNone(adapter.process)

    def test_invalid_proxy_route_cannot_become_unknown_native_submission(self):
        import tempfile
        from types import SimpleNamespace
        module = load("claude")
        with tempfile.TemporaryDirectory() as tmp:
            context = {"schema_version": 1, "backend": "claude", "executable": sys.executable,
                       "cwd": tmp, "evidence_dir": tmp, "caller_host": "omp", "caller_family": "gpt",
                       "private_state_roots": [], "proxy_url": "https://unrelated.invalid"}
            adapter = module.Adapter(bound_context(module, context))
            adapter.task = SimpleNamespace(write_roots=[], read_roots=[])
            adapter.state = "prepared"
            adapter.correlate({"request_id": "route-check", "run_id": "run", "attempt_id": "attempt",
                               "owner_epoch": 1, "operation": "launch_with_binding_barrier", "payload": {}})
            adapter.reserve()
            with self.assertRaises(module.ProtocolError):
                adapter.launch()
            self.assertFalse(adapter.submitted)
            self.assertEqual(adapter.state, "reserved")
            self.assertIsNone(adapter.process)


    def test_legacy_sandbox_echo_cannot_prove_named_permissions(self):
        module = load("codex")
        adapter = object.__new__(module.Adapter)
        adapter.settings = lambda: {"cwd": "scratch"}
        adapter.native_policy = {}
        response = {"cwd": "scratch", "approvalPolicy": "never", "sandbox": {"type": "readOnly"},
                    "activePermissionProfile": {"id": "workflow-attempt"}, "instructionSources": []}
        adapter.check_echo(response)
        for altered in ({"activePermissionProfile": None},
                        {"activePermissionProfile": {"id": ":workspace"}},
                        {"instructionSources": ["project/AGENTS.md"]}, {"instructionSources": None}):
            with self.assertRaises(module.ProtocolError):
                adapter.check_echo(dict(response, **altered))

    def test_global_native_policy_is_bound_without_accepting_project_overlays(self):
        import tempfile
        module = load("codex")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            policy = root / "home" / ".codex" / "AGENTS.md"
            policy.parent.mkdir(parents=True)
            policy.write_text("Preserve explicit task scope.\n", encoding="utf-8")
            declaration = root / "machine.json"
            declaration.write_text(json.dumps({"tokens": {"__USERPROFILE__": str(root / "home")}}), encoding="utf-8")
            adapter = object.__new__(module.Adapter)
            adapter.context = {"declaration_path": str(declaration)}
            adapter.record = lambda *args: None
            adapter.settings = lambda: {"cwd": "scratch"}
            adapter.snapshot_native_policy()
            response = {"cwd": "scratch", "approvalPolicy": "never",
                        "activePermissionProfile": {"id": "workflow-attempt"}, "instructionSources": [str(policy)]}
            adapter.check_echo(response)
            for sources in ([], [str(policy), str(policy)], [str(policy), str(root / "project" / "AGENTS.md")]):
                with self.assertRaises(module.ProtocolError):
                    adapter.check_echo(dict(response, instructionSources=sources))
            policy.write_text("Changed after preparation.\n", encoding="utf-8")
            with self.assertRaises(module.ProtocolError):
                adapter.check_echo(response)

    def test_closed_envelope_and_epoch_fencing(self):
        module = load("codex")
        adapter = object.__new__(module.Adapter)
        adapter.key = None
        request = {"request_id": "q", "run_id": "r", "attempt_id": "a", "owner_epoch": 1,
                   "operation": "observe", "payload": {}}
        adapter.correlate(request)
        with self.assertRaises(module.ProtocolError):
            adapter.correlate(dict(request, owner_epoch=2))
        with self.assertRaises(module.ProtocolError):
            adapter.correlate(dict(request, extra=True))
        with self.assertRaises(module.ProtocolError):
            adapter.correlate(dict(request, owner_epoch=True))

    def test_binding_ack_faults_never_launch(self):
        import tempfile
        module = load("codex")
        for fault in ("eof", "wrong_binding", "wrong_epoch"):
            launched = []
            class Fake:
                def __init__(self, context):
                    self.state, self.submitted = "prepared", False
                def correlate(self, request):
                    if request["owner_epoch"] != 1:
                        raise module.ProtocolError("stale epoch")
                def reserve(self):
                    return {"thread_id": "actual"}
                def record(self, *args):
                    pass
                def launch(self):
                    launched.append(True)
                def snapshot(self):
                    return {}
                def close(self):
                    pass
            request = {"request_id": "q", "run_id": "r", "attempt_id": "a", "owner_epoch": 1,
                       "operation": "launch_with_binding_barrier", "payload": {}}
            ack = dict(request, operation="binding_ack", payload={"committed": True, "binding": {"thread_id": "actual"}})
            if fault == "wrong_binding":
                ack["payload"]["binding"] = {"thread_id": "foreign"}
            if fault == "wrong_epoch":
                ack["owner_epoch"] = 2
            lines = [request] + ([] if fault == "eof" else [ack])
            with tempfile.TemporaryDirectory() as tmp:
                context = Path(tmp) / "context.json"
                context.write_text("{}", encoding="utf-8")
                stdin = io.StringIO("".join(json.dumps(line) + "\n" for line in lines))
                with patch.object(sys, "argv", ["adapter", "--context", str(context)]), patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", io.StringIO()):
                    module.main(Fake)
            self.assertEqual(launched, [], fault)


    def test_claude_wrong_session_and_missing_strict_output_refuse(self):
        module = load("claude")
        for native in ({"session_id": "foreign", "subtype": "success", "is_error": False},
                       {"session_id": "known", "subtype": "success", "is_error": False}):
            adapter = object.__new__(module.Adapter)
            adapter.review, adapter.state = None, "running"
            adapter.output, adapter.inbox = [], queue.Queue()
            adapter.binding = {"session_id": "known"}
            adapter.process = type("Process", (), {"returncode": 0, "poll": lambda self: 0})()
            adapter.record = lambda *args: None
            adapter.cancel_requested = False
            adapter.inbox.put(json.dumps(native))
            adapter.inbox.put(None)
            with self.assertRaises((module.ProtocolError, KeyError)):
                adapter.observe()

    def test_claude_interrupt_requires_observed_settlement(self):
        module = load("claude")
        adapter = object.__new__(module.Adapter)
        adapter.review, adapter.state = None, "cancelling"
        adapter.output, adapter.inbox = [], queue.Queue()
        adapter.binding, adapter.identity, adapter.result = {"session_id": "known"}, None, None
        adapter.cancel_requested, adapter.denied = True, False
        adapter.process = type("Process", (), {"returncode": -2, "poll": lambda self: -2})()
        adapter.record = lambda *args: None
        adapter.inbox.put(None)
        result = adapter.observe()
        self.assertTrue(result["settled"])
        self.assertEqual(adapter.state, "cancelled")

    def test_codex_reservation_contains_no_task_input(self):
        module = load("codex")
        adapter = object.__new__(module.Adapter)
        adapter.state, adapter.review = "prepared", None
        adapter.executable, adapter.key = Path("native"), ("r", "a", 1)
        adapter.context = {}
        calls = []
        adapter.spawn = lambda argv: calls.append(("spawn", argv))
        adapter.send = lambda frame: calls.append(("send", frame))
        adapter.settings = lambda: {"cwd": "candidate"}
        adapter.check_echo = lambda response: None
        adapter.record = lambda *args: None
        def call(method, params):
            calls.append((method, params))
            return {"thread": {"id": "actual-thread"}, "model": "actual-model"} if method == "thread/start" else {"userAgent": "native"}
        adapter.call = call
        adapter.check_sources = lambda: None
        binding = adapter.reserve()
        self.assertEqual(binding["thread_id"], "actual-thread")
        self.assertFalse(any(name == "turn/start" for name, payload in calls))
        self.assertNotIn("input", next(payload for name, payload in calls if name == "thread/start"))

    def test_claude_reservation_launches_no_process(self):
        module = load("claude")
        adapter = object.__new__(module.Adapter)
        adapter.state, adapter.review, adapter.key = "prepared", None, ("r", "a", 1)
        adapter.record = lambda *args: None
        adapter.spawn = lambda argv: self.fail("process before ACK")
        adapter.check_sources = lambda: None
        binding = adapter.reserve()
        self.assertEqual(binding["attempt_id"], "a")
        self.assertEqual(adapter.state, "reserved")
        import uuid
        uuid.UUID(binding["session_id"])

    def test_server_approval_is_denied_not_granted(self):
        module = load("codex")
        adapter = object.__new__(module.Adapter)
        adapter.inbox, adapter.native, adapter.notifications = queue.Queue(), [], []
        adapter.pending, adapter.denied = {}, False
        adapter.record = lambda *args: None
        sent = []
        adapter.send = sent.append
        adapter.inbox.put(json.dumps({"id": 7, "method": "item/commandExecution/requestApproval", "params": {}}))
        adapter.frame()
        self.assertTrue(adapter.denied)
        self.assertIn("error", sent[0])
        self.assertNotIn("result", sent[0])

    def test_unknown_binding_does_not_relaunch_claude(self):
        module = load("claude")
        adapter = object.__new__(module.Adapter)
        adapter.binding, adapter.task, adapter.review = None, None, None
        adapter.process, adapter.identity, adapter.result = None, None, None
        adapter.cancel_requested, adapter.denied = False, False
        adapter.spawn = lambda argv: self.fail("replay forbidden")
        result = adapter.reconcile({"binding": {"backend": "claude", "session_id": "known"}})
        self.assertEqual(adapter.state, "unknown")
        self.assertFalse(result["settled"])

    def test_commentary_cannot_replace_or_duplicate_the_strict_final_result(self):
        import tempfile
        module = load("codex")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            context = {"schema_version": 1, "backend": "codex", "executable": sys.executable, "cwd": str(root),
                       "evidence_dir": str(root), "caller_host": "omp", "caller_family": "gpt", "private_state_roots": []}
            adapter = module.Adapter(bound_context(module, context))
            wire = {"schema_version": 1, "run_id": "run", "step_id": "verify", "item_id": "one", "attempt_id": "attempt",
                    "duty": "verify", "instructions": "Return only the assigned verdict.", "inputs": [], "resources": [],
                    "candidate_dir": str(root), "outputs": [], "allowed_actions": ["read"], "read_roots": [], "write_roots": [],
                    "result_schema": adapter.api.task_result_schema(), "artifact_id": "one", "review_contract": None,
                    "artifact_tier": "B", "semantic_sha256": ""}
            wire["semantic_sha256"] = adapter.api.semantic_hash(wire)
            adapter.task = adapter.api.task_from_dict(wire)
            adapter.state, adapter.turn, adapter.binding = "running", "turn", {"thread_id": "thread"}
            result = {"schema_version": 1, "run_id": "run", "step_id": "verify", "item_id": "one", "attempt_id": "attempt",
                      "status": "completed", "verification": "PASS", "artifacts": [], "evidence": [],
                      "task_sha256": wire["semantic_sha256"], "error": None, "decision": None}
            def item(phase, identity, text):
                return {"method": "item/completed", "params": {"threadId": "thread", "turnId": "turn",
                        "item": {"type": "agentMessage", "id": identity, "phase": phase, "text": text}}}
            terminal = {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}}}
            adapter.notifications = [item("commentary", "progress", "Reading the assigned input."),
                                     item("final_answer", "final", json.dumps(result)), terminal]
            self.assertEqual(adapter.observe()["result"], result)
            adapter.state, adapter.agent_items = "running", {}
            adapter.notifications = [item("commentary", "progress", json.dumps(result)), terminal]
            with self.assertRaises(module.ProtocolError):
                adapter.observe()
            adapter.state, adapter.agent_items = "running", {}
            adapter.notifications = [item("final_answer", "first", json.dumps(result)), item("final_answer", "second", json.dumps(result)), terminal]
            with self.assertRaises(module.ProtocolError):
                adapter.observe()

    def test_refused_queued_turn_interrupt_settles_owned_process_before_cancellation(self):
        import os
        import subprocess
        import tempfile
        module = load("codex")
        with tempfile.TemporaryDirectory() as tmp:
            context = {"schema_version": 1, "backend": "codex", "executable": sys.executable, "cwd": tmp,
                       "evidence_dir": tmp, "caller_host": "omp", "caller_family": "gpt", "private_state_roots": []}
            adapter = module.Adapter(bound_context(module, context))
            adapter.state, adapter.turn, adapter.binding = "running", "turn", {"thread_id": "thread"}
            def refuse(method, params):
                raise module.ProtocolError("Synthetic queued-turn interrupt refusal")
            adapter.call = refuse
            adapter.process = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"],
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                                               start_new_session=os.name != "nt")
            try:
                observed = adapter.cancel()
                self.assertTrue(observed["native"]["cancel_requested"])
                self.assertTrue(observed["settled"])
                self.assertEqual(adapter.state, "cancelled")
                self.assertIsNotNone(adapter.process.poll())
            finally:
                adapter.close()


if __name__ == "__main__":
    unittest.main()
