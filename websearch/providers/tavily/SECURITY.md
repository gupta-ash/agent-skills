# Security Policy -- tavily

## Threat model

This skill runs inside an OpenClaw sandbox with `exec` enabled. It makes outbound HTTPS requests to the Tavily REST API (`api.tavily.com`) and, via the Extract endpoint, processes user-provided URLs. It handles API keys as secrets.

### Attack surface

| Vector | Mitigation | Status |
|--------|------------|--------|
| **SSRF via Extract URLs** | All user-provided URLs are DNS-resolved and checked against 12 blocked CIDR networks via `_is_ssrf_safe()`. IPv4-mapped IPv6 normalized via `_normalize_ip()`. Only `http://` and `https://` schemes allowed. | Done |
| **SSRF via Search endpoint** | Search queries are strings, not URLs — no SSRF vector. API endpoint is hardcoded to `https://api.tavily.com`. | N/A |
| **Secret leakage (stdout)** | API key is never included in JSON output. `--raw` only contains the response body, not request headers. | Done |
| **Secret leakage (stderr)** | Unhandled exceptions are caught at `__main__` and emit stable JSON. Stack traces only print with `--debug`. Even with `--debug`, keys are not in local variables that appear in tracebacks. | Done |
| **Secret leakage (logs)** | No logging framework is used; no `print()` calls outside the final JSON output. | Done |
| **Query injection** | Query is sent as JSON body field, not URL-interpolated. Length capped at 400 chars. | Done |
| **Prompt injection** | No model output is executed or used to build shell commands. | N/A |
| **Dependency supply chain** | Python stdlib only. Zero third-party packages. | Done |

## SSRF Extract guards (v1.0.0)

When `--type extract` is used, all user-provided URLs are validated before being sent to the Tavily Extract API:

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

4. **Blocked URL handling**: URLs that fail SSRF validation are excluded from the API call and reported in `failed_results` with error `"blocked_by_ssrf_guard"`.

## Secrets handling

- **Storage**: API key must be in the `TAVILY_API_KEY` environment variable, never in command-line arguments (visible in `ps`).
- **Transport**: Key is sent only in the `Authorization: Bearer` HTTP header over HTTPS.
- **Output**: Key never appears in stdout, stderr, or any output field.
- **Debug mode**: `--debug` adds request body (minus query) to `meta.debug`; never key values.
- **`.env` file**: Must be in `.gitignore`. Never commit.

## Reporting vulnerabilities

If you discover a security issue, file a private report or contact the project maintainer directly. Do not open a public issue with exploit details.
