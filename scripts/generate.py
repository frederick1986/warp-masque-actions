#!/usr/bin/env python3
"""Generate files from an existing Usque account without any network operations."""
import argparse
import json
import os
from pathlib import Path
import sys

from warp_generator import ConfigError, generate, write_outputs


def read_json(text, label):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise ConfigError(f"{label} must be valid JSON") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--account", type=Path, help="existing Usque JSON file; never registered or modified")
    source.add_argument("--account-env", metavar="NAME", help="read existing account JSON from this environment variable")
    parser.add_argument("--settings", type=Path, help="non-secret JSON settings")
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--check", action="store_true", help="validate/build in memory without writing key material")
    args = parser.parse_args(argv)
    try:
        if args.account:
            account_text = args.account.read_text(encoding="utf-8")
        else:
            account_text = os.environ.get(args.account_env)
            if not account_text:
                raise ConfigError("account environment variable is empty")
        account = read_json(account_text, "account")
        settings = read_json(args.settings.read_text(encoding="utf-8"), "settings") if args.settings else None
        outputs = generate(account, settings)
        if not args.check:
            write_outputs(outputs, args.output, [path for path in (args.account, args.settings) if path])
        manifest = json.loads(outputs["manifest.json"])
        # Never print private keys, token values, input JSON, or account addresses.
        verb = "Validated" if args.check else "Generated"
        print(f"{verb} {manifest['node_count']} nodes; {len(outputs)} files; 0 registrations")
        return 0
    except (ConfigError, OSError, UnicodeError) as error:
        # OSError paths and malformed source data can themselves contain secrets.
        message = str(error) if isinstance(error, ConfigError) else "cannot read input or write output; check paths and permissions"
        print(f"Generation failed: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
