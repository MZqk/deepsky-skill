from __future__ import annotations

from io import BytesIO, StringIO
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import deep_sky_siril as cli  # noqa: E402
import deep_sky_siril_artifacts as artifacts  # noqa: E402
import deep_sky_siril_contract as contract  # noqa: E402
import deep_sky_siril_core as core  # noqa: E402
import deep_sky_siril_session as session_state  # noqa: E402
import deep_sky_siril_tooling as tooling  # noqa: E402
import deep_sky_siril_validation as validation_rules  # noqa: E402
import query_siril_manual as manual_query  # noqa: E402
import siril_background_samples as background_adapter  # noqa: E402


JPEG_1X1 = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    + "08" * 64
    + "ffc0000b080001000101011100ffc40014000100000000000000000000000000000000"
    + "ffda0008010100003f00ffd9"
)


def _write_fits(
    path: Path,
    *,
    width: int = 12,
    height: int = 8,
    nonzero: bool = False,
) -> None:
    cards = [
        "SIMPLE  =                    T",
        "BITPIX  =                   16",
        "NAXIS   =                    2",
        f"NAXIS1  = {width:20d}",
        f"NAXIS2  = {height:20d}",
        "END",
    ]
    header = "".join(card.ljust(80) for card in cards).encode("ascii")
    header += b" " * ((2880 - len(header) % 2880) % 2880)
    data = (
        b"".join(
            ((index % 101) + 1).to_bytes(2, byteorder="big", signed=True)
            for index in range(width * height)
        )
        if nonzero
        else b"\0" * (width * height * 2)
    )
    data += b"\0" * ((2880 - len(data) % 2880) % 2880)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + data)


def _write_jpeg(path: Path, color: tuple[int, int, int] = (18, 24, 32)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if artifacts.Image is not None:
        image = artifacts.Image.new("RGB", (4, 3), color)
        image.save(path, format="JPEG", quality=90)
        return
    path.write_bytes(JPEG_1X1)


def _fake_probe(
    siril: Path,
    *,
    offline: bool = False,
    local_gaia: Path | None = None,
) -> dict[str, object]:
    return {
        "schema": "starun-siril.tool-probe.v4",
        "created_at": "2026-01-01T00:00:00+00:00",
        "helper_contract_version": "1",
        "policy": {
            "offline": offline,
            "network_default": "offline",
            "online_exception": "explicit_remote_gaia_color_calibration",
            "automatic_download": False,
            "download_requires_user_confirmation": True,
            "python_pixel_processing": False,
        },
        "tools": {
            "siril_cli": {
                "path": str(siril),
                "version": "1.4.4",
                "compatible": True,
                "required_range": ">=1.4.4,<1.5",
                "probe_excerpt": "siril 1.4.4",
                "fingerprint": core.fingerprint(siril, role="siril_cli"),
            },
            "starnet": {"compatible": False, "reasons": ["starnet2_unavailable"]},
            "local_gaia": {
                "compatible": local_gaia is not None,
                "path": str(local_gaia) if local_gaia is not None else None,
            },
            "pillow": {"compatible": artifacts.Image is not None, "purpose": "decode_validation"},
            "sirilpy_bridge": {
                "status": "runtime_check_required",
                "required_version": "1.0.25",
            },
        },
        "blocking_reasons": [],
        "warnings": ["starnet_unavailable_preserve_stars_baseline"],
    }


def _session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    input_state: str = "linear",
    keep_intermediates: bool = False,
    width: int = 12,
    height: int = 8,
    offline: bool = False,
    local_gaia: Path | None = None,
) -> tuple[Path, Path, Path]:
    source = tmp_path / "master.fit"
    _write_fits(source, width=width, height=height)
    siril = tmp_path / "siril-cli"
    siril.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    siril.chmod(0o755)
    monkeypatch.setattr(
        tooling,
        "probe_tools",
        lambda *, offline=False: _fake_probe(
            siril,
            offline=offline,
            local_gaia=local_gaia,
        ),
    )
    session = tmp_path / "session"
    session_state.init_session(
        str(source),
        str(session),
        input_state=input_state,
        state_evidence=(
            [f"user confirmed {input_state} stacked master"]
            if input_state in {"linear", "nonlinear"}
            else []
        ),
        channel_mode="broadband",
        channel_map=None,
        target_name="M31",
        target_type="galaxy",
        style="natural",
        stars="adaptive",
        offline=offline,
        keep_intermediates=keep_intermediates,
    )
    return session, source, siril


def _write_ssf_provenance(
    session: Path,
    script: Path,
    protocol: str,
    *,
    manual_evidence: list[Path] | None = None,
) -> Path:
    """Write the Agent-authored provenance fixture required for every SSF."""
    primary = ROOT / "references" / "protocols" / f"{protocol.replace('.', '-')}.md"
    policy = ROOT / "references" / "command-policy.json"
    evidence = manual_evidence or []
    path = script.with_suffix(".provenance.json")
    core.atomic_write_json(
        path,
        {
            "schema": "starun-siril.ssf-provenance.v1",
            "contract_version": "1",
            "run_id": script.stem,
            "protocol": protocol,
            "script": {
                "path": script.relative_to(session).as_posix(),
                "sha256": core.sha256_file(script),
            },
            "references": [
                {
                    "path": primary.relative_to(ROOT).as_posix(),
                    "sha256": core.sha256_file(primary),
                    "role": "primary",
                }
            ],
            "command_policy": {
                "path": policy.relative_to(ROOT).as_posix(),
                "sha256": core.sha256_file(policy),
            },
            "manual_lookup": {
                "status": "performed" if evidence else "not_needed",
                "reason": (
                    "Exact manual command evidence was captured for this fixture."
                    if evidence
                    else "The protocol reference fully specifies this fixture and no command semantics are ambiguous."
                ),
                "evidence": [
                    {
                        "path": item.relative_to(session).as_posix(),
                        "sha256": core.sha256_file(item),
                    }
                    for item in evidence
                ],
            },
            "rationale": {
                "applicability": "This fixture exercises the selected protocol on its declared source.",
                "parameter_choices": [
                    "Values instantiate the protocol skeleton without adding an undeclared processing stage."
                ],
            },
        },
    )
    return path


def _validate_script_file(
    script: Path,
    *,
    session: Path,
    protocol: str,
    source: Path,
    expected_outputs: list[Path],
    probe: dict[str, object],
) -> dict[str, object]:
    return validation_rules.validate_script_file(
        script,
        provenance=script.with_suffix(".provenance.json"),
        session=session,
        session_payload=core.load_json(session / "session.json"),
        protocol=protocol,
        source=source,
        expected_outputs=expected_outputs,
        probe=probe,
    )


def _input_inspect_run(
    session: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], Path, Path]:
    direct_preview = session / "previews" / "010-input-direct.jpg"
    autostretch_preview = session / "previews" / "010-input-autostretch.jpg"
    inspect_script = session / "scripts" / "010-input.ssf"
    inspect_script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                "stat main",
                f'savejpg "{direct_preview.with_suffix("")}" 95',
                "close",
                f'load "{source}"',
                "autostretch -linked -2.8 0.22",
                f'savejpg "{autostretch_preview.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, inspect_script, "input.inspect")

    def fake_inspect(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert any(value == f"--script={inspect_script}" for value in command)
        assert kwargs["env"] == tooling._subprocess_environment("siril_runtime")
        assert "SKILLHUB_TOKEN" not in kwargs["env"]
        _write_jpeg(direct_preview, (12, 16, 20))
        _write_jpeg(autostretch_preview, (48, 56, 64))
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(core.subprocess, "run", fake_inspect)
    receipt = core.run_script(
        str(session),
        protocol="input.inspect",
        script_value=str(inspect_script),
        source_value=str(source),
        expected_values=[str(direct_preview), str(autostretch_preview)],
        timeout=30,
    )
    return receipt, direct_preview, autostretch_preview


def _delivery_script(
    session: Path,
    delivery_source: Path,
    *,
    run_id: str = "120-delivery",
) -> tuple[Path, Path]:
    candidate = session / "artifacts" / f"{run_id}.jpg"
    script = session / "scripts" / f"{run_id}.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{delivery_source}"',
                "stat main",
                f'savejpg "{candidate.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "delivery.render")
    return script, candidate


def _delivery_run(
    session: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str = "120-delivery",
) -> tuple[dict[str, object], Path]:
    _inspect_receipt, _direct_preview, _autostretch_preview = _input_inspect_run(
        session,
        source,
        monkeypatch,
    )

    input_state = core.load_json(session / "session.json")["context"]["input_state"]
    assert input_state != "unknown"
    delivery_source = source
    script, candidate = _delivery_script(
        session,
        delivery_source,
        run_id=run_id,
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert any(value == f"--script={script}" for value in command)
        _write_jpeg(candidate)
        output = (
            "Red layer: Mean: 100, Median: 90, Sigma: 5, Min: 0, Max: 255, "
            "bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        )
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(delivery_source),
        expected_values=[str(candidate)],
        timeout=30,
    )
    return receipt, candidate


def _review(session: Path, run_id: str, candidate: Path, *, verdict: str = "accept") -> Path:
    run_path = session / "runs" / f"{run_id}.json"
    path = session / "reviews" / f"{run_id}.json"
    input_state = core.load_json(session / "session.json")["context"]["input_state"]
    parent_preview = session / "previews" / (
        "010-input-direct.jpg"
        if input_state == "nonlinear"
        else "010-input-autostretch.jpg"
    )
    gates = {
        "structure": "pass",
        "background": "pass",
        "color": "pass",
        "stars": "pass",
        "geometry": "pass",
    }
    if verdict == "uncertain":
        gates["structure"] = "uncertain"
    core.atomic_write_json(
        path,
        {
            "schema": "starun-siril.review.v1",
            "run_id": run_id,
            "run_receipt_sha256": core.sha256_file(run_path),
            "protocol": "delivery.render",
            "inspected_materials": [
                {
                    "path": candidate.relative_to(session).as_posix(),
                    "sha256": core.sha256_file(candidate),
                },
                {
                    "path": parent_preview.relative_to(session).as_posix(),
                    "sha256": core.sha256_file(parent_preview),
                },
            ],
            "verdict": verdict,
            "gates": gates,
            "notes": "Opened the final candidate and verified the visible subject and stars.",
        },
    )
    return path


def _selection(
    session: Path,
    run_id: str,
    candidate: Path,
    review: Path,
    *,
    status: str = "success",
    limitations: list[dict[str, str]] | None = None,
) -> Path:
    path = session / "final-selection.json"
    selected_runs = [run_id]
    review_receipts = [review.relative_to(session).as_posix()]
    core.atomic_write_json(
        path,
        {
            "schema": "starun-siril.final-selection.v1",
            "status": status,
            "candidate_image": candidate.relative_to(session).as_posix(),
            "selected_runs": selected_runs,
            "review_receipts": review_receipts,
            "limitations": limitations or [],
            "stars_required": True,
            "output_contains_stars": True,
        },
    )
    return path


def _failed_selection(session: Path) -> Path:
    path = session / "final-selection.json"
    core.atomic_write_json(
        path,
        {
            "schema": "starun-siril.final-selection.v1",
            "status": "failed",
            "selected_runs": [],
            "review_receipts": [],
            "limitations": [],
            "stars_required": True,
            "output_contains_stars": None,
            "error": {
                "code": "runtime_dependency_missing",
                "message": "Compatible siril-cli is missing",
                "missing_dependencies": ["siril-cli>=1.4.4,<1.5"],
            },
        },
    )
    return path


def test_public_surface_is_standalone_v1_only() -> None:
    assert cli.PUBLIC_COMMANDS == ("probe", "init", "run", "finalize")
    assert core.CONTRACT_VERSION == "1"
    assert "process" not in cli.PUBLIC_COMMANDS
    assert "apply-decision" not in cli.PUBLIC_COMMANDS
    assert not hasattr(core, "SessionEngine")
    assert not hasattr(core, "compile_recipe")


def test_init_freezes_context_without_workflow_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    payload = core.load_json(session / "session.json")
    manifest = core.load_json(session / "manifest.json")

    assert payload["contract_version"] == "1"
    assert payload["schema"] == "starun-siril.session.v1"
    assert payload["context"]["container_validation"] == "siril"
    assert manifest["schema"] == "starun-siril.manifest.v1"
    assert payload["context"]["offline"] is False
    assert payload["execution_policy"] == {
        "log_diagnostics": "fail_closed_v1",
        "network": "offline_default_explicit_gaia_v1",
    }
    assert payload["context"]["keep_intermediates"] is False
    assert payload["input"]["sha256"] == core.sha256_file(source)
    knowledge = payload["knowledge"]
    assert knowledge["schema"] == "starun-siril.knowledge.v1"
    assert knowledge["manual"]["version"] == "1.4.4"
    assert knowledge["manual"]["commit"] == "1550a31d325276124fe961368477c90d49df804b"
    assert knowledge["manual"]["bundle_fingerprint"]
    assert knowledge["manual"]["manifest_sha256"]
    assert knowledge["manual"]["files_sha256"]
    assert knowledge["manual"]["tree_sha256"]
    assert knowledge["command_policy"] == {
        "path": "references/command-policy.json",
        "sha256": core.sha256_file(ROOT / "references" / "command-policy.json"),
        "size": (ROOT / "references" / "command-policy.json").stat().st_size,
    }
    bundle_evidence = session / knowledge["bundle_evidence"]["path"]
    assert core.sha256_file(bundle_evidence) == knowledge["bundle_evidence"]["sha256"]
    assert core.load_json(bundle_evidence)["mode"] == "verify_bundle"
    assert manifest["runs"] == []
    assert manifest["finalization"] is None
    serialized = json.dumps({"session": payload, "manifest": manifest})
    for forbidden in ("phase", "current_task", "next_action", "processing-plan"):
        assert forbidden not in serialized


def test_child_environment_drops_credentials_and_python_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supplied = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "XDG_CACHE_HOME": "/safe/cache",
        "TMPDIR": "/safe/tmp",
        "TZ": "UTC",
        "PYTHONPATH": "/sandbox/python",
        "VIRTUAL_ENV": "/sandbox/venv",
        "SKILLHUB_TOKEN": "sentinel-skillhub-secret",
        "AWS_SECRET_ACCESS_KEY": "sentinel-cloud-secret",
        "HTTPS_PROXY": "https://user:secret@example.invalid",
        "SSH_AUTH_SOCK": "/secret/agent.sock",
        "DYLD_INSERT_LIBRARIES": "/secret/inject.dylib",
        "PYTHONHOME": "/secret/python-home",
    }
    for key, value in supplied.items():
        monkeypatch.setenv(key, value)
    probe_environment = tooling._subprocess_environment("tool_version")
    assert probe_environment["PATH"] == "/safe/bin"
    assert probe_environment["HOME"] == "/safe/home"
    assert probe_environment["XDG_CACHE_HOME"] == "/safe/cache"
    assert probe_environment["LANG"] == "C"
    assert probe_environment["LC_ALL"] == "C"
    assert probe_environment["PIP_NO_INDEX"] == "1"
    assert probe_environment["PYTHONNOUSERSITE"] == "1"
    assert probe_environment["PYTHONUTF8"] == "1"
    assert probe_environment["PYTHONHOME"] == ""
    assert probe_environment["__PYVENV_LAUNCHER__"] == ""
    assert "PYTHONPATH" not in probe_environment
    assert "VIRTUAL_ENV" not in probe_environment
    for forbidden in (
        "SKILLHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "HTTPS_PROXY",
        "SSH_AUTH_SOCK",
        "DYLD_INSERT_LIBRARIES",
    ):
        assert forbidden not in probe_environment

    siril_environment = tooling._subprocess_environment("siril_runtime")
    assert "PYTHONPATH" not in siril_environment
    assert "VIRTUAL_ENV" not in siril_environment
    assert "PYTHONPATH" not in tooling._subprocess_environment("python_cli")


def test_tool_version_probe_uses_the_scoped_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "siril-cli"
    executable.write_text("binary", encoding="utf-8")
    monkeypatch.setenv("SKILLHUB_TOKEN", "sentinel-must-not-leak")
    observed: dict[str, str] = {}
    observed_cwd: Path | None = None

    def fake_version(
        _command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal observed_cwd
        observed.update(kwargs["env"])  # type: ignore[arg-type]
        observed_cwd = Path(kwargs["cwd"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess([], 0, "siril-cli 1.4.4")

    monkeypatch.setattr(tooling.subprocess, "run", fake_version)
    version, _output = tooling._tool_version(executable)
    assert version == "1.4.4"
    assert observed_cwd is not None
    assert not observed_cwd.exists()
    assert Path(observed["HOME"]).parent == observed_cwd
    assert Path(observed["TMPDIR"]).parent == observed_cwd
    assert "PYTHONHOME" not in observed
    assert "SKILLHUB_TOKEN" not in observed


def test_init_defers_sirilpy_validation_to_the_actual_background_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _source, _siril = _session(tmp_path, monkeypatch)
    probe = core.load_json(session / "reports" / "tool-probe.json")
    assert probe["tools"]["sirilpy_bridge"] == {
        "status": "runtime_check_required",
        "required_version": "1.0.25",
    }
    assert probe["blocking_reasons"] == []
    assert (session / "session.json").is_file()
    assert (session / "manifest.json").is_file()


@pytest.mark.parametrize("precreate", [False, True])
def test_init_rolls_back_owned_staging_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    precreate: bool,
) -> None:
    source = tmp_path / "master.fit"
    _write_fits(source)
    siril = tmp_path / "siril-cli"
    siril.write_text("binary", encoding="utf-8")
    siril.chmod(0o755)
    monkeypatch.setattr(
        tooling,
        "probe_tools",
        lambda *, offline=False: _fake_probe(siril, offline=offline),
    )
    target = tmp_path / "transactional-session"
    if precreate:
        target.mkdir()
    original_atomic_write_json = session_state.atomic_write_json

    def fail_session_write(path: Path, value: dict[str, object]) -> None:
        if path.name == "session.json":
            raise OSError("injected staging write failure")
        original_atomic_write_json(path, value)

    monkeypatch.setattr(session_state, "atomic_write_json", fail_session_write)
    with pytest.raises(core.ContractError) as raised:
        session_state.init_session(
            str(source),
            str(target),
            input_state="unknown",
            state_evidence=[],
            channel_mode="unknown",
            channel_map=None,
            target_name=None,
            target_type="unknown",
            style="natural",
            stars="adaptive",
            offline=True,
            keep_intermediates=False,
        )
    assert raised.value.code == "session_init_failed"
    assert target.exists() is precreate
    if precreate:
        assert target.is_dir() and not any(target.iterdir())
    assert not list(tmp_path.glob(".transactional-session.staging-*"))


def test_init_atomically_replaces_an_existing_empty_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "master.fit"
    _write_fits(source)
    siril = tmp_path / "siril-cli"
    siril.write_text("binary", encoding="utf-8")
    siril.chmod(0o755)
    monkeypatch.setattr(
        tooling,
        "probe_tools",
        lambda *, offline=False: _fake_probe(siril, offline=offline),
    )
    target = tmp_path / "existing-empty"
    target.mkdir(mode=0o755)
    result = session_state.init_session(
        str(source),
        str(target),
        input_state="unknown",
        state_evidence=[],
        channel_mode="unknown",
        channel_map=None,
        target_name=None,
        target_type="unknown",
        style="natural",
        stars="adaptive",
        offline=True,
        keep_intermediates=False,
    )
    assert result["status"] == "ready"
    assert (target.stat().st_mode & 0o777) == 0o700
    assert (target / "session.json").is_file()
    assert not list(tmp_path.glob(".existing-empty.staging-*"))


def test_init_concurrent_occupation_preserves_intruder_and_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "master.fit"
    _write_fits(source)
    siril = tmp_path / "siril-cli"
    siril.write_text("binary", encoding="utf-8")
    siril.chmod(0o755)
    monkeypatch.setattr(
        tooling,
        "probe_tools",
        lambda *, offline=False: _fake_probe(siril, offline=offline),
    )
    target = tmp_path / "concurrent-session"
    original_unchanged = session_state._session_target_unchanged

    def occupy_target(path: Path, identity: tuple[int, int] | None) -> bool:
        path.mkdir()
        (path / "intruder.txt").write_text("preserve", encoding="utf-8")
        return original_unchanged(path, identity)

    monkeypatch.setattr(session_state, "_session_target_unchanged", occupy_target)
    with pytest.raises(core.ContractError) as raised:
        session_state.init_session(
            str(source),
            str(target),
            input_state="unknown",
            state_evidence=[],
            channel_mode="unknown",
            channel_map=None,
            target_name=None,
            target_type="unknown",
            style="natural",
            stars="adaptive",
            offline=True,
            keep_intermediates=False,
        )
    assert raised.value.code == "session_not_empty"
    assert (target / "intruder.txt").read_text(encoding="utf-8") == "preserve"
    assert not list(tmp_path.glob(".concurrent-session.staging-*"))


def test_init_commit_failure_preserves_existing_empty_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "master.fit"
    _write_fits(source)
    siril = tmp_path / "siril-cli"
    siril.write_text("binary", encoding="utf-8")
    siril.chmod(0o755)
    monkeypatch.setattr(
        tooling,
        "probe_tools",
        lambda *, offline=False: _fake_probe(siril, offline=offline),
    )
    target = tmp_path / "replace-failure-session"
    target.mkdir()
    real_replace = session_state.os.replace

    def fail_directory_commit(source_path: object, destination_path: object) -> None:
        source_candidate = Path(source_path)  # type: ignore[arg-type]
        destination_candidate = Path(destination_path)  # type: ignore[arg-type]
        if (
            destination_candidate == target
            and source_candidate.name.startswith(".replace-failure-session.staging-")
        ):
            raise OSError("injected directory commit failure")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(session_state.os, "replace", fail_directory_commit)
    with pytest.raises(core.ContractError) as raised:
        session_state.init_session(
            str(source),
            str(target),
            input_state="unknown",
            state_evidence=[],
            channel_mode="unknown",
            channel_map=None,
            target_name=None,
            target_type="unknown",
            style="natural",
            stars="adaptive",
            offline=True,
            keep_intermediates=False,
        )
    assert raised.value.code == "session_init_failed"
    assert target.is_dir() and not any(target.iterdir())
    assert not list(tmp_path.glob(".replace-failure-session.staging-*"))


def test_init_requires_evidence_for_known_state_and_empty_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "master.fit"
    _write_fits(source)
    siril = tmp_path / "siril-cli"
    siril.write_text("binary", encoding="utf-8")
    siril.chmod(0o755)
    monkeypatch.setattr(tooling, "probe_tools", lambda *, offline=False: _fake_probe(siril, offline=offline))
    kwargs = {
        "input_state": "linear",
        "state_evidence": [],
        "channel_mode": "unknown",
        "channel_map": None,
        "target_name": None,
        "target_type": "unknown",
        "style": "natural",
        "stars": "adaptive",
        "offline": False,
        "keep_intermediates": False,
    }
    with pytest.raises(core.ContractError, match="concrete evidence"):
        session_state.init_session(str(source), str(tmp_path / "session-a"), **kwargs)

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "mine.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(core.ContractError, match="must be empty"):
        session_state.init_session(
            str(source),
            str(occupied),
            **{**kwargs, "input_state": "unknown"},
        )
    assert (occupied / "mine.txt").read_text(encoding="utf-8") == "keep"


def test_session_rejects_frozen_probe_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _source, _siril = _session(tmp_path, monkeypatch)
    probe = session / "reports" / "tool-probe.json"
    probe.write_text("{}\n", encoding="utf-8")
    with pytest.raises(core.ContractError, match="Frozen tool probe changed"):
        session_state.load_session(session)


def test_session_without_frozen_knowledge_is_an_unsupported_old_v1_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _source, _siril = _session(tmp_path, monkeypatch)
    path = session / "session.json"
    payload = core.load_json(path)
    payload.pop("knowledge")
    payload.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(payload)
    core.atomic_write_json(path, payload)

    with pytest.raises(core.ContractError) as raised:
        session_state.load_session(session)
    assert raised.value.code == "unsupported_session_contract"


def test_validator_enforces_protocol_commands_and_declared_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    output = session / "artifacts" / "120-final.jpg"
    script = session / "scripts" / "120-delivery.ssf"
    probe = core.load_json(session / "reports" / "tool-probe.json")

    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                'cd "/tmp"',
                f'savejpg "{output.with_suffix("")}" 95',
            ]
        ),
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "delivery.render")
    with pytest.raises(core.ContractError, match="Command cd is forbidden"):
        _validate_script_file(
            script,
            session=session,
            protocol="delivery.render",
            source=source,
            expected_outputs=[output],
            probe=probe,
        )

    extra = session / "previews" / "undeclared.jpg"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                f'savejpg "{output.with_suffix("")}" 95',
                f'savejpg "{extra.with_suffix("")}" 95',
                "close",
            ]
        ),
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "delivery.render")
    with pytest.raises(core.ContractError, match="undeclared outputs"):
        _validate_script_file(
            script,
            session=session,
            protocol="delivery.render",
            source=source,
            expected_outputs=[output],
            probe=probe,
        )

    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                f'save "{source.with_suffix("")}" -chksum',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "color.finish")
    with pytest.raises(core.ContractError, match="escaped the session"):
        _validate_script_file(
            script,
            session=session,
            protocol="color.finish",
            source=source,
            expected_outputs=[source],
            probe=probe,
        )


def test_starnet_protocol_uses_native_siril_with_frozen_binary_and_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    executable = tmp_path / "starnet2"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    model = tmp_path / "StarNet2_weights.mlpackage"
    model.mkdir()
    (model / "Manifest.json").write_text("{}\n", encoding="utf-8")
    probe = core.load_json(session / "reports" / "tool-probe.json")
    probe["tools"]["starnet"] = {
        "compatible": True,
        "executable": core.fingerprint(executable, role="starnet2"),
        "model": contract.fingerprint_resource(model, kind="directory"),
        "reasons": [],
    }
    full = session / "artifacts" / "080-full-source.fit"
    starless = session / "artifacts" / "080-starless.fit"
    layer = session / "artifacts" / "080-star-layer.fit"
    script = session / "scripts" / "080-stars.ssf"
    lines = [
        "requires 1.4.4 1.5.0",
        "set32bits",
        f'set "core.starnet_exe={executable}"',
        f'set "core.starnet_weights={model}"',
        f'load "{source}"',
        f'save "{full.with_suffix("")}" -chksum',
        "starnet -stretch -stride=256 -nostarmask",
        f'save "{starless.with_suffix("")}" -chksum',
        "close",
        'pm "$artifacts/080-full-source$ - $artifacts/080-starless$"',
        f'save "{layer.with_suffix("")}" -chksum',
        "close",
    ]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_ssf_provenance(session, script, "stars.separate")
    validation = _validate_script_file(
        script,
        session=session,
        protocol="stars.separate",
        source=source,
        expected_outputs=[full, starless, layer],
        probe=probe,
    )
    assert "pyscript" not in validation["commands"]
    assert validation["commands"].count("starnet") == 1

    script.write_text(
        "\n".join(value.replace("-nostarmask", "-nostarmask -upscale") for value in lines)
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "stars.separate")
    with pytest.raises(core.ContractError, match="without upsampling"):
        _validate_script_file(
            script,
            session=session,
            protocol="stars.separate",
            source=source,
            expected_outputs=[full, starless, layer],
            probe=probe,
        )
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_ssf_provenance(session, script, "stars.separate")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        for path in (full, starless, layer):
            _write_fits(path)
        output = (
            "Reading FITS: file output.fit, 1 layer(s), 64x64 pixels, 16 bits\n"
            "Gray layer: Mean: 100, Median: 90, Sigma: 5, Min: 0, Max: 255, "
            "bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        )
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(tooling, "probe_tools", lambda *, offline=False: probe)
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="stars.separate",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(full), str(starless), str(layer)],
        timeout=30,
    )
    assert receipt["status"] == "success"
    assert receipt["runtime_bindings_unchanged"] is True
    assert receipt["runtime"]["starnet"] == probe["tools"]["starnet"]


def test_run_writes_immutable_receipt_and_replays_verified_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    assert receipt["status"] == "success"
    assert receipt["protocol"] == "delivery.render"
    assert receipt["script_unchanged"] is True
    assert receipt["knowledge_bindings_unchanged"] is True
    assert "command_policy_sha256" not in receipt
    mapped_commands = receipt["command_knowledge"]["commands"]
    assert [item["command"].lower() for item in mapped_commands] == list(
        dict.fromkeys(receipt["commands"])
    )
    assert all(
        set(item) == {
            "command",
            "path",
            "source_sha256",
            "section_id",
            "entry_sha256",
            "scriptable",
            "policy_authorized",
        }
        for item in mapped_commands
    )
    assert receipt["source_path"] == "@input"
    assert receipt["outputs"][0]["path"] == candidate.relative_to(session).as_posix()
    assert core.load_json(session / "manifest.json")["runs"][-1]["id"] == "120-delivery"

    replay = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(session / "scripts" / "120-delivery.ssf"),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )
    assert replay["replayed"] is True

    provenance_path = session / "scripts" / "120-delivery.provenance.json"
    original_provenance = provenance_path.read_bytes()
    provenance_path.write_bytes(original_provenance + b" \n")
    with pytest.raises(core.ContractError, match="binding differs|receipt is invalid"):
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(session / "scripts" / "120-delivery.ssf"),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
        )
    provenance_path.write_bytes(original_provenance)

    with pytest.raises(core.ContractError, match="requested lineage"):
        core.run_script(
            str(session),
            protocol="color.finish",
            script_value=str(session / "scripts" / "120-delivery.ssf"),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
        )

    script_path = session / "scripts" / "120-delivery.ssf"
    original_script = script_path.read_bytes()
    script_path.write_bytes(original_script + b"# drift\n")
    with pytest.raises(core.ContractError, match="script differs"):
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(script_path),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
        )
    script_path.write_bytes(original_script)

    candidate.write_bytes(b"changed")
    with pytest.raises(core.ContractError, match="output changed"):
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(session / "scripts" / "120-delivery.ssf"),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
        )


@pytest.mark.parametrize("drift", ["script", "provenance", "manual_evidence"])
def test_execution_time_script_or_knowledge_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    candidate = session / "artifacts" / "120-delivery.jpg"
    script = session / "scripts" / "120-delivery.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                f'savejpg "{candidate.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manual_evidence: list[Path] = []
    if drift == "manual_evidence":
        evidence = session / "reports" / "manual-evidence" / "savejpg.command.json"
        stdout = StringIO()
        stderr = StringIO()
        assert manual_query.run(
            ("--command", "savejpg"),
            stdout=stdout,
            stderr=stderr,
        ) == 0, stderr.getvalue()
        evidence.write_text(stdout.getvalue(), encoding="utf-8")
        manual_evidence.append(evidence)
    provenance = _write_ssf_provenance(
        session,
        script,
        "delivery.render",
        manual_evidence=manual_evidence,
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_jpeg(candidate)
        changed = (
            script
            if drift == "script"
            else provenance
            if drift == "provenance"
            else manual_evidence[0]
        )
        changed.write_bytes(changed.read_bytes() + b" \n")
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )

    assert receipt["status"] == "failed"
    assert receipt["script_unchanged"] is (drift != "script")
    assert receipt["knowledge_bindings_unchanged"] is (
        drift not in {"provenance", "manual_evidence"}
    )
    assert candidate.is_file()

    next_script = session / "scripts" / "121-delivery.ssf"
    next_script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{candidate}"',
                f'savejpg "{(session / "artifacts" / "121-delivery").with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(core.ContractError) as raised:
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(next_script),
            source_value=str(candidate),
            expected_values=[str(session / "artifacts" / "121-delivery.jpg")],
            timeout=30,
        )
    assert raised.value.code == "source_unbound"


def test_validate_only_writes_static_receipt_without_starting_siril(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    candidate = session / "artifacts" / "120-delivery.jpg"
    script = session / "scripts" / "120-delivery.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                "stat main",
                f'savejpg "{candidate.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "delivery.render")

    marker = tmp_path / "siril-was-started"
    _siril.write_text(
        f'#!/bin/sh\nprintf called > "{marker}"\nexit 99\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "deep_sky_siril.py"),
            "run",
            "--session",
            str(session),
            "--protocol",
            "delivery.render",
            "--script",
            str(script),
            "--source",
            str(source),
            "--expect",
            str(candidate),
            "--timeout",
            "30",
            "--validate-only",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=tooling._subprocess_environment("python_cli"),
    )
    validated = json.loads(completed.stdout)
    assert not marker.exists()

    def must_not_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("validate-only must not start siril-cli")

    def must_not_probe(*, offline: bool = False) -> dict[str, object]:
        del offline
        raise AssertionError("validate-only must use the frozen session probe")

    monkeypatch.setattr(core.subprocess, "run", must_not_run)
    monkeypatch.setattr(tooling, "probe_tools", must_not_probe)
    assert validated["status"] == "success"
    assert validated["mode"] == "validate_only"
    assert validated["executed"] is False
    assert validated["script_unchanged"] is True
    assert validated["knowledge_bindings_unchanged"] is True
    assert "command_policy_sha256" not in validated
    assert "protocol_applicability" in validated["scope"]["checked"]
    assert validated["scope"]["not_checked"] == [
        "visual_quality",
        "siril_execution",
    ]
    assert (session / "reports" / "120-delivery-static-validation.json").is_file()
    assert not candidate.exists()
    assert not (session / "runs" / "120-delivery.json").exists()
    assert core.load_json(session / "manifest.json")["runs"] == []

    replay = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
        validate_only=True,
    )
    assert replay["replayed"] is True

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_jpeg(candidate)
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(tooling, "probe_tools", lambda *, offline=False: _fake_probe(_siril, offline=offline))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    executed = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )
    assert executed["status"] == "success"
    assert candidate.is_file()


def test_run_fails_closed_on_unknown_log_error_but_preserves_output_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    script, candidate = _delivery_script(session, source)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_jpeg(candidate)
        return subprocess.CompletedProcess(
            command,
            0,
            "error: no suitable data in src fits\n"
            "log: Script execution finished successfully.\n",
        )

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )

    assert receipt["status"] == "failed"
    assert receipt["exit_code"] == 0
    assert receipt["output_validations"]["artifacts/120-delivery.jpg"]["passed"] is True
    assert receipt["outputs"][0]["sha256"] == core.sha256_file(candidate)
    assert receipt["log_diagnostics"]["status"] == "failed"
    assert receipt["log_diagnostics"]["findings"][0]["code"] == "unclassified_siril_error"
    assert receipt["runtime"]["network"]["effective_offline"] is True
    assert "--offline" in receipt["invocation"]
    assert candidate.resolve() not in session_state._verified_run_outputs(session)


def test_run_accepts_context_bound_exif_and_post_success_pipe_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    script, candidate = _delivery_script(session, source)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_jpeg(candidate)
        output = (
            "Error: Unable to read EXIF metadata\n"
            "log: Reading JPG: file 120-delivery.jpg, 3 layer(s), 4x3 pixels\n"
            "Red layer: Mean: 18, Median: 16, Sigma: 5, Min: 0, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
            "Green layer: Mean: 20, Median: 18, Sigma: 5, Min: 0, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
            "Blue layer: Mean: 22, Median: 20, Sigma: 5, Min: 0, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
            "log: Script execution finished successfully.\n"
            "Exception ignored while flushing sys.stdout:\n"
            "BrokenPipeError: [Errno 32] Broken pipe\n"
        )
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )

    assert receipt["status"] == "success"
    assert receipt["log_diagnostics"]["status"] == "warning"
    assert [item["code"] for item in receipt["log_diagnostics"]["findings"]] == [
        "jpeg_exif_unavailable",
        "post_success_pipe_flush",
    ]


def test_new_receipt_replay_rechecks_log_and_diagnostic_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, _candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    script = session / "scripts" / f"{run_id}.ssf"
    log = session / "logs" / f"{run_id}.log"
    log.write_text(log.read_text(encoding="utf-8") + "error: injected drift\n", encoding="utf-8")

    with pytest.raises(core.ContractError) as raised:
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(session / "artifacts" / f"{run_id}.jpg")],
            timeout=30,
        )
    assert raised.value.code == "manifest_invalid"


def test_cli_returns_one_for_a_failed_run_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "run_script", lambda *_args, **_kwargs: {"status": "failed"})
    assert cli.main(
        [
            "run",
            "--session",
            "/unused/session",
            "--protocol",
            "delivery.render",
            "--script",
            "/unused/120-delivery.ssf",
            "--source",
            "/unused/source.fit",
            "--expect",
            "/unused/candidate.jpg",
        ]
    ) == 1


def _color_calibration_script(
    session: Path,
    source: Path,
    *,
    run_id: str,
    catalogue: str | None,
    local_gaia: Path | None = None,
) -> tuple[Path, Path]:
    candidate = session / "previews" / f"{run_id}.jpg"
    script = session / "scripts" / f"{run_id}.ssf"
    lines = ["requires 1.4.4 1.5.0", "set32bits"]
    if local_gaia is not None:
        lines.append(f'set "core.catalogue_gaia_photo={local_gaia}"')
    lines.append(f'load "{source}"')
    if catalogue is not None:
        lines.append(f"pcc -catalog={catalogue}")
    lines.extend([f'savejpg "{candidate.with_suffix("")}" 95', "close"])
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_ssf_provenance(session, script, "color.calibrate")
    return script, candidate


def test_network_policy_defaults_every_non_color_protocol_to_offline() -> None:
    script = "requires 1.4.4 1.5.0\nset32bits\n"
    for protocol in validation_rules.public_protocols():
        if protocol == "color.calibrate":
            continue
        network = contract.classify_siril_network(
            script,
            protocol=protocol,
            session_offline=False,
        )
        assert network["effective_offline"] is True
        assert network["reason"] == "protocol_offline"


@pytest.mark.parametrize(
    ("catalogue", "expect_offline", "reason"),
    [
        (None, True, "protocol_offline"),
        ("localgaia", True, "local_gaia"),
        ("gaia", False, "remote_gaia_explicit"),
    ],
)
def test_color_calibration_network_modes_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalogue: str | None,
    expect_offline: bool,
    reason: str,
) -> None:
    local_gaia = tmp_path / "gaia" if catalogue == "localgaia" else None
    if local_gaia is not None:
        local_gaia.mkdir()
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        local_gaia=local_gaia,
    )
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue=catalogue,
        local_gaia=local_gaia,
    )

    result = core.run_script(
        str(session),
        protocol="color.calibrate",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
        validate_only=True,
    )

    assert result["network"]["effective_offline"] is expect_offline
    assert result["network"]["reason"] == reason
    assert result["validation"]["network"] == result["network"]


def test_offline_session_rejects_explicit_remote_gaia(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch, offline=True)
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue="gaia",
    )
    with pytest.raises(core.ContractError) as raised:
        core.run_script(
            str(session),
            protocol="color.calibrate",
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
            validate_only=True,
        )
    assert raised.value.code == "network_forbidden"


def test_pcc_requires_an_explicit_supported_catalogue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue=None,
    )
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            f'load "{source}"\n',
            f'load "{source}"\npcc\n',
        ),
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "color.calibrate")
    with pytest.raises(core.ContractError, match="-catalog=gaia\\|localgaia"):
        core.run_script(
            str(session),
            protocol="color.calibrate",
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
            validate_only=True,
        )


@pytest.mark.parametrize(
    ("catalogue", "expect_offline"),
    [("localgaia", True), ("gaia", False)],
)
def test_color_calibration_execution_uses_the_validated_network_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalogue: str,
    expect_offline: bool,
) -> None:
    local_gaia = tmp_path / "gaia" if catalogue == "localgaia" else None
    if local_gaia is not None:
        local_gaia.mkdir()
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        local_gaia=local_gaia,
    )
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue=catalogue,
        local_gaia=local_gaia,
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_jpeg(candidate)
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="color.calibrate",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )

    assert receipt["status"] == "success"
    assert ("--offline" in receipt["invocation"]) is expect_offline
    assert receipt["runtime"]["network"]["effective_offline"] is expect_offline


def _make_session_legacy(session: Path) -> None:
    payload = core.load_json(session / "session.json")
    payload.pop("execution_policy")
    unsigned = dict(payload)
    unsigned.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(unsigned)
    core.atomic_write_json(session / "session.json", payload)


def test_legacy_session_is_readable_but_rejects_new_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    _make_session_legacy(session)
    script, candidate = _delivery_script(session, source)

    with pytest.raises(core.ContractError) as raised:
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
        )
    assert raised.value.code == "legacy_session_read_only"

    with pytest.raises(core.ContractError) as static_raised:
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
            validate_only=True,
        )
    assert static_raised.value.code == "legacy_session_read_only"


def test_legacy_receipt_without_diagnostics_can_replay_and_finalize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    _make_session_legacy(session)

    receipt_path = session / "runs" / f"{run_id}.json"
    legacy_receipt = core.load_json(receipt_path)
    legacy_receipt.pop("log_diagnostics")
    legacy_receipt["runtime"].pop("network")
    core.atomic_write_json(receipt_path, legacy_receipt)
    manifest = core.load_json(session / "manifest.json")
    entry = next(item for item in manifest["runs"] if item["id"] == run_id)
    entry["receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(session / "manifest.json", manifest)

    script = session / "scripts" / f"{run_id}.ssf"
    replay = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )
    assert replay["replayed"] is True
    assert "log_diagnostics" not in replay

    review = _review(session, run_id, candidate)
    selection = _selection(session, run_id, candidate, review)
    result = core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=True,
    )
    assert result["status"] == "success"


def test_legacy_color_receipt_accepts_only_the_trusted_previous_reference_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue="gaia",
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_jpeg(candidate)
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="color.calibrate",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )
    assert receipt["status"] == "success"

    legacy_sha = "1d241f549828c02a55e45251c0a3374eab64aa982914a71b503b084df82c1a0b"
    provenance_path = script.with_suffix(".provenance.json")
    provenance = core.load_json(provenance_path)
    provenance["references"][0]["sha256"] = legacy_sha
    core.atomic_write_json(provenance_path, provenance)

    receipt_path = session / "runs" / "040-color.json"
    legacy_receipt = core.load_json(receipt_path)
    legacy_receipt.pop("log_diagnostics")
    legacy_receipt["runtime"].pop("network")
    legacy_receipt["script_provenance"] = {
        **core.fingerprint(provenance_path, include_path=False),
        "path": "scripts/040-color.provenance.json",
    }
    reference = legacy_receipt["knowledge_validation"]["references"][0]
    reference["sha256"] = legacy_sha
    reference["size"] = 1707
    core.atomic_write_json(receipt_path, legacy_receipt)
    manifest = core.load_json(session / "manifest.json")
    entry = next(item for item in manifest["runs"] if item["id"] == "040-color")
    entry["receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(session / "manifest.json", manifest)
    _make_session_legacy(session)

    replay = core.run_script(
        str(session),
        protocol="color.calibrate",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate)],
        timeout=30,
    )
    assert replay["replayed"] is True

    changed = core.load_json(receipt_path)
    changed["knowledge_validation"]["references"][0]["sha256"] = "0" * 64
    core.atomic_write_json(receipt_path, changed)
    manifest = core.load_json(session / "manifest.json")
    entry = next(item for item in manifest["runs"] if item["id"] == "040-color")
    entry["receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(session / "manifest.json", manifest)
    with pytest.raises(core.ContractError):
        session_state._verified_success_receipts(session)


def test_new_receipt_cannot_drop_diagnostics_even_if_manifest_is_rehashed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, _candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    receipt_path = session / "runs" / f"{run_id}.json"
    changed = core.load_json(receipt_path)
    changed.pop("log_diagnostics")
    core.atomic_write_json(receipt_path, changed)
    manifest = core.load_json(session / "manifest.json")
    entry = next(item for item in manifest["runs"] if item["id"] == run_id)
    entry["receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(session / "manifest.json", manifest)

    with pytest.raises(core.ContractError) as raised:
        session_state._verified_success_receipts(session)
    assert raised.value.code == "manifest_invalid"


def test_unknown_run_rejects_scientific_protocol_before_static_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state="unknown",
    )
    candidate = session / "previews" / "020-color.jpg"
    script = session / "scripts" / "020-color.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                "stat main",
                f'savejpg "{candidate.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "color.finish")

    with pytest.raises(core.ContractError) as raised:
        core.run_script(
            str(session),
            protocol="color.finish",
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
            validate_only=True,
        )

    assert raised.value.code == "protocol_not_applicable"
    assert not (session / "reports" / "020-color-static-validation.json").exists()


def test_input_inspect_rejects_a_non_input_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    _receipt, direct, _autostretch = _input_inspect_run(
        session,
        source,
        monkeypatch,
    )
    next_direct = session / "previews" / "011-input-direct.jpg"
    next_autostretch = session / "previews" / "011-input-autostretch.jpg"
    script = session / "scripts" / "011-input.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{direct}"',
                "stat main",
                f'savejpg "{next_direct.with_suffix("")}" 95',
                "close",
                f'load "{direct}"',
                "autostretch -linked -2.8 0.22",
                f'savejpg "{next_autostretch.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "input.inspect")

    with pytest.raises(core.ContractError) as raised:
        core.run_script(
            str(session),
            protocol="input.inspect",
            script_value=str(script),
            source_value=str(direct),
            expected_values=[str(next_direct), str(next_autostretch)],
            timeout=30,
            validate_only=True,
        )

    assert raised.value.code == "protocol_not_applicable"


def test_unknown_delivery_is_rejected_after_input_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state="unknown",
    )
    _receipt, _direct, autostretch = _input_inspect_run(
        session,
        source,
        monkeypatch,
    )
    script, candidate = _delivery_script(session, autostretch)

    with pytest.raises(core.ContractError, match="only Stage 1 input.inspect") as raised:
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(script),
            source_value=str(autostretch),
            expected_values=[str(candidate)],
            timeout=30,
            validate_only=True,
        )

    assert raised.value.code == "protocol_not_applicable"
    assert not (session / "reports" / "120-delivery-static-validation.json").exists()


def test_background_adapter_rejects_wrong_sirilpy_version_during_actual_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = SimpleNamespace(
        __version__="1.0.24",
        check_module_version=lambda _requirement: False,
    )
    monkeypatch.setitem(sys.modules, "sirilpy", fake_module)

    with pytest.raises(RuntimeError, match="Unsupported sirilpy version 1.0.24"):
        background_adapter.main(
            [
                "--contract",
                "/unused/contract.json",
                "--contract-sha256",
                "0" * 64,
                "--expected-source",
                "/unused/source.fit",
                "--receipt",
                "/unused/receipt.json",
            ]
        )


def test_background_bridge_requires_minimal_hash_bound_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        width=64,
        height=64,
    )

    report_dir = session / "reports" / "030-background"
    report_dir.mkdir(parents=True)
    contract = report_dir / "background-sample-contract.json"
    core.atomic_write_json(
        contract,
        {
            "schema": "starun-siril.background-sample-contract.v1",
            "source": {
                "path": str(source),
                "sha256": core.sha256_file(source),
                "width": 64,
                "height": 64,
            },
            "fit_samples": [
                {"id": "fit-1", "x": 32, "y": 32},
            ],
        },
    )
    fit = session / "artifacts" / "030-background.fit"
    output_preview = session / "previews" / "030-background.jpg"
    injection = report_dir / "sample-injection-receipt.json"
    script = session / "scripts" / "030-background.ssf"
    adapter = SCRIPTS / "siril_background_samples.py"
    command = (
        f'pyscript "{adapter}" --contract "{contract}" '
        f'--contract-sha256 {core.sha256_file(contract)} '
        f'--expected-source "{source}" --receipt "{injection}"'
    )
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                command,
                "subsky 1 -existing",
                "stat main",
                f'save "{fit.with_suffix("")}" -chksum',
                "autostretch -linked -2.8 0.22",
                f'savejpg "{output_preview.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "background.subtract")
    validation = _validate_script_file(
        script,
        session=session,
        protocol="background.subtract",
        source=source,
        expected_outputs=[fit, output_preview, injection],
        probe=core.load_json(session / "reports" / "tool-probe.json"),
    )
    assert injection.relative_to(session).as_posix() in validation["declared_writes"]
    assert validation["adapters"] == [
        core.fingerprint(adapter, role="background_sample_adapter")
    ]
    assert validation["sirilpy_bridge"] == {
        "status": "runtime_check_required",
        "required_version": "1.0.25",
    }

    valid_script = script.read_text(encoding="utf-8")
    script.write_text(valid_script.replace("--contract-sha256", "--contract-hash"), encoding="utf-8")
    _write_ssf_provenance(session, script, "background.subtract")
    with pytest.raises(core.ContractError, match="options are incomplete"):
        _validate_script_file(
            script,
            session=session,
            protocol="background.subtract",
            source=source,
            expected_outputs=[fit, output_preview, injection],
            probe=core.load_json(session / "reports" / "tool-probe.json"),
        )
    script.write_text(valid_script, encoding="utf-8")
    _write_ssf_provenance(session, script, "background.subtract")

    def fake_background_run(
        command_value: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        _write_fits(fit, width=64, height=64)
        _write_jpeg(output_preview)
        core.atomic_write_json(
            injection,
            {"schema": "starun-siril.background-sample-injection.v1"},
        )
        return subprocess.CompletedProcess(command_value, 0, "")

    monkeypatch.setattr(core.subprocess, "run", fake_background_run)
    monkeypatch.setattr(
        core,
        "_validate_output",
        lambda output_path, **_kwargs: {
            "passed": True,
            **(
                {
                    "decoder": {
                        "artifact_fingerprint": core.fingerprint(
                            output_path, include_path=False
                        )
                    }
                }
                if output_path.suffix.lower() in {".fit", ".fits", ".fts", ".xisf"}
                else {}
            ),
        },
    )
    receipt = core.run_script(
        str(session),
        protocol="background.subtract",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(fit), str(output_preview), str(injection)],
        timeout=30,
    )
    assert receipt["status"] == "success"
    assert receipt["runtime_bindings_unchanged"] is True
    assert receipt["runtime"]["sirilpy_bridge"] == validation["sirilpy_bridge"]


def test_background_contract_keeps_only_deterministic_safety_invariants(
    tmp_path: Path,
) -> None:
    source = tmp_path / "master.fit"
    _write_fits(source, width=64, height=64)
    contract = tmp_path / "background-sample-contract.json"
    valid = {
        "schema": "starun-siril.background-sample-contract.v1",
        "source": {
            "path": str(source),
            "sha256": core.sha256_file(source),
            "width": 64,
            "height": 64,
        },
        "fit_samples": [{"id": "fit-1", "x": 32, "y": 32}],
    }
    core.atomic_write_json(contract, valid)
    validation_rules._validate_background_sample_contract(contract, source)
    edge_bound = {
        **valid,
        "fit_samples": [{"id": "fit-edge", "x": 0, "y": 63}],
    }
    core.atomic_write_json(contract, edge_bound)
    validation_rules._validate_background_sample_contract(contract, source)

    invalid_contracts = [
        {**valid, "status": "accepted"},
        {**valid, "source": {**valid["source"], "path": str(tmp_path / "other.fit")}},
        {**valid, "source": {**valid["source"], "sha256": "0" * 64}},
        {**valid, "source": {**valid["source"], "width": 63}},
        {**valid, "fit_samples": []},
        {
            **valid,
            "fit_samples": [
                {"id": "fit-1", "x": 20, "y": 20},
                {"id": "fit-1", "x": 40, "y": 40},
            ],
        },
        {
            **valid,
            "fit_samples": [
                {"id": "fit-1", "x": 20, "y": 20},
                {"id": "fit-2", "x": 20, "y": 20},
            ],
        },
        {**valid, "fit_samples": [{"id": "fit-1", "x": float("nan"), "y": 32}]},
        {**valid, "fit_samples": [{"id": "fit-1", "x": 64, "y": 32}]},
    ]
    for payload in invalid_contracts:
        contract.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(core.ContractError):
            validation_rules._validate_background_sample_contract(contract, source)


def test_validator_rejects_unverified_session_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    unbound = session / "artifacts" / "unbound.fit"
    _write_fits(unbound)
    output = session / "artifacts" / "120-final.jpg"
    script = session / "scripts" / "120-delivery.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{unbound}"',
                "stat main",
                f'savejpg "{output.with_suffix("")}" 95',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "delivery.render")
    with pytest.raises(core.ContractError, match="unverified session artifact"):
        _validate_script_file(
            script,
            session=session,
            protocol="delivery.render",
            source=source,
            expected_outputs=[output],
            probe=core.load_json(session / "reports" / "tool-probe.json"),
        )


def test_finalize_rejects_unverified_parent_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    review = _review(session, run_id, candidate)
    unverified = session / "previews" / "unverified-parent.jpg"
    _write_jpeg(unverified)
    payload = core.load_json(review)
    payload["inspected_materials"][1] = {
        "path": unverified.relative_to(session).as_posix(),
        "sha256": core.sha256_file(unverified),
    }
    core.atomic_write_json(review, payload)
    selection = _selection(session, run_id, candidate, review)

    with pytest.raises(core.ContractError, match="verified parent preview"):
        core.finalize_session(
            str(session),
            selection_value=str(selection),
            keep_intermediates=False,
        )


def test_finalize_revalidates_selected_run_provenance_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    review = _review(session, run_id, candidate)
    selection = _selection(session, run_id, candidate, review)
    provenance = session / "scripts" / f"{run_id}.provenance.json"
    provenance.write_bytes(provenance.read_bytes() + b" \n")

    with pytest.raises(core.ContractError) as raised:
        core.finalize_session(
            str(session),
            selection_value=str(selection),
            keep_intermediates=False,
        )
    assert raised.value.code == "skill_output_invalid"
    assert "receipt" in str(raised.value).lower()
    assert not (session / "outputs" / "reference.jpg").exists()
    assert not (session / "outputs" / "final.jpg").exists()


@pytest.mark.parametrize(
    ("input_state", "expected_reference"),
    [
        ("linear", "010-input-autostretch.jpg"),
        ("nonlinear", "010-input-direct.jpg"),
    ],
)
def test_finalize_success_delivers_canonical_outputs_and_prunes_intermediates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_state: str,
    expected_reference: str,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state=input_state,
    )
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    review = _review(session, run_id, candidate)
    selection = _selection(session, run_id, candidate, review)
    reference_sha = core.sha256_file(session / "previews" / expected_reference)

    result = core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=False,
    )

    assert result["schema"] == "starun-siril.final-result.v1"
    assert result["status"] == "success"
    assert result["reference"]["path"] == "outputs/reference.jpg"
    assert result["reference"]["sha256"] == reference_sha
    assert result["final"]["path"] == "outputs/final.jpg"
    assert (session / "outputs" / "reference.jpg").is_file()
    assert (session / "outputs" / "final.jpg").is_file()
    assert not candidate.exists()
    finalization = core.load_json(session / "manifest.json")["finalization"]
    assert finalization["schema"] == "starun-siril.finalization.v1"
    assert finalization["cleanup_completed"] is True


def test_unknown_input_can_only_finalize_as_failed_without_formal_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state="unknown",
    )
    _input_inspect_run(session, source, monkeypatch)
    selection = _failed_selection(session)

    result = core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=True,
    )

    assert result["status"] == "failed"
    assert result["intermediates_preserved"] is True
    assert not (session / "outputs" / "reference.jpg").exists()
    assert not (session / "outputs" / "final.jpg").exists()


def test_review_required_preserves_candidate_without_formal_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    review = _review(session, run_id, candidate, verdict="uncertain")
    selection = _selection(
        session,
        run_id,
        candidate,
        review,
        status="review_required",
    )

    result = core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=False,
    )

    assert result["status"] == "review_required"
    assert result["candidate"]["path"] == candidate.relative_to(session).as_posix()
    assert result["retention_policy"] == "preserve"
    assert candidate.is_file()
    assert not (session / "outputs" / "reference.jpg").exists()
    assert not (session / "outputs" / "final.jpg").exists()


def test_failed_finalize_preserves_recovery_material_without_formal_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _source, _siril = _session(tmp_path, monkeypatch)
    marker = session / "artifacts" / "recovery.fit"
    _write_fits(marker)
    selection = _failed_selection(session)

    result = core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=False,
    )

    assert result["status"] == "failed"
    assert result["retention_policy"] == "preserve"
    assert marker.is_file()
    assert not (session / "outputs" / "reference.jpg").exists()
    assert not (session / "outputs" / "final.jpg").exists()


def test_finalize_same_selection_recovers_cleanup_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    review = _review(session, run_id, candidate)
    selection = _selection(session, run_id, candidate, review)
    original = core._complete_prune_plan

    def interrupt_once(_session: Path, _planned: object) -> list[str]:
        raise OSError("simulated interruption")

    monkeypatch.setattr(core, "_complete_prune_plan", interrupt_once)
    with pytest.raises(OSError, match="simulated interruption"):
        core.finalize_session(
            str(session),
            selection_value=str(selection),
            keep_intermediates=False,
        )
    assert (
        core.load_json(session / "manifest.json")["finalization"][
            "cleanup_completed"
        ]
        is False
    )

    monkeypatch.setattr(core, "_complete_prune_plan", original)
    recovered = core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=False,
    )
    replay = core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=False,
    )
    assert recovered == replay
    assert (
        core.load_json(session / "manifest.json")["finalization"][
            "cleanup_completed"
        ]
        is True
    )


def test_finalize_rejects_different_selection_and_committed_output_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        keep_intermediates=True,
    )
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    review = _review(session, run_id, candidate)
    selection = _selection(session, run_id, candidate, review)
    core.finalize_session(
        str(session),
        selection_value=str(selection),
        keep_intermediates=True,
    )

    changed = core.load_json(selection)
    changed["limitations"] = [
        {"code": "minor_residual", "message": "Minor residual remains."}
    ]
    different = session / "different-selection.json"
    core.atomic_write_json(different, changed)
    with pytest.raises(core.ContractError) as raised:
        core.finalize_session(
            str(session),
            selection_value=str(different),
            keep_intermediates=True,
        )
    assert raised.value.code == "finalization_conflict"

    (session / "outputs" / "final.jpg").write_bytes(b"drift")
    with pytest.raises(core.ContractError):
        core.finalize_session(
            str(session),
            selection_value=str(selection),
            keep_intermediates=True,
        )


def test_selection_and_review_validators_reject_schema_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    run_id = str(receipt["id"])
    review = _review(session, run_id, candidate)
    review_payload = core.load_json(review)
    review_payload["notes"] = ""
    core.atomic_write_json(review, review_payload)
    selection = _selection(session, run_id, candidate, review)
    with pytest.raises(core.ContractError):
        core.finalize_session(
            str(session),
            selection_value=str(selection),
            keep_intermediates=True,
        )

    review_payload["notes"] = "Concrete visual review notes."
    core.atomic_write_json(review, review_payload)
    selection_payload = core.load_json(selection)
    selection_payload["output_contains_stars"] = 1
    core.atomic_write_json(selection, selection_payload)
    with pytest.raises(core.ContractError, match="output_contains_stars"):
        core.finalize_session(
            str(session),
            selection_value=str(selection),
            keep_intermediates=True,
        )

    selection_payload["output_contains_stars"] = True
    selection_payload["quality_score"] = 1.0
    core.atomic_write_json(selection, selection_payload)
    with pytest.raises(core.ContractError, match="missing or extra"):
        core.finalize_session(
            str(session),
            selection_value=str(selection),
            keep_intermediates=True,
        )

    failed = _failed_selection(session)
    failed_payload = core.load_json(failed)
    failed_payload["review_receipts"] = [1]
    core.atomic_write_json(failed, failed_payload)
    with pytest.raises(core.ContractError, match="Review receipt paths"):
        core.finalize_session(
            str(session),
            selection_value=str(failed),
            keep_intermediates=True,
        )


def test_atomic_json_write_never_exposes_partial_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "receipt.json"
    core.atomic_write_json(target, {"state": "old"})
    real_replace = os.replace

    def fail_replace(source: object, destination: object) -> None:
        if Path(destination) == target:
            raise OSError("interrupted rename")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        core.atomic_write_json(target, {"state": "new"})
    assert core.load_json(target) == {"state": "old"}
    assert not list(tmp_path.glob(".receipt.json.tmp-*"))


def test_probe_cli_is_offline_discovery_only() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "deep_sky_siril.py"), "probe", "--offline"],
        check=True,
        capture_output=True,
        text=True,
        env=tooling._subprocess_environment("python_cli"),
    )
    payload = json.loads(completed.stdout)
    assert payload["helper_contract_version"] == "1"
    assert payload["policy"] == {
        "offline": True,
        "network_default": "offline",
        "online_exception": "explicit_remote_gaia_color_calibration",
        "automatic_download": False,
        "python_pixel_processing": False,
    }


@pytest.mark.skipif(
    not Path("/Applications/Siril.app/Contents/MacOS/siril-cli").is_file(),
    reason="local Siril 1.4.4 integration runtime is unavailable",
)
def test_real_siril_strictly_reopens_a_small_fits_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    siril = Path("/Applications/Siril.app/Contents/MacOS/siril-cli").resolve()
    source = tmp_path / "small-master.fit"
    _write_fits(source, width=12, height=8, nonzero=True)
    monkeypatch.setattr(
        tooling,
        "probe_tools",
        lambda *, offline=False: _fake_probe(siril, offline=offline),
    )
    session = tmp_path / "real-strict-session"
    session_state.init_session(
        str(source),
        str(session),
        input_state="linear",
        state_evidence=["synthetic linear FITS fixture"],
        channel_mode="mono",
        channel_map=None,
        target_name=None,
        target_type="unknown",
        style="natural",
        stars="preserve",
        offline=True,
        keep_intermediates=True,
        container_validation="strict",
    )
    output = session / "artifacts" / "110-strict-copy.fit"
    script = session / "scripts" / "110-strict-copy.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{source}"',
                "stat main",
                f'save "{output.with_suffix("")}" -chksum',
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "color.finish")

    receipt = core.run_script(
        str(session),
        protocol="color.finish",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(output)],
        timeout=120,
    )

    assert receipt["status"] == "success"
    checked = receipt["output_validations"]["artifacts/110-strict-copy.fit"]
    assert checked["passed"] is True
    assert checked["container_validation"] == "strict"
    assert checked["strict_container"]["format"] == "FITS"
    assert checked["strict_container"]["geometry"] == {
        "width": 12,
        "height": 8,
        "channels": 1,
        "bitpix": 16,
    }
    assert checked["siril_reopen"]["format"] == "FITS"
    assert checked["siril_reopen"]["geometry"] == {
        "width": 12,
        "height": 8,
        "channels": 1,
        "sample_bits": 16,
    }
    assert checked["decoder"]["runtime_binding_unchanged"] is True
    assert checked["decoder"]["statistics_channel_count"] == 1
    assert checked["decoder"]["script"]["path"].startswith(
        "runtime/decode-validation/"
    )


def test_sirilpy_bridge_probe_defers_validation_to_actual_siril_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("SirilPy declaration must not start a child process")

    monkeypatch.setattr(tooling.subprocess, "run", must_not_run)

    probe = tooling._probe_sirilpy_bridge()

    assert probe == {
        "status": "runtime_check_required",
        "required_version": "1.0.25",
    }


def test_tool_probe_does_not_treat_host_sirilpy_as_preflight_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    siril = tmp_path / "siril-cli"
    siril.write_text("binary", encoding="utf-8")
    siril.chmod(0o755)
    monkeypatch.setattr(tooling, "discover_siril", lambda: siril)
    monkeypatch.setattr(tooling, "_tool_version", lambda _path: ("1.4.4", "siril 1.4.4"))
    monkeypatch.setattr(tooling, "_configured_executable", lambda _name: None)
    monkeypatch.setattr(tooling.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tooling, "_discover_starnet_model", lambda _path: None)

    probe = tooling.probe_tools(offline=True)
    assert probe["blocking_reasons"] == []
    assert probe["warnings"] == ["starnet_unavailable_preserve_stars_baseline"]
    assert probe["tools"]["sirilpy_bridge"] == {
        "status": "runtime_check_required",
        "required_version": "1.0.25",
    }


def test_background_adapter_validates_sirilpy_during_real_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[str] = []
    state: dict[str, object] = {"connected": False, "samples": []}
    source = tmp_path / "master.fit"
    _write_fits(source, width=64, height=64)
    contract = tmp_path / "background-sample-contract.json"
    core.atomic_write_json(
        contract,
        {
            "schema": "starun-siril.background-sample-contract.v1",
            "source": {
                "path": str(source),
                "sha256": core.sha256_file(source),
                "width": 64,
                "height": 64,
            },
            "fit_samples": [{"id": "fit-1", "x": 32, "y": 32}],
        },
    )

    class Interface:
        def connect(self) -> None:
            state["connected"] = True

        def log(self, message: str) -> None:
            logs.append(message)

        def get_image_filename(self) -> str:
            return str(source)

        def clear_image_bgsamples(self) -> None:
            state["samples"] = []

        def set_image_bgsamples(
            self,
            points: list[tuple[float, float]],
            *,
            show_samples: bool,
        ) -> bool:
            assert show_samples is False
            state["samples"] = list(points)
            return True

        def get_image_bgsamples(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(position=point, valid=True)
                for point in state["samples"]
            ]

    fake_module = SimpleNamespace(
        __version__="1.0.25",
        check_module_version=lambda requirement: requirement == "==1.0.25",
        SirilInterface=Interface,
    )
    monkeypatch.setitem(sys.modules, "sirilpy", fake_module)
    receipt = tmp_path / "sample-injection-receipt.json"

    status = background_adapter.main(
        [
            "--contract",
            str(contract),
            "--contract-sha256",
            core.sha256_file(contract),
            "--expected-source",
            str(source),
            "--receipt",
            str(receipt),
        ]
    )

    assert status == 0
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert state["connected"] is True
    assert payload["schema"] == "starun-siril.background-sample-injection.v1"
    assert payload["sirilpy_version"] == "1.0.25"
    assert payload["sirilpy_version_requirement"] == "==1.0.25"
    assert payload["status"] == "verified"
    assert payload["requested_count"] == payload["installed_count"] == 1
    assert payload["requested_positions"] == payload["installed_positions"] == [
        [32.0, 32.0]
    ]
    assert payload["source"] == str(source)
    assert logs == ["Injected 1 hash-bound background samples."]


def test_background_adapter_fails_closed_when_siril_changes_injected_positions() -> None:
    class Interface:
        def clear_image_bgsamples(self) -> None:
            return None

        def set_image_bgsamples(
            self,
            _points: list[tuple[float, float]],
            *,
            show_samples: bool,
        ) -> bool:
            assert show_samples is False
            return True

        def get_image_bgsamples(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(position=(20.0, 20.0), valid=True)]

    with pytest.raises(RuntimeError, match="every declared background sample"):
        background_adapter.install_declared_samples(
            Interface(),
            [(20.0, 20.0), (40.0, 40.0)],
        )
