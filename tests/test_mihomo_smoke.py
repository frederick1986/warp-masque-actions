"""Optional actual-core parsing; temporary, unregistered ECDSA test keys only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from warp_generator import generate, write_outputs


@unittest.skipUnless(os.environ.get("MIHOMO_BIN") and shutil.which("openssl"),
                     "set MIHOMO_BIN to an existing trusted core; OpenSSL also required")
class MihomoSmokeTests(unittest.TestCase):
    def test_mihomo_parses_native_and_file_provider(self):
        binary = str(Path(os.environ["MIHOMO_BIN"]).resolve())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "test-private.pem"
            public = root / "test-public.pem"
            subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(private)], check=True, capture_output=True)
            subprocess.run(["openssl", "ec", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
            # This key pair has no registered identity. It cannot authenticate to WARP.
            account = {"private_key": private.read_text(), "endpoint_pub_key": public.read_text(),
                       "ipv4": "192.0.2.20", "ipv6": "2001:db8::20"}
            for network in ("quic", "h2"):
                directory = root / network
                generated = generate(account, {"endpoints": ["192.0.2.1", "2001:db8::1"],
                    "ports": [443], "network": network, "ruleset_profile": "minimal",
                    "formats": ["mihomo", "provider"], "ai_health_url": "https://example.com/health",
                    "custom_ip_rules": [{"cidr": "198.51.100.0/24", "target": "WARP"}]})
                write_outputs(generated, directory)
                result = subprocess.run([binary, "-t", "-d", str(directory), "-f", str(directory / "warp-masque.yaml")], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                provider_config = {"proxy-providers": {"warp": {"type": "file", "path": "./warp-masque-provider.yaml"}},
                                   "proxy-groups": [{"name": "WARP", "type": "select", "use": ["warp"]}], "rules": ["MATCH,WARP"]}
                (directory / "provider-client.yaml").write_text(yaml.safe_dump(provider_config))
                result = subprocess.run([binary, "-t", "-d", str(directory), "-f", str(directory / "provider-client.yaml")], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
