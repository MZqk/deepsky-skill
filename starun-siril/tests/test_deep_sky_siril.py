from __future__ import annotations

from io import BytesIO, StringIO
import hashlib
import json
import os
import shlex
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
    color_ready: bool = False,
    extra_cards: tuple[str, ...] = (),
) -> None:
    cards = [
        "SIMPLE  =                    T",
        "BITPIX  =                   16",
        "NAXIS   =                    2",
        f"NAXIS1  = {width:20d}",
        f"NAXIS2  = {height:20d}",
        "END",
    ]
    if color_ready:
        cards[2] = "NAXIS   =                    3"
        cards[-1:-1] = ["NAXIS3  =                    3", "CTYPE1  = 'RA---TAN'", "CTYPE2  = 'DEC--TAN'",
                       "CRPIX1  =                    6", "CRPIX2  =                    4", "CRVAL1  =                   10",
                       "CRVAL2  =                   20", "CD1_1   =               -0.001", "CD1_2   =                    0",
                       "CD2_1   =                    0", "CD2_2   =                0.001"]
    cards[-1:-1] = extra_cards
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
    if color_ready:
        data *= 3
    data += b"\0" * ((2880 - len(data) % 2880) % 2880)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + data)


def _write_jpeg(path: Path, color: tuple[int, int, int] = (18, 24, 32), *, size=(4, 3)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if artifacts.Image is not None:
        image = artifacts.Image.new("RGB", size, color)
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
            "network_default": "online",
            "offline_option": "explicit_offline_session",
            "catalogue_policy": "explicit_gaia_or_localgaia_per_script",
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
    input_state: str = "nonlinear",
    channel_mode: str = "broadband",
    keep_intermediates: bool = False,
    width: int = 12,
    height: int = 8,
    offline: bool = False,
    local_gaia: Path | None = None,
    color_ready: bool = False,
) -> tuple[Path, Path, Path]:
    source = tmp_path / "master.fit"
    _write_fits(source, width=width, height=height, color_ready=color_ready or channel_mode == "unknown")
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
            [f"user confirmed input_state={input_state}; synthetic stacked master"]
            if input_state in {"linear", "nonlinear"}
            else []
        ),
        channel_mode=channel_mode,
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
    policy = ROOT / "references" / "command-policy.json"
    evidence = manual_evidence or []
    path = script.with_suffix(".provenance.json")
    variant = None
    if protocol == 'stretch':
        from deep_sky_siril_hdr import declaration as hdr_declaration
        from deep_sky_siril_dualband import declaration as hoo_declaration
        value = script.read_text()
        variant = 'hdr' if hdr_declaration(value) is not None else 'hoo' if hoo_declaration(value) is not None else None
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
            "references": session_state.protocol_reference_bindings(protocol, variant=variant),
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
    science = candidate.with_suffix(".fit")
    script = session / "scripts" / f"{run_id}.ssf"
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                f'load "{delivery_source}"',
                "fmul 1",
                "icc_assign sRGB",
                f'save "{science}" -chksum',
                "close",
                f'load "{science}"',
                "stat main",
                f'savejpg "{candidate.with_suffix("")}" 95',
                "close",
                f'load "{candidate}"',
                "stat main",
                "close",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ssf_provenance(session, script, "delivery.render")
    return script, candidate


def _write_delivery_outputs(source, candidate):
    from deep_sky_siril_artifacts import read_fits_pixels
    from .test_native_processing import write_fits
    raw, scale, zero = read_fits_pixels(source)
    header = core.artifacts.read_fits_header(source)
    extra = [(k, repr(v) if isinstance(v, str) else v) for k, v in header.items()
         if k not in {'SIMPLE', 'BITPIX', 'NAXIS', 'NAXIS1', 'NAXIS2', 'NAXIS3', 'BSCALE', 'BZERO'}]
    write_fits(candidate.with_suffix('.fit'), raw.astype(float)*scale+zero, extra=extra)
    _write_jpeg(candidate, size=(raw.shape[2], raw.shape[1]))


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
    if input_state == "linear":
        parent = session / "artifacts" / "070-stretch.fit"
        preview = session / "previews" / "070-stretch.jpg"
        stretch_script = session / "scripts" / "070-stretch.ssf"
        stretch_script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nmtf 0 0.1 1\nasinh -human 2 0 -clipmode=rgbblend\nsave "{parent}" -chksum\nsavejpg "{preview.with_suffix("")}" 95\nclose\n')
        _write_ssf_provenance(session, stretch_script, "stretch")
        def fake_stretch(command, **_kwargs):
            _write_fits(parent); _write_jpeg(preview)
            return subprocess.CompletedProcess(command, 0, "Reading FITS: file output.fit, 1 layer(s), 12x8 pixels, 16 bits\nGray layer: Mean: 100, Median: 90, Sigma: 5, Min: 0, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n")
        monkeypatch.setattr(core.subprocess, "run", fake_stretch)
        result = core.run_script(str(session), protocol="stretch", script_value=str(stretch_script), source_value=str(source), expected_values=[str(parent), str(preview)], timeout=30)
        assert result["status"] == "success"
        delivery_source = parent
    script, candidate = _delivery_script(
        session,
        delivery_source,
        run_id=run_id,
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert any(value == f"--script={script}" for value in command)
        _write_delivery_outputs(delivery_source, candidate)
        output = (
            "Red layer: Mean: 100, Median: 90, Sigma: 5, Min: 0, Max: 255, "
            "bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        )
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **k:
        dict(passed=True, decoder={'artifact_fingerprint': core.fingerprint(path, include_path=False)}))
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(delivery_source),
        expected_values=[str(candidate), str(candidate.with_suffix('.fit'))],
        timeout=30,
    )
    return receipt, candidate


def _review(session: Path, run_id: str, candidate: Path, *, verdict: str = "accept", parent_preview: Path | None = None) -> Path:
    run_path = session / "runs" / f"{run_id}.json"
    path = session / "reviews" / f"{run_id}.json"
    input_state = core.load_json(session / "session.json")["context"]["input_state"]
    if parent_preview is None:
        parent_preview = session / "previews" / (
            "010-input-direct.jpg"
            if input_state == "nonlinear"
            else "010-input-autostretch.jpg"
        )
        if (session / "previews" / "070-stretch.jpg").is_file():
            parent_preview = session / "previews" / "070-stretch.jpg"
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
            "protocol": core.load_json(run_path)["protocol"],
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
    record = core.load_json(run_path)
    if record.get('tone_proposal'):
        draft = core.review_materials_for_run(session, run_id)
        draft.update(verdict=verdict, gates=gates, notes='Synthetic fixture: actual bound tone materials inspected')
        block = draft['tone_assessment']
        block.update(goal_status='met', additional_benefit='observed',
                     benefit_observation='Known synthetic fixture change', adoption_reason='Known fixture benefit and preservation')
        for row in block['regions']:
            row.update(assessment='acceptable', observation='Known synthetic protected-region fixture retained')
        core.atomic_write_json(path, draft)
    if record.get('delivery_export'):
        document = core.load_json(path)
        science = session / record['delivery_export']['fits']
        document['inspected_materials'].append({'path': record['delivery_export']['fits'],
                                                'sha256': core.sha256_file(science)})
        core.atomic_write_json(path, document)
    checks = record.get('processing_checks', {})
    if checks.get('color_protection'):
        document = core.load_json(path)
        block = core._color_protection_skeleton(record)
        for value in block['structures'].values():
            value['not_applicable_reason'] = 'Synthetic fixture has no identified astronomical emission or reflection structure'
        for row in block['regions']:
            row.update(continuity='Synthetic region retained', color='Synthetic colors inspected',
                       weak_edges='Synthetic boundaries retained', assessment='acceptable')
        document['color_protection_assessment'] = block
        document['inspected_materials'].append({'path': block['regions_path'], 'sha256': core.sha256_file(session / block['regions_path'])})
        core.atomic_write_json(path, document)
    if checks.get('closure', {}).get('warnings') or checks.get('delivery_export', {}).get('histogram_gate', {}).get('warnings'):
        document = core.load_json(path)
        document['inspected_materials'].append({'path': run_path.relative_to(session).as_posix(),
                                               'sha256': core.sha256_file(run_path)})
        core.atomic_write_json(path, document)
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
    # review-check is read-only: it derives or validates review evidence and never writes
    # one, so it cannot change a session, a receipt or a delivered result.
    assert cli.PUBLIC_COMMANDS == ("probe", "init", "run", "review-check", "confirm-input", "finalize", "status")
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
    # A new session freezes exactly the shipped policy, with no workflow state and no
    # legacy value carried over.
    assert payload["execution_policy"] == contract.EXECUTION_POLICY
    assert session_state.execution_policy_is_current(payload)
    assert payload["execution_policy"]["log_diagnostics"] == "fail_closed_v3"
    assert payload["execution_policy"]["guidance"] == "evidence_bound_stretch_flow_v29"
    assert set(payload["execution_policy"]) == {
        "recomposition_contour",
        "artifact_review",
        "stretch_execution",
        "stretch_hdr",
        "evidence_admission",
        "stretch_optimization",
        "log_diagnostics",
        "network",
        "starnet",
        "background",
        "star_diagnostics",
        "psf_provenance",
        "reporting",
        "neutral",
        "stretch",
        "visual_review",
        "guidance",
        "stretch_strategy",
        "stretch_routing",
        "stretch_trial_artifacts",
        "stretch_constraint_preflight",
        "stretch_morphology",
        "stretch_tail_comparison",
        "recovery_summary",
        "color_curve_variants",
        "local_contrast_mask",
        "subject_mask",
        "stage7_references",
        "starless_stretch",
        "stretch_parameters",
        "stretch_noise",
        "subject_enhancement",
        "linear_denoise",
        "recomposition_parameters",
        "tone_adjustment",
        "color_curve",
        "color_preservation",
        "chroma_denoise",
        "color_region_goals",
        "color_evaluation",
        "linear_denoise_backend",
        "recomposition_geometry",
        "local_contrast_hdr",
        "dualband",
        "local_saturation",
        "delivery",
        "recomposition_review",
        "filter_metadata",
        "background_sampling",
        "restoration",
        "local_contrast",
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


@pytest.mark.parametrize(
    "case", ["match", "unknown_sibling", "malformed_sibling", "tampered", "probe_drift", "different_bytes", "conflict", "explicit_unknown", "explicit_channel"]
)
def test_init_auto_recovers_only_verified_same_master_declarations(tmp_path, monkeypatch, case):
    previous, source, _ = _session(tmp_path, monkeypatch, input_state="linear", offline=True, color_ready=True)
    payload = core.load_json(previous / "session.json")
    payload["context"].update(
        channel_mode="dualband-osc",
        style="artistic",
        state_evidence=["user confirmed input_state=linear; channel_mode=dualband-osc; stacked, unstretched master"],
    )
    payload["session_sha256"] = core.stable_hash({k: v for k, v in payload.items() if k != "session_sha256"})
    core.atomic_write_json(previous / "session.json", payload)
    kwargs = {**{k: v for k, v in payload["context"].items() if k != "filter_profile"}, "input_state": "auto", "state_evidence": [],
              "channel_mode": "unknown", "style": "natural", "offline": False}
    if case in {"unknown_sibling", "conflict"}:
        sibling = {**kwargs, "input_state": "unknown", "state_evidence": []}
        if case == "conflict":
            sibling.update(input_state="nonlinear", state_evidence=["user confirmed input_state=nonlinear; previously stretched master"])
        session_state.init_session(str(source), str(tmp_path / "sibling"), **sibling)
    elif case == "malformed_sibling":
        (tmp_path / "malformed").mkdir()
        core.atomic_write_json(tmp_path / "malformed/session.json", {"input": None})
    elif case == "tampered":
        payload["context"]["input_state"] = "nonlinear"
        core.atomic_write_json(previous / "session.json", payload)
    elif case == "probe_drift":
        (previous / "reports/tool-probe.json").write_text("{}\n")
    elif case == "different_bytes":
        _write_fits(source, nonzero=True, color_ready=True)
    elif case == "explicit_unknown":
        kwargs["input_state"] = "unknown"
    elif case == "explicit_channel":
        kwargs["channel_mode"] = "broadband"
    target = tmp_path / "recovered"
    if case == "conflict":
        with pytest.raises(core.ContractError) as raised:
            session_state.init_session(str(source), str(target), **kwargs)
        assert raised.value.code == "input_evidence_conflict"
        assert not target.exists()
        return
    result = session_state.init_session(str(source), str(target), **kwargs)
    _, frozen, _ = session_state.load_session(target)
    context = result["context"]
    expected_state = "unknown" if case in {"tampered", "probe_drift", "different_bytes", "explicit_unknown"} else "linear"
    assert context["input_state"] == expected_state
    assert context["channel_mode"] == (
        "broadband" if case == "explicit_channel" else "dualband-osc" if expected_state == "linear" else "unknown"
    )
    assert context["style"] == "natural" and context["offline"] is False
    if expected_state == "linear":
        assert str(previous / "session.json") in context["state_evidence"][0]
        assert payload["session_sha256"] in context["state_evidence"][0]
    else:
        assert not context["state_evidence"]
    assert frozen["context"] == context


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


@pytest.mark.parametrize("folder", ["plain", "space and 'quote"])
def test_local_contrast_missing_outputs_have_copyable_expect_arguments(tmp_path, monkeypatch, folder):
    session, source, _siril = _session(tmp_path / folder, monkeypatch)
    script = session / "scripts" / "080-local-contrast.ssf"
    document = (ROOT / "references" / "stages" / "stage-08-starless-enhance.md").read_text()
    skeleton = document.split("```ssf\n")[1].split("```")[0]
    skeleton = skeleton.replace("-out=/abs/session/artifacts/080-local-contrast", '"-out=/abs/session/artifacts/080-local-contrast"')
    script.write_text(skeleton.replace("/abs/current-starless.fit", str(source)).replace("/abs/session", str(session)))
    _write_ssf_provenance(session, script, "structure.local-contrast")
    probe = core.load_json(session / "reports" / "tool-probe.json")
    expected = [
        session / "artifacts" / f"080-{name}.fit"
        for name in ("r", "g", "b", "l", "l-enhanced", "mask", "gain",
                     "r-enhanced", "g-enhanced", "b-enhanced", "local-contrast")
    ] + [session / "previews" / f"080-{name}.jpg" for name in ("parent", "mask", "local-contrast")]
    declared = [session / "artifacts/080-local-contrast.fit", session / "previews/080-local-contrast.jpg"]
    with pytest.raises(core.ContractError, match="undeclared outputs") as raised:
        _validate_script_file(script, session=session, protocol="structure.local-contrast",
                             source=source, expected_outputs=declared, probe=probe)
    assert raised.value.code == "unsafe_siril_script"
    arguments = shlex.split(str(raised.value).split("Add to the run command: ", 1)[1])
    assert arguments[::2] == ["--expect"] * 12
    missing = [Path(value) for value in arguments[1::2]]
    assert missing == sorted(set(expected) - set(declared))
    result = _validate_script_file(script, session=session, protocol="structure.local-contrast",
                                  source=source, expected_outputs=declared + missing, probe=probe)
    assert set(result["declared_writes"]) == {path.relative_to(session).as_posix() for path in expected}


def test_starnet_protocol_uses_pinned_bridge_with_frozen_runtime(tmp_path, monkeypatch):
    import siril_starnet as adapter
    session, source, _siril = _session(tmp_path, monkeypatch, input_state="linear")
    executable = tmp_path / "starnet2"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"test model")
    module = tmp_path / "module.py"
    module.write_text("# frozen dependency\n")
    probe = core.load_json(session / "reports/tool-probe.json")
    runtime = {"compatible": True, "version": "2.6.1", "reasons": [],
               "executable": core.fingerprint(executable, role="starnet2"),
               "model": contract.fingerprint_resource(model, kind="file"),
               "adapter": core.fingerprint(SCRIPTS / "siril_starnet.py", role="starnet_adapter"),
               "source_lock": core.fingerprint(ROOT / "references/starnet-source.json"),
               "python": {"status": "verified", "python_version": "test",
                          "interpreter": core.fingerprint(Path(sys.executable).resolve()),
                          "dependencies": {name: {"version": "test", "module": core.fingerprint(module)} for name in adapter.DEPENDENCIES}}}
    probe["tools"]["starnet"] = runtime
    core.atomic_write_json(session / "reports/tool-probe.json", probe)
    payload = core.load_json(session / "session.json")
    payload["tool_probe"]["sha256"] = core.sha256_file(session / "reports/tool-probe.json")
    payload.pop("session_sha256")
    payload["session_sha256"] = contract.stable_hash(payload)
    core.atomic_write_json(session / "session.json", payload)
    report_dir = session / "reports/080-stars"
    report_dir.mkdir()
    contract_path, bridge = report_dir / "contract.json", report_dir / "bridge.json"
    document = {"schema": adapter.CONTRACT_SCHEMA, "source": {"path": str(source), "sha256": core.sha256_file(source)},
                "runtime": runtime, "stride": 256, "work_dir": str(session / "runtime/starnet/080-stars")}
    core.atomic_write_json(contract_path, document)
    full, starless, layer = [session / "artifacts" / name for name in ("080-full-source.fit", "080-starless.fit", "080-star-layer.fit")]
    script = session / "scripts/080-stars.ssf"
    lines = ["requires 1.4.4 1.5.0", "set32bits", f'load "{source}"', f'save "{full.with_suffix("")}" -chksum',
             f'load "{source}"',
             f'pyscript "{SCRIPTS / "siril_starnet.py"}" --contract "{contract_path}" --contract-sha256 {core.sha256_file(contract_path)} --expected-source "{source}" --receipt "{bridge}"',
             f'save "{starless.with_suffix("")}" -chksum', "close",
             'pm "$artifacts/080-full-source$ - $artifacts/080-starless$"', f'save "{layer.with_suffix("")}" -chksum', "close"]
    script.write_text("\n".join(lines) + "\n")
    _write_ssf_provenance(session, script, "stars.separate")
    validation = _validate_script_file(script, session=session, protocol="stars.separate", source=source,
                                       expected_outputs=[full, starless, layer, bridge], probe=probe)
    assert validation["commands"].count("pyscript") == 1
    assert "starnet" not in validation["commands"]
    for stride in (True, 384, 0):
        with pytest.raises(ValueError, match="stride"):
            adapter.validate_contract({**document, "stride": stride}, source, runtime, session, "080-stars")
    with pytest.raises(ValueError, match="frozen runtime"):
        adapter.validate_contract({**document, "runtime": {}}, source, runtime, session, "080-stars")
    module.write_text("# changed dependency\n")
    with pytest.raises(ValueError, match="dependency drift"):
        adapter.validate_contract(document, source, runtime, session, "080-stars")
    module.write_text("# frozen dependency\n")
    bad_lines = [line.replace(str(SCRIPTS / "siril_starnet.py"), "/tmp/unapproved.py") for line in lines]
    script.write_text("\n".join(bad_lines) + "\n")
    _write_ssf_provenance(session, script, "stars.separate")
    with pytest.raises(core.ContractError, match="audited bridge"):
        _validate_script_file(script, session=session, protocol="stars.separate", source=source,
                              expected_outputs=[full, starless, layer, bridge], probe=probe)
    script.write_text("\n".join(lines) + "\n")
    _write_ssf_provenance(session, script, "stars.separate")
    def fake_run(command, **_kwargs):
        for path in (full, starless, layer):
            _write_fits(path)
        core.atomic_write_json(bridge, {"schema": adapter.RECEIPT_SCHEMA, "status": "verified",
            "contract_sha256": core.sha256_file(contract_path), "source": document["source"], "runtime": runtime, "stride": 256})
        Path(document["work_dir"]).mkdir(parents=True, exist_ok=True)
        output = ("Reading FITS: file output.fit, 1 layer(s), 12x8 pixels, 16 bits\n"
                  "Gray layer: Mean: 100, Median: 90, Sigma: 5, Min: 0, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n")
        return subprocess.CompletedProcess(command, 0, output)
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    arguments = dict(protocol="stars.separate", script_value=str(script), source_value=str(source),
                     expected_values=list(map(str, (full, starless, layer, bridge))), primary_output_value=str(starless), timeout=30)
    receipt = core.run_script(str(session), **arguments)
    assert receipt["status"] == "success"
    assert receipt["runtime_bindings_unchanged"] is True
    assert receipt["processing_checks"]["star_separation"]["max_abs_closure"] == 0
    assert core.run_script(str(session), **arguments)["replayed"] is True


@pytest.mark.parametrize("legacy", [False, True, "pre_background", "channel_background", "material_background", "pre_log_v2", "pre_star_diagnostics", "pre_psf", "pre_reporting"])
def test_previous_execution_policy_is_replay_only(tmp_path, monkeypatch, legacy):
    session, source, _ = _session(tmp_path, monkeypatch)
    receipt, _ = _delivery_run(session, source, monkeypatch)
    payload = core.load_json(session / "session.json")
    payload['execution_policy']['star_diagnostics'] = 'subject_roi_residual_v1'
    payload['execution_policy'].pop('local_saturation')
    payload['execution_policy'].pop('recomposition_review', None)
    payload['execution_policy'].pop('filter_metadata', None)
    payload['execution_policy'].pop('background_sampling', None)
    payload['execution_policy'].pop('restoration', None)
    payload['execution_policy'].pop('color_curve', None)
    for key in ('recomposition_contour', 'artifact_review', 'stretch_execution', 'stretch_hdr', 'stage7_references', 'stretch_optimization', 'subject_mask', 'stretch_routing', 'stretch_trial_artifacts', 'stretch_constraint_preflight','stretch_morphology','stretch_tail_comparison','recovery_summary','evidence_admission','color_curve_variants','local_contrast_mask', 'color_region_goals', 'chroma_denoise', 'color_preservation', 'color_evaluation', 'linear_denoise_backend', 'linear_denoise',
                'recomposition_geometry', 'recomposition_parameters', 'local_contrast_hdr', 'tone_adjustment', 'subject_enhancement'):
        payload['execution_policy'].pop(key, None)
    payload['execution_policy'].pop('dualband', None)
    payload['execution_policy'].pop('stretch_strategy', None)
    payload['execution_policy'].pop('starless_stretch', None)
    payload['execution_policy'].pop('stretch_parameters', None)
    payload['execution_policy'].pop('stretch_noise', None)
    payload['execution_policy']['stretch'] = 'masked_dual_v1'
    payload['execution_policy'].pop('local_contrast', None)
    payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
    payload['execution_policy'].pop('delivery')
    payload['execution_policy']['psf_provenance'] = 'native_star_samples_v1'
    payload['execution_policy']['network'] = 'offline_default_explicit_gaia_v2'
    payload['execution_policy']['starnet'] = 'pinned_python_bridge_v1'
    payload["execution_policy"].pop("guidance")
    payload["execution_policy"].pop("visual_review")
    payload["execution_policy"].pop("neutral")
    payload["execution_policy"].pop("stretch")
    payload["execution_policy"].pop("reporting")
    if legacy != "pre_reporting":
        payload["execution_policy"].pop("psf_provenance")
    if legacy not in {"pre_psf", "pre_reporting"}:
        payload["execution_policy"].pop("star_diagnostics")
    payload["execution_policy"]["log_diagnostics"] = "fail_closed_v2" if legacy in {"pre_star_diagnostics", "pre_psf", "pre_reporting"} else "fail_closed_v1"
    if legacy in {"pre_log_v2", "pre_star_diagnostics", "pre_psf", "pre_reporting"}:
        payload["execution_policy"]["background"] = "heldout_material_gate_v3"
    elif legacy == "material_background":
        payload["execution_policy"]["background"] = "heldout_material_gate_v2"
    elif legacy == "channel_background":
        payload["execution_policy"]["background"] = "heldout_channel_gate_v1"
    else:
        payload["execution_policy"].pop("background")
    network_policy = "offline_default_explicit_gaia_v2" if legacy in {"pre_background", "channel_background", "material_background", "pre_log_v2", "pre_star_diagnostics", "pre_psf", "pre_reporting"} else "offline_default_explicit_gaia_v1"
    payload["execution_policy"]["network"] = network_policy
    if legacy is True:
        payload["execution_policy"].pop("starnet")
    # A genuinely legacy receipt was written under its own offline-first rule, so its
    # recorded network block AND the actual invocation must both reflect that rule.
    # The current online-default run just performed cannot stand in for it.
    receipt_path = session / "runs" / (receipt["id"] + ".json")
    core.atomic_write_json(receipt_path, receipt)
    manifest = core.load_json(session / "manifest.json")
    for entry in manifest["runs"]:
        previous_path = session / "runs" / entry["receipt"]
        previous = core.load_json(previous_path)
        previous["runtime"]["network"] = contract.classify_siril_network(
            (session / previous["script"]["path"]).read_text(encoding="utf-8"),
            protocol=str(previous["protocol"]),
            session_offline=bool(payload["context"].get("offline")),
            policy=network_policy,
        )
        if previous["runtime"]["network"]["effective_offline"]:
            if "--offline" not in previous["invocation"]:
                previous["invocation"] = [*previous["invocation"][:-1], "--offline", previous["invocation"][-1]]
        else:
            previous["invocation"] = [item for item in previous["invocation"] if item != "--offline"]
        if legacy == "pre_reporting":
            historical = [
                ("references/cli-contract.md", "2a15658434890e7651963b1c24450b528edb44a0bd0d276c9fad4ad81a45674a", 22266),
                ("references/session-contract.md", "538c3bdbc7332272eaa4a4c820b7721bd6e3be20ab9457c46b73b1399290fdfe", 7445),
                ("references/quality.md", "532beadc0bafca42993f35b23558f40955692d467824d1a3ab601351ffa80de9", 11910),
            ]
            provenance_path = session / previous["script_provenance"]["path"]
            provenance = core.load_json(provenance_path)
            for name, digest, size in historical:
                record = {"path": name, "sha256": digest, "role": "supporting"}
                provenance["references"].append(record)
                previous["knowledge_validation"]["references"].append({**record, "size": size})
            core.atomic_write_json(provenance_path, provenance)
            previous["script_provenance"].update(core.fingerprint(provenance_path, include_path=False))
        log = session / previous["log"]["path"]
        if legacy == "pre_log_v2" and previous["id"] == receipt["id"]:
            log.write_text(log.read_text() + "\nlog: Error: legacy ignored diagnostic\n")
            previous["log"] = {**core.fingerprint(log, include_path=False), "path": str(log.relative_to(session))}
        previous["log_diagnostics"] = artifacts.diagnose_siril_log(
            log.read_text(), exit_code=previous["exit_code"], timed_out=previous["timed_out"],
            execution_valid=True, policy=payload["execution_policy"]["log_diagnostics"],
            validated_display_names=[Path(name).name for name, result in previous["output_validations"].items()
                                     if result.get("passed") and Path(name).suffix in contract.DISPLAY_IMAGE_SUFFIXES],
        )
        core.atomic_write_json(previous_path, previous)
        entry["receipt_sha256"] = core.sha256_file(previous_path)
    core.atomic_write_json(session / "manifest.json", manifest)
    payload.pop("session_sha256")
    payload["session_sha256"] = contract.stable_hash(payload)
    core.atomic_write_json(session / "session.json", payload)
    assert session_state.load_session(session)[1]["execution_policy"] == payload["execution_policy"]
    replay = core.run_script(str(session), protocol="delivery.render", script_value=str(session / receipt["script"]["path"]),
                             source_value=str(source), expected_values=[str(session / r["path"]) for r in receipt["outputs"]], timeout=30)
    assert replay["replayed"] is True
    with pytest.raises(core.ContractError, match="Legacy sessions"):
        core.recover_run_metrics(str(session), receipt["id"])
    fresh = session / "scripts/999-new.ssf"
    fresh.write_text("requires 1.4.4 1.5.0\nset32bits\nclose\n")
    with pytest.raises(core.ContractError, match="Legacy standalone"):
        core.run_script(str(session), protocol="delivery.render", script_value=str(fresh), source_value=str(source),
                        expected_values=[str(session / "previews/new.jpg")], timeout=30)

    review = _review(session, receipt['id'], session / receipt['outputs'][0]['path'])
    selection = _selection(session, receipt['id'], session / receipt['outputs'][0]['path'], review)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)['status'] == 'success'
    if legacy == "pre_log_v2":
        # Model a committed pre-gate session whose channel mode was unknown.
        payload["context"]["channel_mode"] = "unknown"
        payload.pop("session_sha256")
        payload["session_sha256"] = contract.stable_hash(payload)
        core.atomic_write_json(session / "session.json", payload)
    committed = core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)
    assert committed['status'] == 'success'
    if legacy == "pre_log_v2":
        replayed_path = session / "runs" / (receipt["id"] + ".json")
        changed = core.load_json(replayed_path)
        changed["log_diagnostics"]["policy"] = "fail_closed_v2"
        core.atomic_write_json(replayed_path, changed)
        manifest = core.load_json(session / "manifest.json")
        for entry in manifest["runs"]:
            if entry["id"] == receipt["id"]:
                entry["receipt_sha256"] = core.sha256_file(replayed_path)
        core.atomic_write_json(session / "manifest.json", manifest)
        with pytest.raises(core.ContractError):
            session_state._verified_success_receipts(session)

@pytest.mark.parametrize('input_state', ['linear', 'nonlinear'])
def test_review_check_derives_bound_materials_without_writing(tmp_path, monkeypatch, capsys, input_state):
    session, source, _ = _session(tmp_path, monkeypatch, input_state=input_state)
    if input_state == 'linear':
        with pytest.raises(core.ContractError, match='Unbound stretch SSF|at least two executed'):
            _delivery_run(session, source, monkeypatch)
        assert not list((session/'outputs').iterdir())
        return
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    before = _session_bytes(session)
    # The skeleton binds real fingerprints so the Agent does not transcribe them by hand.
    result = core.review_materials_for_run(str(session), receipt['id'])
    assert result['run_id'] == receipt['id']
    assert result['run_receipt_sha256'] == core.sha256_file(session / 'runs' / (receipt['id'] + '.json'))
    assert result['verdict'] is None and result['notes'] == ''
    assert set(result['gates']) == {'structure', 'background', 'color', 'stars', 'geometry'}
    assert all(value is None for value in result['gates'].values())
    paths = {m['path'] for m in result['inspected_materials']}
    expected_parents = ({'previews/070-stretch.jpg'} if input_state == 'linear' else
                        {'previews/010-input-direct.jpg', 'previews/010-input-autostretch.jpg'})
    assert expected_parents.issubset(paths)
    assert {receipt['delivery_export'][role] for role in ('fits', 'jpeg')}.issubset(paths)
    assert len(paths) == len(result['inspected_materials'])
    for material in result['inspected_materials']:
        target = session / material['path'] if material['path'] != '@input' else Path(source)
        assert core.sha256_file(target) == material['sha256']
    # The command is read-only: it never writes a review receipt.
    assert not (session / 'reviews' / (receipt['id'] + '.json')).exists()
    assert core.check_review(str(session), receipt['id'], None)['status'] == 'review_required'
    assert cli.main(['review-check', '--session', str(session), '--run-id', receipt['id']]) == 0
    assert json.loads(capsys.readouterr().out)['review'] == result
    assert _session_bytes(session) == before
    result.update(verdict='accept', notes='Synthetic delivery review fixture.',
                  gates={name: 'pass' for name in result['gates']})
    review = session / 'reviews' / f"{receipt['id']}.json"
    core.atomic_write_json(review, result)
    assert cli.main(['review-check', '--session', str(session), '--run-id', receipt['id'], '--review', str(review)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'review_valid'
    selection = _selection(session, receipt['id'], candidate, review)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)['status'] == 'success'


@pytest.mark.parametrize('mutation', ['no_preview', 'missing', 'changed', 'symlink'])
def test_delivery_review_skeleton_rejects_unverified_parent_materials(tmp_path, monkeypatch, mutation):
    session, source, _ = _session(tmp_path, monkeypatch)
    receipt, _ = _delivery_run(session, source, monkeypatch)
    preview = session / 'previews/010-input-direct.jpg'
    if mutation == 'no_preview':
        verified = core.session_state._verified_run_outputs(session)
        monkeypatch.setattr(core.session_state, '_verified_run_outputs',
                            lambda _: {path for path in verified if path.suffix not in core.DISPLAY_IMAGE_SUFFIXES})
    elif mutation == 'missing':
        preview.unlink()
    elif mutation == 'changed':
        _write_jpeg(preview, (255, 0, 0))
    else:
        copy = tmp_path / 'same-pixels.jpg'
        copy.write_bytes(preview.read_bytes())
        preview.unlink()
        preview.symlink_to(copy)
    before = _session_bytes(session)
    with pytest.raises(core.ContractError):
        core.review_materials_for_run(str(session), receipt['id'])
    assert _session_bytes(session) == before


@pytest.mark.parametrize('kind', ['ordinary', 'tone', 'finish', 'local', 'background'])
def test_delivery_parent_previews_follow_bound_candidate_roles(tmp_path, monkeypatch, kind):
    session = tmp_path
    (session / 'previews').mkdir()
    before = session / 'previews/unusual-before.jpeg'
    candidate = session / 'previews/unusual-after.jpeg'
    mask = session / 'previews/unusual-mask.png'
    other = session / 'previews/other-branch.jpg'
    source = 'artifacts/scientific-parent.fit'
    prior = {'id': 'parent', 'protocol': 'stretch', 'outputs': [{'path': source},
             *({'path': str(path.relative_to(session))} for path in (before, candidate, mask))]}
    if kind == 'tone':
        prior['protocol'] = 'tone.adjust'
    elif kind == 'finish':
        prior['color_finish'] = []
    elif kind == 'local':
        prior['local_saturation'] = {'previews': {'parent': str(before.relative_to(session)),
            'candidate': str(candidate.relative_to(session)), 'mask': str(mask.relative_to(session))}}
    elif kind == 'background':
        prior['background_validation'] = {'display': {'candidate_preview': str(candidate.relative_to(session))}}
    unrelated = {'id': 'other', 'protocol': 'stretch', 'outputs': [{'path': 'artifacts/other.fit'},
                 {'path': str(other.relative_to(session))}]}
    monkeypatch.setattr(core.session_state, '_verified_success_receipts', lambda _: [prior, unrelated])
    monkeypatch.setattr(core.session_state, '_verified_run_outputs', lambda _: {before, candidate, mask, other})
    monkeypatch.setattr(core, '_adjustment_preview_paths', lambda *_: [before, candidate])
    actual = core._delivery_parent_preview_paths(session, {'id': 'delivery', 'source_path': source})
    assert actual == ({before, candidate, mask} if kind == 'ordinary' else {candidate})


@pytest.mark.parametrize('previous', ['neutral', 'hdr', 'visibility'])
def test_pre_neutral_policy_is_replay_only(tmp_path, monkeypatch, previous):
    session, source, _ = _session(tmp_path, monkeypatch)
    receipt, _ = _delivery_run(session, source, monkeypatch)
    payload = core.load_json(session / 'session.json')
    payload['execution_policy']['background'] = 'heldout_material_gate_v3'
    payload['execution_policy']['star_diagnostics'] = 'subject_roi_residual_v1'
    payload['execution_policy'].pop('local_saturation')
    payload['execution_policy'].pop('recomposition_review', None)
    payload['execution_policy'].pop('filter_metadata', None)
    payload['execution_policy'].pop('background_sampling', None)
    payload['execution_policy'].pop('restoration', None)
    payload['execution_policy'].pop('color_curve', None)
    for key in ('recomposition_contour', 'artifact_review', 'stretch_execution', 'stretch_hdr', 'stage7_references', 'stretch_optimization', 'subject_mask', 'stretch_routing', 'stretch_trial_artifacts', 'stretch_constraint_preflight','stretch_morphology','stretch_tail_comparison','recovery_summary','evidence_admission','color_curve_variants','local_contrast_mask', 'color_region_goals', 'chroma_denoise', 'color_preservation', 'color_evaluation', 'linear_denoise_backend', 'linear_denoise',
                'recomposition_geometry', 'recomposition_parameters', 'local_contrast_hdr', 'tone_adjustment', 'subject_enhancement'):
        payload['execution_policy'].pop(key, None)
    payload['execution_policy'].pop('dualband', None)
    payload['execution_policy'].pop('stretch_strategy', None)
    payload['execution_policy'].pop('starless_stretch', None)
    payload['execution_policy'].pop('stretch_parameters', None)
    payload['execution_policy'].pop('stretch_noise', None)
    payload['execution_policy']['stretch'] = 'masked_dual_v1'
    payload['execution_policy'].pop('local_contrast', None)
    payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
    payload['execution_policy'].pop('delivery')
    payload['execution_policy']['psf_provenance'] = 'native_star_samples_v1'
    payload['execution_policy']['network'] = 'offline_default_explicit_gaia_v2'
    payload['execution_policy']['starnet'] = 'pinned_python_bridge_v1'
    assert payload['execution_policy']['visual_review'] == 'subject_visibility_v1'
    assert payload['execution_policy']['stretch'] == 'masked_dual_v1'
    payload['execution_policy'].pop('guidance')
    payload['execution_policy'].pop('visual_review')
    if previous != 'visibility':
        payload['execution_policy'].pop('stretch')
    if previous == 'neutral':
        payload['execution_policy'].pop('neutral')
    payload['execution_policy']['log_diagnostics'] = 'fail_closed_v2'
    _bind_legacy_runtime(session, payload, receipt)
    payload.pop('session_sha256')
    payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(session / 'session.json', payload)
    # A policy that merely drops one or more frozen keys must still be an exact entry in
    # the versioned history table, so its receipts stay replayable and finalizable.
    loaded = session_state.load_session(session)
    assert session_state.known_execution_policy(loaded[1]['execution_policy'])
    assert not session_state.execution_policy_is_current(loaded[1])
    args = dict(protocol='delivery.render', script_value=str(session / receipt['script']['path']),
                source_value=str(source), expected_values=[str(session / r['path']) for r in receipt['outputs']], timeout=30)
    original = (session / 'runs' / (receipt['id'] + '.json')).read_bytes()
    assert core.run_script(str(session), **args)['replayed'] is True
    assert (session / 'runs' / (receipt['id'] + '.json')).read_bytes() == original
    if previous == 'visibility':
        review = _review(session, receipt['id'], session / receipt['outputs'][0]['path'])
        selection = _selection(session, receipt['id'], session / receipt['outputs'][0]['path'], review)
        assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)['status'] == 'success'
    with pytest.raises(core.ContractError, match='Legacy sessions'):
        core.recover_run_metrics(str(session), receipt['id'])
    fresh = session / 'scripts/999-new.ssf'
    fresh.write_text('requires 1.4.4 1.5.0\nset32bits\nclose\n')
    with pytest.raises(core.ContractError, match='Legacy standalone'):
        core.run_script(str(session), **{**args, 'script_value': str(fresh)})


def _bind_legacy_runtime(session, payload, receipt):
    """Make synthetic legacy receipts use the network/log rules they froze."""
    payload["execution_policy"]["starnet"] = "pinned_python_bridge_v1"
    if payload["execution_policy"].get("background") == "heldout_material_gate_v4":
        payload["execution_policy"]["background"] = "heldout_material_gate_v3"
    manifest = core.load_json(session / 'manifest.json')
    for entry in manifest['runs']:
        path = session / 'runs' / entry['receipt']
        record = receipt if entry['id'] == receipt['id'] else core.load_json(path)
        network = contract.classify_siril_network(
            (session / record['script']['path']).read_text(),
            protocol=record['protocol'], session_offline=payload['context']['offline'],
            policy=payload['execution_policy']['network'])
        record['runtime']['network'] = network
        record['invocation'] = [value for value in record['invocation'] if value != '--offline']
        if network['effective_offline']:
            record['invocation'].insert(-1, '--offline')
        record['log_diagnostics'] = core.artifacts.diagnose_siril_log(
            (session / record['log']['path']).read_text(),
            exit_code=record['exit_code'], timed_out=record['timed_out'],
            execution_valid=True, policy=payload['execution_policy']['log_diagnostics'],
            validated_display_names=[Path(name).name for name, result in record['output_validations'].items()
                                     if result.get('passed') and Path(name).suffix in contract.DISPLAY_IMAGE_SUFFIXES])
        core.atomic_write_json(path, record)
        entry['receipt_sha256'] = core.sha256_file(path)
    core.atomic_write_json(session / 'manifest.json', manifest)


def test_pre_visibility_reference_replay_finalize_and_tampering(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    payload = core.load_json(session / 'session.json')
    payload['execution_policy']['background'] = 'heldout_material_gate_v3'
    payload['execution_policy']['star_diagnostics'] = 'subject_roi_residual_v1'
    payload['execution_policy'].pop('local_saturation')
    payload['execution_policy'].pop('recomposition_review', None)
    payload['execution_policy'].pop('filter_metadata', None)
    payload['execution_policy'].pop('background_sampling', None)
    payload['execution_policy'].pop('restoration', None)
    payload['execution_policy'].pop('color_curve', None)
    for key in ('recomposition_contour', 'artifact_review', 'stretch_execution', 'stretch_hdr', 'stage7_references', 'stretch_optimization', 'subject_mask', 'stretch_routing', 'stretch_trial_artifacts', 'stretch_constraint_preflight','stretch_morphology','stretch_tail_comparison','recovery_summary','evidence_admission','color_curve_variants','local_contrast_mask', 'color_region_goals', 'chroma_denoise', 'color_preservation', 'color_evaluation', 'linear_denoise_backend', 'linear_denoise',
                'recomposition_geometry', 'recomposition_parameters', 'local_contrast_hdr', 'tone_adjustment', 'subject_enhancement'):
        payload['execution_policy'].pop(key, None)
    payload['execution_policy'].pop('dualband', None)
    payload['execution_policy'].pop('stretch_strategy', None)
    payload['execution_policy'].pop('starless_stretch', None)
    payload['execution_policy'].pop('stretch_parameters', None)
    payload['execution_policy'].pop('stretch_noise', None)
    payload['execution_policy']['stretch'] = 'masked_dual_v1'
    payload['execution_policy'].pop('local_contrast', None)
    payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
    payload['execution_policy'].pop('delivery')
    payload['execution_policy']['psf_provenance'] = 'native_star_samples_v1'
    payload['execution_policy']['network'] = 'offline_default_explicit_gaia_v2'
    payload['execution_policy']['starnet'] = 'pinned_python_bridge_v1'
    payload['execution_policy'].pop('guidance')
    payload['execution_policy'].pop('visual_review')
    payload['execution_policy']['log_diagnostics'] = 'fail_closed_v2'
    _bind_legacy_runtime(session, payload, receipt)
    payload.pop('session_sha256')
    payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(session / 'session.json', payload)

    reference = {'path': 'references/quality.md',
                 'sha256': 'fbae2f7403aa9e5d83e66b047ed890e3da889ed2afaf0028fc9a98115171cd32',
                 'size': 13825, 'role': 'supporting'}
    provenance_path = session / receipt['script_provenance']['path']
    receipt_path = session / 'runs' / f"{receipt['id']}.json"
    provenance = core.load_json(provenance_path)
    provenance['references'].append({k: v for k, v in reference.items() if k != 'size'})
    receipt['knowledge_validation']['references'].append(reference)

    def bind_records():
        core.atomic_write_json(provenance_path, provenance)
        receipt['script_provenance'].update(core.fingerprint(provenance_path, include_path=False))
        core.atomic_write_json(receipt_path, receipt)
        manifest = core.load_json(session / 'manifest.json')
        next(e for e in manifest['runs'] if e['id'] == receipt['id'])['receipt_sha256'] = core.sha256_file(receipt_path)
        core.atomic_write_json(session / 'manifest.json', manifest)

    bind_records()
    args = dict(protocol='delivery.render', script_value=str(session / receipt['script']['path']),
                source_value=str(source), expected_values=[str(candidate), str(candidate.with_suffix(".fit"))], timeout=30)
    original = receipt_path.read_bytes()
    assert core.run_script(str(session), **args)['replayed'] is True
    assert receipt_path.read_bytes() == original
    review = _review(session, receipt['id'], candidate)
    selection = _selection(session, receipt['id'], candidate, review)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)['status'] == 'success'

    for field, value in [('sha256', '0' * 64), ('size', reference['size'] + 1)]:
        previous = reference[field]
        reference[field] = value
        provenance['references'][-1] = {k: v for k, v in reference.items() if k != 'size'}
        bind_records()
        with pytest.raises(core.ContractError, match='Legacy run evidence'):
            core.run_script(str(session), **args)
        reference[field] = previous



@pytest.mark.parametrize("directory_state", ["absent", "directory", "file", "symlink", "parent_symlink", "root_symlink", "read_error"])

def test_retired_protocol_reference_requires_a_wholly_absent_directory(tmp_path, monkeypatch, directory_state):
    skill = tmp_path / "skill"
    references = skill / "references"
    references.mkdir(parents=True)
    monkeypatch.setattr(session_state, "SKILL_ROOT", skill)
    record = {"path": "references/protocols/input-inspect.md", "sha256": 'a21e780ef42f7f4fe528e3c550f8d76fd5e9d63be8177e27806f46d925b503c8', "size": 357}
    payload = {"execution_policy": dict(next(p for p in session_state._EXECUTION_POLICY_HISTORY if p.get("guidance") == "stage_protocol_bodies_v2"))}
    directory = references / "protocols"
    if directory_state == "directory":
        directory.mkdir()
    elif directory_state == "file":
        directory.write_text("not a directory")
    elif directory_state == "symlink":
        directory.symlink_to(tmp_path / "missing", target_is_directory=True)
    elif directory_state == "parent_symlink":
        references.rmdir()
        references.symlink_to(tmp_path / "missing", target_is_directory=True)
    elif directory_state == "root_symlink":
        link = tmp_path / "skill-link"
        link.symlink_to(skill, target_is_directory=True)
        monkeypatch.setattr(session_state, "SKILL_ROOT", link)
    elif directory_state == "read_error":
        original_lstat = Path.lstat
        def denied(path, *args, **kwargs):
            if path == directory:
                raise PermissionError("Fixture denies directory lookup")
            return original_lstat(path, *args, **kwargs)
        monkeypatch.setattr(Path, "lstat", denied)
    assert session_state._retired_protocol_reference_matches(payload, record) is (directory_state == "absent")


@pytest.mark.parametrize("change", ["current_policy", "unknown_policy", "path", "sha256", "size", "invalid_path", "invalid_sha256", "invalid_size", "boolean_size"])
def test_retired_protocol_reference_rejects_untrusted_evidence(tmp_path, monkeypatch, change):
    skill = tmp_path / "skill"
    (skill / "references").mkdir(parents=True)
    monkeypatch.setattr(session_state, "SKILL_ROOT", skill)
    record = {"path": "references/protocols/input-inspect.md", "sha256": 'a21e780ef42f7f4fe528e3c550f8d76fd5e9d63be8177e27806f46d925b503c8', "size": 357}
    payload = {"execution_policy": dict(next(p for p in session_state._EXECUTION_POLICY_HISTORY if p.get("guidance") == "stage_protocol_bodies_v2"))}
    if change == "current_policy":
        payload["execution_policy"] = contract.EXECUTION_POLICY
    elif change == "unknown_policy":
        payload["execution_policy"]["guidance"] = "unknown"
    elif change == "path":
        record["path"] = "references/protocols/../protocols/input-inspect.md"
    elif change == "sha256":
        record["sha256"] = "0" * 64
    elif change.startswith("invalid_"):
        record[change.removeprefix("invalid_")] = []
    elif change == "boolean_size":
        record["size"] = True
    else:
        record["size"] += 1
    assert not session_state._retired_protocol_reference_matches(payload, record)


@pytest.mark.parametrize("previous_guidance", [None, "progressive_disclosure_v1", "evidence_bound_guidance_v1", "stage_protocol_bodies_v2", "stage_primary_references_v3", "stage_evidence_limits_v4", "stage_starnet_linear_v5", "stage_starnet_precision_v6", "stage_recompose_review_v7", "stage_xisf_metrics_v8"])
@pytest.mark.parametrize("tamper", ["sha256", "size", "evidence"])
def test_previous_guidance_replay_finalize_and_tampering(tmp_path, monkeypatch, previous_guidance, tamper):
    session, source, _ = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    payload = core.load_json(session / "session.json")
    payload['execution_policy'].pop('recomposition_review', None)
    payload['execution_policy'].pop('filter_metadata', None)
    payload['execution_policy'].pop('background_sampling', None)
    payload['execution_policy'].pop('restoration', None)
    payload['execution_policy'].pop('color_curve', None)
    for key in ('recomposition_contour', 'artifact_review', 'stretch_execution', 'stretch_hdr', 'stage7_references', 'stretch_optimization', 'subject_mask', 'stretch_routing', 'stretch_trial_artifacts', 'stretch_constraint_preflight','stretch_morphology','stretch_tail_comparison','recovery_summary','evidence_admission','color_curve_variants','local_contrast_mask', 'color_region_goals', 'chroma_denoise', 'color_preservation', 'color_evaluation', 'linear_denoise_backend', 'linear_denoise',
                'recomposition_geometry', 'recomposition_parameters', 'local_contrast_hdr', 'tone_adjustment', 'subject_enhancement'):
        payload['execution_policy'].pop(key, None)
    payload['execution_policy'].pop('dualband', None)
    payload['execution_policy'].pop('stretch_strategy', None)
    payload['execution_policy'].pop('starless_stretch', None)
    payload['execution_policy'].pop('stretch_parameters', None)
    payload['execution_policy'].pop('stretch_noise', None)
    payload['execution_policy']['stretch'] = 'masked_dual_v1'
    payload['execution_policy'].pop('local_contrast', None)
    payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
    payload["execution_policy"]["background"] = "heldout_material_gate_v3"
    if previous_guidance not in {'stage_recompose_review_v7', 'stage_xisf_metrics_v8'}:
        payload["execution_policy"]["star_diagnostics"] = "subject_roi_residual_v1"
        payload["execution_policy"]["delivery"] = "paired_fits_jpeg_v1"
        receipt["delivery_export"]["policy"] = "paired_fits_jpeg_v1"
        receipt["processing_checks"]["delivery_export"]["policy"] = "paired_fits_jpeg_v1"
    if previous_guidance not in {"evidence_bound_guidance_v1", "stage_protocol_bodies_v2", "stage_primary_references_v3", "stage_evidence_limits_v4", "stage_starnet_linear_v5", "stage_starnet_precision_v6", "stage_recompose_review_v7", "stage_xisf_metrics_v8"}:
        payload['execution_policy'].pop('local_saturation')
        payload['execution_policy'].pop('recomposition_review', None)
        payload['execution_policy'].pop('filter_metadata', None)
        payload['execution_policy'].pop('background_sampling', None)
        payload['execution_policy'].pop('restoration', None)
        payload['execution_policy'].pop('color_curve', None)
        for key in ('recomposition_contour', 'artifact_review', 'stretch_execution', 'stretch_hdr', 'stage7_references', 'stretch_optimization', 'subject_mask', 'stretch_routing', 'stretch_trial_artifacts', 'stretch_constraint_preflight','stretch_morphology','stretch_tail_comparison','recovery_summary','evidence_admission','color_curve_variants','local_contrast_mask', 'color_region_goals', 'chroma_denoise', 'color_preservation', 'color_evaluation', 'linear_denoise_backend', 'linear_denoise',
                    'recomposition_geometry', 'recomposition_parameters', 'local_contrast_hdr', 'tone_adjustment', 'subject_enhancement'):
            payload['execution_policy'].pop(key, None)
        payload['execution_policy'].pop('dualband', None)
        payload['execution_policy'].pop('stretch_strategy', None)
        payload['execution_policy'].pop('starless_stretch', None)
        payload['execution_policy'].pop('stretch_parameters', None)
        payload['execution_policy'].pop('stretch_noise', None)
        payload['execution_policy']['stretch'] = 'masked_dual_v1'
        payload['execution_policy'].pop('local_contrast', None)
        payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
        payload['execution_policy'].pop('delivery')
        payload['execution_policy']['psf_provenance'] = 'native_star_samples_v1'
        payload['execution_policy']['network'] = 'offline_default_explicit_gaia_v2'
    if previous_guidance is None:
        payload["execution_policy"].pop("guidance")
    else:
        payload["execution_policy"]["guidance"] = previous_guidance
    _bind_legacy_runtime(session, payload, receipt)
    if previous_guidance in {"stage_starnet_linear_v5", "stage_starnet_precision_v6", "stage_recompose_review_v7", "stage_xisf_metrics_v8"}:
        payload["execution_policy"]["starnet"] = "versioned_linear_bridge_v2"
    payload.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(payload)
    core.atomic_write_json(session / "session.json", payload)
    assert session_state.known_execution_policy(payload["execution_policy"])
    assert not session_state.execution_policy_is_current(payload)

    previous_references = (
        ("SKILL.md", "7d1bf3e99fdcb78db3f5e100a8273691653755064c4933522b0261fe9810bcce", 20756),
        ("references/protocol-index.md", "0716d2782db02b853b64f6d081ba4875f58da27f79999e17114f537894f2047a", 6049),
        ("references/stage-sequence.md", "27649f748f7f453fe23097ba17cabf8a49ef4d6f11b5b70b89d2f2f5f53f10ea", 6949),
        ("references/cli-contract.md", "47174cacb5337f3bbacc82d6c438cfe89949fa06b472ee9f65ed4c68ce02f341", 27365),
        ("references/quality.md", "de96726b73de4ba4b9e35d02b3645a5a7d2c80a9da57cdecf2d7a9c161e9f64a", 15727),
        ("references/delivery.md", "74b76876173bc33fd0f0d24d145b87f360b210aa96fd786143d55c72a3c7f5fd", 2609),
        ("references/session-contract.md", "32c0f6b470bacd30b0fa406765e476aabdd434a13fd78412915a51460833c5f9", 11373),
    )
    if previous_guidance == "progressive_disclosure_v1":
        previous_references = (
            ("references/astrometry-source-preservation.md", "215644eb50c87ca8eca3ae4d44518983a9c0efcb8a9b56aeee237482a22b5828", 6289),
            ("references/protocols/restoration-deconvolve.md", "1aa953afc8424752237088bf2f7dd675d9dbc7fe8a15bedbed30d947e2b5d9c4", 7978),
            ("references/session-contract.md", "22fcab70582ba60a3f3486b6ac46c8e986b5b72bacfd96ba148784884ee8b096", 11907),
        )
    if previous_guidance == "stage_primary_references_v3":
        previous_references = (
            ('SKILL.md', '7c85bdfc09deb52796446cf1756138c1a3c9546f8104f6ec12fd6cc473166832', 8681),
            ('references/stages/stage-03-background.md', '238873f001c15b2175cd82e4b779d65596725aa5202969dbb3e1a0d3392f709d', 16987),
            ('references/stages/stage-04-astrometry-color.md', 'acf26b9b33e8e1042ce2cf021f7f88ee82257201f96ec951198823028d2a3c2e', 21491),
            ('references/stages/stage-05-linear-restoration.md', '2027041419e183af04c2f08618d8916d1914bdfdfcaf013e0e9e952ba2c81e64', 15020),
        )
    if previous_guidance == "stage_evidence_limits_v4":
        previous_references = (('SKILL.md', 'db086a28edc7816af40fc7b0783874d510a99175972a63d71410469c7917dc63', 8847), ('references/stages/stage-06-stars-separation.md', '63f4fa6c8674696dc9283b22d8bd3261e8a70978e7ccd89f73aeb6cbba65f6f7', 12383), ('references/quality.md', '275e9834fd68f1184d72a37fc19dec82e94a3e5fdcebb8c363d9508642f8085f', 19041), ('references/session-contract.md', 'fe4f61c94a10639a026ccdb9ad0fa614f72243cd8e014d25e08c7af0421dbc72', 14383), ('references/cli-contract.md', '92c9ff1217770dd094d61932a5c4d530d09947e65631af23facaf7f60ef55e9e', 31246))
    if previous_guidance == "stage_starnet_linear_v5":
        previous_references = (('SKILL.md', 'f4722525b46b029ea13c1800b2ce051a927ee6ca09283d5882e34c5f968f5ec2', 9022), ('references/stages/stage-06-stars-separation.md', '6ecea093fccecf53006853d7a674acd15bd736e747f78e4f7886d23707606b34', 13982), ('references/quality.md', 'ba95cb6d2f629f0db3b548b19dfe440113b34e89511228df0719fc5312401097', 19316), ('references/session-contract.md', '483d4159dc186b979bad6e62ff1f77eef051f5e078faecd9317fa80e989d4e9f', 14651), ('references/cli-contract.md', 'c80be1902ad617da7c775de1599ad395c86e201f85848a2735f659701b26f699', 31656))
    if previous_guidance == "stage_starnet_precision_v6":
        previous_references = (('SKILL.md', 'ae352c38e1d050890c0972adc3b8ed0fb3dde50f72a68e6d262d21d0e1748e61', 9306), ('references/stages/stage-10-delivery-finishing.md', 'c780a711c05a7be976b7a4bc986a98f18dba435a1941467b8a118f75deeb833e', 15397), ('references/quality.md', '3096be1527111d5e6ae2358c8a59a69bc5d73d5d6ce2a2c854dcdfcc7131cdff', 19643), ('references/session-contract.md', '0ef155022a145ac638a3e725cf55de142e7348e2da072331ec4f68bdeb5614dd', 15071), ('references/cli-contract.md', 'a1a799acce24c48dbf6b87c780cdda385d7fa781efc9663d2ab2e6a2741b1633', 32386))
    if previous_guidance == 'stage_recompose_review_v7':
        previous_references = (('references/stages/stage-01-input-inspect.md', 'c5e2a99e73b9435df418340f0a0fb6e2b0a2d2e55a26ad7b479e94578be88c2a', 5021), ('references/quality.md', '571e5f4b2d38ac8ff155d13fbfbf505c2868c9e08f1f879455447dc8f287374e', 20562), ('references/cli-contract.md', '2736a6779d3d713ab12347b2201e8f4cd02383c8288bdf38dec5ab64de827b67', 33206), ('references/session-contract.md', '260961b048e72907744d4fc360dd1d5e3d901c71abb9345a46ada97685eea43c', 16157))
    if previous_guidance == "stage_xisf_metrics_v8":
        previous_references = (('SKILL.md', 'c7091cd213f93dff1d48d5984d0697093091b219545658a5cf1da0be0c246e81', 9456), ('references/quality.md', '939bf3c45e97ede457fb3827d763f6258596d903ff117bef05e36331035f9224', 21400), ('references/cli-contract.md', 'a5e86f7be18a4d4cbb934de2ffb10cce65e47cf78b60f99918d3abef7375d384', 34065), ('references/session-contract.md', 'a1aaeb64f30dd130cfcdb5db5ab4146561f347969f83135e856156992654a6da', 16836))
    provenance_path = session / receipt["script_provenance"]["path"]
    receipt_path = session / "runs" / f"{receipt['id']}.json"
    provenance = core.load_json(provenance_path)
    if previous_guidance in {"evidence_bound_guidance_v1", "stage_protocol_bodies_v2"}:
        previous_references = ()
        # The outgoing primary contained the full body and needed no supporting stage.
        provenance["references"] = provenance["references"][:1]
        receipt["knowledge_validation"]["references"] = receipt["knowledge_validation"]["references"][:1]
        reference = {"path": "references/protocols/delivery-render.md", "role": "primary",
                     "sha256": "769cef8ba90a193c323d470d86fcf8d80e84ded707ad01eda8c2e49e2f329293", "size": 3786}
        if previous_guidance == "stage_protocol_bodies_v2":
            reference.update(sha256='1ffa4d75bccdaa206e8016ff33fd63eb0c418d014c300abadb92b2c10228609d', size=367)
            previous_references = (("references/stages/stage-10-delivery-finishing.md",
                                    '0857080ec705a93a8333c6e79779d1dce68dc07dde807342ff915442cdddec6c', 15681),)
        provenance["references"][0] = {k: v for k, v in reference.items() if k != "size"}
        receipt["knowledge_validation"]["references"][0] = reference
    for name, digest, size in previous_references:
        reference = {"path": name, "sha256": digest, "size": size, "role": "supporting"}
        provenance["references"].append({k: v for k, v in reference.items() if k != "size"})
        receipt["knowledge_validation"]["references"].append(reference)

    def bind_records():
        core.atomic_write_json(provenance_path, provenance)
        receipt["script_provenance"].update(core.fingerprint(provenance_path, include_path=False))
        core.atomic_write_json(receipt_path, receipt)
        manifest = core.load_json(session / "manifest.json")
        next(e for e in manifest["runs"] if e["id"] == receipt["id"])["receipt_sha256"] = core.sha256_file(receipt_path)
        core.atomic_write_json(session / "manifest.json", manifest)

    bind_records()
    args = dict(protocol="delivery.render", script_value=str(session / receipt["script"]["path"]),
                source_value=str(source), expected_values=[str(candidate), str(candidate.with_suffix(".fit"))], timeout=30)
    before = _session_bytes(session)
    assert core.run_script(str(session), **args)["replayed"] is True
    assert _session_bytes(session) == before
    review = _review(session, receipt["id"], candidate)
    selection = _selection(session, receipt["id"], candidate, review)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)["status"] == "success"
    before = _session_bytes(session)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)["status"] == "success"
    assert _session_bytes(session) == before

    fresh = session / "scripts/999-new.ssf"
    fresh.write_text("requires 1.4.4 1.5.0\nset32bits\nclose\n")
    before = _session_bytes(session)
    for validate_only in (False, True):
        with pytest.raises(core.ContractError) as raised:
            core.run_script(str(session), **{**args, "script_value": str(fresh)}, validate_only=validate_only)
        assert raised.value.code == "legacy_session_read_only"
    assert _session_bytes(session) == before

    if tamper == "evidence":
        provenance_path.write_bytes(provenance_path.read_bytes() + b" ")
    else:
        reference = receipt["knowledge_validation"]["references"][-1]
        reference[tamper] = "0" * 64 if tamper == "sha256" else reference["size"] + 1
        provenance["references"][-1] = {k: v for k, v in reference.items() if k != "size"}
        bind_records()
    with pytest.raises(core.ContractError):
        core.run_script(str(session), **args)
    with pytest.raises(core.ContractError):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)


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
        expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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
            expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
            timeout=30,
        )
    provenance_path.write_bytes(original_provenance)

    with pytest.raises(core.ContractError, match="requested lineage"):
        core.run_script(
            str(session),
            protocol="color.finish",
            script_value=str(session / "scripts" / "120-delivery.ssf"),
            source_value=str(source),
            expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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
            expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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
            expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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
                "fmul 1",
                "icc_assign sRGB",
                f'save "{candidate.with_suffix(".fit")}" -chksum',
                "close",
                f'load "{candidate.with_suffix(".fit")}"',
                f'savejpg "{candidate.with_suffix("")}" 95',
                "close",
                f'load "{candidate}"',
                "stat main",
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
        _write_delivery_outputs(source, candidate)
        changed = (
            script
            if drift == "script"
            else provenance
            if drift == "provenance"
            else manual_evidence[0]
        )
        changed.write_bytes(changed.read_bytes() + b" \n")
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(core, "_decode_scientific_output", lambda path, **k:
        dict(passed=True, decoder={"artifact_fingerprint": core.fingerprint(path, include_path=False)}))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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
                "fmul 1",
                "icc_assign sRGB",
                f'save "{candidate.with_suffix(".fit")}" -chksum',
                "close",
                f'load "{candidate.with_suffix(".fit")}"',
                "stat main",
                f'savejpg "{candidate.with_suffix("")}" 95',
                "close",
                f'load "{candidate}"',
                "stat main",
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
            "--expect",
            str(candidate.with_suffix(".fit")),
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
        expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
        timeout=30,
        validate_only=True,
    )
    assert replay["replayed"] is True

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_delivery_outputs(source, candidate)
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(tooling, "probe_tools", lambda *, offline=False: _fake_probe(_siril, offline=offline))
    monkeypatch.setattr(core, "_decode_scientific_output", lambda path, **k:
        dict(passed=True, decoder={"artifact_fingerprint": core.fingerprint(path, include_path=False)}))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    executed = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
        timeout=30,
    )
    assert executed["status"] == "success"
    assert candidate.is_file()


@pytest.mark.parametrize(("error_line", "code"), [
    ("error: no suitable data in src fits", "unclassified_siril_error"),
    ("log: Error: catalogue unavailable", "unclassified_siril_error"),
    ("log: Script execution failed.", "siril_script_failed"),
    ("progress: Script execution failed., 100.00%", "siril_script_failed"),
    ("log: Server unreachable or unresponsive (HTTP code 503 - for details see server)", "catalogue_http_error"),
])
def test_run_fails_closed_on_unknown_log_error_but_preserves_output_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_line: str,
    code: str,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    script, candidate = _delivery_script(session, source)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_delivery_outputs(source, candidate)
        return subprocess.CompletedProcess(
            command,
            0,
            f"{error_line}\n"
            "log: Script execution finished successfully.\n",
        )

    monkeypatch.setattr(core, "_decode_scientific_output", lambda path, **k:
        dict(passed=True, decoder={"artifact_fingerprint": core.fingerprint(path, include_path=False)}))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
        timeout=30,
    )

    assert receipt["status"] == "failed"
    assert receipt["exit_code"] == 0
    assert receipt["output_validations"]["artifacts/120-delivery.jpg"]["passed"] is True
    assert receipt["outputs"][0]["sha256"] == core.sha256_file(candidate)
    assert receipt["log_diagnostics"]["status"] == "failed"
    assert receipt["log_diagnostics"]["findings"][0]["code"] == code
    assert receipt["runtime"]["network"]["effective_offline"] is False
    assert "--offline" not in receipt["invocation"]
    assert candidate.resolve() not in session_state._verified_run_outputs(session)


def test_run_accepts_context_bound_exif_and_post_success_pipe_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch)
    script, candidate = _delivery_script(session, source)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_delivery_outputs(source, candidate)
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

    monkeypatch.setattr(core, "_decode_scientific_output", lambda path, **k:
        dict(passed=True, decoder={"artifact_fingerprint": core.fingerprint(path, include_path=False)}))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(
        str(session),
        protocol="delivery.render",
        script_value=str(script),
        source_value=str(source),
        expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
        timeout=30,
    )

    assert receipt["status"] == "success"
    # Both notices are context-proven runtime noise: accepted, recorded in full, and
    # under fail_closed_v3 they no longer set the run-wide status.
    diagnostics = receipt["log_diagnostics"]
    assert diagnostics["status"] == "clean"
    assert diagnostics["warning_count"] == 0
    assert diagnostics["benign_count"] == 2
    assert [item["code"] for item in diagnostics["findings"]] == [
        "jpeg_exif_unavailable",
        "post_success_pipe_flush",
    ]
    assert {item["severity"] for item in diagnostics["findings"]} == {"benign"}


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
            expected_values=[str(session / "artifacts" / f"{run_id}.jpg"), str(session / "artifacts" / f"{run_id}.fit")],
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


_COLOR_FITS_LOG = ('Reading FITS: file output.fit, 3 layer(s), 12x8 pixels, 16 bits\n' +
    ''.join(c + ' layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n'
            for c in ('Red', 'Green', 'Blue')))


def _protection_regions(session, source):
    from deep_sky_siril_artifacts import read_scientific_header
    header = read_scientific_header(source)
    width, height = header['NAXIS1'], header['NAXIS2']
    path = session / 'reports' / (source.stem + '-color-regions.json')
    core.atomic_write_json(path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': core.sha256_file(source), 'width': width, 'height': height},
        'regions': [{'id': 'sky', 'role': 'background', 'rect': [0, 0, width//2, height]},
                    {'id': 'subject', 'role': 'faint', 'rect': [width//2, 0, width, height]}]})
    return path.relative_to(session).as_posix()


def _color_calibration_script(
    session: Path,
    source: Path,
    *,
    run_id: str,
    catalogue: str | None,
    local_gaia: Path | None = None,
) -> tuple[Path, Path]:
    candidate = session / "artifacts" / f"{run_id}.fit"
    script = session / "scripts" / f"{run_id}.ssf"
    evidence = {"method": "pcc", "source_sha256": core.sha256_file(source),
                "protection_regions": _protection_regions(session, source),
                "equipment": {"pcc_compatibility": {"value": "standard_rgb", "source": "user",
                                                   "evidence": "User confirmed test RGB and IR blocking"}}}
    lines = ["requires 1.4.4 1.5.0", "set32bits"]
    if catalogue is not None:
        lines.append("# color-calibration: " + json.dumps(evidence))
    if local_gaia is not None:
        lines.append(f'set "core.catalogue_gaia_photo={local_gaia}"')
    lines.append(f'load "{source}"')
    if catalogue is not None:
        lines.append(f"pcc -catalog={catalogue}")
    lines.extend([f'save "{candidate}" -chksum', "close"])
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_ssf_provenance(session, script, "color.calibrate")
    return script, candidate


@pytest.mark.parametrize('session_offline,reason', [
    (False, 'session_online'),
    (True, 'session_offline'),
], ids=['online', 'offline'])
def test_network_policy_starts_by_session_state(session_offline, reason) -> None:
    script = "requires 1.4.4 1.5.0\nset32bits\n"
    for protocol in validation_rules.public_protocols():
        network = contract.classify_siril_network(
            script,
            protocol=protocol,
            session_offline=session_offline,
        )
        assert network["effective_offline"] is session_offline
        assert network["reason"] == reason


def test_legacy_network_policies_keep_the_offline_first_rule() -> None:
    script = "requires 1.4.4 1.5.0\nset32bits\n"
    for policy in sorted(contract.LEGACY_NETWORK_POLICIES):
        network = contract.classify_siril_network(
            script,
            protocol="stretch",
            session_offline=False,
            policy=policy,
        )
        assert network["effective_offline"] is True
        assert network["reason"] == "protocol_offline"
        assert network["policy"] == policy


def test_unknown_network_policy_is_rejected() -> None:
    with pytest.raises(contract.ContractError, match="Unknown network policy"):
        contract.classify_siril_network(
            "requires 1.4.4 1.5.0\nset32bits\n",
            protocol="stretch",
            session_offline=False,
            policy="invented_policy_v9",
        )


@pytest.mark.parametrize(
    ("catalogue", "expect_offline", "reason"),
    [
        (None, False, "session_online"),
        ("localgaia", False, "session_online"),
        ("gaia", False, "session_online"),
    ],
)
def test_color_calibration_network_modes_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalogue: str | None,
    expect_offline: bool,
    reason: str,
) -> None:
    """The catalogue stays an explicit per-script choice; it no longer gates the network."""
    local_gaia = tmp_path / "gaia" if catalogue == "localgaia" else None
    if local_gaia is not None:
        local_gaia.mkdir()
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state="linear", color_ready=True,        local_gaia=local_gaia,
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
    assert result["network"]["catalogues"] == ([catalogue] if catalogue else [])
    assert result["validation"]["network"] == result["network"]


def test_offline_session_rejects_explicit_remote_gaia(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source, _siril = _session(tmp_path, monkeypatch, offline=True, input_state="linear", color_ready=True)
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
    session, source, _siril = _session(tmp_path, monkeypatch, input_state="linear", color_ready=True)
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


@pytest.mark.parametrize("catalogue", ["localgaia", "gaia"])
def test_color_calibration_execution_starts_online_and_keeps_the_catalogue_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalogue: str,
) -> None:
    local_gaia = tmp_path / "gaia" if catalogue == "localgaia" else None
    if local_gaia is not None:
        local_gaia.mkdir()
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state="linear", color_ready=True,        local_gaia=local_gaia,
    )
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue=catalogue,
        local_gaia=local_gaia,
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_fits(candidate, color_ready=True, nonzero=True)
        return subprocess.CompletedProcess(command, 0, _COLOR_FITS_LOG)

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
    assert "--offline" not in receipt["invocation"]
    assert receipt["runtime"]["network"]["effective_offline"] is False
    # The catalogue decision stays recorded per script even though it no longer gates networking.
    assert receipt["runtime"]["network"]["catalogues"] == [catalogue]


def test_offline_session_execution_still_passes_offline_to_siril(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_gaia = tmp_path / "gaia"
    local_gaia.mkdir()
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        offline=True,
        input_state="linear",
        color_ready=True,
        local_gaia=local_gaia,
    )
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue="localgaia",
        local_gaia=local_gaia,
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_fits(candidate, color_ready=True, nonzero=True)
        return subprocess.CompletedProcess(command, 0, _COLOR_FITS_LOG)

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
    assert "--offline" in receipt["invocation"]
    assert receipt["runtime"]["network"]["effective_offline"] is True
    assert receipt["runtime"]["network"]["reason"] == "session_offline"


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
            expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
            timeout=30,
        )
    assert raised.value.code == "legacy_session_read_only"

    with pytest.raises(core.ContractError) as static_raised:
        core.run_script(
            str(session),
            protocol="delivery.render",
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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
        expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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
    session, source, _siril = _session(tmp_path, monkeypatch, input_state="linear", color_ready=True)
    script, candidate = _color_calibration_script(
        session,
        source,
        run_id="040-color",
        catalogue="gaia",
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_fits(candidate, color_ready=True, nonzero=True)
        return subprocess.CompletedProcess(command, 0, _COLOR_FITS_LOG)

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
    provenance["references"][0].update(path="references/protocols/color-calibrate.md", sha256=legacy_sha)
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
    reference.update(path="references/protocols/color-calibrate.md", sha256=legacy_sha, size=1707)
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


@pytest.mark.parametrize(("input_state", "channel_mode"), [
    ("unknown", "broadband"), ("linear", "unknown"), ("unknown", "unknown"),
])
@pytest.mark.parametrize("validate_only", [False, True])
@pytest.mark.parametrize("protocol", [
    "color.finish", "background.subtract", "restoration.denoise", "stars.separate", "stretch", "delivery.render",
])
def test_unknown_run_rejects_scientific_protocol_before_static_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_state: str,
    channel_mode: str,
    validate_only: bool,
    protocol: str,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state=input_state,
        channel_mode=channel_mode,
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
    _write_ssf_provenance(session, script, protocol)
    manifest_before = (session / "manifest.json").read_bytes()
    def unexpected_run(*args, **kwargs):
        pytest.fail("Unknown context must be rejected before executing Siril")
    monkeypatch.setattr(core.subprocess, "run", unexpected_run)

    with pytest.raises(core.ContractError) as raised:
        core.run_script(
            str(session),
            protocol=protocol,
            script_value=str(script),
            source_value=str(source),
            expected_values=[str(candidate)],
            timeout=30,
            validate_only=validate_only,
        )

    assert raised.value.code == "protocol_not_applicable"
    context = core.load_json(session / "session.json")["context"]
    assert context["input_state"] == input_state
    assert context["channel_mode"] == channel_mode
    assert not (session / "reports" / "020-color-static-validation.json").exists()
    assert not (session / "runs" / "020-color.json").exists()
    assert not candidate.exists()
    assert (session / "manifest.json").read_bytes() == manifest_before


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


@pytest.mark.parametrize(("input_state", "channel_mode"), [
    ("unknown", "broadband"), ("linear", "unknown"), ("unknown", "unknown"),
])
def test_unknown_delivery_is_rejected_after_input_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_state: str,
    channel_mode: str,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state=input_state,
        channel_mode=channel_mode,
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
            expected_values=[str(candidate), str(candidate.with_suffix(".fit"))],
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


def _assert_unbound_stretch_rejected(session, parent, monkeypatch):
    script, output, preview = session/'scripts/070-followup.ssf', session/'artifacts/070-followup.fit', session/'previews/070-followup.jpg'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{parent}"\nmtf 0 0.1 1\nasinh -human 2 0 -clipmode=rgbblend\nsave "{output}" -chksum\nsavejpg "{preview}" 95\nclose\n')
    _write_ssf_provenance(session,script,'stretch')
    with pytest.raises(core.ContractError, match='Unbound stretch SSF'):
        core.run_script(session,protocol='stretch',script_value=script,source_value=parent,
            expected_values=[output,preview],timeout=30,validate_only=True)
    return


@pytest.mark.parametrize("improve", [False, True, "skip", "warning", "v1", "bad_precision"])
def test_background_bridge_requires_minimal_hash_bound_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    improve: bool | str,
) -> None:
    import numpy as np
    from .test_native_processing import write_fits as write_science
    writer = _write_fits
    def write_gradient(path, **kwargs):
        y, x = np.indices((kwargs['height'], kwargs['width']))
        gradient = (x + .4*y)/600*.02
        pixels = .1 + np.stack([gradient*.1, gradient, gradient]) if improve == 'warning' else .1 + gradient
        write_science(path, pixels)
    monkeypatch.setattr(sys.modules[__name__], '_write_fits', write_gradient)
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state="linear",        width=600,
        height=600,
    )

    monkeypatch.setattr(sys.modules[__name__], '_write_fits', writer)
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
                "width": 600,
                "height": 600,
            },
            "fit_samples": [
                *[{"id": f"fit-{i}", "x": x, "y": y} for i, (x,y) in enumerate(
                    [(200,60),(400,60),(200,150),(400,150),(40,175),(560,175),
                     (200,270),(400,270),(200,375),(400,375),(40,420),(560,420),
                     (200,500),(400,500),(200,560),(400,560)])],
            ],
        },
    )
    from .test_background_validation import regions_for
    regions = report_dir / 'regions.json'
    regions_for(source, regions)
    fit = session / "artifacts" / "030-background.fit"
    output_preview = session / "previews" / "030-background.jpg"
    parent_preview = session / "previews" / "030-background-parent.jpg"
    injection = report_dir / "sample-injection-receipt.json"
    script = session / "scripts" / "030-background.ssf"
    adapter = SCRIPTS / "siril_background_samples_scientific.py"
    command = (
        f'pyscript "{adapter}" --contract "{contract}" '
        f'--contract-sha256 {core.sha256_file(contract)} '
        f'--expected-source "{source}" --receipt "{injection}"'
    )
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "# background-validation: " + json.dumps({"regions": str(regions.relative_to(session)), "sha256": core.sha256_file(regions)}),
                "set32bits",
                f'load "{source}"',
                "fmul 1",
                command,
                "subsky 1 -existing",
                "stat main",
                f'save "{fit.with_suffix("")}" -chksum',
                "close",
                f'load "{source}"',
                "mtf 0 0.006 1",
                f'savejpg "{parent_preview.with_suffix("")}" 95',
                "close",
                f'load "{fit}"',
                "mtf 0 0.006 1",
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
        expected_outputs=[fit, parent_preview, output_preview, injection],
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
            expected_outputs=[fit, parent_preview, output_preview, injection],
            probe=core.load_json(session / "reports" / "tool-probe.json"),
        )
    script.write_text(valid_script, encoding="utf-8")
    _write_ssf_provenance(session, script, "background.subtract")

    def fake_background_run(
        command_value: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if improve == 'skip':
            fit.write_bytes(source.read_bytes())
        elif improve == 'warning':
            y, x = np.indices((600,600)); gradient = (x+.4*y)/600*.02
            write_science(fit, .1 + np.stack([gradient*.5, gradient*.1, gradient*.1]))
        elif improve:
            write_science(fit, np.full((600,600), .1))
        else:
            write_science(fit, np.zeros((600,600)))
        _write_jpeg(output_preview)
        _write_jpeg(parent_preview)
        core.atomic_write_json(
            injection,
            {"schema": "starun-siril.background-sample-injection.v1", "status":"verified",
             "source":str(source), "contract":{"sha256":core.sha256_file(contract)},
             "image_precision":{"schema":"starun-siril.native-float32-identity.v1","status":"failed" if improve == 'bad_precision' else "verified",
                                "source_sha256":core.sha256_file(source),"dtype":"float32","bitpix":-32,
                                "shape":[3 if improve == 'warning' else 1,600,600],"tolerance":"two_float32_ULP","endpoints_preserved":True,
                                "max_abs_normalized_error":0,
                                "scientific_source":__import__("deep_sky_siril_artifacts").read_scientific_pixels(source)[1]},
             "requested_positions":[[p['x'],p['y']] for p in core.load_json(contract)['fit_samples']],
             "installed_positions":[[p['x'],p['y']] for p in core.load_json(contract)['fit_samples']]},
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
        expected_values=[str(fit), str(parent_preview), str(output_preview), str(injection)],
        timeout=30,
    )
    if improve and improve not in {"skip", "bad_precision"}:
        assert receipt['status'] == 'success'
        assert receipt['processing_checks']['background_validation']['status'] == 'passed'
        assert core._load_run(session, receipt['id'])[1] == receipt
        assert not (session/'reviews/030-background.json').exists()
        recovery = core.recover_run_metrics(str(session), receipt['id'])
        recovered = core.load_json(session / recovery['report']['path'])
        assert recovered['background_validation'] == receipt['processing_checks']['background_validation']
        assert recovered['recovery_binding']['inputs']['background_validation'] == core.fingerprint(report_dir/'background-validation.json')
        import deep_sky_siril_processing as processing
        def missing_review(run_id):
            raise core.ContractError('review_required', 'Actual background visual review is required')
        with pytest.raises(core.ContractError, match='visual review is required'):
            processing.prepare_processing(session, core.load_json(session/'session.json'),
                'geometry.crop-near-black', fit, session/'artifacts/crop.fit', f'load "{fit}"', {}, None, None,
                lambda name: core._load_run(session, name), missing_review, run_id='031-crop')
        review = _review(session, receipt['id'], output_preview, parent_preview=parent_preview)
        review_data = core.load_json(review)
        review_data['inspected_materials'].append({key: recovery['report'][key] for key in ('path', 'sha256')})
        core.atomic_write_json(review, review_data)
        with pytest.raises(core.ContractError, match='both matching-transfer previews'):
            core._validate_review(session,str(review),receipt['id'])
        data = core.load_json(review)
        report = report_dir/'background-validation.json'
        data['inspected_materials'].append({'path':str(report.relative_to(session)), 'sha256':core.sha256_file(report)})
        data['notes'] = 'Inspected both fixed-transfer previews and protected regions; native channel warnings retained.'
        core.atomic_write_json(review, data)
        core._validate_review(session,str(review),receipt['id'])
        processing.prepare_processing(session, core.load_json(session/'session.json'),
            'geometry.crop-near-black', fit, session/'artifacts/crop.fit', f'load "{fit}"', {}, None, None,
            lambda name: core._load_run(session,name), lambda name: core._validate_review(session,str(review),name)[0], run_id='031-crop')
        if improve == 'v1':
            legacy = core.load_json(report)
            legacy.update(schema='starun-siril.background-validation.v1',policy='heldout_channel_gate_v1')
            for key in ('decision','warnings','benefit_dimensions','new_shadow_crossings','display','sampling_policy'):
                legacy.pop(key)
            core.atomic_write_json(report,legacy)
            receipt['background_validation'].pop('display')
            receipt['background_validation'].pop('sampling_policy', None)
            measured = receipt['processing_checks']['background_validation']
            measured.pop('sampling_policy', None)
            for key in ('policy','decision','warnings'):
                measured.pop(key)
            measured.update(core.fingerprint(report,include_path=False))
            receipt['log_diagnostics']['policy'] = 'fail_closed_v1'
            receipt_path = session/'runs'/f"{receipt['id']}.json"
            core.atomic_write_json(receipt_path,receipt)
            manifest = core.load_json(session/'manifest.json')
            manifest['runs'][-1]['receipt_sha256'] = core.sha256_file(receipt_path)
            core.atomic_write_json(session/'manifest.json',manifest)
            payload = core.load_json(session/'session.json')
            payload['execution_policy'].pop('local_saturation')
            payload['execution_policy'].pop('recomposition_review', None)
            payload['execution_policy'].pop('filter_metadata', None)
            payload['execution_policy'].pop('background_sampling', None)
            payload['execution_policy'].pop('restoration', None)
            payload['execution_policy'].pop('color_curve', None)
            for key in ('recomposition_contour', 'artifact_review', 'stretch_execution', 'stretch_hdr', 'stage7_references', 'stretch_optimization', 'subject_mask', 'stretch_routing', 'stretch_trial_artifacts', 'stretch_constraint_preflight','stretch_morphology','stretch_tail_comparison','recovery_summary','evidence_admission','color_curve_variants','local_contrast_mask', 'color_region_goals', 'chroma_denoise', 'color_preservation', 'color_evaluation', 'linear_denoise_backend', 'linear_denoise',
                        'recomposition_geometry', 'recomposition_parameters', 'local_contrast_hdr', 'tone_adjustment', 'subject_enhancement'):
                payload['execution_policy'].pop(key, None)
            payload['execution_policy'].pop('dualband', None)
            payload['execution_policy'].pop('stretch_strategy', None)
            payload['execution_policy'].pop('starless_stretch', None)
            payload['execution_policy'].pop('stretch_parameters', None)
            payload['execution_policy'].pop('stretch_noise', None)
            payload['execution_policy']['stretch'] = 'masked_dual_v1'
            payload['execution_policy'].pop('local_contrast', None)
            payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
            payload['execution_policy'].pop('delivery')
            payload['execution_policy']['psf_provenance'] = 'native_star_samples_v1'
            payload['execution_policy']['network'] = 'offline_default_explicit_gaia_v2'
            payload['execution_policy']['background'] = 'heldout_channel_gate_v1'
            payload['execution_policy']['log_diagnostics'] = 'fail_closed_v1'
            payload['execution_policy'].pop('guidance')
            payload['execution_policy'].pop('psf_provenance')
            payload['execution_policy'].pop('reporting')
            payload['execution_policy'].pop('neutral')
            payload['execution_policy'].pop('stretch')
            payload['execution_policy'].pop('visual_review')
            payload['execution_policy'].pop('star_diagnostics')
            _bind_legacy_runtime(session, payload, receipt)
            payload.pop('session_sha256')
            payload['session_sha256'] = core.stable_hash(payload)
            core.atomic_write_json(session/'session.json',payload)
            assert core._load_run(session,receipt['id'])[1] == receipt
            replay = core.run_script(str(session),protocol='background.subtract',script_value=str(script),source_value=str(source),
                expected_values=[str(fit),str(parent_preview),str(output_preview),str(injection)],timeout=30)
            assert replay['replayed'] is True
            (session/'scripts/999-new.ssf').write_text('requires 1.4.4 1.5.0\nset32bits\nclose\n')
            with pytest.raises(core.ContractError,match='Legacy standalone'):
                core.run_script(str(session),protocol='background.subtract',script_value=str(session/'scripts/999-new.ssf'),
                    source_value=str(source),expected_values=[str(session/'artifacts/999-new.fit')],timeout=30)
        else:
            _assert_unbound_stretch_rejected(session,fit,monkeypatch)
        if improve == 'warning':
            assert receipt['processing_checks']['background_validation']['warnings']
        report.write_text(report.read_text()+' ')
        with pytest.raises(core.ContractError, match='Run receipt is invalid'):
            core._load_run(session, receipt['id'])
        with pytest.raises(core.ContractError):
            core.recover_run_metrics(str(session), receipt['id'])
        return
    if improve == 'skip':
        assert receipt['status'] == 'success'
        assert receipt['processing_checks']['background_validation']['decision'] == 'skip'
        assert core._load_run(session, receipt['id'])[1] == receipt
        assert session_state._source_is_bound(session, core.load_json(session/'session.json'), source)
    else:
        assert receipt['status'] == 'failed'
        assert receipt['processing_checks']['background_validation']['status'] != 'passed'
    assert (report_dir / 'background-validation.json').is_file()
    assert fit.exists()
    assert not session_state._source_is_bound(session, core.load_json(session/'session.json'), fit)
    review = session/'reviews/030-forged.json'
    core.atomic_write_json(review, {'schema':'starun-siril.review.v1', 'run_id':receipt['id'],
        'protocol':'background.subtract', 'run_receipt_sha256':core.sha256_file(session/'runs/030-background.json'),
        'verdict':'accept', 'gates':dict.fromkeys(('structure','background','color','stars','geometry'),'pass'),
        'inspected_materials':[{'path':str(output_preview.relative_to(session)),'sha256':core.sha256_file(output_preview)}],
        'notes':'Forged accept cannot rehabilitate failed science.'})
    with pytest.raises(core.ContractError, match='cannot be accepted' if improve == 'skip' else 'Run receipt is invalid'):
        core._validate_review(session,str(review),receipt['id'])
    if improve != 'skip':
        with pytest.raises(core.ContractError, match='Run receipt is invalid'):
            core._load_run(session,receipt['id'])
    selection = _selection(session, receipt['id'], output_preview, review)
    with pytest.raises(core.ContractError, match='cannot enter selected lineage' if improve == 'skip' else 'Run receipt is invalid'):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)
    if improve == 'skip':
        _assert_unbound_stretch_rejected(session,source,monkeypatch)
    assert core.sha256_file(source) == receipt['source']['sha256']
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
    if input_state == 'linear':
        with pytest.raises(core.ContractError, match='Unbound stretch SSF'):
            _delivery_run(session, source, monkeypatch)
        assert not list((session/'outputs').iterdir())
        return
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


@pytest.mark.parametrize(("input_state", "channel_mode"), [
    ("unknown", "broadband"), ("linear", "unknown"), ("unknown", "unknown"),
])
def test_unknown_input_can_only_finalize_as_failed_without_formal_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_state: str,
    channel_mode: str,
) -> None:
    session, source, _siril = _session(
        tmp_path,
        monkeypatch,
        input_state=input_state,
        channel_mode=channel_mode,
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


@pytest.mark.parametrize(("input_state", "channel_mode"), [
    ("unknown", "broadband"), ("linear", "unknown"), ("unknown", "unknown"),
])
@pytest.mark.parametrize("status", ["success", "partial_success", "review_required"])
def test_unknown_context_rejects_candidate_finalization(tmp_path, monkeypatch, input_state, channel_mode, status):
    session, source, _ = _session(tmp_path, monkeypatch, input_state=input_state, channel_mode=channel_mode)
    receipt, direct, autostretch = _input_inspect_run(session, source, monkeypatch)
    assert receipt["status"] == "success"
    review = _review(session, receipt["id"], autostretch, parent_preview=direct)
    selection = _selection(
        session, receipt["id"], autostretch, review, status=status,
        limitations=[{"code": "catalogue_unavailable", "message": "Remote catalogue unavailable"}],
    )
    manifest_before = (session / "manifest.json").read_bytes()
    with pytest.raises(core.ContractError, match="Unknown .* stops after Stage 1") as raised:
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)
    assert raised.value.code == "skill_output_invalid"
    assert (session / "manifest.json").read_bytes() == manifest_before
    assert not (session / "reports/final-result.json").exists()
    assert not list((session / "outputs").iterdir())


@pytest.mark.parametrize("channel_mode", ["mono", "broadband", "narrowband", "dualband-osc"])
def test_known_channel_without_map_allows_processing(tmp_path, monkeypatch, channel_mode):
    session, source, _ = _session(tmp_path, monkeypatch, channel_mode=channel_mode)
    payload = core.load_json(session / "session.json")
    assert payload["context"]["channel_map"] is None
    receipt, _ = _delivery_run(session, source, monkeypatch)
    assert receipt["status"] == "success"


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
        "network_default": "online",
        "offline_option": "explicit_offline_session",
        "catalogue_policy": "explicit_gaia_or_localgaia_per_script",
        "automatic_download": False,
        "python_pixel_processing": True,
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
    _write_fits(source, width=12, height=8, nonzero=True, color_ready=True)
    monkeypatch.setattr(
        tooling,
        "probe_tools",
        lambda *, offline=False: _fake_probe(siril, offline=offline),
    )
    session = tmp_path / "real-strict-session"
    session_state.init_session(
        str(source),
        str(session),
        input_state="nonlinear",
        state_evidence=["user confirmed input_state=nonlinear; synthetic FITS fixture"],
        channel_mode="broadband",
        channel_map=None,
        target_name=None,
        target_type="unknown",
        style="natural",
        stars="preserve",
        offline=True,
        keep_intermediates=True,
        container_validation="strict",
    )
    output = session/'artifacts/110-strict-copy.fit'
    script = session/'scripts/110-strict-copy.ssf'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\n'
        f'save "{output}" -chksum\nclose\n')
    result = subprocess.run([str(siril),'--offline','--directory='+str(session),'--script='+str(script)],
        capture_output=True,text=True,timeout=120)
    assert result.returncode == 0, result.stdout+result.stderr
    checked = core._validate_output(output, session=session, run_id='110-strict-copy', output_index=1,
        siril=_fake_probe(siril)['tools']['siril_cli'], timeout=120, container_validation='strict')
    assert checked["passed"] is True
    assert checked["container_validation"] == "strict"
    assert checked["strict_container"]["format"] == "FITS"
    assert checked["strict_container"]["geometry"] == {
        "width": 12,
        "height": 8,
        "channels": 3,
        "bitpix": -32,
    }
    assert checked["siril_reopen"]["format"] == "FITS"
    assert checked["siril_reopen"]["geometry"] == {
        "width": 12,
        "height": 8,
        "channels": 3,
        "sample_bits": 32,
    }
    assert checked["decoder"]["runtime_binding_unchanged"] is True
    assert checked["decoder"]["statistics_channel_count"] == 3
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

        def get_image(self):
            import numpy as np
            from deep_sky_siril_artifacts import read_fits_pixels
            raw, scale, zero = read_fits_pixels(source)
            return SimpleNamespace(data=(raw.astype(float)*scale+zero).astype(np.float32),
                                   bitpix=-32, top_down=False)

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


def _metric_inspect_cli(session, source, monkeypatch):
    # Prepare the existing fake Siril fixture while letting the public CLI execute the run.
    with monkeypatch.context() as setup:
        setup.setattr(core, "run_script", lambda *args, **kwargs: {})
        _, direct, auto = _input_inspect_run(session, source, monkeypatch)
    return ["run", "--session", str(session), "--protocol", "input.inspect",
            "--script", str(session / "scripts/010-input.ssf"), "--source", str(source),
            "--expect", str(direct), "--expect", str(auto)]


def _session_bytes(session):
    return {str(p.relative_to(session)): p.read_bytes() for p in session.rglob("*") if p.is_file()}


@pytest.mark.parametrize("failure", ["write", "generation", "diagnostic_write"])
def test_metric_failure_preserves_cli_success_and_read_only_replay(tmp_path, monkeypatch, capsys, failure):
    import deep_sky_siril_metrics as metrics
    session, source, _ = _session(tmp_path, monkeypatch)
    args = _metric_inspect_cli(session, source, monkeypatch)
    committed = {}
    original_write = Path.write_text

    def fail_write(path, *args, **kwargs):
        if path.name == ".metrics.json.tmp":
            committed.update(_session_bytes(session))
            raise PermissionError("Injected report write failure")
        return original_write(path, *args, **kwargs)

    def fail_generation(**kwargs):
        committed.update(_session_bytes(session))
        raise ValueError("Injected metric generation failure")

    if failure == "generation":
        monkeypatch.setattr(metrics, "generate_metric_report", fail_generation)
    else:
        monkeypatch.setattr(Path, "write_text", fail_write)
    if failure == "diagnostic_write":
        def fail_diagnostic(*args, **kwargs):
            raise OSError("Injected diagnostic disk failure")
        monkeypatch.setattr(core, "_write_metric_evidence", fail_diagnostic)
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    assert result["status"] == "success"
    warning = result["report_warnings"][0]
    assert warning["code"] == "metrics_generation_failed" and "Injected" in warning["message"]
    assert "--run-id 010-input" in warning["recovery_hint"]
    assert committed and all((session / name).read_bytes() == data for name, data in committed.items())
    receipt = core.load_json(session / "runs/010-input.json")
    assert "report_warnings" not in receipt
    error_path = session / "reports/010-input/metrics-error.json"
    if failure == "diagnostic_write":
        assert "Injected diagnostic disk failure" in warning["persistence_error"]
        assert not error_path.exists()
    else:
        error = core.load_json(error_path)
        assert error["run_receipt_sha256"] == core.sha256_file(session / "runs/010-input.json")
        assert error["warning"] == warning and error["created_at"]
    before_replay = _session_bytes(session)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **kw: pytest.fail("Replay executed Siril"))
    assert cli.main(args) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replayed"] is True and replay["status"] == "success"
    assert replay["report_warnings"][0]["code"] == "metrics_report_unavailable"
    assert _session_bytes(session) == before_replay


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_metric_reporting_does_not_swallow_interrupts(tmp_path, monkeypatch, exception):
    import deep_sky_siril_metrics as metrics
    session, source, _ = _session(tmp_path, monkeypatch)
    args = _metric_inspect_cli(session, source, monkeypatch)
    def interrupt(**kwargs):
        raise exception("Interrupted")
    monkeypatch.setattr(metrics, "generate_metric_report", interrupt)
    with pytest.raises(exception):
        cli.main(args)
    assert core.load_json(session / "runs/010-input.json")["status"] == "success"
    assert not (session / "reports/010-input/metrics-error.json").exists()


def test_metric_recovery_is_bound_idempotent_and_reviewable(tmp_path, monkeypatch, capsys):
    import deep_sky_siril_metrics as metrics
    session, source, _ = _session(tmp_path, monkeypatch)
    args = _metric_inspect_cli(session, source, monkeypatch)
    assert cli.main(args) == 0
    capsys.readouterr()
    (session / "reports/010-input/metrics.json").unlink()
    original = _session_bytes(session)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **kw: pytest.fail("Recovery executed Siril"))
    assert metrics.main(["--session", str(session), "--run-id", "010-input"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success" and result["reused"] is False
    path = session / result["report"]["path"]
    report = core.load_json(path)
    binding = report["recovery_binding"]
    assert binding["run_receipt_sha256"] == core.sha256_file(session / "runs/010-input.json")
    assert binding["inputs"]["parent"] == binding["inputs"]["candidate"] == core.fingerprint(source)
    assert report["recommended_verdict"] == "uncertain"
    assert all((session / name).read_bytes() == data for name, data in original.items())
    before = _session_bytes(session)
    monkeypatch.setattr(metrics, "generate_metric_report", lambda **kw: pytest.fail("Existing report was regenerated"))
    assert core.recover_run_metrics(str(session), "010-input")["reused"] is True
    assert cli.main(args) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["report_warnings"][0]["supplemental_report"] == result["report"]
    assert _session_bytes(session) == before
    review = _review(session, "010-input", session / "previews/010-input-autostretch.jpg")
    data = core.load_json(review)
    data["inspected_materials"].append({k: result["report"][k] for k in ("path", "sha256")})
    core.atomic_write_json(review, data)
    core._validate_review(session, str(review), "010-input")
    report["candidate"]["metrics"]["pixel_count"] += 1
    core.atomic_write_json(path, report)
    with pytest.raises(core.ContractError, match="Reviewed material changed"):
        core._validate_review(session, str(review), "010-input")
    with pytest.raises(core.ContractError, match="recovery binding"):
        core.recover_run_metrics(str(session), "010-input")


@pytest.mark.parametrize("target", ["source", "receipt", "preview", "missing_preview", "recovery_symlink", "directory_symlink", "corrupt_recovery"])
def test_metric_recovery_rejects_drift_missing_inputs_and_unsafe_paths(tmp_path, monkeypatch, target):
    session, source, _ = _session(tmp_path, monkeypatch)
    _input_inspect_run(session, source, monkeypatch)
    recovery = session / "reports/010-input/metrics-recovery.json"
    original = (session / "reports/010-input/metrics.json").read_bytes()
    if target in {"source", "receipt", "preview"}:
        path = {"source": source, "receipt": session / "runs/010-input.json",
                "preview": session / "previews/010-input-direct.jpg"}[target]
        path.write_bytes(path.read_bytes() + b" ")
    elif target == "missing_preview":
        (session / "previews/010-input-direct.jpg").unlink()
    elif target == "recovery_symlink":
        recovery.symlink_to(session / "reports/010-input/metrics.json")
    elif target == "directory_symlink":
        directory = recovery.parent
        moved = session / "reports/moved-metrics"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    else:
        recovery.write_text('{"schema":"broken"}')
    before = _session_bytes(session)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **kw: pytest.fail("Recovery executed Siril"))
    with pytest.raises((core.ContractError, OSError)):
        core.recover_run_metrics(str(session), "010-input")
    assert _session_bytes(session) == before
    assert (session / "reports/010-input/metrics.json").read_bytes() == original


def test_metric_recovery_detects_drift_during_measurement(tmp_path, monkeypatch):
    import deep_sky_siril_metrics as metrics
    session, source, _ = _session(tmp_path, monkeypatch)
    _input_inspect_run(session, source, monkeypatch)
    generate = metrics.generate_metric_report
    def change_source(**kwargs):
        report = generate(**kwargs)
        source.write_bytes(source.read_bytes() + b" ")
        return report
    monkeypatch.setattr(metrics, "generate_metric_report", change_source)
    with pytest.raises(core.ContractError, match="master changed"):
        core.recover_run_metrics(str(session), "010-input")
    assert not (session / "reports/010-input/metrics-recovery.json").exists()


def test_metric_recovery_cannot_replace_required_delivery_materials(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    result = core.recover_run_metrics(str(session), receipt["id"])
    review = _review(session, receipt["id"], candidate)
    data = core.load_json(review)
    data["inspected_materials"] = [{k: result["report"][k] for k in ("path", "sha256")}]
    core.atomic_write_json(review, data)
    selection = _selection(session, receipt["id"], candidate, review)
    with pytest.raises(core.ContractError, match="delivery JPEG"):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)
    assert not (session / "outputs/final.jpg").exists()
    data["inspected_materials"] = [{"path": "reports/010-input/metrics-recovery.json", "sha256": "0" * 64}]
    core.recover_run_metrics(str(session), "010-input")
    data["inspected_materials"][0]["sha256"] = core.sha256_file(session / "reports/010-input/metrics-recovery.json")
    core.atomic_write_json(review, data)
    with pytest.raises(core.ContractError, match="another run"):
        core._validate_review(session, str(review), receipt["id"])
    copied = session / "reports" / receipt["id"] / "copied-metrics.json"
    copied.write_bytes((session / "reports/010-input/metrics-recovery.json").read_bytes())
    data["inspected_materials"] = [{"path": str(copied.relative_to(session)), "sha256": core.sha256_file(copied)}]
    core.atomic_write_json(review, data)
    with pytest.raises(core.ContractError, match="another run"):
        core._validate_review(session, str(review), receipt["id"])
    review = _review(session, receipt["id"], candidate)
    data = core.load_json(review)
    data["inspected_materials"].append({k: result["report"][k] for k in ("path", "sha256")})
    core.atomic_write_json(review, data)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)['status'] == 'success'
    assert (session / result["report"]["path"]).is_file()
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)['status'] == 'success'


@pytest.mark.parametrize("option", ["protocol", "candidate", "parent", "stars-tsv", "regions", "star-layer", "output"])
def test_metric_recovery_cli_forbids_manual_overrides(option):
    import deep_sky_siril_metrics as metrics
    with pytest.raises(SystemExit) as error:
        metrics.main(["--session", "/unused", "--run-id", "010-input", "--" + option, "value"])
    assert error.value.code == 2


@pytest.mark.parametrize("target", ["candidate", "stars"])
def test_metric_recovery_rejects_scientific_candidate_and_star_table_drift(tmp_path, monkeypatch, target):
    from .test_psf_provenance import deconvolution_session, bridge, FakeNativeSiril, native_model
    session, source, reports, document, _, kwargs = deconvolution_session(tmp_path, monkeypatch)
    candidate = Path(kwargs['primary_output_value'])
    def fake_run(command, **options):
        from .test_native_processing import write_fits
        import numpy as np
        write_fits(candidate, np.zeros((8, 12)))
        for name in ('055-parent', '055-candidate'):
            _write_jpeg(session / 'previews' / (name + '.jpg'), size=(12, 8))
        receipt = bridge.capture(FakeNativeSiril(document, [native_model()]), document)
        receipt.update(contract_sha256=core.sha256_file(reports/'contract.json'), sirilpy_version=bridge.VERSION)
        (reports/'bridge.json').write_text(json.dumps(receipt))
        bridge.export_models(reports/'after.tsv', bridge.serialize_stars([native_model(fwhmx=3., fwhmy=2.)]), document['detection'])
        return subprocess.CompletedProcess(command, 0, 'log: Running command: makepsf\n')
    monkeypatch.setattr(core.subprocess, 'run', fake_run)
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **kw:
        dict(passed=True, decoder={'artifact_fingerprint': core.fingerprint(path, include_path=False)}))
    receipt = core.run_script(str(session), **kwargs)
    assert receipt['status'] == 'success'
    result = core.recover_run_metrics(str(session), receipt['id'])
    recovered_path = session / result['report']['path']
    recovered = core.load_json(recovered_path)
    assert recovered['recovery_binding']['inputs']['candidate'] == core.fingerprint(candidate)
    before = recovered_path.read_bytes()
    changed = candidate if target == 'candidate' else reports/'after.tsv'
    changed.write_bytes(changed.read_bytes() + b' ')
    monkeypatch.setattr(core.subprocess, 'run', lambda *a, **kw: pytest.fail('Recovery executed Siril'))
    with pytest.raises(core.ContractError):
        core.recover_run_metrics(str(session), receipt['id'])
    assert recovered_path.read_bytes() == before


def test_metric_recovery_exclusive_publish_preserves_competing_evidence(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch)
    _input_inspect_run(session, source, monkeypatch)
    recovery = session/'reports/010-input/metrics-recovery.json'
    original_link = core.os.link
    def competing_writer(source_path, destination):
        Path(destination).write_text('competing evidence')
        return original_link(source_path, destination)
    monkeypatch.setattr(core.os, 'link', competing_writer)
    with pytest.raises(core.ContractError, match='already exists'):
        core.recover_run_metrics(str(session), '010-input')
    assert recovery.read_text() == 'competing evidence'
    assert not list(recovery.parent.glob('.metrics-recovery.json.*'))


def test_failed_execution_is_not_downgraded_to_a_reporting_warning(tmp_path, monkeypatch, capsys):
    import deep_sky_siril_metrics as metrics
    session, source, _ = _session(tmp_path, monkeypatch)
    args = _metric_inspect_cli(session, source, monkeypatch)
    monkeypatch.setattr(core.subprocess, 'run', lambda command, **kw: subprocess.CompletedProcess(command, 1, 'Error: failed'))
    monkeypatch.setattr(metrics, 'generate_metric_report', lambda **kw: pytest.fail('Measured a failed execution'))
    assert cli.main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'failed' and 'report_warnings' not in result
    assert metrics.main(['--session', str(session), '--run-id', '010-input']) == 2
    assert json.loads(capsys.readouterr().err)['status'] == 'failed'
    assert not (session/'reports/010-input/metrics-recovery.json').exists()


@pytest.mark.parametrize('field', ['claim', 'compatibility_evidence', 'method', 'missing', 'protection_regions', 'channel_gains', 'color_protection'])
def test_pcc_replay_rejects_claim_or_compatibility_drift_even_if_manifest_is_rehashed(tmp_path, monkeypatch, field):
    session, source, _ = _session(tmp_path, monkeypatch, input_state='linear', color_ready=True)
    script, candidate = _color_calibration_script(session, source, run_id='040-color', catalogue='gaia')
    def fake_run(command, **kwargs):
        _write_fits(candidate, color_ready=True, nonzero=True)
        return subprocess.CompletedProcess(command, 0, _COLOR_FITS_LOG)
    monkeypatch.setattr(core.subprocess, 'run', fake_run)
    result = core.run_script(session, protocol='color.calibrate', script_value=script, source_value=source,
                             expected_values=[candidate], timeout=30)
    assert result['status'] == 'success'
    frozen = _session_bytes(session)
    assert core.run_script(session, protocol='color.calibrate', script_value=script, source_value=source,
                           expected_values=[candidate], timeout=30)['replayed'] is True
    assert _session_bytes(session) == frozen
    path = session / 'runs/040-color.json'
    receipt = core.load_json(path)
    if field == 'claim':
        receipt['color_calibration']['claim'] = 'display_color_balance'
    elif field == 'method':
        receipt['color_calibration']['method'] = 'preserve'
    elif field == 'missing':
        receipt.pop('color_calibration')
    elif field == 'protection_regions':
        receipt['color_calibration']['protection_regions']['sha256'] = '0'*64
    elif field == 'channel_gains':
        receipt['color_calibration']['channel_gains'] = [1, 1, 1]
    elif field == 'color_protection':
        receipt['processing_checks']['color_protection']['regions'][0]['rgb']['candidate'][0] += .01
    else:
        receipt['color_calibration'][field]['evidence'] = 'Different acquisition evidence'
    core.atomic_write_json(path, receipt)
    manifest = core.load_json(session / 'manifest.json')
    next(row for row in manifest['runs'] if row['id'] == '040-color')['receipt_sha256'] = core.sha256_file(path)
    core.atomic_write_json(session / 'manifest.json', manifest)
    with pytest.raises(core.ContractError):
        session_state._verified_success_receipts(session)


def test_v3_policy_default_display_pcc_replays_without_new_compatibility_requirements(tmp_path, monkeypatch):
    from deep_sky_siril_processing import pcc_evidence
    import deep_sky_siril_processing as processing
    previous = dict(next(p for p in session_state._EXECUTION_POLICY_HISTORY if p.get('guidance') == 'stage_primary_references_v3'))
    with monkeypatch.context() as old:
        old.setattr(session_state, 'EXECUTION_POLICY', previous)
        old.setattr(processing, 'pcc_evidence', lambda evidence, source, channel, **kwargs:
                    {'claim': 'display_color_balance', 'fallback_evidence': evidence['equipment']['pcc_fallback']})
        session, source, _ = _session(tmp_path, monkeypatch, input_state='linear',
                                     channel_mode='dualband-osc', color_ready=True)
        script, candidate = _color_calibration_script(session, source, run_id='040-color', catalogue='gaia')
        text = script.read_text()
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if line.startswith('# color-calibration:'):
                evidence = {'method': 'pcc', 'source_sha256': core.sha256_file(source),
                            'fallback_reason': 'Historical narrowband SPCC fallback',
                            'equipment': {'pcc_fallback': {'source': 'policy_default',
                                                           'evidence': 'Historical display fallback policy'}}}
                lines[index] = '# color-calibration: ' + json.dumps(evidence)
        script.write_text('\n'.join(lines) + '\n')
        _write_ssf_provenance(session, script, 'color.calibrate')
        def fake_run(command, **kwargs):
            _write_fits(candidate, color_ready=True, nonzero=True)
            return subprocess.CompletedProcess(command, 0, _COLOR_FITS_LOG)
        monkeypatch.setattr(core.subprocess, 'run', fake_run)
        receipt = core.run_script(session, protocol='color.calibrate', script_value=script, source_value=source,
                                  expected_values=[candidate], timeout=30)
        assert receipt['status'] == 'success'
        assert receipt['color_calibration']['fallback_evidence']['source'] == 'policy_default'
    assert processing.pcc_evidence is pcc_evidence
    frozen = _session_bytes(session)
    replay = core.run_script(session, protocol='color.calibrate', script_value=script, source_value=source,
                             expected_values=[candidate], timeout=30)
    assert replay['replayed'] is True and replay['color_calibration'] == receipt['color_calibration']
    assert _session_bytes(session) == frozen
    with pytest.raises(core.ContractError, match='initialize a new session'):
        core.run_script(session, protocol='color.calibrate', script_value=script, source_value=source,
                        expected_values=[candidate], timeout=30, validate_only=True)
    assert _session_bytes(session) == frozen
