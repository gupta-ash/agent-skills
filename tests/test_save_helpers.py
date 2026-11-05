"""Public save helper integration checks, using only synthetic project roots."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "_shared" / "save" / "scripts"


class SaveHelpersTests(unittest.TestCase):
    def run_helper(self, name, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPTS / (name + ".py")), *map(str, args)],
            env=env, capture_output=True, check=False,
        )

    def test_scaffold_and_retrieve_seeded_entry_with_public_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            result = self.run_helper("init_project", "new", "--root", project, "--name", "Example")
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            self.assertIn(b"schema_version: v4", (project / "state.md").read_bytes())
            self.assertTrue((project / "state.md").read_bytes().startswith(b"<!-- ash_save v4 |"))
            result = self.run_helper("recall", "get", "M1", "--root", project)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            self.assertIn(b"State the project goal", result.stdout)
            self.assertIn(b"backlog.md", result.stderr)
            result = self.run_helper("recall", "check", "--root", project)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_non_v4_writers_refuse_without_changing_project(self):
        for declaration in (b"schema_version: v3\n", b"schema_version: unknown\n", b""):
            for helper in ("update_state", "regen_manifest", "demote"):
                with self.subTest(helper=helper, declaration=declaration), tempfile.TemporaryDirectory() as temporary:
                    project = Path(temporary)
                    (project / "state.md").write_bytes(declaration + b"project: synthetic\nsave: 3\n")
                    (project / "neighbor.txt").write_bytes(b"not owned by the helper")
                    before = {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
                    result = self.run_helper(helper, "--root", project)
                    self.assertEqual(result.returncode, 4, result.stderr.decode("utf-8", "replace"))
                    after = {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
                    self.assertEqual(after, before)
                    self.assertNotIn(b"/save_migration", result.stderr)

    def test_removed_writer_override_never_mutates_existing_v3(self):
        for helper in ("update_state", "regen_manifest", "demote"):
            with self.subTest(helper=helper), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                state = b"schema_version: v3\nproject: synthetic\nsave: 3\n"
                (project / "state.md").write_bytes(state)
                (project / "neighbor.txt").write_bytes(b"preserved")
                before = {p.name: p.read_bytes() for p in project.iterdir()}
                result = self.run_helper(helper, "--root", project, "--migrating")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual({p.name: p.read_bytes() for p in project.iterdir()}, before)

    def test_scaffolder_cannot_overwrite_existing_v3(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "state.md").write_bytes(b"schema_version: v3\nproject: synthetic\n")
            before = {p.name: p.read_bytes() for p in project.iterdir()}
            result = self.run_helper("init_project", "new", "--root", project, "--name", "Example")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual({p.name: p.read_bytes() for p in project.iterdir()}, before)

    def test_update_state_python_api_has_no_conversion_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            state = b"schema_version: v3\nproject: synthetic\nsave: 3\n"
            (project / "state.md").write_bytes(state)
            program = (
                "import io,pathlib,sys; sys.path.insert(0,sys.argv[1]); import update_state; "
                "root=pathlib.Path(sys.argv[2]); "
                "assert update_state.regenerate(root,4,'T',io.StringIO()) == 4; "
                "assert update_state.require_migrated(root,'test',_err=io.StringIO()) == 4\n"
                "try: update_state.regenerate(root,4,'T',io.StringIO(),migrating=True)\n"
                "except TypeError: pass\n"
                "else: raise AssertionError('removed conversion override was accepted')\n"
            )
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program,
                                     str(SCRIPTS), str(project)], capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            self.assertEqual((project / "state.md").read_bytes(), state)

    def test_generators_keep_original_v4_wire_watermarks(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            result = self.run_helper("init_project", "new", "--root", project, "--name", "Example")
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            for helper, output in (("update_state", "state.md"), ("regen_manifest", "manifest.md")):
                result = self.run_helper(helper, "--root", project, "--save-num", "1", "--checkpoint-ts", "2000-01-01 10:00:00 UTC")
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
                self.assertTrue((project / output).read_bytes().startswith(b"<!-- ash_save v4 | save#1 |"))


if __name__ == "__main__":
    unittest.main()
