from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "package_release.py"
REAL_COMPONENT = Path(__file__).parents[1] / "references" / "siril-manual"
SPEC = importlib.util.spec_from_file_location("deep_sky_siril_package_release", SCRIPT)
assert SPEC and SPEC.loader
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class ReleasePackageTests(unittest.TestCase):
    def _make_skill(self, base: Path) -> Path:
        root = base / "starun-siril"
        for relative_path in PACKAGE.STATIC_RELEASE_FILES:
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative_path == "SKILL.md":
                data = b"""---
name: starun-siril
description: Deterministic release fixture.
license: Proprietary
metadata:
  slug: starun-siril
  version: "0.1.0"
  displayName: Starun-siril
  summary: Fixture
  tags: [astronomy, siril]
  homepage: https://github.com/MZqk/deepsky-skill
---

# Fixture
"""
            elif relative_path == "agents/openai.yaml":
                data = b"""interface:
  display_name: "Starun-siril"
  short_description: "Agent-composed Siril CLI processing"
  default_prompt: "Use the fixture."
"""
            elif relative_path == "LICENSE.md":
                data = b"Copyright 2026 MZqk. All rights reserved.\n"
            else:
                data = f"fixture:{relative_path}\n".encode()
            path.write_bytes(data)

        shutil.copytree(
            REAL_COMPONENT,
            root / "references" / "siril-manual",
            copy_function=shutil.copyfile,
        )
        (root / "release-files.txt").write_text(
            "\n".join(PACKAGE.EXPECTED_ALLOWLIST_ENTRIES) + "\n", encoding="utf-8"
        )
        return root

    @staticmethod
    def _disabled_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("SkillHub must not run")

    def _build(self, root: Path, output: Path, **kwargs: object) -> dict[str, object]:
        return PACKAGE.build_release(
            output,
            skill_root=root,
            skillhub_command="",
            runner=self._disabled_runner,
            **kwargs,
        )

    def test_archive_has_exact_inventory_modes_and_ignores_extras(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            for relative_path in (
                "tests/secret.fit",
                "session/siril.log",
                "scripts/package_release.py",
                "CHANGELOG.md",
            ):
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("not public\n", encoding="utf-8")
            output = base / "dist" / "starun-siril-0.1.0.zip"

            receipt = self._build(root, output)

            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertEqual(
                    names, sorted(item["path"] for item in receipt["files"])
                )
                self.assertIn("references/ssf-provenance.schema.json", names)
                self.assertTrue(
                    all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
                )
                self.assertTrue(
                    all(info.date_time == PACKAGE.FIXED_ZIP_TIMESTAMP for info in archive.infolist())
                )
                self.assertTrue(
                    all(
                        ((info.external_attr >> 16) & 0o177777)
                        == PACKAGE.FIXED_FILE_MODE
                        for info in archive.infolist()
                    )
                )
            written = json.loads(
                output.with_suffix(".zip.release.json").read_text(encoding="utf-8")
            )
            self.assertEqual(written, receipt)
            self.assertGreater(len(receipt["files"]), len(PACKAGE.STATIC_RELEASE_FILES))
            self.assertEqual(receipt["schema"], PACKAGE.RECEIPT_SCHEMA)
            self.assertFalse(receipt["publishable"])
            self.assertEqual(receipt["legal_review"]["status"], "missing")
            self.assertEqual(receipt["components"][0]["id"], "siril-manual")
            self.assertEqual(
                receipt["result"], {"status": "success", "commit_marker": True}
            )

    def test_two_builds_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            first = base / "dist" / "first.zip"
            second = base / "dist" / "second.zip"

            first_receipt = self._build(root, first)
            second_receipt = self._build(root, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                first_receipt["archive"]["sha256"],
                second_receipt["archive"]["sha256"],
            )
            lines = "".join(
                f"{item['path']}:{item['sha256']}\n"
                for item in sorted(first_receipt["files"], key=lambda value: value["path"])
            ).encode()
            self.assertEqual(
                first_receipt["skillhub"]["content_hash"],
                hashlib.sha256(lines).hexdigest(),
            )

    def test_rejects_frontmatter_duplicate_and_identity_mismatches(self) -> None:
        mutations = {
            "duplicate name": (
                b"name: starun-siril\n",
                b"name: starun-siril\nname: starun-siril\n",
                "duplicate-key",
            ),
            "duplicate metadata version": (
                b'  version: "0.1.0"\n',
                b'  version: "0.1.0"\n  version: "0.1.0"\n',
                "duplicate-key",
            ),
            "quoted duplicate name": (
                b"name: starun-siril\n",
                b'name: starun-siril\n"name": attacker-skill\n',
                "unsupported YAML",
            ),
            "quoted duplicate metadata version": (
                b'  version: "0.1.0"\n',
                b'  version: "0.1.0"\n  \'version\': "9.9.9"\n',
                "unsupported YAML",
            ),
            "name": (b"name: starun-siril\n", b"name: wrong-skill\n", "name must"),
            "license": (b"license: Proprietary\n", b"license: MIT\n", "license must"),
            "slug": (b"  slug: starun-siril\n", b"  slug: wrong-skill\n", "metadata.slug"),
            "version": (b'  version: "0.1.0"\n', b'  version: "0.2.0"\n', "metadata.version"),
        }
        for case, (old, new, message) in mutations.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                root = self._make_skill(Path(raw_dir).resolve())
                skill = root / "SKILL.md"
                skill.write_bytes(skill.read_bytes().replace(old, new, 1))
                with self.assertRaisesRegex(PACKAGE.ReleaseError, message):
                    PACKAGE.validate_release_source(root)

    def test_rejects_agent_yaml_plain_or_quoted_duplicate_keys(self) -> None:
        mutations = {
            "plain duplicate": (
                b'  display_name: "Starun-siril"\n',
                b'  display_name: "Starun-siril"\n  display_name: "Attacker"\n',
                "duplicate-key",
            ),
            "quoted duplicate": (
                b'  short_description: "Agent-composed Siril CLI processing"\n',
                b'  short_description: "Agent-composed Siril CLI processing"\n'
                b'  "short_description": "Attacker"\n',
                "unsupported YAML",
            ),
        }
        for case, (old, new, message) in mutations.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                root = self._make_skill(Path(raw_dir).resolve())
                agent = root / "agents" / "openai.yaml"
                agent.write_bytes(agent.read_bytes().replace(old, new, 1))
                with self.assertRaisesRegex(PACKAGE.ReleaseError, message):
                    PACKAGE.validate_release_source(root)

    def test_rejects_changed_manifest_missing_file_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            manifest = root / "release-files.txt"
            manifest.write_text("SKILL.md\n", encoding="utf-8")
            with self.assertRaisesRegex(PACKAGE.ReleaseError, "exactly, in order"):
                PACKAGE.validate_release_source(root)

            manifest.write_text(
                "\n".join(PACKAGE.EXPECTED_ALLOWLIST_ENTRIES) + "\n", encoding="utf-8"
            )
            target = root / "references" / "quality.md"
            target.unlink()
            with self.assertRaisesRegex(PACKAGE.ReleaseError, "missing or unreadable"):
                PACKAGE.validate_release_source(root)

            external = base / "external.md"
            external.write_text("private\n", encoding="utf-8")
            target.symlink_to(external)
            with self.assertRaisesRegex(PACKAGE.ReleaseError, "symbolic link"):
                PACKAGE.validate_release_source(root)

    def test_component_closure_hashes_links_and_journal_fail_closed(self) -> None:
        cases = ("extra", "hash", "symlink", "journal")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                base = Path(raw_dir).resolve()
                root = self._make_skill(base)
                component = root / "references" / "siril-manual"
                if case == "extra":
                    (component / "extra.txt").write_text("extra\n", encoding="utf-8")
                    message = "inventory does not match"
                elif case == "hash":
                    (component / "NOTICE.md").write_text("tampered\n", encoding="utf-8")
                    message = "hash mismatch"
                elif case == "symlink":
                    target = base / "outside.txt"
                    target.write_text("outside\n", encoding="utf-8")
                    (component / "linked.txt").symlink_to(target)
                    message = "link"
                else:
                    journal = root / PACKAGE.COMPONENT_JOURNAL_PATH
                    journal.write_text("{}\n", encoding="utf-8")
                    message = "journal exists"
                with self.assertRaisesRegex(PACKAGE.ReleaseError, message):
                    PACKAGE.validate_release_source(root)

    def test_rejects_internal_existing_and_symlink_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            with self.assertRaisesRegex(PACKAGE.ReleaseError, "outside the skill root"):
                self._build(root, root / "release.zip")

            output = base / "release.zip"
            output.write_bytes(b"existing")
            with self.assertRaisesRegex(PACKAGE.ReleaseError, "--force"):
                self._build(root, output)
            self.assertEqual(output.read_bytes(), b"existing")

            external = base / "outside.zip"
            external.write_bytes(b"outside")
            link = base / "linked.zip"
            link.symlink_to(external)
            with self.assertRaisesRegex(PACKAGE.ReleaseError, "symbolic-link output"):
                self._build(root, link, force=True)
            self.assertEqual(external.read_bytes(), b"outside")

    def test_failed_receipt_commit_leaves_no_success_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            output = base / "release.zip"
            output.write_bytes(b"old archive")
            receipt_path = output.with_suffix(".zip.release.json")
            receipt_path.write_text(
                '{"result":{"status":"success","commit_marker":true}}',
                encoding="utf-8",
            )
            real_replace = PACKAGE.os.replace

            def fail_receipt(source: object, destination: object) -> None:
                if Path(destination) == receipt_path:
                    raise OSError("injected receipt failure")
                real_replace(source, destination)

            with mock.patch.object(PACKAGE.os, "replace", side_effect=fail_receipt):
                with self.assertRaisesRegex(PACKAGE.ReleaseError, "no success receipt"):
                    self._build(root, output, force=True)
            self.assertFalse(receipt_path.exists())
            with zipfile.ZipFile(output) as archive:
                expected = sorted(
                    item.relative_path for item in PACKAGE.validate_release_source(root)
                )
                self.assertEqual(
                    archive.namelist(), expected
                )


if __name__ == "__main__":
    unittest.main()
