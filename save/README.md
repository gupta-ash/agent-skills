# Save

The agent-driven `save` front persists a v4 project working set and full cold session record. It is not a service, hook, database, or unattended scheduler. [SKILL.md](SKILL.md) is the authoritative procedure for Claude Code, Codex and OMP.

Author helpers once in [`_shared/save/scripts`](../_shared/save/scripts/); the explicit installer map projects all seven together into `.agents/skills/save/scripts`. Restore depends on the same closure and the two global references, `save_artifacts.md` and `save_helpers.md`. Do not create a second source script tree under this front.

`recall.py` is the public retrieval helper. Saved project formats, `.ash_save`, v4 watermarks, `.ash-id-families`, entry IDs and archived bytes remain unchanged. No project conversion is included: non-v4 writes refuse with exit 4 before mutation. Source privacy renames do not migrate existing projects.

Fresh scaffolding preserves unrelated files and follows the exact collision/adoption rules in the front. Demotion verifies the destination before removing hot bytes; generators preserve their documented manual fields. Always check actual helper exit codes. A partial failure is not a completed save.

Optional Claude naming uses the separately installed `agent-messaging` front `relay-name/scripts/set_name.py`; missing messaging is an explicit prerequisite, not fake success. Codex and OMP truthfully skip the Claude naming substrate.

Use the root versioned installer, not an installed-home copy or source staging controller. Source tests use synthetic roots only: `python -I -B -m unittest discover -s tests -p test_save_helpers.py`; each helper also supports `--selftest`. These commands are verification instructions, not a claim that they passed.
