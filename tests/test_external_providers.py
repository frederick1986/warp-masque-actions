"""Synthetic-only provider tests: no remote feeds, account reads or tunnels."""
import copy
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
from external_providers import (
    AI, COUNTRIES, FREE_AI, FREE_AUTO, FREE_MANUAL, FREE_STREAM, SELECT, STREAM,
    WARP_RELAY, WARP_RELAY_AUTO, ExternalProviderError, augment_full_config, country_group_name,
    direct_only_config, normalize_provider_settings, provider_targets,
)

ENABLED = {"enabled": True}


def base_config():
    # Deliberately no key material; this fixture is never passed to a live core.
    return {
        "proxies": [{"name": "SYNTHETIC-WARP", "type": "masque", "server": "192.0.2.1", "port": 443}],
        "proxy-groups": [
            {"name": SELECT, "type": "select", "proxies": ["WARP AUTO", "DIRECT"]},
            {"name": "WARP AUTO", "type": "url-test", "proxies": ["SYNTHETIC-WARP"]},
            {"name": AI, "type": "select", "proxies": [SELECT, "DIRECT"]},
            {"name": "🎥 奈飞视频", "type": "select", "proxies": [SELECT, "DIRECT"]},
            {"name": STREAM, "type": "select", "proxies": [SELECT, "DIRECT"]},
            {"name": "📹 油管视频", "type": "select", "proxies": [SELECT, "DIRECT"]},
            {"name": "Ⓜ️ 微软服务", "type": "select", "proxies": ["🎯 全球直连", SELECT]},
            {"name": "🍎 苹果服务", "type": "select", "proxies": ["🎯 全球直连", SELECT]},
            {"name": "🎯 全球直连", "type": "select", "proxies": ["DIRECT", SELECT]},
            {"name": "🛑 全球拦截", "type": "select", "proxies": ["REJECT", "DIRECT"]},
        ],
        "rules": ["IP-CIDR,198.51.100.0/24,DIRECT,no-resolve", f"DOMAIN-SUFFIX,chatgpt.com,{AI}", f"MATCH,{SELECT}"],
    }


def group_map(config):
    return {group["name"]: group for group in config["proxy-groups"]}


class ExternalProviderTests(unittest.TestCase):
    def test_disabled_is_a_deep_copy_without_external_sources(self):
        before = base_config()
        result = augment_full_config(before)
        self.assertEqual(result, before)
        self.assertIsNot(result, before)
        result["proxies"][0]["name"] = "changed"
        self.assertEqual(before["proxies"][0]["name"], "SYNTHETIC-WARP")

    def test_no_network_and_no_input_mutation(self):
        config, settings = base_config(), {"enabled": True, "countries": ["JP", "US", "JP"]}
        before = copy.deepcopy((config, settings))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            result = augment_full_config(config, settings)
            direct_only_config(settings)
        self.assertEqual((config, settings), before)
        self.assertEqual(len(result["proxy-providers"]), 6)
        self.assertEqual(normalize_provider_settings()["countries"], ["US", "JP", "SG"])

    def test_three_independent_health_checks_per_country(self):
        providers = augment_full_config(base_config(), ENABLED)["proxy-providers"]
        self.assertEqual(len(providers), 9)
        for country in ("US", "JP", "SG"):
            source = f"https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/main/output/by-country/clash-{country}.yaml"
            for kind, url, status, timeout in (
                ("GENERAL", "https://www.gstatic.com/generate_204", "204", 5000),
                ("AI", "https://chatgpt.com/", "200-399", 9000),
                ("STREAM", "https://www.netflix.com/", "200-399", 9000),
            ):
                provider = providers[f"FREE-{country}-{kind}"]
                self.assertEqual(provider["type"], "http")
                self.assertEqual(provider["url"], source)
                self.assertEqual(provider["proxy"], "DIRECT")
                self.assertEqual(provider["interval"], 3600)
                self.assertEqual(provider["size-limit"], 8388608)
                self.assertEqual(provider["override"]["dialer-proxy"], "DIRECT")
                self.assertEqual(provider["health-check"], {"enable": True, "url": url, "expected-status": status,
                    "interval": 120, "timeout": timeout, "lazy": False})
        self.assertEqual(len({p["path"] for p in providers.values()}), 9)

    def test_all_selected_countries_have_actual_provider_groups(self):
        result = augment_full_config(base_config(), {"enabled": True, "countries": list(COUNTRIES)})
        groups = group_map(result)
        for code in COUNTRIES:
            self.assertEqual(groups[country_group_name(code)]["use"], [f"FREE-{code}-GENERAL"])
        self.assertEqual(provider_targets({"enabled": True, "countries": ["HK"]}), {"FREE": FREE_AUTO, "HK": "🇭🇰 HK"})
        self.assertEqual(provider_targets(), {})

    def test_stable_and_all_modes_always_exclude_masque(self):
        for mode in ("stable", "all"):
            result = direct_only_config({"enabled": True, "protocol_mode": mode})
            for provider in result["proxy-providers"].values():
                excluded = provider["exclude-type"].split("|")
                self.assertIn("masque", excluded)
                self.assertEqual("hysteria2" in excluded, mode == "stable")
                self.assertEqual("wireguard" in excluded, mode == "stable")

    def test_warp_chain_applies_to_download_and_connection_without_cycles(self):
        result = augment_full_config(base_config(), {"enabled": True, "use_warp": True, "scope": "all-foreign"})
        groups = group_map(result)
        self.assertEqual(groups[WARP_RELAY]["proxies"], [WARP_RELAY_AUTO, "SYNTHETIC-WARP"])
        self.assertEqual(groups[WARP_RELAY_AUTO]["proxies"], ["SYNTHETIC-WARP"])
        self.assertEqual(groups[WARP_RELAY_AUTO]["type"], "url-test")
        for provider in result["proxy-providers"].values():
            self.assertEqual(provider["proxy"], WARP_RELAY)
            self.assertEqual(provider["override"]["dialer-proxy"], WARP_RELAY)
        with self.assertRaises(ExternalProviderError):
            augment_full_config({"proxies": [], "proxy-groups": []}, {"enabled": True, "use_warp": True})

    def test_original_rules_and_warp_groups_unchanged(self):
        before = base_config()
        result = augment_full_config(before, ENABLED)
        self.assertEqual(result["rules"], before["rules"])
        self.assertEqual(result["proxies"], before["proxies"])
        groups = group_map(result)
        self.assertEqual(groups["WARP AUTO"], group_map(before)["WARP AUTO"])
        self.assertEqual(groups[SELECT]["proxies"][0], "WARP AUTO")
        self.assertEqual(groups[AI]["proxies"][0], FREE_AI)
        self.assertEqual(groups[STREAM]["proxies"][0], FREE_STREAM)
        self.assertEqual(groups["📹 油管视频"]["proxies"][0], SELECT)

    def test_scope_filters_preserve_ai_streaming_and_direct_priorities(self):
        for scope, ai, media in (("ai-only", True, False), ("streaming-only", False, True),
                                 ("ai-streaming", True, True), ("all-foreign", True, True)):
            with self.subTest(scope=scope):
                groups = group_map(augment_full_config(base_config(), {"enabled": True, "scope": scope}))
                self.assertEqual(groups[AI]["proxies"][0], FREE_AI if ai else SELECT)
                self.assertEqual(groups[STREAM]["proxies"][0], FREE_STREAM if media else SELECT)
                self.assertEqual(groups["📹 油管视频"]["proxies"][0], FREE_AUTO if scope == "all-foreign" else SELECT)
                self.assertEqual(groups["🛑 全球拦截"]["proxies"][0], "REJECT")
                for group in ("Ⓜ️ 微软服务", "🍎 苹果服务"):
                    self.assertEqual(groups[group]["proxies"][0], "🎯 全球直连")

    def test_direct_only_has_no_warp_masque_bridge_or_account_dependencies(self):
        result = direct_only_config({"enabled": True, "use_warp": True})
        self.assertEqual(result["proxies"], [])
        self.assertNotIn(WARP_RELAY, group_map(result))
        self.assertNotIn("external-controller", result)
        self.assertNotIn("rule-providers", result)
        for provider in result["proxy-providers"].values():
            self.assertEqual(provider["proxy"], "DIRECT")
            self.assertEqual(provider["override"]["dialer-proxy"], "DIRECT")
        text = yaml.safe_dump(result)
        for forbidden in ("WARP", "private-key:", "public-key:", "1080", "2081"):
            self.assertNotIn(forbidden, text)
        self.assertFalse(any(item.get("type") == "masque" for item in result["proxies"]))
        self.assertFalse(any(rule.startswith(("GEOIP,", "GEOSITE,")) for rule in result["rules"]))
        self.assertFalse(result["allow-lan"])
        self.assertEqual(result["bind-address"], "127.0.0.1")

    def test_all_references_resolve_without_self_or_group_cycles(self):
        for config in (direct_only_config(ENABLED), augment_full_config(base_config(), {"enabled": True, "use_warp": True})):
            groups = group_map(config)
            names = set(groups) | {p["name"] for p in config["proxies"]} | {"DIRECT", "REJECT"}
            providers = config["proxy-providers"]
            def visit(name, ancestors):
                self.assertNotIn(name, ancestors)
                for child in groups.get(name, {}).get("proxies", []):
                    if child in groups:
                        visit(child, ancestors + [name])
            for group in groups.values():
                self.assertTrue(set(group.get("proxies", [])) <= names)
                self.assertTrue(set(group.get("use", [])) <= set(providers))
                visit(group["name"], [])
            for rule in config["rules"]:
                fields = rule.split(",")
                self.assertIn(fields[-2] if fields[-1] == "no-resolve" else fields[-1], names)

    def test_validation_rejects_bad_inputs_without_echoing_values(self):
        invalid = [[], {"unknown": "DO_NOT_ECHO"}, {"enabled": "yes"}, {"use_warp": 1},
                   {"scope": "DO_NOT_ECHO"}, {"protocol_mode": "DO_NOT_ECHO"},
                   {"countries": "US"}, {"countries": ["US/../../x"]}, {"countries": [{}]},
                   {"enabled": True, "countries": []}]
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ExternalProviderError) as error:
                normalize_provider_settings(settings)
            self.assertNotIn("DO_NOT_ECHO", str(error.exception))
        with self.assertRaises(ExternalProviderError):
            direct_only_config()

    def test_collisions_fail_instead_of_overwriting_existing_groups_or_providers(self):
        base = base_config()
        base["proxy-groups"].append({"name": FREE_AUTO, "type": "select", "proxies": ["DIRECT"]})
        with self.assertRaises(ExternalProviderError):
            augment_full_config(base, ENABLED)
        base = base_config()
        base["proxy-providers"] = {"FREE-US-GENERAL": {"type": "file", "path": "keep.yaml"}}
        with self.assertRaises(ExternalProviderError):
            augment_full_config(base, ENABLED)


@unittest.skipUnless(os.environ.get("MIHOMO_BIN"), "set MIHOMO_BIN to an existing trusted core")
class ExternalProviderCoreSmokeTests(unittest.TestCase):
    def parse_with_synthetic_providers(self, config, root):
        binary = str(Path(os.environ["MIHOMO_BIN"]).resolve())
        for provider in config["proxy-providers"].values():
            provider["url"] = "http://127.0.0.1:9/synthetic-unreachable-provider.yaml"
            fixture = root / provider["path"]
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text(yaml.safe_dump({"proxies": [{"name": "SYNTHETIC-ONLY", "type": "ss",
                "server": "192.0.2.5", "port": 443, "cipher": "aes-128-gcm", "password": "not-a-real-secret"}]}))
        path = root / "test-config.yaml"
        path.write_text(yaml.safe_dump(config, allow_unicode=True))
        result = subprocess.run([binary, "-t", "-d", str(root), "-f", str(path)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_core_parses_direct_configs_with_only_synthetic_local_caches(self):
        # -t validates configuration syntax, never starts listeners/tunnels. No
        # production URL reaches the core; cached providers contain fake nodes.
        for mode in ("stable", "all"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = direct_only_config({"enabled": True, "protocol_mode": mode, "use_warp": True})
                self.parse_with_synthetic_providers(config, root)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required for unregistered synthetic ECDSA keys")
    def test_core_parses_combined_masque_and_warp_chained_external_providers(self):
        from warp_generator import generate
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, public = root / "synthetic-private.pem", root / "synthetic-public.pem"
            subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(private)],
                           check=True, capture_output=True)
            subprocess.run(["openssl", "ec", "-in", str(private), "-pubout", "-out", str(public)],
                           check=True, capture_output=True)
            account = {"private_key": private.read_text(), "endpoint_pub_key": public.read_text(), "ipv4": "192.0.2.20"}
            output = generate(account, {"endpoints": ["192.0.2.1"], "ports": [443],
                "ruleset_profile": "minimal", "formats": ["mihomo"], "chatgpt_route": "US",
                "custom_ip_rules": [{"cidr": "198.51.100.0/24", "target": "FREE"}],
                "external_providers": {"enabled": True, "use_warp": True, "scope": "all-foreign"}})
            config = yaml.safe_load(output["warp-masque.yaml"])
            self.assertEqual(config["proxy-providers"]["FREE-US-GENERAL"]["proxy"], WARP_RELAY)
            self.parse_with_synthetic_providers(config, root)


if __name__ == "__main__":
    unittest.main()
