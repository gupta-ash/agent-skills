"""Source-only consumers using a real loopback HTTP server and temporary Store.

No account, actual ClickUp endpoint, live credentials or native model is used.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "_workflow" not in sys.modules:
    package = types.ModuleType("_workflow")
    package.__path__ = [str(ROOT / "_shared/workflow/src")]
    sys.modules["_workflow"] = package
from _workflow.store import Store
from wiki.runtime import actions

TEST_ROUTES = {key: "synthetic-" + key for key in ("openclaw", "claude", "ai", "tech", "trading", "health", "default")}


class Loopback:
    def __init__(self):
        self.posts = []
        self.gets = []
        self.tasks = []
        self.drop_response = False
        self.post_status = 200
        self.intent_witness = None
        state = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def send_json(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                parsed = urlsplit(self.path)
                state.gets.append(self.path)
                parts = parsed.path.strip("/").split("/")
                list_id = parts[-2]
                tasks = [task for task in state.tasks if task["list"]["id"] == list_id]
                page = int(parse_qs(parsed.query).get("page", [0])[0])
                self.send_json(200, {"tasks": tasks[page * 100:(page + 1) * 100],
                                     "last_page": (page + 1) * 100 >= len(tasks)})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                list_id = self.path.strip("/").split("/")[-2]
                if state.intent_witness:
                    state.intent_witness(list_id, body)
                state.posts.append({"path": self.path, "body": body})
                if state.post_status != 200:
                    self.send_json(state.post_status, {"err": "Synthetic provider refusal"})
                    return
                task = {**body, "id": f"task-{len(state.posts)}", "url": "https://tasks.invalid/created",
                        "list": {"id": list_id}, "status": {"status": "open"}}
                state.tasks.append(task)
                if state.drop_response:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_json(200, task)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/v2"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class ActionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.routes_path = self.root / "clickup-routes.json"
        self.routes_path.write_text(json.dumps(TEST_ROUTES), encoding="utf-8")
        environment = patch.dict(os.environ, {"CLICKUP_ROUTES_FILE": str(self.routes_path)})
        environment.start()
        self.addCleanup(environment.stop)
        self.project = self.root / "project"
        self.store = Store(self.project)
        self.store.initialize()
        self.addCleanup(self.store.close)
        self.store.create_run("actions-run", "wiki-ingest", "b" * 64, {}, {}, run_kind="inline")
        self.helper = actions._helpers()
        self.server = Loopback()
        self.addCleanup(self.server.close)
        self.client_factory = lambda: self.helper.ClickUpClient(
            api_token="synthetic-loopback-token", team_id="synthetic-team", timeout=2, api_base=self.server.base)
        self.tasks = ({"topic": "tech/ai/models", "name": "Try the synthetic method",
                       "url": "https://source.invalid/article", "title": "Synthetic source",
                       "detail": "Compare the method under condition Q."},)

    def execute(self, **kwargs):
        args = dict(enabled=True, dry_run=False, knowledge_accepted=True,
                    workflow="wiki-ingest", tasks=self.tasks, store=self.store,
                    run_id="actions-run", operation_id="action-op", client_factory=self.client_factory)
        args.update(kwargs)
        with self.store.acquire_owner("actions-run"):
            return actions.execute_clickup(**args)

    def events(self):
        return [(row["event_type"], json.loads(row["payload_json"])) for row in
                self.store.connection.execute("SELECT event_type,payload_json FROM wf_events WHERE event_type LIKE 'clickup_%' ORDER BY event_id")]

    def test_disabled_returns_before_helpers_env_factory_or_database(self):
        class Forbidden:
            def __getattribute__(self, name):
                raise AssertionError("Disabled path inspected a forbidden resource")
        with patch.object(actions, "_helpers", side_effect=AssertionError("Helper import on disabled path")), patch.dict(os.environ, {}, clear=True):
            result = actions.execute_clickup(enabled=False, dry_run=False, knowledge_accepted=True,
                workflow="not-a-workflow", tasks=Forbidden(), store=Forbidden(), run_id="", operation_id="",
                client_factory=Forbidden())
        self.assertEqual("disabled", result["status"])
        self.assertEqual([], self.server.posts)
        self.assertEqual([], self.events())

    def test_preview_no_credentials_client_database_or_network(self):
        before = len(list(self.store.connection.execute("SELECT * FROM wf_events")))
        class Forbidden:
            def __getattribute__(self, name):
                raise AssertionError("Preview accessed the Store")
        class RoutesOnly(dict):
            def get(self, key, default=None):
                if key != "CLICKUP_ROUTES_FILE":
                    raise AssertionError("Preview inspected authentication: " + key)
                return str(self_outer.routes_path)
        self_outer = self
        with patch.object(os, "environ", RoutesOnly()):
            result = actions.execute_clickup(enabled=True, dry_run=True, knowledge_accepted=False,
                workflow="wiki-ingest", tasks=self.tasks, store=Forbidden(), run_id="unused", operation_id="unused",
                client_factory=lambda: self.fail("Preview constructed a client"))
        self.assertEqual("preview", result["status"])
        body = result["results"][0]["would_create"]
        self.assertEqual(4, body["priority"])
        self.assertEqual(["wiki", "tech"], body["tags"])
        self.assertEqual(TEST_ROUTES["ai"], result["results"][0]["routed_list_id"])
        self.assertEqual(before, len(list(self.store.connection.execute("SELECT * FROM wf_events"))))
        self.assertEqual(([], []), (self.server.posts, self.server.gets))

    def test_helper_cli_preview_without_token_or_client(self):
        home = self.root / "synthetic-home"
        home.mkdir()
        env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH"}}
        env.update({key: str(home) for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP")})
        env["CLICKUP_ROUTES_FILE"] = str(self.routes_path)
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "wiki/resources/clickup/create_task_deduped.py"),
            "--topic", "wealth/risk", "--name", "Synthetic action", "--url", "https://source.invalid/report",
            "--description", "Synthetic detail", "--dry-run"], env=env, cwd=home, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        envelope = json.loads(result.stdout)
        self.assertEqual("create_task_deduped_dryrun", envelope["action"])
        self.assertEqual(TEST_ROUTES["trading"], envelope["results"]["routed_list_id"])
        self.assertIsNone(envelope["error"])

    def test_post_has_durable_exact_intent_and_settled_result_dedup(self):
        # The server thread observes a separate read-only SQLite connection;
        # Store's main-thread connection is never shared across threads.
        def witness(list_id, body):
            import sqlite3
            connection = sqlite3.connect(f"file:{(self.project / 'state.db').as_posix()}?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT payload_json FROM wf_events WHERE event_type='clickup_intent'").fetchall()
                self.assertEqual(1, len(rows))
                intent = json.loads(rows[0][0])["request"]
                self.assertEqual((list_id, body), (intent["list_id"], intent["body"]))
            finally:
                connection.close()
        self.server.intent_witness = witness
        first = self.execute()
        self.assertEqual("completed", first["status"])
        self.assertEqual("created", first["results"][0]["status"])
        self.assertEqual("task-1", first["results"][0]["task_id"])
        self.assertEqual(1, len(self.server.posts))
        body = self.server.posts[0]["body"]
        self.assertEqual((4, ["wiki", "tech"]), (body["priority"], body["tags"]))
        second = self.execute(client_factory=lambda: self.fail("Settled collection inspected credentials"))
        self.assertEqual(first["results"], second["results"])
        self.assertEqual(["clickup_batch", "clickup_intent", "clickup_receipt"], [kind for kind, payload in self.events()])
        self.assertEqual(1, len(self.server.posts))

    def test_lost_post_response_restart_reconciles_exact_destination_once(self):
        self.server.drop_response = True
        first = self.execute()
        self.assertEqual("blocked", first["status"])
        self.assertTrue(first["knowledge_accepted"])
        self.assertEqual(1, len(self.server.posts))
        self.store.close()
        self.store = Store(self.project)
        self.store.initialize()
        self.addCleanup(self.store.close)
        second = self.execute()
        self.assertEqual("completed", second["status"])
        self.assertEqual("reconciled", second["results"][0]["status"])
        self.assertEqual("task-1", second["results"][0]["task_id"])
        self.assertEqual(1, len(self.server.posts))

    def test_unresolved_intent_never_posts_after_readonly_absence(self):
        self.server.post_status = 503
        self.assertEqual("blocked", self.execute()["status"])
        self.assertEqual(1, len(self.server.posts))
        self.server.post_status = 200
        self.assertEqual("blocked", self.execute()["status"])
        self.assertEqual([], self.server.tasks)
        self.assertEqual(1, len(self.server.posts))

    def test_prefix_url_wrong_destination_and_late_page_are_not_false_dedup(self):
        list_id = TEST_ROUTES["ai"]
        self.server.tasks = [
            {"id": "prefix", "description": "Source: https://source.invalid/article-longer", "list": {"id": list_id}},
            {"id": "wrong-list", "description": "Source: https://source.invalid/article", "list": {"id": TEST_ROUTES["default"]}},
        ]
        first = self.execute()
        self.assertEqual("created", first["results"][0]["status"])
        self.assertEqual(1, len(self.server.posts))
        for index in range(100):
            self.server.tasks.insert(0, {"id": f"unrelated-{index}", "description": "https://source.invalid/other", "list": {"id": list_id}})
        found = self.helper.search_tasks_by_url(self.client_factory(), list_id, "https://source.invalid/article")
        self.assertEqual("task-1", found["id"])
        self.assertTrue(any("page=1" in path for path in self.server.gets))

    def test_exact_dedup_ambiguous_refusal_and_changed_intent(self):
        task = {"id": "already", "name": "Existing", "url": "https://tasks.invalid/already",
                "description": "Source: https://source.invalid/article", "list": {"id": TEST_ROUTES["ai"]}}
        self.server.tasks.append(task)
        result = self.execute()
        self.assertEqual("dedup", result["results"][0]["status"])
        self.assertEqual([], self.server.posts)
        changed = ({**self.tasks[0], "name": "Changed immutable request"},)
        self.assertEqual("ACTION_INTENT_CONFLICT", self.execute(tasks=changed)["error"]["code"])
        self.server.tasks.append({**task, "id": "ambiguous"})
        ambiguous = self.execute(operation_id="new-explicit-operation")
        self.assertEqual("failed", ambiguous["status"])
        self.assertEqual("ambiguous_tasks", ambiguous["error"]["code"])
        self.assertEqual([], self.server.posts)

    def test_mutation_429_and_5xx_have_one_submission_each(self):
        client = self.client_factory()
        for code in (429, 500, 503):
            with self.subTest(code=code):
                before = len(self.server.posts)
                self.server.post_status = code
                with self.assertRaises(self.helper.ClickUpError):
                    client.post("/list/" + TEST_ROUTES["ai"] + "/task", {"name": "Single mutation"})
                self.assertEqual(before + 1, len(self.server.posts))

    def test_uncertain_intent_cannot_change_api_target_on_resume(self):
        self.server.post_status = 503
        self.assertEqual("blocked", self.execute()["status"])
        replacement = Loopback()
        self.addCleanup(replacement.close)
        result = self.execute(client_factory=lambda: self.helper.ClickUpClient(
            api_token="synthetic", team_id="synthetic", api_base=replacement.base))
        self.assertEqual("ACTION_INTENT_CONFLICT", result["error"]["code"])
        self.assertEqual(([], []), (replacement.gets, replacement.posts))
        self.assertEqual(1, len(self.server.posts))

    def test_only_ingestion_after_accepted_knowledge_and_routes(self):
        self.assertEqual("blocked", self.execute(knowledge_accepted=False)["status"])
        self.assertEqual("failed", self.execute(workflow="wiki-content-check")["status"])
        self.assertEqual([], self.events())
        self.assertEqual([], self.server.posts)
        expected = {"tech/ai": TEST_ROUTES["ai"], "tech/devtools/openclaw/plugins": TEST_ROUTES["openclaw"],
                    "tech/devtools/claude-code": TEST_ROUTES["claude"], "tech/network": TEST_ROUTES["tech"],
                    "wealth/risk": TEST_ROUTES["trading"], "health/sleep": TEST_ROUTES["health"],
                    " /TECH/AI/models/ ": TEST_ROUTES["ai"], "technology": TEST_ROUTES["default"],
                    "other/topic": TEST_ROUTES["default"], "ai": TEST_ROUTES["ai"]}
        for topic, list_id in expected.items():
            self.assertEqual(list_id, self.helper.route_topic(topic))

    def test_missing_or_invalid_routes_refuse_only_the_invoked_helper(self):
        with patch.dict(os.environ, {}, clear=True):
            disabled = self.execute(enabled=False)
            self.assertEqual("disabled", disabled["status"])
            refused = self.execute(client_factory=lambda: self.fail("Missing routes inspected auth"))
            self.assertEqual("config_error", refused["error"]["code"])
        invalid = ["{", "[]", "null", json.dumps({"default": "synthetic-default"})]
        for value in ("", "   ", 42, None):
            invalid.append(json.dumps({**TEST_ROUTES, "ai": value}))
        for document in invalid:
            with self.subTest(document=document):
                self.routes_path.write_text(document, encoding="utf-8")
                refused = self.execute(dry_run=True, client_factory=lambda: self.fail("Invalid routes inspected auth"))
                self.assertEqual("config_error", refused["error"]["code"])
        self.routes_path.unlink()
        refused = self.execute(dry_run=True)
        self.assertEqual("config_error", refused["error"]["code"])
        self.assertEqual([], self.events())
        self.assertEqual(([], []), (self.server.posts, self.server.gets))

    def test_route_configuration_is_loaded_at_invocation_not_import(self):
        changed = {**TEST_ROUTES, "ai": "synthetic-new-ai", "custom": "synthetic-custom"}
        self.routes_path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertEqual("synthetic-new-ai", self.helper.route_topic("tech/ai/new"))
        self.assertEqual("synthetic-custom", self.helper.route_topic("custom"))
        self.assertEqual(TEST_ROUTES["default"], self.helper.route_topic("technique"))


if __name__ == "__main__":
    unittest.main()
