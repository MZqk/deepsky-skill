#!/usr/bin/env python3
"""Local tool discovery and subprocess isolation for standalone contract 1."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

import deep_sky_siril_artifacts as artifacts
from deep_sky_siril_contract import (
    CONTRACT_VERSION,
    SCHEMA_PREFIX,
    SIRIL_MINIMUM,
    SIRIL_OBSOLETE,
    ContractError,
    fingerprint,
    fingerprint_matches,
    fingerprint_resource,
    utc_now,
)

VERSION_PATTERN = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
# Child processes receive only the platform state needed to locate executables,
# user-local configuration and temporary storage.  In particular, credentials,
# proxy URLs, loader injection variables and arbitrary Python configuration are
# never copied from the parent environment.
_CHILD_INHERITED_ENV_KEYS = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "XDG_STATE_HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TZ",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMDRIVE",
)
_CHILD_FIXED_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "LC_MESSAGES": "C",
    "PIP_NO_INDEX": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
    "PYTHONHOME": "",
    "__PYVENV_LAUNCHER__": "",
}
_CHILD_ENV_PURPOSES = frozenset({"tool_version", "siril_runtime", "python_cli"})


class _ToolVersionResult(tuple[str | None, str]):
    """Two-item legacy result carrying a private probe classification."""

    status: str

    def __new__(
        cls,
        version: str | None,
        output: str,
        *,
        status: str,
    ) -> "_ToolVersionResult":
        instance = super().__new__(cls, (version, output))
        instance.status = status
        return instance


def _version_tuple(value: Any) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.search(str(value or ""))
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _subprocess_environment(purpose: str) -> dict[str, str]:
    """Build a credential-free, purpose-specific child-process environment."""
    if purpose not in _CHILD_ENV_PURPOSES:
        raise ValueError(f"Unsupported child environment purpose: {purpose}")
    environment = {
        key: value
        for key in _CHILD_INHERITED_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }
    environment.update(_CHILD_FIXED_ENV)
    return environment


def _configured_file(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve(strict=False)
    return path if path.is_file() and not path.is_symlink() else None


def _configured_executable(name: str) -> Path | None:
    path = _configured_file(name)
    return path if path is not None and os.access(path, os.X_OK) else None


def _configured_resource(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve(strict=False)
    return path if (path.is_file() or path.is_dir()) and not path.is_symlink() else None


def _discover_starnet_model(executable: Path | None) -> Path | None:
    configured = _configured_resource("DEEP_SKY_SIRIL_STARNET_MODEL")
    if configured is not None:
        return configured
    candidates: list[Path] = []
    if executable is not None:
        candidates.extend(
            [
                executable.parent / "StarNet2_weights.mlpackage",
                executable.parent / "StarNet2_weights.onnx",
                executable.parent / "rgb_starnet_weights.pb",
                executable.parent.parent / "lib" / "starnet2" / "StarNet2_weights.mlpackage",
                executable.parent.parent / "lib" / "starnet2" / "StarNet2_weights.onnx",
                executable.parent / "lib" / "starnet2" / "StarNet2_weights.mlpackage",
                executable.parent / "lib" / "starnet2" / "StarNet2_weights.onnx",
            ]
        )
    candidates.extend(
        [
            Path("/usr/local/lib/starnet2/StarNet2_weights.mlpackage"),
            Path("/usr/local/lib/starnet2/StarNet2_weights.onnx"),
            Path("/opt/homebrew/lib/starnet2/StarNet2_weights.mlpackage"),
            Path("/opt/homebrew/lib/starnet2/StarNet2_weights.onnx"),
            Path("/usr/lib/starnet2/StarNet2_weights.mlpackage"),
            Path("/usr/lib/starnet2/StarNet2_weights.onnx"),
        ]
    )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        if (resolved.is_file() or resolved.is_dir()) and not resolved.is_symlink():
            return resolved
    return None


def discover_siril() -> Path | None:
    configured = _configured_executable("DEEP_SKY_SIRIL_BIN")
    if configured:
        return configured
    for name in ("siril-cli", "siril_cli"):
        found = shutil.which(name)
        if found:
            path = Path(found).resolve()
            if path.is_file() and not path.is_symlink():
                return path
    for value in (
        "/Applications/Siril.app/Contents/MacOS/siril-cli",
        "/usr/local/bin/siril-cli",
        "/opt/homebrew/bin/siril-cli",
        "/usr/bin/siril-cli",
    ):
        path = Path(value)
        if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def _tool_version(path: Path) -> tuple[str | None, str]:
    try:
        with tempfile.TemporaryDirectory(prefix="starun-siril-probe-") as raw:
            isolated = Path(raw).resolve()
            directories = {
                "HOME": isolated / "home",
                "XDG_CACHE_HOME": isolated / "cache",
                "XDG_CONFIG_HOME": isolated / "config",
                "XDG_DATA_HOME": isolated / "data",
                "XDG_STATE_HOME": isolated / "state",
                "TMPDIR": isolated / "tmp",
                "TEMP": isolated / "tmp",
                "TMP": isolated / "tmp",
            }
            try:
                for directory in set(directories.values()):
                    directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return _ToolVersionResult(
                    None,
                    f"{type(exc).__name__}: {exc}",
                    status="environment_unavailable",
                )
            environment = _subprocess_environment("tool_version")
            environment.pop("PYTHONHOME", None)
            environment.update(
                {name: str(directory) for name, directory in directories.items()}
            )
            try:
                completed = subprocess.run(
                    [str(path), "--version"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    timeout=15,
                    check=False,
                    cwd=isolated,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                return _ToolVersionResult(
                    None,
                    f"{type(exc).__name__}: {exc}",
                    status="timeout",
                )
            except OSError as exc:
                return _ToolVersionResult(
                    None,
                    f"{type(exc).__name__}: {exc}",
                    status="execution_failed",
                )
    except OSError as exc:
        return _ToolVersionResult(
            None,
            f"{type(exc).__name__}: {exc}",
            status="environment_unavailable",
        )
    if completed.returncode != 0:
        return _ToolVersionResult(
            None,
            completed.stdout,
            status="execution_failed",
        )
    match = VERSION_PATTERN.search(completed.stdout)
    if match is None:
        return _ToolVersionResult(
            None,
            completed.stdout,
            status="unparseable",
        )
    return _ToolVersionResult(
        match.group(0),
        completed.stdout,
        status="ok",
    )


def _probe_sirilpy_bridge() -> dict[str, Any]:
    return {
        "status": "runtime_check_required",
        "required_version": "1.0.25",
    }


def probe_tools(*, offline: bool = False) -> dict[str, Any]:
    siril_path = discover_siril()
    version_result = _tool_version(siril_path) if siril_path else (None, "")
    version, version_output = version_result
    version_status = getattr(
        version_result,
        "status",
        "ok" if version is not None else "unparseable",
    )
    parsed = _version_tuple(version)
    siril_compatible = bool(parsed and SIRIL_MINIMUM <= parsed < SIRIL_OBSOLETE)
    siril: dict[str, Any] = {
        "path": str(siril_path) if siril_path else None,
        "version": version,
        "compatible": siril_compatible,
        "required_range": ">=1.4.4,<1.5",
        "probe_excerpt": " ".join(version_output.split())[:512],
        "fingerprint": fingerprint(siril_path, role="siril_cli") if siril_path else None,
    }

    starnet = _configured_executable("DEEP_SKY_SIRIL_STARNET2_BIN")
    if starnet is None:
        for name in ("starnet2", "starnet++"):
            found = shutil.which(name)
            if found:
                candidate = Path(found).resolve()
                if candidate.is_file() and not candidate.is_symlink():
                    starnet = candidate
                    break
    star_model = _discover_starnet_model(starnet)
    star_ready = bool(starnet and star_model)
    starnet_record = {
        "compatible": star_ready,
        "executable": fingerprint(starnet, role="starnet2") if starnet else None,
        "model": (
            fingerprint_resource(
                star_model,
                kind="directory" if star_model.is_dir() else "file",
            )
            if star_model
            else None
        ),
        "reasons": [
            reason
            for missing, reason in (
                (not starnet, "starnet2_unavailable"),
                (not star_model, "starnet_model_unavailable"),
            )
            if missing
        ],
    }

    gaia_raw = os.environ.get("DEEP_SKY_SIRIL_GAIA_DIR", "").strip()
    gaia = Path(gaia_raw).expanduser().resolve(strict=False) if gaia_raw else None
    gaia_ready = bool(gaia and gaia.is_dir() and not gaia.is_symlink())
    sirilpy_bridge = _probe_sirilpy_bridge()
    blocking_reasons = []
    if siril_path is None:
        blocking_reasons.append("siril_cli_missing")
    elif version_status == "environment_unavailable":
        blocking_reasons.append("siril_cli_probe_environment_unavailable")
    elif version_status == "timeout":
        blocking_reasons.append("siril_cli_probe_timeout")
    elif version_status == "execution_failed":
        blocking_reasons.append("siril_cli_probe_execution_failed")
    elif version_status == "unparseable" or parsed is None:
        blocking_reasons.append("siril_cli_version_unparseable")
    elif not siril_compatible:
        blocking_reasons.append("siril_cli_incompatible")
    warnings = [] if star_ready else ["starnet_unavailable_preserve_stars_baseline"]
    return {
        "schema": f"{SCHEMA_PREFIX}.tool-probe.v4",
        "created_at": utc_now(),
        "helper_contract_version": CONTRACT_VERSION,
        "policy": {
            "offline": bool(offline),
            "network_default": "offline",
            "online_exception": "explicit_remote_gaia_color_calibration",
            "automatic_download": False,
            "python_pixel_processing": False,
        },
        "tools": {
            "siril_cli": siril,
            "starnet": starnet_record,
            "local_gaia": {"compatible": gaia_ready, "path": str(gaia) if gaia_ready else None},
            "pillow": {"compatible": artifacts.Image is not None, "purpose": "decode_validation"},
            "sirilpy_bridge": sirilpy_bridge,
        },
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }


def verify_tool_fingerprint(record: Any, *, name: str) -> None:
    if not isinstance(record, dict) or not fingerprint_matches(record):
        raise ContractError("runtime_dependency_missing", f"Runtime tool changed or is missing: {name}")


def _require_protocol_runtime_dependency(
    protocol: str,
    probe: dict[str, Any],
) -> dict[str, Any] | None:
    if protocol != "background.subtract":
        return None
    bridge = probe.get("tools", {}).get("sirilpy_bridge", {})
    if (
        not isinstance(bridge, dict)
        or bridge.get("status") != "runtime_check_required"
        or bridge.get("required_version") != "1.0.25"
    ):
        raise ContractError(
            "session_hash_drift",
            "Frozen SirilPy runtime-check declaration is invalid",
        )
    return bridge


__all__ = [
    "_subprocess_environment",
    "_tool_version",
    "_probe_sirilpy_bridge",
    "_require_protocol_runtime_dependency",
    "discover_siril",
    "probe_tools",
    "verify_tool_fingerprint",
]
