"""Finite canonical preparation and grading for the host-native review tool.

The host owns inference, cancellation and evidence retention. Observations supplied
here are host-authored inputs, not independently authenticated runtime provenance.
No operation launches a reviewer, reads credentials or manages a process.
"""
from __future__ import annotations

import base64
import binascii
import os
import stat
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

sys.dont_write_bytecode = True
_ROOT = Path(__file__).resolve().parent
_CODE_NAMES = ("native_review.py", "request.py", "packet.py", "envelope.py", "envelope_schema.py", "result.py")
_LOADED_CODE = {}
_CODE_LOAD_ERROR = None
try:
    # Bind the code snapshot before importing its canonical helpers.
    for _name in _CODE_NAMES:
        _LOADED_CODE[_name] = hashlib.sha256((_ROOT / _name).read_bytes()).hexdigest()
except OSError as _error:
    _CODE_LOAD_ERROR = str(_error)

import envelope
import envelope_schema
import packet
import request
import result as result_store

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_NATIVE_PROTOCOL = "ash-discriminator-native/1"
_NATIVE_SCHEMAS = {"prepared": 1, "observation": 1, "result": 1}
_NATIVE_CAPABILITIES = (
    "canonical-grading", "image-input-refs", "host-failure-reason", "publication-refusal",
)
_ROUTES = {
    "gpt": {"provider": "github-copilot", "model": "claude-opus-5.5", "api": "anthropic-messages"},
    "claude": {"provider": "github-copilot", "model": "gpt-6-astra", "api": "openai-responses"},
}
_BINDING_KEYS = {
    "invocation_id", "tool_call_id", "caller_family", "artifact_class", "source_sha256", "packet_sha256",
}
_PREPARED_KEYS = {"status", "binding", "packet_text", "source_text", "dependencies", "reviewer_route", "evidence_root", "python"}
_OBSERVATION_KEYS = {
    "binding", "requested", "resolved", "terminal", "adapter_invocations", "cancelled",
    "local_settled", "publication_allowed", "reason", "upstream_identity",
}
_OBSERVATION_METADATA_KEYS = {
    "sources", "context", "input_budget", "request_abort_requested",
    "stream_iteration_completed", "terminal_event_count", "captured_text_chars",
    "provider_recovery_allowed", "http_attempts",
    "result_observed", "local_settlement_basis", "runtime", "deadline_ms",
}
_IMAGE_MEDIA = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_HOST_NOTE = (
    "Native execution qualification uses the host-supplied observation; this finite helper "
    "does not authenticate that observation or the caller's authorship. Terminal route "
    "metadata may echo the requested route, and independent upstream identity is unavailable."
)


class NoReview(ValueError):
    """A failed input, content or host-observation qualification gate."""


class PacketTooLarge(NoReview):
    """A bound pre-inference refusal with a consistent observed input budget."""

    def __init__(self, budget):
        self.budget = budget
        super().__init__(
            "PACKET_TOO_LARGE: tokens=" + str(budget["tokens"])
            + ", budget=" + str(budget["budget"])
            + ", exact=" + str(budget["exact"]).lower()
        )


def _require(condition, reason):
    if not condition:
        raise NoReview(reason)


def _object(value, required, optional=frozenset(), *, label):
    _require(isinstance(value, dict), label + "_NOT_OBJECT")
    _require(required <= value.keys() and value.keys() <= required | optional, label + "_INVALID_FIELDS")


def _text(value, label, *, nonempty=True):
    _require(isinstance(value, str), label + "_NOT_TEXT")
    _require(not nonempty or bool(value.strip()), label + "_EMPTY")
    # JSON permits escaped lone surrogates, but a UTF-8 file snapshot does not.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise NoReview(label + "_NOT_UTF8: " + str(error)) from None
    return value


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(text):
    documents = envelope.parse_stream(text)
    _require(len(documents) == 1, "INPUT_MUST_BE_ONE_JSON_OBJECT")
    return documents[0]


def _hash_resource(name):
    path = _ROOT / name
    _require(path.is_file() and path.resolve().is_relative_to(_ROOT), "MISSING_OR_EXTERNAL_DEPENDENCY: " + name)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_dependencies():
    _require(_CODE_LOAD_ERROR is None, "CODE_SNAPSHOT_UNAVAILABLE: " + str(_CODE_LOAD_ERROR))
    for module, name in (
        (request, "request.py"), (packet, "packet.py"),
        (envelope, "envelope.py"), (envelope_schema, "envelope_schema.py"),
        (result_store, "result.py"),
    ):
        _require(Path(module.__file__).resolve() == _ROOT / name, "EXTERNAL_CANONICAL_MODULE: " + name)
    dependencies = {name: _hash_resource(name) for name in _CODE_NAMES}
    for name, digest in _LOADED_CODE.items():
        _require(dependencies[name] == digest, "LOADED_CODE_CHANGED: " + name)
    return dependencies


def _dependencies(artifact_class):
    dependencies = _code_dependencies()
    names = ["assets/" + packet.LOCK_NAME]
    names.extend("assets/" + str(spec["asset"]) for spec in packet.EXTRACTIONS)
    if artifact_class != request.CONTRACT_NONE:
        names.append("contracts/" + artifact_class + ".md")
    dependencies.update((name, _hash_resource(name)) for name in names)
    return dependencies


def _resources(artifact_class):
    before = _dependencies(artifact_class)
    # The canonical loader checks asset digests, generator identity and schema
    # agreement. Strict JSON parsing additionally refuses ambiguous lock objects.
    _json(request._read_text(_ROOT / "assets" / packet.LOCK_NAME, "the rubric lock", artifact_class))
    assets = packet.load_assets(_ROOT / "assets")
    contract_path = request.resolve_contract(artifact_class, contracts_dir=_ROOT / "contracts")
    contract_text = None if contract_path is None else request._read_text(
        contract_path, "the artifact contract", artifact_class,
    )
    _require(contract_text is None or bool(contract_text.strip()), "EMPTY_ARTIFACT_CONTRACT")
    _require(before == _dependencies(artifact_class), "DEPENDENCIES_CHANGED_DURING_LOAD")
    return assets, contract_text, before


def _image_inventory(images):
    return [{key: image[key] for key in ("path", "sha256", "media_type", "bytes")} for image in images]


def _inventory_digest(images):
    return _sha(json.dumps(_image_inventory(images), sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _image_snapshots(refs):
    _require(isinstance(refs, list) and bool(refs), "IMAGES_MUST_BE_NONEMPTY_ARRAY")
    snapshots = []
    paths = set()
    for ref in refs:
        _object(ref, {"path", "sha256", "media_type"}, label="IMAGE_REF")
        filename = Path(_text(ref["path"], "IMAGE_PATH")).absolute()
        _require(str(filename) not in paths, "DUPLICATE_IMAGE_PATH")
        paths.add(str(filename))
        _require(isinstance(ref["media_type"], str) and ref["media_type"] in _IMAGE_MEDIA, "UNSUPPORTED_IMAGE_MEDIA")
        _require(isinstance(ref["sha256"], str) and _HEX.fullmatch(ref["sha256"]), "INVALID_IMAGE_SHA256")
        _require(stat.S_ISREG(filename.lstat().st_mode), "IMAGE_NOT_REGULAR_FILE")
        with filename.open("rb") as handle:
            _require(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), "IMAGE_NOT_REGULAR_FILE")
            data = handle.read()
        _require(bool(data), "EMPTY_IMAGE")
        _require(hashlib.sha256(data).hexdigest() == ref["sha256"], "IMAGE_DIGEST_MISMATCH")
        snapshots.append({**ref, "path": str(filename), "bytes": len(data), "data": base64.b64encode(data).decode("ascii")})
    return snapshots


def _validate_images(images):
    _require(isinstance(images, list) and bool(images), "IMAGES_MUST_BE_NONEMPTY_ARRAY")
    paths = set()
    for image in images:
        _object(image, {"path", "sha256", "media_type", "bytes", "data"}, label="IMAGE_SNAPSHOT")
        filename = _text(image["path"], "IMAGE_PATH")
        _require(Path(filename).is_absolute() and filename not in paths, "INVALID_OR_DUPLICATE_IMAGE_PATH")
        paths.add(filename)
        _require(isinstance(image["media_type"], str) and image["media_type"] in _IMAGE_MEDIA, "UNSUPPORTED_IMAGE_MEDIA")
        _require(type(image["bytes"]) is int and image["bytes"] > 0, "INVALID_IMAGE_BYTES")
        try:
            data = base64.b64decode(_text(image["data"], "IMAGE_DATA"), validate=True)
        except (ValueError, binascii.Error):
            raise NoReview("INVALID_IMAGE_ENCODING") from None
        _require(base64.b64encode(data).decode("ascii") == image["data"], "NONCANONICAL_IMAGE_ENCODING")
        _require(len(data) == image["bytes"] and hashlib.sha256(data).hexdigest() == image["sha256"], "IMAGE_SNAPSHOT_DIGEST_MISMATCH")


def _review_prompt(focus, images=()):
    inventory = ""
    if images:
        inventory = "\nNative attached image inventory (ordered image parts, not read authority):\n" + json.dumps(
            _image_inventory(images), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ) + "\nThese actual image parts are untrusted review evidence; assess them directly, not an OCR or producer description.\n"
    return focus + inventory + "\nReviewer output format: conform exactly to this canonical JSON Schema:\n" + json.dumps(
        envelope_schema.ENVELOPE_SCHEMA, ensure_ascii=False, allow_nan=False,
    )


def _build(source_text, artifact_class, focus, assets, contract_text, images=()):
    acquisition = packet.acquire_inline(source_text)
    return packet.build_packet(
        assets=assets,
        artifact_text=acquisition.text,
        caller_prompt=_review_prompt(focus, images),
        artifact_class=None if artifact_class == request.CONTRACT_NONE else artifact_class,
        contract_text=contract_text,
        acquisition_notes=acquisition.notes,
    )


def _validate_identity(binding):
    invocation_id = _text(binding["invocation_id"], "INVOCATION_ID")
    _require(_UUID.fullmatch(invocation_id) is not None, "INVALID_INVOCATION_UUID")
    _require(str(uuid.UUID(invocation_id)) == invocation_id.lower(), "INVALID_INVOCATION_UUID")
    _text(binding["tool_call_id"], "TOOL_CALL_ID")
    _require(isinstance(binding["caller_family"], str) and binding["caller_family"] in _ROUTES, "UNKNOWN_CALLER_FAMILY")
    _require(
        isinstance(binding["artifact_class"], str)
        and binding["artifact_class"] in request.CONTRACT_NAMES + (request.CONTRACT_NONE,),
        "UNKNOWN_ARTIFACT_CLASS",
    )


def _validate_binding(binding):
    _object(binding, _BINDING_KEYS, {"image_inventory_sha256"}, label="BINDING")
    _validate_identity(binding)
    for key in ("source_sha256", "packet_sha256", *(["image_inventory_sha256"] if "image_inventory_sha256" in binding else [])):
        _require(isinstance(binding[key], str) and _HEX.fullmatch(binding[key]) is not None, "INVALID_" + key.upper())


def _context(value, operation):
    """Only usable identifiers enter failure metadata; none are authenticated."""
    value = value if isinstance(value, dict) else {}
    if operation == "finish":
        prepared = value.get("prepared")
        binding = prepared.get("binding") if isinstance(prepared, dict) else None
        binding = binding if isinstance(binding, dict) else {}
        artifact_class = binding.get("artifact_class")
    else:
        binding = value
        artifact_class = value.get("contract")
    if not isinstance(artifact_class, str) or artifact_class not in request.CONTRACT_NAMES + (request.CONTRACT_NONE,):
        artifact_class = request.ARTIFACT_CLASS_UNRESOLVED
    family = binding.get("caller_family")
    if not isinstance(family, str) or family not in _ROUTES:
        family = None
    invocation_id = binding.get("invocation_id")
    if not isinstance(invocation_id, str) or _UUID.fullmatch(invocation_id) is None:
        invocation_id = "unavailable"
    observation = value.get("observation") if operation == "finish" else None
    observation = observation if isinstance(observation, dict) else {}
    return artifact_class, family, invocation_id, observation


def _observed_text(route, key):
    value = route.get(key) if isinstance(route, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _provenance(invocation_id, observation):
    return envelope.Provenance(
        service_instance=invocation_id,
        channel="host-native-stream",
        requested_model=_observed_text(observation.get("requested"), "model") or "unavailable",
        server_reported_model=_observed_text(observation.get("terminal"), "model"),
        # Supplied JSON is not an attestation, even when all qualification gates pass.
        attested_route=None,
    )


def _compact(delivery, family, observation, reason=None):
    review = delivery.get("review")
    summary = review.get("summary") if isinstance(review, dict) else None
    if delivery["status"] != envelope.STATUS_REVIEWED or not isinstance(summary, str):
        summary = reason or "Native discriminator produced no qualifying review"
    terminal = observation.get("terminal")
    return {
        "status": delivery["status"],
        "verdict": delivery["verdict"],
        "exitCode": request.exit_code_for(delivery["verdict"]),
        "counts": delivery["counts"],
        "must_fix_high_count": delivery["must_fix_high_count"],
        "loop_should_continue": delivery["loop_should_continue"],
        "caller_family": family,
        "reviewer_provider": _observed_text(terminal, "provider"),
        "reviewer_model": _observed_text(terminal, "model"),
        "reviewer_api": _observed_text(terminal, "api"),
        "summary": summary[:1024],
        "result_path": None,
        "receipt_path": None,
    }


def _no_review(value, operation, reason, *, input_budget=None):
    artifact_class, family, invocation_id, observation = _context(value, operation)
    detail = reason
    if input_budget is not None:
        detail += "\n\nObserved input budget: " + json.dumps(input_budget, sort_keys=True, allow_nan=False)
    delivery = envelope.no_review(
        status=envelope.STATUS_TOO_LARGE if input_budget is not None else envelope.STATUS_TOOLING_FAILURE,
        fault=(envelope.FAULT_CALLER_OR_ENVIRONMENT if input_budget is not None or operation != "finish"
               else envelope.FAULT_UNKNOWN_REVIEW_STATE),
        title="Native discriminator produced no qualifying review",
        detail=detail + "\n\n" + _HOST_NOTE,
        recommendation=("Reduce the review packet to the observed input budget before any separately authorized attempt."
                        if input_budget is not None else
                        "Inspect the retained inputs, host observation and raw output; resolve the reported fault before any separately authorized attempt."),
        artifact_class=artifact_class,
        provenance=_provenance(invocation_id, observation),
        run_state=None,
    )
    return {"delivery": delivery, "compact": _compact(delivery, family, observation, reason)}


def _publication_refusal(value):
    # This is an unused candidate until the host's final gates disqualify
    # publication. It does not claim those later events already occurred.
    return _no_review(
        value, "finish",
        "HOST_FINAL_PUBLICATION_DISQUALIFIED: This canonical no-review template is for "
        "a final host cancellation, dependency, retention or publication failure after "
        "the helper snapshot. Consult the retained host observation for the actual cause; "
        "this template is not evidence that a later event has already occurred.",
    )


def _host_metadata():
    return {
        "evidence_root": str(result_store.default_store_root()),
        "python": {"executable": sys.executable, "version": list(sys.version_info[:3])},
    }


def _failure(value, operation, reason, *, input_budget=None):
    result = {"status": "finished" if operation == "finish" else "refused", **_host_metadata(),
              **_no_review(value, operation, reason, input_budget=input_budget)}
    if operation != "finish":
        result["reason"] = reason
    else:
        result["publication_refusal"] = _publication_refusal(value)
        result["diagnostic_delivery"] = None
        raw = value.get("raw_text") if isinstance(value, dict) else None
        if isinstance(raw, str) and raw:
            # The host separately retains the original raw text. Optional content
            # diagnostics are not nested into or substituted for canonical no-review.
            artifact_class, _, invocation_id, observation = _context(value, operation)
            try:
                result["diagnostic_delivery"] = envelope.deliver(
                    model_text=raw, artifact_class=artifact_class,
                    provenance=_provenance(invocation_id, observation),
                    error_condition="Native review did not qualify: " + reason,
                    caller_notes=(_HOST_NOTE, "Diagnostic content is not a qualifying native review."),
                )
            except Exception as error:
                result.update(_no_review(
                    value, operation, reason + "; diagnostic delivery failed: "
                    + type(error).__name__ + ": " + str(error), input_budget=input_budget,
                ))
    return result


def handshake(value):
    """Negotiate semantic compatibility without preparing or dispatching review."""
    try:
        _object(value, {"protocol", "schemas", "capabilities"}, label="HANDSHAKE_INPUT")
        protocol = _text(value["protocol"], "HANDSHAKE_PROTOCOL")
        _require(protocol == _NATIVE_PROTOCOL, "INCOMPATIBLE_NATIVE_PROTOCOL")
        schemas = value["schemas"]
        _object(schemas, set(_NATIVE_SCHEMAS), label="HANDSHAKE_SCHEMAS")
        _require(all(type(schemas[name]) is int for name in _NATIVE_SCHEMAS), "HANDSHAKE_SCHEMA_VERSION_NOT_INTEGER")
        _require(schemas == _NATIVE_SCHEMAS, "INCOMPATIBLE_NATIVE_SCHEMAS")
        capabilities = value["capabilities"]
        _require(isinstance(capabilities, list) and bool(capabilities), "HANDSHAKE_CAPABILITIES_MUST_BE_NONEMPTY_ARRAY")
        for capability in capabilities:
            _text(capability, "HANDSHAKE_CAPABILITY")
        _require(len(set(capabilities)) == len(capabilities), "HANDSHAKE_DUPLICATE_CAPABILITY")
        _require(set(capabilities) <= set(_NATIVE_CAPABILITIES), "UNSUPPORTED_NATIVE_CAPABILITY")
        dependencies = _code_dependencies()
        response = {
            "status": "compatible",
            "contract": {"protocol": _NATIVE_PROTOCOL, "schemas": dict(_NATIVE_SCHEMAS),
                         "capabilities": list(_NATIVE_CAPABILITIES)},
            "dependencies": dependencies,
            **_host_metadata(),
        }
        _require(dependencies == _code_dependencies(), "CODE_DEPENDENCIES_CHANGED_DURING_HANDSHAKE")
        return response
    except Exception as error:
        return _failure(value, "handshake", type(error).__name__ + ": " + str(error))


def prepare(value):
    """Read the original artifact once and return a bound canonical snapshot."""
    try:
        _object(value, {"invocation_id", "tool_call_id", "caller_family", "contract", "source"}, {"focus", "images"}, label="PREPARE_INPUT")
        binding = {
            "invocation_id": value["invocation_id"], "tool_call_id": value["tool_call_id"],
            "caller_family": value["caller_family"], "artifact_class": value["contract"],
        }
        _validate_identity(binding)
        source_path = _text(value["source"], "SOURCE_PATH")
        argv = [request.FLAG_CONTRACT, value["contract"], request.FLAG_SOURCE, source_path]
        if "focus" in value:
            argv.extend((request.FLAG_PROMPT_FILE, _text(value["focus"], "FOCUS_PATH")))
        resolved = request.parse(argv, contracts_dir=_ROOT / "contracts").review
        assets, contract_text, dependencies = _resources(resolved.artifact_class)
        acquisition = request._acquire(resolved, packet)
        _require(bool(acquisition.text), "EMPTY_SOURCE_ARTIFACT")
        focus = ""
        if resolved.prompt_file is not None:
            focus = acquisition.text if resolved.prompt_file.samefile(resolved.source) else request._read_text(
                resolved.prompt_file, "the caller review focus", resolved.artifact_class,
            )
        images = _image_snapshots(value["images"]) if "images" in value else []
        built = _build(acquisition.text, resolved.artifact_class, focus, assets, contract_text, images)
        if images:
            binding["image_inventory_sha256"] = _inventory_digest(images)
        _require(dependencies == _dependencies(resolved.artifact_class), "DEPENDENCIES_CHANGED_DURING_PREPARATION")
        binding["source_sha256"] = _sha(acquisition.text)
        binding["packet_sha256"] = built.packet_digest
        return {
            "status": "prepared", "binding": binding, "packet_text": built.text,
            "source_text": acquisition.text, "dependencies": dependencies,
            "reviewer_route": dict(_ROUTES[binding["caller_family"]]),
            **({"images": images} if images else {}),
            **_host_metadata(),
        }
    except Exception as error:
        return _failure(value, "prepare", type(error).__name__ + ": " + str(error))


def _validate_prepared(prepared):
    _object(prepared, _PREPARED_KEYS, {"images"}, label="PREPARED")
    _require(prepared["status"] == "prepared", "PREPARATION_DID_NOT_QUALIFY")
    binding = prepared["binding"]
    _validate_binding(binding)
    images = prepared.get("images", [])
    _require(("images" in prepared) == ("image_inventory_sha256" in binding), "IMAGE_BINDING_MISSING")
    if "images" in prepared:
        _validate_images(images)
        _require(_inventory_digest(images) == binding["image_inventory_sha256"], "IMAGE_INVENTORY_DIGEST_MISMATCH")
    _require(prepared["reviewer_route"] == _ROUTES[binding["caller_family"]], "FORGED_REVIEWER_ROUTE")
    metadata = _host_metadata()
    _require(prepared["evidence_root"] == metadata["evidence_root"], "STALE_EVIDENCE_ROOT")
    _require(prepared["python"] == metadata["python"], "PYTHON_RUNTIME_CHANGED")
    source_text = _text(prepared["source_text"], "SOURCE_SNAPSHOT", nonempty=False)
    _require(bool(source_text), "EMPTY_SOURCE_SNAPSHOT")
    packet_text = _text(prepared["packet_text"], "PACKET_TEXT")
    _require(_sha(source_text) == binding["source_sha256"], "SOURCE_SNAPSHOT_DIGEST_MISMATCH")
    _require(_sha(packet_text) == binding["packet_sha256"], "PACKET_DIGEST_MISMATCH")
    artifact_class = binding["artifact_class"]
    assets, contract_text, dependencies = _resources(artifact_class)
    _require(prepared["dependencies"] == dependencies, "STALE_OR_FORGED_DEPENDENCIES")

    # Recover only the caller-owned focus slot, using the canonical assembly's
    # complete fixed prefix and suffix. Rebuilding proves that no tool-owned row,
    # contract, schema, fence or source snapshot was substituted. No caller path
    # survives preparation, so finish cannot reread the original file.
    template = _build(source_text, artifact_class, "", assets, contract_text, images)
    artifact_block = packet._fence("ARTIFACT", source_text)[0]
    suffix = _review_prompt("", images) + "\n\n" + packet._ROW_BANNERS[5] + "\n\n" + artifact_block + "\n"
    _require(template.text.endswith(suffix), "CANONICAL_PACKET_LAYOUT_MISMATCH")
    prefix = template.text[:-len(suffix)]
    _require(
        len(packet_text) >= len(prefix) + len(suffix)
        and packet_text.startswith(prefix) and packet_text.endswith(suffix),
        "FORGED_CANONICAL_PACKET",
    )
    focus = packet_text[len(prefix):len(packet_text) - len(suffix)]
    rebuilt = _build(source_text, artifact_class, focus, assets, contract_text, images)
    _require(rebuilt.text == packet_text and rebuilt.packet_digest == binding["packet_sha256"], "FORGED_CANONICAL_PACKET")
    _require(dependencies == _dependencies(artifact_class), "DEPENDENCIES_CHANGED_DURING_PACKET_VALIDATION")
    return binding, dependencies


def _capacity_refusal(observation):
    budget = observation.get("input_budget")
    _require(isinstance(budget, dict), "PACKET_TOO_LARGE_WITHOUT_OBSERVED_BUDGET")
    _require(all(type(budget.get(key)) is int and 0 < budget[key] <= 2**53 - 1
                 for key in ("contextWindow", "maxTokens", "budget", "tokens"))
             and type(budget.get("exact")) is bool and budget.get("fits") is False,
             "PACKET_TOO_LARGE_WITH_INVALID_BUDGET")
    _require(budget["budget"] == budget["contextWindow"] - budget["maxTokens"]
             and budget["tokens"] > budget["budget"], "PACKET_TOO_LARGE_WITH_INCONSISTENT_BUDGET")
    _require(type(observation["adapter_invocations"]) is int and observation["adapter_invocations"] == 0
             and observation["terminal"] is None and observation["publication_allowed"] is False,
             "PACKET_TOO_LARGE_NOT_PRE_INFERENCE")
    return PacketTooLarge(budget)


def _validate_observation(observation, binding):
    _object(observation, _OBSERVATION_KEYS, _OBSERVATION_METADATA_KEYS, label="HOST_OBSERVATION")
    _validate_binding(observation["binding"])
    _require(observation["binding"] == binding, "STALE_OR_MISMATCHED_HOST_BINDING")
    _require(type(observation["cancelled"]) is bool, "CANCELLED_NOT_BOOLEAN")
    _require(observation["cancelled"] is False, "INVOCATION_CANCELLED")
    reason = observation["reason"]
    if reason is not None:
        _text(reason, "HOST_FAILURE_REASON")
        if reason == "PACKET_TOO_LARGE":
            raise _capacity_refusal(observation)
        raise NoReview("HOST_EXECUTION_OR_RETENTION_FAILURE: " + reason)
    expected = _ROUTES[binding["caller_family"]]
    for key in ("requested", "resolved"):
        _object(observation[key], set(expected), label=key.upper() + "_ROUTE")
        _require(observation[key] == expected, key.upper() + "_ROUTE_MISMATCH")
    terminal = observation["terminal"]
    _object(terminal, set(expected) | {"stopReason", "toolCallCount"}, label="TERMINAL")
    _require({key: terminal[key] for key in expected} == expected, "TERMINAL_ROUTE_MISMATCH")
    _require(terminal["stopReason"] == "stop", "NONQUALIFYING_TERMINAL_STOP")
    _require(type(terminal["toolCallCount"]) is int and terminal["toolCallCount"] == 0, "TERMINAL_TOOL_USE_OR_UNOBSERVED")
    _require(type(observation["adapter_invocations"]) is int and observation["adapter_invocations"] == 1, "NOT_ONE_ADAPTER_INVOCATION")
    for key in ("local_settled", "publication_allowed"):
        _require(type(observation[key]) is bool, key.upper() + "_NOT_BOOLEAN")
    _require(observation["local_settled"] is True, "LOCAL_STREAM_NOT_SETTLED")
    _require(observation["publication_allowed"] is True, "RESULT_RETENTION_OR_PUBLICATION_NOT_QUALIFIED")
    _require(observation["upstream_identity"] is None, "UNSUPPORTED_UPSTREAM_IDENTITY_CLAIM")


def _validate_image_observation(prepared, observation):
    images = prepared.get("images")
    if images is None:
        return
    identity = prepared["binding"]["image_inventory_sha256"]
    context = observation.get("context")
    budget = observation.get("input_budget")
    _require(isinstance(context, dict) and context.get("image_inventory_sha256") == identity, "IMAGE_CONTEXT_UNOBSERVED")
    _require(context.get("parent_history_supplied") is False and context.get("responses_continuation_supplied") is False
             and context.get("message_count") == 1 and context.get("tool_count") == 0
             and context.get("native_session_id") == prepared["binding"]["invocation_id"], "IMAGE_CONTEXT_NOT_ISOLATED")
    filename = Path(_text(context.get("path"), "IMAGE_CONTEXT_PATH"))
    _require(filename.is_absolute() and filename.resolve().is_relative_to(Path(prepared["evidence_root"]).resolve()), "IMAGE_CONTEXT_OUTSIDE_EVIDENCE")
    raw = filename.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == context.get("sha256"), "IMAGE_CONTEXT_DIGEST_MISMATCH")
    captured = _json(raw.decode("utf-8"))
    _object(captured, {"systemPrompt", "messages", "tools"}, label="IMAGE_CONTEXT")
    _require(captured["tools"] == [] and isinstance(captured["systemPrompt"], list) and bool(captured["systemPrompt"])
             and all(isinstance(text, str) and text for text in captured["systemPrompt"]), "IMAGE_CONTEXT_NOT_ISOLATED")
    messages = captured["messages"]
    _require(isinstance(messages, list) and len(messages) == 1, "IMAGE_CONTEXT_NOT_FRESH")
    _object(messages[0], {"role", "content", "timestamp"}, label="IMAGE_MESSAGE")
    expected = [{"type": "text", "text": prepared["packet_text"]}] + [
        {"type": "image", "data": image["data"], "mimeType": image["media_type"]} for image in images
    ]
    _require(messages[0]["role"] == "user" and messages[0]["content"] == expected, "ATTACHED_IMAGE_CONTEXT_MISMATCH")
    retained = context.get("images")
    _require(isinstance(retained, list) and len(retained) == len(images), "IMAGE_RETENTION_UNOBSERVED")
    for image, actual in zip(images, retained):
        _object(actual, {"path", "sha256", "media_type", "bytes", "retained_path", "width", "height", "tokens"}, label="RETAINED_IMAGE")
        _require(all(actual[key] == image[key] for key in ("path", "sha256", "media_type", "bytes")), "RETAINED_IMAGE_IDENTITY_MISMATCH")
        _require(all(type(actual[key]) is int and actual[key] > 0 for key in ("width", "height", "tokens")), "IMAGE_CAPACITY_UNOBSERVED")
        retained_path = Path(_text(actual["retained_path"], "RETAINED_IMAGE_PATH"))
        _require(retained_path.is_absolute() and retained_path.resolve().parent == filename.resolve().parent, "IMAGE_RETENTION_OUTSIDE_CONTEXT")
        data = retained_path.read_bytes()
        _require(len(data) == image["bytes"] and hashlib.sha256(data).hexdigest() == image["sha256"], "RETAINED_IMAGE_DIGEST_MISMATCH")
    _require(isinstance(budget, dict) and budget.get("method") == "native-verified-image-rule+text-budget"
             and budget.get("image_inventory_sha256") == identity and budget.get("fits") is True
             and isinstance(budget.get("image_rule"), dict) and budget["image_rule"].get("verified") is True,
             "WHOLE_IMAGE_INPUT_CAPACITY_UNOBSERVED")
    _require(all(type(budget.get(key)) is int and budget[key] > 0 for key in
                 ("contextWindow", "maxTokens", "budget", "image_tokens", "framing_tokens", "tokens", "image_count_budget")), "INVALID_IMAGE_INPUT_CAPACITY")
    _require(type(budget.get("image_count")) is int and budget["image_count"] == len(images) <= budget["image_count_budget"], "IMAGE_COUNT_CAPACITY_UNOBSERVED")
    _require(isinstance(budget.get("sdk_sources"), dict) and bool(budget["sdk_sources"]), "NATIVE_IMAGE_BUDGET_SOURCES_UNOBSERVED")
    text = budget.get("text")
    _require(isinstance(text, dict) and type(text.get("tokens")) is int and text["tokens"] >= 0
             and type(text.get("exact")) is bool and text.get("fits") is True, "IMAGE_TEXT_BUDGET_UNOBSERVED")
    _require(budget["budget"] == budget["contextWindow"] - budget["maxTokens"]
             and budget["image_tokens"] == sum(image["tokens"] for image in retained)
             and budget["tokens"] == text["tokens"] + budget["image_tokens"] + budget["framing_tokens"]
             and budget["tokens"] <= budget["budget"], "WHOLE_IMAGE_INPUT_CAPACITY_MISMATCH")


def finish(value):
    """Strictly grade raw content only after bound host execution gates qualify."""
    try:
        _object(value, {"prepared", "raw_text", "observation"}, label="FINISH_INPUT")
        binding, dependencies = _validate_prepared(value["prepared"])
        observation = value["observation"]
        _validate_observation(observation, binding)
        _validate_image_observation(value["prepared"], observation)
        raw = _text(value["raw_text"], "RAW_TEXT", nonempty=False)
        documents = envelope.parse_stream(raw)
        expected_echo = None if binding["artifact_class"] == request.CONTRACT_NONE else binding["artifact_class"]
        for index, document in enumerate(documents):
            reasons = envelope.conformance_reasons(document)
            _require(not reasons, "NONCONFORMING_DOCUMENT_" + str(index + 1) + ": " + "; ".join(reasons))
            _require(document["artifact_class_echo"] == expected_echo, "ARTIFACT_CLASS_ECHO_MISMATCH_" + str(index + 1))
        delivery = envelope.deliver(
            model_text=raw,
            artifact_class=binding["artifact_class"],
            provenance=_provenance(binding["invocation_id"], observation),
            caller_notes=(_HOST_NOTE,),
        )
        _require(
            delivery["status"] == envelope.STATUS_REVIEWED
            and delivery["review_conforming"] is True and delivery["contract_echo"]["agrees"] is True
            and request.exit_code_for(delivery["verdict"]) in (0, 2, 4),
            "CANONICAL_DELIVERY_DID_NOT_QUALIFY",
        )
        _require(dependencies == _dependencies(binding["artifact_class"]), "DEPENDENCIES_CHANGED_DURING_GRADING")
        return {
            "status": "finished", "delivery": delivery,
            "compact": _compact(delivery, binding["caller_family"], observation),
            "publication_refusal": _publication_refusal(value), "diagnostic_delivery": None,
        }
    except PacketTooLarge as error:
        return _failure(value, "finish", str(error), input_budget=error.budget)
    except Exception as error:
        return _failure(value, "finish", type(error).__name__ + ": " + str(error))


def _arguments(argv):
    _require(bool(argv) and argv[0] in ("handshake", "prepare", "finish"), "EXPECTED_HANDSHAKE_PREPARE_OR_FINISH")
    values = {}
    index = 1
    while index < len(argv):
        key = argv[index]
        _require(key in ("--input", "--output") and key not in values, "UNKNOWN_OR_DUPLICATE_CLI_FLAG")
        _require(index + 1 < len(argv) and not argv[index + 1].startswith("--"), "CLI_FLAG_WITHOUT_VALUE")
        values[key] = _text(argv[index + 1], "CLI_PATH")
        index += 2
    _require(set(values) == {"--input", "--output"}, "INPUT_AND_OUTPUT_PATHS_REQUIRED")
    return argv[0], Path(values["--input"]), Path(values["--output"])


def _output_candidate(argv):
    indices = [index for index, token in enumerate(argv) if token == "--output"]
    if len(indices) == 1 and indices[0] + 1 < len(argv):
        value = argv[indices[0] + 1]
        if value.strip() and not value.startswith("--"):
            return Path(value)
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    operation = argv[0] if argv and argv[0] in ("handshake", "prepare", "finish") else "prepare"
    output_path = _output_candidate(argv)
    value = None
    try:
        operation, input_path, output_path = _arguments(argv)
        _require(input_path.is_file(), "INPUT_JSON_FILE_MISSING")
        _require(input_path.resolve() != output_path.resolve(), "INPUT_AND_OUTPUT_PATHS_EQUAL")
        _require(not output_path.exists(), "OUTPUT_ALREADY_EXISTS")
        value = _json(request._read_text(input_path, "the native helper input", request.ARTIFACT_CLASS_UNRESOLVED))
        if operation == "handshake":
            result = handshake(value)
        elif operation == "prepare":
            result = prepare(value)
        else:
            result = finish(value)
    except Exception as error:
        result = _failure(value, operation, type(error).__name__ + ": " + str(error))
    try:
        _require(output_path is not None, "OUTPUT_PATH_UNAVAILABLE")
        payload = (json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        # Exclusive creation is also the final race-safe no-overwrite gate. Parents
        # must treat any nonzero helper exit or failed read as no qualifying output.
        with output_path.open("xb") as stream:
            written = stream.write(payload)
            _require(written == len(payload), "INCOMPLETE_HELPER_OUTPUT_WRITE")
            stream.flush()
        return 0
    except Exception as error:
        envelope.bind_diagnostic_stream(sys.stderr)
        failure = _failure(value, operation, "HELPER_OUTPUT_RETENTION_FAILED: " + type(error).__name__ + ": " + str(error))
        sys.stderr.write(json.dumps(failure, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
