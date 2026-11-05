"""Optional ingestion actions, separate from accepted knowledge publication.

The caller owns the Store lock/lifecycle. An immutable batch and each exact POST
intent are committed before submission. Any existing intent can only be collected
or reconciled by GET; absence of a matching task never authorizes another POST.
"""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path


def _envelope(status, results=(), error=None, *, accepted=False, dry_run=False):
    return {"provider": "clickup", "action": "create_task_deduped_dryrun" if dry_run else "create_task_deduped",
            "status": status, "results": list(results), "error": error, "http_status": 0,
            "knowledge_accepted": accepted, "meta": {"duration_ms": 0, "version": "1.0.0"}}


def _helpers():
    path = Path(__file__).resolve().parents[1] / "resources/clickup/create_task_deduped.py"
    spec = importlib.util.spec_from_file_location("_wiki_clickup_tasks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _requests(helper, tasks):
    if not isinstance(tasks, tuple):
        raise ValueError("tasks must be a tuple of action dictionaries")
    result = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Each action must be a dictionary")
        topic = task.get("topic", "default")
        domain = task.get("domain", topic.split("/", 1)[0] if isinstance(topic, str) else "")
        description = task.get("description")
        if description is None:
            title, detail = task.get("title", ""), task.get("detail", "")
            if not isinstance(title, str) or not isinstance(detail, str):
                raise ValueError("Action title/detail must be strings")
            description = f"From: {title}\n\n{detail}\n\nSource: {task.get('url', '')}"
        tags = task.get("tags", ["wiki"] + ([domain] if domain and domain != "default" else []))
        result.append(helper.prepare_task(topic=topic, name=task.get("name"), url=task.get("url"),
                                          description=description, priority=task.get("priority", 4), tags=tags))
    return result


def _journal(store, run_id, operation_id):
    events = []
    for row in store.connection.execute(
            "SELECT event_type,payload_json FROM wf_events WHERE run_id=? AND event_type IN (?,?,?) ORDER BY event_id",
            (run_id, "clickup_batch", "clickup_intent", "clickup_receipt")):
        payload = json.loads(row["payload_json"])
        if payload.get("operation_id") == operation_id:
            events.append((row["event_type"], payload))
    return events


def _authorized(store, run_id):
    store.assert_owner(run_id)
    run = store.read_run(run_id)
    if not run or run["cancel_requested"] or run["status"] in ("cancelled", "failed", "preview"):
        raise ValueError("Cancelled/failed/preview runs cannot issue external actions")


def execute_clickup(*, enabled: bool, dry_run: bool, knowledge_accepted: bool,
                    workflow: str, tasks: tuple[dict, ...], store, run_id: str,
                    operation_id: str, client_factory=None) -> dict:
    """Return a standard provider envelope with separate optional-action status.

    Input tasks: topic/name/url, description OR title/detail, optional domain,
    priority (default4), tags (default wiki + domain). Topic may be a retained
    routing key or a full wiki topic prefix. No input can select a raw list ID.
    """
    # Deliberately precedes helper lookup, validation, credential and Store access.
    if not enabled:
        return _envelope("disabled", accepted=knowledge_accepted)
    if workflow != "wiki-ingest":
        return _envelope("failed", error={"code": "CLICKUP_INGEST_ONLY", "message": "ClickUp opt-in is ingestion-only"}, accepted=knowledge_accepted)
    if not dry_run and not knowledge_accepted:
        return _envelope("blocked", error={"code": "KNOWLEDGE_NOT_ACCEPTED", "message": "Publish accepted knowledge before external actions"})
    results = []
    pending = False
    try:
        helper = _helpers()
        requests = _requests(helper, tasks)
        if dry_run:
            return _envelope("preview", [{"would_check_list": item["list_id"],
                "would_search_url": item["source_url"], "would_create": item["body"],
                "topic": item["topic"], "routed_list_id": item["list_id"]} for item in requests],
                accepted=knowledge_accepted, dry_run=True)
        if not isinstance(run_id, str) or not run_id or not isinstance(operation_id, str) or not operation_id:
            raise ValueError("run_id and operation_id must be nonempty strings")
        _authorized(store, run_id)
        events = _journal(store, run_id, operation_id)
        batch = {"operation_id": operation_id, "requests": requests,
                 "request_sha256": sha256(_json(requests).encode("utf-8")).hexdigest()}
        batches = [payload for kind, payload in events if kind == "clickup_batch"]
        if any(payload != batch for payload in batches) or (events and not batches):
            return _envelope("blocked", error={"code": "ACTION_INTENT_CONFLICT", "message": "Operation is bound to a different immutable batch"}, accepted=True)
        intents, receipts = {}, {}
        for kind, payload in events:
            if kind == "clickup_batch":
                continue
            index = payload.get("index")
            if type(index) is not int or not 0 <= index < len(requests) or payload.get("request") != requests[index]:
                raise ValueError("Stored action identity does not match this batch")
            target = intents if kind == "clickup_intent" else receipts
            if index in target and target[index] != payload:
                raise ValueError("Conflicting duplicate action journal event")
            target[index] = payload
        pending = bool(set(intents) - set(receipts))
        # Collecting settled receipts needs neither credentials nor API access.
        client = None
        if not batches:
            store.append_event(run_id, None, "clickup_batch", batch)
        for index, request in enumerate(requests):
            if index in receipts:
                results.append(receipts[index]["result"])
                continue
            _authorized(store, run_id)
            if client is None:
                client = (client_factory or helper.ClickUpClient)()
            target_url = client.api_base.rstrip("/") + request["path"]
            common = {"operation_id": operation_id, "index": index, "request": request, "target_url": target_url}
            if index in intents and intents[index] != common:
                return _envelope("blocked", results, {"code": "ACTION_INTENT_CONFLICT",
                    "message": "Reconciliation cannot change the exact API target", "index": index}, accepted=True)
            existing = helper.search_tasks_by_url(client, request["list_id"], request["source_url"])
            if existing:
                result = {"status": "reconciled" if index in intents else "dedup",
                          "existing_task": existing, "task_id": existing["id"],
                          "list_id": request["list_id"], "topic": request["topic"],
                          "message": f"Task already exists: {existing['name']} ({existing['id']})"}
                store.append_event(run_id, None, "clickup_receipt", {**common, "result": result})
                pending = False
                results.append(result)
                continue
            if index in intents:
                return _envelope("blocked", results, {"code": "ACTION_OUTCOME_UNRESOLVED",
                    "message": "Existing intent has no exact URL/destination match; no second POST is authorized",
                    "index": index}, accepted=True)
            # Commit ACK is synchronous. A process loss at any later point is an
            # uncertain submission, even if the socket was not actually opened.
            store.append_event(run_id, None, "clickup_intent", common)
            pending = True
            _authorized(store, run_id)
            try:
                http_status, data = client.post(request["path"], body=request["body"])
                if not 200 <= http_status < 300 or not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
                    raise ValueError("Create response has no successful stable task ID")
                destination = data.get("list")
                if destination is not None and (not isinstance(destination, dict) or str(destination.get("id")) != request["list_id"]):
                    raise ValueError("Create response reports a different destination")
                result = {"status": "created", "task_id": data["id"], "task_url": data.get("url"),
                          "task_name": data.get("name"), "list_id": request["list_id"], "topic": request["topic"]}
                store.append_event(run_id, None, "clickup_receipt", {**common, "result": result})
                pending = False
            except Exception as exc:
                # Do not call the helper again or infer no effect from a transport
                # error. The committed intent is sufficient for safe GET recovery.
                store.append_event(run_id, None, "clickup_observed", {**common, "outcome": "unknown",
                    "error": {"code": getattr(exc, "code", "ACTION_RESPONSE_UNAVAILABLE"), "message": str(exc)}})
                return _envelope("blocked", results, {"code": "ACTION_OUTCOME_UNRESOLVED",
                    "message": str(exc), "index": index}, accepted=True)
            results.append(result)
        return _envelope("completed", results, accepted=True)
    except Exception as exc:
        return _envelope("blocked" if pending else "failed", results,
            {"code": "ACTION_OUTCOME_UNRESOLVED" if pending else getattr(exc, "code", "ACTION_FAILED"),
             "message": str(exc)}, accepted=knowledge_accepted, dry_run=dry_run)
