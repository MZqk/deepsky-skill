from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys

import pytest


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


@pytest.mark.parametrize("policy", ["fail_closed_v1", "fail_closed_v2", "fail_closed_v3"])
def test_log_diagnostics_accepts_context_bound_jpeg_exif_warning(policy: str) -> None:
    diagnostics = artifacts.diagnose_siril_log(
        _jpeg_reopen_log(),
        exit_code=0,
        timed_out=False,
        execution_valid=True,
        validated_display_names=["final.jpg"],
        policy=policy,
    )

    # v3 re-labels this context-proven noise as benign; v1 and v2 keep it a warning.
    benign = policy == "fail_closed_v3"
    assert diagnostics == {
        "policy": policy,
        "status": "clean" if benign else "warning",
        "warning_count": 0 if benign else 1,
        "fatal_count": 0,
        **({"benign_count": 1} if benign else {}),
        "findings": [
            {
                "code": "jpeg_exif_unavailable",
                "severity": "benign" if benign else "warning",
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
@pytest.mark.parametrize("policy", ["fail_closed_v1", "fail_closed_v2", "fail_closed_v3"])
def test_log_diagnostics_rejects_unproven_exif_errors(
    log_text: str,
    display_names: list[str],
    policy: str,
) -> None:
    diagnostics = artifacts.diagnose_siril_log(
        log_text,
        exit_code=0,
        timed_out=False,
        execution_valid=True,
        validated_display_names=display_names,
        policy=policy,
    )

    assert diagnostics["status"] == "failed"
    assert diagnostics["fatal_count"] == 1
    assert diagnostics["findings"][0]["code"] == "unclassified_siril_error"


@pytest.mark.parametrize("policy", ["fail_closed_v1", "fail_closed_v2", "fail_closed_v3"])
def test_log_diagnostics_accepts_only_post_success_broken_pipe(policy: str) -> None:
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
        policy=policy,
    )

    benign = policy == "fail_closed_v3"
    assert diagnostics["status"] == ("clean" if benign else "warning")
    assert diagnostics["warning_count"] == (0 if benign else 1)
    assert diagnostics["findings"][0]["code"] == "post_success_pipe_flush"
    assert diagnostics["findings"][0]["severity"] == ("benign" if benign else "warning")

    rejected = artifacts.diagnose_siril_log(
        log_text.replace("log: Script execution finished successfully.\n", ""),
        exit_code=0,
        timed_out=False,
        execution_valid=True,
        policy=policy,
    )
    assert rejected["status"] == "failed"
    assert rejected["fatal_count"] == 2


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("Error: catalogue unavailable", "unclassified_siril_error"),
        ("log: Error: catalogue unavailable", "unclassified_siril_error"),
        ("LOG: RuntimeError: catalogue unavailable", "unclassified_siril_error"),
        ("log: Traceback (most recent call last):", "unclassified_siril_error"),
        ("log: Exception: catalogue unavailable", "unclassified_siril_error"),
        ("Script execution failed.", "siril_script_failed"),
        ("log: Script execution failed.", "siril_script_failed"),
        ("progress: Script execution failed., 100.00%", "siril_script_failed"),
        ("log: Server unreachable or unresponsive (HTTP code 503 - for details see server)", "catalogue_http_error"),
        ("Server unreachable or unresponsive (HTTP code 404 - for details see server)", "catalogue_http_error"),
    ],
)
def test_v2_diagnostics_preserve_fatal_line_evidence(line: str, code: str) -> None:
    diagnostics = artifacts.diagnose_siril_log(
        f"started_at=2026-10-04\n  {line}  \n",
        exit_code=0,
        timed_out=False,
        execution_valid=True,
        policy="fail_closed_v2",
    )
    assert diagnostics == {
        "policy": "fail_closed_v2",
        "status": "failed",
        "warning_count": 0,
        "fatal_count": 1,
        "findings": [{"code": code, "severity": "fatal", "line_number": 2, "text": line}],
    }


def test_v1_classification_remains_frozen_for_legacy_receipts() -> None:
    diagnostics = artifacts.diagnose_siril_log(
        "log: Error: catalogue unavailable\n"
        "log: Server unreachable or unresponsive (HTTP code 503 - for details see server)\n"
        "log: Script execution failed.\n"
        "progress: Script execution failed., 100.00%\n",
        exit_code=1,
        timed_out=False,
        execution_valid=False,
        policy="fail_closed_v1",
    )
    assert diagnostics == {
        "policy": "fail_closed_v1", "status": "clean",
        "warning_count": 0, "fatal_count": 0, "findings": [],
    }


def test_v2_diagnostics_do_not_match_incidental_error_text() -> None:
    diagnostics = artifacts.diagnose_siril_log(
        "log: Catalog error threshold: 0.05\n"
        "log: No error: fit completed\n"
        "log: Previous Script execution failed. recovered\n"
        "log: Server unreachable or unresponsive (HTTP code 200 - diagnostic example)\n"
        "log: Reading FITS: file Error-master.fit, 1 layer(s), 4x3 pixels\n",
        exit_code=0, timed_out=False, execution_valid=True,
    )
    assert diagnostics["status"] == "clean"
    assert diagnostics["findings"] == []


@pytest.mark.parametrize("prefix", ["", "log: "])
@pytest.mark.parametrize("invalid_context", [None, "exit", "timeout", "outputs", "success", "denoise", "nlbayes"])
def test_v2_denoise_warning_requires_complete_context(prefix, invalid_context) -> None:
    log = (
        "log: Running command: denoise\n"
        f"{prefix}error: no suitable data in src fits\n"
        "log: NL-Bayes denoise\n"
        "log: Script execution finished successfully.\n"
    )
    for field, marker in [
        ("success", "log: Script execution finished successfully.\n"),
        ("denoise", "log: Running command: denoise\n"),
        ("nlbayes", "log: NL-Bayes denoise\n"),
    ]:
        if invalid_context == field:
            log = log.replace(marker, "")
    diagnostics = artifacts.diagnose_siril_log(
        log, exit_code=1 if invalid_context == "exit" else 0,
        timed_out=invalid_context == "timeout", execution_valid=invalid_context != "outputs",
        policy="fail_closed_v2",
    )
    assert diagnostics["status"] == ("warning" if invalid_context is None else "failed")
    assert diagnostics["findings"][0]["code"] == (
        "denoise_copyfits_nonfatal" if invalid_context is None else "unclassified_siril_error"
    )


@pytest.mark.parametrize("prefix", ["", "log: "])
@pytest.mark.parametrize("execution_valid", [False, True])
def test_v2_prefixed_exif_requires_valid_decoder(prefix, execution_valid) -> None:
    log = _jpeg_reopen_log().replace("Error: Unable", prefix + "Error: Unable")
    diagnostics = artifacts.diagnose_siril_log(
        log, exit_code=0, timed_out=False, execution_valid=execution_valid,
        validated_display_names=["final.jpg"], policy="fail_closed_v2",
    )
    assert diagnostics["status"] == ("warning" if execution_valid else "failed")


@pytest.mark.parametrize("invalid_context", [None, "exit", "timeout", "outputs", "success"])
def test_v2_prefixed_pipe_warning_requires_verified_success(invalid_context) -> None:
    log = (
        "log: Script execution finished successfully.\n"
        "log: Exception ignored while flushing sys.stdout:\n"
        "log: BrokenPipeError: [Errno 32] Broken pipe\n"
    )
    if invalid_context == "success":
        log = log.replace("log: Script execution finished successfully.\n", "")
    diagnostics = artifacts.diagnose_siril_log(
        log, exit_code=1 if invalid_context == "exit" else 0,
        timed_out=invalid_context == "timeout", execution_valid=invalid_context != "outputs",
        policy="fail_closed_v2",
    )
    assert diagnostics["status"] == ("warning" if invalid_context is None else "failed")
    assert diagnostics["fatal_count"] == (0 if invalid_context is None else 2)


def test_v2_pcc_warning_does_not_hide_runtime_failure() -> None:
    diagnostics = artifacts.diagnose_siril_log(
        "log: The photometric color calibration seems to have found an imprecise solution, consider correcting the image gradient first\n"
        "log: Error: catalogue unavailable\n",
        exit_code=0, timed_out=False, execution_valid=True,
    )
    assert diagnostics["status"] == "failed"
    assert diagnostics["warning_count"] == diagnostics["fatal_count"] == 1
    assert diagnostics["findings"][0]["code"] == "pcc_imprecise_solution"


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


BROKEN_PIPE_LOG = (
    "log: Script execution finished successfully.\n"
    "Exception ignored while flushing sys.stdout:\n"
    "BrokenPipeError: [Errno 32] Broken pipe\n"
)
CATALOGUE_FAILURE_LOG = (
    "log: Server unreachable or unresponsive (HTTP code 400 - for details see https://x)\n"
    "log: Error: unable to retrieve the remote catalogue from the server\n"
    "progress: Script execution failed., 100.00%\n"
)
IMPRECISE_PHOTOMETRY_LOG = (
    "log: The photometric color calibration seems to have found an imprecise "
    "solution, consider correcting the image gradient first.\n"
    "log: Script execution finished successfully.\n"
)


def test_benign_reclassification_keeps_evidence_without_setting_status() -> None:
    # v3 re-labels only context-proven runtime noise: the run reads clean, but the exact
    # line, number and text stay in the receipt.
    v2 = artifacts.diagnose_siril_log(BROKEN_PIPE_LOG, exit_code=0, timed_out=False,
                                       execution_valid=True, policy="fail_closed_v2")
    v3 = artifacts.diagnose_siril_log(BROKEN_PIPE_LOG, exit_code=0, timed_out=False,
                                       execution_valid=True, policy="fail_closed_v3")
    assert (v2["status"], v2["warning_count"]) == ("warning", 1)
    assert (v3["status"], v3["warning_count"]) == ("clean", 0)
    assert v3["benign_count"] == 1
    assert v3["findings"][0]["code"] == "post_success_pipe_flush"
    assert v3["findings"][0]["severity"] == "benign"
    assert v3["findings"][0]["text"] == v2["findings"][0]["text"]
    assert v3["findings"][0]["line_number"] == v2["findings"][0]["line_number"]


@pytest.mark.parametrize("policy", ["fail_closed_v2", "fail_closed_v3"])
def test_benign_reclassification_never_hides_a_fatal_finding(policy: str) -> None:
    # A catalogue failure and a script failure must stay fatal under every policy, so a
    # genuine service outage is never read as routine runtime noise.
    report = artifacts.diagnose_siril_log(CATALOGUE_FAILURE_LOG, exit_code=1, timed_out=False,
                                          execution_valid=False, policy=policy)
    assert report["status"] == "failed"
    assert report["fatal_count"] >= 1
    assert not report.get("benign_count")
    codes = {item["code"] for item in report["findings"]}
    assert "catalogue_http_error" in codes
    assert "siril_script_failed" in codes


def test_imprecise_photometry_stays_a_real_warning_under_v3() -> None:
    # An imprecise photometric fit is a degraded measurement, not runtime noise: it must
    # still raise the run-wide status under the benign re-classification.
    report = artifacts.diagnose_siril_log(IMPRECISE_PHOTOMETRY_LOG, exit_code=0, timed_out=False,
                                          execution_valid=True, policy="fail_closed_v3")
    assert report["status"] == "warning"
    assert report["warning_count"] == 1
    assert report["findings"][0]["code"] == "pcc_imprecise_solution"
    assert report["findings"][0]["severity"] == "warning"


def test_legacy_diagnostics_normalize_for_replay_comparison() -> None:
    # A v2 receipt records the same evidence as `warning`; replay must still accept it
    # under v3, while a fatal receipt never normalizes into a passing one.
    warning_record = {
        "policy": "fail_closed_v2", "status": "warning", "warning_count": 1, "fatal_count": 0,
        "findings": [{"code": "denoise_copyfits_nonfatal", "severity": "warning",
                      "line_number": 22, "text": "error: no suitable data in src fits"}],
    }
    fatal_record = {
        "policy": "fail_closed_v2", "status": "failed", "warning_count": 0, "fatal_count": 1,
        "findings": [{"code": "catalogue_http_error", "severity": "fatal",
                      "line_number": 34, "text": "Server unreachable"}],
    }
    v3 = {
        "policy": "fail_closed_v3", "status": "clean", "warning_count": 0, "fatal_count": 0,
        "benign_count": 1,
        "findings": [{"code": "denoise_copyfits_nonfatal", "severity": "benign",
                      "line_number": 22, "text": "error: no suitable data in src fits"}],
    }
    normalize = artifacts.normalize_log_diagnostics
    assert normalize(warning_record) == normalize(v3)
    normalized_fatal = normalize(fatal_record)
    assert normalized_fatal["status"] == "failed"
    assert normalized_fatal["findings"][0]["severity"] == "fatal"
    assert normalize(fatal_record) != normalize(v3)
    assert normalize(None) is None
    # v1 keeps a distinct identity; it must never be treated as a v2/v3 receipt.
    assert normalize(dict(warning_record, policy="fail_closed_v1")) != normalize(warning_record)
