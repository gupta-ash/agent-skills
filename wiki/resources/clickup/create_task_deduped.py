#!/usr/bin/env python
"""URL/destination-exact ClickUp routing helper; mutations are never retried.

The installed ingestion controller owns durable intent/recovery. This resource's
CLI is an explicit one-shot helper, not an alternative wiki publication route.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

# File-relative loading works from Windows paths and without cwd/sys.path edits.
if __package__:
    from . import clickup_api as _api
else:
    _spec = importlib.util.spec_from_file_location("_wiki_clickup_api", Path(__file__).with_name("clickup_api.py"))
    _api = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_api)
ClickUpClient = _api.ClickUpClient
ClickUpError = _api.ClickUpError
make_envelope = _api.make_envelope
run_action = _api.run_action

REQUIRED_ROUTES = frozenset({"openclaw", "claude", "ai", "tech", "trading", "health", "default"})


def load_routes() -> dict[str, str]:
    """Read operator-owned routing only when this optional helper is invoked."""
    path = os.environ.get("CLICKUP_ROUTES_FILE", "").strip()
    if not path:
        raise ClickUpError("config_error", "CLICKUP_ROUTES_FILE is required for ClickUp actions")
    try:
        routes = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ClickUpError("config_error", "CLICKUP_ROUTES_FILE must name a readable JSON object") from exc
    if (not isinstance(routes, dict) or not REQUIRED_ROUTES <= routes.keys()
            or any(not isinstance(key, str) or not key.strip()
                   or not isinstance(value, str) or not value.strip() for key, value in routes.items())):
        raise ClickUpError("config_error", "ClickUp routes require nonempty string destinations for "
                           + ", ".join(sorted(REQUIRED_ROUTES)))
    return routes


def route_topic(topic: str) -> str:
    """Preserve explicit routing keys and the ingestion topic-prefix table."""
    key = topic.lower().strip().strip("/")
    routes = load_routes()
    for prefix, route in (("tech/devtools/openclaw", "openclaw"),
                          ("tech/devtools/claude-code", "claude"),
                          ("tech/ai", "ai"), ("tech", "tech"),
                          ("wealth", "trading"), ("health", "health")):
        if key == prefix or key.startswith(prefix + "/"):
            return routes[route]
    return routes.get(key, routes["default"])


def contains_exact_url(description: str, url: str) -> bool:
    """Compare complete URL tokens, not substrings/prefixes or normalized URLs."""
    if not isinstance(description, str):
        return False
    if url in re.findall(r"\[[^\]]*\]\((https?://[^\s]+?)\)", description):
        return True
    for token in re.findall(r"https?://[^\s<>\"'`]+", description):
        if token == url:
            return True
    return False


def prepare_task(*, topic, name, url, description, priority=4, tags=()):
    """Resolve a deterministic request without credentials, clients or effects."""
    if any(not isinstance(value, str) or not value.strip() for value in (topic, name, url)):
        raise ClickUpError("invalid_task", "topic, name and url must be nonempty strings")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or any(c.isspace() for c in url):
        raise ClickUpError("invalid_task", "A public-reference HTTP(S) URL without credentials is required")
    if not isinstance(description, str) or type(priority) is not int or priority not in (1, 2, 3, 4):
        raise ClickUpError("invalid_task", "description/priority is invalid")
    if isinstance(tags, str):
        tags = tuple(tag.strip() for tag in tags.split(",") if tag.strip())
    if not isinstance(tags, (tuple, list)) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ClickUpError("invalid_task", "tags must be strings")
    if not contains_exact_url(description, url):
        description = description.rstrip() + "\n\nSource: " + url
    body = {"name": name, "description": description, "priority": priority}
    if tags:
        body["tags"] = list(dict.fromkeys(tags))
    list_id = route_topic(topic)
    return {"topic": topic, "source_url": url, "list_id": list_id,
            "path": f"/list/{list_id}/task", "body": body}


def search_tasks_by_url(client, list_id: str, url: str):
    """Read every page in the exact destination; reject ambiguous matches."""
    matches = {}
    for page in range(1000):
        _, data = client.get(f"/list/{list_id}/task", params={
            "archived": "false", "include_closed": "true", "page": page})
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise ClickUpError("invalid_response", "Task search did not return a task list")
        tasks = data["tasks"]
        for task in tasks:
            if not isinstance(task, dict):
                raise ClickUpError("invalid_response", "Invalid task in search response")
            destination = task.get("list", {})
            if destination and (not isinstance(destination, dict) or str(destination.get("id")) != str(list_id)):
                continue
            if not contains_exact_url(task.get("description") or task.get("text_content") or "", url):
                continue
            task_id = task.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise ClickUpError("invalid_response", "Matched task has no stable ID")
            status = task.get("status", {})
            matches[task_id] = {"id": task_id, "name": task.get("name"), "url": task.get("url"),
                                "status": status.get("status", "unknown") if isinstance(status, dict) else status,
                                "list_id": str(list_id)}
        if data.get("last_page") is True or not tasks or ("last_page" not in data and len(tasks) < 100):
            break
    else:
        raise ClickUpError("incomplete_search", "Task search pagination did not settle")
    if len(matches) > 1:
        raise ClickUpError("ambiguous_tasks", "Multiple exact URL/destination matches require reconciliation")
    return next(iter(matches.values()), None)


def main():
    parser = argparse.ArgumentParser(description="Create one ClickUp task with exact URL dedup")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--priority", type=int, default=4)
    parser.add_argument("--tags", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    def do_create(start_time):
        request = prepare_task(topic=args.topic, name=args.name, url=args.url,
                               description=args.description, priority=args.priority, tags=args.tags)
        if args.dry_run:
            return make_envelope("create_task_deduped_dryrun", {
                "would_check_list": request["list_id"], "would_search_url": args.url,
                "would_create": request["body"], "topic": args.topic,
                "routed_list_id": request["list_id"],
            }, start_time=start_time)
        client = ClickUpClient()
        existing = search_tasks_by_url(client, request["list_id"], args.url)
        if existing:
            return make_envelope("create_task_deduped", {
                "status": "dedup", "existing_task": existing,
                "message": f"Task already exists: {existing['name']} ({existing['id']})",
                "list_id": request["list_id"], "topic": args.topic,
            }, start_time=start_time)
        _, data = client.post(request["path"], body=request["body"])
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
            raise ClickUpError("unknown_effect", "Create response lacks a task ID; do not resubmit")
        return make_envelope("create_task_deduped", {
            "status": "created", "task_id": data["id"], "task_url": data.get("url"),
            "task_name": data.get("name"), "list_id": request["list_id"], "topic": args.topic,
        }, start_time=start_time)

    run_action("create_task_deduped", do_create)


if __name__ == "__main__":
    main()
