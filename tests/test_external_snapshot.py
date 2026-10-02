"""Synthetic, read-only snapshot tests; never contact feeds or run a proxy."""
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib import request

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import external_snapshot as snapshot
from external_providers import COUNTRIES, PROVIDER_URL, STABLE_EXCLUSIONS

# All-zero, synthetic test bytes, never a registered WireGuard account.
SYNTHETIC_WG_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
SYNTHETIC_REALITY_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def proxy(kind="ss", **changes):
    fields = {
        "ss": {"cipher": "aes-128-gcm", "password": "SYNTHETIC-SS-PASSWORD"},
        "vmess": {"uuid": "00000000-0000-0000-0000-000000000001", "cipher": "auto", "alterId": 0},
        "vless": {"uuid": "00000000-0000-0000-0000-000000000002"},
        "trojan": {"password": "SYNTHETIC-TROJAN-PASSWORD"},
        "hysteria": {"auth-str": "SYNTHETIC-HYSTERIA-PASSWORD", "up": "30 Mbps", "down": "100 Mbps"},
        "hysteria2": {"password": "SYNTHETIC-HYSTERIA2-PASSWORD"},
        "tuic": {"uuid": "00000000-0000-0000-0000-000000000003", "password": "SYNTHETIC-TUIC-PASSWORD"},
        "wireguard": {"private-key": SYNTHETIC_WG_KEY, "public-key": SYNTHETIC_WG_KEY, "ip": "192.0.2.100"},
        "snell": {"psk": "SYNTHETIC-SNELL-PASSWORD", "version": 3},
        "anytls": {"password": "SYNTHETIC-ANYTLS-PASSWORD"},
    }.get(kind, {})
    return {"name": "ORIGINAL-UNTRUSTED-NAME", "type": kind, "server": "node.example.invalid", "port": 443, **fields, **changes}


def feed(nodes, **extra):
    return yaml.safe_dump({"proxies": nodes, **extra}, sort_keys=False).encode("utf-8")


def load(nodes, mode="stable"):
    return snapshot.load_snapshot(["US"], mode, lambda url: feed(nodes))


class SnapshotTests(unittest.TestCase):
    def test_import_does_no_network_or_account_io(self):
        with patch("urllib.request.build_opener", side_effect=AssertionError("network forbidden")), \
             patch("builtins.open", side_effect=AssertionError("account reads forbidden")):
            importlib.reload(snapshot)

    def test_countries_use_only_fixed_urls_and_preserve_input_order(self):
        fetched = []
        def fetch(url):
            fetched.append(url)
            return feed([proxy(server=f"n{len(fetched)}.example.invalid")])
        nodes, meta = snapshot.load_snapshot(["JP", "US", "JP", "SG"], fetcher=fetch)
        self.assertEqual(fetched, [PROVIDER_URL.format(country=c) for c in ("JP", "US", "SG")])
        self.assertEqual(meta["countries"], ["JP", "US", "SG"])
        self.assertEqual([n["name"] for n in nodes], ["EXT-JP-1", "EXT-US-1", "EXT-SG-1"])
        self.assertEqual(meta["country_nodes"], {"JP": ["EXT-JP-1"], "US": ["EXT-US-1"], "SG": ["EXT-SG-1"]})
        self.assertEqual(meta["source_urls"], fetched)
        self.assertFalse(meta["connectivity_tested"])
        self.assertFalse(meta["country_locations_verified"])

    def test_all_allowed_countries(self):
        nodes, meta = snapshot.load_snapshot(list(COUNTRIES), fetcher=lambda url: feed([proxy()]))
        self.assertEqual(len(nodes), 1)
        self.assertEqual(set(meta["country_nodes"]), set(COUNTRIES))
        self.assertTrue(all(names == ["EXT-US-1"] for names in meta["country_nodes"].values()))

    def test_dedup_ignores_names_and_removed_dialer_and_interface_settings(self):
        first = proxy(name="DIRECT", **{"dialer-proxy": "WARP", "interface-name": "eth-unavailable", "routing-mark": 99})
        same = proxy(name="REJECT", **{"dialer-proxy": "UNKNOWN-PROXY"})
        other = proxy(server="other.example.invalid")
        payloads = {"US": feed([first, same, other]), "JP": feed([same, other])}
        nodes, meta = snapshot.load_snapshot(["US", "JP"], fetcher=lambda url: payloads["US" if "-US." in url else "JP"])
        self.assertEqual([n["name"] for n in nodes], ["EXT-US-1", "EXT-US-2"])
        self.assertEqual(meta["country_nodes"]["JP"], ["EXT-US-1", "EXT-US-2"])
        self.assertEqual(meta["totals"]["downloaded"], 5)
        self.assertEqual(meta["totals"]["deduplicated"], 3)
        self.assertEqual(meta["totals"]["embedded"], 2)
        self.assertEqual(meta["totals"]["usable"], 2)
        for node in nodes:
            self.assertEqual(node["dialer-proxy"], "DIRECT")
            self.assertNotIn("interface-name", node)
            self.assertNotIn("routing-mark", node)
        for counts in meta["source_counts"]:
            self.assertEqual(counts["downloaded"], counts["rejected"] + counts["deduplicated"] + counts["embedded"])

    def test_dedup_preserves_distinct_credentials_and_options(self):
        nodes, _ = load([proxy(), proxy(password="ANOTHER-SYNTHETIC-PASSWORD"), proxy(udp=True)])
        self.assertEqual(len(nodes), 3)

    def test_preserves_opaque_reality_and_transport_options_without_mutation(self):
        node = proxy("vless", tls=True, udp=True, network="ws", servername="tls.example.invalid",
                     **{"reality-opts": {"public-key": SYNTHETIC_REALITY_KEY, "short-id": "0123", "support-x25519mlkem768": True},
                        "ws-opts": {"path": "/a?b=c", "headers": {"Host": "front.example.invalid", "X-Test": "opaque"}},
                        "smux": {"enabled": True, "protocol": "h2mux", "max-connections": 4}})
        before = deepcopy(node)
        nodes, _ = load([node])
        self.assertEqual(node, before)
        self.assertEqual(nodes[0], {**node, "name": "EXT-US-1", "dialer-proxy": "DIRECT"})

    def test_reality_valid_keys_and_short_ids_are_preserved_exactly(self):
        for key in (SYNTHETIC_REALITY_KEY, "_" * 42 + "8"):
            for short_id in (None, "", "01", "aB12", "0123456789ABCDEF"):
                opts = {"public-key": key}
                if short_id is not None:
                    opts["short-id"] = short_id
                node = proxy("vless", tls=True, **{"reality-opts": opts})
                with self.subTest(short_id=short_id):
                    nodes, _ = load([node])
                self.assertEqual(nodes[0]["reality-opts"], opts)

    def test_invalid_reality_keys_short_ids_and_nested_reality_are_rejected(self):
        bad_keys = [None, True, [], "", "SYNTHETIC-PUBLIC", "A" * 42, "A" * 44,
                    SYNTHETIC_REALITY_KEY + "=", "/" * 42 + "8", "+" * 42 + "8",
                    "A" * 42 + "!", " " + SYNTHETIC_REALITY_KEY, SYNTHETIC_REALITY_KEY + "\n"]
        bad_opts = [{"public-key": key} for key in bad_keys]
        bad_opts += [{"public-key": SYNTHETIC_REALITY_KEY, "short-id": value}
                     for value in (None, True, 12, [], "0", "abc", "0g", "ab-cd", "01 02", "00" * 9)]
        bad_opts += [{"public-key": SYNTHETIC_REALITY_KEY, "support-x25519mlkem768": "true"}]
        bad = [proxy("vless", tls=True, **{"reality-opts": opts}) for opts in bad_opts]
        bad += [proxy("vless", tls=True, network="xhttp", **{
            "xhttp-opts": {"download-settings": {"reality-opts": {"public-key": "invalid"}}}})]
        nodes, meta = load(bad + [proxy("vless")])
        self.assertEqual(len(nodes), 1)
        self.assertEqual(meta["totals"]["rejected"], len(bad))

    def test_full_remote_config_imports_only_inline_nodes(self):
        data = feed([proxy()], **{
            "proxy-providers": {"EVIL": {"type": "http", "url": "https://unapproved.invalid/private"}},
            "proxy-groups": [{"name": "DIRECT", "type": "relay", "proxies": ["EVIL"]}],
            "external-controller": "0.0.0.0:9090", "secret": "DO_NOT_COPY", "tun": {"enable": True},
            "rules": ["MATCH,EVIL"], "dns": {"enable": True, "listen": "0.0.0.0:53"},
        })
        nodes, meta = snapshot.load_snapshot(["US"], fetcher=lambda url: data)
        result = json.dumps([nodes, meta])
        for forbidden in ("unapproved.invalid", "DO_NOT_COPY", "0.0.0.0", "EVIL", "external-controller", "rules"):
            self.assertNotIn(forbidden, result)
        self.assertEqual(len(nodes), 1)

    def test_stable_mode_matches_existing_exclusions_and_masque_never_allowed(self):
        all_nodes = [proxy(kind) for kind in sorted(snapshot.SUPPORTED_TYPES)]
        all_nodes += [proxy("masque"), proxy("http"), proxy("socks5"), proxy("relay"), proxy("direct")]
        for mode in ("stable", "all"):
            nodes, meta = load(all_nodes, mode)
            expected = snapshot.SUPPORTED_TYPES - (set(STABLE_EXCLUSIONS) if mode == "stable" else set())
            self.assertEqual({n["type"] for n in nodes}, expected)
            self.assertEqual(meta["totals"]["rejected"], len(all_nodes) - len(expected))

    def test_unknown_fields_local_files_plugins_and_hidden_dependencies_are_rejected(self):
        bad = [
            {"proxy": "NO_SUCH_GROUP"}, {"proxies": ["NO_SUCH_GROUP"]}, {"use": ["MISSING"]},
            {"proxy-providers": {"x": {}}}, {"dialer_proxy": "OTHER"}, {"interfaces": ["eth0"]},
            {"unknown-future-option": "anything"}, {"certificate": "/etc/private-cert.pem"},
            {"private-key": "/etc/private-key.pem"}, {"plugin": "/tmp/executable"},
            {"plugin": "unknown-plugin"}, {"plugin-opts": {"proxy": "MISSING"}},
            {"ws-opts": {"dialer-proxy": "MISSING"}}, {"ws-opts": {"Dialer_Proxy": "MISSING"}},
            {"smux": {"interface-name": "eth0"}}, {"reality-opts": {"public-key": "x", "private-key": "/tmp/key"}},
            {"ws-opts": {"nested": {"detour": "MISSING"}}}, {"tlsmirror-opts": {"primary-egress-outbound": "OTHER"}},
        ]
        nodes, meta = load([proxy(**changes) for changes in bad] + [proxy()])
        self.assertEqual(len(nodes), 1)
        self.assertEqual(meta["totals"]["rejected"], len(bad))

    def test_bad_host_port_type_credentials_and_structures_are_rejected(self):
        bad = [None, True, 1, "ss://not-yaml", [], {}, {"type": []}]
        bad += [proxy(server=host) for host in ("", " ", "https://x.invalid", "user@x.invalid", "example.invalid:443", "../file", "a\nb", "fe80::1%eth0", "[::1]", "a..b")]
        bad += [proxy(port=port) for port in (True, False, 0, 65536, "443", 443.1, None)]
        bad += [proxy(password=value) for value in ("", "   ", None, 123, [], "a\nb")]
        bad += [proxy(cipher=""), proxy("vless", uuid=""), proxy("trojan", password=None), proxy("snell", psk=""),
                proxy("anytls", password=""), proxy("hysteria", **{"auth-str": ""}),
                proxy("tuic", uuid=""), proxy("vmess", alterId=True), proxy("vmess", cipher=""),
                proxy(udp="yes"), proxy(**{"ws-opts": []}), proxy(**{"alpn": "h2"}),
                proxy(**{"alpn": [False]}), proxy(network=[]), proxy(**{"smux": {"x": float("nan")}})]
        nodes, meta = load(bad + [proxy()], "all")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(meta["totals"]["rejected"], len(bad))

    def test_valid_hostname_ip_and_unicode_domain_preserved(self):
        servers = ["192.0.2.1", "2001:db8::1", "node.example.invalid", "xn--bcher-kva.example", "bücher.example"]
        nodes, _ = load([proxy(server=server) for server in servers])
        self.assertEqual([n["server"] for n in nodes], servers)

    def test_tuic_v4_and_v5_mutually_exclusive_and_wireguard_requires_inline_endpoint(self):
        v4 = proxy("tuic", token="SYNTHETIC-TOKEN")
        del v4["uuid"], v4["password"]
        wg = proxy("wireguard", peers=[{"server": "peer.example.invalid", "port": 443, "public-key": SYNTHETIC_WG_KEY}])
        invalid = [proxy("tuic", token="SYNTHETIC-TOKEN"), proxy("wireguard", **{"public-key": ""}),
                   proxy("wireguard", ip="not-an-ip"), proxy("wireguard", peers=[{"proxy": "OTHER"}]),
                   proxy("wireguard", peers=[]), proxy("wireguard", **{"private-key": "/etc/private"}),
                   proxy("wireguard", dns=["https://dns.example.invalid/#MISSING"]),
                   proxy("wireguard", dns=["dns.example.invalid"]), proxy("wireguard", dns="1.1.1.1")]
        nodes, meta = load([v4, wg] + invalid, "all")
        self.assertEqual(len(nodes), 2)
        self.assertEqual(meta["totals"]["rejected"], len(invalid))

    def test_wireguard_inline_dns_resolvers_and_keys_are_preserved(self):
        node = proxy("wireguard", dns=["192.0.2.53", "2001:db8::53"], **{"remote-dns-resolve": True})
        nodes, _ = load([node], "all")
        self.assertEqual(nodes[0], {**node, "name": "EXT-US-1", "dialer-proxy": "DIRECT"})

    def test_metadata_never_contains_original_names_hosts_or_credentials(self):
        nodes, meta = load([proxy(name="PRIVATE-NAME", password="NEVER-IN-METADATA", server="private.example.invalid")])
        text = json.dumps(meta)
        self.assertNotIn("PRIVATE-NAME", text)
        self.assertNotIn("NEVER-IN-METADATA", text)
        self.assertNotIn("private.example.invalid", text)
        self.assertEqual(nodes[0]["password"], "NEVER-IN-METADATA")

    def test_all_selected_countries_must_succeed_and_have_usable_nodes(self):
        for failure in (b"proxies: []", feed([proxy("masque")]), b"malformed: [", RuntimeError("DO_NOT_ECHO")):
            fetched = []
            def fetch(url):
                fetched.append(url)
                if "-JP." in url:
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
                return feed([proxy()])
            with self.subTest(failure=type(failure).__name__), self.assertRaises(snapshot.SnapshotError) as error:
                snapshot.load_snapshot(["US", "JP", "SG"], fetcher=fetch)
            self.assertIn("JP", str(error.exception))
            self.assertNotIn("DO_NOT_ECHO", str(error.exception))
            self.assertEqual(len(fetched), 2)

    def test_invalid_options_fail_before_fetching(self):
        fetcher = Mock(side_effect=AssertionError("must not fetch"))
        for countries in (None, "US", [], {}, ["us"], ["XX"], ["US/../other"], [[]], [1]):
            with self.subTest(countries=countries), self.assertRaises(snapshot.SnapshotError):
                snapshot.load_snapshot(countries, fetcher=fetcher)
        for mode in ("unknown", [], None, True):
            with self.assertRaises(snapshot.SnapshotError):
                snapshot.load_snapshot(["US"], mode, fetcher)
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.load_snapshot(["US"], fetcher=True)
        fetcher.assert_not_called()

    def test_bad_utf8_size_root_tags_keys_and_aliases_fail_closed(self):
        cases = [
            b"", b"\xff\xfe", "text-not-bytes", b"proxies: []\nproxies: []", b"proxies: [{type: ss, type: vless}]",
            b"!!python/object/apply:os.system ['DO_NOT_ECHO']", b"proxies: !!set {}", b"[one,two]", b"proxies: {}",
            b"proxies: []\nother: {1: value}", b"x: &x []\nproxies: [*x]", b"proxies: &x [*x]",
            b"base: &base {a: b}\nproxies: [{<<: *base}]", b"proxies: []\n---\nproxies: []", b"proxies: [\x00]",
        ]
        for payload in cases:
            with self.subTest(payload=str(payload)[:40]), self.assertRaises(snapshot.SnapshotError) as error:
                snapshot.load_snapshot(["US"], fetcher=lambda url: payload)
            self.assertNotIn("DO_NOT_ECHO", str(error.exception))
        with patch.object(snapshot, "MAX_BYTES", 10), self.assertRaises(snapshot.SnapshotError):
            snapshot.load_snapshot(["US"], fetcher=lambda url: b"x" * 11)

    def test_yaml_depth_node_scalar_and_proxy_limits(self):
        for limit, value, payload in (
            ("MAX_DEPTH", 5, b"proxies: [[[[[[[[]]]]]]] ]"),
            ("MAX_YAML_NODES", 5, feed([proxy()])),
            ("MAX_SCALAR_LENGTH", 20, b"proxies: []\nother: " + b"x" * 21),
            ("MAX_PROXIES", 1, feed([proxy(), proxy()])),
        ):
            with self.subTest(limit=limit), patch.object(snapshot, limit, value), self.assertRaises(snapshot.SnapshotError):
                snapshot.load_snapshot(["US"], fetcher=lambda url: payload)

    def test_deterministic_result_for_same_input(self):
        data = feed([proxy(), proxy("vless")])
        self.assertEqual(snapshot.load_snapshot(["US", "SG"], fetcher=lambda url: data),
                         snapshot.load_snapshot(["US", "SG"], fetcher=lambda url: data))


class FakeResponse:
    def __init__(self, content, *, url=None, status=200, headers=None):
        self.body = io.BytesIO(content)
        self.url = url or PROVIDER_URL.format(country="US")
        self.status = status
        self.headers = headers or {}
        self.closed = False
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def geturl(self):
        return self.url

    def read1(self, count):
        self.read_sizes.append(count)
        return self.body.read(count)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.url = PROVIDER_URL.format(country="US")

    def download(self, response):
        opener = Mock()
        opener.open.return_value = response
        with patch.object(request, "build_opener", return_value=opener) as build:
            result = snapshot._download(self.url)
        return result, opener, build

    def test_bounded_https_default_tls_no_environment_proxy(self):
        payload = feed([proxy()])
        response = FakeResponse(payload, headers={"Content-Length": str(len(payload))})
        result, opener, build = self.download(response)
        self.assertEqual(result, payload)
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(h, request.ProxyHandler) and h.proxies == {} for h in handlers))
        self.assertTrue(any(isinstance(h, snapshot._NoRedirect) for h in handlers))
        self.assertTrue(any(isinstance(h, request.HTTPSHandler) for h in handlers))
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, self.url)
        self.assertEqual(req.get_header("Accept-encoding"), "identity")
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], snapshot.DOWNLOAD_TIMEOUT)
        self.assertTrue(response.closed)
        self.assertTrue(all(0 < size <= 65_536 for size in response.read_sizes))

    def test_unapproved_urls_blocked_before_network(self):
        with patch.object(request, "build_opener", side_effect=AssertionError("must not connect")):
            for url in ("http://raw.githubusercontent.com/x", "https://evil.invalid/x", self.url + "?token=secret",
                        self.url + "/../other", "https://user:pass@raw.githubusercontent.com/x", "file:///etc/passwd"):
                with self.subTest(url=url), self.assertRaises(snapshot.SnapshotError):
                    snapshot._download(url)

    def test_redirect_handler_rejects_cross_origin_same_origin_and_non_https(self):
        handler = snapshot._NoRedirect()
        for destination in ("https://evil.invalid/", "http://raw.githubusercontent.com/", self.url):
            with self.assertRaises(snapshot.SnapshotError):
                handler.redirect_request(request.Request(self.url), None, 302, "Found", {}, destination)

    def test_redirected_responses_status_encoding_and_content_length_fail(self):
        cases = [
            FakeResponse(b"x", url="https://evil.invalid/"), FakeResponse(b"x", status=302),
            FakeResponse(b"x", status=404), FakeResponse(b"x", headers={"Content-Encoding": "gzip"}),
            FakeResponse(b"x", headers={"Content-Length": "-1"}), FakeResponse(b"x", headers={"Content-Length": "x"}),
            FakeResponse(b"x", headers={"Content-Length": "9"}),
            FakeResponse(b"x", headers={"Content-Length": str(snapshot.MAX_BYTES + 1)}),
        ]
        for response in cases:
            with self.subTest(headers=response.headers), self.assertRaises(snapshot.SnapshotError):
                self.download(response)
            self.assertTrue(response.closed)

    def test_body_size_limit_is_enforced_without_content_length(self):
        response = FakeResponse(b"x" * 101)
        with patch.object(snapshot, "MAX_BYTES", 100), self.assertRaises(snapshot.SnapshotError):
            self.download(response)
        self.assertEqual(response.read_sizes, [101])
        self.assertTrue(response.closed)

    def test_deadline_and_transport_exceptions_are_sanitized(self):
        with patch.object(snapshot.time, "monotonic", side_effect=[0, snapshot.DOWNLOAD_DEADLINE + 1]), \
             self.assertRaises(snapshot.SnapshotError):
            self.download(FakeResponse(b"x"))
        opener = Mock()
        opener.open.side_effect = OSError("DO_NOT_ECHO secret remote data")
        with patch.object(request, "build_opener", return_value=opener), self.assertRaises(snapshot.SnapshotError) as error:
            snapshot._download(self.url)
        self.assertNotIn("DO_NOT_ECHO", str(error.exception))


if __name__ == "__main__":
    unittest.main()
