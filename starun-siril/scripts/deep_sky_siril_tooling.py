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
from siril_manual_bundle import strict_json_bytes, BundleError

import deep_sky_siril_artifacts as artifacts
from deep_sky_siril_contract import (
    CONTRACT_VERSION,
    SCHEMA_PREFIX,
    SIRIL_MINIMUM,
    SIRIL_OBSOLETE,
    SKILL_ROOT,
    SCRIPTS_ROOT,
    load_json,
    ContractError,
    fingerprint,
    fingerprint_matches,
    fingerprint_resource,
    utc_now,
    stable_hash,
    sha256_file,
    resource_fingerprint_matches,
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


def probe_deepsnr() -> dict[str, Any]:
    """Inspect an installed DeepSNR without downloading models or running inference."""
    executable = _configured_executable('DEEP_SKY_SIRIL_DEEPSNR_BIN')
    if not os.environ.get('DEEP_SKY_SIRIL_DEEPSNR_BIN'):
        found = shutil.which('deepsnr')
        executable = Path(found).resolve() if found else None
    info, error = {}, None
    if executable:
        try:
            result = subprocess.run([str(executable), '--machine-info'], capture_output=True,
                                    text=True, timeout=15, check=True,
                                    env=_subprocess_environment('tool_version'))
            info = strict_json_bytes(result.stdout.encode(), document='DeepSNR machine info')
            if not isinstance(info, dict):
                info = {}
        except (OSError, ValueError, BundleError, subprocess.SubprocessError) as exc:
            error = str(exc)
    model = _configured_resource('DEEP_SKY_SIRIL_DEEPSNR_MODEL')
    if executable and not os.environ.get('DEEP_SKY_SIRIL_DEEPSNR_MODEL'):
        names = ['DeepSNR_weights_v2.mlpackage', 'DeepSNR_weights_v2.onnx']
        backend = info.get('backend', {})
        if isinstance(backend, dict) and backend.get('id'):
            names = [names[0] if backend['id'] == 'coreml' else names[1]]
        directories = [executable.parent, executable.parent/'lib/deepsnr',
                       executable.parent.parent/'lib/deepsnr', Path('/usr/local/lib/deepsnr'),
                       Path('/opt/homebrew/lib/deepsnr'), Path('/usr/lib/deepsnr')]
        model = next((p.resolve() for d in directories for n in names
                      if (p := d/n).exists() and not p.is_symlink()), None)
    adapter = SCRIPTS_ROOT/'siril_deepsnr.py'
    features, models = info.get('features'), info.get('models')
    linear = features.get('linear_mtf') if isinstance(features, dict) else None
    ready = (info.get('product') == 'deepsnr' and info.get('version') == '1.3.2'
             and isinstance(linear, dict) and linear.get('supported') is True and isinstance(models, list)
             and any(m.get('id') == 2 and set(m.get('color_spaces', [])) == {'rgb', 'grayscale'}
                     for m in models if isinstance(m, dict) and isinstance(m.get('color_spaces'), list)
                     and all(isinstance(c, str) for c in m['color_spaces'])))
    if model and not ((model.is_file() and model.suffix == '.onnx') or (model.is_dir() and model.suffix == '.mlpackage')):
        model = None
    try:
        model_record = fingerprint_resource(model, kind='directory' if model.is_dir() else 'file') if model else None
    except (OSError, ContractError) as exc:
        model_record, error = None, str(exc)
    reasons = [reason for missing, reason in (
        (not executable, 'deepsnr_unavailable'), (not ready, 'deepsnr_capabilities_incompatible'),
        (not model_record, 'deepsnr_model_unavailable'), (not adapter.is_file(), 'deepsnr_adapter_unavailable')) if missing]
    return {'compatible': not reasons, 'version': info.get('version'), 'supported_version': '1.3.2',
            'executable': fingerprint(executable) if executable else None, 'model': model_record,
            'adapter': fingerprint(adapter) if adapter.is_file() else None,
            'capabilities': info, 'capabilities_sha256': stable_hash(info), 'reasons': reasons,
            **({'error': error} if error else {})}


def deepsnr_runtime_unchanged(runtime: Any) -> bool:
    return bool(isinstance(runtime, dict) and runtime.get('compatible')
                and runtime.get('version') == '1.3.2'
                and runtime.get('capabilities_sha256') == stable_hash(runtime.get('capabilities'))
                and fingerprint_matches(runtime.get('executable'))
                and resource_fingerprint_matches(runtime.get('model'))
                and fingerprint_matches(runtime.get('adapter')))


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


def _probe_starnet_python(siril_path: Path) -> dict[str, Any]:
    """Resolve and import the Python environment actually selected by Siril."""
    try:
        with tempfile.TemporaryDirectory(prefix="starun-starnet-probe-") as raw:
            work = Path(raw).resolve()
            receipt = work / "python.json"
            script = work / "probe.ssf"
            config = work / "siril.ini"
            config.write_text("[core]\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nauto_update_scripts=false\n")
            script.write_text('requires 1.4.4 1.5.0\nset32bits\npyscript "' + str(SCRIPTS_ROOT / "siril_starnet.py") + '" --probe --receipt "' + str(receipt) + '"\nclose\n')
            completed = subprocess.run([str(siril_path), f"--initfile={config}", f"--directory={work}",
                                        "--offline", f"--script={script}"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       errors="replace", timeout=60, check=False, cwd=work,
                                       env=_subprocess_environment("siril_runtime"))
            if completed.returncode == 0 and receipt.is_file():
                result = load_json(receipt)
                if result.get("status") == "verified":
                    return result
            return {"status": "unavailable", "error": completed.stdout[-2000:]}
    except (OSError, subprocess.TimeoutExpired, ValueError, ContractError) as exc:
        return {"status": "unavailable", "error": str(exc)}


def starnet_runtime_unchanged(runtime: Any) -> bool:
    from deep_sky_siril_contract import resource_fingerprint_matches
    if not isinstance(runtime, dict):
        return False
    python = runtime.get("python", {})
    if runtime.get("adapter", {}).get("path") == str(SCRIPTS_ROOT / "siril_starnet_linear.py"):
        try:
            lock = load_json(Path(runtime["source_lock"]["path"]))
            if lock.get("legacy_adapter_sha256") != sha256_file(SCRIPTS_ROOT / "siril_starnet.py"):
                return False
        except (ContractError, KeyError, OSError):
            return False
    return bool(runtime.get("compatible") and fingerprint_matches(runtime.get("executable"))
                and resource_fingerprint_matches(runtime.get("model"))
                and fingerprint_matches(runtime.get("adapter"))
                and fingerprint_matches(runtime.get("source_lock"))
                and python.get("status") == "verified"
                and fingerprint_matches(python.get("interpreter"))
                and all(fingerprint_matches(r.get("module")) for r in python.get("dependencies", {}).values()))


SPCC_CATEGORIES = ('oscsensor', 'monosensor', 'redfilter', 'greenfilter',
                   'bluefilter', 'oscfilter', 'osclpf', 'whiteref')


def _probe_spcc_profiles(siril_path: Path) -> dict[str, Any]:
    """Read Siril's existing response names without syncing or downloading data."""
    try:
        with tempfile.TemporaryDirectory(prefix="starun-spcc-probe-") as raw:
            work = Path(raw)
            config, script = work / 'siril.ini', work / 'profiles.ssf'
            config.write_text('[core]\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nuse_spcc_repository=false\nauto_update_scripts=false\nauto_update_spcc=false\n')
            script.write_text('requires 1.4.4 1.5.0\nset32bits\n' +
                              ''.join('spcc_list ' + category + '\n' for category in SPCC_CATEGORIES) + 'close\n')
            result = subprocess.run([str(siril_path), f'--initfile={config}', f'--directory={work}',
                '--offline', f'--script={script}'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, errors='replace', timeout=30, check=False,
                cwd=work, env=_subprocess_environment('siril_runtime'))
            names = {category: [] for category in SPCC_CATEGORIES}
            index = -1
            current = None
            for line in result.stdout.splitlines():
                if line.startswith('log: Running command:'):
                    if line == 'log: Running command: spcc_list':
                        index += 1
                        current = SPCC_CATEGORIES[index] if index < len(SPCC_CATEGORIES) else None
                    else:
                        current = None
                elif current and line.startswith('log: '):
                    names[current].append(line[5:].strip())
            names = {category: entries[1:] for category, entries in names.items()}
            compatible = result.returncode == 0 and bool(names['whiteref']) and bool(names['monosensor'] or names['oscsensor'])
            return {'compatible': compatible, 'source': 'native_spcc_list_offline',
                    'names': names, 'names_sha256': stable_hash(names),
                    'error': None if compatible else result.stdout[-2000:]}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {'compatible': False, 'source': 'native_spcc_list_offline', 'names': {}, 'error': str(exc)}


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
    star_version = _tool_version(starnet)[0] if starnet else None
    parsed_star = _version_tuple(star_version)
    star_version_ok = bool(parsed_star and (2, 5, 0) <= parsed_star <= (2, 6, 2))
    native = parsed_star == (2, 6, 2)
    adapter = SCRIPTS_ROOT / ("siril_starnet_linear.py" if native else "siril_starnet.py")
    lock_path = SKILL_ROOT / "references" / ("starnet-linear-source.json" if native else "starnet-source.json")
    try:
        source_lock = load_json(lock_path)
        adapter_record = fingerprint(adapter, role="starnet_adapter")
        adapter_ok = (adapter_record["sha256"] == source_lock["adapter_sha256"]
                      and (not native or source_lock.get("legacy_adapter_sha256") == sha256_file(SCRIPTS_ROOT / "siril_starnet.py")))
    except (OSError, ContractError, KeyError):
        adapter_record, adapter_ok = None, False
    python = (_probe_starnet_python(siril_path)
              if siril_compatible and starnet and star_model and star_version_ok and adapter_ok
              else {"status": "not_probed"})
    star_ready = bool(starnet and star_model and star_version_ok and adapter_ok and python.get("status") == "verified")
    starnet_record = {
        "compatible": star_ready, "version": star_version, "supported_range": ">=2.5.0,<=2.6.2",
        "executable": fingerprint(starnet, role="starnet2") if starnet else None,
        "model": fingerprint_resource(star_model, kind="directory" if star_model.is_dir() else "file") if star_model else None,
        "adapter": adapter_record, "source_lock": fingerprint(lock_path) if lock_path.is_file() else None,
        "python": python,
        "reasons": [reason for missing, reason in (
            (not starnet, "starnet2_unavailable"), (not star_model, "starnet_model_unavailable"),
            (not star_version_ok, "starnet_version_incompatible"), (not adapter_ok, "starnet_adapter_unapproved"),
            (python.get("status") != "verified", "starnet_python_unavailable")) if missing],
    }

    gaia_raw = os.environ.get("DEEP_SKY_SIRIL_GAIA_DIR", "").strip()
    gaia = Path(gaia_raw).expanduser().resolve(strict=False) if gaia_raw else None
    gaia_ready = bool(gaia and gaia.is_dir() and not gaia.is_symlink())
    astro_value = os.environ.get("DEEP_SKY_SIRIL_GAIA_ASTRO_FILE")
    astro = Path(astro_value).expanduser() if astro_value else None
    astro_ready = bool(astro and astro.is_file() and not astro.is_symlink() and astro.stat().st_size > 0)
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
            "network_default": "online",
            "offline_option": "explicit_offline_session",
            "catalogue_policy": "explicit_gaia_or_localgaia_per_script",
            "automatic_download": False,
            "python_pixel_processing": True,
        },
        "tools": {
            "siril_cli": siril,
            "spcc_profiles": _probe_spcc_profiles(siril_path) if siril_compatible else {"compatible": False, "names": {}},
            "starnet": starnet_record,
            "deepsnr": probe_deepsnr(),
            "local_gaia": {"compatible": gaia_ready, "path": str(gaia) if gaia_ready else None},
            "local_gaia_astro": {"compatible": astro_ready, "path": str(astro.resolve()) if astro_ready else None,
                                 "fingerprint": fingerprint(astro) if astro_ready else None},
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
    *, native_denoise: bool = False,
) -> dict[str, Any] | None:
    if protocol == "stars.separate":
        runtime = probe.get("tools", {}).get("starnet", {})
        if not starnet_runtime_unchanged(runtime):
            raise ContractError("runtime_dependency_missing", "Frozen StarNet CLI/model/adapter/Python runtime is unavailable or changed")
        return runtime["python"]
    if protocol == 'restoration.denoise' and not native_denoise:
        return None
    if native_denoise and not deepsnr_runtime_unchanged(probe.get('tools', {}).get('deepsnr')):
        raise ContractError('runtime_dependency_missing', 'Frozen DeepSNR runtime is unavailable or changed')
    if protocol not in {"background.subtract", "restoration.deconvolve", 'restoration.denoise'}:
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
