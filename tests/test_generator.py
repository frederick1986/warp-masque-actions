"""Offline regression tests use deliberately invalid, nonfunctional fixture keys."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from generate import main
from gen_masque import build
from warp_generator import (AI, AI_AUTO, AUTO, DIRECT, FALLBACK, FINAL, ConfigError, generate, generate_external,
                            write_outputs)

ACCOUNT = json.loads((ROOT / "tests/fixtures/synthetic-account.json").read_text())
SMALL = {"endpoint_source": "account", "ports": [443], "ruleset_profile": "minimal"}


def output(options=None, account=None):
    return generate(ACCOUNT if account is None else account, {**SMALL, **(options or {})})


def full(options=None, account=None):
    return yaml.safe_load(output(options, account)["warp-masque.yaml"])


class GeneratorTests(unittest.TestCase):
    def test_fixture_is_not_real_der_or_account(self):
        self.assertTrue(base64.b64decode(ACCOUNT["private_key"]).startswith(b"TEST_ONLY_"))
        self.assertEqual(ACCOUNT["endpoint_v4"], "192.0.2.1")

    def test_defaults_deduplicate_official_alias(self):
        generated = generate(ACCOUNT)
        self.assertEqual(json.loads(generated["manifest.json"])["node_count"], 56)
        self.assertEqual(len(yaml.safe_load(generated["warp-masque-provider.yaml"])["proxies"]), 56)
        self.assertEqual(yaml.safe_load(generated["warp-masque-provider.yaml"])["proxies"][0]["sni"],
                         "consumer-masque.cloudflareclient.com")

    def test_generation_has_no_network(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            self.assertEqual(len(output()), 4)

    def test_account_is_unchanged_and_tokens_never_exported(self):
        before = copy.deepcopy(ACCOUNT)
        generated = output()
        self.assertEqual(ACCOUNT, before)
        self.assertNotIn("TEST-ONLY-DO-NOT-EXPORT", "".join(generated.values()))
        self.assertNotIn("usque-config.json", generated)

    def test_mihomo_provider_and_links_match(self):
        generated = output()
        nodes = yaml.safe_load(generated["warp-masque-provider.yaml"])["proxies"]
        self.assertEqual(nodes, yaml.safe_load(generated["warp-masque.yaml"])["proxies"])
        links = generated["warp-masque-shadowrocket.txt"].splitlines()
        self.assertEqual(len(links), len(nodes))
        for node, link in zip(nodes, links):
            parsed = urllib.parse.urlsplit(link)
            self.assertEqual(parsed.hostname, node["server"])
            self.assertEqual(parsed.port, node["port"])
            self.assertEqual(urllib.parse.parse_qs(parsed.query)["privateKey"], [node["private-key"]])

    def test_custom_endpoints_are_validated_and_deduplicated(self):
        nodes = full({"endpoints": ["[2001:db8::1]", "2001:0db8::1", "EXAMPLE.COM"], "ports": [443, 443, 8443]})["proxies"]
        self.assertEqual(len(nodes), 4)
        self.assertEqual(nodes[-1]["server"], "example.com")

    def test_family_filter_pins_hostname_resolution(self):
        nodes = full({"endpoints": ["192.0.2.1", "2001:db8::1", "example.com"], "family": "ipv6"})["proxies"]
        self.assertEqual([n["server"] for n in nodes], ["2001:db8::1", "example.com"])
        self.assertTrue(all(n["ip-version"] == "ipv6" for n in nodes))

    def test_empty_family_and_too_many_nodes_fail(self):
        for options in ({"endpoints": ["192.0.2.1"], "family": "ipv6"}, {"max_nodes": 1}):
            with self.subTest(options=options), self.assertRaises(ConfigError):
                output(options)

    def test_sni_dns_mtu_reach_native_nodes(self):
        config = full({"dns": ["9.9.9.9"], "mtu": 1400, "sni": "masque.example.com"})
        for node in config["proxies"]:
            self.assertEqual(node["dns"], ["9.9.9.9"])
            self.assertEqual(node["mtu"], 1400)
            self.assertEqual(node["sni"], "masque.example.com")
        self.assertEqual(config["dns"]["nameserver"], ["9.9.9.9"])

    def test_no_remote_dns_omits_node_resolvers(self):
        self.assertNotIn("dns", full({"remote_dns": False})["proxies"][0])

    def test_pem_is_normalized(self):
        account = {**ACCOUNT, "private_key": "-----BEGIN EC PRIVATE KEY-----\n" + ACCOUNT["private_key"] + "\n-----END EC PRIVATE KEY-----"}
        self.assertEqual(full(account=account)["proxies"][0]["private-key"], ACCOUNT["private_key"])

    def test_missing_and_malformed_account_fields_fail_without_echo(self):
        for field, value in (("private_key", "SECRET!"), ("endpoint_pub_key", ""), ("ipv4", "SECRET!"), ("ipv6", "1.2.3.4")):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError) as error:
                    output(account={**ACCOUNT, field: value})
                self.assertNotIn("SECRET!", str(error.exception))

    def test_ipv4_only_and_ipv6_only_accounts(self):
        self.assertNotIn("ipv6", full(account={**ACCOUNT, "ipv6": ""})["proxies"][0])
        only6 = full({"formats": ["mihomo", "provider"]}, {**ACCOUNT, "ipv4": ""})
        self.assertNotIn("ip", only6["proxies"][0])
        with self.assertRaises(ConfigError):
            output(account={**ACCOUNT, "ipv4": ""})

    def test_h2_uses_account_h2_addresses(self):
        nodes = full({"network": "h2", "formats": ["mihomo", "provider"]})["proxies"]
        self.assertEqual(nodes[0]["server"], "192.0.2.2")
        self.assertEqual(nodes[0]["network"], "h2")
        for options in ({"network": "h2"}, {"network": "h2", "endpoint_source": "curated", "formats": ["mihomo"]}):
            with self.assertRaises(ConfigError):
                output(options)

    def test_settings_reject_bad_types_ranges_and_injection(self):
        bad = {"ports": [True], "mtu": 1200, "max_nodes": 0, "dns": ["https://dns.example"],
               "sni": "ok.example\nprivate-key: x", "endpoints": ["https://example.com"],
               "remote_dns": "false", "formats": ["bogus"], "health_url": "file:///tmp/x",
               "health_interval": 1, "chatgpt_route": "US", "unknown": 1, "family": "bogus"}
        for field, value in bad.items():
            with self.subTest(field=field), self.assertRaises(ConfigError):
                output({field: value})
        for endpoint in ("999.1.1.1", "192.0.2.1:443", "bad name", "fe80::1%eth0"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ConfigError):
                output({"endpoints": [endpoint]})

    def test_custom_cidrs_are_emitted_first_and_normalized(self):
        rules = full({"custom_ip_rules": [{"cidr": "192.0.2.9/24", "target": "DIRECT"}, {"cidr": "2001:db8::7", "target": "WARP"}]})["rules"]
        self.assertEqual(rules[:2], ["IP-CIDR,192.0.2.0/24,DIRECT,no-resolve", f"IP-CIDR6,2001:db8::7/128,{AUTO},no-resolve"])

    def test_invalid_cidr_or_unsupported_country_rejected(self):
        for item in ({"cidr": "999.1.1.1/24", "target": "DIRECT"}, {"cidr": "2001:db8::/129", "target": "DIRECT"}, {"cidr": "192.0.2.0/24", "target": "US"}):
            with self.assertRaises(ConfigError):
                output({"custom_ip_rules": [item]})

    def test_ai_route_controls_reach_actual_rules(self):
        config = full({"chatgpt_route": "DIRECT", "other_ai_route": "WARP", "ruleset_profile": "acl4ssr"})
        self.assertIn("DOMAIN-SUFFIX,chatgpt.com,DIRECT", config["rules"])
        self.assertIn(f"DOMAIN-SUFFIX,claude.ai,{AUTO}", config["rules"])
        self.assertTrue(any(r.startswith("RULE-SET,") and r.endswith(AUTO) for r in config["rules"]))
        self.assertLess(config["rules"].index("DOMAIN-SUFFIX,chatgpt.com,DIRECT"), next(i for i, r in enumerate(config["rules"]) if r.startswith("RULE-SET,")))

    def test_ai_health_group_is_real_and_selected_first(self):
        config = full({"ai_health_url": "https://example.com/health"})
        groups = {g["name"]: g for g in config["proxy-groups"]}
        self.assertEqual(groups[AI]["proxies"][0], AI_AUTO)
        self.assertEqual(groups[AI_AUTO]["url"], "https://example.com/health")
        self.assertEqual(groups[AI_AUTO]["type"], "url-test")

    def test_acl_profile_preserves_primary_routing_defaults(self):
        config = full({"ruleset_profile": "acl4ssr"})
        groups = {g["name"]: g for g in config["proxy-groups"]}
        for name in ("Ⓜ️ 微软服务", "🍎 苹果服务"):
            self.assertEqual(groups[name]["proxies"][0], DIRECT)
        self.assertEqual(groups[FALLBACK]["type"], "fallback")
        self.assertIn(f"GEOIP,CN,{DIRECT}", config["rules"])
        self.assertEqual(config["rules"][-1], f"MATCH,{FINAL}")

    def test_all_rule_and_group_references_exist(self):
        for profile in ("minimal", "acl4ssr"):
            config = full({"ruleset_profile": profile})
            names = {n["name"] for n in config["proxies"] + config["proxy-groups"]} | {"DIRECT", "REJECT"}
            for group in config["proxy-groups"]:
                self.assertTrue(set(group["proxies"]) <= names)
                self.assertNotIn(group["name"], group["proxies"])
            for rule in config["rules"]:
                fields = rule.split(",")
                target = fields[-2] if fields[-1] == "no-resolve" else fields[-1]
                self.assertIn(target, names)
                if fields[0] == "RULE-SET":
                    self.assertIn(fields[1], config["rule-providers"])

    def test_minimal_does_not_depend_on_rule_databases_or_urls(self):
        config = full()
        self.assertNotIn("rule-providers", config)
        self.assertFalse(any(rule.startswith(("GEOIP,", "GEOSITE,")) for rule in config["rules"]))

    def test_exports_are_deterministic_and_defaults_not_mutated(self):
        first = output()
        output({"custom_ip_rules": [{"cidr": "192.0.2.0/24", "target": "REJECT"}]})
        self.assertEqual(first, output())

    def test_bridge_exports_are_loopback_only_and_need_external_usque(self):
        generated = output({"formats": ["singbox-local", "vless-local"], "bridge": {"uuid": "11111111-1111-4111-8111-111111111111"}})
        for name in ("sing-box-usque-local.json", "sing-box-vless-local.json"):
            config = json.loads(generated[name])
            self.assertEqual(config["inbounds"][0]["listen"], "127.0.0.1")
            self.assertEqual(config["outbounds"][0]["server"], "127.0.0.1")
            self.assertEqual(config["outbounds"][0]["type"], "socks")
            self.assertNotIn(ACCOUNT["private_key"], generated[name])
        with self.assertRaises(ConfigError):
            output({"formats": ["vless-local"]})
        with self.assertRaises(ConfigError):
            output({"formats": ["singbox-local"], "bridge": {"mixed_port": 1080}})

    def test_combined_external_exports_and_target_rules_are_connected(self):
        generated = output({"external_providers": {"enabled": True, "countries": ["US", "JP"]},
                            "chatgpt_route": "FREE", "custom_ip_rules": [{"cidr": "192.0.2.0/24", "target": "US"}]})
        config = yaml.safe_load(generated["warp-masque.yaml"])
        self.assertEqual(len(config["proxy-providers"]), 6)
        self.assertEqual(config["rules"][0], "IP-CIDR,192.0.2.0/24,🇺🇸 US,no-resolve")
        self.assertIn("DOMAIN-SUFFIX,chatgpt.com,⚡ 免费落地自动", config["rules"])
        direct = yaml.safe_load(generated["external-direct.yaml"])
        self.assertEqual(direct["proxies"], [])
        self.assertNotIn(ACCOUNT["private_key"], generated["external-direct.yaml"])
        self.assertTrue(all(p["override"]["dialer-proxy"] == "DIRECT" for p in direct["proxy-providers"].values()))
        manifest = json.loads(generated["manifest.json"])
        self.assertEqual(manifest["external_provider_count"], 6)
        self.assertFalse(manifest["external_nodes_materialized"])

    def test_external_only_needs_no_account_and_declares_remote_sources(self):
        generated = generate_external({"external_providers": {"enabled": True}})
        self.assertEqual(set(generated), {"external-direct.yaml", "manifest.json"})
        self.assertFalse(json.loads(generated["manifest.json"])["account_reused"])
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(main(["--external-only", "--settings", str(ROOT / "examples/external-providers.json"), "--check"]), 0)
        self.assertIn("external providers", stdout.getvalue())
        with self.assertRaises(ConfigError):
            generate_external()

    def test_disabled_or_unselected_external_targets_fail(self):
        for options in ({"chatgpt_route": "FREE"}, {"external_providers": {"enabled": True, "countries": ["US"]}, "chatgpt_route": "JP"}):
            with self.assertRaises(ConfigError):
                output(options)

    def test_legacy_build_api(self):
        links, text, count = build(ACCOUNT)
        self.assertEqual(count, len(links))
        self.assertEqual(count, len(yaml.safe_load(text)["proxies"]))

    def test_output_permissions_and_no_raw_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "outputs"
            write_outputs(output(), destination)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in destination.iterdir()))
            self.assertFalse((destination / "usque-config.json").exists())

    def test_output_symlinks_and_input_overwrites_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "input").write_text("untouched")
            (root / "warp-masque.yaml").symlink_to(root / "input")
            with self.assertRaises(ConfigError):
                write_outputs(output(), root)
            self.assertEqual((root / "input").read_text(), "untouched")
            (root / "warp-masque.yaml").unlink()
            with self.assertRaises(ConfigError):
                write_outputs(output(), root, [root / "warp-masque.yaml"])

    def test_cli_check_and_errors_are_redacted(self):
        with patch.dict(os.environ, {"TEST_ACCOUNT": json.dumps(ACCOUNT)}), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(main(["--account-env", "TEST_ACCOUNT", "--check"]), 0)
            self.assertIn("0 registrations", stdout.getvalue())
            self.assertNotIn(ACCOUNT["private_key"], stdout.getvalue())
        with patch.dict(os.environ, {"TEST_ACCOUNT": "SECRET_INVALID_JSON"}), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(main(["--account-env", "TEST_ACCOUNT", "--check"]), 2)
            self.assertNotIn("SECRET_INVALID_JSON", stderr.getvalue())

    def test_cli_requires_explicit_existing_account(self):
        run = subprocess.run([sys.executable, str(ROOT / "scripts/generate.py")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("required", run.stderr)

    def test_cli_and_legacy_write_same_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            for script, args in (("generate.py", ["--account", str(ROOT / "tests/fixtures/synthetic-account.json"), "--output"]), ("gen_masque.py", [str(ROOT / "tests/fixtures/synthetic-account.json")])):
                path = Path(temporary) / script
                run = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args, str(path)], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
            for path in (Path(temporary) / "generate.py").iterdir():
                self.assertEqual(path.read_bytes(), (Path(temporary) / "gen_masque.py" / path.name).read_bytes())

    def test_workflow_has_no_registration_schedule_or_public_account_upload(self):
        text = (ROOT / ".github/workflows/warp-masque.yml").read_text()
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertNotIn("usque register", text)
        self.assertNotIn("git push", text)
        steps = workflow["jobs"]["generate"]["steps"]
        self.assertNotIn("secrets.", str(steps[0]))
        gate = steps[0]["run"]
        for private, enabled, confirmed, expected in (("false", "true", "true", 1), ("true", "false", "true", 1), ("true", "true", "false", 1), ("true", "true", "true", 0)):
            result = subprocess.run(["bash", "-c", gate], env={**os.environ, "REPOSITORY_PRIVATE": private, "GENERATION_ENABLED": enabled, "CONFIRMED": confirmed}, capture_output=True)
            self.assertEqual(result.returncode, expected)
        account_step = next(step for step in steps if "WARP_ACCOUNT_JSON" in step.get("env", {}))
        upload_step = next(step for step in steps if "upload-artifact" in step.get("uses", ""))
        for step in (account_step, upload_step):
            self.assertIn("github.event.repository.private", step["if"])
            self.assertIn("vars.WARP_GENERATION_ENABLED", step["if"])
            self.assertIn("inputs.confirm_private_artifact", step["if"])


if __name__ == "__main__":
    unittest.main()
