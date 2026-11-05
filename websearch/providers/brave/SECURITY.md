# Security Policy -- brave

## Threat model

This skill runs inside an OpenClaw sandbox with `exec` enabled. It makes outbound HTTPS requests to the Brave Search API and (optionally) to URLs returned in search results. It handles API keys as secrets.

### Attack surface

| Vector                   | Mitigation                                                    | Status  |
|--------------------------|---------------------------------------------------------------|---------|
| **SSRF via endpoint**    | `BRAVE_SEARCH_ENDPOINT` is allowlisted to `https://api.search.brave.com/` only. Override requires `--allow-custom-endpoint`. | Done |
| **SSRF via content fetch** | `--content` fetches URLs from Brave results (not arbitrary user input). Scheme restricted to `http`/`https`. DNS-resolved IPs checked against 12 blocked networks via `_is_ssrf_safe()`. IPv4-mapped IPv6 normalized via `_normalize_ip()`. Raw HTML capped at 10MB per URL. | Done |
| **Secret leakage (stdout)** | API keys are never included in JSON output. `--raw` only contains the response body, not request headers. `--raw` is truncated at 100KB. | Done |
| **Secret leakage (stderr)** | Unhandled exceptions are caught at `__main__` and emit stable JSON. Stack traces only print with `--debug`. Even with `--debug`, keys are not in local variables that appear in tracebacks. | Done |
| **Secret leakage (logs)** | No logging framework is used; no `print()` calls outside the final JSON output. | Done |
| **Retry-After DoS**     | `Retry-After` header is capped at 60 seconds (`MAX_RETRY_AFTER_SECONDS`). | Done |
| **Query injection**      | Query is URL-encoded via `urllib.parse.urlencode`. Length capped at 2000 chars. | Done |
| **Prompt injection**     | No model output is executed or used to build shell commands.  | N/A     |
| **Dependency supply chain** | Python stdlib only. Zero third-party packages.             | Done    |
| **Cache poisoning**      | Cache keys are SHA-256 of (endpoint + params + content_opts). Cache files are in a user-controlled directory. | Done |
| **Path traversal (cache)** | Cache keys are hex SHA-256 digests; `Path.resolve()` normalizes paths. | Done |

## SSRF content fetch guards (v2.0)

When `--content` is used to extract text from search result URLs, all target URLs are validated before fetching:

1. **Scheme validation**: Only `http://` and `https://` schemes are allowed. All other schemes (ftp, file, gopher, etc.) are rejected.

2. **DNS resolution + IP blocking**: The hostname is resolved via DNS, and the resulting IP address is checked against `_BLOCKED_NETWORKS` — a list of 12 private/reserved CIDR ranges:

   | Network | Description |
   |---------|-------------|
   | `10.0.0.0/8` | RFC-1918 private (Class A) |
   | `172.16.0.0/12` | RFC-1918 private (Class B) |
   | `192.168.0.0/16` | RFC-1918 private (Class C) |
   | `127.0.0.0/8` | Loopback |
   | `169.254.0.0/16` | Link-local (includes cloud metadata `169.254.169.254`) |
   | `0.0.0.0/8` | Unspecified / "this network" |
   | `100.64.0.0/10` | Carrier-grade NAT (RFC-6598) |
   | `198.18.0.0/15` | Benchmarking (RFC-2544) |
   | `240.0.0.0/4` | Reserved for future use |
   | `::1/128` | IPv6 loopback |
   | `fc00::/7` | IPv6 unique-local |
   | `fe80::/10` | IPv6 link-local |

3. **IPv4-mapped IPv6 normalization**: `_normalize_ip()` detects IPv4-mapped IPv6 addresses (e.g., `::ffff:127.0.0.1`) and extracts the underlying IPv4 address for proper blocking.

4. **HTML size cap**: Raw HTML responses are limited to 10MB per URL to prevent memory exhaustion.

## Secrets handling

- **Storage**: API keys must be in environment variables (`BRAVE_API_KEY` or `BRAVE_API_KEY_1..3`), never in command-line arguments (visible in `ps`).
- **Transport**: Keys are sent only in the `X-Subscription-Token` HTTP header over HTTPS.
- **Output**: Keys never appear in stdout, stderr, or any output field.
- **Debug mode**: `--debug` adds `meta.debug.key_count` (integer), never key values.
- **`.env` file**: Must be in `.gitignore`. Never commit.

## Reporting vulnerabilities

If you discover a security issue, file a private report or contact the project maintainer directly. Do not open a public issue with exploit details.

## Remaining TODOs

| Priority | Item                                                                 | Target  |
|----------|----------------------------------------------------------------------|---------|
| Low      | Consider signing cache files or adding HMAC to prevent tampering     | v2.1.0  |
