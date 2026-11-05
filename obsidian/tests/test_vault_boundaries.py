"""Real CLI regressions for vault containment and invalid vault roots."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class VaultBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.neighbor = self.root / "vault-neighbor"
        self.vault.mkdir()
        self.neighbor.mkdir()
        self.outside = self.neighbor / "outside.md"
        self.outside.write_text("Outside [[PrivateTarget]]", encoding="utf-8")
        self.note = self.vault / "inside.md"
        self.note.write_text("Inside [[LocalTarget]]", encoding="utf-8")
        self.environment = {
            key: value for key, value in os.environ.items()
            if not (key.startswith("VAULT_") and key.endswith("_LOCATION"))
        }
        self.environment["VAULT_FIXTURE_LOCATION"] = str(self.vault)

    def invoke(self, script, *arguments, vault="Fixture"):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / script), *arguments, "--vault", vault],
            env=self.environment, cwd=self.root, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_outside_reads_are_refused(self):
        for script, command in [("vault_ops.py", "read"), ("vault_search.py", "links-from")]:
            for path in ["../vault-neighbor/outside.md", str(self.outside)]:
                with self.subTest(script=script, path=path):
                    code, data = self.invoke(script, command, path)
                    self.assertEqual(code, 1)
                    self.assertEqual(set(data), {"error"})
                    self.assertNotIn("PrivateTarget", data["error"])

    def test_outside_writes_and_moves_preserve_files(self):
        original = self.outside.read_bytes()
        for arguments in [
            ("write", "../vault-neighbor/outside.md", "--content", "Replaced"),
            ("move", "inside.md", "../vault-neighbor/moved.md"),
        ]:
            with self.subTest(command=arguments[0]):
                code, data = self.invoke("vault_ops.py", *arguments)
                self.assertEqual(code, 1)
                self.assertEqual(set(data), {"error"})
                self.assertEqual(self.outside.read_bytes(), original)
                self.assertEqual(self.note.read_text(encoding="utf-8"), "Inside [[LocalTarget]]")
                self.assertFalse((self.neighbor / "moved.md").exists())

    def test_regular_file_cannot_be_a_search_vault(self):
        code, data = self.invoke("vault_search.py", "text", "Outside", vault=str(self.outside))
        self.assertEqual(code, 1)
        self.assertEqual(set(data), {"error"})

    def test_normalized_in_vault_paths_still_work(self):
        (self.vault / "folder").mkdir()
        path = "folder/../inside.md"
        code, note = self.invoke("vault_ops.py", "read", path)
        self.assertEqual(code, 0)
        self.assertEqual(note["content"], "Inside [[LocalTarget]]")
        code, links = self.invoke("vault_search.py", "links-from", path)
        self.assertEqual(code, 0)
        self.assertEqual(links, {"path": "inside.md", "outbound_links": ["LocalTarget"], "count": 1})

    def test_move_overwrite_requires_explicit_opt_in(self):
        for command in ["move", "move-file"]:
            with self.subTest(command=command):
                source = self.vault / (command + ".md")
                destination = self.vault / (command + "-destination.md")
                source.write_bytes(b"New canonical")
                destination.write_bytes(b"Prior canonical")
                code, data = self.invoke("vault_ops.py", command, source.name, destination.name)
                self.assertEqual(code, 1)
                self.assertEqual(set(data), {"error"})
                self.assertEqual(source.read_bytes(), b"New canonical")
                self.assertEqual(destination.read_bytes(), b"Prior canonical")
                code, data = self.invoke(
                    "vault_ops.py", command, source.name, destination.name,
                    "--overwrite", "--no-wikilink-update",
                )
                self.assertEqual(code, 0)
                self.assertTrue(data["ok"])
                self.assertFalse(source.exists())
                self.assertEqual(destination.read_bytes(), b"New canonical")


if __name__ == "__main__":
    unittest.main()
