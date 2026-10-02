#!/usr/bin/env python3
"""Fail closed before a manually requested workflow can access account secrets."""
import os
import sys


class GateError(ValueError):
    pass


def check_gate(env):
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or env.get("GITHUB_RUN_ATTEMPT") != "1":
        raise GateError("Use a new Run workflow request; automatic triggers and re-runs are disabled.")
    mode = env.get("GENERATION_MODE")
    destination = env.get("OUTPUT_DESTINATION")
    if mode not in {"sample", "account", "external-only"} or destination not in {"artifact", "repository"}:
        raise GateError("Choose a supported generation mode and output destination.")
    if destination == "repository":
        if env.get("GITHUB_REF_TYPE") != "branch" or env.get("CONFIRM_PUBLISH_OUTPUTS") != "true":
            raise GateError("Repository publication requires a branch and explicit confirmation for this run.")
    if mode == "account":
        if env.get("GENERATION_ENABLED") != "true":
            raise GateError("Account generation requires WARP_GENERATION_ENABLED=true and an existing account Secret.")
        if destination == "artifact" and (env.get("REPOSITORY_PRIVATE") != "true" or env.get("CONFIRM_PRIVATE_ARTIFACT") != "true"):
            raise GateError("Account artifact downloads require a private repository and private-artifact confirmation.")


def main():
    try:
        check_gate(os.environ)
    except GateError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2
    if os.environ["OUTPUT_DESTINATION"] == "repository":
        print("::warning::Confirmed repository publication: generated keys may be publicly reusable and remain in Git history, forks and caches. Removing a file does not revoke its keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
