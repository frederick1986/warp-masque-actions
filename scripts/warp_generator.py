"""Offline WARP exports. This module never registers, downloads or connects."""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
import uuid

import yaml

from external_providers import (ExternalProviderError, augment_full_config, direct_only_config,
                                normalize_provider_settings, provider_targets)
from masque_defaults import AI_DOMAINS, DEFAULT_SNI, PORTS, RULESETS, V4, V6

SELECT = "🚀 节点选择"
AUTO = "♻️ 自动选择"
FALLBACK = "🔄 故障转移"
MANUAL = "☑️ 手动切换"
AI = "🤖 AI服务"
AI_AUTO = "🤖 AI自动选择"
DIRECT = "🎯 全球直连"
FINAL = "🐟 漏网之鱼"
CHATGPT_DOMAINS = ("chatgpt.com", "openai.com", "oaistatic.com", "oaiusercontent.com")
TARGETS = {"WARP": AUTO, "PROXY": SELECT, "AI": AI, "DIRECT": "DIRECT", "REJECT": "REJECT"}
FORMATS = ("mihomo", "provider", "shadowrocket", "singbox-local", "vless-local")
DEFAULTS = {
    "endpoint_source": "curated", "endpoints": None, "family": "dual",
    "ports": list(PORTS), "sni": DEFAULT_SNI, "mtu": 1280,
    "dns": ["1.1.1.1", "2606:4700:4700::1111"], "remote_dns": True,
    "network": "quic", "max_nodes": 500, "ruleset_profile": "acl4ssr",
    "chatgpt_route": "AI", "other_ai_route": "AI", "custom_ip_rules": [],
    "ai_health_url": None, "health_url": "https://www.gstatic.com/generate_204",
    "health_interval": 300, "formats": ["mihomo", "provider", "shadowrocket"],
    "bridge": {},
    "external_providers": {},
}


class ConfigError(ValueError):
    """A safe error containing field names, never account values."""


def require(condition, message):
    if not condition:
        raise ConfigError(message)


def integer(value, field, minimum, maximum):
    require(type(value) is int and minimum <= value <= maximum,
            f"{field} must be an integer from {minimum} to {maximum}")
    return value


def unique(values):
    return list(dict.fromkeys(values))


def host(value, field="endpoints"):
    require(isinstance(value, str) and bool(value.strip()), f"{field} must contain a host")
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        address = ipaddress.ip_address(value)
        require("%" not in value, f"{field} must not contain a zone identifier")
        return str(address)
    except ValueError:
        pass
    require(len(value) <= 253 and not re.fullmatch(r"[0-9.]+", value)
            and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in value.rstrip(".").split(".")),
            f"{field} must contain IP addresses or hostnames, without ports or URLs")
    return value.rstrip(".").lower()


def address(value, version, field):
    require(isinstance(value, str), f"{field} must be an IP address or CIDR")
    try:
        result = ipaddress.ip_interface(value.strip())
    except ValueError:
        raise ConfigError(f"{field} must be an IP address or CIDR") from None
    require(result.version == version, f"{field} has the wrong address family")
    return str(result)


def key_body(value, field):
    require(isinstance(value, str) and bool(value.strip()), f"account is missing {field}")
    # Support Usque base64 DER and PEM without copying device IDs/access tokens.
    body = "".join(line.strip() for line in value.strip().splitlines()
                   if not line.strip().startswith("-----"))
    try:
        require(bool(base64.b64decode(body, validate=True)), f"{field} is empty")
    except (binascii.Error, ValueError):
        raise ConfigError(f"{field} must be base64 DER or PEM") from None
    return body


def normalize_account(account):
    require(isinstance(account, dict), "account must be a JSON object")
    result = {"private-key": key_body(account.get("private_key"), "private_key"),
              "public-key": key_body(account.get("endpoint_pub_key"), "endpoint_pub_key")}
    for field, version, output in (("ipv4", 4, "ip"), ("ipv6", 6, "ipv6")):
        if account.get(field):
            result[output] = address(account[field], version, field)
    require("ip" in result or "ipv6" in result, "account needs ipv4 or ipv6")
    return result


def validate_url(value, field):
    require(isinstance(value, str), f"{field} must be an HTTP(S) URL")
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
        _ = parsed.port
    except ValueError:
        valid = False
    require(valid and not any(c.isspace() for c in value), f"{field} must be an HTTP(S) URL without credentials")
    return value


def normalize_settings(settings=None):
    settings = {} if settings is None else settings
    require(isinstance(settings, dict), "settings must be a JSON object")
    require(not set(settings) - set(DEFAULTS), "settings contains unknown fields; see examples/generator.json")
    result = {**DEFAULTS, **settings}
    try:
        result["external_providers"] = normalize_provider_settings(result["external_providers"])
    except ExternalProviderError as error:
        raise ConfigError(str(error)) from None
    targets = {**TARGETS, **provider_targets(result["external_providers"])}
    for field, allowed in (("endpoint_source", ("curated", "account")),
                           ("family", ("dual", "ipv4", "ipv6")),
                           ("network", ("quic", "h2")),
                           ("ruleset_profile", ("acl4ssr", "minimal"))):
        require(result[field] in allowed, f"{field} must be one of {', '.join(allowed)}")
    for field in ("ports", "dns", "formats"):
        require(isinstance(result[field], list) and bool(result[field]), f"{field} must be a non-empty list")
    result["ports"] = unique([integer(p, "ports", 1, 65535) for p in result["ports"]])
    for field, low, high in (("mtu", 1280, 9000), ("max_nodes", 1, 1000), ("health_interval", 30, 86400)):
        integer(result[field], field, low, high)
    require(type(result["remote_dns"]) is bool, "remote_dns must be boolean")
    result["sni"] = host(result["sni"], "sni")
    # Plain DNS IPs work consistently in all native exports, unlike client-specific URL syntax.
    normalized_dns = []
    for value in result["dns"]:
        try:
            require(isinstance(value, str) and "%" not in value, "dns must contain IP addresses")
            normalized_dns.append(str(ipaddress.ip_address(value)))
        except ValueError:
            raise ConfigError("dns must contain IP addresses") from None
    result["dns"] = unique(normalized_dns)
    require(all(isinstance(f, str) and f in FORMATS for f in result["formats"]), "formats contains an unsupported format")
    result["formats"] = unique(result["formats"])
    require(not (result["network"] == "h2" and "shadowrocket" in result["formats"]),
            "H2 is supported only in Mihomo/provider exports; remove shadowrocket from formats")
    validate_url(result["health_url"], "health_url")
    if result["ai_health_url"] is not None:
        validate_url(result["ai_health_url"], "ai_health_url")
    for field in ("chatgpt_route", "other_ai_route"):
        require(isinstance(result[field], str) and result[field] in targets,
                f"{field} must be a built-in target or an explicitly enabled external target")
    require(isinstance(result["custom_ip_rules"], list), "custom_ip_rules must be a list")
    rules = []
    for item in result["custom_ip_rules"]:
        require(isinstance(item, dict) and set(item) == {"cidr", "target"}, "custom_ip_rules entries need cidr and target")
        require(isinstance(item["target"], str) and item["target"] in targets, "custom_ip_rules target is unsupported")
        try:
            require(isinstance(item["cidr"], str) and "%" not in item["cidr"], "custom_ip_rules CIDR is invalid")
            network = ipaddress.ip_network(item["cidr"], strict=False)
        except ValueError:
            raise ConfigError("custom_ip_rules CIDR is invalid") from None
        kind = "IP-CIDR6" if network.version == 6 else "IP-CIDR"
        rules.append(f"{kind},{network},{targets[item['target']]},no-resolve")
    result["custom_ip_rules"] = unique(rules)
    bridge = result["bridge"]
    require(isinstance(bridge, dict) and not set(bridge) - {"socks_port", "mixed_port", "vless_port", "uuid"},
            "bridge contains unknown fields")
    result["bridge"] = {"socks_port": 1080, "mixed_port": 2080, "vless_port": 2081, **bridge}
    for field in ("socks_port", "mixed_port", "vless_port"):
        integer(result["bridge"][field], f"bridge.{field}", 1, 65535)
    if any(f in result["formats"] for f in ("singbox-local", "vless-local")):
        ports = [result["bridge"][p] for p in ("socks_port", "mixed_port", "vless_port")]
        require(len(set(ports)) == len(ports), "bridge ports must be different")
    if "vless-local" in result["formats"]:
        try:
            result["bridge"]["uuid"] = str(uuid.UUID(result["bridge"].get("uuid", "")))
        except (ValueError, AttributeError, TypeError):
            raise ConfigError("vless-local requires your existing bridge.uuid; no credential is generated") from None
    return result


def endpoint_pairs(account, settings):
    endpoints = settings["endpoints"]
    if endpoints is None:
        if settings["endpoint_source"] == "account":
            prefix = "endpoint_h2_" if settings["network"] == "h2" else "endpoint_"
            endpoints = [account.get(prefix + family) for family in ("v4", "v6") if account.get(prefix + family)]
        else:
            require(settings["network"] == "quic", "H2 needs account endpoints or explicit endpoints")
            endpoints = V4 + V6
    require(isinstance(endpoints, list) and bool(endpoints), "endpoints must be a non-empty list")
    endpoints = unique([host(value) for value in endpoints])
    if settings["family"] != "dual":
        family = 4 if settings["family"] == "ipv4" else 6
        filtered = []
        for endpoint in endpoints:
            try:
                if ipaddress.ip_address(endpoint).version == family:
                    filtered.append(endpoint)
            except ValueError:
                # Hostnames remain; ip-version on the node pins DNS address selection.
                filtered.append(endpoint)
        endpoints = filtered
    require(bool(endpoints), "no endpoints remain after family filtering")
    pairs = [(endpoint, port) for endpoint in endpoints for port in settings["ports"]]
    require(len(pairs) <= settings["max_nodes"], "endpoint/port combinations exceed max_nodes")
    return pairs


def build_nodes(account, settings):
    credentials = normalize_account(account)
    nodes = []
    for server, port in endpoint_pairs(account, settings):
        node = {"name": f"WARP-{server}-{port}", "type": "masque", "server": server,
                "port": port, **credentials, "mtu": settings["mtu"], "udp": True,
                "sni": settings["sni"], "remote-dns-resolve": settings["remote_dns"]}
        if settings["remote_dns"]:
            node["dns"] = settings["dns"]
        if settings["network"] != "quic":
            node["network"] = settings["network"]
        if settings["family"] != "dual":
            node["ip-version"] = settings["family"]
        nodes.append(node)
    return nodes


def full_config(nodes, settings):
    targets = {**TARGETS, **provider_targets(settings["external_providers"])}
    names = [n["name"] for n in nodes]
    groups = [{"name": SELECT, "type": "select", "proxies": [AUTO, FALLBACK, MANUAL, "DIRECT"]},
              {"name": AUTO, "type": "url-test", "proxies": names, "url": settings["health_url"],
               "interval": settings["health_interval"], "tolerance": 50, "lazy": True},
              {"name": FALLBACK, "type": "fallback", "proxies": names,
               "url": settings["health_url"], "interval": settings["health_interval"]},
              {"name": MANUAL, "type": "select", "proxies": names},
              {"name": DIRECT, "type": "select", "proxies": ["DIRECT", SELECT, AUTO]},
              {"name": FINAL, "type": "select", "proxies": [SELECT, DIRECT, AUTO]}]
    ai_choices = [SELECT, AUTO, FALLBACK, MANUAL, "DIRECT"]
    if settings["ai_health_url"]:
        groups.append({"name": AI_AUTO, "type": "url-test", "proxies": names,
                       "url": settings["ai_health_url"], "interval": settings["health_interval"], "lazy": True})
        ai_choices.insert(0, AI_AUTO)
    groups.append({"name": AI, "type": "select", "proxies": ai_choices})
    # Explicit CIDR overrides win over both domain and upstream rule sets.
    rules = list(settings["custom_ip_rules"])
    rules += [f"DOMAIN-SUFFIX,{domain},{targets[settings['chatgpt_route']]}" for domain in CHATGPT_DOMAINS]
    rules += [f"DOMAIN-SUFFIX,{domain},{targets[settings['other_ai_route']]}"
              for domain in unique(AI_DOMAINS) if domain not in CHATGPT_DOMAINS]
    providers = {}
    if settings["ruleset_profile"] == "acl4ssr":
        defined = {group["name"] for group in groups}
        for index, (group, url) in enumerate(RULESETS):
            if group not in defined:
                if group in ("🛑 全球拦截", "🍃 应用净化"):
                    choices = ["REJECT", "DIRECT"]
                elif group in ("Ⓜ️ 微软服务", "🍎 苹果服务"):
                    choices = [DIRECT, SELECT, AUTO]
                elif group == "🌍 国外媒体":
                    choices = [SELECT, AUTO, FALLBACK, DIRECT]
                elif group in ("📲 电报信息", "📢 谷歌FCM"):
                    choices = [SELECT, DIRECT, AUTO]
                else:
                    choices = [SELECT, AUTO, FALLBACK, MANUAL, "DIRECT"]
                groups.append({"name": group, "type": "select", "proxies": choices})
                defined.add(group)
            tag = f"rule{index:02d}"
            providers[tag] = {"type": "http", "behavior": "classical", "format": "text",
                              "interval": 86400, "url": url, "path": f"./ruleset/{tag}.list"}
            target = targets[settings["other_ai_route"]] if group == AI else group
            rules.append(f"RULE-SET,{tag},{target}")
    # No GeoIP database downloads are needed for the minimal profile.
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16"):
        rules.append(f"IP-CIDR,{cidr},DIRECT,no-resolve")
    for cidr in ("::1/128", "fc00::/7", "fe80::/10"):
        rules.append(f"IP-CIDR6,{cidr},DIRECT,no-resolve")
    if settings["ruleset_profile"] == "acl4ssr":
        rules.append(f"GEOIP,CN,{DIRECT}")
    rules.append(f"MATCH,{FINAL}")
    config = {"mixed-port": 7890, "allow-lan": False, "bind-address": "127.0.0.1",
              "mode": "rule", "log-level": "info", "ipv6": True,
              "dns": {"enable": True, "listen": "127.0.0.1:1053", "ipv6": True,
                      "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
                      "nameserver": settings["dns"]},
              "proxies": nodes, "proxy-groups": groups, "rules": rules}
    if providers:
        config["rule-providers"] = providers
    return augment_full_config(config, settings["external_providers"])


def yaml_text(value, sensitive=True):
    notice = "contains account key material. Keep private." if sensitive else "external providers; no WARP account keys."
    return f"# Generated offline; {notice}\n" + yaml.safe_dump(
        value, allow_unicode=True, sort_keys=False, width=120)


def shadowrocket_links(nodes, settings):
    require(all("ip" in n for n in nodes), "shadowrocket links require account ipv4")
    lines = []
    for node in nodes:
        server = f"[{node['server']}]" if ":" in node["server"] else node["server"]
        params = {"publicKey": node["public-key"], "privateKey": node["private-key"],
                  "ip": node["ip"].split("/")[0], "dns": ",".join(settings["dns"]), "udp": "1"}
        # Preserve the primary project's native URI dialect. SNI/MTU and routing are
        # not portable in this dialect; use the accompanying YAML for those options.
        lines.append(f"masque://{server}:{node['port']}?{urllib.parse.urlencode(params)}#"
                     + urllib.parse.quote(node["name"], safe=""))
    return lines


def local_bridge(settings, vless=False):
    bridge = settings["bridge"]
    inbound = {"type": "mixed", "tag": "local-in", "listen": "127.0.0.1", "listen_port": bridge["mixed_port"]}
    if vless:
        inbound.update(type="vless", listen_port=bridge["vless_port"], users=[{"uuid": bridge["uuid"]}])
    return {"log": {"level": "warn"}, "inbounds": [inbound],
            "outbounds": [{"type": "socks", "tag": "usque-local", "server": "127.0.0.1",
                           "server_port": bridge["socks_port"], "version": "5"}],
            "route": {"final": "usque-local"}}


def generate(account, options=None):
    settings = normalize_settings(options)
    nodes = build_nodes(account, settings)
    outputs = {}
    if "mihomo" in settings["formats"]:
        outputs["warp-masque.yaml"] = yaml_text(full_config(nodes, settings))
    if "provider" in settings["formats"]:
        outputs["warp-masque-provider.yaml"] = yaml_text({"proxies": nodes})
    if "shadowrocket" in settings["formats"]:
        outputs["warp-masque-shadowrocket.txt"] = "\n".join(shadowrocket_links(nodes, settings)) + "\n"
    if settings["external_providers"]["enabled"]:
        outputs["external-direct.yaml"] = yaml_text(direct_only_config(settings["external_providers"]), sensitive=False)
    for format_name, file_name, vless in (("singbox-local", "sing-box-usque-local.json", False),
                                          ("vless-local", "sing-box-vless-local.json", True)):
        if format_name in settings["formats"]:
            outputs[file_name] = json.dumps(local_bridge(settings, vless), indent=2) + "\n"
    outputs["manifest.json"] = json.dumps({"schema_version": 1, "node_count": len(nodes),
        "formats": settings["formats"], "files": list(outputs), "account_reused": True,
        "registered_accounts": 0, "live_connectivity_tested": False,
        "external_provider_count": 3 * len(settings["external_providers"]["countries"]) if settings["external_providers"]["enabled"] else 0,
        "external_nodes_materialized": False}, indent=2) + "\n"
    return outputs


def generate_external(options=None):
    """Generate a standalone external-provider config without reading an account."""
    settings = normalize_settings(options)
    try:
        config = direct_only_config(settings["external_providers"])
    except ExternalProviderError as error:
        raise ConfigError(str(error)) from None
    outputs = {"external-direct.yaml": yaml_text(config, sensitive=False)}
    outputs["manifest.json"] = json.dumps({"schema_version": 1, "node_count": 0,
        "formats": ["external-direct"], "files": list(outputs), "account_reused": False,
        "registered_accounts": 0, "live_connectivity_tested": False,
        "external_provider_count": len(config["proxy-providers"]), "external_nodes_materialized": False}, indent=2) + "\n"
    return outputs


def write_outputs(outputs, directory, protected_paths=()):
    directory = Path(directory)
    require(not directory.is_symlink(), "output directory must not be a symlink")
    targets = [directory / name for name in outputs]
    protected = {Path(path).resolve() for path in protected_paths}
    require(not any(path.is_symlink() or path.resolve() in protected for path in targets),
            "output would overwrite an input or symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    for path, content in zip(targets, outputs.values()):
        # Replacing a temp file also avoids following a pre-existing hard link.
        fd, temporary = tempfile.mkstemp(prefix=".warp-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
