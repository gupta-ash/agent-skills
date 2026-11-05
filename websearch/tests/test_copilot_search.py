"""Offline Copilot refusal contracts and retained isolated parsing algorithms.

No live provider workflow is enabled here. API/CLI tests tripwire environment
selection, private state, credentials, subprocesses and network boundaries.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PROVIDER_PATH = ROOT / "websearch" / "providers" / "copilot-search" / "search.py"
SOURCE_URL = "https://example.com/"
OTHER_URL = "https://example.org/reference"

DISABLED_REASON = (
    "Copilot disabled: reliable search execution is not qualified (search_not_executed); "
    "diagnostics deferred."
)


def model_row(model="gpt-5.5", **changes):
    row = {
        "id": model,
        "capabilities": {"type": "chat", "supports": {"tool_calls": True}},
        "supported_endpoints": ["/responses"],
        "policy": {"state": "enabled"},
    }
    row.update(changes)
    return row


def search_call(urls=(SOURCE_URL,), *, status="completed"):
    return {
        "type": "web_search_call",
        "id": "search-fixture",
        "status": status,
        "action": {"type": "search", "query": "offline fixture", "sources": [
            {"type": "url", "url": url} for url in urls
        ]},
    }


def open_call(url=SOURCE_URL, *, status="completed"):
    return {
        "type": "web_search_call",
        "id": "open-fixture",
        "status": status,
        "action": {"type": "open_page", "url": url},
    }


def message(text, annotations=()):
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text,
                     "annotations": list(annotations)}],
    }


def citation(url=SOURCE_URL, title="Example"):
    return {"type": "url_citation", "url": url, "title": title,
            "start_index": 0, "end_index": 7}


def response(*output):
    return {"id": "resp-offline-fixture", "status": "completed", "output": list(output)}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Cannot load source fixture: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module




class ProviderFixture(unittest.TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory(prefix="copilot-offline-")
        self.addCleanup(self.private.cleanup)
        self.home = Path(self.private.name)
        paths = {
            "HOME": self.home, "USERPROFILE": self.home,
            "APPDATA": self.home / "AppData" / "Roaming",
            "LOCALAPPDATA": self.home / "AppData" / "Local",
            "TMP": self.home / "tmp", "TEMP": self.home / "tmp",
            "TMPDIR": self.home / "tmp", "XDG_CACHE_HOME": self.home / "cache",
        }
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        self.enterContext(mock.patch.dict(os.environ, {
            **{name: str(path) for name, path in paths.items()},
            "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }, clear=True))
        self.enterContext(mock.patch.object(Path, "home", return_value=self.home))
        self.enterContext(mock.patch.object(sys, "dont_write_bytecode", True))
        self.tripwires = []
        for target in ("urllib.request.urlopen", "urllib.request.build_opener",
                       "socket.create_connection", "socket.getaddrinfo",
                       "subprocess.run", "subprocess.Popen"):
            self.tripwires.append(self.enterContext(mock.patch(
                target, side_effect=AssertionError("External effect forbidden"))))
        name = f"_offline_copilot_{id(self)}"
        self.addCleanup(sys.modules.pop, name, None)
        self.provider = load_module(name, PROVIDER_PATH)

    def assert_disabled(self, value):
        self.assertEqual(value["error_code"], "disabled")
        self.assertEqual(value["error"], DISABLED_REASON)
        self.assertEqual(value["text"], "")
        for field in ("results", "candidate_sources", "pages_read"):
            self.assertEqual(value[field], [], field)
        for field in ("tool_calls_made", "search_calls_made", "auth_retries",
                      "transport_fallbacks", "elapsed_ms"):
            self.assertIs(type(value[field]), int, field)
            self.assertEqual(value[field], 0, field)
        self.assertIs(value["cached"], False)
        for field in ("model", "transport", "http_status", "upstream_code",
                      "upstream_type", "upstream_param"):
            self.assertIsNone(value[field], field)
        for tripwire in self.tripwires:
            tripwire.assert_not_called()

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", [str(PROVIDER_PATH), *args]), \
                mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
            status = self.provider.main()
        return status, stdout.getvalue(), stderr.getvalue()


class CatalogSelectionTests(ProviderFixture):

    def test_exact_pin_selects_only_eligible_exact_id(self):
        catalog = {"data": [model_row("gpt-5.5"), model_row("gpt-6.0")]}
        self.assertEqual(self.provider._select_model(catalog, "gpt-6.0"), "gpt-6.0")
        for requested in ("gpt-6", "GPT-6.0", "gpt-4.1", "missing"):
            with self.subTest(requested=requested):
                with self.assertRaises(self.provider.CopilotSearchError) as caught:
                    self.provider._select_model(catalog, requested)
                self.assertEqual(caught.exception.error_code, "model_unavailable")

    def test_missing_or_ineligible_metadata_cannot_qualify(self):
        invalid_rows = [
            model_row("gpt-4.1"), model_row("gpt-4o"), model_row("o3"),
            model_row(""), model_row(7),
            model_row(capabilities=None), model_row(capabilities={}),
            model_row(capabilities={"type": "embeddings", "supports": {"tool_calls": True}}),
            model_row(capabilities={"type": "chat", "supports": {}}),
            model_row(capabilities={"type": "chat", "supports": {"tool_calls": False}}),
            model_row(capabilities={"type": "chat", "supports": {"tool_calls": 1}}),
            model_row(capabilities={"type": "chat", "supports": {"tool_calls": "true"}}),
            model_row(supported_endpoints=["/chat/completions"]),
            model_row(supported_endpoints="/responses"),
            model_row(supported_endpoints=None), model_row(policy={"state": "disabled"}),
        ]
        missing_id = model_row()
        missing_id.pop("id")
        invalid_rows.append(missing_id)
        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(self.provider.CopilotSearchError) as caught:
                    self.provider._select_model({"data": [row]}, None)
                self.assertEqual(caught.exception.error_code, "capability_unavailable")

    def test_malformed_catalog_is_classified(self):
        for catalog in (None, {}, {"data": None}, {"data": {}}, {"data": [None]}, [], "models"):
            with self.subTest(catalog=catalog):
                with self.assertRaises(self.provider.CopilotSearchError) as caught:
                    self.provider._select_model(catalog, None)
                self.assertEqual(caught.exception.error_code, "capability_unavailable")
                self.assertNotIn("Traceback", str(caught.exception))

    def test_identical_duplicate_is_deduplicated_but_conflict_is_refused(self):
        row = model_row()
        self.assertEqual(self.provider._select_model([row, copy.deepcopy(row)], None), "gpt-5.5")
        conflict = model_row(capabilities={"type": "chat", "supports": {"tool_calls": False}})
        with self.assertRaises(self.provider.CopilotSearchError) as caught:
            self.provider._select_model([row, conflict], None)
        self.assertEqual(caught.exception.error_code, "capability_unavailable")

    def test_disabled_explicit_pin_never_substitutes_default(self):
        catalog = [model_row(), model_row("gpt-6.0", policy={"state": "disabled"})]
        with self.assertRaises(self.provider.CopilotSearchError) as caught:
            self.provider._select_model(catalog, "gpt-6.0")
        self.assertEqual(caught.exception.error_code, "model_unavailable")

class ResponseEvidenceTests(ProviderFixture):
    def test_structured_citations_take_precedence_over_markdown_and_candidates(self):
        uncited = "https://example.net/uncited"
        raw = response(search_call((SOURCE_URL, OTHER_URL, uncited)), message(
            f"Structured evidence plus [other source]({OTHER_URL}).", [citation(SOURCE_URL, "Structured title")],
        ))
        parsed = self.provider._parse_response(raw, 10)
        self.assertEqual([(r["title"], r["url"]) for r in parsed["results"]], [
            ("Structured title", SOURCE_URL), ("other source", OTHER_URL),
        ])
        self.assertEqual(parsed["candidate_sources"], [SOURCE_URL, OTHER_URL, uncited])
        self.assertNotIn(uncited, [r["url"] for r in parsed["results"]])
        self.assertEqual(parsed["pages_read"], [])
        self.assertEqual(parsed["tool_calls_made"], 1)
        self.assertEqual(parsed["search_calls_made"], 1)

    def test_candidates_pages_and_completed_call_counts_stay_distinct(self):
        raw = response(
            search_call((SOURCE_URL, SOURCE_URL, OTHER_URL)), open_call(OTHER_URL), open_call(OTHER_URL),
            search_call(("https://failed.example/",), status="failed"),
            open_call("https://pending.example/", status="in_progress"),
            message("No invented citation."),
        )
        parsed = self.provider._parse_response(raw, 10)
        self.assertEqual(parsed["candidate_sources"], [SOURCE_URL, OTHER_URL])
        self.assertEqual(parsed["pages_read"], [OTHER_URL])
        self.assertEqual(parsed["results"], [])
        self.assertEqual(parsed["tool_calls_made"], 3)
        self.assertEqual(parsed["search_calls_made"], 1)

    def test_markdown_accepts_only_http_urls_with_completed_tool_evidence(self):
        failed = "https://failed.example/"
        invented = "https://invented.example/"
        raw = response(
            search_call((SOURCE_URL, "ftp://files.example/")), open_call(OTHER_URL),
            open_call(failed, status="failed"),
            message(f"[source]({SOURCE_URL}) [opened]({OTHER_URL}) [source]({SOURCE_URL}) "
                    f"[invented]({invented}) [failed]({failed}) [unsafe](javascript:alert) "
                    "[ftp](ftp://files.example/) [relative](/local) [anchor](#section)"),
        )
        parsed = self.provider._parse_response(raw, 0)
        self.assertEqual([r["url"] for r in parsed["results"]], [SOURCE_URL, OTHER_URL])
        self.assertEqual(parsed["pages_read"], [OTHER_URL])
        self.assertNotIn("ftp://files.example/", parsed["candidate_sources"])

    def test_missing_tool_completion_is_not_evidence(self):
        pending_search = search_call()
        pending_search.pop("status")
        pending_open = open_call(OTHER_URL)
        pending_open.pop("status")
        parsed = self.provider._parse_response(response(
            pending_search, pending_open, message(f"[unproven]({SOURCE_URL})"),
        ), 10)
        self.assertEqual(parsed["tool_calls_made"], 0)
        self.assertEqual(parsed["search_calls_made"], 0)
        self.assertEqual(parsed["candidate_sources"], [])
        self.assertEqual(parsed["pages_read"], [])
        self.assertEqual(parsed["results"], [])

    def test_result_limit_does_not_truncate_text_candidates_or_pages(self):
        raw = response(search_call((SOURCE_URL, OTHER_URL)), open_call(OTHER_URL), message(
            "Full answer retained.", [citation(SOURCE_URL, "First"), citation(OTHER_URL, "Second")],
        ))
        limited = self.provider._parse_response(raw, 1)
        unlimited = self.provider._parse_response(raw, 0)
        self.assertEqual(len(limited["results"]), 1)
        self.assertEqual(len(unlimited["results"]), 2)
        for key in ("text", "candidate_sources", "pages_read", "tool_calls_made", "search_calls_made"):
            self.assertEqual(limited[key], unlimited[key])

    def test_bare_prose_is_only_an_isolated_parser_shape(self):
        value = self.provider._parse_response(
            response(message("Bare model prose, not proven search.")), 10)
        self.assertEqual(value["search_calls_made"], 0)
        self.assertEqual(value["tool_calls_made"], 0)
        self.assertEqual(value["results"], [])
        self.assertTrue(value["text"])

    def test_failed_search_is_not_completed_parser_evidence(self):
        value = self.provider._parse_response(response(
            search_call(status="failed"), message("Search was not completed.")), 10)
        self.assertEqual(value["search_calls_made"], 0)
        self.assertEqual(value["tool_calls_made"], 0)
        self.assertEqual(value["candidate_sources"], [])
        self.assertEqual(value["results"], [])

    def test_malformed_response_schema_is_parse_error_not_raw_output(self):
        invalid = (
            None, [], {}, {"status": "completed", "output": None},
            {"status": "completed", "output": "not-a-list"},
            {"status": "completed", "output": [None]},
            response({"type": "message", "content": "not-a-list"}),
            response(message(123)),
            response({"type": "web_search_call", "status": "completed", "action": []}),
            {"status": "incomplete", "output": [message("Partial answer")]},
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(self.provider.CopilotSearchError) as caught:
                    self.provider._parse_response(raw, 10)
                self.assertEqual(caught.exception.error_code, "parse_error")


class DisabledProviderTests(ProviderFixture):
    def setUp(self):
        super().setUp()
        for name in ("SearchCache", "IDETokenManager", "_get", "_post", "_read_json",
                     "_models_url", "_select_model", "_client_headers", "_build_body"):
            self.tripwires.append(self.enterContext(mock.patch.object(
                self.provider, name, side_effect=AssertionError("Disabled provider effect"))))
        environment = mock.MagicMock(wraps=os.environ)
        environment.get.side_effect = AssertionError("Disabled provider environment selection")
        environment.__getitem__.side_effect = AssertionError("Disabled provider environment read")
        self.enterContext(mock.patch.object(self.provider, "os", types.SimpleNamespace(
            environ=environment)))
        self.tripwires.extend((environment.get, environment.__getitem__))
        for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes",
                     "mkdir", "unlink"):
            self.tripwires.append(self.enterContext(mock.patch.object(
                Path, name, side_effect=AssertionError("Disabled provider private state"))))

    def test_api_defaults_pins_cache_and_direct_environment_cannot_enable_provider(self):
        with mock.patch.dict(os.environ, {
            "COPILOT_SEARCH_MODEL": "gpt-6.0",
            "COPILOT_SEARCH_BYPASS_PROXY": "1",
            "COPILOT_PROXY_URL": "http://invalid.example/responses",
        }):
            for pin in (None, "gpt-5.5", "gpt-6.0", "", " ", "invalid-model"):
                for cache in (False, True):
                    for format in ("json", "markdown", "both"):
                        with self.subTest(pin=pin, cache=cache, format=format):
                            value = self.provider.copilot_search(
                                "offline fixture", "high", 1, format, cache, model=pin)
                            self.assert_disabled(value)
                            self.assertEqual(value["query"], "offline fixture")
                            self.assertEqual(value["context_size"], "high")
                            if format == "json":
                                self.assertNotIn("markdown", value)
                            else:
                                self.assertIn(DISABLED_REASON, value["markdown"])

    def test_unconditional_refusal_precedes_input_validation(self):
        value = self.provider.copilot_search(
            "FETCHED_URL: https://example.com/", "invalid-context", -1,
            "both", True, model=object())
        self.assert_disabled(value)
        with self.assertRaises(TypeError):
            self.provider.copilot_search("fixture", "medium", 10, "json", False, "gpt-6.0")

    def test_cli_all_formats_exit_one_without_any_effect_or_model_bypass(self):
        for format in ("json", "markdown", "both"):
            for options in ((), ("--no-cache", "--model", "gpt-6.0"),
                            ("--model", "")):
                with self.subTest(format=format, options=options):
                    status, stdout, stderr = self.cli(
                        "--query", "offline fixture", "--format", format, *options)
                    self.assertEqual(status, 1)
                    self.assertEqual(stderr, "")
                    if format == "markdown":
                        self.assertIn(DISABLED_REASON, stdout)
                    else:
                        self.assert_disabled(json.loads(stdout))
                    for tripwire in self.tripwires:
                        tripwire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
