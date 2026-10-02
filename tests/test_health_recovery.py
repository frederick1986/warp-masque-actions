"""No live calls: synthetic accounts and injected probes/registrars only."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from warp_generator import ConfigError, build_nodes, normalize_settings
from warp_health import (MihomoProbe, TRACE_URL, check_account, classify_probe,
                         probe_config, select_endpoints, summarize)
from warp_recovery import UsqueRegistrar, install_candidate, private_write, recover_account

ACCOUNT = json.loads((ROOT / "tests/fixtures/synthetic-account.json").read_text())
NEW_ACCOUNT = {**ACCOUNT, "private_key": base64.b64encode(b"TEST_ONLY_DIFFERENT_INVALID_KEY").decode()}
OPTIONS = {"endpoint_source": "account", "ports": [443], "ruleset_profile": "minimal"}
CHECK_OPTIONS = {"sleep": lambda _: None, "delay": 0}
AUTH_LOG = "login failed! Please double-check if your tls key and cert is enrolled in the Cloudflare Access service"


def observation(outcome):
    evidence = {"healthy": "warp_trace_verified", "auth_rejected": "explicit_tunnel_auth_rejection",
                "network": "connectivity_or_timeout", "transient": "service_or_rate_limit_response",
                "unknown": "unclassified_failure"}[outcome]
    return {"outcome": outcome, "evidence": evidence}


def candidate_probe(node, timeout):
    return observation("healthy" if node["private-key"] == NEW_ACCOUNT["private_key"] else "auth_rejected")


class HealthTests(unittest.TestCase):
    def test_trace_requires_success_and_warp_marker(self):
        self.assertEqual(classify_probe(0, 200, "ip=192.0.2.1\nwarp=on\n")["outcome"], "healthy")
        self.assertEqual(classify_probe(0, 200, "warp=plus\n")["outcome"], "healthy")
        for code, status, text in ((0, 200, "warp=off"), (0, 200, "hello"), (22, 403, "warp=on")):
            self.assertNotEqual(classify_probe(code, status, text)["outcome"], "healthy")

    def test_only_explicit_tunnel_rejections_count_as_auth(self):
        for log in (AUTH_LOG, "failed to dial connect-ip: connect-ip: server responded with 403", "CRYPTO_ERROR 0x131 (remote): tls: access denied"):
            self.assertEqual(classify_probe(28, 0, core_log=log)["outcome"], "auth_rejected")
        for log in ("HTTP 403 at destination", "tls: bad certificate", "failed to parse private key", "login failed for another service"):
            self.assertNotEqual(classify_probe(22, 403, core_log=log)["outcome"], "auth_rejected")

    def test_network_transient_unknown_are_distinct(self):
        for code in (5, 6, 7, 28):
            self.assertEqual(classify_probe(code)["outcome"], "network")
        for status in (429, 500, 503):
            self.assertEqual(classify_probe(22, status)["outcome"], "transient")
        self.assertEqual(classify_probe(60)["outcome"], "unknown")

    def test_auth_threshold_needs_two_hosts_two_attempts_each(self):
        def rows(hosts, count):
            return [{"endpoint": host, "attempt": attempt, **observation("auth_rejected")}
                    for host in range(hosts) for attempt in range(count)]
        self.assertEqual(summarize(rows(1, 4))["status"], "unknown")
        self.assertEqual(summarize(rows(2, 1))["status"], "unknown")
        report = summarize(rows(2, 2))
        self.assertEqual(report["status"], "auth_failure")
        self.assertFalse(report["expiry_proven"])
        self.assertTrue(report["manual_review_required"])

    def test_mixed_auth_and_network_never_justifies_replacement(self):
        probe = Mock(side_effect=[observation("auth_rejected")] * 2 + [observation("network")] * 2)
        self.assertEqual(check_account(ACCOUNT, OPTIONS, probe, **CHECK_OPTIONS)["status"], "unknown")

    def test_retries_recover_without_replacing_anything(self):
        probe = Mock(side_effect=[observation("network"), observation("healthy")])
        report = check_account(ACCOUNT, OPTIONS, probe, **CHECK_OPTIONS)
        self.assertEqual(report["status"], "healthy")
        self.assertTrue(report["recovered_after_retry"])
        self.assertFalse(report["registration_performed"])
        self.assertEqual(probe.call_count, 2)

    def test_all_timeouts_are_network_not_expiry(self):
        report = check_account(ACCOUNT, OPTIONS, lambda *_: observation("network"), **CHECK_OPTIONS)
        self.assertEqual(report["status"], "network")
        self.assertFalse(report["manual_review_required"])

    def test_endpoint_selection_uses_hosts_and_families(self):
        nodes = build_nodes(ACCOUNT, normalize_settings({"ports": [443, 500]}))
        selected = select_endpoints(nodes, 3)
        self.assertEqual(len({n["server"] for n in selected}), 3)
        self.assertIn(":", selected[1]["server"])

    def test_reports_never_include_trace_or_logs(self):
        result = classify_probe(28, core_log=AUTH_LOG + " SECRET_TOKEN")
        text = json.dumps(result)
        self.assertNotIn("SECRET_TOKEN", text)
        self.assertNotIn("192.0.2.1", json.dumps(classify_probe(0, 200, "ip=192.0.2.1\nwarp=on")))

    def test_probe_config_has_no_direct_fallback_or_external_controller(self):
        node = build_nodes(ACCOUNT, normalize_settings(OPTIONS))[0]
        config = probe_config(node, 12345)
        self.assertEqual(config["rules"], [f"MATCH,{node['name']}"])
        self.assertEqual(config["bind-address"], "127.0.0.1")
        self.assertFalse(config["allow-lan"])
        self.assertNotIn("DIRECT", config["rules"])
        self.assertNotIn("proxy-providers", config)
        self.assertNotIn("external-controller", config)

    def test_live_probe_command_is_forced_through_local_proxy_and_cleaned_up(self):
        node = build_nodes(ACCOUNT, normalize_settings(OPTIONS))[0]
        process = MagicMock()
        process.poll.return_value = None
        socket_mock = MagicMock()
        socket_mock.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 12345)
        with patch("warp_health.socket.socket", return_value=socket_mock), patch("warp_health.subprocess.Popen", return_value=process), patch("warp_health.wait_ready", return_value=True), patch("warp_health.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "warp=on\nip=SECRET_IP\n\n200", "")) as run:
            result = MihomoProbe("/synthetic/mihomo")(node, 5)
        self.assertEqual(result["outcome"], "healthy")
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["curl", "--disable"])
        self.assertEqual(command[command.index("--noproxy") + 1], "")
        self.assertEqual(command[command.index("--proxy") + 1], "http://127.0.0.1:12345")
        self.assertEqual(command[-1], TRACE_URL)
        process.terminate.assert_called_once()

    def test_health_cli_requires_explicit_live_opt_in(self):
        import healthcheck
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            healthcheck.main(["--account", "does-not-exist", "--mihomo-bin", "unused"])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.account = self.root / "account.json"
        self.account.write_text(json.dumps(ACCOUNT))
        self.original = self.account.read_bytes()
        self.output = self.root / "outputs"
        self.state = self.root / ".warp-recovery"
        self.registrar = Mock(side_effect=lambda path: private_write(path, json.dumps(NEW_ACCOUNT)))

    def tearDown(self):
        self.temporary.cleanup()

    def recover(self, **kwargs):
        return recover_account(self.account, OPTIONS, kwargs.pop("probe", candidate_probe),
            kwargs.pop("registrar", self.registrar), output_directory=self.output, state_directory=self.state,
            check_options=CHECK_OPTIONS, **kwargs)

    def test_new_registration_requires_both_approval_and_terms(self):
        for approval, terms in ((False, False), (True, False), (False, True)):
            report = self.recover(approve_registration=approval, accept_terms=terms)
            self.assertEqual(report["status"], "approval_required")
        self.registrar.assert_not_called()
        self.assertEqual(self.account.read_bytes(), self.original)
        with self.assertRaises(ConfigError):
            self.recover(approve_registration="false", accept_terms="false")
        self.registrar.assert_not_called()

    def test_network_unknown_and_healthy_never_register(self):
        for outcome in ("network", "transient", "unknown", "healthy"):
            report = self.recover(probe=lambda *_: observation(outcome), approve_registration=True, accept_terms=True)
            self.assertEqual(report["status"], "unchanged")
        self.registrar.assert_not_called()

    def test_verified_candidate_activates_and_keeps_rollback(self):
        self.output.mkdir()
        (self.output / "warp-masque.yaml").write_text("old output")
        (self.output / "unrelated.txt").write_text("preserve me")
        report = self.recover(approve_registration=True, accept_terms=True)
        self.assertEqual(report["status"], "replaced")
        self.assertTrue(report["registration_attempted"])
        self.assertTrue(report["account_replaced"])
        self.registrar.assert_called_once()
        self.assertEqual(json.loads(self.account.read_text()), NEW_ACCOUNT)
        self.assertIn(NEW_ACCOUNT["private_key"], (self.output / "warp-masque.yaml").read_text())
        self.assertEqual((self.output / "unrelated.txt").read_text(), "preserve me")
        backup = self.state / report["backup_directory"]
        self.assertEqual((backup / "0.bin").read_bytes(), self.original)
        self.assertEqual(json.loads((backup / "journal.json").read_text())["stage"], "activated")
        self.assertFalse((self.state / "pending-account.json").exists())
        self.assertEqual(self.account.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(NEW_ACCOUNT["private_key"], json.dumps(report))

    def test_candidate_timeout_preserves_current_and_blocks_new_registration(self):
        def probe(node, _):
            return observation("network" if node["private-key"] == NEW_ACCOUNT["private_key"] else "auth_rejected")
        report = self.recover(probe=probe, approve_registration=True, accept_terms=True)
        self.assertEqual(report["status"], "candidate_not_healthy")
        self.assertEqual(self.account.read_bytes(), self.original)
        second = self.recover(approve_registration=True, accept_terms=True)
        self.assertEqual(second["status"], "pending_review")
        self.assertEqual(self.registrar.call_count, 1)
        resumed = self.recover(resume_pending=True)
        self.assertEqual(resumed["status"], "replaced")
        self.assertFalse(resumed["registration_attempted"])
        self.assertEqual(self.registrar.call_count, 1)

    def test_uncertain_registration_is_not_retried(self):
        registrar = Mock(side_effect=subprocess.TimeoutExpired("synthetic", 1))
        report = self.recover(registrar=registrar, approve_registration=True, accept_terms=True)
        self.assertEqual(report["status"], "registration_uncertain")
        again = self.recover(registrar=registrar, approve_registration=True, accept_terms=True)
        self.assertEqual(again["status"], "pending_review")
        self.assertEqual(registrar.call_count, 1)
        self.assertEqual(self.account.read_bytes(), self.original)

    def test_malformed_candidate_never_replaces_current(self):
        registrar = Mock(side_effect=lambda path: private_write(path, "INVALID_SECRET_JSON"))
        report = self.recover(registrar=registrar, approve_registration=True, accept_terms=True)
        self.assertEqual(report["status"], "activation_failed")
        self.assertEqual(self.account.read_bytes(), self.original)
        self.assertNotIn("INVALID_SECRET_JSON", json.dumps(report))

    def test_same_key_candidate_rejected(self):
        registrar = Mock(side_effect=lambda path: private_write(path, json.dumps(ACCOUNT)))
        report = self.recover(registrar=registrar, approve_registration=True, accept_terms=True)
        self.assertEqual(report["status"], "activation_failed")
        self.assertEqual(self.account.read_bytes(), self.original)

    def test_existing_lock_prevents_registration(self):
        self.state.mkdir()
        (self.state / "operation.lock").write_text("interrupted")
        with self.assertRaises(ConfigError):
            self.recover(approve_registration=True, accept_terms=True)
        self.registrar.assert_not_called()

    def test_dangling_candidate_symlink_never_registers_or_escapes_staging(self):
        self.state.mkdir()
        pending = self.state / "pending-account.json"
        escaped = self.root / "outside.json"
        pending.symlink_to(escaped)
        report = self.recover(approve_registration=True, accept_terms=True)
        self.assertEqual(report["status"], "pending_review")
        self.registrar.assert_not_called()
        self.assertFalse((self.state / "registration-started.json").exists())
        self.assertFalse(escaped.exists())
        with patch("warp_recovery.subprocess.run") as run, self.assertRaises(ConfigError):
            UsqueRegistrar("/synthetic/usque")(pending)
        run.assert_not_called()

    def test_settings_and_non_file_targets_are_rejected_before_registration(self):
        self.output.mkdir()
        settings = self.output / "manifest.json"
        settings.write_text("{}")
        with self.assertRaises(ConfigError):
            self.recover(approve_registration=True, accept_terms=True, protected_paths=[settings])
        settings.unlink()
        settings.mkdir()
        with self.assertRaises(ConfigError):
            self.recover(approve_registration=True, accept_terms=True)
        self.registrar.assert_not_called()
        self.assertEqual(self.account.read_bytes(), self.original)

    def test_protected_settings_cannot_be_used_for_report(self):
        self.output.mkdir()
        settings = self.output / "recovery.json"
        settings.write_text("{}")
        with self.assertRaises(ConfigError):
            self.recover(approve_registration=True, accept_terms=True, protected_paths=[settings])
        self.registrar.assert_not_called()

    def test_mid_install_failure_rolls_back_account_and_outputs(self):
        self.output.mkdir()
        self.state.mkdir()
        target = self.output / "warp-masque.yaml"
        target.write_text("old output")
        original_write = private_write
        def fail_one(path, content):
            if Path(path) == target and content == b"new output":
                raise OSError("synthetic storage failure")
            return original_write(path, content)
        with patch("warp_recovery.private_write", side_effect=fail_one), self.assertRaises(OSError):
            install_candidate(self.account, json.dumps(NEW_ACCOUNT).encode(), {"warp-masque.yaml": "new output"}, self.output, self.state, self.original)
        self.assertEqual(self.account.read_bytes(), self.original)
        self.assertEqual(target.read_text(), "old output")
        journal = next(self.state.glob("rollback-*/journal.json"))
        self.assertEqual(json.loads(journal.read_text())["stage"], "rolled_back")

    def test_registrar_is_bounded_and_has_no_logged_credentials(self):
        destination = self.root / "candidate.json"
        def mocked_run(command, **kwargs):
            private_write(destination, json.dumps(NEW_ACCOUNT))
            return subprocess.CompletedProcess(command, 0)
        with patch("warp_recovery.subprocess.run", side_effect=mocked_run) as run:
            UsqueRegistrar("/synthetic/usque")(destination)
        args, kwargs = run.call_args
        self.assertIn("--accept-tos", args[0])
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["umask"], 0o077)
        self.assertEqual(kwargs["timeout"], 120)

    def test_recovery_is_not_enabled_in_actions(self):
        import recover_account as cli
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch("sys.stderr", new_callable=io.StringIO), patch("recover_account.MihomoProbe") as probe:
            code = cli.main(["--account", str(self.account), "--mihomo-bin", "/synthetic/mihomo", "--usque-bin", "/synthetic/usque", "--run-live-check", "--approve-new-registration", "--accept-cloudflare-terms"])
        self.assertEqual(code, 2)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
