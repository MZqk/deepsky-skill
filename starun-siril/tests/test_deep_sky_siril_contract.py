from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import deep_sky_siril as cli  # noqa: E402
import deep_sky_siril_artifacts as artifact_rules  # noqa: E402
import deep_sky_siril_contract as contract  # noqa: E402
import deep_sky_siril_validation as validation_rules  # noqa: E402


def test_public_contract_exposes_four_commands_and_v1(capsys: pytest.CaptureFixture[str]) -> None:
    assert contract.CONTRACT_VERSION == "1"
    assert tuple(cli.PUBLIC_COMMANDS) == ("probe", "init", "run", "finalize")

    exit_code = cli.main(["install-tool"])

    assert exit_code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["schema"] == "starun-siril.error.v1"
    assert error["status"] == "failed"
    assert error["error"]["code"] == "invalid_arguments"


def test_container_validation_is_init_only_and_defaults_to_siril() -> None:
    parser = cli.build_parser()
    default_args = parser.parse_args(["init", "/input.fit", "--session", "/session"])
    strict_args = parser.parse_args(
        [
            "init",
            "/input.fit",
            "--session",
            "/session",
            "--container-validation",
            "strict",
        ]
    )

    assert default_args.container_validation == "siril"
    assert strict_args.container_validation == "strict"
    with pytest.raises(contract.ContractError) as run_override:
        parser.parse_args(
            [
                "run",
                "--session",
                "/session",
                "--protocol",
                "input.inspect",
                "--script",
                "/session/scripts/010-inspect.ssf",
                "--source",
                "/input.fit",
                "--expect",
                "/session/previews/010-inspect.jpg",
                "--container-validation",
                "strict",
            ]
        )
    assert run_override.value.code == "invalid_arguments"


def test_validate_display_fails_closed_without_a_real_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300"
        + "08" * 64
        + "ffc0000b080001000101011100ffc40014000100000000000000000000000000000000"
        + "ffda0008010100003f00ffd9"
    )
    path = tmp_path / "preview.jpg"
    path.write_bytes(jpeg)
    monkeypatch.setattr(artifact_rules, "Image", None)

    result = artifact_rules.validate_display(path)

    assert result == {"passed": False, "reason": "pillow_required_for_format"}


def test_validate_display_rejects_structurally_plausible_but_undecodable_jpeg(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truncated-scan.jpg"
    path.write_bytes(
        bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000"
            "ffc0000b080010001003011100"
            "ffda0008010100003f00ffd9"
        )
    )

    result = artifact_rules.validate_display(path)

    assert result["passed"] is False
    assert result["reason"].startswith("decode_failed:")


def test_contract_serialization_and_fingerprint(tmp_path: Path) -> None:
    assert contract.canonical_json({"z": 2, "a": 1}) == b'{"a":1,"z":2}'
    with pytest.raises(contract.ContractError) as invalid_json:
        contract.canonical_json({"invalid": math.nan})
    assert invalid_json.value.code == "invalid_json_value"

    payload_path = tmp_path / "payload.json"
    contract.atomic_write_json(payload_path, {"z": 2, "a": 1})
    assert payload_path.read_bytes() == b'{"a":1,"z":2}\n'
    assert contract.load_json(payload_path) == {"a": 1, "z": 2}

    record = contract.fingerprint(payload_path)
    assert contract.fingerprint_matches(record)
    payload_path.write_bytes(b'{"changed":true}\n')
    assert not contract.fingerprint_matches(record)


def test_contract_session_paths_remain_fail_closed(tmp_path: Path) -> None:
    session = tmp_path / "session"
    artifacts = session / "artifacts"
    artifacts.mkdir(parents=True)
    artifact = artifacts / "image.fit"
    artifact.write_bytes(b"fits")

    assert contract.session_path(
        session,
        "artifacts/image.fit",
        must_exist=True,
        allowed_roots=("artifacts",),
    ) == artifact

    for value in ("../escape.fit", "logs/not-allowed.txt"):
        with pytest.raises(contract.ContractError) as unsafe:
            contract.session_path(session, value, allowed_roots=("artifacts",))
        assert unsafe.value.code == "unsafe_path"

    outside = tmp_path / "outside.fit"
    outside.write_bytes(b"outside")
    link = artifacts / "linked.fit"
    link.symlink_to(outside)
    with pytest.raises(contract.ContractError) as unsafe_link:
        contract.session_path(session, link, must_exist=True, allowed_roots=("artifacts",))
    assert unsafe_link.value.code == "unsafe_path"

    with pytest.raises(contract.ContractError) as outside_relative:
        contract.relative_session_path(session, outside)
    assert outside_relative.value.code == "unsafe_path"


@pytest.mark.parametrize(
    "adapter_name",
    (
        "/tmp/unbundled-adapter.py",
        "subdirectory/unbundled-adapter.py",
        "../unbundled-adapter.py",
    ),
)
def test_pyscript_policy_adapter_name_cannot_escape_scripts_root(
    tmp_path: Path,
    adapter_name: str,
) -> None:
    tokens = [
        "pyscript",
        str(tmp_path / "unbundled-adapter.py"),
        "--contract",
        "contract.json",
        "--contract-sha256",
        "0" * 64,
        "--expected-source",
        "source.fit",
        "--receipt",
        "receipt.json",
    ]
    policy = {"pyscript_protocols": {"background.subtract": adapter_name}}
    with pytest.raises(contract.ContractError) as unsafe_adapter:
        validation_rules._validate_pyscript(
            "background.subtract",
            tokens,
            session=tmp_path,
            source=tmp_path / "source.fit",
            policy=policy,
        )
    assert unsafe_adapter.value.code == "unsafe_siril_script"
