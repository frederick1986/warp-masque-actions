#!/usr/bin/env python3
"""Compatibility entry point: gen_masque.py ACCOUNT_JSON OUTPUT_DIRECTORY.

For configurable generation use generate.py --account ... --settings ... .
Neither entry point registers accounts or accesses the network.
"""
import json
import sys

from generate import main as generate_main
from masque_defaults import AI_DOMAINS, PORTS, RULESETS, V4, V6  # legacy imports
from warp_generator import generate


def build(cfg):
    """Preserve the old (links, full_yaml, node_count) Python API."""
    outputs = generate(cfg)
    return (outputs["warp-masque-shadowrocket.txt"].splitlines(),
            outputs["warp-masque.yaml"], json.loads(outputs["manifest.json"])["node_count"])


def main():
    if len(sys.argv) != 3:
        print("Usage: gen_masque.py <existing-usque-config.json> <output-directory>", file=sys.stderr)
        return 2
    return generate_main(["--account", sys.argv[1], "--output", sys.argv[2]])


if __name__ == "__main__":
    raise SystemExit(main())
