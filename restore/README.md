# Restore

`restore` is an agent-driven read/ground/report procedure, not a Python executable or MCP server. [SKILL.md](SKILL.md) defines actual host instruction discovery, the ordered hot-file working set, cold pointers, parked counts, and truthful unavailable actions.

Installation includes the complete [save helper/reference closure](../save/README.md), including public `recall.py`. A non-v4 root refuses with exit 4; there is no public converter or fallback v3 procedure. Existing saved formats and IDs are preserved, not silently migrated. Routine restore never loads the entire archive or checkpoint history.

Claude's optional name reapplication requires `agent-messaging`'s `relay-name/scripts/set_name.py` and reports a request rather than claiming asynchronous rename success. Missing messaging is an explicit unavailable prerequisite. Codex and OMP do not run the Claude-only naming path.

Use the repository's versioned installer and separate offline Verify. Source tests must use synthetic project roots and fresh harness sessions; copying a front does not prove a real restore was performed. Shared helper tests are documented in [save](../save/README.md).
