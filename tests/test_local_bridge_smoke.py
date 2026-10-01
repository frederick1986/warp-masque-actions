"""Optional syntax checking only; no proxy service is started."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from warp_generator import generate, write_outputs


@unittest.skipUnless(os.environ.get("SING_BOX_BIN"), "set SING_BOX_BIN to an existing trusted core")
class LocalBridgeSmokeTests(unittest.TestCase):
    def test_sing_box_parses_both_local_bridge_templates(self):
        binary = str(Path(os.environ["SING_BOX_BIN"]).resolve())
        account = json.loads((ROOT / "tests/fixtures/synthetic-account.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = generate(account, {"formats": ["singbox-local", "vless-local"],
                "bridge": {"uuid": "11111111-1111-4111-8111-111111111111"}})
            write_outputs(generated, root)
            for name in ("sing-box-usque-local.json", "sing-box-vless-local.json"):
                result = subprocess.run([binary, "check", "-c", str(root / name)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
