"""Opt-in tunnel diagnostics. Reports contain categories, never raw logs/trace IPs.

Unlike the offline generator, MihomoProbe intentionally accesses the network when
called. Tests inject a probe and must not call the live implementation.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

from warp_generator import build_nodes, integer, normalize_settings, require, yaml_text, write_outputs

TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
AUTH_PATTERNS = (
    r"login failed! please double-check if your tls key and cert is enrolled in the cloudflare access service",
    r"connect-ip: server responded with (?:401|403)\b",
    r"failed to dial connect-ip: (?:401|403)\b",
    r"crypto_error 0x131 \(remote\): tls: access denied",
)


def classify_probe(returncode, http_status=0, body="", core_log=""):
    # A normal website 401/403 is not evidence of WARP account rejection.
    if returncode == 0 and http_status == 200:
        fields = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
        if fields.get("warp") in ("on", "plus"):
            return {"outcome": "healthy", "evidence": "warp_trace_verified"}
        return {"outcome": "unknown", "evidence": "trace_did_not_verify_warp"}
    logs = core_log.lower()
    if any(re.search(pattern, logs) for pattern in AUTH_PATTERNS):
        return {"outcome": "auth_rejected", "evidence": "explicit_tunnel_auth_rejection"}
    if "too many requests" in logs or http_status == 429 or 500 <= http_status <= 599:
        return {"outcome": "transient", "evidence": "service_or_rate_limit_response"}
    if returncode in (5, 6, 7, 28) or any(marker in logs for marker in (
            "network is unreachable", "no route to host", "i/o timeout", "context deadline exceeded",
            "connection timed out", "connection refused", "no such host")):
        return {"outcome": "network", "evidence": "connectivity_or_timeout"}
    return {"outcome": "unknown", "evidence": "unclassified_failure"}


def summarize(attempts):
    outcomes = {row["outcome"] for row in attempts}
    if "healthy" in outcomes:
        status = "healthy"
    elif attempts and outcomes == {"auth_rejected"}:
        counts = {}
        for row in attempts:
            counts[row["endpoint"]] = counts.get(row["endpoint"], 0) + 1
        status = "auth_failure" if len(counts) >= 2 and min(counts.values()) >= 2 else "unknown"
    elif outcomes == {"network"}:
        status = "network"
    elif outcomes and outcomes <= {"network", "transient"}:
        status = "transient"
    else:
        status = "unknown"
    return {"schema_version": 1, "status": status, "attempts": attempts,
            "expiry_proven": False, "manual_review_required": status == "auth_failure",
            "registration_performed": False,
            "recovered_after_retry": status == "healthy" and len(attempts) > 1}


def select_endpoints(nodes, maximum=3):
    """Prefer distinct hosts and address families, not ports on one host."""
    buckets = [[], [], []]
    seen = set()
    for node in nodes:
        server = node["server"]
        if server in seen:
            continue
        seen.add(server)
        bucket = 1 if ":" in server else (0 if re.fullmatch(r"[0-9.]+", server) else 2)
        buckets[bucket].append(node)
    return [node for row in itertools.zip_longest(*buckets) for node in row if node is not None][:maximum]


def check_account(account, options, probe, *, max_endpoints=3, retries=2, timeout=20, delay=2, sleep=time.sleep):
    integer(max_endpoints, "max_endpoints", 1, 10)
    integer(retries, "retries", 1, 5)
    integer(timeout, "timeout", 1, 60)
    integer(delay, "retry_delay", 0, 60)
    settings = normalize_settings(options)
    selected = select_endpoints(build_nodes(account, settings), max_endpoints)
    attempts = []
    for index, node in enumerate(selected, start=1):
        for attempt in range(1, retries + 1):
            observation = probe(node, timeout)
            # Accept only a bounded vocabulary; a subprocess cannot inject raw data
            # into a published report via its exception or output.
            require(observation.get("outcome") in ("healthy", "auth_rejected", "network", "transient", "unknown"),
                    "health probe returned an invalid outcome")
            require(observation.get("evidence") in (
                "warp_trace_verified", "trace_did_not_verify_warp", "explicit_tunnel_auth_rejection",
                "service_or_rate_limit_response", "connectivity_or_timeout", "unclassified_failure",
                "local_core_startup_failed", "local_tool_failed"), "health probe returned invalid evidence")
            attempts.append({"endpoint": index, "attempt": attempt,
                             "outcome": observation["outcome"], "evidence": observation["evidence"]})
            if observation["outcome"] == "healthy":
                return summarize(attempts)
            if attempt < retries:
                sleep(delay)
    return summarize(attempts)


def probe_config(node, port):
    # One outbound, one MATCH rule, no DIRECT fallback, providers, subscriptions,
    # controller, health groups or optional background downloads.
    return {"mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
            "mode": "rule", "log-level": "warning", "ipv6": True,
            "dns": {"enable": False}, "proxies": [node], "rules": [f"MATCH,{node['name']}"]}


def wait_ready(process, port, timeout):
    deadline = time.monotonic() + min(timeout, 10)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return process.poll() is None
        except OSError:
            time.sleep(0.1)
    return False


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


class MihomoProbe:
    def __init__(self, mihomo_binary, curl_binary="curl"):
        self.binary = str(Path(mihomo_binary).resolve())
        self.curl = str(curl_binary)

    def __call__(self, node, timeout):
        with tempfile.TemporaryDirectory(prefix="warp-health-") as directory, tempfile.TemporaryFile() as log:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            write_outputs({"config.yaml": yaml_text(probe_config(node, port))}, directory)
            process = None
            try:
                process = subprocess.Popen([self.binary, "-d", directory, "-f", str(Path(directory) / "config.yaml")],
                    cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                if not wait_ready(process, port, timeout):
                    return {"outcome": "unknown", "evidence": "local_core_startup_failed"}
                command = [self.curl, "--disable", "--silent", "--show-error", "--fail-with-body",
                    "--proxy", f"http://127.0.0.1:{port}", "--noproxy", "", "--retry", "0",
                    "--connect-timeout", str(timeout), "--max-time", str(timeout),
                    "--max-filesize", "65536", "--write-out", "\n%{http_code}", TRACE_URL]
                try:
                    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                            text=True, timeout=timeout + 2)
                    body, _, status_text = result.stdout.rpartition("\n")
                    http_status = int(status_text) if status_text.isdigit() else 0
                    returncode = result.returncode
                except subprocess.TimeoutExpired:
                    body, http_status, returncode = "", 0, 28
                log.seek(0, 2)
                length = log.tell()
                log.seek(max(0, length - 524288))
                logs = log.read().decode("utf-8", errors="replace")
                return classify_probe(returncode, http_status, body, logs)
            except OSError:
                return {"outcome": "unknown", "evidence": "local_tool_failed"}
            finally:
                if process is not None:
                    stop_process(process)


def report_text(report):
    return json.dumps(report, indent=2) + "\n"
