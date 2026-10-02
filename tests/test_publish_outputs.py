"""Publication security regressions: synthetic files and local bare Git only.

These tests never register an account, contact a hosting service, or use real
credentials. Git's protocol allowlist additionally forbids network transports.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import publish_outputs as publisher
from publish_outputs import ALLOWED_FILES, PublicationError, publish_outputs, validate_outputs
from workflow_gate import GateError, check_gate

GIT_RUN = subprocess.run
EXPECTED_FILES = {
    "manifest.json", "warp-masque.yaml", "warp-masque-provider.yaml",
    "warp-masque-shadowrocket.txt", "external-direct.yaml",
    "sing-box-usque-local.json", "sing-box-vless-local.json",
}
SYNTHETIC_SECRET = "TEST_ONLY_NEVER_A_REAL_ACCOUNT_TOKEN"


def write_bundle(directory, files=None):
    """Make an intentionally inert output bundle, including its exact manifest."""
    files = {"warp-masque.yaml": b"proxies: []\n"} if files is None else files
    directory.mkdir(parents=True, exist_ok=True)
    data = {name: content.encode() if isinstance(content, str) else content
            for name, content in files.items()}
    data["manifest.json"] = (json.dumps({"schema_version": 1, "files": sorted(files)},
                                         sort_keys=True) + "\n").encode()
    for name, content in data.items():
        (directory / name).write_bytes(content)
    return data


class OutputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "generated"
        self.bundle = write_bundle(self.source)

    def manifest(self, value):
        (self.source / "manifest.json").write_text(json.dumps(value), encoding="utf-8")

    def test_allowlist_is_exact_and_excludes_raw_accounts_tokens_and_reports(self):
        self.assertEqual(set(ALLOWED_FILES), EXPECTED_FILES)

    def test_valid_bundle_returns_only_exact_bytes(self):
        self.assertEqual(validate_outputs(self.source), self.bundle)
        self.assertTrue(all(isinstance(data, bytes)
                            for data in validate_outputs(self.source).values()))

    def test_all_supported_outputs_are_accepted(self):
        files = {name: b"SYNTHETIC_OUTPUT\n" for name in EXPECTED_FILES - {"manifest.json"}}
        expected = write_bundle(self.source, files)
        self.assertEqual(validate_outputs(self.source), expected)

    def test_real_generator_synthetic_bundle_passes_without_raw_account_tokens(self):
        from warp_generator import generate, write_outputs

        account = json.loads((ROOT / "tests/fixtures/synthetic-account.json").read_text())
        generated = generate(account, {"endpoint_source": "account", "ports": [443],
                                       "ruleset_profile": "minimal"})
        write_outputs(generated, self.source)
        validated = validate_outputs(self.source)
        self.assertEqual(validated, {name: value.encode() for name, value in generated.items()})
        self.assertNotIn(b"TEST-ONLY-DO-NOT-EXPORT", b"".join(validated.values()))

    def test_empty_oversize_and_manifest_only_bundles_are_rejected(self):
        path = self.source / "warp-masque.yaml"
        for size in (0, publisher.MAX_FILE_BYTES + 1):
            with self.subTest(size=size):
                with path.open("wb") as handle:
                    handle.truncate(size)
                with self.assertRaises(PublicationError):
                    validate_outputs(self.source)
        path.unlink()
        self.manifest({"schema_version": 1, "files": []})
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)

    def test_unexpected_files_including_raw_credentials_are_rejected(self):
        for filename in ("account.json", "usque-config.json", "token.txt", "health.json",
                         ".env", ".gitignore", "warp-masque.yaml.bak", "config.yaml"):
            with self.subTest(filename=filename):
                target = self.source / filename
                target.write_text(SYNTHETIC_SECRET)
                with self.assertRaises(PublicationError) as error:
                    validate_outputs(self.source)
                self.assertNotIn(SYNTHETIC_SECRET, str(error.exception))
                target.unlink()

    def test_empty_missing_or_non_directory_source_is_rejected(self):
        for path in (self.root / "missing", self.root / "empty", self.source / "manifest.json"):
            with self.subTest(path=path.name):
                if path.name == "empty":
                    path.mkdir()
                with self.assertRaises(PublicationError):
                    validate_outputs(path)

    def test_manifest_is_required(self):
        (self.source / "manifest.json").unlink()
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)

    def test_malformed_non_utf8_or_non_object_manifest_is_rejected_safely(self):
        invalid = (b"{", b"\xff\xfe", b"[]", b"null", b'"not an object"',
                   ('{"files": ["' + SYNTHETIC_SECRET).encode())
        for raw in invalid:
            with self.subTest(raw=raw):
                (self.source / "manifest.json").write_bytes(raw)
                with self.assertRaises(PublicationError) as error:
                    validate_outputs(self.source)
                self.assertNotIn(SYNTHETIC_SECRET, str(error.exception))

    def test_schema_version_must_be_integer_one(self):
        for version in (None, False, True, 0, 2, "1", 1.0, [], {}):
            with self.subTest(version=version):
                self.manifest({"schema_version": version, "files": ["warp-masque.yaml"]})
                with self.assertRaises(PublicationError):
                    validate_outputs(self.source)
        self.manifest({"files": ["warp-masque.yaml"]})
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)

    def test_manifest_files_must_be_unique_exact_filename_list(self):
        for files in (None, {}, "warp-masque.yaml", [], ["warp-masque.yaml"] * 2,
                      ["manifest.json", "warp-masque.yaml"], ["external-direct.yaml"],
                      ["warp-masque.yaml", "external-direct.yaml"], [1], [None], [["warp-masque.yaml"]]):
            with self.subTest(files=files):
                self.manifest({"schema_version": 1, "files": files})
                with self.assertRaises(PublicationError):
                    validate_outputs(self.source)
        self.manifest({"schema_version": 1})
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)

    def test_manifest_cannot_reference_traversal_absolute_or_nested_paths(self):
        for name in ("../account.json", "/tmp/account.json", "outputs/warp-masque.yaml",
                     "./warp-masque.yaml", "..\\account.json", "warp-masque.yaml\x00",
                     "WARP-MASQUE.YAML", "warp-masque.yaml\n"):
            with self.subTest(name=name):
                self.manifest({"schema_version": 1, "files": [name]})
                with self.assertRaises(PublicationError):
                    validate_outputs(self.source)

    def test_nested_directory_is_rejected(self):
        nested = self.source / "extra"
        nested.mkdir()
        (nested / "account.json").write_text(SYNTHETIC_SECRET)
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)

    def test_allowlisted_directory_is_rejected(self):
        (self.source / "warp-masque.yaml").unlink()
        (self.source / "warp-masque.yaml").mkdir()
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)

    def test_symlink_source_directory_is_rejected(self):
        linked = self.root / "linked-generated"
        linked.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(PublicationError):
            validate_outputs(linked)

    def test_symlink_ancestor_of_source_directory_is_rejected(self):
        linked = self.root / "linked-root"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PublicationError):
            validate_outputs(linked / "generated")

    def test_symlinked_output_and_manifest_are_rejected(self):
        for name in ("warp-masque.yaml", "manifest.json"):
            with self.subTest(name=name):
                original = self.bundle[name]
                outside = self.root / (name + ".outside")
                outside.write_bytes(original)
                target = self.source / name
                target.unlink()
                target.symlink_to(outside)
                with self.assertRaises(PublicationError):
                    validate_outputs(self.source)
                self.assertEqual(outside.read_bytes(), original)
                target.unlink()
                target.write_bytes(original)

    def test_broken_symlink_is_rejected(self):
        (self.source / "external-direct.yaml").symlink_to(self.root / "missing")
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)

    def test_hardlinked_output_and_manifest_are_rejected(self):
        for name in ("warp-masque.yaml", "manifest.json"):
            with self.subTest(name=name):
                link = self.root / (name + ".hardlink")
                os.link(self.source / name, link)
                with self.assertRaises(PublicationError):
                    validate_outputs(self.source)
                link.unlink()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_special_file_is_rejected_without_reading_it(self):
        (self.source / "warp-masque.yaml").unlink()
        os.mkfifo(self.source / "warp-masque.yaml")
        with self.assertRaises(PublicationError):
            validate_outputs(self.source)


class WorkflowGateTests(unittest.TestCase):
    def env(self, **overrides):
        return {
            "GENERATION_MODE": "sample", "OUTPUT_DESTINATION": "artifact",
            "CONFIRM_PUBLISH_OUTPUTS": "false", "CONFIRM_PRIVATE_ARTIFACT": "false",
            "REPOSITORY_PRIVATE": "false", "GENERATION_ENABLED": "false",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_TYPE": "branch",
            "GITHUB_RUN_ATTEMPT": "1", **overrides,
        }

    def test_sample_and_external_artifacts_are_manual_and_need_no_secrets(self):
        for mode in ("sample", "external-only"):
            with self.subTest(mode=mode):
                check_gate(self.env(GENERATION_MODE=mode))

    def test_every_mode_and_destination_rejects_automatic_events_and_reruns(self):
        for mode in ("sample", "account", "external-only"):
            for destination in ("artifact", "repository"):
                for override in (
                    {"GITHUB_EVENT_NAME": event} for event in
                    ("push", "schedule", "pull_request", "pull_request_target", "workflow_run", "")
                ):
                    with self.subTest(mode=mode, destination=destination, override=override):
                        env = self.env(GENERATION_MODE=mode, OUTPUT_DESTINATION=destination,
                                       CONFIRM_PUBLISH_OUTPUTS="true", CONFIRM_PRIVATE_ARTIFACT="true",
                                       REPOSITORY_PRIVATE="true", GENERATION_ENABLED="true", **override)
                        with self.assertRaises(GateError):
                            check_gate(env)
                for attempt in ("", "0", "2", "3", "01", "true", "-1"):
                    with self.subTest(mode=mode, destination=destination, attempt=attempt):
                        with self.assertRaises(GateError):
                            check_gate(self.env(GENERATION_MODE=mode, OUTPUT_DESTINATION=destination,
                                                CONFIRM_PUBLISH_OUTPUTS="true", CONFIRM_PRIVATE_ARTIFACT="true",
                                                REPOSITORY_PRIVATE="true", GENERATION_ENABLED="true",
                                                GITHUB_RUN_ATTEMPT=attempt))

    def test_repository_publication_requires_exact_confirmation_and_branch(self):
        for mode in ("sample", "account", "external-only"):
            for confirmation in ("false", "", "True", "1", "yes"):
                with self.subTest(mode=mode, confirmation=confirmation):
                    with self.assertRaises(GateError):
                        check_gate(self.env(GENERATION_MODE=mode, OUTPUT_DESTINATION="repository",
                                            GENERATION_ENABLED="true", CONFIRM_PUBLISH_OUTPUTS=confirmation))
            for ref_type in ("tag", "", "pull_request"):
                with self.subTest(mode=mode, ref_type=ref_type):
                    with self.assertRaises(GateError):
                        check_gate(self.env(GENERATION_MODE=mode, OUTPUT_DESTINATION="repository",
                                            GENERATION_ENABLED="true", CONFIRM_PUBLISH_OUTPUTS="true",
                                            GITHUB_REF_TYPE=ref_type))
            check_gate(self.env(GENERATION_MODE=mode, OUTPUT_DESTINATION="repository",
                                GENERATION_ENABLED="true", CONFIRM_PUBLISH_OUTPUTS="true"))

    def test_account_generation_requires_separate_enable_switch(self):
        for destination in ("artifact", "repository"):
            for enabled in ("false", "", "True", "1", "yes"):
                with self.subTest(destination=destination, enabled=enabled):
                    with self.assertRaises(GateError):
                        check_gate(self.env(GENERATION_MODE="account", OUTPUT_DESTINATION=destination,
                                            CONFIRM_PUBLISH_OUTPUTS="true", CONFIRM_PRIVATE_ARTIFACT="true",
                                            REPOSITORY_PRIVATE="true", GENERATION_ENABLED=enabled))

    def test_account_artifact_requires_private_repository_and_private_confirmation(self):
        for private, confirmed in (("false", "false"), ("false", "true"), ("true", "false"),
                                   ("", "true"), ("true", "True"), ("True", "true")):
            with self.subTest(private=private, confirmed=confirmed):
                with self.assertRaises(GateError):
                    check_gate(self.env(GENERATION_MODE="account", GENERATION_ENABLED="true",
                                        REPOSITORY_PRIVATE=private, CONFIRM_PRIVATE_ARTIFACT=confirmed,
                                        CONFIRM_PUBLISH_OUTPUTS="true"))
        check_gate(self.env(GENERATION_MODE="account", GENERATION_ENABLED="true",
                            REPOSITORY_PRIVATE="true", CONFIRM_PRIVATE_ARTIFACT="true"))

    def test_private_artifact_confirmation_does_not_authorize_repository_publication(self):
        with self.assertRaises(GateError):
            check_gate(self.env(GENERATION_MODE="account", OUTPUT_DESTINATION="repository",
                                GENERATION_ENABLED="true", REPOSITORY_PRIVATE="true",
                                CONFIRM_PRIVATE_ARTIFACT="true"))

    def test_unknown_or_missing_mode_and_destination_fail_closed(self):
        for mode in ("", "live", "recover", "Account"):
            with self.subTest(mode=mode), self.assertRaises(GateError):
                check_gate(self.env(GENERATION_MODE=mode))
        for destination in ("", "public", "both", "Repository"):
            with self.subTest(destination=destination), self.assertRaises(GateError):
                check_gate(self.env(OUTPUT_DESTINATION=destination))
        with self.assertRaises(GateError):
            check_gate({})

    def test_gate_errors_do_not_echo_untrusted_environment_values(self):
        for field in ("GENERATION_MODE", "OUTPUT_DESTINATION", "GITHUB_EVENT_NAME",
                      "GITHUB_RUN_ATTEMPT"):
            with self.subTest(field=field):
                with self.assertRaises(GateError) as error:
                    check_gate(self.env(**{field: SYNTHETIC_SECRET}))
                self.assertNotIn(SYNTHETIC_SECRET, str(error.exception))


class PublicationCliTests(unittest.TestCase):
    def env(self, **overrides):
        return {"CONFIRM_PUBLISH_OUTPUTS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF_TYPE": "branch", **overrides}

    def call_main(self, env):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), redirect_stdout(stdout), redirect_stderr(stderr):
            status = publisher.main(["--source", "unused-source", "--repository", "unused-repository",
                                     "--branch", "main"])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_checks_all_guards_before_touching_git_or_generated_files(self):
        invalid = [{}, *({key: value} for key, values in (
            ("CONFIRM_PUBLISH_OUTPUTS", ("", "false", "True", "1")),
            ("GITHUB_EVENT_NAME", ("", "push", "schedule", "pull_request", "workflow_run")),
            ("GITHUB_RUN_ATTEMPT", ("", "2", "01")),
            ("GITHUB_REF_TYPE", ("", "tag")),
        ) for value in values)]
        for overrides in invalid:
            with self.subTest(overrides=overrides), patch.object(publisher, "publish_outputs") as publish:
                env = self.env(**overrides) if overrides else {}
                status, stdout, stderr = self.call_main(env)
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                self.assertIn("Publication failed", stderr)
                publish.assert_not_called()

    def test_cli_with_authorized_env_publishes_and_reports_only_commit_or_noop(self):
        for result in ("1" * 40, None):
            with self.subTest(result=result), patch.object(publisher, "publish_outputs", return_value=result) as publish:
                status, stdout, stderr = self.call_main(self.env())
                self.assertEqual(status, 0)
                self.assertEqual(stderr, "")
                self.assertIn(result or "unchanged", stdout)
                publish.assert_called_once_with(Path("unused-source"), Path("unused-repository"), "main")

    def test_cli_os_errors_do_not_echo_paths_or_file_content(self):
        with patch.object(publisher, "publish_outputs", side_effect=OSError(SYNTHETIC_SECRET)):
            status, stdout, stderr = self.call_main(self.env())
        self.assertEqual(status, 2)
        self.assertNotIn(SYNTHETIC_SECRET, stdout + stderr)
        self.assertNotIn("Traceback", stderr)


class LocalGitPublicationTests(unittest.TestCase):
    """Verify resulting remote trees without trusting the publisher's checkout."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
            "GIT_AUTHOR_NAME": "Synthetic publication test",
            "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic publication test",
            "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.origin = self.root / "origin.git"
        self.client = self.root / "checkout"
        self.peer = self.root / "other-checkout"
        self.source = self.root / "generated"
        self.bundle = write_bundle(self.source)
        self.git(self.root, "init", "--bare", str(self.origin))
        self.git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git(self.root, "init", "-b", "main", str(self.client))
        (self.client / "README.md").write_text("initial source\n")
        self.git(self.client, "add", "README.md")
        self.git(self.client, "commit", "-m", "Initial synthetic source")
        self.git(self.client, "remote", "add", "origin", str(self.origin))
        self.git(self.client, "push", "-u", "origin", "main")
        self.git(self.root, "clone", str(self.origin), str(self.peer))

    def git(self, repository, *args, **kwargs):
        result = GIT_RUN(["git", "-C", str(repository), *args], check=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        return result.stdout.decode().strip()

    def tip(self):
        return self.git(self.origin, "rev-parse", "refs/heads/main")

    def remote_files(self):
        return set(self.git(self.origin, "ls-tree", "-r", "--name-only", "main").splitlines())

    def remote_bytes(self, name):
        result = GIT_RUN(["git", "-C", str(self.origin), "show", f"main:{name}"],
                         check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout

    def commit_peer(self, files, message="Unrelated upstream edit"):
        for name, content in files.items():
            path = self.peer / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git(self.peer, "add", "--all")
        self.git(self.peer, "commit", "-m", message)
        self.git(self.peer, "push", "origin", "main")
        return self.tip()

    def assert_only_outputs_changed(self, commit):
        changed = self.git(self.origin, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
        self.assertTrue(changed)
        self.assertLessEqual(set(changed.splitlines()), {f"outputs/{name}" for name in EXPECTED_FILES})

    def test_publish_commits_only_allowlisted_files_and_returns_remote_sha(self):
        original_tip = self.tip()
        commit = publish_outputs(self.source, self.client, "main")
        self.assertEqual(commit, self.tip())
        self.assertNotEqual(commit, original_tip)
        self.assertEqual(self.remote_files(), {"README.md"} | {f"outputs/{name}" for name in self.bundle})
        self.assert_only_outputs_changed(commit)
        for name, content in self.bundle.items():
            self.assertEqual(self.remote_bytes(f"outputs/{name}"), content)

    def test_fetches_latest_branch_and_preserves_new_source_commits(self):
        stale_tip = self.git(self.client, "rev-parse", "HEAD")
        upstream = self.commit_peer({"README.md": "new upstream source\n", "scripts/new.py": "# newer source\n"})
        self.assertNotEqual(upstream, stale_tip)
        commit = publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.git(self.origin, "rev-parse", f"{commit}^"), upstream)
        self.assertEqual(self.remote_bytes("README.md"), b"new upstream source\n")
        self.assertEqual(self.remote_bytes("scripts/new.py"), b"# newer source\n")
        self.assert_only_outputs_changed(commit)

    def test_local_staged_dirty_untracked_and_committed_source_are_not_published(self):
        (self.client / "local-only.py").write_text("# unpushed local commit\n")
        self.git(self.client, "add", "local-only.py")
        self.git(self.client, "commit", "-m", "Must stay local")
        (self.client / "staged-source.py").write_text("# staged only\n")
        self.git(self.client, "add", "staged-source.py")
        (self.client / "README.md").write_text("dirty local source\n")
        (self.client / "account.json").write_text(SYNTHETIC_SECRET)
        original_head = self.git(self.client, "rev-parse", "HEAD")
        original_status = self.git(self.client, "status", "--porcelain=v1")
        original_index = self.git(self.client, "diff", "--cached", "--binary")
        commit = publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.git(self.client, "rev-parse", "HEAD"), original_head)
        self.assertEqual(self.git(self.client, "status", "--porcelain=v1"), original_status)
        self.assertEqual(self.git(self.client, "diff", "--cached", "--binary"), original_index)
        self.assertEqual(self.remote_bytes("README.md"), b"initial source\n")
        self.assertTrue({"local-only.py", "staged-source.py", "account.json"}.isdisjoint(self.remote_files()))
        self.assert_only_outputs_changed(commit)

    def test_identical_bundle_is_noop_without_new_commit(self):
        first = publish_outputs(self.source, self.client, "main")
        count = self.git(self.origin, "rev-list", "--count", "main")
        self.assertIsNone(publish_outputs(self.source, self.client, "main"))
        self.assertEqual(self.tip(), first)
        self.assertEqual(self.git(self.origin, "rev-list", "--count", "main"), count)

    def test_stale_managed_files_are_removed_but_other_source_is_preserved(self):
        previous = write_bundle(self.peer / "outputs", {
            "warp-masque.yaml": b"old synthetic output\n",
            "sing-box-usque-local.json": b"{}\n", "external-direct.yaml": b"proxies: []\n",
        })
        self.git(self.peer, "add", "outputs")
        self.git(self.peer, "commit", "-m", "Previous managed outputs")
        self.git(self.peer, "push", "origin", "main")
        commit = publish_outputs(self.source, self.client, "main")
        for name in set(previous) - set(self.bundle):
            self.assertNotIn(f"outputs/{name}", self.remote_files())
        self.assertEqual(self.remote_bytes("README.md"), b"initial source\n")
        self.assert_only_outputs_changed(commit)

    def test_unexpected_remote_output_file_is_rejected_without_remote_change(self):
        before = self.commit_peer({"outputs/account.json": SYNTHETIC_SECRET})
        with self.assertRaises(PublicationError) as error:
            publish_outputs(self.source, self.client, "main")
        self.assertNotIn(SYNTHETIC_SECRET, str(error.exception))
        self.assertEqual(self.tip(), before)
        self.assertEqual(self.remote_bytes("outputs/account.json"), SYNTHETIC_SECRET.encode())

    def test_nested_remote_output_directory_is_rejected(self):
        before = self.commit_peer({"outputs/warp-masque.yaml/account.json": SYNTHETIC_SECRET})
        with self.assertRaises(PublicationError):
            publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.tip(), before)

    def test_remote_outputs_path_cannot_be_a_regular_file(self):
        before = self.commit_peer({"outputs": "not a directory\n"})
        with self.assertRaises(PublicationError):
            publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.tip(), before)

    def test_executable_remote_output_is_rejected(self):
        (self.peer / "outputs").mkdir()
        output = self.peer / "outputs/warp-masque.yaml"
        output.write_text("synthetic executable mode\n")
        output.chmod(0o755)
        self.git(self.peer, "add", "outputs")
        self.git(self.peer, "commit", "-m", "Unsafe output mode")
        self.git(self.peer, "push", "origin", "main")
        before = self.tip()
        with self.assertRaises(PublicationError):
            publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.tip(), before)

    def test_symlinked_remote_outputs_directory_is_rejected(self):
        (self.peer / "outputs").symlink_to(".", target_is_directory=True)
        self.git(self.peer, "add", "outputs")
        self.git(self.peer, "commit", "-m", "Unsafe output directory")
        self.git(self.peer, "push", "origin", "main")
        before = self.tip()
        with self.assertRaises(PublicationError):
            publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.tip(), before)
        self.assertEqual(self.remote_bytes("README.md"), b"initial source\n")

    def test_symlinked_remote_managed_file_is_rejected(self):
        (self.peer / "outputs").mkdir()
        (self.peer / "outputs/warp-masque.yaml").symlink_to("../README.md")
        self.git(self.peer, "add", "outputs")
        self.git(self.peer, "commit", "-m", "Unsafe managed output")
        self.git(self.peer, "push", "origin", "main")
        before = self.tip()
        with self.assertRaises(PublicationError):
            publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.tip(), before)
        self.assertEqual(self.remote_bytes("README.md"), b"initial source\n")

    def test_unsafe_source_never_changes_remote(self):
        (self.source / "account.json").write_text(SYNTHETIC_SECRET)
        before = self.tip()
        with self.assertRaises(PublicationError):
            publish_outputs(self.source, self.client, "main")
        self.assertEqual(self.tip(), before)

    def test_missing_branch_is_not_created(self):
        before = self.git(self.origin, "show-ref")
        with self.assertRaises(PublicationError):
            publish_outputs(self.source, self.client, "does-not-exist")
        self.assertEqual(self.git(self.origin, "show-ref"), before)

    def test_invalid_branch_cannot_inject_refspec_or_option(self):
        before = self.tip()
        for branch in ("", "--all", "main:other", "+main", "../main", "main\nother"):
            with self.subTest(branch=branch), self.assertRaises(PublicationError):
                publish_outputs(self.source, self.client, branch)
        self.assertEqual(self.tip(), before)

    def test_competing_push_fails_without_force_or_overwriting_new_source(self):
        push_commands = []
        competing_commit = []

        def race(command, *args, **kwargs):
            if isinstance(command, (list, tuple)) and "push" in command:
                push_commands.append(list(command))
                if not competing_commit:
                    competing_commit.append(self.commit_peer({"README.md": "concurrent source change\n"}))
            return GIT_RUN(command, *args, **kwargs)

        with patch.object(publisher.subprocess, "run", side_effect=race):
            with self.assertRaises(PublicationError):
                publish_outputs(self.source, self.client, "main")
        self.assertTrue(push_commands)
        for command in push_commands:
            self.assertFalse(any(arg == "-f" or arg.startswith("--force") or arg.startswith("+")
                                 for arg in command))
        self.assertEqual(self.tip(), competing_commit[0])
        self.assertEqual(self.remote_bytes("README.md"), b"concurrent source change\n")
        self.assertNotIn("outputs/manifest.json", self.remote_files())

    def test_git_failure_diagnostics_do_not_leak_subprocess_output(self):
        before = self.tip()
        stdout, stderr = io.StringIO(), io.StringIO()

        def failing_push(command, *args, **kwargs):
            if isinstance(command, (list, tuple)) and "push" in command:
                raise subprocess.CalledProcessError(1, command, output=SYNTHETIC_SECRET.encode(),
                                                    stderr=SYNTHETIC_SECRET.encode())
            return GIT_RUN(command, *args, **kwargs)

        with redirect_stdout(stdout), redirect_stderr(stderr), \
                patch.object(publisher.subprocess, "run", side_effect=failing_push):
            with self.assertRaises(PublicationError) as error:
                publish_outputs(self.source, self.client, "main")
        self.assertNotIn(SYNTHETIC_SECRET, str(error.exception) + stdout.getvalue() + stderr.getvalue())
        self.assertEqual(self.tip(), before)

    def test_attributes_do_not_transform_output_or_execute_clean_filter(self):
        self.commit_peer({".gitattributes": "outputs/*.yaml filter=synthetic-danger\n"})
        marker = self.root / "filter-was-run"
        self.git(self.client, "config", "filter.synthetic-danger.clean", f"touch '{marker}'; cat")
        self.git(self.client, "config", "filter.synthetic-danger.required", "true")
        publish_outputs(self.source, self.client, "main")
        self.assertFalse(marker.exists())
        self.assertEqual(self.remote_bytes("outputs/warp-masque.yaml"), self.bundle["warp-masque.yaml"])

    def test_local_git_hooks_cannot_execute_during_publication(self):
        marker = self.root / "hook-was-run"
        for name in ("pre-commit", "post-commit", "post-checkout", "pre-push"):
            hook = self.client / ".git/hooks" / name
            hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
            hook.chmod(0o755)
        commit = publish_outputs(self.source, self.client, "main")
        self.assertEqual(commit, self.tip())
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
