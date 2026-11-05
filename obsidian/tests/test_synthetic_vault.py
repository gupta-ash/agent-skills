"""Exercise only disposable vault files, never configured user vaults."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class SyntheticVaultTests(unittest.TestCase):
    def test_write_read_and_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "vault"
            vault.mkdir()
            environment = {
                key: value for key, value in os.environ.items()
                if not (key.startswith("VAULT_") and key.endswith("_LOCATION"))
            }
            environment["VAULT_FIXTURE_LOCATION"] = str(vault)

            def invoke(script, *arguments):
                result = subprocess.run(
                    [sys.executable, "-B", str(SCRIPTS / script),
                     *arguments, "--vault", "Fixture"],
                    env=environment, cwd=temporary, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, encoding="utf-8", timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)

            invoke("vault_ops.py", "write", "fixture.md", "--content", "Synthetic needle #fixture")
            note = invoke("vault_ops.py", "read", "fixture.md")
            self.assertIn("Synthetic needle", note["content"])
            matches = invoke("vault_search.py", "text", "needle")
            self.assertIn("fixture.md", json.dumps(matches))


if __name__ == "__main__":
    unittest.main()
