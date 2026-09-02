from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import deep_sky_siril_artifacts as artifacts  # noqa: E402
import deep_sky_siril_tooling as tooling  # noqa: E402


def _stat_line(channel: str, *, mean: float, median: float, maximum: float) -> str:
    return (
        f"log: {channel} layer: Mean: {mean}, Median: {median}, Sigma: 5, "
        f"Min: 0, Max: {maximum}, bgnoise: 4, avgDev: 3, MAD: 2, "
        "sqrt(BWMV): 1\n"
    )


def test_statistics_samples_bind_mixed_fits_and_jpeg_domains() -> None:
    output = (
        "log: Reading FITS: file master.fit, 3 layer(s), 2160x3840 pixels, 32 bits\n"
        + _stat_line("Red", mean=100, median=90, maximum=65535)
        + _stat_line("Green", mean=110, median=95, maximum=60000)
        + _stat_line("Blue", mean=120, median=105, maximum=62000)
        + "log: Reading JPG: file preview.jpg, 3 layer(s), 2160x3840 pixels\n"
        + _stat_line("Red", mean=18.6, median=3, maximum=255)
        + _stat_line("Green", mean=94.5, median=92, maximum=255)
        + _stat_line("Blue", mean=109.3, median=107, maximum=255)
    )

    samples = artifacts.parse_statistics_samples(output)

    assert len(samples) == 2
    fits, jpeg = samples
    assert fits["source_format"] == "FITS"
    assert fits["native_denominator"] == 65535.0
    assert fits["channels"]["red"]["adu_16_equivalent"]["maximum"] == 65535.0
    assert fits["channels"]["red"]["normalized"]["maximum"] == 1.0

    assert jpeg["source_format"] == "JPEG"
    assert jpeg["native_denominator"] == 255.0
    assert jpeg["normalization_denominator"] == 65535.0
    assert jpeg["channels"]["red"]["adu_16_equivalent"]["mean"] == pytest.approx(
        18.6 * 257.0
    )
    assert jpeg["channels"]["red"]["adu_16_equivalent"]["maximum"] == 65535.0
    assert jpeg["channels"]["red"]["normalized"]["median"] == pytest.approx(
        3.0 / 255.0
    )
    assert jpeg["channels"]["blue"]["normalized"]["maximum"] == 1.0


def _jpeg_reopen_log() -> str:
    return (
        "started_at=2026-08-30T00:00:00+00:00\n"
        "Error: Unable to read EXIF metadata\n"
        "log: Reading JPG: file final.jpg, 3 layer(s), 4x3 pixels\n"
        + _stat_line("Red", mean=18, median=16, maximum=255)
        + _stat_line("Green", mean=20, median=18, maximum=255)
        + _stat_line("Blue", mean=22, median=20, maximum=255)
    )


def test_log_diagnostics_accepts_context_bound_jpeg_exif_warning() -> None:
    diagnostics = artifacts.diagnose_siril_log(
        _jpeg_reopen_log(),
        exit_code=0,
        timed_out=False,
        execution_valid=True,
        validated_display_names=["final.jpg"],
    )

    assert diagnostics == {
        "policy": "fail_closed_v1",
        "status": "warning",
        "warning_count": 1,
        "fatal_count": 0,
        "findings": [
            {
                "code": "jpeg_exif_unavailable",
                "severity": "warning",
                "line_number": 2,
                "text": "Error: Unable to read EXIF metadata",
            }
        ],
    }


@pytest.mark.parametrize(
    ("log_text", "display_names"),
    [
        ("Error: Unable to read EXIF metadata\n", ["final.jpg"]),
        (_jpeg_reopen_log(), []),
        (_jpeg_reopen_log().replace("Blue layer", "log: ignored"), ["final.jpg"]),
    ],
    ids=["missing-reopen", "decoder-unbound", "statistics-incomplete"],
)
def test_log_diagnostics_rejects_unproven_exif_errors(
    log_text: str,
    display_names: list[str],
) -> None:
    diagnostics = artifacts.diagnose_siril_log(
        log_text,
        exit_code=0,
        timed_out=False,
        execution_valid=True,
        validated_display_names=display_names,
    )

    assert diagnostics["status"] == "failed"
    assert diagnostics["fatal_count"] == 1
    assert diagnostics["findings"][0]["code"] == "unclassified_siril_error"


def test_log_diagnostics_accepts_only_post_success_broken_pipe() -> None:
    log_text = (
        "log: Script execution finished successfully.\n"
        "closing pipes\n"
        "Exception ignored while flushing sys.stdout:\n"
        "BrokenPipeError: [Errno 32] Broken pipe\n"
    )
    diagnostics = artifacts.diagnose_siril_log(
        log_text,
        exit_code=0,
        timed_out=False,
        execution_valid=True,
    )

    assert diagnostics["status"] == "warning"
    assert diagnostics["warning_count"] == 1
    assert diagnostics["findings"][0]["code"] == "post_success_pipe_flush"

    rejected = artifacts.diagnose_siril_log(
        log_text.replace("log: Script execution finished successfully.\n", ""),
        exit_code=0,
        timed_out=False,
        execution_valid=True,
    )
    assert rejected["status"] == "failed"
    assert rejected["fatal_count"] == 2


@pytest.mark.parametrize(
    "error_line",
    [
        "error: no suitable data in src fits",
        "Traceback (most recent call last):",
        "RuntimeError: module failed",
    ],
)
def test_log_diagnostics_fails_closed_on_unknown_runtime_errors(
    error_line: str,
) -> None:
    diagnostics = artifacts.diagnose_siril_log(
        f"log: Running command: denoise\n{error_line}\n"
        "log: Script execution finished successfully.\n",
        exit_code=0,
        timed_out=False,
        execution_valid=True,
    )

    assert diagnostics["status"] == "failed"
    assert diagnostics["fatal_count"] == 1
    assert diagnostics["findings"][0]["code"] == "unclassified_siril_error"


def test_log_diagnostics_clean_log_has_no_findings() -> None:
    diagnostics = artifacts.diagnose_siril_log(
        "log: Script execution finished successfully.\n",
        exit_code=0,
        timed_out=False,
        execution_valid=True,
    )
    assert diagnostics["status"] == "clean"
    assert diagnostics["findings"] == []


def _installed_siril(tmp_path: Path) -> Path:
    executable = tmp_path / "siril-cli"
    executable.write_bytes(b"diagnostic executable")
    executable.chmod(0o755)
    return executable


def _isolate_optional_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tooling, "_configured_executable", lambda _name: None)
    monkeypatch.setattr(tooling.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tooling, "_discover_starnet_model", lambda _path: None)


def test_probe_reports_missing_siril_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tooling, "discover_siril", lambda: None)
    _isolate_optional_tools(monkeypatch)

    probe = tooling.probe_tools(offline=True)

    assert probe["blocking_reasons"] == ["siril_cli_missing"]
    assert probe["tools"]["siril_cli"]["path"] is None
    assert probe["tools"]["siril_cli"]["fingerprint"] is None


@pytest.mark.parametrize(
    ("result", "expected_reason"),
    [
        (
            tooling._ToolVersionResult(
                "1.5.0",
                "siril 1.5.0",
                status="ok",
            ),
            "siril_cli_incompatible",
        ),
        (
            tooling._ToolVersionResult(
                None,
                "Siril development build",
                status="unparseable",
            ),
            "siril_cli_version_unparseable",
        ),
        (
            tooling._ToolVersionResult(
                None,
                "PermissionError: denied",
                status="execution_failed",
            ),
            "siril_cli_probe_execution_failed",
        ),
        (
            tooling._ToolVersionResult(
                None,
                "TimeoutExpired: timed out",
                status="timeout",
            ),
            "siril_cli_probe_timeout",
        ),
    ],
)
def test_probe_reports_distinct_siril_failures_and_keeps_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: tooling._ToolVersionResult,
    expected_reason: str,
) -> None:
    executable = _installed_siril(tmp_path)
    monkeypatch.setattr(tooling, "discover_siril", lambda: executable)
    monkeypatch.setattr(tooling, "_tool_version", lambda _path: result)
    _isolate_optional_tools(monkeypatch)

    probe = tooling.probe_tools(offline=True)
    siril = probe["tools"]["siril_cli"]

    assert probe["blocking_reasons"] == [expected_reason]
    assert siril["compatible"] is False
    assert siril["path"] == str(executable)
    assert siril["fingerprint"] == {
        "path": str(executable),
        "role": "siril_cli",
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "size": executable.stat().st_size,
    }


def test_probe_classifies_unusable_temp_environment_without_losing_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _installed_siril(tmp_path)
    monkeypatch.setattr(tooling, "discover_siril", lambda: executable)
    _isolate_optional_tools(monkeypatch)

    def unavailable_temp(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("No usable temporary directory found")

    monkeypatch.setattr(tooling.tempfile, "TemporaryDirectory", unavailable_temp)

    version_result = tooling._tool_version(executable)
    assert version_result == (None, "FileNotFoundError: No usable temporary directory found")
    assert version_result.status == "environment_unavailable"

    probe = tooling.probe_tools(offline=True)
    siril = probe["tools"]["siril_cli"]
    assert probe["blocking_reasons"] == [
        "siril_cli_probe_environment_unavailable"
    ]
    assert siril["path"] == str(executable)
    assert siril["fingerprint"]["sha256"] == hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("unparseable", "unparseable"),
        ("execution_failed", "execution_failed"),
        ("timeout", "timeout"),
    ],
)
def test_tool_version_preserves_two_item_interface_and_classifies_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_status: str,
) -> None:
    executable = _installed_siril(tmp_path)

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if mode == "execution_failed":
            return subprocess.CompletedProcess(command, 7, "cannot execute")
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(command, 0, "Siril development build")

    monkeypatch.setattr(tooling.subprocess, "run", fake_run)

    result = tooling._tool_version(executable)
    version, output = result
    assert version is None
    assert isinstance(output, str)
    assert result.status == expected_status
