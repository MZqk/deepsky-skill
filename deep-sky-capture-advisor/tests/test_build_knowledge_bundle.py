from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import build_knowledge_bundle as builder
from knowledge_common import (
    BundleIntegrityError,
    load_validated_bundle,
    strict_json_loads,
    validate_bundle,
    validate_human_verification,
)


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
    _run_git(source, "config", "user.name", "Bundle Test")
    _run_git(source, "config", "user.email", "bundle@example.test")
    (source / ".gitignore").write_text("*.ignored.md\n", encoding="utf-8")
    for number, category in enumerate(builder.INCLUDED_DIRS):
        directory = source / category
        directory.mkdir()
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


def test_check_reads_commit_blobs_without_source_indexes_or_source_code(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    commit = _run_git(source_repo, "rev-parse", "HEAD")
    result = builder.build(
        source_repo,
        check=True,
        expect_source_commit=commit,
        paths=bundle_paths,
    )
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
    result = builder.build(source_repo, paths=bundle_paths)
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
    first = next((source_repo / builder.INCLUDED_DIRS[0]).glob("*.md"))
    first.write_text(first.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8")
    with pytest.raises(builder.BundleError, match="completely clean"):
        builder.build(source_repo, check=True, paths=bundle_paths)

    _run_git(source_repo, "restore", ".")
    ignored = source_repo / builder.INCLUDED_DIRS[0] / "extra.ignored.md"
    ignored.write_text(_page(builder.INCLUDED_DIRS[0], 99), encoding="utf-8")
    assert _run_git(source_repo, "status", "--porcelain") == ""
    with pytest.raises(builder.BundleError, match="extra or ignored"):
        builder.build(source_repo, check=True, paths=bundle_paths)


def test_committed_formal_symlink_is_rejected(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    link = source_repo / builder.INCLUDED_DIRS[0] / "linked.md"
    try:
        link.symlink_to("页面-00.md")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    _run_git(source_repo, "add", str(link.relative_to(source_repo)))
    _run_git(source_repo, "commit", "-qm", "add malicious formal symlink")
    with pytest.raises(builder.BundleError, match="regular Git blob"):
        builder.build(source_repo, check=True, paths=bundle_paths)


def test_expected_hash_mismatch_has_no_install_side_effect(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    with pytest.raises(builder.BundleError, match="Formal page SHA-256 mismatch"):
        builder.build(
            source_repo,
            expect_formal_sha256="0" * 64,
            paths=bundle_paths,
        )
    assert not bundle_paths.references_root.exists()


def test_manifest_authority_counts_and_runtime_file_set_are_recomputed(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    builder.build(source_repo, paths=bundle_paths)
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
    builder.build(source_repo, paths=bundle_paths)
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
    builder.build(source_repo, paths=bundle_paths)
    previous = load_validated_bundle(bundle_paths.references_root)

    page = next((source_repo / builder.INCLUDED_DIRS[0]).glob("*.md"))
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
        builder.build(source_repo, replace=True, paths=bundle_paths)
    restored = load_validated_bundle(bundle_paths.references_root)
    assert restored.fingerprint == previous.fingerprint
    assert not bundle_paths.journal_path.exists()


def test_catalog_path_and_extra_knowledge_page_are_rejected(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    builder.build(source_repo, paths=bundle_paths)
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
    builder.build(source_repo, paths=bundle_paths)
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

    result = builder.build(source_repo, replace=True, paths=bundle_paths)
    assert result["installed"] is True
    assert load_validated_bundle(bundle_paths.references_root).manifest["schema_version"] == 2


def test_transaction_journal_blocks_runtime_loading(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    builder.build(source_repo, paths=bundle_paths)
    bundle_paths.journal_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(BundleIntegrityError, match="unfinished bundle transaction"):
        load_validated_bundle(bundle_paths.references_root)


def test_optional_runtime_file_must_be_declared_when_it_appears(
    source_repo: Path,
    bundle_paths: builder.BundlePaths,
) -> None:
    builder.build(source_repo, paths=bundle_paths)
    (bundle_paths.skill_root / "NOTICE.md").write_text("late notice\n", encoding="utf-8")
    with pytest.raises(BundleIntegrityError, match="runtime_files"):
        load_validated_bundle(bundle_paths.references_root)
