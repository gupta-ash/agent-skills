"""Offline router workflows using real builders, parsing and normalization.

The router consumes serialized subprocess JSON; no provider transport is run.
"""

import contextlib
from datetime import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
QUERY = "workflow fixture"
PRIVATE_MARKER = "PRIVATE_WORKFLOW_METADATA_MUST_NOT_ESCAPE"


DISABLED_REASON = (
    "Copilot disabled: reliable search execution is not qualified (search_not_executed); "
    "diagnostics deferred."
)

class FrozenClock:
    @classmethod
    def utcnow(cls):
        return datetime(2026, 10, 23, 12, 0, 0)


def provider_reply(provider, query=QUERY):
    """Provider-CLI shapes, deliberately unlike the unified result schema."""
    row = {
        "title": "Fixture source",
        "url": "https://www.example.org/evidence",
        "position": 1,
    }
    if provider == "codex-search":
        row["snippet"] = "Codex raw snippet"
    elif provider == "brave":
        row.update(description="Brave description", age="2 days ago",
                   page_age="2026-10-21", source="Fixture News", breaking=True,
                   extra_snippets=["Additional Brave evidence"])
    elif provider == "tavily":
        row.update(content="Tavily content", score=0.91,
                   published_date="2026-10-21")
    elif provider == "serper":
        row.update(description="Serper description", date="2026-10-21",
                   source="Fixture Wire")
    elif provider == "exa":
        row.update(content_excerpt="Exa excerpt", score=0.87,
                   published_date="2026-10-20", author="Fixture Author")
    elif provider == "searxng":
        row.update(description="SearXNG description", score=1.5)
    else:
        raise ValueError("No raw fixture for " + provider)
    payload = {"query": query, "results": [row], "error": None,
               "error_code": None, "meta": {"duration_ms": 37}}
    if provider == "codex-search":
        payload.update(provider="codex-search", synthesized=False, text=None,
                       candidate_sources=None, pages_read=None)
    if provider == "serper":
        payload["supplemental"] = {
            "people_also_ask": [{"question": "Fixture question?"}],
            "related_searches": ["fixture related query"],
            "knowledge_graph": {"title": "Fixture entity"},
        }
    return payload


def failed_reply(provider):
    return ({"query": QUERY, "results": [],
             "error": provider + " fixture rejection",
             "error_code": "fixture_failure"}, 1)




class SearchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.real_run = subprocess.run
        self.real_popen = subprocess.Popen
        self.normal_parent_environment = {
            name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
            if name in os.environ
        }
        self.temporary = tempfile.TemporaryDirectory(
            prefix="websearch-workflows-" + uuid.uuid4().hex + "-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.key_dir = self.home / "keys"
        self.codex_home = self.home / ".codex"
        paths = {
            "HOME": self.home,
            "USERPROFILE": self.home,
            "APPDATA": self.home / "AppData" / "Roaming",
            "LOCALAPPDATA": self.home / "AppData" / "Local",
            "TMP": self.home / "tmp",
            "TEMP": self.home / "tmp",
            "TMPDIR": self.home / "tmp",
            "XDG_CACHE_HOME": self.home / "cache",
            "XDG_CONFIG_HOME": self.home / "config",
            "XDG_DATA_HOME": self.home / "data",
            "CODEX_HOME": self.codex_home,
            "PYTHONPATH": self.home / "pythonpath",
        }
        for path in (*paths.values(), self.key_dir):
            path.mkdir(parents=True, exist_ok=True)
        # Dummy-only state exists before the router import. No inherited keys,
        # proxy configuration, account files or installed profile are visible.
        (self.codex_home / "auth.json").write_text(json.dumps({
            "tokens": {"access_token": "dummy-workflow-token",
                       "account_id": "dummy-workflow-account"},
        }), encoding="utf-8")
        for name in ("brave", "tavily", "serper", "exa"):
            (self.key_dir / (name + ".json")).write_text(json.dumps({
                "keys": ["dummy-workflow-" + name], "current_index": 0,
            }), encoding="utf-8")
        environment = {name: str(path) for name, path in paths.items()}
        environment.update(PATH="", PYTHONDONTWRITEBYTECODE="1",
                           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        patch = mock.patch.dict(os.environ, environment, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(sys, "dont_write_bytecode", True)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(
            subprocess, "run", side_effect=AssertionError("Unexpected subprocess"))
        self.process = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(
            subprocess, "Popen", side_effect=AssertionError("Real process forbidden"))
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(
            socket, "create_connection", side_effect=AssertionError("Network forbidden"))
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(
            socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden"))
        patch.start()
        self.addCleanup(patch.stop)
        for target in ("urllib.request.urlopen", "urllib.request.build_opener"):
            patch = mock.patch(target, side_effect=AssertionError("Live HTTP forbidden"))
            patch.start()
            self.addCleanup(patch.stop)
        self.stderr = io.StringIO()
        redirect = contextlib.redirect_stderr(self.stderr)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        spec = importlib.util.spec_from_file_location(
            "search_workflow_router_" + uuid.uuid4().hex, ROOT / "scripts" / "search.py")
        self.router = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.router)
        self.process.assert_not_called()

    def workflow(self, replies, **overrides):
        """Feed serialized CLI replies through the real _run_provider parser."""
        replies = list(replies)
        self.commands = []
        self.process_options = []

        def respond(command, **options):
            index = len(self.commands)
            self.commands.append(list(command))
            self.process_options.append(options)
            if index >= len(replies):
                raise AssertionError("Unexpected additional provider command")
            payload, returncode = replies[index]
            stdout = payload if isinstance(payload, str) else json.dumps(payload)
            return subprocess.CompletedProcess(command, returncode, stdout, "")

        self.process.reset_mock()
        self.process.side_effect = respond
        arguments = dict(query=QUERY, mode="web", count=5, freshness=None,
                         domains=None, timeout=15, force_provider=None,
                         explain=False, key_dir=str(self.key_dir))
        arguments.update(overrides)
        result = self.router.execute_search(**arguments)
        self.assertEqual(len(self.commands), len(replies),
                         "Every prepared boundary reply must be consumed exactly once")
        return result



    def assert_copilot_disabled(self, result, entry="copilot-search"):
        self.assertEqual(result["error_code"], "all_disabled")
        self.assertTrue(result["error"])
        self.assertEqual(result["provider_errors"], [{
            "provider": entry, "error": DISABLED_REASON, "error_code": "disabled",
        }])
        self.assertEqual(result["results"], [])
        self.assertIs(result["synthesized"], False)
        self.assertIsNone(result["provider"])
        for field in ("text", "candidate_sources", "pages_read"):
            self.assertIsNone(result[field], field)
        for field in ("model", "transport", "http_status"):
            self.assertNotIn(field, result["meta"], field)

    def explicit_synthesis_error_trail(self, payload, fallback):
        if fallback:
            # Exercise the success trail without changing any production route.
            with mock.patch.dict(self.router.ROUTES, {
                    "web": ["anthropic-search", "brave:web"]}):
                result = self.workflow([(payload, 1), (provider_reply("brave"), 0)])
            self.assertEqual([self.selector(cmd) for cmd in self.commands],
                             ["anthropic-search", "brave:web"])
            self.assertEqual(result["provider"], "brave")
            self.assertEqual(result["fallback_position"], 1)
            self.assert_raw(result)
            trail = result["meta"]["fallback_errors"]
        else:
            result = self.workflow([(payload, 1)], mode="synthesis",
                                   force_provider="anthropic-search")
            self.assertEqual(result["error_code"], "all_failed")
            self.assertIs(result["synthesized"], False)
            self.assertEqual(result["results"], [])
            trail = result["provider_errors"]
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["provider"], "anthropic-search")
        self.assertEqual(trail[0]["error"], "Explicit synthesis inference rejected")
        self.assertEqual(trail[0]["error_code"], "http_error")
        return trail[0]

    @staticmethod
    def option(command, name):
        return command[command.index(name) + 1] if name in command else None

    def selector(self, command):
        """Identify the invoked endpoint from its actual path and CLI options."""
        self.assertEqual(command[0], sys.executable)
        provider = Path(command[1]).relative_to(ROOT / "providers").parts[0]
        if provider in ("brave", "serper"):
            return provider + ":" + self.option(command, "--type")
        if provider == "tavily":
            if self.option(command, "--search-depth") == "advanced":
                return "tavily:research"
            return "tavily:" + (self.option(command, "--topic") or "general")
        if provider == "exa":
            category = self.option(command, "--category")
            if category:
                return "exa:" + category.replace(" ", "-")
            if self.option(command, "--type") == "similar":
                return "exa:similar"
            search_type = self.option(command, "--search-type")
            return "exa:" + ({"deep": "deep-search"}.get(search_type, search_type) or "auto")
        return provider

    def assert_options(self, command, **options):
        for flag, value in options.items():
            name = "--" + flag.replace("_", "-")
            self.assertEqual(command.count(name), 1, name)
            self.assertEqual(self.option(command, name), str(value), name)

    def assert_raw(self, result):
        self.assertIsNone(result["error"])
        self.assertIsNone(result["error_code"])
        self.assertIs(result["synthesized"], False)
        for field in ("text", "candidate_sources", "pages_read"):
            self.assertIsNone(result[field], field)
        self.assertEqual(result["meta"]["result_count"], len(result["results"]))

    def test_raw_web_fallback_advances_each_selector_once_in_approved_order(self):
        chain = ["codex-search", "brave:web", "tavily:general", "exa:auto", "searxng"]
        for stop in range(len(chain)):
            with self.subTest(success=chain[stop]):
                provider = chain[stop].split(":", 1)[0]
                replies = [failed_reply(entry) for entry in chain[:stop]]
                replies.append((provider_reply(provider), 0))
                result = self.workflow(replies)
                self.assertEqual([self.selector(cmd) for cmd in self.commands],
                                 chain[:stop + 1])
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["fallback_position"], stop)
                trail = result["meta"].get("fallback_errors", [])
                self.assertEqual([error["provider"] for error in trail], chain[:stop])
                self.assertTrue(all(error["error_code"] == "fixture_failure" for error in trail))
                self.assert_raw(result)

    def test_valid_empty_at_every_web_position_is_terminal(self):
        chain = ["codex-search", "brave:web", "tavily:general", "exa:auto", "searxng"]
        for stop, entry in enumerate(chain):
            with self.subTest(empty=entry):
                provider = entry.split(":", 1)[0]
                empty = provider_reply(provider)
                empty["results"] = []
                replies = [failed_reply(previous) for previous in chain[:stop]]
                result = self.workflow([*replies, (empty, 0)])
                self.assertEqual([self.selector(cmd) for cmd in self.commands], chain[:stop + 1])
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["fallback_position"], stop)
                self.assertEqual(result["results"], [])
                self.assertEqual([error["provider"] for error in result["meta"].get(
                    "fallback_errors", [])], chain[:stop])
                self.assert_raw(result)

    def test_all_web_failures_exhaust_each_selector_once(self):
        chain = ["codex-search", "brave:web", "tavily:general", "exa:auto", "searxng"]
        result = self.workflow([failed_reply(entry) for entry in chain])
        self.assertEqual([self.selector(cmd) for cmd in self.commands], chain)
        self.assertEqual(result["error_code"], "all_failed")
        self.assertEqual([error["provider"] for error in result["provider_errors"]], chain)

    def test_codex_provider_json_and_exit_failures_are_not_empty_success(self):
        rejected = provider_reply("codex-search")
        rejected.update(error="Codex unavailable", error_code="auth_error", results=[])
        cases = [(rejected, 0, "auth_error"),
                 ("not JSON", 0, "parse_error"),
                 ("", 1, "no_output"),
                 (provider_reply("codex-search"), 2, "unknown")]
        for payload, status, code in cases:
            with self.subTest(status=status, code=code):
                result = self.workflow([(payload, status), (provider_reply("brave"), 0)])
                self.assertEqual([self.selector(cmd) for cmd in self.commands],
                                 ["codex-search", "brave:web"])
                self.assertEqual(result["meta"]["fallback_errors"][0]["error_code"], code)
                self.assert_raw(result)

    def test_codex_raw_provenance_order_null_synthesis_and_command_constraints(self):
        query = "raw evidence — fixture"
        payload = provider_reply("codex-search", query)
        payload["results"] = [
            {"title": "Third source", "url": "https://www.example.org/third",
             "snippet": "Third raw snippet", "position": 1},
            {"title": "First source", "url": "https://example.net/first",
             "snippet": "First raw snippet", "position": 2},
        ]
        result = self.workflow([(payload, 0)], query=query, count=2,
                               freshness="hour", domains=" example.org, example.net ",
                               timeout=12)
        command = self.commands[0]
        self.assert_options(command, query=query, max_results=2, recency_days=1,
                            domains="example.org,example.net", timeout=12)
        self.assertNotIn("--key-file", command)
        self.assertEqual(result["query"], query)
        self.assertEqual(result["provider"], "codex-search")
        self.assertEqual(result["provider_mode"], "default")
        self.assertEqual(result["mode"], "web")
        self.assertEqual(result["fallback_position"], 0)
        self.assertEqual([row["url"] for row in result["results"]],
                         [row["url"] for row in payload["results"]])
        self.assertEqual([row["snippet"] for row in result["results"]],
                         ["Third raw snippet", "First raw snippet"])
        self.assertEqual([row["domain"] for row in result["results"]],
                         ["example.org", "example.net"])
        self.assertEqual([row["position"] for row in result["results"]], [1, 2])
        self.assertEqual(result["meta"]["duration_ms"], 37)
        self.assertNotIn("model", result["meta"])
        self.assert_raw(result)
        options = self.process_options[0]
        self.assertTrue(options["capture_output"])
        self.assertTrue(options["text"])
        self.assertEqual(options["encoding"], "utf-8")
        self.assertEqual(options["timeout"], 22)
        self.assertEqual(options["env"]["HOME"], str(self.home))
        self.assertEqual(options["env"]["USERPROFILE"], str(self.home))
        self.assertEqual(options["env"]["LOCALAPPDATA"], str(self.home / "AppData" / "Local"))
        self.assertEqual(options["env"]["PYTHONPATH"].split(os.pathsep),
                         [str(ROOT / "providers" / "_shared"), str(self.home / "pythonpath")])
        self.assertNotIn("OPENAI_API_KEY", options["env"])
        self.assertNotIn("ANTHROPIC_API_KEY", options["env"])

    def test_all_twelve_specialists_keep_endpoint_order_and_stop_on_success(self):
        chains = {
            "news": ["tavily:news", "brave:news", "serper:news", "searxng"],
            "finance": ["brave:news", "serper:news", "tavily:general", "searxng"],
            "tech-news": ["brave:news", "tavily:news", "serper:news", "searxng"],
            "company": ["brave:web", "serper:search", "exa:auto", "searxng"],
            "filings": ["exa:financial-report", "serper:search", "brave:web", "searxng"],
            "people": ["exa:people", "brave:web", "serper:search", "searxng"],
            "research": ["brave:web", "exa:neural", "serper:search", "codex-search", "searxng"],
            "local": ["brave:web", "tavily:general", "serper:search", "searxng"],
            "currency": ["brave:news", "serper:news", "brave:web", "searxng"],
            "social": ["exa:tweet", "brave:news", "serper:news", "searxng"],
            "similar": ["exa:similar"],
            "deep": ["tavily:research", "exa:deep-search"],
        }
        snippets = {"codex-search": "Codex raw snippet", "brave": "Brave description",
                    "tavily": "Tavily content", "serper": "Serper description",
                    "exa": "Exa excerpt", "searxng": "SearXNG description"}
        for mode, chain in chains.items():
            query = "https://example.org/original" if mode == "similar" else QUERY
            for stop, entry in enumerate(chain):
                with self.subTest(mode=mode, success=entry):
                    provider, _, subtype = entry.partition(":")
                    replies = [failed_reply(previous) for previous in chain[:stop]]
                    replies.append((provider_reply(provider, query), 0))
                    result = self.workflow(replies, mode=mode, query=query)
                    self.assertEqual([self.selector(cmd) for cmd in self.commands],
                                     chain[:stop + 1])
                    self.assertEqual(result["provider"], provider)
                    self.assertEqual(result["provider_mode"], subtype or "default")
                    self.assertEqual(result["mode"], mode)
                    self.assertEqual(result["fallback_position"], stop)
                    self.assertEqual(result["results"][0]["snippet"], snippets[provider])
                    self.assertEqual([error["provider"] for error in
                                      result["meta"].get("fallback_errors", [])], chain[:stop])
                    self.assert_raw(result)

    def test_explicit_exa_and_serper_override_general_codex_route(self):
        for entry in ("exa:neural", "serper:search"):
            with self.subTest(provider=entry):
                provider, subtype = entry.split(":")
                result = self.workflow([(provider_reply(provider), 0)],
                                       force_provider=entry, count=7, freshness="year",
                                       domains="example.org", timeout=12)
                command = self.commands[0]
                self.assertEqual(self.selector(command), entry)
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["provider_mode"], subtype)
                self.assertEqual(result["fallback_position"], 0)
                self.assert_options(command, count=7, timeout=12,
                                    key_file=self.key_dir / (provider + ".json"))
                if provider == "exa":
                    self.assert_options(command, query=QUERY, search_type="neural",
                                        include_domains="example.org")
                    self.assertEqual(result["results"][0]["author"], "Fixture Author")
                else:
                    self.assert_options(command, query=QUERY + " site:example.org",
                                        type="search", tbs="qdr:y")
                    self.assertEqual(result["supplemental"],
                                     provider_reply("serper")["supplemental"])
                self.assert_raw(result)

    def test_news_tavily_preserves_topic_time_domains_and_published_date(self):
        result = self.workflow([(provider_reply("tavily"), 0)], mode="news", count=7,
                               freshness="hour", domains=" news.example, wire.example ", timeout=12)
        self.assert_options(self.commands[0], query=QUERY, max_results=7, topic="news",
                            time_range="day", include_domains="news.example,wire.example",
                            timeout=12, key_file=self.key_dir / "tavily.json")
        self.assertEqual(result["results"][0]["snippet"], "Tavily content")
        self.assertEqual(result["results"][0]["pub_date"], "2026-10-21")
        self.assertEqual(result["results"][0]["pub_date_raw"], "2026-10-21")
        self.assertEqual(result["results"][0]["relevance_score"], 0.91)
        self.assert_raw(result)

    def test_finance_brave_keeps_news_endpoint_date_domain_and_hour_rounding(self):
        result = self.workflow([(provider_reply("brave"), 0)], mode="finance", count=7,
                               freshness="hour", domains=" news.example, wire.example ", timeout=12)
        self.assert_options(self.commands[0], query=QUERY + " (site:news.example OR site:wire.example)",
                            count=7, type="news", freshness="pd", timeout=12,
                            key_file=self.key_dir / "brave.json")
        row = result["results"][0]
        self.assertEqual(row["pub_date_raw"], "2026-10-21")
        self.assertEqual(row["page_age"], "2 days ago")
        self.assertEqual(row["source_name"], "Fixture News [BREAKING]")
        self.assertEqual(row["extra_snippets"], ["Additional Brave evidence"])
        self.assert_raw(result)

    def test_finance_fallback_tavily_remains_general_with_filters(self):
        result = self.workflow([failed_reply("brave:news"), failed_reply("serper:news"),
                                (provider_reply("tavily"), 0)], mode="finance",
                               freshness="month", domains="reports.example")
        command = self.commands[-1]
        self.assertEqual(self.selector(command), "tavily:general")
        self.assert_options(command, query=QUERY, time_range="month",
                            include_domains="reports.example")
        self.assertNotIn("--topic", command)
        self.assertNotIn("--search-depth", command)
        self.assert_raw(result)

    def test_filings_exa_category_date_and_domains_are_not_query_rewrites(self):
        with mock.patch.object(self.router, "datetime", FrozenClock):
            result = self.workflow([(provider_reply("exa"), 0)], mode="filings", count=7,
                                   freshness="month", domains=" sec.gov, reports.example ", timeout=12)
        self.assert_options(self.commands[0], query=QUERY, count=7,
                            category="financial report", start_date="2026-09-23",
                            include_domains="sec.gov,reports.example", timeout=12,
                            key_file=self.key_dir / "exa.json")
        self.assertEqual(result["results"][0]["pub_date"], "2026-10-20")
        self.assertEqual(result["results"][0]["author"], "Fixture Author")
        self.assertEqual(result["results"][0]["relevance_score"], 0.87)
        self.assert_raw(result)

    def test_filings_serper_fallback_keeps_search_endpoint_and_explicit_freshness(self):
        result = self.workflow([failed_reply("exa:financial-report"),
                                (provider_reply("serper"), 0)], mode="filings",
                               freshness="year", domains="sec.gov")
        self.assert_options(self.commands[-1], query=QUERY + " site:sec.gov",
                            type="search", tbs="qdr:y")
        self.assertEqual(result["results"][0]["pub_date"], "2026-10-21")
        self.assertEqual(result["results"][0]["source_name"], "Fixture Wire")
        self.assert_raw(result)

    def test_deep_uses_tavily_advanced_then_exa_deep_with_date_and_domain_filters(self):
        for stop in (0, 1):
            with self.subTest(stop=stop), mock.patch.object(self.router, "datetime", FrozenClock):
                replies = [failed_reply("tavily:research")] if stop else []
                replies.append((provider_reply("exa" if stop else "tavily"), 0))
                result = self.workflow(replies, mode="deep", freshness="week",
                                       domains=" research.example, papers.example ", count=7)
                self.assert_options(self.commands[0], query=QUERY, max_results=7,
                                    search_depth="advanced", time_range="week",
                                    include_domains="research.example,papers.example")
                if stop:
                    self.assert_options(self.commands[1], query=QUERY, count=7,
                                        search_type="deep", start_date="2026-10-16",
                                        include_domains="research.example,papers.example")
                    self.assertNotIn("--type", self.commands[1])
                self.assert_raw(result)

    def test_similar_keeps_url_endpoint_exclusion_and_filters(self):
        url = "https://example.org/original"
        with mock.patch.object(self.router, "datetime", FrozenClock):
            result = self.workflow([(provider_reply("exa", url), 0)], mode="similar", query=url,
                                   freshness="week", domains="papers.example", count=7)
        command = self.commands[0]
        self.assert_options(command, type="similar", url=url, count=7,
                            start_date="2026-10-16", include_domains="papers.example",
                            key_file=self.key_dir / "exa.json")
        self.assertIn("--exclude-source-domain", command)
        self.assertNotIn("--query", command)
        self.assert_raw(result)

    def test_serper_auto_freshness_is_limited_to_all_five_time_sensitive_modes(self):
        for mode in ("news", "tech-news", "finance", "currency", "social"):
            for freshness, expected in ((None, "qdr:w"), ("day", "qdr:d")):
                with self.subTest(mode=mode, freshness=freshness):
                    result = self.workflow([(provider_reply("serper"), 0)], mode=mode,
                                           force_provider="serper:news", freshness=freshness)
                    self.assert_options(self.commands[0], type="news", tbs=expected)
                    self.assert_raw(result)
        for mode in ("web", "company", "filings", "people", "research", "local", "similar", "deep"):
            with self.subTest(not_time_sensitive=mode):
                self.workflow([(provider_reply("serper"), 0)], mode=mode,
                              force_provider="serper:news")
                self.assertNotIn("--tbs", self.commands[0])
        self.workflow([(provider_reply("serper"), 0)], mode="news", force_provider="serper:search")
        self.assertNotIn("--tbs", self.commands[0])

    def test_searxng_last_resort_keeps_positional_domain_query_and_raw_results(self):
        result = self.workflow([failed_reply("codex-search"), failed_reply("brave:web"),
                                failed_reply("tavily:general"), failed_reply("exa:auto"),
                                (provider_reply("searxng"), 0)],
                               domains="example.org,example.net", freshness="week", count=7)
        command = self.commands[-1]
        self.assertEqual(command[2], QUERY + " (site:example.org OR site:example.net)")
        self.assert_options(command, count=7)
        self.assertNotIn("--query", command)
        self.assertNotIn("--freshness", command)
        self.assertNotIn("--time-range", command)
        self.assertNotIn("--start-date", command)
        self.assertEqual(result["results"][0]["relevance_score"], 1.5)
        self.assert_raw(result)

    def test_real_loader_command_and_subprocess_preserve_private_environment(self):
        # Only synthetic credentials cross stdin/environment; argv and emitted
        # probe evidence contain paths, variable names and comparison booleans.
        probe = r'''
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

def forbidden(*args, **kwargs):
    raise AssertionError("Credential probe cannot perform an external effect")

socket.create_connection = socket.getaddrinfo = forbidden
subprocess.run = subprocess.Popen = forbidden
urllib.request.urlopen = urllib.request.build_opener = forbidden
expected = json.load(sys.stdin)
checks = {}
try:
    argv = sys.argv[1:]
    script = Path(argv[0])
    provider = script.parents[1].name
    key_file = argv[argv.index("--key-file") + 1]
    spec = importlib.util.spec_from_file_location("credential_probe_provider", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    checks["provider_keys"] = all(os.environ.get(name) == value
                                  for name, value in expected["provider_keys"].items())
    checks["numbered_keys"] = all(os.environ.get(name) == value
                                  for name, value in expected["numbered_keys"].items())
    checks["nonallowlisted"] = os.environ.get("PRIVATE_INHERITANCE_FIXTURE") == expected["private"]
    checks["parent_environment"] = all(os.environ.get(name) == value
                                      for name, value in expected["environment"].items())
    checks["pythonpath"] = os.environ.get("PYTHONPATH") == expected["pythonpath"]
    variable = expected["variable"]
    missing = str(Path(key_file).with_name("absent-keyfile.json"))
    if provider == "brave":
        checks["file_first"] = module._parse_keys(key_file) == expected["file_keys"][:3]
        checks["comma_separated"] = module._parse_keys(missing) == expected["comma_keys"][:3]
        os.environ[variable] = " "
        checks["numbered_fallback"] = module._parse_keys(missing) == expected["numbered_values"]
    else:
        checks["file_first"] = module.load_keys(key_file, variable) == expected["file_keys"]
        checks["comma_separated"] = module.load_keys(missing, variable) == expected["comma_keys"]
except Exception:
    checks["probe_completed"] = False
else:
    checks["probe_completed"] = True
print(json.dumps({"query": "workflow fixture", "results": [], "error": None,
                  "error_code": None, "checks": checks}))
'''
        provider_keys = {name.upper() + "_API_KEY": " , ".join(
            "dummy-inherited-" + name + "-" + str(index) for index in range(4))
            for name in ("brave", "tavily", "exa", "serper")}
        numbered_keys = {"BRAVE_API_KEY_" + str(index): " dummy-numbered-" + str(index) + " "
                         for index in (1, 2, 3)}
        private_value = "synthetic nonallowlisted session value"
        fixture_environment = {
            **self.normal_parent_environment, **provider_keys, **numbered_keys,
            "PRIVATE_INHERITANCE_FIXTURE": private_value,
        }
        with mock.patch.dict(os.environ, fixture_environment):
            parent_environment = os.environ.copy()
            inherited_path = str(ROOT / "providers" / "_shared") + os.pathsep + os.environ["PYTHONPATH"]
            for entry in ("brave:web", "tavily:general", "exa:auto", "serper:search"):
                provider = entry.split(":", 1)[0]
                with self.subTest(provider=provider):
                    file_keys = ["dummy-file-" + provider + "-" + str(index) for index in range(4)]
                    (self.key_dir / (provider + ".json")).write_text(json.dumps({
                        "keys": file_keys, "active": 1,
                    }), encoding="utf-8")
                    variable = provider.upper() + "_API_KEY"
                    expected = {
                        "provider_keys": provider_keys, "numbered_keys": numbered_keys,
                        "private": private_value,
                        "environment": {**parent_environment, "PYTHONPATH": inherited_path},
                        "pythonpath": inherited_path, "variable": variable,
                        "file_keys": file_keys[1:] + file_keys[:1],
                        "comma_keys": [value.strip() for value in provider_keys[variable].split(",")],
                        "numbered_values": [value.strip() for value in numbered_keys.values()],
                    }
                    observed = []

                    def spawn(command, **options):
                        observed.append(list(command))
                        private_values = [*provider_keys.values(), *numbered_keys.values(), private_value,
                                          *file_keys]
                        self.assertTrue(all(value not in json.dumps(command) for value in private_values))
                        with mock.patch.object(subprocess, "Popen", self.real_popen):
                            completed = self.real_run(
                                [command[0], "-S", "-B", "-c", probe, *command[1:]],
                                input=json.dumps(expected), **options)
                        self.assertTrue(completed.returncode == 0)
                        self.assertTrue(completed.stderr == "")
                        try:
                            checks = json.loads(completed.stdout)["checks"]
                        except (ValueError, KeyError, TypeError):
                            self.fail("Credential subprocess did not return boolean probe evidence")
                        self.assertTrue(bool(checks) and all(value is True for value in checks.values()))
                        return completed

                    self.process.reset_mock()
                    self.process.side_effect = spawn
                    result = self.router.execute_search(
                        QUERY, "web", 5, None, None, 15, entry, False, key_dir=str(self.key_dir))
                    self.assertTrue(self.process.call_count == 1)
                    self.assertEqual([self.selector(command) for command in observed], [entry])
                    self.assertEqual(result["provider"], provider)
                    self.assertEqual(result["results"], [])
                    self.assert_raw(result)
                    self.assertTrue(os.environ.copy() == parent_environment)

    def test_forced_and_unforced_copilot_refuse_before_command_construction(self):
        requests = (("synthesis", None), ("web", "copilot-search"),
                    ("synthesis", "copilot-search"),
                    ("news", "copilot-search:diagnostic"))
        with mock.patch.object(self.router, "_build_command", side_effect=AssertionError(
                "Disabled command must not be built")) as build, \
                mock.patch.object(self.router, "_run_provider", side_effect=AssertionError(
                    "Disabled provider must not run")) as run:
            for mode, forced in requests:
                with self.subTest(mode=mode, forced=forced):
                    result = self.workflow([], mode=mode, force_provider=forced)
                    self.assert_copilot_disabled(result, forced or "copilot-search")
            build.assert_not_called()
            run.assert_not_called()
        self.process.assert_not_called()

    def test_disabled_explanations_report_empty_chain_skips_and_classified_reason(self):
        with mock.patch.object(self.router, "_build_command", side_effect=AssertionError(
                "Explanation cannot build a command")) as build, \
                mock.patch.object(self.router, "_run_provider", side_effect=AssertionError(
                    "Explanation cannot run a provider")) as run:
            for mode, forced in (("synthesis", None), ("web", "copilot-search")):
                with self.subTest(mode=mode, forced=forced):
                    result = self.workflow([], mode=mode, force_provider=forced, explain=True)
                    self.assertEqual(result["chain"], [])
                    self.assertEqual(result["disabled_providers"], ["copilot-search"])
                    self.assertEqual(result["skipped_entries"], ["copilot-search"])
                    self.assertEqual(result["provider_errors"], [{
                        "provider": "copilot-search", "error_code": "disabled",
                        "error": DISABLED_REASON,
                    }])
                    self.assertEqual(result["force_provider"], forced)
                    self.assertNotIn("error", result)
            web = self.workflow([], explain=True)
            self.assertEqual(web["chain"], ["codex-search", "brave:web",
                                          "tavily:general", "exa:auto", "searxng"])
            build.assert_not_called()
            run.assert_not_called()

    def test_router_cli_refusal_exits_one_and_explanation_exits_zero_without_execution(self):
        with mock.patch.object(self.router, "_build_command", side_effect=AssertionError(
                "Disabled CLI cannot build a command")) as build, \
                mock.patch.object(self.router, "_run_provider", side_effect=AssertionError(
                    "Disabled CLI cannot run a provider")) as run:
            for route in (("--mode", "synthesis"), ("--force-provider", "copilot-search")):
                for explain in (False, True):
                    with self.subTest(route=route, explain=explain):
                        stdout = io.StringIO()
                        arguments = ["search.py", "--query", QUERY, *route]
                        if explain:
                            arguments.append("--explain-routing")
                        with mock.patch.object(sys, "argv", arguments), \
                                contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as exit:
                            self.router.main()
                        self.assertEqual(exit.exception.code, 0 if explain else 1)
                        result = json.loads(stdout.getvalue())
                        if explain:
                            self.assertEqual(result["chain"], [])
                            self.assertEqual(result["skipped_entries"], ["copilot-search"])
                        else:
                            self.assert_copilot_disabled(result)
            build.assert_not_called()
            run.assert_not_called()
        self.process.assert_not_called()

    def test_disabled_copilot_skip_trail_precedes_an_explicit_raw_candidate(self):
        with mock.patch.dict(self.router.ROUTES, {
                "web": ["copilot-search", "brave:web"]}):
            result = self.workflow([(provider_reply("brave"), 0)])
        self.assertEqual([self.selector(cmd) for cmd in self.commands], ["brave:web"])
        self.assertEqual(result["meta"]["fallback_errors"], [{
            "provider": "copilot-search", "error": DISABLED_REASON, "error_code": "disabled",
        }])
        self.assert_raw(result)

    def test_explicit_synthesis_failure_trails_preserve_safe_metadata(self):
        populated = {"model": "gpt-5.1", "transport": "direct", "http_status": 429,
                     "upstream_code": "rate_limit", "upstream_type": "request_error",
                     "upstream_param": "model"}
        for metadata in (populated, dict.fromkeys(populated), {**populated, "http_status": 0}):
            for fallback in (False, True):
                with self.subTest(metadata=metadata, fallback=fallback):
                    payload = {"query": QUERY, "results": [], "text": "No query evidence."}
                    payload.update(metadata, error="Explicit synthesis inference rejected", error_code="http_error")
                    payload.update(raw={"private": PRIVATE_MARKER}, headers={"Authorization": PRIVATE_MARKER},
                                   response={"body": PRIVATE_MARKER}, auth_retries=1, transport_fallbacks=1)
                    entry = self.explicit_synthesis_error_trail(payload, fallback)
                    self.assertEqual(set(entry), {"provider", "error", "error_code", *metadata})
                    for field, value in metadata.items():
                        self.assertEqual(entry[field], value, field)
                        if field == "http_status" and value is not None:
                            self.assertIs(type(entry[field]), int)
                    self.assertNotIn(PRIVATE_MARKER, json.dumps(entry))

    def test_explicit_synthesis_failure_trails_drop_mistyped_and_unlisted_metadata(self):
        safe = {"model": "gpt-5.1", "transport": "proxy", "http_status": 503,
                "upstream_code": "unavailable", "upstream_type": "server_error",
                "upstream_param": None}
        for field in safe:
            invalid = (True, False, "503", 503.0, [], {"private": PRIVATE_MARKER}) if field == "http_status" else (
                True, False, 503, 503.0, [PRIVATE_MARKER], {"private": PRIVATE_MARKER})
            for value in invalid:
                for fallback in (False, True):
                    with self.subTest(field=field, value=value, fallback=fallback):
                        payload = {"query": QUERY, "results": [], "text": "No query evidence."}
                        payload.update(safe, error="Explicit synthesis inference rejected", error_code="http_error")
                        payload[field] = value
                        payload.update(raw=PRIVATE_MARKER, headers={"X-Private": PRIVATE_MARKER},
                                       upstream_response={"body": PRIVATE_MARKER})
                        entry = self.explicit_synthesis_error_trail(payload, fallback)
                        self.assertLessEqual(set(entry), {"provider", "error", "error_code", *safe})
                        self.assertIsNone(entry.get(field), field)
                        for retained, expected in safe.items():
                            if retained != field:
                                self.assertIn(retained, entry)
                                self.assertEqual(entry[retained], expected, retained)
                        for retained in ("model", "transport", "upstream_code", "upstream_type", "upstream_param"):
                            self.assertTrue(entry.get(retained) is None or type(entry[retained]) is str, retained)
                        self.assertTrue(entry.get("http_status") is None or type(entry["http_status"]) is int)
                        self.assertNotIn(PRIVATE_MARKER, json.dumps(entry))

    def test_anthropic_prose_and_raw_empty_keep_existing_success_semantics(self):
        prose = {"query": QUERY, "results": [], "text": "Explicit Anthropic prose.",
                 "model": "claude-fixture", "tool_calls_made": 0,
                 "error": None, "error_code": None}
        result = self.workflow([(prose, 0)], force_provider="anthropic-search")
        self.assertEqual(result["provider"], "anthropic-search")
        self.assertIsNone(result["error"])
        self.assertIsNone(result["error_code"])
        self.assertIs(result["synthesized"], True)
        self.assertEqual(result["text"], "Explicit Anthropic prose.")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["meta"]["model"], "claude-fixture")
        self.assertEqual(result["meta"]["tool_calls_made"], 0)
        self.assertNotIn("search_calls_made", result["meta"])
        for entry in ("brave:web", "tavily:general", "serper:search", "exa:auto", "searxng"):
            with self.subTest(raw=entry):
                payload = provider_reply(entry.split(":", 1)[0])
                payload["results"] = []
                result = self.workflow([(payload, 0)], force_provider=entry)
                self.assertEqual(result["provider"], entry.split(":", 1)[0])
                self.assertEqual(result["fallback_position"], 0)
                self.assertEqual(result["results"], [])
                self.assertNotIn("fallback_errors", result["meta"])
                self.assert_raw(result)



    def test_missing_codex_script_records_skip_before_real_brave_command(self):
        absent = self.home / "absent_codex_search.py"
        with mock.patch.dict(self.router.SCRIPTS, {"codex-search": str(absent)}):
            result = self.workflow([(provider_reply("brave"), 0)])
        self.assertEqual([self.selector(cmd) for cmd in self.commands], ["brave:web"])
        error = result["meta"]["fallback_errors"][0]
        self.assertEqual(error["provider"], "codex-search")
        self.assertIn(str(absent), error["error"])
        self.assert_raw(result)

    def test_disabled_codex_records_skip_without_launching_it(self):
        with mock.patch.object(self.router, "DISABLED_PROVIDERS", {"codex-search"}):
            result = self.workflow([(provider_reply("brave"), 0)])
        self.assertEqual([self.selector(cmd) for cmd in self.commands], ["brave:web"])
        error = result["meta"]["fallback_errors"][0]
        self.assertEqual(error["provider"], "codex-search")
        self.assertEqual(error["error_code"], "disabled")
        self.assertTrue(error["error"])
        self.assert_raw(result)

    def test_all_disabled_general_providers_return_the_complete_skip_trail(self):
        with mock.patch.object(self.router, "DISABLED_PROVIDERS",
                               {"codex-search", "brave", "tavily", "exa", "searxng", "copilot-search"}):
            result = self.workflow([])
        self.assertEqual(result["error_code"], "all_disabled")
        self.assertEqual([error["provider"] for error in result["provider_errors"]],
                         ["codex-search", "brave:web", "tavily:general", "exa:auto", "searxng"])
        self.assertTrue(all(error["error_code"] == "disabled" for error in result["provider_errors"]))
        self.assertEqual(result["results"], [])
        self.assertIs(result["synthesized"], False)


if __name__ == "__main__":
    unittest.main()
