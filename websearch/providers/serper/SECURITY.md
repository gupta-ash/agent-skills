# Serper Skill — Security Model

Security analysis for the Serper search skill (`scripts/serper.py`).
Covers API key handling, network boundaries, input validation, code safety,
error handling, and trust context within the OpenClaw agent hierarchy.

---

## 1. API Key Handling

| Property | Detail |
|----------|--------|
| **Resolution order** | `--api-key` CLI argument > `SERPER_API_KEY` environment variable |
| **Transmission** | Sent exclusively in the `X-API-KEY` HTTP header |
| **Stdout/stderr** | Key is **never** emitted to stdout or stderr. Debug messages log the *source* (`"API key source: --api-key argument"`) but never the value. |
| **Disk persistence** | The script performs zero file I/O — the key is never written to disk, cached, or logged to a file. |
| **Error output** | Missing-key errors say `"no API key found"` without echoing any partial key material. |

**Implementation references:**
- `_get_api_key()` (lines 40-50): resolves key, logs source only.
- `_make_request()` (line 93): `req.add_header("X-API-KEY", api_key)` — sole use of key value.

---

## 2. Network Boundary

| Property | Detail |
|----------|--------|
| **Allowed host** | `https://google.serper.dev` only |
| **BASE_URL** | Hardcoded constant (line 22) — not configurable via CLI, env, or config file |
| **URL construction** | `f"{BASE_URL}/{endpoint}"` where `endpoint` is validated to `"search"` or `"news"` |
| **Secondary calls** | None — the script makes exactly one HTTP POST per invocation |
| **Protocol** | HTTPS only (hardcoded scheme in `BASE_URL`) |

This eliminates SSRF risk in v1. The script cannot be redirected to internal hosts,
metadata endpoints (169.254.169.254), or RFC-1918 addresses. Content extraction
in future v3 will require explicit SSRF guards (see SERPER_ROADMAP.md).

---

## 3. Input Validation

| Input | Constraint | Enforcement |
|-------|-----------|-------------|
| `--query` | Required, non-empty, max 2048 characters | `MAX_QUERY_LENGTH = 2048` constant; checked before any network call |
| `--count` | Integer, range 1-100 | `argparse` type=int + explicit bounds check |
| `--type` | Must be `search` or `news` | `argparse choices=["search", "news"]` — rejects all other values at parse time |
| `--timeout` | Integer | `argparse` type=int (default 15) |
| `--page` | Integer or omitted | `argparse` type=int |
| `--gl`, `--hl`, `--tbs` | Free-form strings | Passed through to Serper API payload; Serper validates server-side |

All validation failures emit structured JSON with `error_code: "validation_error"`
and exit code 2 — no network call is made.

---

## 4. Code Safety

| Property | Detail |
|----------|--------|
| **Dependencies** | Pure Python stdlib: `argparse`, `json`, `os`, `re`, `sys`, `time`, `urllib.request`, `urllib.error`, `typing` |
| **No dynamic execution** | Zero use of `eval()`, `exec()`, `compile()`, `__import__()`, or `importlib` |
| **No subprocess** | Zero use of `subprocess`, `os.system()`, `os.popen()`, or `shlex` |
| **No file I/O** | Zero use of `open()`, `pathlib`, `tempfile`, `shutil`, or any filesystem write |
| **No deserialization** | Zero use of `pickle`, `marshal`, `shelve`, or `yaml.load()` |
| **No code generation** | No template engines, no string-to-code paths |

The script is a single-pass pipeline: parse args, validate, POST JSON, format
response, emit JSON, exit. There are no callbacks, hooks, plugins, or
extension points.

---

## 5. Error Handling

| Scenario | Stdout | Stderr | Exit Code |
|----------|--------|--------|-----------|
| Validation failure | Stable JSON with `error_code` | Silent (unless `--debug`) | 2 |
| Missing API key | Stable JSON with `config_error` | Silent (unless `--debug`) | 2 |
| HTTP 401/403 | JSON with `auth_error` | Silent (unless `--debug`) | 1 |
| HTTP 429 | JSON with `rate_limited` | Silent (unless `--debug`) | 1 |
| HTTP 5xx | JSON with `server_error` | Silent (unless `--debug`) | 1 |
| Network failure | JSON with `network_error` | Silent (unless `--debug`) | 1 |
| Parse error | JSON with `parse_error` | Silent (unless `--debug`) | 1 |
| Unhandled exception | JSON with `internal_error` | Traceback only with `--debug` | 1 |

**Security properties of error output:**
- HTTP error response bodies are truncated to **200 characters** (`str(detail)[:200]`)
  to prevent error amplification or oversized payloads.
- Raw Python tracebacks are **never** written to stdout. They appear on stderr
  only when `--debug` is explicitly passed.
- The top-level `except Exception` handler (lines 401-418) guarantees JSON output
  even on catastrophic failures.
- Error messages never contain the API key, request headers, or full response bodies.

---

## 6. Trust Context in OpenClaw

When used within the OpenClaw agent pipeline, the Serper skill operates under
the Scout agent's security constraints:

| Layer | Constraint |
|-------|-----------|
| **Scout network** | `network: bridge` — internet access allowed (required for Serper API calls) |
| **Scout filesystem** | `workspaceAccess: none` — Scout cannot write to the filesystem; the script's lack of file I/O is defense-in-depth |
| **Scout output** | All Scout output is treated as `untrusted` (DATA tier) by Coordinator |
| **Coordinator processing** | Coordinator is air-gapped (`network: none`); it processes Scout search results as raw data, never as instructions |
| **Injection detection** | Scouts flag `INJECTION_FLAG` if prompt injection patterns are detected in fetched content; Coordinator and Main validate before any durable write |
| **API key isolation** | `SERPER_API_KEY` is injected via Docker environment variable into the Scout container; the Scout agent cannot access other agents' credentials |

**Blast-radius analysis:** If a Scout agent is compromised via malicious search
results, the damage is limited to:
- The Scout's own ephemeral container (no persistent filesystem)
- Network calls within the Scout's bridge network
- The Scout cannot write to the workspace, send messages to arbitrary sessions,
  or access other agents' credentials
- Coordinator treats all Scout output as untrusted DATA — never executes it
