#!/usr/bin/env python3
"""Manually approve one recovery. Never scheduled or called by generation."""
import argparse
import os
from pathlib import Path
import sys

from generate import read_json
from warp_generator import ConfigError, require, write_outputs
from warp_health import MihomoProbe, report_text
from warp_recovery import CLOUDFLARE_TERMS, UsqueRegistrar, recover_account


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True, type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--mihomo-bin", required=True, type=Path)
    parser.add_argument("--usque-bin", type=Path)
    parser.add_argument("--curl-bin", default="curl")
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--run-live-check", action="store_true")
    parser.add_argument("--approve-new-registration", action="store_true", help="approve ONE new Cloudflare device registration if current evidence qualifies")
    parser.add_argument("--accept-cloudflare-terms", action="store_true", help=CLOUDFLARE_TERMS)
    parser.add_argument("--resume-pending", action="store_true", help="recheck existing staged candidate without a new registration")
    args = parser.parse_args(argv)
    if not args.run_live_check:
        parser.error("--run-live-check is required")
    try:
        require(os.name == "posix", "manual recovery currently requires POSIX private-file permissions")
        require(os.environ.get("GITHUB_ACTIONS") != "true", "recovery in GitHub Actions is not enabled; use an explicitly approved private environment")
        require(args.resume_pending or args.usque_bin is not None, "--usque-bin is required for a new registration")
        settings = read_json(args.settings.read_text(encoding="utf-8"), "settings") if args.settings else None
        registrar = UsqueRegistrar(args.usque_bin) if args.usque_bin else None
        report = recover_account(args.account, settings, MihomoProbe(args.mihomo_bin, args.curl_bin), registrar,
            output_directory=args.output, state_directory=args.state_dir,
            approve_registration=args.approve_new_registration, accept_terms=args.accept_cloudflare_terms,
            resume_pending=args.resume_pending, protected_paths=[args.settings] if args.settings else [])
        write_outputs({"recovery.json": report_text(report)}, args.output,
                      [args.account, *([args.settings] if args.settings else [])])
        print(f"Recovery: {report['status']}; replaced: {report['account_replaced']}")
        if report.get("registration_attempted"):
            print(f"Registration was attempted with Cloudflare terms acceptance: {CLOUDFLARE_TERMS}")
        return 0 if report["status"] in ("replaced", "unchanged") else 4
    except (ConfigError, OSError, UnicodeError) as error:
        message = str(error) if isinstance(error, ConfigError) else "check input, tool paths and private recovery journal"
        print(f"Recovery failed: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
