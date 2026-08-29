from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import build_knowledge_bundle as builder
from knowledge_common import (
    BundleIntegrityError,
    load_validated_bundle,
    strict_json_loads,
    tree_sha256,
    validate_bundle,
    validate_human_verification,
)


TEST_SOURCE_REMOTE = "git@github.com:MZqk/StarunWiki.git"


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _run_git_bytes(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    return completed.stdout


def _formal_root(repository: Path) -> Path:
    return repository.joinpath(*builder.SOURCE_PACK_ROOT.parts)


def _detected_formal_root(repository: Path) -> tuple[PurePosixPath, Path]:
    candidates = [
        (source_pack_root, repository.joinpath(*source_pack_root.parts))
        for source_pack_root in builder.SUPPORTED_SOURCE_PACK_ROOTS
        if all(
            (repository.joinpath(*source_pack_root.parts) / directory).is_dir()
            for directory in builder.INCLUDED_DIRS
        )
    ]
    assert len(candidates) == 1, candidates
    return candidates[0]


def _source_identity(repository: Path) -> dict[str, str]:
    commit = _run_git(repository, "rev-parse", "HEAD")
    source_pack_root, _ = _detected_formal_root(repository)
    source_directories = [
        (source_pack_root / directory).as_posix()
        for directory in builder.INCLUDED_DIRS
    ]
    raw_paths = _run_git_bytes(
        repository,
        "ls-tree",
        "-r",
        "-z",
        "--name-only",
        commit,
        "--",
        *source_directories,
    )
    source_paths = [
        raw.decode("utf-8")
        for raw in raw_paths.split(b"\0")
        if raw and raw.decode("utf-8").endswith(".md") and not raw.endswith(b"/index.md")
    ]
    formal_sha = tree_sha256(
        (
            PurePosixPath(source_path).relative_to(source_pack_root).as_posix(),
            _run_git_bytes(repository, "cat-file", "blob", f"{commit}:{source_path}"),
        )
        for source_path in source_paths
    )
    return {
        "expect_source_remote": TEST_SOURCE_REMOTE,
        "expect_source_commit": commit,
        "expect_formal_sha256": formal_sha,
    }


def _build(repository: Path, **kwargs: object) -> dict[str, object]:
    return builder.build(repository, **_source_identity(repository), **kwargs)


def _page(category: str, number: int) -> str:
    return f'''---
type: Concept
title: 测试页面 {number:02d}
description: 用于可信构建器测试的正式知识页。
tags:
  - 测试
category: {category}
status: stable
updated: "2026-08-28"
generated:
  by: process:test-fixture
  at: "2026-08-28T12:00:00+08:00"
review:
  owner: test-reviewer
  state: needs-human-review
stale_after: "2027-08-28"
difficulty: 入门
audience: 测试者
applies_to:
  系统:
    - 测试系统
  条件:
    - 测试条件
  不适用:
    - 非测试情形
sources:
  - id: source-{number:02d}
    title: 测试来源 {number:02d}
    resource: https://example.test/{number:02d}
verified: null
---

# 测试页面 {number:02d}

这是 {category} 的测试正文。
'''


@pytest.fixture()
def source_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    _run_git(source, "init", "-q")
    _run_git(source, "remote", "add", "origin", TEST_SOURCE_REMOTE)
    _run_git(source, "config", "user.name", "Bundle Test")
    _run_git(source, "config", "user.email", "bundle@example.test")
    (source / ".gitignore").write_text("*.ignored.md\n", encoding="utf-8")
    for number, category in enumerate(builder.INCLUDED_DIRS):
        directory = _formal_root(source) / category
        directory.mkdir(parents=True)
        (directory / f"页面-{number:02d}.md").write_text(_page(category, number), encoding="utf-8")
    scripts = source / "scripts"
    scripts.mkdir()
    (scripts / "build_knowledge_catalog.py").write_text(
        "from pathlib import Path\nPath('SOURCE_SCRIPT_EXECUTED').write_text('bad')\n",
        encoding="utf-8",
    )
    _run_git(source, "add", ".")
    _run_git(source, "commit", "-qm", "fixture")
    return source


@pytest.fixture()
def bundle_paths(tmp_path: Path) -> builder.BundlePaths:
    skill = tmp_path / "skill"
    runtime = {
        "SKILL.md": b"---\nname: deep-sky-capture-advisor\ndescription: test\n---\n",
        "agents/openai.yaml": b"interface:\n  display_name: Test\n",
        "scripts/query_knowledge.py": b"# query fixture\n",
        "scripts/knowledge_common.py": b"# common fixture\n",
    }
    for relative, content in runtime.items():
        path = skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return builder.BundlePaths(skill)


def test_strict_json_and_human_verification_reject_ambiguous_claims() -> None:
    with pytest.raises(BundleIntegrityError, match="duplicate object key"):
        strict_json_loads('{"schema_version": 2, "schema_version": 999}')
    with pytest.raises(BundleIntegrityError, match="non-finite"):
        strict_json_loads('{"value": NaN}')

    assert validate_human_verification(
        {"by": "human:reviewer", "at": "2026-08-28", "scope": "SOP"}
    )
    assert validate_human_verification(
        {
            "by": "human:mz",
            "at": "2026-08-28T12:30:00+08:00",
            "scope": "导星参数与失效边界",
        }
    )
    for invalid in (
        {"by": "human:", "at": "2026-08-28", "scope": "SOP"},
        {"by": "model:reviewer", "at": "2026-08-28", "scope": "SOP"},
        {"by": "human:reviewer", "at": "not-a-date", "scope": "SOP"},
        {"by": "human:reviewer", "at": "2026-08-28T12:30:00", "scope": "SOP"},
        {"by": "human:reviewer", "at": "2026-08-28", "scope": "x"},
        {"by": "human:reviewer", "at": "2026-08-28", "scope": "全文正确"},
    ):
        with pytest.raises(BundleIntegrityError):
            validate_human_verification(invalid)


def test_yaml_aliases_and_recursive_metadata_fail_closed() -> None:
    aliased = _page(builder.INCLUDED_DIRS[0], 0).replace(
        "verified: null",
        "verified: &loop [*loop]",
    )
    with pytest.raises(builder.BundleError, match="must not use YAML anchors or aliases"):
        builder._validated_page(aliased.encode("utf-8"), "00-知识库规范/alias.md")

    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(builder.BundleError, match="cyclic object graph"):
        builder._json_safe(cyclic)

    nested: object = "leaf"
    for _ in range(builder.JSON_SAFE_DEPTH_LIMIT + 2):
        nested = [nested]
    with pytest.raises(builder.BundleError, match="depth limit"):
        builder._json_safe(nested)

    wide_frontmatter = _page(builder.INCLUDED_DIRS[0], 0).replace(
        "verified: null",
        "wide: [" + ",".join("x" for _ in range(builder.YAML_NODE_TOKEN_LIMIT + 1)) + "]\n"
        "verified: null",
    )
    with pytest.raises(builder.BundleError, match="pre-load limit"):
        builder._validated_page(
            wide_frontmatter.encode("utf-8"),
            "00-知识库规范/wide.md",
        )

    deep_value = "[" * (builder.JSON_SAFE_DEPTH_LIMIT + 1) + "x" + "]" * (
        builder.JSON_SAFE_DEPTH_LIMIT + 1
    )
    deep_frontmatter = _page(builder.INCLUDED_DIRS[0], 0).replace(
        "verified: null",
        f"deep: {deep_value}\nverified: null",
    )
    with pytest.raises(builder.BundleError, match="pre-load depth limit"):
        builder._validated_page(
            deep_frontmatter.encode("utf-8"),
            "00-知识库规范/deep.md",
        )


@pytest.mark.parametrize(
    ("identity", "message"),
    (
        ({}, "expect-source-remote"),
        ({"expect_source_remote": TEST_SOURCE_REMOTE}, "expect-source-commit"),
        (
            {
                "expect_source_remote": TEST_SOURCE_REMOTE,
                "expect_source_commit": "0" * 40,
            },
            "expect-formal-sha256",
        ),
    ),
)
def test_build_requires_complete_source_identity_before_git_or_bundle_writes(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
    monkeypatch: pytest.MonkeyPatch,
    identity: dict[str, str],
    message: str,
) -> None:
    monkeypatch.setattr(
        builder,
        "_run_git_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Git must not run")),
    )
    with pytest.raises(builder.BundleError, match=message):
        builder.build(source_repo, paths=bundle_paths, **identity)
    assert not bundle_paths.references_root.exists()


def test_cli_requires_build_identity_but_recover_remains_independent(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_knowledge_bundle.py", "--source", str(source_repo), "--check"],
    )
    assert builder.main() == 2
    assert "--expect-source-remote is required" in capsys.readouterr().err

    monkeypatch.setattr(builder, "recover", lambda: {"status": "no-op", "recovered": True})
    monkeypatch.setattr(sys, "argv", ["build_knowledge_bundle.py", "--recover"])
    assert builder.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "no-op"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_knowledge_bundle.py",
            "--recover",
            "--expect-source-commit",
            "0" * 40,
        ],
    )
    assert builder.main() == 2
    assert "--recover cannot be combined" in capsys.readouterr().err


def test_source_inspection_is_remote_pinned_isolated_and_non_installing(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    result = builder.inspect_source(
        source_repo,
        expect_source_remote=TEST_SOURCE_REMOTE,
    )
    identity = _source_identity(source_repo)
    assert result["installed"] is False
    assert result["inspected"] is True
    assert result["source_remote"] == TEST_SOURCE_REMOTE
    assert result["source_commit"] == identity["expect_source_commit"]
    assert result["formal_page_sha256"] == identity["expect_formal_sha256"]
    assert result["source_layout"] == builder.SOURCE_PACK_ROOT.as_posix()
    assert result["source_isolation"] in {"macos-sandbox-exec", "linux-bwrap"}
    assert result["git_command_timeout_seconds"] == builder.GIT_COMMAND_TIMEOUT_SECONDS
    assert result["source_operation_timeout_seconds"] == builder.SOURCE_OPERATION_TIMEOUT_SECONDS
    assert result["formal_page_limit_bytes"] == builder.FORMAL_PAGE_LIMIT_BYTES
    assert result["formal_bundle_limit_bytes"] == builder.FORMAL_BUNDLE_LIMIT_BYTES
    assert not bundle_paths.references_root.exists()


def test_source_inspection_rejects_unapproved_remote_before_git(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "_run_git_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Git must not run")),
    )
    with pytest.raises(builder.BundleError, match="not an approved StarunWiki"):
        builder.inspect_source(
            source_repo,
            expect_source_remote="https://example.test/not-approved.git",
        )

    with pytest.raises(builder.BundleError, match="not an approved StarunWiki"):
        builder.inspect_source(source_repo, expect_source_remote="https://[")


def test_cli_source_inspection_returns_candidate_pins_without_installing(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_knowledge_bundle.py",
            "--inspect-source",
            "--source",
            str(source_repo),
            "--expect-source-remote",
            TEST_SOURCE_REMOTE,
        ],
    )
    assert builder.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["inspected"] is True
    assert result["source_remote"] == TEST_SOURCE_REMOTE
    assert len(result["source_commit"]) == 40
    assert len(result["formal_page_sha256"]) == 64


def test_legacy_repository_root_layout_remains_reproducible(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    nested_root = _formal_root(source_repo)
    for directory in builder.INCLUDED_DIRS:
        (nested_root / directory).rename(source_repo / directory)
    nested_root.rmdir()
    nested_root.parent.rmdir()
    _run_git(source_repo, "add", "-A")
    _run_git(source_repo, "commit", "-qm", "use legacy source layout")

    result = _build(source_repo, check=True, paths=bundle_paths)
    assert result["checked"] is True
    assert result["source_layout"] == "repository-root"
    assert result["content_page_count"] == len(builder.INCLUDED_DIRS)
    assert not bundle_paths.references_root.exists()


def test_committed_dual_source_layout_is_rejected_as_ambiguous(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    category = builder.INCLUDED_DIRS[0]
    legacy_category = source_repo / category
    legacy_category.mkdir()
    legacy_page = legacy_category / "duplicate.md"
    legacy_page.write_bytes((_formal_root(source_repo) / category / "页面-00.md").read_bytes())
    _run_git(source_repo, "add", str(legacy_page.relative_to(source_repo)))
    _run_git(source_repo, "commit", "-qm", "add ambiguous legacy layout")
    identity = {
        "expect_source_remote": TEST_SOURCE_REMOTE,
        "expect_source_commit": _run_git(source_repo, "rev-parse", "HEAD"),
        "expect_formal_sha256": "0" * 64,
    }

    with pytest.raises(builder.BundleError, match="Ambiguous source knowledge layouts in committed tree"):
        builder.build(source_repo, **identity, check=True, paths=bundle_paths)
    assert not bundle_paths.references_root.exists()


def test_ignored_alternate_source_layout_is_rejected_as_ambiguous(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    identity = _source_identity(source_repo)
    category = builder.INCLUDED_DIRS[0]
    legacy_category = source_repo / category
    legacy_category.mkdir()
    (legacy_category / "extra.ignored.md").write_text(_page(category, 99), encoding="utf-8")
    assert _run_git(source_repo, "status", "--porcelain") == ""

    with pytest.raises(builder.BundleError, match="Ambiguous source knowledge layouts in worktree"):
        builder.build(source_repo, **identity, check=True, paths=bundle_paths)
    assert not bundle_paths.references_root.exists()


def test_remote_identity_mismatch_has_no_install_side_effect(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    identity = _source_identity(source_repo)
    identity["expect_source_remote"] = "https://github.com/MZqk/StarunWiki.git"
    with pytest.raises(builder.BundleError, match="remote does not exactly match"):
        builder.build(source_repo, paths=bundle_paths, **identity)
    assert not bundle_paths.references_root.exists()


def test_commit_identity_mismatch_stops_before_formal_blob_reads(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _source_identity(source_repo)
    identity["expect_source_commit"] = "0" * 40
    monkeypatch.setattr(
        builder,
        "_commit_formal_entries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("formal blobs must not be inspected after a commit mismatch")
        ),
    )
    with pytest.raises(builder.BundleError, match="Source commit mismatch"):
        builder.build(source_repo, paths=bundle_paths, **identity)
    assert not bundle_paths.references_root.exists()


def test_commit_pin_is_compared_before_worktree_status(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = source_repo.resolve()

    def git_text(arguments: list[str], source: Path) -> str:
        assert source == resolved
        if arguments == ["rev-parse", "--show-toplevel"]:
            return str(resolved)
        if arguments == ["remote", "get-url", "--all", builder.SOURCE_REMOTE_NAME]:
            return TEST_SOURCE_REMOTE
        if arguments == ["rev-parse", "--verify", "HEAD^{commit}"]:
            return "1" * 40
        raise AssertionError(f"unexpected Git text command: {arguments}")

    monkeypatch.setattr(builder, "_run_git_text", git_text)
    monkeypatch.setattr(
        builder,
        "_run_git_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("worktree status must not run after a commit pin mismatch")
        ),
    )
    with pytest.raises(builder.BundleError, match="Source commit mismatch"):
        builder.build(
            source_repo,
            expect_source_remote=TEST_SOURCE_REMOTE,
            expect_source_commit="0" * 40,
            expect_formal_sha256="0" * 64,
            check=True,
            paths=bundle_paths,
        )
    assert not bundle_paths.references_root.exists()


def test_formal_pin_is_compared_before_worktree_or_yaml_processing(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _source_identity(source_repo)
    identity["expect_formal_sha256"] = "0" * 64
    monkeypatch.setattr(
        builder,
        "_detect_worktree_source_pack_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("worktree inventory must not run after a formal pin mismatch")
        ),
    )
    monkeypatch.setattr(
        builder,
        "_render_retrieval_corpus",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("YAML/corpus processing must not run after a formal pin mismatch")
        ),
    )
    with pytest.raises(builder.BundleError, match="Formal page SHA-256 mismatch"):
        builder.build(source_repo, **identity, check=True, paths=bundle_paths)
    assert not bundle_paths.references_root.exists()


def test_git_timeout_and_environment_are_mandatory(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = Path("/usr/bin/git")
    monkeypatch.setenv("GIT_DIR", "/tmp/hostile-git-dir")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/hostile.dylib")
    monkeypatch.setattr(
        builder,
        "_git_command",
        lambda arguments, source: ("test-isolation", [str(git), *arguments], git),
    )

    def timeout(command: list[str], **kwargs: object) -> None:
        assert kwargs["timeout"] == builder.GIT_COMMAND_TIMEOUT_SECONDS
        assert kwargs["stdout_limit"] == builder.GIT_STDOUT_LIMIT_BYTES
        assert kwargs["stderr_limit"] == builder.GIT_STDERR_LIMIT_BYTES
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert "GIT_DIR" not in environment
        assert "DYLD_INSERT_LIBRARIES" not in environment
        assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert environment["GIT_NO_LAZY_FETCH"] == "1"
        raise subprocess.TimeoutExpired(command, builder.GIT_COMMAND_TIMEOUT_SECONDS)

    monkeypatch.setattr(builder, "_run_bounded_process", timeout)
    with pytest.raises(builder.BundleError, match="timed out after 30s under test-isolation"):
        builder._run_git_bytes(["status", "--porcelain=v1"], source_repo)


def test_git_output_limit_is_enforced_by_the_real_process_runner(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable)
    command = [
        str(executable),
        "-B",
        "-c",
        "import os; os.write(1, b'x' * 4096)",
    ]
    monkeypatch.setattr(
        builder,
        "_git_command",
        lambda arguments, source: ("test-isolation", command, executable),
    )
    with pytest.raises(builder.BundleError, match="stdout exceeded the 1024-byte limit"):
        builder._run_git_bytes(["bounded-output-test"], source_repo, stdout_limit=1024)


def test_bounded_process_timeout_terminates_the_process_group(
    source_repo: Path,
) -> None:
    executable = str(Path(sys.executable))
    script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        builder._run_bounded_process(
            [executable, "-B", "-c", script],
            cwd=source_repo,
            env={"PATH": os.environ.get("PATH", "")},
            timeout=0.2,
            stdout_limit=1024,
            stderr_limit=1024,
        )
    assert time.monotonic() - started < 5


def test_expired_total_source_budget_stops_before_process_creation(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "_git_command",
        lambda arguments, source: (
            "test-isolation",
            [str(Path(sys.executable)), "-B", "-c", "raise SystemExit(0)"],
            Path(sys.executable),
        ),
    )
    monkeypatch.setattr(
        builder,
        "_run_bounded_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("expired source budget must stop before process creation")
        ),
    )
    token = builder._SOURCE_OPERATION_DEADLINE.set(0.0)
    try:
        with pytest.raises(builder.BundleError, match="120s total budget"):
            builder._run_git_bytes(["budget-test"], source_repo)
    finally:
        builder._SOURCE_OPERATION_DEADLINE.reset(token)


def test_formal_page_count_and_total_byte_limits_fail_closed(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = _run_git(source_repo, "rev-parse", "HEAD")
    monkeypatch.setattr(builder, "FORMAL_PAGE_COUNT_LIMIT", 1)
    with pytest.raises(builder.BundleError, match="Formal page count exceeds"):
        builder._commit_formal_entries(source_repo, commit)

    monkeypatch.setattr(builder, "FORMAL_PAGE_LIMIT_BYTES", 4)
    monkeypatch.setattr(builder, "FORMAL_BUNDLE_LIMIT_BYTES", 6)
    limits: list[int] = []

    def oversized_second_blob(
        arguments: list[str],
        source: Path,
        *,
        stdout_limit: int,
    ) -> bytes:
        limits.append(stdout_limit)
        return b"1234" if len(limits) == 1 else b"123"

    monkeypatch.setattr(builder, "_run_git_bytes", oversized_second_blob)
    entries = {
        "00-知识库规范/a.md": ("100644", "blob", "a" * 40),
        "00-知识库规范/b.md": ("100644", "blob", "b" * 40),
    }
    with pytest.raises(builder.BundleError, match="Formal bundle exceeds"):
        builder._read_commit_blobs(source_repo, entries)
    assert limits == [4, 2]

    monkeypatch.setattr(builder, "WORKTREE_ENTRY_LIMIT", 1)
    with pytest.raises(builder.BundleError, match="worktree inventory exceeds"):
        builder._worktree_formal_paths(source_repo, builder.SOURCE_PACK_ROOT)


def test_linux_bwrap_policy_mounts_only_system_runtime_and_source(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "_git_executable", lambda: Path("/usr/bin/git"))
    monkeypatch.setattr(
        builder,
        "_git_isolation",
        lambda: ("linux-bwrap", Path("/usr/bin/bwrap")),
    )
    isolation, command, _ = builder._git_command(["status", "--porcelain=v1"], source_repo)
    assert isolation == "linux-bwrap"
    assert "--unshare-all" in command
    assert "--cap-drop" in command
    assert ["--ro-bind", str(source_repo), "/src"] == command[
        command.index(str(source_repo)) - 1 : command.index(str(source_repo)) + 2
    ]
    assert ["--ro-bind", "/", "/"] not in [
        command[index : index + 3] for index in range(len(command) - 2)
    ]
    assert command[command.index("--chdir") + 1] == "/src"


def test_os_isolation_denies_git_repository_writes(source_repo: Path) -> None:
    with pytest.raises(builder.BundleError, match="Git command failed under"):
        builder._run_git_bytes(["tag", "sandbox-write-must-fail"], source_repo)
    assert _run_git(source_repo, "tag", "--list", "sandbox-write-must-fail") == ""


def test_check_reads_commit_blobs_without_source_indexes_or_source_code(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    result = _build(source_repo, check=True, paths=bundle_paths)
    assert result["checked"] is True
    assert result["installed"] is False
    assert result["content_page_count"] == len(builder.INCLUDED_DIRS)
    assert len(result["formal_page_sha256"]) == 64
    assert len(result["retrieval_corpus_sha256"]) == 64
    assert not (source_repo / "SOURCE_SCRIPT_EXECUTED").exists()
    assert not bundle_paths.references_root.exists()


def test_install_produces_manifest_v2_catalog_v1_and_valid_runtime_closure(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    result = _build(source_repo, paths=bundle_paths)
    validated = load_validated_bundle(bundle_paths.references_root)
    assert result["installed"] is True
    assert validated.manifest["schema_version"] == 2
    assert validated.catalog["schema_version"] == 1
    assert validated.manifest["bundle"]["formal_page_sha256"] == result["formal_page_sha256"]
    assert set(validated.manifest["runtime_files"]) == {
        "SKILL.md",
        "agents/openai.yaml",
        "scripts/query_knowledge.py",
        "scripts/knowledge_common.py",
    }
    assert validated.manifest["source"]["working_tree_dirty"] is False
    assert validated.manifest["runtime"]["query_network_access"] is False


def test_dirty_and_ignored_formal_pages_are_rejected(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    first = next((_formal_root(source_repo) / builder.INCLUDED_DIRS[0]).glob("*.md"))
    first.write_text(first.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8")
    with pytest.raises(builder.BundleError, match="completely clean"):
        _build(source_repo, check=True, paths=bundle_paths)

    _run_git(source_repo, "restore", ".")
    ignored = _formal_root(source_repo) / builder.INCLUDED_DIRS[0] / "extra.ignored.md"
    ignored.write_text(_page(builder.INCLUDED_DIRS[0], 99), encoding="utf-8")
    assert _run_git(source_repo, "status", "--porcelain") == ""
    with pytest.raises(builder.BundleError, match="extra or ignored"):
        _build(source_repo, check=True, paths=bundle_paths)


def test_committed_formal_symlink_is_rejected(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    link = _formal_root(source_repo) / builder.INCLUDED_DIRS[0] / "linked.md"
    try:
        link.symlink_to("页面-00.md")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    _run_git(source_repo, "add", str(link.relative_to(source_repo)))
    _run_git(source_repo, "commit", "-qm", "add malicious formal symlink")
    with pytest.raises(builder.BundleError, match="regular Git blob"):
        _build(source_repo, check=True, paths=bundle_paths)


def test_source_repository_path_symlink_is_rejected(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
    tmp_path: Path,
) -> None:
    link = tmp_path / "source-link"
    try:
        link.symlink_to(source_repo, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(builder.BundleError, match="Source path must not be a symbolic link"):
        builder.build(link, **_source_identity(source_repo), check=True, paths=bundle_paths)
    assert not bundle_paths.references_root.exists()


def test_expected_hash_mismatch_has_no_install_side_effect(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    identity = _source_identity(source_repo)
    identity["expect_formal_sha256"] = "0" * 64
    with pytest.raises(builder.BundleError, match="Formal page SHA-256 mismatch"):
        builder.build(source_repo, **identity, paths=bundle_paths)
    assert not bundle_paths.references_root.exists()


def test_manifest_authority_counts_and_runtime_file_set_are_recomputed(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    _build(source_repo, paths=bundle_paths)
    catalog_bytes = bundle_paths.catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    manifest = json.loads(bundle_paths.manifest_path.read_text(encoding="utf-8"))
    manifest["bundle"]["human_verified_page_count"] = len(catalog["entries"])
    with pytest.raises(BundleIntegrityError, match="human_verified_page_count"):
        validate_bundle(
            manifest,
            catalog,
            catalog_bytes,
            bundle_paths.knowledge_root,
            skill_root=bundle_paths.skill_root,
        )

    original = json.loads(bundle_paths.manifest_path.read_text(encoding="utf-8"))
    original["runtime_files"]["scripts/build_knowledge_bundle.py"] = "0" * 64
    with pytest.raises(BundleIntegrityError, match="runtime_files"):
        validate_bundle(
            original,
            catalog,
            catalog_bytes,
            bundle_paths.knowledge_root,
            skill_root=bundle_paths.skill_root,
        )


def test_formal_page_tamper_reports_page_and_tree_hash_mismatches(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    _build(source_repo, paths=bundle_paths)
    page = next(
        path for path in bundle_paths.knowledge_root.rglob("*.md") if path.name != "index.md"
    )
    page.write_bytes(page.read_bytes() + b"\n")

    with pytest.raises(BundleIntegrityError) as captured:
        load_validated_bundle(bundle_paths.references_root)

    errors = captured.value.errors
    assert any("page SHA-256 does not match catalog" in error for error in errors)
    assert any("manifest.bundle.formal_page_sha256" in error for error in errors)
    assert any("manifest.bundle.knowledge_sha256" in error for error in errors)


@pytest.mark.parametrize("failure_point", ("old-catalog", "new-catalog", "new-manifest"))
def test_transaction_failure_restores_the_previous_artifact_set(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    _build(source_repo, paths=bundle_paths)
    previous = load_validated_bundle(bundle_paths.references_root)

    page = next((_formal_root(source_repo) / builder.INCLUDED_DIRS[0]).glob("*.md"))
    page.write_text(page.read_text(encoding="utf-8") + "\n提交后的新正文。\n", encoding="utf-8")
    _run_git(source_repo, "add", str(page.relative_to(source_repo)))
    _run_git(source_repo, "commit", "-qm", "new source generation")

    original_rename: Callable[[Path, Path], None] = builder._rename
    injected = {"done": False}

    def fail_once(source: Path, destination: Path) -> None:
        point = (
            "old-catalog"
            if destination.parent.name == "old" and source.name == "catalog.json"
            else "new-catalog"
            if source.parent.name == "new" and source.name == "catalog.json"
            else "new-manifest"
            if source.parent.name == "new" and source.name == "manifest.json"
            else None
        )
        if not injected["done"] and point == failure_point:
            injected["done"] = True
            raise OSError(f"injected {failure_point} switch failure")
        original_rename(source, destination)

    monkeypatch.setattr(builder, "_rename", fail_once)
    with pytest.raises(builder.BundleError, match="recovered safely"):
        _build(source_repo, replace=True, paths=bundle_paths)
    restored = load_validated_bundle(bundle_paths.references_root)
    assert restored.fingerprint == previous.fingerprint
    assert not bundle_paths.journal_path.exists()


@pytest.mark.parametrize("failed_artifact", ("knowledge", "catalog.json", "manifest.json"))
def test_interrupted_initial_install_completes_the_recorded_new_artifact_set(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
    monkeypatch: pytest.MonkeyPatch,
    failed_artifact: str,
) -> None:
    original_rename: Callable[[Path, Path], None] = builder._rename
    injected = {"done": False}

    def fail_once(source: Path, destination: Path) -> None:
        if (
            not injected["done"]
            and source.parent.name == "new"
            and source.name == failed_artifact
        ):
            injected["done"] = True
            raise OSError(f"injected initial-install failure for {failed_artifact}")
        original_rename(source, destination)

    monkeypatch.setattr(builder, "_rename", fail_once)
    with pytest.raises(builder.BundleError, match="recovered safely"):
        _build(source_repo, paths=bundle_paths)

    validated = load_validated_bundle(bundle_paths.references_root)
    assert validated.facts["content_page_count"] == len(builder.INCLUDED_DIRS)
    assert not bundle_paths.journal_path.exists()
    assert not list(bundle_paths.references_root.glob(".bundle-txn-*"))


def test_recovery_rejects_symlinked_transaction_subdirectories(
    bundle_paths: builder.BundlePaths,
    tmp_path: Path,
) -> None:
    bundle_paths.references_root.mkdir(parents=True)
    transaction_id = "a" * 32
    transaction_dir = bundle_paths.references_root / f".bundle-txn-{transaction_id}"
    transaction_dir.mkdir()
    (transaction_dir / "old").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (transaction_dir / "new").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    bundle_paths.journal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "transaction_dir": transaction_dir.name,
                "old": None,
                "new": {
                    "catalog_file_sha256": "0" * 64,
                    "knowledge_sha256": "0" * 64,
                    "manifest_file_sha256": "0" * 64,
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(builder.BundleError, match="Transaction new must be a real directory"):
        builder.recover(bundle_paths)
    assert bundle_paths.journal_path.exists()
    assert list(outside.iterdir()) == []


def test_catalog_path_and_extra_knowledge_page_are_rejected(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    _build(source_repo, paths=bundle_paths)
    catalog_bytes = bundle_paths.catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    manifest = json.loads(bundle_paths.manifest_path.read_text(encoding="utf-8"))
    catalog["entries"][0]["path"] = "../../outside.md"
    with pytest.raises(BundleIntegrityError, match="direct Markdown|relative POSIX path"):
        validate_bundle(
            manifest,
            catalog,
            json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            bundle_paths.knowledge_root,
            skill_root=bundle_paths.skill_root,
        )

    (bundle_paths.knowledge_root / "unexpected.md").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(BundleIntegrityError, match="unexpected"):
        load_validated_bundle(bundle_paths.references_root)


def test_explicit_replace_migrates_an_opaque_legacy_v1_baseline(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    _build(source_repo, paths=bundle_paths)
    legacy = json.loads(bundle_paths.manifest_path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    legacy.pop("runtime_files")
    legacy["bundle"].pop("formal_page_sha256")
    bundle_paths.manifest_path.write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(BundleIntegrityError, match="manifest"):
        load_validated_bundle(bundle_paths.references_root)

    result = _build(source_repo, replace=True, paths=bundle_paths)
    assert result["installed"] is True
    assert load_validated_bundle(bundle_paths.references_root).manifest["schema_version"] == 2


def test_transaction_journal_blocks_runtime_loading(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    _build(source_repo, paths=bundle_paths)
    bundle_paths.journal_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(BundleIntegrityError, match="unfinished bundle transaction"):
        load_validated_bundle(bundle_paths.references_root)


def test_optional_runtime_file_must_be_declared_when_it_appears(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    _build(source_repo, paths=bundle_paths)
    (bundle_paths.skill_root / "NOTICE.md").write_text("late notice\n", encoding="utf-8")
    with pytest.raises(BundleIntegrityError, match="runtime_files"):
        load_validated_bundle(bundle_paths.references_root)
