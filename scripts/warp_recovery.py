"""Manually gated replacement: diagnose, stage, validate, back up, activate.

No caller in the offline generator or GitHub workflow invokes this module.
Registration is never retried automatically, including after an uncertain result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from generate import read_json
from warp_generator import ConfigError, generate, normalize_account, require
from warp_health import check_account

CLOUDFLARE_TERMS = "https://www.cloudflare.com/application/terms/"


def private_write(path, data):
    path = Path(path)
    require(not path.is_symlink(), "refusing to overwrite a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=".warp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class UsqueRegistrar:
    """Called only after a fresh explicit approval AND terms acceptance gate."""
    def __init__(self, binary, timeout=120):
        self.binary = str(Path(binary).resolve())
        self.timeout = timeout

    def __call__(self, destination):
        destination = Path(destination)
        require(not destination.is_symlink() and not destination.exists(),
                "candidate already exists or is a symlink; registration refused")
        destination = destination.resolve()
        # Upstream Usque --accept-tos creates/registers a device and enrolls its key.
        # Suppress upstream logs; neither tokens nor output are copied to a report.
        subprocess.run([self.binary, "--config", str(destination), "register", "--accept-tos",
                        "--name", "warp-manual-recovery"], cwd=destination.parent,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=self.timeout, check=True, umask=0o077)
        require(destination.is_file() and not destination.is_symlink(), "registration did not save a regular candidate file")
        os.chmod(destination, 0o600)


def install_candidate(account_path, candidate_bytes, outputs, output_directory, state_directory, original_bytes, protected_paths=()):
    """Back up all affected files, roll back ordinary errors, retain crash recovery."""
    account_path, destination, state = Path(account_path), Path(output_directory), Path(state_directory)
    require(not destination.is_symlink(), "output directory must not be a symlink")
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(destination, 0o700)
    targets = [(account_path, candidate_bytes)] + [(destination / name, value.encode("utf-8")) for name, value in outputs.items()]
    protected = {Path(path).resolve() for path in protected_paths}
    require(not any(path.resolve() in protected for path, _ in targets), "replacement would overwrite a protected input")
    require(len({path.resolve() for path, _ in targets}) == len(targets), "account and output paths overlap")
    require(all(not path.is_symlink() and (not path.exists() or path.is_file()) for path, _ in targets),
            "replacement targets must be regular files")
    require(account_path.read_bytes() == original_bytes, "account changed since diagnosis; replacement stopped")
    backup = Path(tempfile.mkdtemp(prefix="rollback-", dir=state))
    previous = []
    entries = []
    for index, (path, _) in enumerate(targets):
        content = path.read_bytes() if path.exists() else None
        previous.append(content)
        if content is not None:
            private_write(backup / f"{index}.bin", content)
        entries.append({"target": str(path.resolve()), "backup": f"{index}.bin" if content is not None else None})
    # This private journal makes a process/power interruption recoverable; it is
    # not claimed to be a filesystem-wide atomic transaction.
    journal = {"stage": "installing", "files": entries}
    private_write(backup / "journal.json", json.dumps(journal, indent=2))
    installed = []
    try:
        for index, (path, content) in enumerate(targets):
            private_write(path, content)
            installed.append(index)
        journal["stage"] = "activated"
        private_write(backup / "journal.json", json.dumps(journal, indent=2))
    except BaseException:
        # Best effort immediate rollback; backups/journal remain if storage itself
        # also prevents rollback. No second registration is attempted.
        failed = False
        for index in reversed(installed):
            path = targets[index][0]
            try:
                if previous[index] is None:
                    path.unlink(missing_ok=True)
                else:
                    private_write(path, previous[index])
            except (OSError, ConfigError):
                failed = True
        journal["stage"] = "rollback_needs_review" if failed else "rolled_back"
        private_write(backup / "journal.json", json.dumps(journal, indent=2))
        raise
    return backup.name


def recover_account(account_path, options, probe, registrar, *, output_directory="outputs", state_directory=None,
                    approve_registration=False, accept_terms=False, resume_pending=False, check_options=None, protected_paths=()):
    require(all(type(value) is bool for value in (approve_registration, accept_terms, resume_pending)),
            "recovery approvals must be explicit booleans")
    account_path = Path(account_path)
    require(account_path.is_file() and not account_path.is_symlink(), "account must be an existing regular file")
    original_bytes = account_path.read_bytes()
    original = read_json(original_bytes.decode("utf-8"), "account")
    # Reject incompatible settings/overlapping paths before any registration.
    expected_outputs = generate(original, options)
    destination = Path(output_directory)
    require(not destination.is_symlink() and (not destination.exists() or destination.is_dir()),
            "output must be a regular directory")
    require(not account_path.resolve().is_relative_to(destination.resolve()),
            "raw account must be stored outside the output directory")
    protected = {Path(path).resolve() for path in protected_paths}
    for name in [*expected_outputs, "recovery.json"]:
        target = destination / name
        require(not target.is_symlink() and (not target.exists() or target.is_file())
                and target.resolve() != account_path.resolve() and target.resolve() not in protected,
                "output path overlaps an account or symlink")
    check_options = check_options or {}
    current = check_account(original, options, probe, **check_options)
    report = {"schema_version": 1, "status": "unchanged", "current_health": current,
              "registration_attempted": False, "account_replaced": False}
    if current["status"] != "auth_failure":
        report["reason"] = "health_evidence_does_not_justify_replacement"
        return report
    if not resume_pending and not (approve_registration and accept_terms):
        report.update(status="approval_required", reason="new_registration_and_terms_need_explicit_approval")
        return report
    require(resume_pending or callable(registrar), "an explicit registration adapter is required")
    state = Path(state_directory) if state_directory else account_path.parent / ".warp-recovery"
    require(not state.is_symlink(), "recovery state directory must not be a symlink")
    require(not state.resolve().is_relative_to(destination.resolve())
            and not destination.resolve().is_relative_to(state.resolve()),
            "state and output directories must be separate, not nested")
    require(not any(path.is_relative_to(state.resolve()) for path in protected | {account_path.resolve()}),
            "inputs must be stored outside the recovery state directory")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    pending = state / "pending-account.json"
    marker = state / "registration-started.json"
    lock = state / "operation.lock"
    require(account_path.resolve() not in {pending.resolve(), marker.resolve(), lock.resolve()}, "account path overlaps recovery state")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ConfigError("recovery lock exists; inspect interrupted operation before retrying") from None
    os.close(descriptor)
    try:
        require(account_path.read_bytes() == original_bytes, "account changed during diagnosis; retry after review")
        if resume_pending:
            require(pending.is_file() and not pending.is_symlink(), "no regular pending candidate to resume")
        else:
            if marker.exists() or pending.exists() or marker.is_symlink() or pending.is_symlink():
                report.update(status="pending_review", reason="previous_registration_may_exist_no_automatic_retry")
                return report
            # A marker survives uncertain registration so a later run cannot
            # silently create yet another account.
            private_write(marker, json.dumps({"stage": "registration_started", "automatic_retry_allowed": False}))
            report["registration_attempted"] = True
            try:
                registrar(pending)
            except (OSError, subprocess.SubprocessError, ConfigError):
                report.update(status="registration_uncertain", reason="inspect_pending_state_before_any_new_registration")
                return report
        activation_started = False
        try:
            require(pending.is_file() and not pending.is_symlink(), "candidate is not a regular file")
            candidate_bytes = pending.read_bytes()
            candidate = read_json(candidate_bytes.decode("utf-8"), "candidate")
            require(normalize_account(candidate)["private-key"] != normalize_account(original)["private-key"],
                    "candidate did not change the account key")
            outputs = generate(candidate, options)
            candidate_health = check_account(candidate, options, probe, **check_options)
            report["candidate_health"] = candidate_health
            if candidate_health["status"] != "healthy":
                report.update(status="candidate_not_healthy", reason="current_account_preserved_candidate_kept_for_review")
                return report
            activation_started = True
            backup = install_candidate(account_path, candidate_bytes, outputs, output_directory, state, original_bytes, protected_paths)
        except (ConfigError, OSError, UnicodeError):
            report.update(status="activation_failed", reason="inspect_private_rollback_journal_and_pending_candidate")
            if activation_started:
                report.update(account_replaced=None, files_require_review=True)
            return report
        report.update(status="replaced", account_replaced=True, backup_directory=backup,
                      reason="candidate_verified_before_activation")
        try:
            pending.unlink()
            marker.unlink(missing_ok=True)
        except OSError:
            report["cleanup_requires_review"] = True
        return report
    finally:
        lock.unlink(missing_ok=True)
