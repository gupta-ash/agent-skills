"""Installed workflow CLI. Registry paths are relative to the installed skills root."""
from __future__ import annotations

# Direct execution bootstraps only this flattened _workflow package. It never
# discovers an authoring src/ tree, changes cwd, or searches a user's home.
if not __package__:
    import sys
    from pathlib import Path
    _directory = Path(__file__).resolve().parent
    if _directory.name != "_workflow" or not (_directory / "__init__.py").is_file():
        print('{"schema_version":1,"run_id":"","status":"failed","outputs":[],"decision":null,"error":{"code":"INVALID_RUNTIME_LAYOUT","message":"Run the flattened installed _workflow/workflow.py","details":{}}}')
        raise SystemExit(1)
    sys.path.insert(0, str(_directory.parent))
    __package__ = "_workflow"

import argparse
import os
import importlib.util
import json
from pathlib import Path
import platform
import re
import sys
from urllib.parse import urlsplit

from .controller import Controller, ControllerError, RunOutcome, RunRequest, _digest, _path, json_value
from .store import Store


ROUTES = frozenset(("wiki-ingest", "wiki-compile", "wiki-strategize", "wiki-reverify", "wiki-content-check"))


def _read_json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ControllerError("INVALID_JSON", "Duplicate JSON object key", {"key": key})
            value[key] = item
        return value
    return json.loads(_path(path, regular=True).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ControllerError("INVALID_JSON", "Nonfinite JSON number")))


def load_registry(skills_root):
    root = _path(skills_root)
    registry_path = root / "_workflow" / "registry.json"
    registry = _read_json(registry_path)
    if not isinstance(registry, dict) or set(registry) != {"schema_version", "workflows"} or type(registry["schema_version"]) is not int or registry["schema_version"] != 1:
        raise ControllerError("INVALID_REGISTRY", "Version-one closed workflow registry required")
    routes = registry["workflows"]
    if not isinstance(routes, dict) or set(routes) != ROUTES:
        raise ControllerError("INVALID_REGISTRY", "Registry must contain exactly the five accepted wiki verbs")
    expected = {"module": "wiki/runtime/workflows.py", "factory": "build_workflow", "artifact_validator": "validate_artifact"}
    if any(value != expected for value in routes.values()):
        raise ControllerError("INVALID_REGISTRY", "Registry contains an unaccepted domain import")
    return registry


def registered_factory(skills_root, workflow):
    registry = load_registry(skills_root)
    if workflow not in registry["workflows"]:
        raise ControllerError("UNKNOWN_WORKFLOW", "Unknown registered workflow", {"workflow": workflow})
    root = _path(skills_root)
    module_path = _path(root / registry["workflows"][workflow]["module"], root=root, regular=True)
    # Establish the real installed packages for ordinary relative imports. No
    # synthetic replacement package is manufactured for a missing initializer.
    for package in (root / "wiki", root / "wiki" / "runtime"):
        _path(package / "__init__.py", regular=True)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    name = "wiki.runtime.workflows"
    existing = sys.modules.get(name)
    if existing is not None and Path(existing.__file__).resolve() != module_path.resolve():
        raise ControllerError("MODULE_SHADOWED", "A different domain module is already loaded")
    if existing is None:
        specification = importlib.util.spec_from_file_location(name, module_path)
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        try:
            specification.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    else:
        module = existing
    factory, validator = getattr(module, "build_workflow", None), getattr(module, "validate_artifact", None)
    restorer = getattr(module, "restore_workflow", None)
    if not callable(factory) or not callable(validator) or not callable(restorer):
        raise ControllerError("INVALID_REGISTRY", "Registered domain lacks its required source interfaces")
    return factory, validator, restorer


def _user_profile():
    """Observe the invoking account, not HOME/USERPROFILE or another checkout."""
    if sys.platform != "win32":
        import pwd
        return _path(pwd.getpwuid(os.getuid()).pw_dir)
    import ctypes
    from ctypes import wintypes
    token = wintypes.HANDLE()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    userenv.GetUserProfileDirectoryW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ControllerError("MACHINE_IDENTITY_UNAVAILABLE", "Invoking account profile is unavailable")
    try:
        size = wintypes.DWORD(0)
        userenv.GetUserProfileDirectoryW(token, None, ctypes.byref(size))
        if not size.value:
            raise ControllerError("MACHINE_IDENTITY_UNAVAILABLE", "Invoking account profile is unavailable")
        buffer = ctypes.create_unicode_buffer(size.value)
        if not userenv.GetUserProfileDirectoryW(token, buffer, ctypes.byref(size)):
            raise ControllerError("MACHINE_IDENTITY_UNAVAILABLE", "Invoking account profile is unavailable")
        return _path(buffer.value)
    finally:
        kernel.CloseHandle(token)


def _configuration_root(skills_root=None):
    runtime = Path(__file__).resolve().parent
    root = _path(skills_root) if skills_root is not None else runtime.parent
    if (skills_root is None and runtime.name != "_workflow") or root.name != "skills" or root.parent.name != ".agents":
        raise ControllerError("INVALID_RUNTIME_LAYOUT", "Installed .agents/skills layout or explicit config_root required")
    if root.parent.parent != _user_profile():
        raise ControllerError("MACHINE_IDENTITY_MISMATCH", "Installed skills must belong to the invoking account profile")
    return root.parent / "agent-skills"


def _machine_identity(machine, hostname=None):
    name = platform.node() if hostname is None else hostname
    if type(name) is not str or not name or any(c in name for c in "/\\\0"):
        raise ControllerError("MACHINE_IDENTITY_UNAVAILABLE", "OS hostname is unavailable or unsafe")
    if not isinstance(machine, dict) or type(machine.get("machine")) is not str or machine["machine"].casefold() != name.casefold():
        raise ControllerError("MACHINE_IDENTITY_MISMATCH", "Runtime declaration does not match observed OS hostname")
    tokens = machine.get("tokens")
    if not isinstance(tokens, dict) or _path(tokens.get("__USERPROFILE__", "")) != _user_profile():
        raise ControllerError("MACHINE_IDENTITY_MISMATCH", "Runtime declaration does not match the invoking account profile")
    return machine["machine"]


def resolve_machine(*, config_root=None, hostname=None):
    root = _path(config_root) if config_root is not None else _configuration_root()
    path = _path(root / "runtime.json", regular=True)
    machine = _read_json(path)
    _machine_identity(machine, hostname)
    return path, machine


def _relative(root, value):
    if type(value) is not str or not value or "\\" in value or ":" in value or value.startswith("/") or any(p in ("", ".", "..") for p in value.split("/")):
        raise ControllerError("INVALID_NATIVE_MANIFEST", "Normalized contained manifest path required")
    return _path(Path(root) / value, root=Path(root), regular=True)


def _omp_runtime_files(root, manifest):
    """Observe the selected current runtime, never an approved-version inventory."""
    root = _path(root)
    tree = manifest.get("package_tree_relative")
    if type(tree) is not str or not tree or "\\" in tree or ":" in tree or any(p in ("", ".", "..") for p in tree.split("/")):
        raise ControllerError("INVALID_NATIVE_MANIFEST", "Normalized contained OMP package tree required")
    package = _relative(root, tree + "/package.json")
    identity = _read_json(package)
    if not isinstance(identity, dict) or identity.get("name") != "@oh-my-pi/pi-coding-agent":
        raise ControllerError("INVALID_NATIVE_MANIFEST", "Selected OMP package identity does not match the manifest")
    return (_relative(root, manifest["installer"]["runtime_relative"]),
            _relative(root, manifest["executable_relative"]), _relative(root, manifest["payload_relative"]), package)


# The fixed runtime projection from deploy/exports.json, not a census of the
# installed directory. Unknown user files and caches are never owned inputs.
_DISCRIMINATOR_RUNTIME_FILES = (
    "SKILL.md", "agents/discriminator.toml", "assets/preamble_envelope.txt", "assets/preamble_finding.txt",
    "assets/rubric.lock.json", "assets/rubric.txt", "claude_reviewer.py", "contracts/code-fleet.md",
    "contracts/code-service.md", "contracts/configuration.md", "contracts/model-instruction.md",
    "contracts/pipeline-template.md", "contracts/plan-design.md", "contracts/project-state.md",
    "contracts/research-report.md", "contracts/vault-raw.md", "contracts/vault-wiki.md", "envelope.py",
    "envelope_schema.py", "family_attestation.py", "native_review.py", "packet.py", "request.py",
    "result.py", "supervisor.py", "telemetry.py", "transport.py")


def _discriminator_files(root):
    root = _path(root)
    return [_path(root / name, root=root, regular=True) for name in _DISCRIMINATOR_RUNTIME_FILES]


def _omp_review_extension(reference):
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256", "media_type"}:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Closed source review-extension InputRef required")
    path = _path(reference.get("path", ""), regular=True)
    if reference != {"path": str(path), "sha256": _digest(path), "media_type": "text/typescript"}:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Source review-extension hash/media/path changed")
    if len(path.parents) < 5:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Generated source review-extension layout required")
    home = path.parents[4]
    if path != home / ".omp/shared/extensions/discriminator/index.ts":
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Only the generated shared discriminator export may be selected")
    root = home / ".agents/skills/discriminator"
    paths = _discriminator_files(root)
    return [{"path": str(member), "sha256": _digest(member)} for member in [path, *sorted(paths, key=str)]]


def _omp_default_review_extension(machine):
    home = _path(machine.get("tokens", {}).get("__USERPROFILE__", ""))
    reference = _input_ref(home / ".omp/shared/extensions/discriminator/index.ts")
    _omp_review_extension(reference)
    return reference


def _omp_settings(value):
    settings = {}
    if "agent_dir" in value:
        agent = value["agent_dir"]
        if type(agent) is not str or not Path(agent).is_absolute() or not Path(agent).is_dir():
            raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP agent_dir must name an existing absolute native directory")
        settings["agent_dir"] = str(_path(agent))
    if "config_files" in value:
        files = value["config_files"]
        if type(files) is not list or not files or any(type(path) is not str or not Path(path).is_absolute() for path in files):
            raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP config_files must be a nonempty absolute native file list")
        paths = [_path(path, regular=True) for path in files]
        if len(set(paths)) != len(paths):
            raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP config_files cannot contain duplicate native paths")
        settings["config_files"] = [str(path) for path in paths]
    if "review_extension" in value:
        _omp_review_extension(value["review_extension"])
        settings["review_extension"] = dict(value["review_extension"])
    return settings


def resolve_native_configuration(request, skills_root, *, config_root=None, hostname=None):
    """Read target-local nonsecret execution declarations; never probe a runtime."""
    root = _path(config_root) if config_root is not None else _configuration_root(skills_root)
    declaration_path, machine = resolve_machine(config_root=root, hostname=hostname)
    extra = machine.get("workflow_native", {})
    if not isinstance(extra, dict) or set(extra) - {"omp", "codex", "claude"}:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Closed execution-only backend metadata required")
    result = {}
    managed = machine.get("omp_managed", {})
    if not isinstance(managed, dict) or type(managed.get("enabled", False)) is not bool:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP enabled must be boolean")
    if managed.get("enabled") is True:
        install_root = _path(managed.get("install_root", ""))
        manifest_path = _path(root / "omp-install.json", regular=True)
        manifest = _read_json(manifest_path)
        if type(manifest.get("schema")) is not int or manifest["schema"] != 1 or manifest.get("package") != "@oh-my-pi/pi-coding-agent":
            raise ControllerError("INVALID_NATIVE_MANIFEST", "Unaccepted OMP package manifest")
        runtime_files = _omp_runtime_files(install_root, manifest)
        bun = runtime_files[0]
        omp = extra.get("omp", {})
        if not isinstance(omp, dict) or set(omp) - {"model", "agent_dir", "config_files", "review_extension"}:
            raise ControllerError("INVALID_NATIVE_DECLARATION", "Closed OMP execution settings required; certification fields are retired")
        if "model" in omp and (type(omp["model"]) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._/-]*", omp["model"])):
            raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP model must be an exact provider/model selector")
        context = {"declaration_path": str(declaration_path), "install_manifest_path": str(manifest_path), **_omp_settings(omp)}
        if request is not None and request.caller_host == "omp" and "review_extension" not in context:
            context["review_extension"] = _omp_default_review_extension(machine)
        if "model" in omp:
            context["model"] = omp["model"]
        sources = [declaration_path, manifest_path, *runtime_files, *context.get("config_files", ())]
        if "review_extension" in context:
            sources.extend(item["path"] for item in _omp_review_extension(context["review_extension"]))
            sources.append(_path(machine["tokens"].get("__OMP_PYTHON__", ""), regular=True))
        result["omp"] = {"bun": str(bun), "context": context, "source_refs": [_input_ref(path) for path in dict.fromkeys(map(str, sources))]}
    elif "omp" in extra:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Declared OMP route requires an enabled installed runtime")
    for backend in ("codex", "claude"):
        if backend not in extra:
            continue
        try:
            value = extra[backend]
            allowed = {"executable", "private_state_roots", "windows_sandbox", "model", "task_enabled"} if backend == "codex" else {"executable", "private_state_roots", "proxy_url", "model"}
            if not isinstance(value, dict) or not {"executable", "private_state_roots"} <= set(value) or set(value) - allowed:
                raise ControllerError("INVALID_NATIVE_DECLARATION", "Closed native executable/private-state metadata required; certification fields are retired")
            python = _path(machine["tokens"].get("__OMP_PYTHON__", ""), regular=True)
            executable = _path(value["executable"], regular=True)
            if executable.suffix.lower() in (".cmd", ".bat", ".js"):
                raise ControllerError("INVALID_NATIVE_DECLARATION", "Native executable required, not shell shim")
            if not isinstance(value["private_state_roots"], list) or not value["private_state_roots"]:
                raise ControllerError("INVALID_NATIVE_DECLARATION", "Explicit nonempty private-state exclusions required")
            context = {"executable": str(executable), "private_state_roots": [str(_path(p)) for p in value["private_state_roots"]],
                       "declaration_path": str(declaration_path)}
            if backend == "codex":
                enabled = value.get("task_enabled", False)
                if type(enabled) is not bool:
                    raise ControllerError("INVALID_NATIVE_DECLARATION", "Codex task_enabled must be boolean")
                context["task_enabled"] = enabled
            if "windows_sandbox" in value:
                if value["windows_sandbox"] not in ("elevated", "unelevated", "mxc"):
                    raise ControllerError("INVALID_NATIVE_DECLARATION", "Unsupported Windows sandbox route")
                context["windows_sandbox"] = value["windows_sandbox"]
            if "proxy_url" in value:
                url = urlsplit(value["proxy_url"])
                if url.scheme not in ("http", "https") or url.hostname not in ("localhost", "127.0.0.1", "::1") or url.username or url.password or url.query or url.fragment:
                    raise ControllerError("INVALID_NATIVE_DECLARATION", "Only a credential-free loopback proxy is permitted")
                context["proxy_url"] = value["proxy_url"]
            if "model" in value:
                if type(value["model"]) is not str or not value["model"].strip() or value["model"] != value["model"].strip():
                    raise ControllerError("INVALID_NATIVE_DECLARATION", "Exact nonempty execution model required")
                context["model"] = value["model"]
            context["discriminator"] = {"python": str(python), "request": str(Path(skills_root) / "discriminator/request.py")}
            result[backend] = {"python": str(python), "context": context, "source_refs": [_input_ref(path) for path in (declaration_path, executable, python)]}
        except (ControllerError, KeyError, TypeError, ValueError) as exc:
            if request is None or request.backend not in ("auto", "omp") or request.caller_host == backend or backend != "claude":
                raise
            result[backend] = {"unavailable": getattr(exc, "code", type(exc).__name__)}
    return result


def _input_ref(path):
    path = _path(path, regular=True)
    media = {".json": "application/json", ".py": "text/x-python", ".ts": "text/typescript", ".md": "text/markdown"}
    return {"path": str(path), "sha256": _digest(path), "media_type": media.get(path.suffix, "application/octet-stream")}


def _checked_refs(values):
    """Check every admitted ref without normalizing or coalescing its identity.

    Resolved-path aliases are refused, not dropped. Distinct paths remain bound
    independently even when their bytes or physical hardlink identity coincide.
    """
    from .contracts import InputRef
    if not isinstance(values, list) or not values:
        raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Nonempty hash-bound native references required")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"path", "sha256", "media_type"}:
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Closed InputRef required")
        ref = InputRef(**value)
        path = _path(ref.path, regular=True)
        identity = str(path.resolve()).casefold() if sys.platform == "win32" else str(path.resolve())
        if identity in seen or _digest(path) != ref.sha256:
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Duplicate or stale native reference", {"path": str(path)})
        seen.add(identity)
        result.append(dict(value))
    return result


def _omp_selection(config, adapter):
    """Validate current source/selection policy; fingerprint is observation only."""
    context = config["context"]
    if set(context) - {"declaration_path", "install_manifest_path", "model", "agent_dir", "config_files", "review_extension"}:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Closed OMP context required; certification fields are retired")
    declaration_path = _path(context["declaration_path"], regular=True)
    if declaration_path.name != "runtime.json" or _path(context["install_manifest_path"], regular=True) != declaration_path.parent / "omp-install.json":
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Distribution-local runtime.json and omp-install.json required")
    declaration = _read_json(context["declaration_path"])
    declared = declaration.get("workflow_native", {}).get("omp", {})
    if not isinstance(declared, dict) or set(declared) - {"model", "agent_dir", "config_files", "review_extension"}:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Closed OMP declaration required")
    if ("model" in context) != ("model" in declared) or context.get("model") != declared.get("model"):
        raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP model including absence must match declaration")
    if "model" in declared and (type(declared["model"]) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._/-]*", declared["model"])):
        raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP model must be exact provider/model")
    settings = _omp_settings(context)
    declared_settings = _omp_settings(declared)
    if "review_extension" not in declared_settings and "review_extension" in settings:
        canonical = _path(declaration.get("tokens", {}).get("__USERPROFILE__", "")) / ".omp/shared/extensions/discriminator/index.ts"
        if Path(settings["review_extension"]["path"]) != canonical:
            raise ControllerError("INVALID_NATIVE_DECLARATION", "Undeclared review selection must be the canonical owned extension")
        declared_settings["review_extension"] = settings["review_extension"]
    if settings != declared_settings:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP settings route differs from declaration")
    manifest = _read_json(context["install_manifest_path"])
    if type(manifest.get("schema")) is not int or manifest["schema"] != 1 or manifest.get("package") != "@oh-my-pi/pi-coding-agent":
        raise ControllerError("INVALID_NATIVE_MANIFEST", "Unaccepted OMP package manifest")
    managed = declaration.get("omp_managed", {})
    if managed.get("enabled") is not True:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "Selected OMP runtime must be enabled")
    root = _path(managed.get("install_root", ""))
    runtime_files = _omp_runtime_files(root, manifest)
    if _path(config["bun"], regular=True) != runtime_files[0]:
        raise ControllerError("INVALID_NATIVE_DECLARATION", "OMP must use the declared Bun runtime path")
    settings_refs = [_input_ref(path) for path in settings.get("config_files", ())]
    review_sources = _omp_review_extension(settings["review_extension"]) if "review_extension" in settings else []
    fingerprint = {"adapter_sha256": _digest(adapter), "declaration_sha256": _digest(context["declaration_path"]),
                   "manifest_sha256": _digest(context["install_manifest_path"]), "model_hint": context.get("model"),
                   "agent_dir": settings.get("agent_dir"), "config_files": [{key: ref[key] for key in ("path", "sha256")} for ref in settings_refs],
                   "review_extension": settings.get("review_extension"), "review_extension_sources": review_sources}
    review_refs = [_input_ref(item["path"]) for item in review_sources]
    if review_sources:
        review_refs.append(_input_ref(declaration.get("tokens", {}).get("__OMP_PYTHON__", "")))
    return [*[_input_ref(path) for path in runtime_files], *settings_refs, *review_refs], fingerprint


def preflight_native(request: RunRequest, skills_root: Path, *, native_configuration=None) -> dict:
    """Static source/declaration admission. No SDK, model, network or subprocess."""
    try:
        root = _path(skills_root)
        configuration = json_value(native_configuration) if native_configuration is not None else resolve_native_configuration(request, root)
        if not isinstance(configuration, dict) or set(configuration) - {"omp", "codex", "claude"}:
            raise ControllerError("INVALID_NATIVE_DECLARATION", "Closed native backend configuration required")
        if request.caller_host not in ("omp", "codex", "claude") or request.caller_family not in ("gpt", "claude") or (request.caller_host in ("codex", "claude") and request.caller_family != {"codex": "gpt", "claude": "claude"}[request.caller_host]):
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Observed caller host and model family are incompatible")
        selected = "omp" if request.backend == "auto" else request.backend
        if selected not in ("omp", "codex", "claude"):
            raise ControllerError("INVALID_NATIVE_DECLARATION", "Unknown native backend")
        if selected == "codex":
            raise ControllerError("NATIVE_BACKEND_UNAVAILABLE", "Codex tasks are unavailable by approved scope; caller reviews remain supported")
        refs, admitted, task_backends = [], {}, set()
        machine = None

        def admit(host, *, task_required=False):
            nonlocal machine
            config = configuration.get(host)
            if not isinstance(config, dict) or set(config) != ({"bun", "context", "source_refs"} if host == "omp" else {"python", "context", "source_refs"}) or not isinstance(config.get("context"), dict):
                raise ControllerError("INVALID_NATIVE_DECLARATION", "Selected native route is unavailable or malformed", {"backend": host})
            context = config["context"]
            declaration_path = _path(context["declaration_path"], regular=True)
            declaration = _read_json(declaration_path)
            identity = _machine_identity(declaration)
            if machine is not None and machine.casefold() != identity.casefold():
                raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Native routes disagree about machine identity")
            runtime = root / "_workflow"
            paths = [runtime / name for name in ("controller.py", "workflow.py", "contracts.py", "store.py", "publication.py")]
            adapter = runtime / "harness" / host / ("adapter.ts" if host == "omp" else "adapter.py")
            paths.extend((adapter, declaration_path))
            declared_refs = _checked_refs(config["source_refs"])
            if host == "omp" and request.caller_host == "omp" and "review_extension" not in context:
                context["review_extension"] = _omp_default_review_extension(declaration)
            if host == "omp":
                paths.append(_path(context["install_manifest_path"], regular=True))
                selection_refs, _ = _omp_selection(config, adapter)
                paths.extend(ref["path"] for ref in selection_refs)
                if "review_extension" in context and Path(context["review_extension"]["path"]) != root.parent.parent / ".omp/shared/extensions/discriminator/index.ts":
                    raise ControllerError("INVALID_NATIVE_DECLARATION", "Only this account's private discriminator extension may be selected")
            else:
                resolved = resolve_native_configuration(request, root, config_root=declaration_path.parent).get(host)
                if not isinstance(resolved, dict) or context != resolved.get("context") or config["python"] != resolved.get("python"):
                    raise ControllerError("INVALID_NATIVE_DECLARATION", "Native executable/model/private-state route differs from declaration")
                paths.extend((config["python"], context["executable"]))
                if host == "claude":
                    paths.append(runtime / "harness/codex/adapter.py")
                if request.caller_host == host:
                    paths.extend(_discriminator_files(root / "discriminator"))
            actual = [_input_ref(path) for path in dict.fromkeys(map(str, paths))]
            unique = {ref["path"]: ref for ref in actual}
            for ref in declared_refs:
                if ref["path"] in unique and unique[ref["path"]] != ref:
                    raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Declared native source changed")
                unique[ref["path"]] = ref
            config["source_refs"] = list(unique.values())
            refs.extend(config["source_refs"])
            machine = identity
            admitted[host] = config
            if task_required:
                task_backends.add(host)

        for host in sorted({request.caller_host, selected}):
            admit(host, task_required=host == selected)
        unavailable = {"codex": {"status": "unavailable_by_approved_scope", "task_enabled": False}}
        if request.backend == "auto" and "claude" not in task_backends:
            original_refs, original_machine = len(refs), machine
            try:
                admit("claude", task_required=True)
            except Exception as exc:
                del refs[original_refs:]
                machine = original_machine
                unavailable["claude"] = {"status": "unavailable", "error": getattr(exc, "code", type(exc).__name__)}
        unique = {}
        for ref in refs:
            if ref["path"] in unique and unique[ref["path"]] != ref:
                raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Conflicting native source references")
            unique[ref["path"]] = ref
        return {"schema_version": 1, "machine": machine, "caller_host": request.caller_host, "caller_family": request.caller_family,
                "backend": request.backend, "configuration": admitted, "task_backends": sorted(task_backends),
                "input_refs": list(unique.values()), "unavailable": unavailable}
    except ControllerError as exc:
        if exc.code in ("NATIVE_CAPABILITY_UNAVAILABLE", "INVALID_NATIVE_DECLARATION", "NATIVE_BACKEND_UNAVAILABLE"):
            raise
        raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Native declaration/source admission refused", {"cause": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Native declaration or source is missing, malformed or stale", {"cause": str(exc)}) from exc


def _controller(skills_root, workflow):
    factory, validator, restorer = registered_factory(skills_root, workflow)
    return Controller(factory, artifact_validator=validator, skills_root=skills_root, workflow_restorer=restorer)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ControllerError("INVALID_REQUEST", message)


def main(argv=None, *, skills_root=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run = commands.add_parser("run", allow_abbrev=False)
    run.add_argument("--request", required=True)
    for name in ("status", "resume", "cancel"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--project", required=True)
        command.add_argument("--run", required=True)
        if name == "resume":
            command.add_argument("--decision")
    outcome = None
    try:
        args = parser.parse_args(argv)
        root = Path(skills_root) if skills_root is not None else Path(__file__).resolve().parent.parent
        if args.command == "run":
            request = RunRequest.from_dict(_read_json(_path(args.request, regular=True)))
            outcome = _controller(root, request.workflow).run(request)
        elif args.command == "status":
            # No registry import, machine read, schema bootstrap or result consume.
            outcome = Controller(lambda request: None, skills_root=root).status(_path(args.project), args.run)
        elif args.command == "cancel":
            outcome = Controller(lambda request: None, skills_root=root).cancel(_path(args.project), args.run)
        else:
            store = Store(_path(args.project), read_only=True)
            try:
                row = store.read_run(args.run)
                if row is None:
                    raise ControllerError("UNKNOWN_RUN", "No such run")
                workflow = row["workflow"]
            finally:
                store.close()
            decision = _read_json(_path(args.decision, regular=True)) if args.decision else None
            outcome = _controller(root, workflow).resume(_path(args.project), args.run, decision)
    except Exception as exc:
        outcome = RunOutcome(status="failed", error={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc), "details": json_value(getattr(exc, "details", {}))})
    print(json.dumps(outcome.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if outcome.status in ("done", "preview") else 2 if outcome.status == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
