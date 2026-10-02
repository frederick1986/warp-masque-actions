#!/usr/bin/env python3
"""Publish only validated generated files, after a user-operated manual workflow gate.

Git plumbing with an isolated index avoids checkout, hooks, filters, dirty working
trees and accidental staging. Git subprocess output is never forwarded to logs.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

ALLOWED_FILES = frozenset({
    "manifest.json", "warp-masque.yaml", "warp-masque-provider.yaml",
    "warp-masque-shadowrocket.txt", "external-direct.yaml",
    "sing-box-usque-local.json", "sing-box-vless-local.json",
})
MAX_FILE_BYTES = 8 * 1024 * 1024


class PublicationError(ValueError):
    pass


def validate_outputs(source):
    source = Path(source).absolute()
    if any(path.is_symlink() for path in (source, *source.parents)) or not source.is_dir():
        raise PublicationError("Output source must be a real directory without symlink ancestors.")
    contents = {}
    for path in source.iterdir():
        info = path.lstat()
        if path.name not in ALLOWED_FILES or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PublicationError("Output source contains a non-allowlisted file, directory or link.")
        if not 0 < info.st_size <= MAX_FILE_BYTES:
            raise PublicationError("Output files must be nonempty and within the size limit.")
        # O_NOFOLLOW plus fstat also rejects replacement by a link before open.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            current = os.fstat(handle.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                raise PublicationError("Output source changed during validation.")
            content = handle.read(MAX_FILE_BYTES + 1)
        if not 0 < len(content) <= MAX_FILE_BYTES:
            raise PublicationError("Output files must be nonempty and within the size limit.")
        contents[path.name] = content
    try:
        manifest = json.loads(contents["manifest.json"])
        listed = manifest["files"]
        valid = (type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
                 and isinstance(listed, list) and all(isinstance(name, str) for name in listed)
                 and len(listed) == len(set(listed)) and bool(listed)
                 and set(listed) == set(contents) - {"manifest.json"})
    except (KeyError, ValueError, TypeError, UnicodeError):
        valid = False
    if not valid:
        raise PublicationError("Manifest must exactly describe the supported generated files.")
    return contents


def run_git(repository, *args, env=None, data=None):
    try:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
                                 "-C", str(repository), *args], input=data, capture_output=True,
                                env=env, timeout=120, check=True)
    except (subprocess.SubprocessError, OSError):
        raise PublicationError("Git operation failed; check branch protection, write access or a concurrent update. No force push was attempted.") from None
    return result.stdout


def target_files(repository, commit):
    root = run_git(repository, "ls-tree", "-z", commit, "--", "outputs")
    if root and not root.startswith(b"040000 tree "):
        raise PublicationError("The target outputs path must be a normal Git directory.")
    entries = run_git(repository, "ls-tree", "-r", "-z", commit, "--", "outputs")
    paths = set()
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        if metadata.split(b" ", 2)[:2] != [b"100644", b"blob"] or path not in {f"outputs/{name}".encode() for name in ALLOWED_FILES}:
            raise PublicationError("The target outputs directory contains a non-allowlisted path or file mode.")
        paths.add(path.decode("ascii"))
    return paths


def publish_outputs(source, repository, branch):
    contents = validate_outputs(source)
    # Explicit ref validation plus argv (never a shell) prevents ref/path injection.
    if not isinstance(branch, str) or not branch or branch.startswith("-"):
        raise PublicationError("Publication requires a valid branch name.")
    run_git(repository, "check-ref-format", f"refs/heads/{branch}")
    run_git(repository, "fetch", "--no-tags", "origin", f"refs/heads/{branch}")
    parent = run_git(repository, "rev-parse", "--verify", "FETCH_HEAD^{commit}").decode().strip()
    previous = target_files(repository, parent)
    paths = {f"outputs/{name}" for name in contents}
    with tempfile.TemporaryDirectory(prefix="warp-publish-") as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index"),
               "GIT_AUTHOR_NAME": "github-actions[bot]", "GIT_COMMITTER_NAME": "github-actions[bot]",
               "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
               "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"}
        run_git(repository, "read-tree", parent, env=env)
        for path in sorted(previous - paths):
            run_git(repository, "update-index", "--force-remove", "--", path, env=env)
        for name, content in sorted(contents.items()):
            oid = run_git(repository, "hash-object", "-w", "--stdin", env=env, data=content).decode().strip()
            run_git(repository, "update-index", "--add", "--cacheinfo", "100644", oid, f"outputs/{name}", env=env)
        tree = run_git(repository, "write-tree", env=env).decode().strip()
        if tree == run_git(repository, "rev-parse", f"{parent}^{{tree}}", env=env).decode().strip():
            return None
        changed = run_git(repository, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", parent, tree, env=env)
        if any(path.decode() not in previous | paths for path in changed.split(b"\0") if path):
            raise PublicationError("Refusing a commit that changes paths outside the generated file allowlist.")
        commit = run_git(repository, "commit-tree", tree, "-p", parent, env=env,
                         data=b"Update manually generated outputs\n").decode().strip()
        # A writer racing after fetch makes this fail closed. No force/rebase/retry.
        run_git(repository, "push", "origin", f"{commit}:refs/heads/{branch}", env=env)
    return commit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--branch", required=True)
    args = parser.parse_args(argv)
    try:
        if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
                or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
                or os.environ.get("GITHUB_REF_TYPE") != "branch"
                or os.environ.get("CONFIRM_PUBLISH_OUTPUTS") != "true"):
            raise PublicationError("Publication requires a newly confirmed, user-operated manual workflow on a branch.")
        commit = publish_outputs(args.source, args.repository, args.branch)
        print(f"Published generated outputs in commit {commit}." if commit else "Generated outputs are unchanged; no commit needed.")
        return 0
    except (PublicationError, OSError, UnicodeError) as error:
        message = str(error) if isinstance(error, PublicationError) else "Cannot read generated files; check paths and permissions."
        print(f"Publication failed: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
