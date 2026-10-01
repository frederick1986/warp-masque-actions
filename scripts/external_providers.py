"""Offline, opt-in country providers adapted from the donor's free-egress path.

This module creates configuration dictionaries only. It never fetches a remote
subscription, reads account material, registers an account, or opens a tunnel.
The result targets Mihomo; its provider overrides are not classic Clash syntax.
"""
from __future__ import annotations

from copy import deepcopy

from masque_defaults import AI_DOMAINS

COUNTRIES = {
    "US": "🇺🇸", "JP": "🇯🇵", "SG": "🇸🇬",
    "HK": "🇭🇰", "TW": "🇹🇼", "KR": "🇰🇷",
}
PROVIDER_URL = ("https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/"
                "main/output/by-country/clash-{country}.yaml")
FREE_AUTO = "⚡ 免费落地自动"
FREE_MANUAL = "🌍 免费落地可手动"
FREE_AI = "🤖 AI自动优选"
FREE_STREAM = "🎬 流媒体自动优选"
WARP_RELAY = "WARP中转"
WARP_RELAY_AUTO = "🚀 WARP中转自动"
SELECT = "🚀 节点选择"
AI = "🤖 AI服务"
STREAM = "🌍 国外媒体"
DEFAULTS = {
    "enabled": False,
    "countries": ["US", "JP", "SG"],
    "use_warp": False,
    "scope": "ai-streaming",
    "protocol_mode": "stable",
}
SCOPES = ("ai-streaming", "ai-only", "streaming-only", "all-foreign")
# Retain donor stable mode, additionally excluding MASQUE in both modes.
STABLE_EXCLUSIONS = ("hysteria2", "tuic", "wireguard", "http", "https", "socks4", "socks5")
HEALTH = {
    "general": ("https://www.gstatic.com/generate_204", "204", 5000),
    "ai": ("https://chatgpt.com/", "200-399", 9000),
    "stream": ("https://www.netflix.com/", "200-399", 9000),
}
STREAMING_GROUPS = {"🎥 奈飞视频", STREAM, "Netflix", "Disney", "TikTok", "Spotify", "PrimeVideo", "HBO", "Emby"}
FOREIGN_GROUPS = {SELECT, "📲 电报信息", "📢 谷歌FCM", "📹 油管视频", "Ⓜ️ 微软服务", "🍎 苹果服务"}


class ExternalProviderError(ValueError):
    """Validation errors contain field names, never caller-provided values."""


def _require(condition, message):
    if not condition:
        raise ExternalProviderError(message)


def normalize_provider_settings(settings=None):
    settings = {} if settings is None else settings
    _require(isinstance(settings, dict), "external_providers must be a JSON object")
    _require(not set(settings) - set(DEFAULTS), "external_providers contains unknown fields")
    result = {**deepcopy(DEFAULTS), **deepcopy(settings)}
    for field in ("enabled", "use_warp"):
        _require(type(result[field]) is bool, f"external_providers.{field} must be boolean")
    _require(isinstance(result["scope"], str) and result["scope"] in SCOPES,
             "external_providers.scope must be ai-streaming, ai-only, streaming-only or all-foreign")
    _require(isinstance(result["protocol_mode"], str) and result["protocol_mode"] in ("stable", "all"),
             "external_providers.protocol_mode must be stable or all")
    countries = result["countries"]
    _require(isinstance(countries, list) and all(isinstance(c, str) and c in COUNTRIES for c in countries),
             "external_providers.countries must list supported country codes: US, JP, SG, HK, TW, KR")
    result["countries"] = list(dict.fromkeys(countries))
    _require(not result["enabled"] or result["countries"],
             "enabled external_providers needs at least one country")
    return result


def country_group_name(country):
    _require(isinstance(country, str) and country in COUNTRIES, "unsupported external provider country")
    return f"{COUNTRIES[country]} {country}"


def provider_targets(settings=None):
    """Expose only enabled country routes to the caller's CIDR/AI validation."""
    settings = normalize_provider_settings(settings)
    if not settings["enabled"]:
        return {}
    return {"FREE": FREE_AUTO, **{code: country_group_name(code) for code in settings["countries"]}}


def _provider_id(country, kind):
    return f"FREE-{country}-{kind.upper()}"


def _health(kind):
    url, expected, timeout = HEALTH[kind]
    return {"url": url, "expected-status": expected, "interval": 120, "timeout": timeout, "lazy": False}


def _providers(settings):
    result = {}
    excluded = ["masque"]
    if settings["protocol_mode"] == "stable":
        excluded.extend(STABLE_EXCLUSIONS)
    upstream = WARP_RELAY if settings["use_warp"] else "DIRECT"
    for country in settings["countries"]:
        for kind in HEALTH:
            result[_provider_id(country, kind)] = {
                "type": "http", "url": PROVIDER_URL.format(country=country),
                "path": f"./providers/free-{country}-{kind}.yaml",
                "interval": 3600, "size-limit": 8388608,
                "proxy": upstream, "exclude-type": "|".join(excluded),
                "health-check": {"enable": True, **_health(kind)},
                "override": {
                    "additional-prefix": f"{COUNTRIES[country]} {country} | ",
                    # Explicit DIRECT also replaces a chain supplied by the feed.
                    "dialer-proxy": upstream,
                },
            }
    return result


def _groups(settings):
    def automatic(name, kind, countries, tolerance=30):
        return {"name": name, "type": "url-test", **_health(kind),
                "tolerance": tolerance, "max-failed-times": 2,
                "use": [_provider_id(code, kind) for code in countries]}

    countries = settings["countries"]
    groups = [automatic(FREE_AUTO, "general", countries),
              automatic(FREE_AI, "ai", countries),
              automatic(FREE_STREAM, "stream", countries),
              {"name": FREE_MANUAL, "type": "select", "proxies": [FREE_AI, FREE_STREAM, FREE_AUTO],
               "use": [_provider_id(code, "general") for code in countries]}]
    groups.extend(automatic(country_group_name(code), "general", [code], 20) for code in countries)
    return groups


def _prepend_choices(group, choices):
    existing = group.get("proxies", [])
    # Preserve the primary project's intentional DIRECT-first Apple/Microsoft paths.
    count = 1 if existing and existing[0] in ("DIRECT", "🎯 全球直连", "REJECT") else 0
    group["proxies"] = list(dict.fromkeys(existing[:count] + choices + existing[count:]))


def augment_full_config(config, provider_settings=None):
    """Return a copy with external choices; never replace routing rules or WARP groups.

    use_warp chains both provider downloads and external node connections through
    a dedicated group containing only the config's inline MASQUE nodes. It cannot
    accidentally select an external node and form a cycle through a mixed group.
    """
    settings = normalize_provider_settings(provider_settings)
    _require(isinstance(config, dict), "base configuration must be an object")
    result = deepcopy(config)
    if not settings["enabled"]:
        return result
    groups = result.setdefault("proxy-groups", [])
    _require(isinstance(groups, list) and all(isinstance(g, dict) and isinstance(g.get("name"), str) for g in groups),
             "base configuration proxy-groups must contain named groups")
    providers = result.setdefault("proxy-providers", {})
    _require(isinstance(providers, dict), "base configuration proxy-providers must be an object")
    new_groups = _groups(settings)
    if settings["use_warp"]:
        warp = [node["name"] for node in result.get("proxies", [])
                if isinstance(node, dict) and node.get("type") == "masque" and isinstance(node.get("name"), str)]
        _require(bool(warp), "external_providers.use_warp requires inline MASQUE nodes")
        new_groups.append({"name": WARP_RELAY_AUTO, "type": "url-test", "hidden": True,
                           "proxies": warp, **_health("general"), "tolerance": 30})
        new_groups.append({"name": WARP_RELAY, "type": "select", "proxies": [WARP_RELAY_AUTO] + warp})
    existing_names = {g["name"] for g in groups} | {
        node.get("name") for node in result.get("proxies", []) if isinstance(node, dict)}
    _require(not existing_names.intersection(g["name"] for g in new_groups),
             "external provider group names conflict with the base configuration")
    new_providers = _providers(settings)
    _require(not set(providers).intersection(new_providers),
             "external provider names conflict with the base configuration")
    countries = [country_group_name(code) for code in settings["countries"]]
    scope = settings["scope"]
    for group in groups:
        if group.get("type") != "select":
            continue
        name = group["name"]
        if name in (AI, "AI") and scope in ("ai-streaming", "ai-only", "all-foreign"):
            _prepend_choices(group, [FREE_AI, FREE_MANUAL] + countries)
        elif name in STREAMING_GROUPS and scope in ("ai-streaming", "streaming-only", "all-foreign"):
            _prepend_choices(group, [FREE_STREAM, FREE_MANUAL] + countries)
        elif name in FOREIGN_GROUPS and scope == "all-foreign":
            _prepend_choices(group, [FREE_AUTO, FREE_MANUAL] + countries)
        elif name == SELECT:
            # Keep the original default, while making the new providers selectable.
            choices = group.get("proxies", [])
            group["proxies"] = list(dict.fromkeys(choices + [FREE_AUTO, FREE_MANUAL] + countries))
    groups.extend(new_groups)
    providers.update(new_providers)
    return result


def direct_only_config(provider_settings=None):
    """Standalone external-node config, with no account, MASQUE node or local bridge.

    The provider URLs remain remote: this does not promise that the live source
    contains usable nodes. Mihomo excludes type: masque and overrides chaining.
    """
    settings = normalize_provider_settings(provider_settings)
    _require(settings["enabled"], "external-direct export requires enabled external_providers")
    settings["use_warp"] = False
    countries = [country_group_name(code) for code in settings["countries"]]
    groups = [
        {"name": SELECT, "type": "select", "proxies": [FREE_AUTO, FREE_MANUAL] + countries + ["DIRECT"]},
        {"name": AI, "type": "select", "proxies": [SELECT, "DIRECT"]},
        {"name": STREAM, "type": "select", "proxies": [SELECT, "DIRECT"]},
    ]
    rules = []
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16"):
        rules.append(f"IP-CIDR,{cidr},DIRECT,no-resolve")
    for cidr in ("::1/128", "fc00::/7", "fe80::/10"):
        rules.append(f"IP-CIDR6,{cidr},DIRECT,no-resolve")
    for domain in dict.fromkeys(["chatgpt.com", "openai.com", "oaistatic.com", "oaiusercontent.com"] + AI_DOMAINS):
        rules.append(f"DOMAIN-SUFFIX,{domain},{AI}")
    for domain in ("netflix.com", "netflix.net", "nflxvideo.net", "nflximg.net", "nflxso.net", "nflxext.com",
                   "disneyplus.com", "dssott.com", "spotify.com", "scdn.co", "tiktok.com", "tiktokcdn.com",
                   "primevideo.com", "hbo.com", "max.com", "emby.media"):
        rules.append(f"DOMAIN-SUFFIX,{domain},{STREAM}")
    rules.append(f"MATCH,{SELECT}")
    config = {"mixed-port": 7890, "allow-lan": False, "bind-address": "127.0.0.1",
              "mode": "rule", "log-level": "info", "ipv6": True,
              "dns": {"enable": True, "listen": "127.0.0.1:1053", "ipv6": True,
                      "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
                      "nameserver": ["1.1.1.1", "2606:4700:4700::1111"]},
              "proxies": [], "proxy-groups": groups, "rules": rules}
    return augment_full_config(config, settings)
