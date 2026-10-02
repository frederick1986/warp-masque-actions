#!/usr/bin/env python3
"""Build three self-contained WARP and WARP-chained inline-node configurations.

The CLI explicitly downloads selected donor country feeds. ``build_bundle`` is a
pure renderer: it only consumes a caller-supplied, validated snapshot and never
registers accounts, opens proxy tunnels, or fetches runtime subscriptions.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sys

import yaml

from external_providers import (FREE_AUTO, FREE_MANUAL, WARP_RELAY, WARP_RELAY_AUTO,
                                PROVIDER_URL, country_group_name, provider_targets)
from external_snapshot import SnapshotError
from warp_generator import (AI_AUTO, AUTO, FALLBACK, MANUAL, SELECT, ConfigError, build_nodes, full_config,
                            normalize_settings, require, unique, write_outputs)


WARP_DIRECT = "🛡️ WARP直连出口"
EXTERNAL_CHAIN = "🔗 WARP中转第三方"


COUNT_FIELDS = ("bytes", "downloaded", "rejected", "deduplicated", "embedded", "usable")


def _bundle_options(options=None):
    """This new command enables snapshots by default; legacy exports stay opt-in."""
    options = {} if options is None else deepcopy(options)
    require(isinstance(options, dict), "settings must be a JSON object")
    external = options.get("external_providers", {})
    require(isinstance(external, dict), "external_providers must be a JSON object")
    options["external_providers"] = {"enabled": True, **external}
    return options


def _prepare(account, options=None):
    """Validate every local input before the CLI is permitted to fetch feeds."""
    require(account is not None, "account must be a JSON object")
    settings = normalize_settings(_bundle_options(options))
    settings["external_providers"]["use_warp"] = True
    nodes = build_nodes(account, settings)
    pure = deepcopy(settings)
    external_targets = provider_targets(settings["external_providers"])
    mapped_names = set(external_targets.values())
    mappings = []
    if nodes:
        for field in ("chatgpt_route", "other_ai_route"):
            target = pure[field]
            if target in external_targets:
                pure[field] = "WARP"
                mappings.append({"field": field, "from": target, "to": "WARP"})
        rules = []
        for rule in pure["custom_ip_rules"]:
            fields = rule.split(",")
            if fields[2] in mapped_names:
                target = next(code for code, name in external_targets.items() if name == fields[2])
                fields[2] = AUTO
                mapping = {"field": "custom_ip_rules", "from": target, "to": "WARP"}
                if mapping not in mappings:
                    mappings.append(mapping)
            rules.append(",".join(fields))
        pure["custom_ip_rules"] = rules
    pure["external_providers"]["enabled"] = False
    pure["external_providers"]["use_warp"] = False
    return settings, nodes, pure, mappings


def _snapshot_parts(snapshot, countries):
    require(isinstance(snapshot, (tuple, list)) and len(snapshot) == 2,
            "enabled external exports require a validated snapshot")
    nodes, metadata = deepcopy(snapshot)
    require(isinstance(nodes, list) and bool(nodes) and isinstance(metadata, dict),
            "external snapshot must contain usable nodes and metadata")
    names = []
    for node in nodes:
        require(isinstance(node, dict) and isinstance(node.get("name"), str)
                and re.fullmatch(r"EXT-(?:US|JP|SG|HK|TW|KR)-[1-9][0-9]*", node["name"])
                and isinstance(node.get("type"), str) and node["type"] != "masque"
                and isinstance(node.get("server"), str) and bool(node["server"])
                and type(node.get("port")) is int and 1 <= node["port"] <= 65535,
                "external snapshot contains an invalid node")
        names.append(node["name"])
        # Never retain a donor chain, even if the snapshot was supplied directly.
        node["dialer-proxy"] = "DIRECT"
    require(len(names) == len(set(names)), "external snapshot contains duplicate names")
    country_nodes = metadata.get("country_nodes")
    require(isinstance(country_nodes, dict), "external snapshot needs country node metadata")
    selected = {}
    for country in countries:
        members = country_nodes.get(country)
        require(isinstance(members, list) and bool(members)
                and all(isinstance(name, str) and name in names for name in members),
                "each selected country must have usable snapshot nodes")
        selected[country] = unique(members)
    require(set(names) == {name for members in selected.values() for name in members},
            "external snapshot contains nodes outside the selected countries")
    sources = metadata.get("source_counts")
    require(isinstance(sources, list) and len(sources) == len(countries),
            "external snapshot needs per-source counts")
    source_counts = []
    for country in countries:
        matching = [source for source in sources
                    if isinstance(source, dict) and source.get("country") == country]
        require(len(matching) == 1, "external snapshot source counts are inconsistent")
        source = matching[0]
        require(all(type(source.get(field)) is int and source[field] >= 0 for field in COUNT_FIELDS),
                "external snapshot source counts must be non-negative integers")
        # Construct the allowlisted URL instead of copying untrusted metadata.
        source_counts.append({"country": country, "url": PROVIDER_URL.format(country=country),
                              **{field: source[field] for field in COUNT_FIELDS}})
    return nodes, selected, source_counts


def _inline(config, nodes, country_nodes):
    """Replace donor-provider selections with canonical concrete node names."""
    config = deepcopy(config)
    config.pop("proxy-providers", None)
    external = deepcopy(nodes)
    for node in external:
        node["dialer-proxy"] = WARP_RELAY
    config["proxies"].extend(external)
    for group in config["proxy-groups"]:
        if "use" not in group:
            continue
        names = list(group.get("proxies", []))
        for provider in group.pop("use"):
            match = re.fullmatch(r"FREE-([A-Z]{2})-(?:GENERAL|AI|STREAM)", provider)
            require(bool(match) and match[1] in country_nodes,
                    "generated provider reference cannot be materialized")
            names.extend(country_nodes[match[1]])
        group["proxies"] = unique(names)
        require(bool(group["proxies"]), "generated external group has no usable nodes")
    _validate_references(config)
    return config


def _combined_choices(config, settings):
    """Expose the two outbound modes clearly without changing WARP-only groups."""
    config["proxy-groups"].extend([
        {"name": WARP_DIRECT, "type": "select", "proxies": [AUTO, FALLBACK, MANUAL]},
        {"name": EXTERNAL_CHAIN, "type": "select", "proxies": [FREE_AUTO, FREE_MANUAL]
         + [country_group_name(country) for country in settings["external_providers"]["countries"]]},
    ])
    for group in config["proxy-groups"]:
        if group["name"] == SELECT:
            group["proxies"] = [WARP_DIRECT, EXTERNAL_CHAIN, "DIRECT"]
    _validate_references(config)
    return config


def _donor_config(combined):
    """Keep WARP solely as hidden transport for the donor's external exits."""
    config = deepcopy(combined)
    removed = {WARP_DIRECT, AUTO, FALLBACK, MANUAL, AI_AUTO}
    config["proxy-groups"] = [group for group in config["proxy-groups"] if group["name"] not in removed]
    for group in config["proxy-groups"]:
        group["proxies"] = unique([EXTERNAL_CHAIN if name in removed else name for name in group["proxies"]])
        if group["name"] == SELECT:
            group["proxies"] = [EXTERNAL_CHAIN]
        if group["name"] in (WARP_RELAY, WARP_RELAY_AUTO):
            group["hidden"] = True
    # An explicit WARP routing target also means the chained exit in donor mode.
    rules = []
    for rule in config["rules"]:
        fields = rule.split(",")
        index = -2 if fields[-1] == "no-resolve" else -1
        if fields[index] in removed:
            fields[index] = EXTERNAL_CHAIN
        rules.append(",".join(fields))
    config["rules"] = rules
    _validate_references(config)
    return config


def _validate_references(config):
    """Check both selector and dialer edges, so a hidden node chain cannot cycle."""
    require("proxy-providers" not in config, "bundle must not contain runtime proxy providers")
    groups = config["proxy-groups"]
    nodes = config["proxies"]
    definitions = groups + nodes
    names = [item["name"] for item in definitions]
    require(len(names) == len(set(names)), "generated proxy names must be unique")
    allowed = set(names) | {"DIRECT", "REJECT"}
    graph = {}
    for item in definitions:
        require("use" not in item, "bundle must not contain runtime proxy-provider references")
        edges = list(item.get("proxies", []))
        if "dialer-proxy" in item:
            edges.append(item["dialer-proxy"])
        require(all(edge in allowed for edge in edges), "generated proxy reference is missing")
        graph[item["name"]] = edges
    visiting, visited = set(), set()
    def visit(name):
        require(name not in visiting, "generated proxy graph contains a cycle")
        if name in visited or name not in graph:
            return
        visiting.add(name)
        for child in graph[name]:
            visit(child)
        visiting.remove(name)
        visited.add(name)
    for name in graph:
        visit(name)
    for rule in config["rules"]:
        fields = rule.split(",")
        target = fields[-2] if fields[-1] == "no-resolve" else fields[-1]
        require(target in allowed, "generated rule target is missing")


def _yaml(config):
    return "# Inline-node export. Contains WARP account key material; keep private.\n" + yaml.safe_dump(
        config, allow_unicode=True, sort_keys=False, width=120)


def build_bundle(account, options=None, snapshot=None):
    """Return standalone filename -> text, without fetching or changing inputs.

    FREE/country routes in the pure WARP file map to WARP auto-selection. The
    combined and donor files keep those external routes. Donor mode maps WARP
    routes to chained external exits; its WARP transport selectors are hidden. Only field/target names, never CIDRs or
    credentials, are recorded in the manifest's route-mapping disclosure. With no
    supplied snapshot (or external exports disabled), only pure WARP is rendered.
    """
    settings, warp, pure_settings, mappings = _prepare(account, options)
    configs = {}
    external, country_nodes, source_counts = [], {}, []
    configs["masque.yaml"] = full_config(warp, pure_settings)
    _validate_references(configs["masque.yaml"])
    if settings["external_providers"]["enabled"] and snapshot is not None:
        external, country_nodes, source_counts = _snapshot_parts(
            snapshot, settings["external_providers"]["countries"])
        combined = _combined_choices(_inline(full_config(warp, settings), external, country_nodes), settings)
        configs["usque-custom-pro.yaml"] = _donor_config(combined)
        configs["combined.yaml"] = combined
    outputs = {name: _yaml(config) for name, config in configs.items()}
    manifest = {
        "schema_version": 1,
        "files": list(outputs),
        "formats": list(configs),
        "node_count": len(warp) + len(external),
        "warp_node_count": len(warp),
        "external_node_count": len(external),
        "file_node_counts": {name: len(config["proxies"]) for name, config in configs.items()},
        "account_reused": True,
        "registered_accounts": 0,
        "connectivity_tested": False,
        "live_connectivity_tested": False,
        "external_nodes_materialized": bool(external),
        "external_provider_count": 0,
        "source_counts": source_counts,
        "source_totals": {**{field: sum(source[field] for source in source_counts) for field in COUNT_FIELDS},
                          "usable": len(external)},
        "country_locations_verified": False,
        "country_node_counts": {country: len(names) for country, names in country_nodes.items()},
        "pure_warp_route_mappings": mappings,
        "donor_warp_route": "WARP-chained external exit" if external else None,
    }
    outputs["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    return outputs


def _read_json(text, label):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise ConfigError(f"{label} must be valid JSON") from None


def load_snapshot(countries, protocol_mode="stable"):
    # Keep the renderer independent of networking and make CLI downloads mockable.
    from external_snapshot import load_snapshot as download_snapshot
    return download_snapshot(countries, protocol_mode=protocol_mode)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--account", type=Path, help="existing Usque JSON; never registered or modified")
    source.add_argument("--account-env", metavar="NAME", help="read existing account JSON from this environment variable")
    parser.add_argument("--settings", type=Path, help="non-secret JSON settings")
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--no-external", action="store_true", help="emit only pure masque.yaml and manifest, with no feed downloads")
    args = parser.parse_args(argv)
    try:
        options = _read_json(args.settings.read_text(encoding="utf-8"), "settings") if args.settings else None
        options = _bundle_options(options)
        account_text = args.account.read_text(encoding="utf-8") if args.account else os.environ.get(args.account_env)
        require(bool(account_text), "account environment variable is empty")
        account = _read_json(account_text, "account")
        require(account is not None, "account must be a JSON object")
        settings, _, _, _ = _prepare(account, options)
        snapshot = None
        if settings["external_providers"]["enabled"] and not args.no_external:
            try:
                snapshot = load_snapshot(settings["external_providers"]["countries"],
                                         protocol_mode=settings["external_providers"]["protocol_mode"])
            except SnapshotError as error:
                raise ConfigError(str(error)) from None
            except Exception:
                # Feed bodies, URLs, exception traces and credentials never enter logs.
                raise ConfigError("external snapshot download or validation failed") from None
        outputs = build_bundle(account, options, snapshot)
        write_outputs(outputs, args.output, [path for path in (args.account, args.settings) if path])
        manifest = json.loads(outputs["manifest.json"])
        print(f"Generated {manifest['warp_node_count']} WARP nodes; {manifest['external_node_count']} external nodes; "
              f"{len(outputs)} files; 0 registrations; connectivity not tested")
        return 0
    except (ConfigError, OSError, UnicodeError) as error:
        message = str(error) if isinstance(error, ConfigError) else "cannot read input or write output; check paths and permissions"
        print(f"Generation failed: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
