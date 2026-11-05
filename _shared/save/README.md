# Shared v4 save helpers

The seven current public helpers are `citemap`, `recall`, `init_project`, `demote`, `update_state`, `regen_manifest`, and `validate_entries`. Author them once under `scripts/`; `deploy/exports.json` explicitly installs their complete sibling closure for save and restore. The two runtime documents under `docs/runtime/` are the global references installed under `.agents/`.

Preserve v4 saved-data identifiers, original `ash_save` watermarks and byte-preserving retrieval/moves. Non-v4 writers refuse with exit 4; the public distribution includes no converter, legacy helper tree, CLI override or API bypass. Fresh initialization remains the collision-guarded `init_project.py new` operation, not mutation of an existing non-v4 project.

All helpers are standard-library Python and support `--selftest`. Run them with `python -S -I -B <script> --selftest` using an owned temporary directory. The root `tests/test_save_helpers.py` checks actual subprocess scaffolding/retrieval and non-v4 no-write refusals. Do not use live projects for development fixtures.
