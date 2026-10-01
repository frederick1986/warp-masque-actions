#!/usr/bin/env python3
"""Explicit live tunnel check. Never registers or replaces an account."""
import argparse
import json
from pathlib import Path
import sys

from generate import read_json
from warp_generator import ConfigError, write_outputs
from warp_health import MihomoProbe, check_account, report_text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True, type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--mihomo-bin", required=True, type=Path)
    parser.add_argument("--curl-bin", default="curl")
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--run-live-check", action="store_true", help="explicitly allow this tunnel check to contact Cloudflare")
    parser.add_argument("--max-endpoints", type=int, default=3)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)
    if not args.run_live_check:
        parser.error("--run-live-check is required; offline generation never runs this automatically")
    try:
        account = read_json(args.account.read_text(encoding="utf-8"), "account")
        settings = read_json(args.settings.read_text(encoding="utf-8"), "settings") if args.settings else None
        report = check_account(account, settings, MihomoProbe(args.mihomo_bin, args.curl_bin),
                               max_endpoints=args.max_endpoints, retries=args.retries, timeout=args.timeout)
        write_outputs({"health.json": report_text(report)}, args.output, [args.account, *([args.settings] if args.settings else [])])
        print(f"Tunnel check: {report['status']}; attempts: {len(report['attempts'])}; account unchanged")
        return {"healthy": 0, "network": 3, "transient": 3, "auth_failure": 4, "unknown": 5}[report["status"]]
    except (ConfigError, OSError, UnicodeError) as error:
        message = str(error) if isinstance(error, ConfigError) else "check input, tool paths and output permissions"
        print(f"Health check failed: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
