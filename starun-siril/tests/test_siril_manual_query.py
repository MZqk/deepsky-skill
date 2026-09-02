from __future__ import annotations

import hashlib
from contextlib import ExitStack, contextmanager
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL = Path(__file__).parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import query_siril_manual as query  # noqa: E402
import siril_manual_bundle as bundle  # noqa: E402


_FIXTURE_TRUST: dict[str, dict[str, str]] = {}


@contextmanager
def _trust_fixture(root: Path):
    anchors = _FIXTURE_TRUST[str(root.resolve())]
    with ExitStack() as stack:
        for name, value in anchors.items():
            stack.enter_context(mock.patch.object(bundle, name, value))
        yield


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_fixture(base: Path, *, include_cycle: bool = False) -> Path:
    root = base / "starun-siril"
    component = root / "references" / "siril-manual"
    component.mkdir(parents=True)

    commands_rst = b"Commands\n========\n\nPinned command documentation.\n"
    page_rst = (
        b"Background Extraction\n=====================\n\n"
        b"Use a background model conservatively.\n\n.. include:: include.rst\n\n"
        b".. csv-table:: Parameters\n"
        b"   :file: table.txt\n"
        b"   :delim: 0x09\n"
    )
    include_rst = (
        b".. include:: page.rst\n"
        if include_cycle
        else b"SPCC, GHS, denoise, and deconvolution are documented here.\n"
    )
    section_note = b"Derived section dependency.\n"
    table_data = b"name\tmeaning\natan2\ttwo-argument arctangent\n"
    source_files = {
        "source/doc/Commands.rst": commands_rst,
        "source/doc/page.rst": page_rst,
        "source/doc/include.rst": include_rst,
        "source/doc/section-note.txt": section_note,
        "source/doc/table.txt": table_data,
    }
    for relative, data in source_files.items():
        _write(component / relative, data)

    source_hash = hashlib.sha256(commands_rst).hexdigest()
    page_hash = hashlib.sha256(page_rst).hexdigest()
    section_note_hash = hashlib.sha256(section_note).hexdigest()
    table_hash = hashlib.sha256(table_data).hexdigest()
    command_names = ["autostretch", "addmax", "manualcmd"] + [
        f"cmd{index:03d}" for index in range(196)
    ]
    command_records = [
        {
            "description": (
                "Automatic display stretch."
                if name == "autostretch"
                else "Pinned Siril command description."
            ),
            "name": name,
            "path": "doc/Commands.rst",
            "scriptable": name != "addmax",
            "section_id": "section:commands",
            "source_sha256": source_hash,
            "title": name,
            "usage": f"{name} [arguments]",
        }
        for name in command_names
    ]
    commands_document = {
        "schema": query.COMMANDS_SCHEMA,
        "commands": command_records,
    }
    catalog_document = {
        "schema": query.CATALOG_SCHEMA,
        "records": [
            {
                "aliases": [],
                "headings": ["Background Extraction"],
                "id": "page:background",
                "kind": "page",
                "path": "doc/page.rst",
                "search_text": "background extraction SPCC GHS denoise deconvolution",
                "section": "Processing",
                "source_sha256": page_hash,
                "title": "Background Extraction",
                "dependencies": [
                    {
                        "path": "doc/table.txt",
                        "sha256": table_hash,
                    }
                ],
            },
            *[
                {
                    "aliases": [],
                    "headings": [name],
                    "id": f"command:{name}",
                    "kind": "command",
                    "path": "doc/Commands.rst",
                    "search_text": record["description"] + " " + record["usage"],
                    "section": "Commands",
                    "source_sha256": source_hash,
                    "title": name,
                }
                for name, record in zip(command_names, command_records)
            ],
        ],
    }
    section_body = "Pinned command documentation."
    section = {
        "body": section_body,
        "end_line": 4,
        "heading": "Commands",
        "id": "section:commands",
        "path": "doc/Commands.rst",
        "schema": query.SECTION_SCHEMA,
        "sha256": hashlib.sha256(section_body.encode()).hexdigest(),
        "source_sha256": source_hash,
        "start_line": 1,
        "title": "Commands",
        "dependencies": [
            {
                "path": "doc/section-note.txt",
                "sha256": section_note_hash,
            }
        ],
    }
    aliases_document = {
        "schema": query.ALIASES_SCHEMA,
        "aliases": [
            {
                "alias": "背景提取",
                "language": "zh-CN",
                "reviewed": True,
                "target_ids": ["page:background"],
            },
            {
                "alias": "自动拉伸",
                "language": "zh-CN",
                "reviewed": True,
                "target_ids": ["command:autostretch"],
            },
        ],
    }
    image_selection = {
        "schema": query.IMAGE_SELECTION_SCHEMA,
        "policy": {
            "coverage": "selected",
            "exclude": ["decorative"],
            "formats": ["png"],
            "include": ["cli-knowledge"],
        },
        "selected": [],
        "omitted_local_references": [],
    }
    generated = {
        "catalog.json": _json_bytes(catalog_document),
        "commands.json": _json_bytes(commands_document),
        "sections.jsonl": _json_bytes(section),
        "aliases.zh-en.json": _json_bytes(aliases_document),
        "image-selection.json": _json_bytes(image_selection),
        "LICENSE.GPL-3.0.txt": (
            SKILL / "references/siril-manual/LICENSE.GPL-3.0.txt"
        ).read_bytes(),
        "LICENSE.GFDL-1.2.txt": (
            SKILL / "references/siril-manual/LICENSE.GFDL-1.2.txt"
        ).read_bytes(),
        "NOTICE.md": b"Official Siril documentation component.\n",
        "MODIFICATIONS.md": b"Indexes and selected-image policy are derived.\n",
    }
    for relative, data in generated.items():
        _write(component / relative, data)

    record_paths = sorted([*generated, *source_files])
    records: list[dict[str, object]] = []
    for relative in record_paths:
        data = (component / relative).read_bytes()
        record: dict[str, object] = {
            "path": relative,
            "role": (
                "upstream_rst"
                if relative.endswith(".rst")
                else "upstream_dependency"
                if relative.startswith("source/doc/")
                else "derived_metadata"
            ),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
        if relative.startswith("source/doc/"):
            record["upstream_path"] = relative[len("source/") :]
            record["upstream_blob"] = hashlib.sha1(
                b"blob " + str(len(data)).encode("ascii") + b"\0" + data
            ).hexdigest()
        records.append(record)
    files_document = {"schema": bundle.FILES_SCHEMA, "files": records}
    files_data = _json_bytes(files_document)
    _write(component / bundle.FILES_NAME, files_data)

    index_metadata = {
        name: {
            "path": path,
            "sha256": hashlib.sha256((component / path).read_bytes()).hexdigest(),
            "size_bytes": (component / path).stat().st_size,
        }
        for name, path in bundle.EXPECTED_INDEX_PATHS.items()
    }
    manifest = {
        "schema": bundle.MANIFEST_SCHEMA,
        "component": {"id": "siril-manual", "version": bundle.MANUAL_VERSION},
        "manual": {
            "version": bundle.MANUAL_VERSION,
            "commit": bundle.MANUAL_COMMIT,
            "commit_time": bundle.EXPECTED_COMMIT_TIME,
            "source_url": bundle.EXPECTED_SOURCE_URL,
            "source_archive_url": bundle.EXPECTED_SOURCE_ARCHIVE_URL,
            "source_archive_sha256": bundle.EXPECTED_SOURCE_ARCHIVE_SHA256,
            "rtd_build_id": int(bundle.RTD_BUILD_ID),
            "rtd_url": bundle.EXPECTED_RTD_URL,
        },
        "license": {
            "concluded": "NOASSERTION",
            "legal_review": "required",
            "entries": [
                {
                    "id": "GPL-3.0-only",
                    "path": "LICENSE.GPL-3.0.txt",
                    "sha256": hashlib.sha256(
                        generated["LICENSE.GPL-3.0.txt"]
                    ).hexdigest(),
                    "scope": (
                        "siril-doc upstream material except separately "
                        "attributed content"
                    ),
                },
                {
                    "id": "LicenseRef-MuniPack-GNU-FDL-version-unspecified",
                    "inferred_candidate_spdx": "GFDL-1.2-no-invariants-only",
                    "path": "LICENSE.GFDL-1.2.txt",
                    "sha256": hashlib.sha256(
                        generated["LICENSE.GFDL-1.2.txt"]
                    ).hexdigest(),
                    "source_url": "https://ftp.gnu.org/gnu/Licenses/fdl-1.2.txt",
                    "applies_to": [
                        "source/doc/photometry/general.rst#munipack-derived-excerpt"
                    ],
                },
            ],
        },
        "files": {
            "path": bundle.FILES_NAME,
            "sha256": hashlib.sha256(files_data).hexdigest(),
            "size_bytes": len(files_data),
        },
        "tree": {
            "algorithm": bundle.TREE_ALGORITHM,
            "sha256": bundle.tree_digest(records),
            "file_count": len(records),
        },
        "indexes": index_metadata,
        "coverage": {"rst": "complete_at_pinned_commit", "image": "selected"},
        "counts": {
            "rst_files": sum(path.endswith(".rst") for path in source_files),
            "include_dependencies": sum(
                not path.endswith(".rst") for path in source_files
            ),
            "commands": len(command_records),
            "sections": 1,
            "selected_images": 0,
            "component_files": len(records),
        },
        "build": {"builder_version": "fixture-v1"},
    }
    manifest_bytes = _json_bytes(manifest)
    _write(component / bundle.MANIFEST_NAME, manifest_bytes)
    _FIXTURE_TRUST[str(root.resolve())] = {
        "EXPECTED_MANIFEST_SHA256": hashlib.sha256(manifest_bytes).hexdigest(),
        "EXPECTED_FILES_SHA256": hashlib.sha256(files_data).hexdigest(),
        "EXPECTED_TREE_SHA256": manifest["tree"]["sha256"],
        "EXPECTED_GPL_LICENSE_SHA256": manifest["license"]["entries"][0]["sha256"],
        "EXPECTED_GFDL_LICENSE_SHA256": manifest["license"]["entries"][1]["sha256"],
    }
    protocol_commands = {name: [] for name in query.EXPECTED_PROTOCOLS}
    protocol_commands["input.inspect"] = ["autostretch"]
    policy = {
        "schema": query.POLICY_SCHEMA,
        "contract_version": "1",
        "protocol_commands": protocol_commands,
    }
    _write(root / query.POLICY_PATH, _json_bytes(policy))
    return root


class SirilManualQueryTests(unittest.TestCase):
    def _run(self, root: Path, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with _trust_fixture(root):
            code = query.run(arguments, skill_root=root, stdout=stdout, stderr=stderr)
        raw = stdout.getvalue() if code == 0 else stderr.getvalue()
        return code, json.loads(raw), raw

    def test_verify_search_and_read_include_are_integrity_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            code, document, _ = self._run(root, "--verify-bundle")
            self.assertEqual(code, 0)
            self.assertEqual(document["schema"], bundle.QUERY_SCHEMA)
            self.assertEqual(document["status"], "ok")
            self.assertEqual(document["result"]["commands"], 199)
            self.assertFalse(document["manual"]["upstream_reverified_now"])

            code, document, _ = self._run(root, "背景提取", "--top", "2")
            self.assertEqual(code, 0)
            self.assertEqual(document["result"]["status"], "matches")
            self.assertEqual(document["result"]["results"][0]["id"], "page:background")
            self.assertEqual(document["result"]["unmatched_terms"], [])

            code, document, _ = self._run(root, "完全未知词汇")
            self.assertEqual(code, 0)
            self.assertEqual(document["result"]["status"], "no_match")
            self.assertEqual(document["result"]["unmatched_terms"], ["完全未知词汇"])

            code, document, _ = self._run(root, "--read", "doc/page.rst")
            self.assertEqual(code, 0)
            self.assertIn("SPCC, GHS", document["result"]["content"])
            self.assertEqual(
                document["result"]["resolved_includes"], ["doc/include.rst"]
            )
            self.assertEqual(
                document["result"]["resolved_csv_tables"], ["doc/table.txt"]
            )
            self.assertIn(
                ".. bundled-csv-table-begin:: doc/table.txt",
                document["result"]["content"],
            )
            self.assertIn("atan2\ttwo-argument arctangent", document["result"]["content"])

            text_stdout = io.StringIO()
            with _trust_fixture(root):
                code = query.run(
                    ("--read", "doc/page.rst", "--format", "text"),
                    skill_root=root,
                    stdout=text_stdout,
                    stderr=io.StringIO(),
                )
            self.assertEqual(code, 0)
            self.assertIn(
                f"source sha256: {document['result']['source_sha256']}",
                text_stdout.getvalue(),
            )
            self.assertIn(
                f"resolved sha256: {document['result']['resolved_sha256']}",
                text_stdout.getvalue(),
            )

    def test_command_documentation_and_execution_authority_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            expected = {
                "autostretch": (True, "allowed", ["input.inspect"]),
                "manualcmd": (True, "manual_only", []),
                "addmax": (False, "non_scriptable", []),
            }
            for name, (scriptable, state, protocols) in expected.items():
                with self.subTest(command=name):
                    code, document, _ = self._run(root, "--command", name)
                    self.assertEqual(code, 0)
                    self.assertEqual(document["result"]["documentation"]["scriptable"], scriptable)
                    self.assertEqual(document["result"]["execution_policy"]["state"], state)
                    self.assertEqual(
                        document["result"]["execution_policy"]["allowed_protocols"],
                        protocols,
                    )

            code, document, _ = self._run(root, "--command", "not-a-command")
            self.assertEqual(code, 3)
            self.assertEqual(document["error"]["code"], "not_found")

    def test_output_atomically_creates_evidence_and_preserves_json_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw).resolve()
            root = _make_fixture(temporary / "skill")
            evidence = temporary / "autostretch.command.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with _trust_fixture(root):
                code = query.run(
                    ("--command", "autostretch", "--output", str(evidence)),
                    skill_root=root,
                    stdout=stdout,
                    stderr=stderr,
                )
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(evidence.is_file())
            self.assertEqual(evidence.read_text(encoding="utf-8"), stdout.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["mode"], "command")

            original = evidence.read_bytes()
            stderr = io.StringIO()
            with _trust_fixture(root):
                code = query.run(
                    ("--command", "autostretch", "--output", str(evidence)),
                    skill_root=root,
                    stdout=io.StringIO(),
                    stderr=stderr,
                )
            self.assertEqual(code, 2)
            self.assertEqual(evidence.read_bytes(), original)
            self.assertIn("--output", stderr.getvalue())

    def test_output_rejects_relative_paths_and_symlinks_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw).resolve()
            root = _make_fixture(temporary / "skill")
            target = temporary / "target.json"
            target.write_text("preserve\n", encoding="utf-8")
            link = temporary / "evidence.json"
            link.symlink_to(target)

            for output in ("relative.json", str(link)):
                with self.subTest(output=output):
                    stderr = io.StringIO()
                    with _trust_fixture(root):
                        code = query.run(
                            ("--command", "autostretch", "--output", output),
                            skill_root=root,
                            stdout=io.StringIO(),
                            stderr=stderr,
                        )
                    self.assertEqual(code, 2)
                    self.assertEqual(target.read_text(encoding="utf-8"), "preserve\n")
                    self.assertIn("--output", stderr.getvalue())

    def test_each_mode_reverifies_every_file_contributing_to_its_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))

            def observed_paths(*arguments: str) -> set[str]:
                observed: list[set[str]] = []
                original = bundle.BundleSnapshot.reverify

                def spy(
                    snapshot: bundle.BundleSnapshot,
                    relative_paths: object = (),
                ) -> None:
                    observed.append(set(relative_paths))
                    original(snapshot, relative_paths)

                with mock.patch.object(bundle.BundleSnapshot, "reverify", new=spy):
                    code, _, _ = self._run(root, *arguments)
                self.assertEqual(code, 0)
                self.assertEqual(len(observed), 1)
                return observed[0]

            search_paths = observed_paths("背景提取", "--top", "1")
            self.assertIn("source/doc/page.rst", search_paths)
            self.assertIn("source/doc/table.txt", search_paths)

            command_paths = observed_paths("--command", "autostretch")
            self.assertIn("source/doc/Commands.rst", command_paths)

            section_paths = observed_paths("--read", "section:commands")
            self.assertIn("source/doc/Commands.rst", section_paths)
            self.assertIn("source/doc/section-note.txt", section_paths)

            with _trust_fixture(root):
                expected_closure = set(bundle.verify_bundle(root).captures)
            verify_paths = observed_paths("--verify-bundle")
            self.assertEqual(verify_paths, expected_closure)

    def test_policy_is_locked_to_standalone_v1_and_the_twelve_protocols(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            policy_path = root / query.POLICY_PATH
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["contract_version"] = "2"
            policy_path.write_bytes(_json_bytes(policy))
            code, document, _ = self._run(root, "background")
            self.assertEqual(code, 1)
            self.assertIn("contract_version", document["error"]["message"])

        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            policy_path = root / query.POLICY_PATH
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            del policy["protocol_commands"]["delivery.render"]
            policy_path.write_bytes(_json_bytes(policy))
            code, document, _ = self._run(root, "background")
            self.assertEqual(code, 1)
            self.assertIn("standalone v1 protocol set", document["error"]["message"])

    def test_manifest_license_and_upstream_git_blob_are_not_self_asserted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            manifest_path = (
                root / "references/siril-manual" / bundle.MANIFEST_NAME
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["license"]["concluded"] = "GPL-3.0-only"
            with _trust_fixture(root):
                with self.assertRaisesRegex(bundle.BundleError, "NOASSERTION"):
                    bundle._validate_manifest(manifest)

        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            component = root / "references/siril-manual"
            files_path = component / bundle.FILES_NAME
            files_document = json.loads(files_path.read_text(encoding="utf-8"))
            commands_record = next(
                record
                for record in files_document["files"]
                if record["path"] == "source/doc/Commands.rst"
            )
            commands_record["upstream_blob"] = "0" * 40
            files_data = _json_bytes(files_document)
            files_path.write_bytes(files_data)

            manifest_path = component / bundle.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["sha256"] = hashlib.sha256(files_data).hexdigest()
            manifest["files"]["size_bytes"] = len(files_data)
            manifest["tree"]["sha256"] = bundle.tree_digest(
                files_document["files"]
            )
            manifest_data = _json_bytes(manifest)
            manifest_path.write_bytes(manifest_data)
            anchors = _FIXTURE_TRUST[str(root.resolve())]
            anchors["EXPECTED_MANIFEST_SHA256"] = hashlib.sha256(
                manifest_data
            ).hexdigest()
            anchors["EXPECTED_FILES_SHA256"] = hashlib.sha256(files_data).hexdigest()
            anchors["EXPECTED_TREE_SHA256"] = manifest["tree"]["sha256"]

            code, document, _ = self._run(root, "--verify-bundle")
            self.assertEqual(code, 1)
            self.assertIn("Git blob mismatch", document["error"]["message"])

    def test_source_drift_after_snapshot_is_rejected_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            page = root / "references/siril-manual/source/doc/page.rst"
            original_search = query._search

            def search_then_drift(*args: object, **kwargs: object) -> object:
                result = original_search(*args, **kwargs)
                page.write_bytes(page.read_bytes() + b"drift\n")
                return result

            with mock.patch.object(query, "_search", side_effect=search_then_drift):
                code, document, _ = self._run(root, "背景提取", "--top", "1")
            self.assertEqual(code, 1)
            self.assertIn("changed during the query", document["error"]["message"])

        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            unselected = root / "references/siril-manual/source/doc/include.rst"
            original_policy_loader = query._load_policy

            def load_policy_then_drift(*args: object, **kwargs: object) -> object:
                policy = original_policy_loader(*args, **kwargs)
                unselected.write_bytes(unselected.read_bytes() + b"drift\n")
                return policy

            with mock.patch.object(
                query, "_load_policy", side_effect=load_policy_then_drift
            ):
                code, document, _ = self._run(root, "--verify-bundle")
            self.assertEqual(code, 1)
            self.assertIn("changed during the query", document["error"]["message"])

    @unittest.skipUnless(
        bundle._secure_openat_available(), "secure dirfd traversal is POSIX-only"
    )
    def test_parent_symlink_swap_during_read_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            root = base / "component"
            source = root / "source"
            target = source / "doc" / "page.rst"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"trusted\n")
            external_source = base / "external" / "source"
            external_target = external_source / "doc" / "page.rst"
            external_target.parent.mkdir(parents=True)
            external_target.write_bytes(b"secret\n")

            original_read = bundle.os.read
            swapped = False

            def swap_parent_then_read(descriptor: int, size: int) -> bytes:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    source.rename(root / "source-original")
                    source.symlink_to(external_source, target_is_directory=True)
                return original_read(descriptor, size)

            with mock.patch.object(
                bundle.os, "read", side_effect=swap_parent_then_read
            ):
                with self.assertRaisesRegex(bundle.BundleError, "changed"):
                    bundle._read_regular_no_follow(root, "source/doc/page.rst")

    def test_usage_and_exact_read_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            for arguments in (
                (),
                ("query", "--command", "autostretch"),
                ("query", "--top", "0"),
                ("--read", "../outside.rst"),
            ):
                with self.subTest(arguments=arguments):
                    code, document, _ = self._run(root, *arguments)
                    self.assertEqual(code, 2)
                    self.assertEqual(document["error"]["code"], "usage_error")
            code, document, _ = self._run(root, "--read", "doc/missing.rst")
            self.assertEqual(code, 3)
            self.assertEqual(document["error"]["code"], "not_found")

    def test_tamper_extra_symlink_duplicate_json_and_include_cycle_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            root = _make_fixture(base)
            page = root / "references/siril-manual/source/doc/page.rst"
            page.write_bytes(page.read_bytes() + b"tamper\n")
            code, document, _ = self._run(root, "background")
            self.assertEqual(code, 1)
            self.assertEqual(document["error"]["code"], "bundle_integrity_error")

        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw))
            (root / "references/siril-manual/extra.txt").write_text("extra", encoding="utf-8")
            code, _, _ = self._run(root, "--verify-bundle")
            self.assertEqual(code, 1)

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            root = _make_fixture(base)
            page = root / "references/siril-manual/source/doc/page.rst"
            external = base / "external.rst"
            external.write_text("external", encoding="utf-8")
            page.unlink()
            page.symlink_to(external)
            code, _, _ = self._run(root, "--verify-bundle")
            self.assertEqual(code, 1)

        with self.assertRaises(bundle.BundleError):
            bundle.strict_json_bytes(b'{"same":1,"same":2}', document="duplicate")
        with self.assertRaisesRegex(bundle.BundleError, "escapes source/doc"):
            query._resolve_doc_target(
                "source/doc/page.rst",
                "../../outside.txt",
                directive="csv-table :file:",
            )

        with tempfile.TemporaryDirectory() as raw:
            root = _make_fixture(Path(raw), include_cycle=True)
            code, document, _ = self._run(root, "--read", "doc/page.rst")
            self.assertEqual(code, 1)
            self.assertIn("include cycle", document["error"]["message"])

    def test_direct_execution_needs_no_B_writes_no_pycache_and_works_from_other_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            watched = [path for path in SKILL.rglob("*") if path.is_file()]
            mtimes = {path: path.stat().st_mtime_ns for path in watched}
            pycache_before = {
                path.relative_to(SKILL): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in SKILL.rglob("__pycache__/*")
                if path.is_file()
            }
            environment = {"DEEP_SKY_TEST_SECRET": "must-not-appear"}
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "query_siril_manual.py"),
                    "--verify-bundle",
                ],
                cwd=elsewhere,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn("must-not-appear", completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "ok")
            self.assertEqual(
                {
                    path.relative_to(SKILL): (
                        path.stat().st_size,
                        path.stat().st_mtime_ns,
                    )
                    for path in SKILL.rglob("__pycache__/*")
                    if path.is_file()
                },
                pycache_before,
            )
            self.assertEqual(
                {path: path.stat().st_mtime_ns for path in watched},
                mtimes,
            )


if __name__ == "__main__":
    unittest.main()
