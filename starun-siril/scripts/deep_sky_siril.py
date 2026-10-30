#!/usr/bin/env python3
"""CLI-first standalone Siril executor for starun-siril contract 1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from deep_sky_siril_contract import (
    CONTRACT_VERSION,
    DEFAULT_TIMEOUT,
    ContractError,
    atomic_write_json,
)
from deep_sky_siril_core import check_review, confirm_input, finalize_session, run_script
from deep_sky_siril_session import init_session
from deep_sky_siril_tooling import probe_tools
from deep_sky_siril_validation import public_protocols


PUBLIC_COMMANDS = ("probe", "init", "run", "review-check", "confirm-input", "finalize", "status")


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContractError("invalid_arguments", message)


def _channel_map(value: str | None) -> dict[str, str] | None:
    if value is None:
        return None
    result: dict[str, str] = {}
    for item in value.split(","):
        if "=" not in item:
            raise ContractError("channel_map_invalid", "Channel map must use R=...,G=...,B=...")
        channel, role = (part.strip() for part in item.split("=", 1))
        normalized = {"R": "red", "G": "green", "B": "blue"}.get(channel.upper())
        if normalized is None or normalized in result:
            raise ContractError("channel_map_invalid", f"Invalid or duplicate channel: {channel}")
        if role not in {"SII", "Ha", "OIII", "R", "G", "B"}:
            raise ContractError("channel_map_invalid", f"Unsupported channel role: {role}")
        result[normalized] = role
    if set(result) != {"red", "green", "blue"}:
        raise ContractError("channel_map_invalid", "Channel map requires R, G, and B")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = ContractArgumentParser(
        prog="deep_sky_siril.py",
        description="Agent-composed, protocol-bound Siril CLI execution for one stacked master",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=ContractArgumentParser,
    )

    probe = subparsers.add_parser("probe", help="Probe bounded Siril and optional capabilities")
    probe.add_argument("--offline", action="store_true", help="Report an offline session policy")
    probe.add_argument("--output")

    init = subparsers.add_parser("init", help="Create a standalone contract 1 session")
    init.add_argument("input", help="Single already-stacked master")
    init.add_argument("--session", required=True, help="Dedicated empty session directory")
    init.add_argument(
        "--input-state",
        choices=("auto", "linear", "nonlinear", "unknown"),
        default="auto",
        help="Recover verified same-master declarations; unknown context requires inspection and confirm-input",
    )
    init.add_argument("--state-evidence", action="append", default=[])
    init.add_argument(
        "--channel-mode",
        choices=("mono", "broadband", "narrowband", "dualband-osc", "unknown"),
        default="unknown",
    )
    init.add_argument("--channel-map")
    init.add_argument("--target-name")
    init.add_argument("--target-type", default="unknown")
    init.add_argument("--dualband", choices=("auto", "off"), default="off",
                      help="Opt in to eligible Ha/OIII HOO trials with auto; default off")
    init.add_argument("--style", choices=("vivid", "natural", "balanced", "artistic"), default="vivid")
    init.add_argument(
        "--stars",
        choices=("adaptive", "preserve", "standalone-starless"),
        default="adaptive",
    )
    init.add_argument("--offline", action="store_true", help="Start every Siril run with --offline; forbids remote Gaia in this session")
    init.add_argument("--keep-intermediates", action="store_true")
    init.add_argument(
        "--container-validation",
        choices=("siril", "strict"),
        default="siril",
        help="Freeze Siril reopen validation; strict adds a full container preflight",
    )

    run = subparsers.add_parser("run", help="Validate and optionally execute one Agent-authored .ssf")
    run.add_argument("--session", required=True)
    run.add_argument("--protocol", required=True, choices=public_protocols())
    run.add_argument("--script", required=True)
    run.add_argument(
        "--source",
        required=True,
        metavar="PATH",
        help="Filesystem path to the bound current parent; prefer an absolute path. "
             "For input.inspect use session.json input.path, not the receipt marker @input",
    )
    run.add_argument("--expect", action="append", required=True)
    run.add_argument("--primary-output")
    run.add_argument("--separation-run")
    run.add_argument("--stretch-run")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    run.add_argument(
        "--validate-only",
        action="store_true",
        help="Write a static validation receipt without starting siril-cli",
    )

    review = subparsers.add_parser(
        "review-check",
        help="Derive or validate a review receipt without writing one",
    )
    review.add_argument("--session", required=True)
    review.add_argument("--run-id", required=True)
    review.add_argument(
        "--review",
        help="Existing review receipt to validate; omit to print the skeleton",
    )

    status = subparsers.add_parser("status", help="Read accepted parent, next-stage intent, blockers and trial budget")
    status.add_argument("--session", required=True)
    status.add_argument("--regions", help="Previously reviewed ROI contract to propose rebinding without writing it")
    status.add_argument("--parameters", help="Existing solved stretch report to verify reusable prefix evidence")

    confirm = subparsers.add_parser("confirm-input", help="Confirm unknown input context after accepted inspection")
    confirm.add_argument("--session", required=True)
    confirm.add_argument("--run-id", required=True)
    confirm.add_argument("--input-state", choices=("linear", "nonlinear"))
    confirm.add_argument("--channel-mode", choices=("mono", "broadband", "narrowband", "dualband-osc"))
    confirm.add_argument("--state-evidence", action="append", required=True)

    finalize = subparsers.add_parser(
        "finalize",
        help="Commit a standalone final result and deliverable outputs",
    )
    finalize.add_argument("--session", required=True)
    finalize.add_argument("--selection", required=True)
    finalize.add_argument("--keep-intermediates", action="store_true")
    return parser


def _public_error(error: ContractError) -> dict[str, Any]:
    return {
        "schema": "starun-siril.error.v1",
        "status": "failed",
        "contract_version": CONTRACT_VERSION,
        "error": {
            "code": error.code,
            "message": str(error),
            "missing_dependencies": error.missing_dependencies,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    command_hint = (list(argv)[0] if argv else sys.argv[1] if len(sys.argv) > 1 else None)
    try:
        args = build_parser().parse_args(argv)
        if args.command == "probe":
            result = probe_tools(offline=bool(args.offline))
            if args.output:
                atomic_write_json(Path(args.output).expanduser().resolve(), result)
        elif args.command == "init":
            result = init_session(
                args.input,
                args.session,
                input_state=args.input_state,
                state_evidence=args.state_evidence,
                channel_mode=args.channel_mode,
                channel_map=_channel_map(args.channel_map),
                target_name=args.target_name,
                target_type=args.target_type,
                style=args.style,
                dualband=args.dualband,
                stars=args.stars,
                offline=bool(args.offline),
                keep_intermediates=bool(args.keep_intermediates),
                container_validation=args.container_validation,
            )
        elif args.command == "run":
            result = run_script(
                args.session,
                protocol=args.protocol,
                script_value=args.script,
                source_value=args.source,
                expected_values=args.expect,
                primary_output_value=args.primary_output,
                separation_run=args.separation_run,
                stretch_run=args.stretch_run,
                timeout=args.timeout,
                validate_only=bool(args.validate_only),
            )
        elif args.command == "review-check":
            result = check_review(
                args.session,
                args.run_id,
                args.review,
            )
        elif args.command == "status":
            from deep_sky_siril_resume import session_status
            result = session_status(args.session, args.regions, args.parameters)
        elif args.command == "confirm-input":
            result = confirm_input(args.session, args.run_id, input_state=args.input_state,
                                   channel_mode=args.channel_mode, state_evidence=args.state_evidence)
        elif args.command == "finalize":
            result = finalize_session(
                args.session,
                selection_value=args.selection,
                keep_intermediates=bool(args.keep_intermediates),
            )
        else:  # argparse prevents this branch.
            raise ContractError("invalid_arguments", "Unknown command")
    except ContractError as error:
        print(json.dumps(_public_error(error), ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    except Exception as error:  # Keep the public CLI structured without leaking a traceback.
        wrapped = ContractError("internal_error", f"{type(error).__name__}: {error}")
        print(json.dumps(_public_error(wrapped), ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if command_hint == "run" and result.get("status") != "success":
        return 1
    if command_hint == "finalize" and result.get("status") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
