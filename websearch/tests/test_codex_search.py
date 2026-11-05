"""Offline consumer-contract tests; no endpoint calls or real credential reads."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
import uuid


ROOT = Path(__file__).resolve().parents[1]


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provider = load_source("codex_provider", ROOT / "providers" / "codex-search" / "search.py")
router = load_source("search_router", ROOT / "scripts" / "search.py")


def row(n=1):
    return {"title": "Source " + str(n), "url": "https://example.org/source/" + str(n),
            "snippet": "Useful snippet " + str(n)}


class CodexSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        # All scenarios run without OMP executable paths, OMP variables or user auth.
        self.env = {"HOME": self.tmp.name, "USERPROFILE": self.tmp.name, "PATH": ""}
        self.environment = mock.patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.home_patch = mock.patch.object(provider.Path, "home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        # Accidental real HTTP is forbidden, including malformed-credential cases.
        self.opener = mock.Mock()
        self.original_build = provider.urllib.request.build_opener
        self.factory = mock.patch.object(provider.urllib.request, "build_opener", return_value=self.opener)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.opener.open.side_effect = AssertionError("Unexpected transport call")

    def auth_file(self, data):
        path = self.home / ".codex" / "auth.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def token(self):
        os.environ["CODEX_ACCESS_TOKEN"] = "fixture-token-secret"

    def reply(self, payload):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8"))
        self.opener.open.side_effect = None
        self.opener.open.return_value = response

    def sanitized(self, result, code):
        self.assertEqual(result["error_code"], code)
        self.assertTrue(result["error"])
        self.assertEqual(result["results"], [])
        text = json.dumps(result)
        for secret in ("fixture-token-secret", "fixture-account-secret", "body-secret", "header-secret"):
            self.assertNotIn(secret, text)
        self.assertGreaterEqual(result["meta"]["duration_ms"], 0)

    def test_environment_precedence_never_reads_or_mixes_file(self):
        self.token()
        self.auth_file({"tokens": {"access_token": "file-token", "account_id": "file-account"}})
        self.reply({"results": [row()]})
        with mock.patch.object(provider.Path, "read_text", side_effect=AssertionError("File auth read")):
            result = provider.codex_search("fixture")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-token-secret")
        self.assertIsNone(request.get_header("Chatgpt-account-id"))
        self.assertEqual(result["results"][0]["snippet"], "Useful snippet 1")
        os.environ["CODEX_ACCOUNT_ID"] = "fixture-account-secret"
        provider.codex_search("fixture")
        self.assertEqual(self.opener.open.call_args.args[0].get_header("Chatgpt-account-id"),
                         "fixture-account-secret")

    def test_file_credentials_are_one_source_and_unchanged(self):
        os.environ["CODEX_ACCESS_TOKEN"] = "   "
        os.environ["CODEX_ACCOUNT_ID"] = "unrelated-env-account"
        path = self.auth_file({"tokens": {"access_token": "file-token", "account_id": "file-account"}})
        before = path.read_bytes()
        self.reply({"results": [row()]})
        result = provider.codex_search("fixture")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer file-token")
        self.assertEqual(request.get_header("Chatgpt-account-id"), "file-account")
        self.assertIsNone(result["error"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob("*") if p.is_file()),
                         [".codex/auth.json"])

    def test_missing_malformed_unreadable_credentials_never_send(self):
        os.environ["OPENAI_API_KEY"] = "not-codex"
        result = provider.codex_search("fixture")
        self.sanitized(result, "auth_error")
        for data in ("body-secret", [], {}, {"tokens": []}, {"tokens": {}},
                     {"tokens": {"access_token": 12}},
                     {"tokens": {"access_token": "fixture-token-secret", "account_id": []}}):
            with self.subTest(data=data):
                path = self.auth_file(data)
                before = path.read_bytes()
                self.sanitized(provider.codex_search("fixture"), "auth_error")
                self.assertEqual(path.read_bytes(), before)
        with mock.patch.object(provider.Path, "read_text", side_effect=PermissionError("body-secret")):
            self.sanitized(provider.codex_search("fixture"), "auth_error")
        self.opener.open.assert_not_called()

    def test_input_bounds_before_auth_or_transport(self):
        invalid = ({"query": " "}, {"max_results": 0}, {"max_results": 21},
                   {"max_results": True}, {"timeout": 0}, {"timeout": -1},
                   {"timeout": float("nan")}, {"timeout": float("inf")},
                   {"recency_days": 0}, {"recency_days": -1}, {"recency_days": 1.5},
                   {"domains": "example.org"}, {"domains": [""]})
        with mock.patch.object(provider, "_credentials", side_effect=AssertionError("Auth accessed")):
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs):
                    args = {"query": "fixture", **kwargs}
                    self.sanitized(provider.codex_search(**args), "invalid_input")
        self.opener.open.assert_not_called()

    def test_exact_request_options_order_and_router_raw_provenance(self):
        self.token()
        self.reply({"results": [row(3), row(1), row(2)], "output": "ignored"})
        result = provider.codex_search("fixture query", 2, 7, ["example.org", "example.com"], 15)
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 15})
        self.assertEqual(request.full_url, "https://chatgpt.com/backend-api/codex/alpha/search")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        body = json.loads(request.data)
        self.assertEqual(str(uuid.UUID(body["id"])), body["id"])
        self.assertEqual(body["commands"]["search_query"], [
            {"q": "fixture query", "recency": 7, "domains": ["example.org", "example.com"]}])
        self.assertEqual([r["url"] for r in result["results"]], [row(3)["url"], row(1)["url"]])
        self.assertEqual([r["position"] for r in result["results"]], [1, 2])
        unified = router._normalize_output(result, "codex-search", None, "web", 0, 10)
        self.assertEqual(unified["results"][0]["snippet"], "Useful snippet 3")
        self.assertEqual(unified["results"][0]["url"], row(3)["url"])
        self.assertEqual(unified["results"][0]["domain"], "example.org")
        self.assertFalse(unified["synthesized"])
        for field in ("text", "candidate_sources", "pages_read"):
            self.assertIsNone(unified[field])

    def test_absent_constraints_stay_absent(self):
        self.token()
        self.reply({"results": [{"url": "https://example.org/", "title": None,
                                  "snippet": None, "domain": None, "type": None, "ref_id": None}]})
        result = provider.codex_search("fixture")
        query = json.loads(self.opener.open.call_args.args[0].data)["commands"]["search_query"][0]
        self.assertEqual(query, {"q": "fixture"})
        self.assertEqual(result["results"], [{"url": "https://example.org/", "title": None,
                                              "snippet": None, "position": 1}])

    def test_router_constraints_reach_provider_request(self):
        self.token()
        self.reply({"results": [row(1), row(2)]})
        for freshness, days in (("hour", 1), ("day", 1), ("week", 7), ("month", 30), ("year", 365)):
            with self.subTest(freshness=freshness):
                cmd, _ = router._build_command("codex-search", None, "fixture", 1,
                                                freshness, ["example.org"], 12, "web")
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = provider.main(cmd[2:])
                self.assertEqual(exit_code, 0)
                body = json.loads(self.opener.open.call_args.args[0].data)
                self.assertEqual(body["commands"]["search_query"],
                                 [{"q": "fixture", "recency": days, "domains": ["example.org"]}])
                self.assertEqual(len(json.loads(stdout.getvalue())["results"]), 1)
                self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 12)

    def route_with_provider(self, payload):
        self.token()
        self.reply(payload)
        calls = []

        def run(cmd, name, sub_type, timeout):
            calls.append(name)
            if name == "codex-search":
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    status = provider.main(cmd[2:])
                return json.loads(stdout.getvalue()), status, 1
            if name == "searxng":
                return {"query": "fixture", "results": [{"url": "https://fallback.example/", "description": "fallback"}]}, 0, 1
            return {"error": "fixture quota exhausted", "error_code": "rate_limited", "results": []}, 1, 1

        with mock.patch.object(router, "_run_provider", side_effect=run), contextlib.redirect_stderr(io.StringIO()):
            result = router.execute_search("fixture", "web", 1, None, None, 10, None, False)
        return result, calls

    def test_explicit_empty_stops_real_router_fallback(self):
        result, calls = self.route_with_provider({"results": []})
        self.assertEqual(result["provider"], "codex-search")
        self.assertEqual(result["results"], [])
        self.assertEqual(calls, ["codex-search"])
        self.assertIsNone(result["error"])

    def test_malformed_schema_triggers_fallback_instead_of_no_match(self):
        bad = (None, [], {}, {"output": "body-secret"}, {"results": None}, {"results": {}},
               {"results": [None]}, {"results": [{}]}, {"results": [{"url": "ftp://example.org/"}]},
               {"results": [{"url": "https:///missing"}]}, {"results": [{"url": "https://example.org:bad/"}]},
               {"results": [{"url": "https://exa mple.org/"}]})
        bad += tuple({"results": [{**row(), field: []}]} for field in ("title", "snippet", "domain", "type", "ref_id"))
        # Validate even malformed rows beyond the requested local count.
        bad += ({"results": [row(), {"url": "not-a-url"}]},)
        for payload in bad:
            with self.subTest(payload=payload):
                result, calls = self.route_with_provider(payload)
                self.assertEqual(result["provider"], "searxng")
                self.assertEqual(calls.count("codex-search"), 1)
                self.assertEqual(calls[-1], "searxng")
                self.assertIsNone(result["error"])
                self.assertEqual(result["results"][0]["url"], "https://fallback.example/")
                codex_errors = [error for error in result["meta"]["fallback_errors"]
                                if error["provider"] == "codex-search"]
                self.assertEqual([error["error_code"] for error in codex_errors], ["parse_error"])

    def test_errors_sanitized_single_attempt_no_auth_writes(self):
        path = self.auth_file({"tokens": {"access_token": "fixture-token-secret",
                                           "account_id": "fixture-account-secret"}})
        before = path.read_bytes()
        errors = [(urllib.error.HTTPError("https://service/", status, "body-secret", {"X": "header-secret"}, io.BytesIO(b"body-secret")), code)
                  for status, code in ((401, "auth_error"), (403, "forbidden"), (429, "rate_limited"), (500, "http_error"))]
        errors += [(socket.timeout("body-secret"), "timeout"),
                   (urllib.error.URLError(socket.timeout("body-secret")), "timeout"),
                   (urllib.error.URLError("body-secret"), "network_error"),
                   (provider.http.client.IncompleteRead(b"body-secret"), "network_error"),
                   (ConnectionError("body-secret"), "network_error")]
        for exc, code in errors:
            with self.subTest(code=code, exception=type(exc).__name__):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = exc
                self.sanitized(provider.codex_search("fixture"), code)
                self.opener.open.assert_called_once()
                self.assertEqual(path.read_bytes(), before)
        self.reply(b"not JSON body-secret")
        self.sanitized(provider.codex_search("fixture"), "parse_error")
        self.reply(b"\xff")
        self.sanitized(provider.codex_search("fixture"), "parse_error")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len([p for p in self.home.rglob("*") if p.is_file()]), 1)

    def test_redirect_refusal_never_forwards_authorization(self):
        self.token()
        # Exercise urllib's real redirect/error processors with a socket-free HTTPS handler.
        original_build = self.original_build
        for status in (301, 302, 303, 307, 308):
            requests = []

            class Transport(urllib.request.HTTPSHandler):
                def https_open(self, request):
                    requests.append(request)
                    headers = Message()
                    headers["Location"] = "https://redirect.example/body-secret"
                    response = urllib.response.addinfourl(io.BytesIO(b"body-secret"), headers, request.full_url, status)
                    response.msg = "Redirect"
                    return response

            real_opener = original_build(provider._NoRedirect(), Transport())
            with mock.patch.object(provider.urllib.request, "build_opener", return_value=real_opener):
                result = provider.codex_search("fixture")
            self.sanitized(result, "http_error")
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].full_url, "https://chatgpt.com/backend-api/codex/alpha/search")

    def test_cli_json_exit_contract(self):
        self.token()
        for argv, payload, expected, code in ((["--query", "fixture"], {"results": []}, 0, None),
                (["--query", "fixture"], {"output": "body-secret"}, 1, "parse_error"),
                (["--query", " "], {"results": []}, 2, "invalid_input"),
                (["--query", "fixture", "--max-results", "21"], {}, 2, "invalid_input")):
            self.reply(payload)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = provider.main(argv)
            self.assertEqual(status, expected)
            self.assertEqual(json.loads(stdout.getvalue())["error_code"], code)
        for argv in ([], ["--query", "fixture", "--timeout", "body-secret"]):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
                provider.main(argv)
            self.assertEqual(raised.exception.code, 2)
            self.sanitized(json.loads(stdout.getvalue()), "invalid_input")

    def test_synthesis_semantics_remain_distinct(self):
        for name in ("copilot-search", "anthropic-search"):
            raw = {"query": "fixture", "results": [], "text": "Synthesized answer",
                   "candidate_sources": ["https://example.org/"], "pages_read": ["https://example.org/read"]}
            self.assertFalse(router._is_error(raw, 0, name))
            self.assertTrue(router._is_error({"results": [], "text": " "}, 0, name))
            output = router._normalize_output(raw, name, None, "synthesis", 0, 1)
            self.assertTrue(output["synthesized"])
            self.assertEqual(output["text"], "Synthesized answer")
            self.assertEqual(output["candidate_sources"], raw["candidate_sources"])
            self.assertEqual(output["pages_read"], raw["pages_read"])


if __name__ == "__main__":
    unittest.main()