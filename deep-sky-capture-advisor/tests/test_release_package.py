from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from package_release import (  # noqa: E402
    FIXED_ZIP_TIME,
    ReleasePackageError,
    _skill_metadata,
    build_release,
    collect_release_files,
)


EXPECTED_AUTHORIZATION = {
    "schema_version": 1,
    "skill_name": "deep-sky-capture-advisor",
    "slug": "deep-sky-capture-advisor",
    "version": "0.1.0",
    "displayName": "深空摄影知识顾问",
    "license": "Proprietary",
    "summary": "面向 SkillHub 公开分发的中文深空摄影知识顾问非权威测试版，基于内置可追溯快照回答规划、拍摄、后期与排障问题。",
    "tags": ["astronomy", "astrophotography", "deep-sky", "siril", "chinese"],
    "homepage": "https://github.com/MZqk/skills",
    "authorization_scope": "skillhub-publication:deep-sky-capture-advisor@0.1.0",
    "source_commit": "d4094fb5e7811f0cea072344f3d1dfae08d3a2b5",
    "catalog_sha256": "5ec7724359b9ec9062b9fac42f87e136dec0238677eec5ed23b5f341b426c012",
    "knowledge_sha256": "419443bbb2aa84a1766a5e9e834e975dd60be2a324a14a7699186bf9b98d3ddb",
    "authority": "nonauthoritative",
    "non_authoritative_disclosure": "非权威参考：内置依据尚未完成人工签署、已过期或超出核验范围。",
    "distribution_target": "SkillHub public beta",
    "authorized_on": "2026-08-28",
    "authorization_basis": "explicit-user-instruction",
    "future_changes_automatically_authorized": False,
    "public_publication_authorized": True,
    "skillhub_publication_authorized": True,
    "other_publication_channels_authorized": False,
}

EXPECTED_STATIC_ARCHIVE_FILES = {
    "SKILL.md",
    "NOTICE.md",
    "release-authorization.json",
    "agents/openai.yaml",
    "scripts/knowledge_common.py",
    "scripts/query_knowledge.py",
    "references/catalog.json",
    "references/manifest.json",
}


def copy_skill(tmp_path: Path, name: str = "skill") -> Path:
    destination = tmp_path / name
    shutil.copytree(SKILL_ROOT, destination, symlinks=True)
    return destination


def archive_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def independent_skillhub_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            file_sha = hashlib.sha256(archive.read(name)).hexdigest()
            digest.update(f"{name}:{file_sha}\n".encode("utf-8"))
    return digest.hexdigest()


def test_authorization_is_exact_snapshot_specific_public_beta_lock() -> None:
    authorization = json.loads(
        (SKILL_ROOT / "release-authorization.json").read_text(encoding="utf-8")
    )
    assert authorization == EXPECTED_AUTHORIZATION
    assert not {
        "content_hash",
        "skillhub_content_hash",
        "official_skillhub_content_hash",
    } & authorization.keys()


def test_notice_preserves_proprietary_and_third_party_rights_boundaries() -> None:
    notice = (SKILL_ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assert "proprietary" in notice
    assert "third-party" in notice
    assert "deep-sky-capture-advisor@0.1.0" in notice
    assert "SkillHub" in notice
    assert "future version" in notice


def test_skillhub_metadata_uses_quick_validate_compatible_frontmatter_shape() -> None:
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = skill_text.split("---", 2)[1]
    top_level_keys = {
        line.split(":", 1)[0]
        for line in frontmatter.splitlines()
        if line and not line.startswith(" ") and ":" in line
    }
    assert "metadata" in top_level_keys
    assert "license" in top_level_keys
    assert not {"slug", "version", "displayName", "summary", "tags", "homepage"} & top_level_keys
    assert _skill_metadata(SKILL_ROOT) == {
        "slug": "deep-sky-capture-advisor",
        "version": "0.1.0",
        "displayName": "深空摄影知识顾问",
        "summary": "面向 SkillHub 公开分发的中文深空摄影知识顾问非权威测试版，基于内置可追溯快照回答规划、拍摄、后期与排障问题。",
        "tags": ["astronomy", "astrophotography", "deep-sky", "siril", "chinese"],
        "homepage": "https://github.com/MZqk/skills",
        "license": "Proprietary",
    }


def test_skill_metadata_does_not_treat_name_as_an_implicit_slug(tmp_path: Path) -> None:
    copied = copy_skill(tmp_path)
    skill_path = copied / "SKILL.md"
    lines = skill_path.read_text(encoding="utf-8").splitlines()
    skill_path.write_text(
        "\n".join(line for line in lines if not line.startswith("  slug:")) + "\n",
        encoding="utf-8",
    )
    assert _skill_metadata(copied)["slug"] is None


def test_release_zip_is_byte_deterministic_and_uses_official_content_hash_algorithm(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.zip"
    second_path = tmp_path / "second.zip"
    first = build_release(SKILL_ROOT, first_path)
    second = build_release(SKILL_ROOT, second_path)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["archive_sha256"] == second["archive_sha256"]
    assert first["runtime_tree_sha256"] == second["runtime_tree_sha256"]
    assert first["skillhub_content_hash"] == independent_skillhub_content_hash(first_path)
    assert second["skillhub_content_hash"] == independent_skillhub_content_hash(second_path)
    assert first["skillhub_content_hash_algorithm"] == "sha256(sorted path:sha256\\n records)"
    assert first["external_publication_performed"] is False

    with zipfile.ZipFile(first_path) as archive:
        assert archive.comment == b""
        assert archive.namelist() == sorted(archive.namelist())
        for info in archive.infolist():
            assert info.date_time == FIXED_ZIP_TIME
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.extra == b""
            assert info.comment == b""
            assert ((info.external_attr >> 16) & 0o777) == 0o644


def test_release_zip_contains_only_explicit_runtime_allowlist(tmp_path: Path) -> None:
    copied = copy_skill(tmp_path)
    (copied / ".env").write_text("TOKEN=must-not-ship\n", encoding="utf-8")
    (copied / "secrets.json").write_text('{"token":"must-not-ship"}\n', encoding="utf-8")
    cache = copied / "scripts" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "query_knowledge.cpython-312.pyc").write_bytes(b"cache")
    (copied / "tests" / "private-fixture.txt").write_text("secret\n", encoding="utf-8")

    output = tmp_path / "release.zip"
    build_release(copied, output)
    names = archive_names(output)
    expected_knowledge = {
        path.relative_to(copied).as_posix()
        for path in (copied / "references" / "knowledge").rglob("*.md")
    }
    assert set(names) == EXPECTED_STATIC_ARCHIVE_FILES | expected_knowledge
    assert len(names) == 60
    assert all("build_knowledge_bundle.py" not in name for name in names)
    assert all("package_release.py" not in name for name in names)
    assert all(not name.startswith("tests/") for name in names)
    assert all("__pycache__" not in name and not name.endswith(".pyc") for name in names)
    assert ".env" not in names
    assert "secrets.json" not in names


@pytest.mark.parametrize("relative", ["cache.bin", ".release-transaction.md", "page.tmp"])
def test_release_rejects_non_markdown_or_transaction_files_in_knowledge(
    tmp_path: Path,
    relative: str,
) -> None:
    copied = copy_skill(tmp_path)
    (copied / "references" / "knowledge" / relative).write_text("not releasable\n", encoding="utf-8")
    with pytest.raises(ReleasePackageError, match="transaction marker|non-Markdown"):
        collect_release_files(copied)
    with pytest.raises(ReleasePackageError):
        build_release(copied, tmp_path / "rejected.zip")


def test_extracted_release_is_a_self_contained_queryable_skill(tmp_path: Path) -> None:
    output = tmp_path / "release.zip"
    build_release(SKILL_ROOT, output)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(output) as archive:
        archive.extractall(extracted)

    assert (extracted / "SKILL.md").is_file()
    assert not (extracted / "scripts" / "build_knowledge_bundle.py").exists()
    assert not (extracted / "scripts" / "package_release.py").exists()
    assert not (extracted / "tests").exists()
    completed = subprocess.run(
        [sys.executable, "-B", str(extracted / "scripts" / "query_knowledge.py"), "--verify-bundle"],
        cwd=extracted,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_release_fails_closed_on_authorization_or_knowledge_drift(tmp_path: Path) -> None:
    unauthorized = copy_skill(tmp_path, "unauthorized")
    authorization_path = unauthorized / "release-authorization.json"
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    authorization["future_changes_automatically_authorized"] = True
    authorization_path.write_text(
        json.dumps(authorization, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(
        ReleasePackageError,
        match="future_changes_automatically_authorized|runtime_files.*release-authorization",
    ):
        build_release(unauthorized, tmp_path / "unauthorized.zip")

    tampered = copy_skill(tmp_path, "tampered")
    page = next(
        path
        for path in sorted((tampered / "references" / "knowledge").rglob("*.md"))
        if path.name != "index.md"
    )
    page.write_text(page.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")
    with pytest.raises(
        ReleasePackageError,
        match="knowledge_sha256|Knowledge SHA-256 mismatch|page SHA-256 does not match catalog",
    ):
        build_release(tampered, tmp_path / "tampered.zip")


def test_release_fails_closed_when_runtime_query_is_tampered(tmp_path: Path) -> None:
    tampered = copy_skill(tmp_path)
    query = tampered / "scripts" / "query_knowledge.py"
    query.write_text(query.read_text(encoding="utf-8") + "\n# runtime tamper\n", encoding="utf-8")
    with pytest.raises(
        ReleasePackageError,
        match=r"runtime_files\.scripts/query_knowledge\.py.*SHA-256 mismatch",
    ):
        build_release(tampered, tmp_path / "runtime-tampered.zip")


def test_release_refuses_to_overwrite_an_existing_archive(tmp_path: Path) -> None:
    output = tmp_path / "release.zip"
    output.write_bytes(b"keep me")
    with pytest.raises(ReleasePackageError, match="Refusing to overwrite"):
        build_release(SKILL_ROOT, output)
    assert output.read_bytes() == b"keep me"
