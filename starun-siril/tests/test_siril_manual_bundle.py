from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "build_siril_manual.py"
SPEC = importlib.util.spec_from_file_location("deep_sky_siril_build_manual", SCRIPT)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class SirilManualBundleTests(unittest.TestCase):
    def test_checked_in_bundle_is_complete_and_pinned(self) -> None:
        lock = BUILDER.load_source_lock()
        manifest = BUILDER.validate_bundle(
            ROOT / "references" / "siril-manual", expected_lock=lock
        )
        self.assertEqual(manifest["manual"]["version"], "1.4.4")
        self.assertEqual(
            manifest["manual"]["commit"],
            "1550a31d325276124fe961368477c90d49df804b",
        )
        self.assertEqual(manifest["manual"]["rtd_build_id"], 34132359)
        self.assertEqual(
            manifest["manual"]["source_archive_sha256"],
            "13d19abb4f1309f53200820bfa8b9507219ba836edb3d79bc7045d7eb0fc40a0",
        )
        self.assertEqual(
            manifest["counts"],
            {
                "rst_files": 536,
                "include_dependencies": 8,
                "commands": 199,
                "sections": 994,
                "selected_images": 24,
                "component_files": 577,
            },
        )

    def test_text_dependency_and_license_closures_are_explicit(self) -> None:
        component = ROOT / "references" / "siril-manual"
        inventory = json.loads((component / "files.json").read_text(encoding="utf-8"))
        dependency_paths = {
            record["upstream_path"]
            for record in inventory["files"]
            if record["role"] == "include-dependency"
        }
        self.assertEqual(
            dependency_paths,
            {
                "doc/astrometry/cats.txt",
                "doc/installation/build_options.txt",
                "doc/preferences/getA.txt",
                "doc/processing/pm_im_functions.txt",
                "doc/processing/pm_operators.txt",
                "doc/processing/pm_px_functions.txt",
                "doc/scripts/gui_and_args_template.py",
                "doc/scripts/gui_template.py",
            },
        )
        self.assertEqual(
            BUILDER.load_source_lock()["source"]["expected_dependency_paths"],
            sorted(dependency_paths),
        )

        manifest = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["license"]["concluded"], "NOASSERTION")
        self.assertEqual(manifest["license"]["legal_review"], "required")
        self.assertEqual(
            [entry["path"] for entry in manifest["license"]["entries"]],
            ["LICENSE.GPL-3.0.txt", "LICENSE.GFDL-1.2.txt"],
        )
        self.assertEqual(
            manifest["license"]["entries"][1]["inferred_candidate_spdx"],
            "GFDL-1.2-no-invariants-only",
        )

    def test_inventory_binds_exact_upstream_bytes_with_git_blob_ids(self) -> None:
        component = ROOT / "references" / "siril-manual"
        inventory = json.loads((component / "files.json").read_text(encoding="utf-8"))
        source_records = [
            record for record in inventory["files"] if record["role"] == "source-rst"
        ]
        self.assertEqual(len(source_records), 536)
        for record in source_records:
            data = (component / record["path"]).read_bytes()
            blob = hashlib.sha1(
                b"blob " + str(len(data)).encode("ascii") + b"\0" + data
            ).hexdigest()
            self.assertEqual(record["upstream_blob"], blob)
            self.assertEqual(record["path"], "source/" + record["upstream_path"])

    def test_command_index_has_scriptability_and_policy_coverage(self) -> None:
        component = ROOT / "references" / "siril-manual"
        command_payload = json.loads((component / "commands.json").read_text(encoding="utf-8"))
        commands = {record["name"].casefold(): record for record in command_payload["commands"]}
        self.assertEqual(len(commands), 199)
        self.assertFalse(commands["addmax"]["scriptable"])
        self.assertTrue(commands["autostretch"]["scriptable"])
        self.assertIn("autostretch [-linked]", commands["autostretch"]["usage"])

        policy = json.loads((ROOT / "references" / "command-policy.json").read_text())
        allowed = {
            command.casefold()
            for protocol_commands in policy["protocol_commands"].values()
            for command in protocol_commands
        }
        self.assertLessEqual(allowed, commands.keys())
        self.assertTrue(all(commands[name]["scriptable"] for name in allowed))

        aliases = json.loads((component / "aliases.zh-en.json").read_text(encoding="utf-8"))
        aliased_commands = {
            target.removeprefix("command:")
            for item in aliases["aliases"]
            for target in item["target_ids"]
            if target.startswith("command:")
        }
        self.assertLessEqual(allowed, aliased_commands)

        aliased_pages = {
            target.removeprefix("page:")
            for item in aliases["aliases"]
            for target in item["target_ids"]
            if target.startswith("page:")
        }
        top_level_pages = {
            "doc/" + path.name
            for path in (component / "source" / "doc").glob("*.rst")
        }
        self.assertLessEqual(top_level_pages, aliased_pages)

    def test_selected_images_are_referenced_pngs_with_locked_reasons(self) -> None:
        component = ROOT / "references" / "siril-manual"
        payload = json.loads((component / "image-selection.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], BUILDER.IMAGE_SELECTION_SCHEMA)
        self.assertEqual(len(payload["selected"]), 24)
        for item in payload["selected"]:
            self.assertTrue(item["upstream_path"].endswith(".png"))
            self.assertTrue(item["references"])
            self.assertTrue(item["reason"])
            data = (component / item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])

    def test_network_downloader_explicitly_disables_proxies(self) -> None:
        data = b"fixed archive bytes"
        handlers: list[object] = []

        class Response:
            def __init__(self) -> None:
                self.offset = 0

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def geturl(self) -> str:
                return "https://gitlab.com/fixed/archive.tar.gz"

            def read(self, size: int) -> bytes:
                if self.offset:
                    return b""
                self.offset = len(data)
                return data

        class Opener:
            def open(self, request: object, timeout: int) -> Response:
                self.request = request
                self.timeout = timeout
                return Response()

        def fake_build_opener(*items: object) -> Opener:
            handlers.extend(items)
            return Opener()

        with mock.patch.object(BUILDER, "build_opener", side_effect=fake_build_opener):
            observed = BUILDER._download_archive(
                "https://gitlab.com/fixed/archive.tar.gz",
                hashlib.sha256(data).hexdigest(),
                1024,
            )
        self.assertEqual(observed, data)
        proxy_handlers = [item for item in handlers if isinstance(item, BUILDER.ProxyHandler)]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})

    def test_archive_reader_rejects_links_and_traversal(self) -> None:
        lock = BUILDER.load_source_lock()
        root = lock["source"]["archive_root"]

        def archive_with(member: tarfile.TarInfo) -> bytes:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                archive.addfile(member)
            return buffer.getvalue()

        link = tarfile.TarInfo(f"{root}/doc/link.rst")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../secret"
        with self.assertRaisesRegex(BUILDER.ManualBuildError, "link or special"):
            BUILDER.read_safe_tar(archive_with(link), lock)

        traversal = tarfile.TarInfo(f"{root}/../escape.rst")
        traversal.size = 0
        with self.assertRaisesRegex(BUILDER.ManualBuildError, "unsafe path"):
            BUILDER.read_safe_tar(archive_with(traversal), lock)

    def test_local_pinned_inputs_reject_leaf_and_parent_symlinks(self) -> None:
        data = b"pinned local input\n"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            real_parent = root / "real"
            real_parent.mkdir()
            real_file = real_parent / "input.bin"
            real_file.write_bytes(data)

            leaf_link = root / "leaf-link"
            leaf_link.symlink_to(real_file)
            with self.assertRaisesRegex(BUILDER.ManualBuildError, "links"):
                BUILDER._read_archive_bytes(leaf_link, digest, len(data))

            parent_link = root / "parent-link"
            parent_link.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(BUILDER.ManualBuildError, "links"):
                BUILDER._read_archive_bytes(parent_link / "input.bin", digest, len(data))

            self.assertEqual(
                BUILDER._read_archive_bytes(real_file, digest, len(data)), data
            )

    def test_local_pinned_input_detects_descriptor_drift(self) -> None:
        data = b"pinned local input\n"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve() / "input.bin"
            source.write_bytes(data)
            original_fstat = BUILDER.os.fstat
            calls = 0

            def drifting_fstat(fd: int) -> os.stat_result | SimpleNamespace:
                nonlocal calls
                calls += 1
                observed = original_fstat(fd)
                if calls == 2:
                    values = {
                        name: getattr(observed, name)
                        for name in (
                            "st_dev",
                            "st_ino",
                            "st_mode",
                            "st_size",
                            "st_mtime_ns",
                            "st_ctime_ns",
                        )
                    }
                    values["st_mtime_ns"] += 1
                    return SimpleNamespace(**values)
                return observed

            with mock.patch.object(BUILDER.os, "fstat", side_effect=drifting_fstat):
                with self.assertRaisesRegex(BUILDER.ManualBuildError, "changed while"):
                    BUILDER._read_archive_bytes(source, digest, len(data))

    def test_csv_table_file_dependency_accepts_blank_lines_and_tab_indentation(self) -> None:
        rst = {
            "doc/nested/page.rst": (
                "\t.. csv-table:: Functions\n"
                "\n"
                "\t\t:file: table.csv\n"
                "\t\t:header-rows: 1\n"
                "\n"
                "After\n"
            ).encode("utf-8")
        }
        includes, _, references = BUILDER._resolved_directives(rst)
        self.assertEqual(includes, {"doc/nested/table.csv"})
        self.assertEqual(
            references,
            {
                "doc/nested/page.rst": [
                    {
                        "path": "doc/nested/table.csv",
                        "line": 3,
                        "directive": "csv-table",
                    }
                ]
            },
        )

    def test_transaction_rolls_back_old_bundle_at_each_commit_phase(self) -> None:
        for fail_at in ("after_journal", "after_old_moved", "after_new_installed"):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as directory:
                references = Path(directory) / "references"
                references.mkdir()
                target = references / "siril-manual"
                target.mkdir()
                (target / "marker").write_text("old", encoding="utf-8")
                staging = references / ".siril-manual.staging-test"
                staging.mkdir()
                (staging / "marker").write_text("new", encoding="utf-8")

                with self.assertRaisesRegex(BUILDER.ManualBuildError, "rolled back"):
                    BUILDER.transactional_commit(staging, target, fail_at=fail_at)
                self.assertEqual((target / "marker").read_text(encoding="utf-8"), "old")
                self.assertFalse(staging.exists())
                self.assertFalse((references / BUILDER.JOURNAL_NAME).exists())

    def test_journal_presence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            references = Path(directory) / "references"
            component = references / "siril-manual"
            component.mkdir(parents=True)
            (references / BUILDER.JOURNAL_NAME).write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(BUILDER.ManualBuildError, "journal exists"):
                BUILDER.validate_bundle(component)


if __name__ == "__main__":
    unittest.main()
