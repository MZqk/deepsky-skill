from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "package_release.py"
SPEC = importlib.util.spec_from_file_location("package_release", SCRIPT)
assert SPEC and SPEC.loader
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class ReleasePackageTests(unittest.TestCase):
    def _make_skill(self, base: Path) -> Path:
        root = base / "siril-mosaic"
        payloads = {
            "LICENSE.md": b"Copyright 2026 MZqk. All rights reserved.\n",
            "SKILL.md": b"---\nname: siril-mosaic\nlicense: Proprietary\n---\n",
            "agents/openai.yaml": b"interface:\n  display_name: Siril Mosaic\n",
            "references/quality.md": b"quality\n",
            "references/workflow.md": b"workflow\n",
            "scripts/siril_mosaic.py": b"print('mosaic')\n",
        }
        for relative_path, data in payloads.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (root / "release-files.txt").write_text(
            "\n".join(PACKAGE.EXPECTED_RELEASE_FILES) + "\n", encoding="utf-8"
        )
        return root

    @staticmethod
    def _disabled_skillhub_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("SkillHub must not run in this test")

    def _build(self, root: Path, output: Path, **kwargs: object) -> dict[str, object]:
        return PACKAGE.build_release(
            output,
            skill_root=root,
            skillhub_command="",
            runner=self._disabled_skillhub_runner,
            **kwargs,
        )

    def test_archive_inventory_is_exact_and_ignores_unlisted_data(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            extra_files = {
                "tests/test_siril_mosaic.py": b"test",
                "dev-runs/session/panel.fit": b"FITS",
                "dev-runs/session/siril.log": b"log",
                "dev-runs/session/initfile.ini": b"config",
                "extra.jpg": b"jpeg",
            }
            for relative_path, data in extra_files.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            output = base / "dist" / "siril-mosaic-0.1.0.zip"

            receipt = self._build(root, output)

            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), list(PACKAGE.EXPECTED_RELEASE_FILES))
                self.assertEqual(
                    archive.read("LICENSE.md"),
                    b"Copyright 2026 MZqk. All rights reserved.\n",
                )
                self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
                self.assertTrue(all(info.date_time == PACKAGE.FIXED_ZIP_TIMESTAMP for info in archive.infolist()))
                self.assertTrue(
                    all(
                        ((info.external_attr >> 16) & 0o177777)
                        == PACKAGE.FIXED_FILE_MODE
                        for info in archive.infolist()
                    )
                )
            receipt_path = output.with_suffix(".zip.release.json")
            self.assertTrue(receipt_path.is_file())
            written = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(written, receipt)
            self.assertEqual(
                written["result"], {"status": "success", "commit_marker": True}
            )

    def test_two_builds_have_identical_archive_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            first = base / "dist" / "first.zip"
            second = base / "dist" / "second.zip"

            first_receipt = self._build(root, first)
            second_receipt = self._build(root, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_receipt["archive"]["sha256"], second_receipt["archive"]["sha256"])

    def test_receipt_content_hash_matches_skillhub_algorithm(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            output = base / "dist" / "release.zip"

            receipt = self._build(root, output)

            lines = "".join(
                f"{item['path']}:{item['sha256']}\n" for item in receipt["files"]
            ).encode("utf-8")
            self.assertEqual(receipt["skillhub"]["content_hash"], hashlib.sha256(lines).hexdigest())

    def test_rejects_missing_file_without_leaving_archive(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            (root / "references" / "quality.md").unlink()
            output = base / "dist" / "release.zip"

            with self.assertRaises(PACKAGE.ReleaseError):
                self._build(root, output)

            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".zip.release.json").exists())

    def test_rejects_allowlisted_file_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            external = base / "external.md"
            external.write_text("private", encoding="utf-8")
            target = root / "references" / "quality.md"
            target.unlink()
            target.symlink_to(external)

            with self.assertRaisesRegex(PACKAGE.ReleaseError, "symbolic link"):
                PACKAGE.validate_release_source(root)

    def test_rejects_allowlist_manifest_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            manifest = root / "release-files.txt"
            external = base / "external-release-files.txt"
            external.write_bytes(manifest.read_bytes())
            manifest.unlink()
            manifest.symlink_to(external)

            with self.assertRaisesRegex(PACKAGE.ReleaseError, "symbolic link"):
                PACKAGE.validate_release_source(root)

    def test_rejects_duplicate_absolute_and_backslash_allowlist_paths(self) -> None:
        invalid_manifests = {
            "duplicate": list(PACKAGE.EXPECTED_RELEASE_FILES) + ["SKILL.md"],
            "absolute": ["/private/data.fits"],
            "backslash": [r"scripts\siril_mosaic.py"],
        }
        for case, entries in invalid_manifests.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                root = self._make_skill(Path(raw_dir).resolve())
                (root / "release-files.txt").write_text(
                    "\n".join(entries) + "\n", encoding="utf-8"
                )

                with self.assertRaises(PACKAGE.ReleaseError):
                    PACKAGE.validate_release_source(root)

    def test_rejects_symlink_directory_and_invalid_allowlist_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            external = base / "external"
            external.mkdir()
            (external / "quality.md").write_text("private", encoding="utf-8")
            (root / "references" / "quality.md").unlink()
            (root / "references" / "workflow.md").unlink()
            (root / "references").rmdir()
            (root / "references").symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(PACKAGE.ReleaseError, "symbolic link"):
                PACKAGE.validate_release_source(root)

            (root / "references").unlink()
            (root / "references").mkdir()
            (root / "references" / "quality.md").write_text("quality", encoding="utf-8")
            (root / "release-files.txt").write_text(
                "SKILL.md\n../private\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(PACKAGE.ReleaseError, "invalid allowlist path"):
                PACKAGE.validate_release_source(root)

    def test_rejects_internal_output_and_overwrite_without_force(self) -> None:
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

    def test_rejects_symlink_output_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            external = base / "external.zip"
            external.write_bytes(b"outside")
            output = base / "release.zip"
            output.symlink_to(external)

            with self.assertRaisesRegex(PACKAGE.ReleaseError, "symbolic-link output"):
                self._build(root, output, force=True)
            self.assertEqual(external.read_bytes(), b"outside")

    def test_rejects_symlink_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            real_output = base / "real-output"
            real_output.mkdir()
            linked_output = base / "link-output"
            linked_output.symlink_to(real_output, target_is_directory=True)

            with self.assertRaisesRegex(PACKAGE.ReleaseError, "output path component"):
                self._build(root, linked_output / "release.zip")

            self.assertFalse((real_output / "release.zip").exists())

    def test_force_replaces_existing_archive_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            output = base / "release.zip"
            receipt_path = output.with_suffix(".zip.release.json")
            output.write_bytes(b"existing")
            receipt_path.write_bytes(b"existing receipt")

            receipt = self._build(root, output, force=True)

            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), receipt["archive"]["sha256"])
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), receipt)

    def test_force_second_replace_failure_cannot_leave_old_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            output = base / "release.zip"
            receipt_path = output.with_suffix(".zip.release.json")
            output.write_bytes(b"old archive")
            receipt_path.write_text(
                '{"result":{"status":"success","commit_marker":true}}',
                encoding="utf-8",
            )
            real_replace = PACKAGE.os.replace

            def fail_receipt_replace(source: object, destination: object) -> None:
                if Path(destination) == receipt_path:
                    raise OSError("injected receipt replace failure")
                real_replace(source, destination)

            with mock.patch.object(PACKAGE.os, "replace", side_effect=fail_receipt_replace):
                with self.assertRaisesRegex(PACKAGE.ReleaseError, "no success receipt"):
                    self._build(root, output, force=True)

            self.assertNotEqual(output.read_bytes(), b"old archive")
            self.assertFalse(receipt_path.exists())
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), list(PACKAGE.EXPECTED_RELEASE_FILES))

    def test_preflight_uses_only_dry_run_and_validates_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            output = base / "release.zip"
            calls: list[list[str]] = []

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                if command[1:] == ["--version"]:
                    return subprocess.CompletedProcess(command, 0, "skillhub 2026.8.5\n", "")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {"dryRun": True, "slug": "siril-mosaic", "version": "0.1.0"}
                    ),
                    "",
                )

            receipt = PACKAGE.build_release(
                output,
                skill_root=root,
                skillhub_preflight=True,
                skillhub_command="/mock/skillhub",
                runner=runner,
            )

            self.assertEqual(calls[0], ["/mock/skillhub", "--version"])
            self.assertEqual(calls[1][0:2], ["/mock/skillhub", "publish"])
            self.assertEqual(calls[1][-2:], ["--dry-run", "--json"])
            self.assertEqual(receipt["skillhub"]["cli_version"], "skillhub 2026.8.5")
            self.assertTrue(receipt["skillhub"]["dry_run"]["dryRun"])

    def test_failed_preflight_leaves_no_new_archive_or_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            output = base / "release.zip"

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[1:] == ["--version"]:
                    return subprocess.CompletedProcess(command, 0, "skillhub 2026.8.5\n", "")
                return subprocess.CompletedProcess(command, 0, '{"dryRun":false}', "")

            with self.assertRaisesRegex(PACKAGE.ReleaseError, "unexpected SkillHub"):
                PACKAGE.build_release(
                    output,
                    skill_root=root,
                    skillhub_preflight=True,
                    skillhub_command="/mock/skillhub",
                    runner=runner,
                )

            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".zip.release.json").exists())

    def test_archive_inventory_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            base = Path(raw_dir).resolve()
            root = self._make_skill(base)
            output = base / "release.zip"
            self._build(root, output)
            release_files = PACKAGE.validate_release_source(root)

            with zipfile.ZipFile(output, mode="a") as archive:
                archive.writestr("unexpected.txt", b"not allowlisted")

            with self.assertRaisesRegex(PACKAGE.ReleaseError, "inventory mismatch"):
                PACKAGE.verify_release_archive(output, release_files)


if __name__ == "__main__":
    unittest.main()
