from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile


SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGER = SKILL_ROOT / "scripts" / "package_release.py"
SUBPROCESS_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
}


def _build_release(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    archive = tmp_path / "starun-siril-0.1.0.zip"
    completed = subprocess.run(
        [sys.executable, str(PACKAGER), "--output", str(archive)],
        cwd=tmp_path,
        env=SUBPROCESS_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    return archive, json.loads(completed.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_bundle_has_exact_runtime_inventory(tmp_path: Path) -> None:
    archive, report = _build_release(tmp_path)

    assert report["schema"] == "starun-siril.release-receipt/v2"
    assert report["skill"] == {"slug": "starun-siril", "version": "0.1.0"}
    assert report["publishable"] is False
    assert report["components"][0]["id"] == "siril-manual"
    assert report["archive"]["inventory"] == [item["path"] for item in report["files"]]

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        provenance_schema = json.loads(
            bundle.read("references/ssf-provenance.schema.json")
        )
    assert names == sorted(item["path"] for item in report["files"])
    assert len(names) == len(set(names))
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert "references/ssf-provenance.schema.json" in names
    assert "references/stage-sequence.md" in names
    assert provenance_schema["$id"] == "starun-siril.ssf-provenance.v1"
    assert "references/tool-install.schema.json" not in names
    assert "references/tool-installation.md" not in names

    forbidden = {
        "CHANGELOG.md",
        "RELEASING.md",
        "requirements-dev.txt",
        "release-files.txt",
        "package_release.py",
        "deep_sky_siril_engine.py",
        "deep_sky_siril_recipes.py",
        "export_starun_result.py",
        "processing-plan.json",
    }
    assert not forbidden & {Path(name).name for name in names}
    scripts = {Path(name).name for name in names if name.startswith("scripts/")}
    assert scripts == {
        "deep_sky_siril.py",
        "deep_sky_siril_artifacts.py",
        "deep_sky_siril_contract.py",
        "deep_sky_siril_core.py",
        "deep_sky_siril_session.py",
        "deep_sky_siril_tooling.py",
        "deep_sky_siril_validation.py",
        "query_siril_manual.py",
        "siril_background_samples.py",
        "siril_manual_bundle.py",
    }


def test_released_runtime_is_portable_and_supports_strict_static_validation(
    tmp_path: Path,
) -> None:
    archive, report = _build_release(tmp_path)
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(unpacked)
    foreign_cwd = tmp_path / "foreign-cwd"
    foreign_cwd.mkdir()

    manual_check = subprocess.run(
        [
            sys.executable,
            "-B",
            str(unpacked / "scripts" / "query_siril_manual.py"),
            "--verify-bundle",
        ],
        cwd=foreign_cwd,
        env=SUBPROCESS_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(manual_check.stdout)["status"] == "ok"
    assert not any(foreign_cwd.iterdir()), "manual query wrote outside the bundle"

    command_query = subprocess.run(
        [
            sys.executable,
            "-B",
            str(unpacked / "scripts" / "query_siril_manual.py"),
            "--command",
            "autostretch",
        ],
        cwd=foreign_cwd,
        env=SUBPROCESS_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    command_payload = json.loads(command_query.stdout)
    assert command_payload["schema"] == "starun-siril.manual-query.v1"
    assert command_payload["status"] == "ok"
    assert command_payload["result"]["command"] == "autostretch"
    assert command_payload["result"]["status"] == "found"
    assert command_payload["result"]["execution_policy"]["state"] == "allowed"
    assert not any(foreign_cwd.iterdir()), "command query wrote outside the bundle"

    probe = subprocess.run(
        [
            sys.executable,
            "-B",
            str(unpacked / "scripts" / "deep_sky_siril.py"),
            "probe",
            "--offline",
        ],
        cwd=foreign_cwd,
        env=SUBPROCESS_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    probe_payload = json.loads(probe.stdout)
    assert probe_payload["helper_contract_version"] == "1"
    assert probe_payload["policy"]["automatic_download"] is False
    assert not any(foreign_cwd.iterdir()), "probe wrote outside the session"

    fake_siril = tmp_path / "siril-cli"
    fake_siril.write_text("#!/bin/sh\necho 'siril 1.4.4'\n", encoding="utf-8")
    fake_siril.chmod(0o755)
    runtime_env = {**SUBPROCESS_ENV, "DEEP_SKY_SIRIL_BIN": str(fake_siril)}
    source = tmp_path / "master.fit"
    source.write_bytes(b"release portability fixture")
    session = tmp_path / "session"
    initialized = subprocess.run(
        [
            sys.executable,
            "-B",
            str(unpacked / "scripts" / "deep_sky_siril.py"),
            "init",
            str(source),
            "--session",
            str(session),
            "--input-state",
            "linear",
            "--state-evidence",
            "release bundle static-validation fixture",
            "--channel-mode",
            "mono",
            "--container-validation",
            "strict",
            "--offline",
        ],
        cwd=foreign_cwd,
        env=runtime_env,
        check=True,
        capture_output=True,
        text=True,
    )
    initialized_payload = json.loads(initialized.stdout)
    assert initialized_payload["contract_version"] == "1"
    assert initialized_payload["context"]["container_validation"] == "strict"
    assert initialized_payload["knowledge"]["manual"]["version"] == "1.4.4"
    assert initialized_payload["knowledge"]["bundle_evidence"]["path"] == (
        "reports/manual-evidence/bundle-verification.json"
    )
    assert (session / "reports" / "manual-evidence" / "bundle-verification.json").is_file()
    assert not any(foreign_cwd.iterdir()), "init wrote outside its explicit session"

    script = session / "scripts" / "010-input.ssf"
    preview = session / "previews" / "010-input.jpg"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                "stat main",
                "autostretch -linked -2.8 0.22",
                f'savejpg "{preview.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    protocol_reference = unpacked / "references" / "protocols" / "input-inspect.md"
    command_policy = unpacked / "references" / "command-policy.json"
    provenance = script.with_suffix(".provenance.json")
    provenance.write_text(
        json.dumps(
            {
                "schema": "starun-siril.ssf-provenance.v1",
                "contract_version": "1",
                "run_id": "010-input",
                "protocol": "input.inspect",
                "script": {
                    "path": "scripts/010-input.ssf",
                    "sha256": _sha256(script),
                },
                "references": [
                    {
                        "path": "references/protocols/input-inspect.md",
                        "sha256": _sha256(protocol_reference),
                        "role": "primary",
                    }
                ],
                "command_policy": {
                    "path": "references/command-policy.json",
                    "sha256": _sha256(command_policy),
                },
                "manual_lookup": {
                    "status": "not_needed",
                    "reason": (
                        "The pinned bundle was verified by init and this script "
                        "instantiates the complete input.inspect protocol skeleton."
                    ),
                    "evidence": [],
                },
                "rationale": {
                    "applicability": "Every execution session begins with input.inspect.",
                    "parameter_choices": [
                        "Use linked autostretch only for the diagnostic preview.",
                        "Keep the immutable source as the scientific parent.",
                    ],
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    static_validation = subprocess.run(
        [
            sys.executable,
            "-B",
            str(unpacked / "scripts" / "deep_sky_siril.py"),
            "run",
            "--session",
            str(session),
            "--protocol",
            "input.inspect",
            "--script",
            str(script),
            "--source",
            str(source),
            "--expect",
            str(preview),
            "--validate-only",
        ],
        cwd=foreign_cwd,
        env=runtime_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert static_validation.returncode == 0, static_validation.stderr
    validation_payload = json.loads(static_validation.stdout)
    assert validation_payload["status"] == "success"
    assert validation_payload["mode"] == "validate_only"
    assert validation_payload["executed"] is False
    assert validation_payload["script_provenance"]["path"] == (
        "scripts/010-input.provenance.json"
    )
    assert validation_payload["knowledge_validation"]["manual_evidence"] == []
    assert validation_payload["command_knowledge"]["manual"]["version"] == "1.4.4"
    assert not any(foreign_cwd.iterdir()), "run wrote outside its explicit session"

    checked = subprocess.run(
        [sys.executable, str(PACKAGER), "--check"],
        cwd=tmp_path,
        env=SUBPROCESS_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    checked_payload = json.loads(checked.stdout)
    assert checked_payload["status"] == "valid_candidate"
    assert checked_payload["publishable"] is False
    assert checked_payload["file_count"] == len(report["files"])
    assert checked_payload["content_hash"] == report["skillhub"]["content_hash"]
