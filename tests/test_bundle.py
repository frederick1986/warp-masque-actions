"""Synthetic-only tests for three complete WARP/chained configuration exports."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from external_providers import (FREE_AI, FREE_AUTO, FREE_MANUAL, FREE_STREAM, HEALTH,
                                WARP_RELAY, WARP_RELAY_AUTO, country_group_name)
from external_snapshot import SnapshotError, load_snapshot
from generate_bundle import EXTERNAL_CHAIN, WARP_DIRECT, build_bundle, main
from warp_generator import AI, AI_AUTO, AUTO, FALLBACK, MANUAL, SELECT, ConfigError, generate, write_outputs

ACCOUNT = json.loads((ROOT / "tests/fixtures/synthetic-account.json").read_text())
SMALL = {"endpoint_source": "account", "ports": [443], "ruleset_profile": "minimal"}
YAMLS = {"masque.yaml", "usque-custom-pro.yaml", "combined.yaml"}


def snapshot():
    nodes = [{"name": "EXT-US-1", "type": "ss", "server": "192.0.2.50", "port": 443,
              "cipher": "aes-128-gcm", "password": "SYNTHETIC-PASSWORD", "dialer-proxy": "DIRECT"},
             {"name": "EXT-JP-1", "type": "trojan", "server": "192.0.2.51", "port": 443,
              "password": "SYNTHETIC-PASSWORD", "sni": "example.test", "network": "ws",
              "ws-opts": {"path": "/synthetic", "headers": {"Host": "example.test"}}},
             {"name": "EXT-SG-1", "type": "ss", "server": "192.0.2.52", "port": 443,
              "cipher": "aes-128-gcm", "password": "SYNTHETIC-PASSWORD"}]
    counts = [{"country": c, "bytes": 100, "downloaded": 2 if c == "JP" else 1,
               "rejected": 0, "deduplicated": int(c == "JP"), "embedded": 1,
               "usable": 2 if c == "JP" else 1} for c in ("US", "JP", "SG")]
    return nodes, {"country_nodes": {"US": ["EXT-US-1"], "JP": ["EXT-US-1", "EXT-JP-1"], "SG": ["EXT-SG-1"]},
                   "source_counts": counts, "connectivity_tested": False}


def bundle(options=None, with_snapshot=True):
    return build_bundle(ACCOUNT, {**SMALL, **(options or {})}, snapshot() if with_snapshot else None)


def groups(config):
    return {group["name"]: group for group in config["proxy-groups"]}


def resolved(test, config):
    definitions = {item["name"]: item for item in config["proxies"] + config["proxy-groups"]}
    allowed = set(definitions) | {"DIRECT", "REJECT"}
    visited = set()
    def visit(name, ancestors=()):
        test.assertNotIn(name, ancestors)
        if name not in definitions or name in visited:
            return
        item = definitions[name]
        test.assertNotIn("use", item)
        for child in list(item.get("proxies", [])) + ([item["dialer-proxy"]] if "dialer-proxy" in item else []):
            test.assertIn(child, allowed)
            visit(child, ancestors + (name,))
        visited.add(name)
    for name in definitions:
        visit(name)
    for rule in config["rules"]:
        fields = rule.split(",")
        test.assertIn(fields[-2] if fields[-1] == "no-resolve" else fields[-1], allowed)


class BundleTests(unittest.TestCase):
    def test_exact_three_independent_inline_configs(self):
        outputs = bundle()
        self.assertEqual(set(outputs), YAMLS | {"manifest.json"})
        for name in YAMLS:
            config = yaml.safe_load(outputs[name])
            for field in ("proxies", "proxy-groups", "rules", "dns"):
                self.assertTrue(config[field])
            self.assertEqual(config["mixed-port"], 7890)
            self.assertFalse(config["allow-lan"])
            self.assertEqual(config["bind-address"], "127.0.0.1")
            self.assertNotIn("proxy-providers", config)
            self.assertNotIn("Au1rxx/free-vpn-subscriptions", outputs[name])
            self.assertNotIn("Generated offline", outputs[name])
            resolved(self, config)

    def test_pure_warp_has_no_external_nodes_or_groups(self):
        config = yaml.safe_load(bundle()["masque.yaml"])
        self.assertTrue(all(node["type"] == "masque" for node in config["proxies"]))
        for name in (FREE_AUTO, FREE_AI, FREE_STREAM, FREE_MANUAL, WARP_RELAY, WARP_RELAY_AUTO,
                     WARP_DIRECT, EXTERNAL_CHAIN, country_group_name("US")):
            self.assertNotIn(name, groups(config))
        self.assertTrue(all("dialer-proxy" not in node for node in config["proxies"]))
        self.assertNotIn("SYNTHETIC-PASSWORD", yaml.safe_dump(config))
        self.assertIn(f"DOMAIN-SUFFIX,chatgpt.com,{AI}", config["rules"])

    def test_donor_and_combined_have_distinct_real_exit_choices(self):
        outputs = bundle()
        combined, donor = (yaml.safe_load(outputs[n]) for n in ("combined.yaml", "usque-custom-pro.yaml"))
        self.assertNotEqual(outputs["combined.yaml"], outputs["usque-custom-pro.yaml"])
        self.assertEqual(groups(combined)[SELECT]["proxies"], [WARP_DIRECT, EXTERNAL_CHAIN, "DIRECT"])
        self.assertEqual(groups(combined)[WARP_DIRECT]["proxies"], [AUTO, FALLBACK, MANUAL])
        self.assertEqual(groups(donor)[SELECT]["proxies"], [EXTERNAL_CHAIN])
        for name in (WARP_DIRECT, AUTO, FALLBACK, MANUAL, AI_AUTO):
            self.assertNotIn(name, groups(donor))
        for name in (WARP_RELAY, WARP_RELAY_AUTO):
            self.assertTrue(groups(donor)[name]["hidden"])
        warp = {node["name"] for node in donor["proxies"] if node["type"] == "masque"}
        for group in donor["proxy-groups"]:
            if group["name"] not in (WARP_RELAY, WARP_RELAY_AUTO):
                self.assertFalse(warp.intersection(group["proxies"]))

    def test_external_nodes_force_warp_only_chains_with_legacy_false_setting(self):
        for enabled in (False, True):
            outputs = bundle({"external_providers": {"enabled": True, "use_warp": enabled}})
            for name in ("combined.yaml", "usque-custom-pro.yaml"):
                config = yaml.safe_load(outputs[name])
                warp = [node["name"] for node in config["proxies"] if node["type"] == "masque"]
                self.assertEqual(groups(config)[WARP_RELAY_AUTO]["proxies"], warp)
                self.assertEqual(groups(config)[WARP_RELAY]["proxies"], [WARP_RELAY_AUTO] + warp)
                self.assertTrue(all(n["dialer-proxy"] == WARP_RELAY for n in config["proxies"] if n["type"] != "masque"))
                resolved(self, config)

    def test_no_snapshot_or_external_disabled_yields_pure_warp_only(self):
        outputs = bundle(with_snapshot=False)
        disabled = bundle({"external_providers": {"enabled": False}})
        self.assertEqual(set(outputs), {"masque.yaml", "manifest.json"})
        self.assertEqual(set(disabled), set(outputs))
        self.assertEqual(disabled["masque.yaml"], outputs["masque.yaml"])
        manifest = json.loads(disabled["manifest.json"])
        self.assertEqual(manifest["external_node_count"], 0)
        self.assertFalse(manifest["external_nodes_materialized"])

    def test_account_is_required(self):
        for account in (None, [], {}, "DO-NOT-ECHO"):
            with self.subTest(account=type(account)), self.assertRaises(ConfigError):
                build_bundle(account, SMALL, snapshot())

    def test_renderer_is_pure_deterministic_and_has_no_network(self):
        account, options, source = copy.deepcopy(ACCOUNT), copy.deepcopy(SMALL), snapshot()
        before = copy.deepcopy((account, options, source))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
             patch("generate_bundle.load_snapshot", side_effect=AssertionError("network forbidden")):
            self.assertEqual(build_bundle(account, options, source), build_bundle(account, options, source))
        self.assertEqual((account, options, source), before)

    def test_donor_chain_overwritten_and_protocol_options_preserved(self):
        source = snapshot()
        source[0][0]["dialer-proxy"] = FREE_AUTO
        outputs = build_bundle(ACCOUNT, SMALL, source)
        for name in ("combined.yaml", "usque-custom-pro.yaml"):
            nodes = {node["name"]: node for node in yaml.safe_load(outputs[name])["proxies"]}
            for original in source[0]:
                self.assertEqual(nodes[original["name"]], {**original, "dialer-proxy": WARP_RELAY})
        self.assertEqual(source[0][0]["dialer-proxy"], FREE_AUTO)

    def test_country_and_health_groups_preserve_inline_membership(self):
        for name, text in bundle().items():
            if name not in ("combined.yaml", "usque-custom-pro.yaml"):
                continue
            g = groups(yaml.safe_load(text))
            expected = ["EXT-US-1", "EXT-JP-1", "EXT-SG-1"]
            for group, kind in ((FREE_AUTO, "general"), (FREE_AI, "ai"), (FREE_STREAM, "stream")):
                self.assertEqual(g[group]["proxies"], expected)
                self.assertEqual(g[group]["url"], HEALTH[kind][0])
                self.assertEqual(g[group]["expected-status"], HEALTH[kind][1])
            self.assertEqual(g[country_group_name("JP")]["proxies"], ["EXT-US-1", "EXT-JP-1"])
            self.assertEqual(g[FREE_MANUAL]["proxies"], [FREE_AI, FREE_STREAM, FREE_AUTO] + expected)

    def test_external_route_mappings_and_secret_free_disclosure(self):
        outputs = bundle({"chatgpt_route": "JP", "other_ai_route": "FREE", "custom_ip_rules": [
            {"cidr": "198.51.100.9/24", "target": "US"}, {"cidr": "2001:db8:1234::/48", "target": "FREE"},
            {"cidr": "203.0.113.0/24", "target": "DIRECT"}]})
        pure = yaml.safe_load(outputs["masque.yaml"])
        self.assertEqual(pure["rules"][:3], [f"IP-CIDR,198.51.100.0/24,{AUTO},no-resolve",
            f"IP-CIDR6,2001:db8:1234::/48,{AUTO},no-resolve", "IP-CIDR,203.0.113.0/24,DIRECT,no-resolve"])
        self.assertIn(f"DOMAIN-SUFFIX,chatgpt.com,{AUTO}", pure["rules"])
        for name in ("combined.yaml", "usque-custom-pro.yaml"):
            rules = yaml.safe_load(outputs[name])["rules"]
            self.assertIn(f"DOMAIN-SUFFIX,chatgpt.com,{country_group_name('JP')}", rules)
            self.assertEqual(rules[0], f"IP-CIDR,198.51.100.0/24,{country_group_name('US')},no-resolve")
            self.assertEqual(rules[1], f"IP-CIDR6,2001:db8:1234::/48,{FREE_AUTO},no-resolve")
        mappings = json.loads(outputs["manifest.json"])["pure_warp_route_mappings"]
        self.assertEqual(mappings, [{"field": "chatgpt_route", "from": "JP", "to": "WARP"},
            {"field": "other_ai_route", "from": "FREE", "to": "WARP"},
            {"field": "custom_ip_rules", "from": "US", "to": "WARP"},
            {"field": "custom_ip_rules", "from": "FREE", "to": "WARP"}])
        for value in ("198.51.100", "2001:db8", "203.0.113"):
            self.assertNotIn(value, outputs["manifest.json"])

    def test_donor_warp_routes_map_to_chain_with_acl_rules(self):
        outputs = bundle({"ruleset_profile": "acl4ssr", "chatgpt_route": "WARP",
                          "ai_health_url": "https://example.test/health",
                          "custom_ip_rules": [{"cidr": "198.51.100.0/24", "target": "WARP"}]})
        for name in YAMLS:
            config = yaml.safe_load(outputs[name])
            self.assertIn("rule-providers", config)
            self.assertIn("🌍 国外媒体", groups(config))
            resolved(self, config)
        donor = yaml.safe_load(outputs["usque-custom-pro.yaml"])
        self.assertIn(f"DOMAIN-SUFFIX,chatgpt.com,{EXTERNAL_CHAIN}", donor["rules"])
        self.assertEqual(donor["rules"][0], f"IP-CIDR,198.51.100.0/24,{EXTERNAL_CHAIN},no-resolve")

    def test_legacy_provider_api_stays_unchanged(self):
        outputs = generate(ACCOUNT, {**SMALL, "external_providers": {"enabled": True}})
        self.assertIn("external-direct.yaml", outputs)
        self.assertIn("warp-masque-provider.yaml", outputs)
        self.assertIn("proxy-providers", yaml.safe_load(outputs["external-direct.yaml"]))

    def test_manifest_safe_counts_and_publisher_compatibility(self):
        from publish_outputs import validate_outputs
        source = snapshot()
        source[1]["DO-NOT-ECHO"] = ACCOUNT["private_key"]
        source[1]["source_counts"][0]["url"] = "https://example.test/DO-NOT-ECHO"
        outputs = build_bundle(ACCOUNT, SMALL, source)
        manifest = json.loads(outputs["manifest.json"])
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(set(manifest["files"]), YAMLS)
        self.assertEqual(manifest["source_totals"], {"bytes": 300, "downloaded": 4, "rejected": 0,
                                                   "deduplicated": 1, "embedded": 3, "usable": 3})
        self.assertFalse(manifest["connectivity_tested"])
        self.assertFalse(manifest["country_locations_verified"])
        self.assertEqual(manifest["registered_accounts"], 0)
        for value in (ACCOUNT["private_key"], ACCOUNT["endpoint_pub_key"], "SYNTHETIC-PASSWORD", "DO-NOT-ECHO"):
            self.assertNotIn(value, outputs["manifest.json"])
        with tempfile.TemporaryDirectory() as temporary:
            write_outputs(outputs, temporary)
            self.assertEqual(set(validate_outputs(temporary)), set(outputs))

    def test_invalid_snapshots_fail_safely(self):
        invalid = [([], {}), ([{}], {}), (snapshot()[0], {}), "DO-NOT-ECHO"]
        for mutate in (lambda s: s[0][0].update(type="masque"), lambda s: s[0][0].update(name="DO-NOT-ECHO"),
                       lambda s: s[0][0].update(port=True), lambda s: s[0].append(copy.deepcopy(s[0][0])),
                       lambda s: s[1]["country_nodes"].update(US=[]),
                       lambda s: s[1]["country_nodes"].update(US=["DO-NOT-ECHO"]),
                       lambda s: s[1]["source_counts"][0].update(downloaded="DO-NOT-ECHO")):
            source = snapshot()
            mutate(source)
            invalid.append(source)
        for source in invalid:
            with self.subTest(source=type(source)), self.assertRaises(ConfigError) as raised:
                build_bundle(ACCOUNT, SMALL, source)
            self.assertNotIn("DO-NOT-ECHO", str(raised.exception))

    def test_snapshot_integration_uses_synthetic_feeds_only(self):
        feed = yaml.safe_dump({"proxies": [{"name": "ignored", "type": "ss", "server": "192.0.2.80",
            "port": 443, "cipher": "aes-128-gcm", "password": "synthetic-only"}]})
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            source = load_snapshot(["US", "JP", "SG"], fetcher=lambda url: feed.encode())
            outputs = build_bundle(ACCOUNT, SMALL, source)
        config = yaml.safe_load(outputs["combined.yaml"])
        external = [node for node in config["proxies"] if node["type"] != "masque"]
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0]["dialer-proxy"], WARP_RELAY)
        self.assertEqual(groups(config)[country_group_name("SG")]["proxies"], ["EXT-US-1"])


class BundleCliTests(unittest.TestCase):
    def invoke(self, args, account=ACCOUNT, options=SMALL):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = root / "settings.json"
            settings.write_text(json.dumps(options))
            output = root / "outputs"
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"SYNTHETIC_ACCOUNT_JSON": json.dumps(account)}), \
                 patch("generate_bundle.load_snapshot", return_value=snapshot()) as download, \
                 patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                result = main(["--account-env", "SYNTHETIC_ACCOUNT_JSON", "--settings", str(settings),
                               "--output", str(output)] + args)
            files = {p.name: p.read_text() for p in output.iterdir()} if output.exists() else {}
            return result, files, stdout.getvalue(), stderr.getvalue(), download

    def test_default_cli_fetches_and_writes_exact_bundle(self):
        result, files, stdout, stderr, download = self.invoke([])
        self.assertEqual(result, 0, stderr)
        self.assertEqual(set(files), YAMLS | {"manifest.json"})
        download.assert_called_once_with(["US", "JP", "SG"], protocol_mode="stable")
        self.assertIn("3 external nodes", stdout)
        self.assertNotIn(ACCOUNT["private_key"], stdout)
        self.assertNotIn("SYNTHETIC-PASSWORD", stdout)

    def test_no_external_skips_network_and_maps_external_routes(self):
        for options in (SMALL, {**SMALL, "chatgpt_route": "FREE", "other_ai_route": "JP"}):
            result, files, _, stderr, download = self.invoke(["--no-external"], options=options)
            self.assertEqual(result, 0, stderr)
            download.assert_not_called()
            self.assertEqual(set(files), {"masque.yaml", "manifest.json"})

    def test_disabled_external_setting_skips_network(self):
        result, files, _, stderr, download = self.invoke([], options={**SMALL, "external_providers": {"enabled": False}})
        self.assertEqual(result, 0, stderr)
        download.assert_not_called()
        self.assertEqual(set(files), {"masque.yaml", "manifest.json"})

    def test_invalid_inputs_fail_before_any_download(self):
        cases = [({**ACCOUNT, "private_key": "DO-NOT-ECHO!!!"}, SMALL), (None, SMALL), ({}, SMALL),
                 (ACCOUNT, []), (ACCOUNT, {**SMALL, "endpoints": ["https://DO-NOT-ECHO.example"]}),
                 (ACCOUNT, {**SMALL, "max_nodes": 1}),
                 (ACCOUNT, {**SMALL, "external_providers": {"countries": []}})]
        for account, options in cases:
            with self.subTest(account=type(account), options=type(options)):
                result, files, _, stderr, download = self.invoke([], account=account, options=options)
                self.assertEqual(result, 2)
                self.assertEqual(files, {})
                download.assert_not_called()
                self.assertNotIn("DO-NOT-ECHO", stderr)

    def test_source_failures_safe_and_no_partial_exports(self):
        for error, expected in ((RuntimeError("DO-NOT-ECHO"), "external snapshot download or validation failed"),
                                (SnapshotError("US feed contains no usable nodes"), "US feed contains no usable nodes")):
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "outputs"
                stderr = io.StringIO()
                with patch.dict(os.environ, {"SYNTHETIC_ACCOUNT_JSON": json.dumps(ACCOUNT)}), \
                     patch("generate_bundle.load_snapshot", side_effect=error), patch("sys.stderr", stderr):
                    result = main(["--account-env", "SYNTHETIC_ACCOUNT_JSON", "--output", str(output)])
                self.assertEqual(result, 2)
                self.assertFalse(output.exists())
                self.assertIn(expected, stderr.getvalue())
                self.assertNotIn("DO-NOT-ECHO", stderr.getvalue())

    def test_existing_account_file_unmodified_and_private_output_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            account = root / "account.json"
            account.write_text(json.dumps(ACCOUNT))
            before = account.read_bytes()
            with patch("generate_bundle.load_snapshot", side_effect=AssertionError("network forbidden")), patch("sys.stdout", io.StringIO()):
                result = main(["--account", str(account), "--no-external", "--output", str(root / "outputs")])
            self.assertEqual(result, 0)
            self.assertEqual(account.read_bytes(), before)
            for file in (root / "outputs").iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_account_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            account = Path(temporary) / "masque.yaml"
            account.write_text(json.dumps(ACCOUNT))
            before = account.read_bytes()
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(main(["--account", str(account), "--no-external", "--output", temporary]), 2)
            self.assertEqual(account.read_bytes(), before)

    def test_account_required_and_no_external_only_command(self):
        for args in ([], ["--external-only"]):
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(args)
            self.assertEqual(raised.exception.code, 2)


@unittest.skipUnless(os.environ.get("MIHOMO_BIN") and shutil.which("openssl"), "trusted MIHOMO_BIN and OpenSSL required")
class BundleCoreSmokeTests(unittest.TestCase):
    def test_real_core_parses_three_synthetic_configs(self):
        binary = str(Path(os.environ["MIHOMO_BIN"]).resolve())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private, public = root / "synthetic-private.pem", root / "synthetic-public.pem"
            subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(private)],
                           check=True, capture_output=True)
            subprocess.run(["openssl", "ec", "-in", str(private), "-pubout", "-out", str(public)],
                           check=True, capture_output=True)
            account = {"private_key": private.read_text(), "endpoint_pub_key": public.read_text(), "ipv4": "192.0.2.20"}
            outputs = build_bundle(account, {"endpoints": ["192.0.2.1"], "ports": [443],
                "ruleset_profile": "minimal", "chatgpt_route": "US",
                "custom_ip_rules": [{"cidr": "198.51.100.0/24", "target": "FREE"}]}, snapshot())
            for name in YAMLS:
                path = root / name
                path.write_text(outputs[name])
                # -t parses only. These generated keys have no registered account.
                result = subprocess.run([binary, "-t", "-d", str(root), "-f", str(path)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

