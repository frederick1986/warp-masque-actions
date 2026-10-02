"""Explicit, bounded snapshots of the donor's public Mihomo country feeds.

Importing this module does no I/O. ``load_snapshot`` downloads only the fixed
country URLs, extracts inline proxies, and never starts a proxy or reads WARP
accounts. Remote rules, listeners, groups and providers are never imported.
The result is static configuration validation, not a connectivity guarantee.
This is a conservative safety/schema filter, not a substitute for the selected
Mihomo version's parser: opaque transport/plugin options and protocol-specific
cipher/credential combinations still need that core's offline config check.
"""
from __future__ import annotations

from copy import deepcopy
import base64
import binascii
import hashlib
import ipaddress
import json
import math
import re
import ssl
import time
from urllib import request

import yaml

from external_providers import COUNTRIES, PROVIDER_URL, STABLE_EXCLUSIONS

MAX_BYTES = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT = 20
DOWNLOAD_DEADLINE = 60
MAX_DEPTH = 32
MAX_YAML_NODES = 200_000
MAX_SCALAR_LENGTH = 65_536
MAX_PROXIES = 10_000
SUPPORTED_TYPES = frozenset({"ss", "vmess", "vless", "trojan", "hysteria", "hysteria2", "tuic", "wireguard", "snell", "anytls"})
_ALLOWED_URLS = frozenset(PROVIDER_URL.format(country=c) for c in COUNTRIES)

# An allowlist prevents an unknown future option from introducing file/provider
# dependencies. Known protocol option dictionaries stay intact, subject to the
# recursive dependency/file checks below (including reality-opts).
_COMMON_FIELDS = frozenset({
    "name", "type", "server", "port", "udp", "ip-version", "tfo", "mptcp", "smux",
    "dialer-proxy", "interface-name", "routing-mark",
})
_TLS_FIELDS = frozenset({
    "tls", "sni", "servername", "fingerprint", "alpn", "skip-cert-verify",
    "name-cert-verify", "client-fingerprint", "reality-opts", "ech-opts",
    "shadow-tls-opts", "restls-opts", "jls-opts",
})
_TRANSPORT_FIELDS = frozenset({
    "network", "ws-opts", "http-opts", "h2-opts", "grpc-opts", "http-upgrade-opts",
    "xhttp-opts", "kcp-opts", "mkcp-opts", "mekya-opts", "packet-encoding",
})
_TYPE_FIELDS = {
    "ss": {"cipher", "password", "plugin", "plugin-opts", "udp-over-tcp", "udp-over-tcp-version"},
    "vmess": {"uuid", "alterId", "cipher", "global-padding", "authenticated-length"},
    "vless": {"uuid", "flow", "encryption"},
    "trojan": {"password", "ss-opts"},
    "hysteria": {"auth", "auth-str", "obfs", "protocol", "up", "down", "up-speed", "down-speed",
                 "ports", "recv-window-conn", "recv-window", "disable_mtu_discovery", "fast-open"},
    "hysteria2": {"password", "obfs", "obfs-password", "ports", "hop-interval", "up", "down",
                  "up-speed", "down-speed", "recv-window-conn", "recv-window", "max-stream-receive-window",
                  "initial-stream-receive-window", "max-connection-receive-window",
                  "initial-connection-receive-window", "cwnd", "udp-mtu", "fast-open"},
    "tuic": {"token", "uuid", "password", "ip", "heartbeat-interval", "disable-sni", "reduce-rtt",
             "request-timeout", "udp-relay-mode", "congestion-controller", "bbr-profile",
             "max-udp-relay-packet-size", "fast-open", "max-open-streams"},
    "wireguard": {"private-key", "public-key", "pre-shared-key", "ip", "ipv6", "allowed-ips", "reserved",
                  "persistent-keepalive", "mtu", "remote-dns-resolve", "dns", "peers", "ip-stack",
                  "amnezia-wg-option", "workers"},
    "snell": {"psk", "version", "obfs-opts", "reuse", "version-hint"},
    "anytls": {"password", "idle-session-check-interval", "idle-session-timeout", "min-idle-session"},
}
_DEPENDENCY_FIELDS = frozenset({
    "proxy", "proxies", "proxy-provider", "proxy-providers", "proxy-groups", "rule-providers", "use",
    "dialer-proxy", "dialer", "detour", "outbound", "outbounds", "interface", "interfaces", "interface-name",
    "routing-mark", "certificate", "ca", "ca-cert", "ca-file", "cert-file", "key-file", "private-key-file",
    "certificate-path", "private-key-path", "primary-egress-outbound", "primary-ingress-outbound",
})
_OPTION_FIELDS = frozenset({"smux", "ss-opts", "obfs-opts", "plugin-opts", "ip-stack", "amnezia-wg-option"}) | frozenset(
    field for field in _TLS_FIELDS | _TRANSPORT_FIELDS if field.endswith("-opts")
)
_BOOLEAN_FIELDS = frozenset({
    "udp", "tls", "tfo", "mptcp", "skip-cert-verify", "global-padding", "authenticated-length",
    "udp-over-tcp", "disable_mtu_discovery", "fast-open", "disable-sni", "reduce-rtt", "remote-dns-resolve", "reuse",
})


class SnapshotError(ValueError):
    """Errors intentionally exclude feed content, original names and credentials."""


class _RejectedNode(ValueError):
    pass


class _SnapshotLoader(yaml.SafeLoader):
    """Reject duplicate/non-string keys, YAML aliases and excessive resources."""

    def __init__(self, stream):
        super().__init__(stream)
        self._depth = 0
        self._nodes = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise SnapshotError("external snapshot YAML aliases are not allowed")
        self._depth += 1
        self._nodes += 1
        try:
            if self._depth > MAX_DEPTH or self._nodes > MAX_YAML_NODES:
                raise SnapshotError("external snapshot YAML resource limit exceeded")
            node = super().compose_node(parent, index)
            if isinstance(node, yaml.ScalarNode) and len(node.value) > MAX_SCALAR_LENGTH:
                raise SnapshotError("external snapshot YAML scalar limit exceeded")
            return node
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise SnapshotError("external snapshot YAML mapping is invalid")
        result = {}
        for key_node, value_node in node.value:
            if key_node.tag != "tag:yaml.org,2002:str" or key_node.value == "<<":
                raise SnapshotError("external snapshot YAML keys must be plain strings")
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise SnapshotError("external snapshot YAML contains duplicate keys")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Raw GitHub files need no redirects. Rejecting all of them is stricter
        # than accepting same-origin redirects and avoids path/source changes.
        raise SnapshotError("external snapshot redirects are not allowed")


def _download(url):
    """Read a fixed HTTPS resource, with no ambient proxy or authentication."""
    if url not in _ALLOWED_URLS:
        raise SnapshotError("external snapshot source is not approved")
    try:
        opener = request.build_opener(request.ProxyHandler({}), _NoRedirect(),
                                      request.HTTPSHandler(context=ssl.create_default_context()))
        req = request.Request(url, headers={"Accept": "application/yaml,text/yaml,text/plain",
                                           "Accept-Encoding": "identity", "User-Agent": "warp-masque-snapshot/1"})
        deadline = time.monotonic() + DOWNLOAD_DEADLINE
        with opener.open(req, timeout=DOWNLOAD_TIMEOUT) as response:
            if response.geturl() != url or response.status != 200:
                raise SnapshotError("external snapshot source response is invalid")
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise SnapshotError("external snapshot compressed responses are not accepted")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 10):
                raise SnapshotError("external snapshot response length is invalid")
            if length is not None and int(length) > MAX_BYTES:
                raise SnapshotError("external snapshot exceeds the download size limit")
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise SnapshotError("external snapshot download deadline exceeded")
                # read1 bounds each iteration to one underlying read, unlike
                # read(n), which can wait indefinitely for a slow trickle.
                chunk = response.read1(min(65_536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise SnapshotError("external snapshot exceeds the download size limit")
                chunks.append(chunk)
            if time.monotonic() >= deadline:
                raise SnapshotError("external snapshot download deadline exceeded")
            if length is not None and size != int(length):
                raise SnapshotError("external snapshot response is incomplete")
            return b"".join(chunks)
    except SnapshotError:
        raise
    except Exception:
        # urllib errors can embed full response lines and other remote content.
        raise SnapshotError("external snapshot download failed") from None


def _parse(payload):
    if not isinstance(payload, bytes):
        raise SnapshotError("external snapshot fetcher must return bytes")
    if not payload or len(payload) > MAX_BYTES:
        raise SnapshotError("external snapshot is empty or exceeds the size limit")
    try:
        document = yaml.load(payload.decode("utf-8-sig", errors="strict"), Loader=_SnapshotLoader)
    except SnapshotError:
        raise
    except (UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        raise SnapshotError("external snapshot is not valid bounded UTF-8 YAML") from None
    if not isinstance(document, dict) or not isinstance(document.get("proxies"), list):
        raise SnapshotError("external snapshot must contain an inline proxies list")
    proxies = document["proxies"]
    if len(proxies) > MAX_PROXIES:
        raise SnapshotError("external snapshot exceeds the proxy count limit")
    return proxies


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _host(value):
    if not _text(value) or value != value.strip() or len(value) > 253:
        return False
    try:
        ipaddress.ip_address(value)
        return "%" not in value  # no machine-local IPv6 zone/interface dependency
    except ValueError:
        try:
            host = value.encode("idna").decode("ascii")
        except UnicodeError:
            return False
        return all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                   for label in host.rstrip(".").split("."))


def _port(value):
    return type(value) is int and 1 <= value <= 65535


def _wireguard_key(value):
    if not _text(value):
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (ValueError, binascii.Error):
        return False


def _reality_options(value):
    """Match Mihomo's raw base64url X25519 key and hexadecimal short ID.

    Source: MetaCubeX/mihomo adapter/outbound/reality.go, RealityOptions.Parse.
    Never pad, normalize or replace the supplied authentication fields.
    """
    if not isinstance(value, dict):
        return False
    key, short_id = value.get("public-key"), value.get("short-id", "")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
        return False
    try:
        if len(base64.b64decode(key + "=", altchars=b"-_", validate=True)) != 32:
            return False
    except (ValueError, binascii.Error):
        return False
    if not isinstance(short_id, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){0,8}", short_id):
        return False
    return "support-x25519mlkem768" not in value or type(value["support-x25519mlkem768"]) is bool


def _inline_tree(value, *, wireguard=False):
    """Keep opaque protocol options, but never a hidden chain or file input."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower().replace("_", "-") in _DEPENDENCY_FIELDS:
                raise _RejectedNode()
            if key == "private-key" and not wireguard:
                raise _RejectedNode()
            # XHTTP download-settings may contain a second Reality configuration.
            if key == "reality-opts" and not _reality_options(child):
                raise _RejectedNode()
            _inline_tree(child, wireguard=wireguard)
    elif isinstance(value, list):
        for child in value:
            _inline_tree(child, wireguard=wireguard)
    elif isinstance(value, str):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise _RejectedNode()
    elif value is not None and type(value) not in (bool, int, float):
        raise _RejectedNode()
    elif type(value) is float and not math.isfinite(value):
        raise _RejectedNode()


def _sanitize(raw, protocol_mode):
    if not isinstance(raw, dict) or raw.get("type") not in SUPPORTED_TYPES:
        raise _RejectedNode()
    kind = raw["type"]
    if protocol_mode == "stable" and kind in STABLE_EXCLUSIONS:
        raise _RejectedNode()
    allowed = _COMMON_FIELDS | _TLS_FIELDS | _TRANSPORT_FIELDS | _TYPE_FIELDS[kind]
    if set(raw) - allowed or not _host(raw.get("server")) or not _port(raw.get("port")):
        raise _RejectedNode()
    node = deepcopy(raw)
    # Original labels and platform-specific routing never survive the snapshot.
    # Only this top-level dialer is replaced; hidden nested chains are rejected.
    for field in ("name", "dialer-proxy", "interface-name", "routing-mark"):
        node.pop(field, None)
    _inline_tree(node, wireguard=kind == "wireguard")
    for field in _BOOLEAN_FIELDS & node.keys():
        if type(node[field]) is not bool:
            raise _RejectedNode()
    for field in _OPTION_FIELDS & node.keys():
        if not isinstance(node[field], dict):
            raise _RejectedNode()
    if "alpn" in node and (not isinstance(node["alpn"], list) or not all(_text(v) for v in node["alpn"])):
        raise _RejectedNode()
    if "network" in node and node["network"] not in ("tcp", "ws", "http", "h2", "grpc", "mkcp", "mekya", "http-upgrade", "xhttp"):
        raise _RejectedNode()
    for field in ("sni", "servername"):
        if field in node and node[field] != "" and not _host(node[field]):
            raise _RejectedNode()
    required = {
        "ss": ("cipher", "password"), "vmess": ("uuid", "cipher"), "vless": ("uuid",),
        "trojan": ("password",), "hysteria2": ("password",), "snell": ("psk",), "anytls": ("password",),
        "wireguard": ("private-key",),
    }.get(kind, ())
    if any(not _text(node.get(field)) for field in required):
        raise _RejectedNode()
    if kind == "hysteria" and not any(_text(node.get(field)) for field in ("auth", "auth-str")):
        raise _RejectedNode()
    if kind == "tuic":
        v4 = _text(node.get("token")) and "uuid" not in node and "password" not in node
        v5 = _text(node.get("uuid")) and _text(node.get("password")) and "token" not in node
        if not (v4 or v5):
            raise _RejectedNode()
    if kind == "vmess" and "alterId" in node and (type(node["alterId"]) is not int or node["alterId"] < 0):
        raise _RejectedNode()
    if kind == "ss" and "plugin" in node and node["plugin"] not in ("obfs", "v2ray-plugin", "gost-plugin", "shadow-tls", "restls", "kcptun", "jls"):
        raise _RejectedNode()
    if kind == "wireguard":
        if not _wireguard_key(node["private-key"]) or not any(_text(node.get(field)) for field in ("ip", "ipv6")):
            raise _RejectedNode()
        for field in ("public-key", "pre-shared-key"):
            if field in node and not _wireguard_key(node[field]):
                raise _RejectedNode()
        for field in ("ip", "ipv6"):
            if field in node:
                try:
                    if not _text(node[field]):
                        raise _RejectedNode()
                    address = ipaddress.ip_interface(node[field])
                    if address.version != (4 if field == "ip" else 6):
                        raise _RejectedNode()
                except (ValueError, TypeError):
                    raise _RejectedNode() from None
        if "dns" in node:
            # DNS URL fragments can refer to a missing proxy/group. Inline IP
            # resolvers need no extra rules, providers or client-local files.
            if not isinstance(node["dns"], list) or not node["dns"]:
                raise _RejectedNode()
            for resolver in node["dns"]:
                try:
                    if not isinstance(resolver, str) or "%" in resolver:
                        raise _RejectedNode()
                    ipaddress.ip_address(resolver)
                except (ValueError, TypeError):
                    raise _RejectedNode() from None
        if "peers" in node:
            peers = node["peers"]
            if not isinstance(peers, list) or not peers:
                raise _RejectedNode()
            peer_fields = {"server", "port", "public-key", "pre-shared-key", "allowed-ips", "reserved", "persistent-keepalive"}
            if any(not isinstance(peer, dict) or set(peer) - peer_fields or not _host(peer.get("server")) or
                   not _port(peer.get("port")) or not _wireguard_key(peer.get("public-key")) or
                   ("pre-shared-key" in peer and not _wireguard_key(peer["pre-shared-key"])) for peer in peers):
                raise _RejectedNode()
        elif not _text(node.get("public-key")):
            raise _RejectedNode()
    node["dialer-proxy"] = "DIRECT"
    return node


def load_snapshot(countries, protocol_mode="stable", fetcher=None) -> tuple[list[dict], dict]:
    """Fetch every selected country or raise; never return a partial snapshot.

    ``fetcher(url) -> bytes`` is an offline-test seam, not a configurable URL.
    Metadata contains no original labels, hosts or credentials. ``downloaded``
    counts input nodes; ``deduplicated`` counts globally repeated nodes;
    ``embedded`` counts newly added nodes; ``usable`` counts unique names in a
    country's mapping (or globally unique nodes in totals). Thus a cross-country
    duplicate stays selectable in both countries but is embedded only once.
    Country labels are claims made by the source, not geolocation verification.
    """
    if not isinstance(countries, (list, tuple)) or not countries or any(
        not isinstance(code, str) or code not in COUNTRIES for code in countries
    ):
        raise SnapshotError("snapshot countries must list US, JP, SG, HK, TW or KR")
    if not isinstance(protocol_mode, str) or protocol_mode not in ("stable", "all"):
        raise SnapshotError("snapshot protocol_mode must be stable or all")
    if fetcher is not None and not callable(fetcher):
        raise SnapshotError("snapshot fetcher must be callable")
    countries = list(dict.fromkeys(countries))
    fetch = _download if fetcher is None else fetcher
    nodes, seen, source_counts, country_nodes = [], {}, [], {}
    for country in countries:
        url = PROVIDER_URL.format(country=country)
        try:
            payload = fetch(url)
        except Exception:
            raise SnapshotError(f"external snapshot download failed for {country}") from None
        try:
            raw_nodes = _parse(payload)
        except SnapshotError as error:
            raise SnapshotError(f"{country}: {error}") from None
        counts = {"country": country, "url": url, "bytes": len(payload), "downloaded": len(raw_nodes),
                  "rejected": 0, "deduplicated": 0, "embedded": 0, "usable": 0}
        names, country_seen = [], set()
        for raw in raw_nodes:
            try:
                node = _sanitize(raw, protocol_mode)
            except (TypeError, _RejectedNode):
                counts["rejected"] += 1
                continue
            fingerprint = hashlib.sha256(json.dumps(node, sort_keys=True, ensure_ascii=True,
                                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).digest()
            if fingerprint in seen:
                counts["deduplicated"] += 1
                name = seen[fingerprint]
            else:
                counts["embedded"] += 1
                name = f"EXT-{country}-{counts['embedded']}"
                seen[fingerprint] = name
                nodes.append({"name": name, **node})
            if name not in country_seen:
                names.append(name)
                country_seen.add(name)
        if not names:
            raise SnapshotError(f"external snapshot has no usable nodes for {country}")
        counts["usable"] = len(names)
        country_nodes[country] = names
        source_counts.append(counts)
    totals = {key: sum(source[key] for source in source_counts)
              for key in ("bytes", "downloaded", "rejected", "deduplicated", "embedded")}
    totals["usable"] = len(nodes)
    metadata = {"countries": countries, "protocol_mode": protocol_mode, "country_nodes": country_nodes,
                "source_urls": [entry["url"] for entry in source_counts], "source_counts": source_counts,
                "totals": totals, "connectivity_tested": False, "country_locations_verified": False}
    return nodes, metadata
