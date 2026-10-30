#!/usr/bin/env python3
"""Evidence-bound parameter solving for Agent-selected Siril stretch operators."""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

from deep_sky_siril_artifacts import read_fits_pixels, diagnose_siril_log
from deep_sky_siril_contract import (ContractError, RUN_ID_PATTERN, fingerprint, fingerprint_matches,
                                    sha256_file, session_path, load_json, protocol_reference_bindings)
from siril_manual_bundle import BundleError, strict_json_bytes as bundle_json

ROOT = Path(__file__).resolve().parents[1]
SHADOW_LIMIT = 0.0001
PROTOCOL_MIN_MTF = 0.0001
PROTOCOL_MAX_MTF = 0.9999
METHODS = {"mtf", "asinh", "ght", "autostretch"}
STATISTICS = {"median", "mad", "p01", "p99", "span"}


def fail(message):
    raise ContractError("stretch_parameters_invalid", message)


def strict_json_bytes(data, *, document):
    try:
        return bundle_json(data, document=document)
    except BundleError as exc:
        fail(str(exc))


def number(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        fail("Stretch numbers must be finite JSON numbers")
    return float(value)


def pixels(path):
    raw, scale, zero = read_fits_pixels(path)
    data = np.asarray(raw, dtype=np.float64) * scale + zero
    if data.shape[0] not in (1, 3) or not np.isfinite(data).all():
        fail(f"Stretch requires finite mono/RGB FITS: {path}; nonfinite_count={int(np.count_nonzero(~np.isfinite(data)))}")
    return data


def statistics(data, regions):
    lum = data[0] if data.shape[0] == 1 else np.einsum("c,cyx->yx", [0.2126, 0.7152, 0.0722], data)
    height = data.shape[1]
    result = {}
    for region in regions:
        x0, y0, x1, y1 = region["rect"]
        values = lum[height-y1:height-y0, x0:x1]
        median = float(np.median(values))
        low, high = map(float, np.percentile(values, [1, 99]))
        result[region["id"]] = {"median": median, "mad": float(np.median(np.abs(values-median))),
                                "p01": low, "p99": high, "span": high-low}
    return result


def clipping(data):
    return [{"shadow_clip_rate": float(np.mean(plane <= 0)),
             "highlight_sat_rate": float(np.mean(plane >= 1))} for plane in data]


def optimization_enabled(payload):
    return payload.get('execution_policy', {}).get('stretch_optimization') in {'highlight_budget_mtf_v1', 'highlight_budget_mtf_v2'}


def safe_blackpoint(layers, *, headroom=False):
    """Use the strict count budget and round down in Siril's float32 parameter domain."""
    shadow = 1.0
    for data in layers:
        for plane in data:
            flat = plane.ravel()
            budget = math.ceil(SHADOW_LIMIT * flat.size) - 1
            existing = int(np.count_nonzero(flat <= 0))
            if existing > budget:
                fail("The source already reaches the scientific shadow clipping threshold")
            if headroom:
                budget = existing + (budget - existing) // 10
            boundary = np.partition(flat, budget)[budget]
            value = np.float32(boundary)
            if value >= boundary:
                value = np.nextafter(value, np.float32(-np.inf))
            shadow = min(shadow, float(value))
    return max(0.0, shadow)


def calculate_adaptive_mtf(fits_path, target_type="unknown", target_bg=None, shadow_sigma_factor=2.8):
    """Explicit legacy calculation; global statistics are not verified background measurements."""
    data = pixels(Path(fits_path))
    med = float(np.median(data))
    mad = float(np.median(np.abs(data-med)))
    sigma = 1.4826 * mad
    category = (target_type or "").lower()
    target = target_bg if target_bg is not None else (
        0.22 if "nebula" in category else 0.18 if "cluster" in category else 0.20)
    target = number(target)
    factor = number(shadow_sigma_factor)
    if not 0 < target < 1 or factor < 0:
        fail("Legacy target background must be in (0, 1) and shadow factor nonnegative")
    shadow = max(0.0, med-factor*sigma)
    if shadow >= med:
        shadow = max(0.0, med-1e-4)
    if shadow >= 1:
        fail("Legacy shadow is outside the normalized pixel domain")
    x = min(max((med-shadow)/(1-shadow), 1e-6), 1-1e-6)
    mid = x*(target-1)/(2*x*target-target-x)
    mid = min(max(mid, PROTOCOL_MIN_MTF), PROTOCOL_MAX_MTF)
    return {"shadow": shadow, "midtones": mid, "highlight": 1.0,
            "diagnostics": {"median": med, "mad": mad, "sigma": sigma, "target_bg": target,
                            "target_type": target_type, "raw_shadow": shadow, "raw_midtones": mid,
                            "shadow_sigma_factor": factor, "mode": "legacy-mtf"}}


def record(session, path):
    return {"path": path.relative_to(session).as_posix(), **fingerprint(path, include_path=False)}


def bound_file(session, binding, roots=("reports",)):
    if (not isinstance(binding, dict) or not {"path", "sha256"} <= binding.keys()
        or not isinstance(binding.get("path"), str) or not isinstance(binding.get("sha256"), str)):
        fail("Missing evidence path/hash binding")
    path = session_path(session, binding["path"], must_exist=True, allowed_roots=roots)
    if sha256_file(path) != binding["sha256"]:
        fail(f"Evidence hash changed: {binding['path']}")
    return path


def source_file(session, payload, binding):
    if (not isinstance(binding, dict) or set(binding) != {"path", "sha256"}
        or not isinstance(binding["path"], str) or not isinstance(binding["sha256"], str)):
        fail("Source must declare path and sha256")
    path = (Path(payload["input"]["path"]) if binding["path"] == "@input"
            else session_path(session, binding["path"], must_exist=True, allowed_roots=("artifacts",)))
    if path.is_symlink() or sha256_file(path) != binding["sha256"]:
        fail("Source fingerprint changed")
    return path


def read_request(session, payload, request_path):
    from deep_sky_siril_metrics import measure_regions
    path = session_path(session, request_path, must_exist=True, allowed_roots=("reports",))
    request = strict_json_bytes(path.read_bytes(), document="stretch request")
    from deep_sky_siril_stretch_stepwise import REQUEST as STEPWISE_REQUEST, read_request as read_stepwise
    if isinstance(request, dict) and request.get('schema') == STEPWISE_REQUEST:
        proposal, _, _, source, regions, _, path = read_stepwise(session, payload, path)
        return proposal, source, regions, path
    required = {"schema", "source", "regions", "operators", "manual_lookup"}
    from deep_sky_siril_stretch_v2 import REQUEST, EXTRA_STATISTICS, PROPOSAL_REQUEST, PROPOSAL_POLICY, proposal_tokens
    from deep_sky_siril_stretch_profiles import REQUEST as PROFILE_REQUEST
    protected = isinstance(request, dict) and request.get('schema') in {REQUEST, PROFILE_REQUEST, PROPOSAL_REQUEST}
    proposal = isinstance(request, dict) and request.get('schema') == PROPOSAL_REQUEST
    if protected:
        required |= {'profile', 'targets', 'core_absence'}
    if isinstance(request, dict) and (request.get('schema') == PROFILE_REQUEST or proposal and 'strategy' in request):
        required.add('strategy')
    if proposal and 'artifact_focus' in request:
        required.add('artifact_focus')
    if proposal and 'baseline_proposal' in request:
        required.add('baseline_proposal')
    if not isinstance(request, dict) or set(request) != required or request["schema"] not in {"starun-siril.stretch-request.v1", REQUEST, PROFILE_REQUEST, PROPOSAL_REQUEST}:
        fail("Invalid stretch request fields or schema")
    if proposal and payload.get('execution_policy', {}).get('stretch_parameters') != PROPOSAL_POLICY:
        fail('Agent factor proposals require their frozen parameter policy')
    source = source_file(session, payload, request["source"])
    regions_path = bound_file(session, request["regions"])
    measured = measure_regions(source, None, regions_path)
    if measured["status"] != "measured":
        fail("Stretch regions are not finite, geometry-bound scientific measurements")
    regions = strict_json_bytes(regions_path.read_bytes(), document="stretch regions")["regions"]
    roles = {r["id"]: r["role"] for r in regions}
    operators = request["operators"]
    if not isinstance(operators, list) or not 1 <= len(operators) <= 32:
        fail("Declare 1..32 Agent-selected operators")
    for step in operators:
        if not isinstance(step, dict):
            fail("Operators must be objects")
        required_step = {"method", "purpose", "constraints"} | ({"landmarks"} if step.get("method") == "ght" else set())
        if proposal:
            required_step.add('factors')
        if set(step) != required_step or not isinstance(step["method"], str) or step["method"] not in METHODS or not isinstance(step["purpose"], str) or not step["purpose"].strip():
            fail("Each operator needs a supported method, purpose and constraints")
        if proposal:
            proposal_tokens(step, 0., {'LP': 0., 'SP': .5, 'HP': 1.})
        constraints = step["constraints"]
        if not isinstance(constraints, list) or not 1 <= len(constraints) <= 64:
            fail("Each operator requires explicit subject and protection constraints")
        used = set()
        for constraint in constraints:
            if not isinstance(constraint, dict) or set(constraint) != {"region", "statistic", "min", "max"}:
                fail("Constraints require region, statistic, min and max")
            if (not isinstance(constraint["region"], str) or not isinstance(constraint["statistic"], str)
                or constraint["region"] not in roles or constraint["statistic"] not in STATISTICS | (EXTRA_STATISTICS if protected else set())):
                fail("Constraint refers to an unknown region or statistic")
            if number(constraint["min"]) > number(constraint["max"]):
                fail("Constraint minimum exceeds maximum")
            if constraint['statistic'] == 'hf_sigma2' and roles[constraint['region']] != 'background':
                fail('hf_sigma2 budgets must refer to trusted sky regions')
            used.add(roles[constraint["region"]])
        if not {"background", "faint"} <= used or not protected and not ({"core", "stars"} & used):
            fail("Each step requires background, faint-subject and core or stars protection constraints")
        if step["method"] == "ght":
            landmarks = step["landmarks"]
            if not isinstance(landmarks, dict) or set(landmarks) != {"LP", "SP", "HP"}:
                fail("GHS requires LP/SP/HP region-statistic landmarks")
            for landmark in landmarks.values():
                if not isinstance(landmark, dict) or set(landmark) != {"region", "statistic"}:
                    fail("Invalid GHS landmark")
                if (not isinstance(landmark["region"], str) or not isinstance(landmark["statistic"], str)
                    or landmark["region"] not in roles or landmark["statistic"] not in STATISTICS):
                    fail("Unknown GHS landmark region/statistic")
    lookup = request["manual_lookup"]
    if not isinstance(lookup, dict) or set(lookup) != {"status", "reason", "evidence"} or not isinstance(lookup["reason"], str) or not lookup["reason"].strip():
        fail("Declare authentic manual lookup evidence or a complete-skeleton rationale")
    if not isinstance(lookup["status"], str) or lookup["status"] not in {"performed", "not_needed"} or not isinstance(lookup["evidence"], list):
        fail("Invalid manual lookup status")
    if (lookup["status"] == "performed") != bool(lookup["evidence"]):
        fail("Manual lookup evidence cardinality conflicts with status")
    documents = []
    for evidence in lookup["evidence"]:
        evidence_path = bound_file(session, evidence)
        if not evidence["path"].startswith("reports/manual-evidence/"):
            fail("Manual evidence must be in reports/manual-evidence")
        documents.append(strict_json_bytes(evidence_path.read_bytes(), document="manual evidence"))
    if documents:
        from query_siril_manual import verify_query_evidence_documents
        verify_query_evidence_documents(documents, skill_root=ROOT, session_payload=payload)
    if protected:
        from deep_sky_siril_stretch_v2 import validate
        validate(request, regions)
    if request['schema'] == PROFILE_REQUEST or proposal and 'strategy' in request:
        from deep_sky_siril_stretch_profiles import validate_request
        validate_request(session, payload, request, path)
    if 'baseline_proposal' in request:
        from deep_sky_siril_visibility import verify_proposal
        verify_proposal(session, request)
    from deep_sky_siril_stretch_profiles import routing_required, BOUND_PLAN, load_plan, candidate_run_id
    if routing_required(payload):
        if not proposal or 'strategy' not in request:
            fail('New stretch requests require v4 factors and a bound morphology strategy')
        plan, _ = load_plan(session, payload, request['strategy']['plan'])
        if plan['schema'] != BOUND_PLAN:
            fail('New stretch requests require a v2 morphology strategy')
        if request['strategy']['variant'] == 'tail':
            main_variant = 'exception' if plan['mode'] == 'exception' else 'profile'
            main_path = session/'reports'/candidate_run_id(plan['run_id'], main_variant)/'stretch-parameters.json'
            from deep_sky_siril_stretch_profiles import _tail_candidate
            _tail_candidate(session, payload, main_path, allow_ineligible=True)
    return request, source, regions, path


def accepted_parent(session, payload, source):
    import deep_sky_siril_core as core
    from deep_sky_siril_processing import source_state
    from deep_sky_siril_session import _verified_success_receipts
    state = source_state(session, payload, source)
    if state["domain"] != "linear" or payload["context"]["channel_mode"] == "unknown":
        fail("Stretch requires a confirmed linear parent and known channel mode")
    receipts = _verified_success_receipts(session)
    parents = [state["run"]] if state["run"] else [
        r for r in receipts if r["protocol"] == "input.inspect" and r["source_path"] == "@input"]
    if len(parents) != 1:
        fail("An accepted parent/input inspection is required")
    if state["separation_run"] and state["separation_run"] != parents[0]["id"]:
        parents += [next(r for r in receipts if r["id"] == state["separation_run"])]
    full = None
    for parent in parents:
        reviews = [p for p in (session/"reviews").glob("*.json") if load_json(p).get("run_id") == parent["id"]]
        if len(reviews) != 1 or core._validate_review(session, str(reviews[0]), parent["id"])[0]["verdict"] != "accept":
            fail("Actual accepted parent review is required")
        if parent["protocol"] == "stars.separate":
            full = session_path(session, parent["star_separation"]["full_source"], must_exist=True, allowed_roots=("artifacts",))
    return full


def check_constraints(values, constraints):
    rows = []
    for constraint in constraints:
        value = values[constraint["region"]][constraint["statistic"]]
        rows.append({**constraint, "value": value, "satisfied": constraint["min"] <= value <= constraint["max"]})
    return rows


def violation(rows):
    return max((max(r["min"]-r["value"], r["value"]-r["max"], 0) /
                max(abs(r["min"]), abs(r["max"]), 1e-12) for r in rows), default=0.0)


def tokens_for(method, coordinates, black, landmarks):
    fmt = lambda value: format(float(value), ".17g")
    if method == "mtf":
        mid = 1/(1+math.exp(-coordinates[0]))
        tokens = ["mtf", fmt(black), fmt(mid), "1"]
    elif method == "asinh":
        tokens = ["asinh", "-human", fmt(math.exp(coordinates[0])), fmt(black), "-clipmode=rgbblend"]
    elif method == "ght":
        tokens = ["ght", "-D="+fmt(coordinates[0]), "-B="+fmt(coordinates[1]),
                  *["-"+k+"="+fmt(landmarks[k]) for k in ("LP", "SP", "HP")], "-human", "-clipmode=rgbblend"]
    else:
        tokens = ["autostretch", "-linked", fmt(coordinates[0]), fmt(coordinates[1])]
    from deep_sky_siril_processing import validate_parameters
    validate_parameters(tokens, "stretch")
    if method == "autostretch" and not (-8 <= coordinates[0] <= -2.8 and .08 <= coordinates[1] <= .18):
        fail("Linked autostretch parameters are outside the protocol bounds")
    return tokens


def gentle_key(tokens):
    if tokens[0] == "mtf":
        return (abs(math.log(float(tokens[2])/(1-float(tokens[2])))),)
    if tokens[0] == "asinh":
        return (float(tokens[2]),)
    if tokens[0] == "ght":
        return (float(tokens[1][3:]), abs(float(tokens[2][3:])), float(tokens[2][3:]))
    return (float(tokens[3]), float(tokens[2]))


class NativeTrials:
    """Run validated local pixel commands; never publish trial images as scientific parents."""

    def __init__(self, session, runtime, directory, evidence_dir, deadline):
        self.session, self.runtime, self.directory = session, runtime, directory
        self.evidence_dir, self.deadline, self.count = evidence_dir, deadline, 0
        self.scientific_attempt = None

    def _claim_attempt(self):
        if self.scientific_attempt is not None:
            from deep_sky_siril_stretch_profiles import claim_attempt
            claim_attempt(*self.scientific_attempt)
            self.scientific_attempt = None

    def run(self, source, chain):
        from deep_sky_siril_processing import validate_parameters
        from deep_sky_siril_tooling import _subprocess_environment
        if not fingerprint_matches(self.runtime["fingerprint"]):
            fail("Frozen Siril runtime changed")
        for step in chain:
            if step["command"] not in METHODS:
                fail("Trial commands must be selected stretch operators")
            validate_parameters([step["command"], *step["parameters"]], "stretch")
        self.count += 1
        label = f"trial-{self.count:04d}"
        script = self.evidence_dir/(label+".ssf")
        log = self.evidence_dir/(label+".log")
        output = self.directory/(label+".fit")
        commands = "\n".join(" ".join([s["command"], *s["parameters"]]) for s in chain)
        preview = ('\nicc_assign sRGB\nsavejpg '+json.dumps(str(output.with_suffix('')))+' 95'
                   if getattr(self, 'previews', False) else '')
        script.write_text("requires 1.4.4 1.5.0\nset32bits\nload "+json.dumps(str(source))+
                          "\n"+commands+"\nsave "+json.dumps(str(output))+" -chksum"+preview+"\nclose\n", encoding="utf-8")
        config = self.directory/"trial.ini"
        if not config.exists():
            config.write_text("[core]\nforce_16bit=false\ncheck_updates=false\nscript_check_requires=true\n"
                              "[gui]\nicc_autoassignment=0\nuse_scripts_repository=false\n"
                              "use_spcc_repository=false\nauto_update_scripts=false\nauto_update_spcc=false\n")
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Stretch parameter solve timed out")
        self._claim_attempt()
        try:
            result = subprocess.run([self.runtime["path"], "--initfile="+str(config),
                                     "--directory="+str(self.directory), "--offline", "--script="+str(script)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=remaining, env=_subprocess_environment("siril_runtime"))
        except subprocess.TimeoutExpired as exc:
            text = exc.stdout or b""
            log.write_text(text.decode(errors="replace") if isinstance(text, bytes) else text)
            raise TimeoutError("Stretch parameter solve timed out") from exc
        log.write_text(result.stdout, encoding="utf-8")
        diagnostics = diagnose_siril_log(result.stdout, exit_code=result.returncode, timed_out=False, execution_valid=output.is_file())
        if result.returncode or diagnostics["status"] == "failed" or not output.is_file():
            fail(f"Native trial failed; inspect {log}")
        if not fingerprint_matches(self.runtime["fingerprint"]):
            fail("Frozen Siril runtime changed during trial")
        return output, {"script": record(self.session, script), "log": record(self.session, log),
                        "output_sha256": sha256_file(output)}


def solve_stretch_parameters(session_path_value, request_path, run_id, timeout=1800, *, _derived_source=None, _shared_attempt=None):
    from deep_sky_siril_session import effective_session_payload, load_session, execution_policy_is_current
    deadline = time.monotonic()+number(timeout)
    session, payload, manifest = load_session(session_path_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        fail('Historical sessions are replay/finalize-only; create a current session for new solving')
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None or not 1 <= number(timeout) <= 86400:
        fail("Invalid run ID or solve timeout")
    request, source, regions, request_file = read_request(session, payload, request_path)
    from deep_sky_siril_stretch_stepwise import REQUEST as STEPWISE_REQUEST, run as run_stepwise
    if request['schema'] == STEPWISE_REQUEST:
        if _derived_source is not None or _shared_attempt is not None:
            fail('Stepwise HOO is outside this execution policy')
        return run_stepwise(session, payload, request_file, run_id, deadline)
    from deep_sky_siril_stretch_profiles import validate_body_chain
    if _shared_attempt is None or request.get('strategy', {}).get('variant') != 'rgb':
        validate_body_chain(payload, request['operators'])
    from deep_sky_siril_stretch_profiles import routing_required
    if routing_required(payload) and _derived_source is not None and _shared_attempt is None:
        fail('Derived stretch sources require their bound HOO scientific attempt')
    if _shared_attempt is not None:
        marker = session/'reports'/_shared_attempt/'scientific-attempt.json'
        parent = load_json(marker)['request']
        role = request.get('strategy', {}).get('variant')
        if (role not in {'rgb', 'ha', 'oiii'} or parent.get('strategy') != request['strategy']['plan']
            or parent.get('source') != request['source'] or parent['operators'][role] != request['operators']):
            fail('Derived child does not belong to the claimed HOO scientific attempt')
        expected = None if role == 'rgb' else marker.parent/'extraction-trials'/(
            'ha.fit' if role == 'ha' else 'oiii-denoised.fit' if parent['oiii_denoise']['enabled'] else 'oiii.fit')
        if (Path(_derived_source).resolve() if _derived_source is not None else None) != expected:
            fail('HOO child source differs from its actual extracted channel')
    full = accepted_parent(session, payload, source)
    scientific_source = source
    if _derived_source is not None:
        source = session_path(session, _derived_source, must_exist=True, allowed_roots=("reports",))
        full = None
    if full and any(s["method"] == "autostretch" for s in request["operators"]):
        fail("Starless branches require an explicit MTF/Asinh/GHS chain")
    from deep_sky_siril_stretch_v2 import REQUEST, PROPOSAL_REQUEST, solve
    from deep_sky_siril_stretch_profiles import REQUEST as PROFILE_REQUEST, validate_request
    if request['schema'] == PROFILE_REQUEST or request['schema'] == PROPOSAL_REQUEST and 'strategy' in request:
        validate_request(session, payload, request, request_file, run_id)
    if request['schema'] in {REQUEST, PROFILE_REQUEST, PROPOSAL_REQUEST}:
        return solve(session, payload, request, source, scientific_source, full, regions, request_file,
                     run_id, deadline, derived=_derived_source is not None, shared_attempt=_shared_attempt)
    runtime = load_json(session/"reports/tool-probe.json")["tools"]["siril_cli"]
    if not runtime.get("compatible") or not runtime.get("fingerprint") or not fingerprint_matches(runtime["fingerprint"]):
        fail("Frozen Siril runtime is missing or changed")
    directory = session_path(session, f"reports/{run_id}", allowed_roots=("reports",))
    report_path = directory/"stretch-parameters.json"
    trials_dir = directory/"parameter-trials"
    if report_path.exists() or report_path.is_symlink() or trials_dir.exists() or trials_dir.is_symlink():
        fail("Solve evidence already exists; use a new run ID")
    trials_dir.mkdir(parents=True)
    bindings = {"request": record(session, request_file),
                "regions": record(session, bound_file(session, request["regions"])),
                "source": {**request["source"], "size": scientific_source.stat().st_size},
                "runtime": runtime}
    if _derived_source is not None:
        bindings["derived_source"] = record(session, source)
    if full:
        bindings["full_source"] = record(session, full)
    report = {"schema": "starun-siril.stretch-parameters.v1", "run_id": run_id, "status": "search_exhausted",
              "selection_policy": "legacy_gentle_v1",
              "bindings": bindings, "request": request, "transfer_chain": [], "steps": [],
              "requires_visual_review": True}

    if _derived_source is not None:
        report["schema"] = "starun-siril.derived-stretch-parameters.v1"

    def unchanged():
        source_file(session, payload, request["source"])
        if "derived_source" in bindings:
            bound_file(session, bindings["derived_source"])
        for key in ("request", "regions", "full_source"):
            if key in bindings:
                bound_file(session, bindings[key], ("reports",) if key != "full_source" else ("artifacts",))
        if not fingerprint_matches(runtime["fingerprint"]):
            fail("Frozen runtime fingerprint changed")

    try:
        with tempfile.TemporaryDirectory(prefix="starun-stretch-") as raw:
            oracle = NativeTrials(session, runtime, Path(raw), trials_dir, deadline)
            current, current_full = source, full
            for index, step in enumerate(request["operators"]):
                data = pixels(current)
                full_data = pixels(current_full) if current_full else None
                if full_data is not None and full_data.shape != data.shape:
                    fail("Full/starless geometry differs")
                reference = load_json(bound_file(session, request["regions"]))["reference"]
                measured_shape = (reference["height"], reference["width"])
                if data.shape[1:] != measured_shape:
                    fail(f"Trial geometry differs from regions: {measured_shape}")
                before = statistics(data, regions)
                black = safe_blackpoint([data] + ([full_data] if full_data is not None else []))
                landmarks = {key: before[value["region"]][value["statistic"]]
                             for key, value in step.get("landmarks", {}).items()}
                if landmarks and not 0 <= landmarks["LP"] <= landmarks["SP"] <= landmarks["HP"] <= 1:
                    fail("GHS current-domain landmarks violate LP/SP/HP bounds; revise the evidence")
                method = step["method"]
                bounds = {"mtf": [(-16.0, 16.0)], "asinh": [(0.0, math.log(1000))],
                          "ght": [(float(np.finfo(np.float32).eps), 10.0), (0.0, 15.0)], "autostretch": [(-8.0, -2.8), (.08, .18)]}[method]
                trace = {"method": method, "purpose": step["purpose"], "input_sha256": sha256_file(current),
                         "input_domain": "linear" if index == 0 else "nonlinear_intermediate",
                         "input_statistics": before, "source_clipping": clipping(data), "trials": []}
                report["steps"].append(trace)
                best, best_rank, evaluated = None, None, set()
                trial_start = oracle.count
                evaluation_limit = (128 - (2 if full else 1)) // (2 if full else 1)

                def evaluate(coordinates):
                    nonlocal best, best_rank
                    if coordinates in evaluated or len(evaluated) >= evaluation_limit:
                        return
                    evaluated.add(coordinates)
                    tokens = tokens_for(method, coordinates, black, landmarks)
                    chain = [{"command": tokens[0], "parameters": tokens[1:]}]
                    output, evidence = oracle.run(current, chain)
                    after = pixels(output)
                    if after.shape != data.shape:
                        fail("Native trial changed geometry")
                    rows = check_constraints(statistics(after, regions), step["constraints"])
                    rates = clipping(after)
                    full_rows, full_rates = [], []
                    if current_full:
                        full_output, full_evidence = oracle.run(current_full, chain)
                        full_after = pixels(full_output)
                        if full_after.shape != data.shape:
                            fail("Native full-source trial changed geometry")
                        star_constraints = [c for c in step["constraints"] if next(
                            r["role"] for r in regions if r["id"] == c["region"]) == "stars"]
                        full_rows = check_constraints(statistics(full_after, regions), star_constraints)
                        full_rates = clipping(full_after)
                        evidence["full_source"] = full_evidence
                        full_output.unlink()
                    safe = all(c["shadow_clip_rate"] < SHADOW_LIMIT for c in rates+full_rates)
                    rank = (not (safe and all(r["satisfied"] for r in rows+full_rows)),
                            max(violation(rows+full_rows), 1.0 if not safe else 0.0),
                            *gentle_key(tokens), *coordinates)
                    trace["trials"].append({"parameters": chain[0], "constraints": rows,
                                            "clipping": rates, "full_constraints": full_rows,
                                            "full_clipping": full_rates, "evidence": evidence})
                    if best_rank is None or rank < best_rank:
                        best_rank, best = rank, (coordinates, chain[0], rows)
                    output.unlink()

                gentlest = {"mtf": (0.0,), "asinh": (0.0,), "ght": (float(np.finfo(np.float32).eps), 0.0), "autostretch": (-8.0, .08)}[method]
                evaluate(gentlest)
                if method == "mtf" and best_rank[0]:
                    for constraint in step["constraints"]:
                        if constraint["statistic"] != "median":
                            continue
                        x = (before[constraint["region"]]["median"]-black)/(1-black)
                        y = (constraint["min"]+constraint["max"])/2
                        if 0 < x < 1 and 0 < y < 1:
                            mid = x*(y-1)/(2*x*y-y-x)
                            if 0 < mid < 1:
                                evaluate((min(16.0, max(-16.0, math.log(mid/(1-mid)))),))
                search_bounds = bounds
                # ponytail: finite coarse/refined search may miss feasible islands; expand only with corpus evidence.
                for level in range(4):
                    if not best_rank[0] and gentle_key([best[1]["command"], *best[1]["parameters"]]) == gentle_key(tokens_for(method, gentlest, black, landmarks)):
                        break
                    count = 17 if len(bounds) == 1 else (7 if level == 0 else 5)
                    grids = [np.linspace(lo, hi, count).tolist() for lo, hi in search_bounds]
                    candidates = sorted(itertools.product(*grids),
                                        key=lambda x: gentle_key(tokens_for(method, x, black, landmarks)))
                    for coordinates in candidates:
                        if not best_rank[0] and gentle_key(tokens_for(method, coordinates, black, landmarks)) >= gentle_key([best[1]["command"], *best[1]["parameters"]]):
                            break
                        evaluate(coordinates)
                        if not best_rank[0]:
                            break
                    if len(evaluated) >= evaluation_limit:
                        break
                    search_bounds = [(max(original[0], x-(hi-lo)/(count-1)),
                                      min(original[1], x+(hi-lo)/(count-1)))
                                     for x, (lo, hi), original in zip(best[0], search_bounds, bounds)]
                trace["evaluation_count"] = len(evaluated)
                trace["native_evaluation_count"] = oracle.count-trial_start
                trace["selected_parameters"] = best[1]
                trace["constraints"] = best[2]
                if best_rank[0]:
                    report["reason"] = f"No feasible parameters found for operator {index+1}"
                    break
                report["transfer_chain"].append(best[1])
                current, evidence = oracle.run(source, report["transfer_chain"])
                trace["selected_replay"] = evidence
                if full:
                    current_full, evidence = oracle.run(full, report["transfer_chain"])
                    trace["selected_full_replay"] = evidence
                    full_final = pixels(current_full)
                    star_constraints = [c for c in step["constraints"] if next(r["role"] for r in regions if r["id"] == c["region"]) == "stars"]
                    if (any(c["shadow_clip_rate"] >= SHADOW_LIMIT for c in clipping(full_final))
                        or not all(r["satisfied"] for r in check_constraints(statistics(full_final, regions), star_constraints))):
                        fail("Full-source prefix replay fails frozen scientific constraints")
                trace["native_evaluation_count"] = oracle.count-trial_start
                if not all(r["satisfied"] for r in check_constraints(statistics(pixels(current), regions), step["constraints"])):
                    fail("Complete prefix replay differs from the selected trial constraints")
            else:
                final = pixels(current)
                final_rows = check_constraints(statistics(final, regions), request["operators"][-1]["constraints"])
                if all(r["satisfied"] for r in final_rows) and all(c["shadow_clip_rate"] < SHADOW_LIMIT for c in clipping(final)):
                    report.update(status="solved", final_constraints=final_rows, final_clipping=clipping(final),
                                  final_sha256=sha256_file(current))
            unchanged()
            if time.monotonic() > deadline:
                raise TimeoutError("Stretch parameter solve timed out")
    except (ContractError, OSError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        report.update(status="timed_out" if isinstance(exc, TimeoutError) else "failed", reason=str(exc))
    try:
        unchanged()
    except (ContractError, OSError) as exc:
        report.update(status="failed", reason=str(exc))
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return {"status": report["status"], "parameters_path": str(report_path), "report": report}


def load_parameters(session, payload, path, source=None, chain=None, *, derived_source=None):
    parameters_path = session_path(session, path, must_exist=True, allowed_roots=("reports",))
    report = strict_json_bytes(parameters_path.read_bytes(), document="stretch parameters")
    from deep_sky_siril_stretch_stepwise import REPORT as STEPWISE_REPORT, load_parameters as load_stepwise
    if isinstance(report, dict) and report.get('schema') == STEPWISE_REPORT:
        if derived_source is not None:
            fail('Stepwise derived HOO sources are unsupported')
        return load_stepwise(session, payload, parameters_path, source, chain)
    from deep_sky_siril_stretch_v2 import REPORT, DERIVED
    schema = "starun-siril.derived-stretch-parameters.v1" if derived_source is not None else "starun-siril.stretch-parameters.v1"
    if not isinstance(report, dict) or report.get("schema") not in {schema, DERIVED if derived_source is not None else REPORT} or report.get("status") != "solved":
        fail("Only solved parameter reports can generate or authorize a stretch")
    if RUN_ID_PATTERN.fullmatch(str(report.get("run_id", ""))) is None or parameters_path != session/"reports"/report["run_id"]/"stretch-parameters.json":
        fail("Parameter report run ID/path mismatch")
    bindings = report.get("bindings")
    if (not isinstance(bindings, dict) or not {"request", "regions", "source", "runtime"} <= bindings.keys()
        or not isinstance(report.get("transfer_chain"), list) or not isinstance(report.get("request"), dict)):
        fail("Incomplete solved parameter report")
    request, bound_source, regions, _ = read_request(session, payload, bound_file(session, bindings["request"]))
    from deep_sky_siril_stretch_v2 import REQUEST, PROPOSAL_REQUEST
    from deep_sky_siril_stretch_profiles import REQUEST as PROFILE_REQUEST
    if (request['schema'] in {REQUEST, PROFILE_REQUEST, PROPOSAL_REQUEST}) != (report['schema'] in {REPORT, DERIVED}):
        fail('Parameter report cannot downgrade the protected request version')
    if request != report["request"] or bindings["source"] != {**request["source"], "size": bound_source.stat().st_size}:
        fail("Parameter request/source binding changed")
    if derived_source is not None:
        if bound_file(session, bindings.get("derived_source", {})) != derived_source:
            fail("Derived parameter source changed")
        bound_source = derived_source
    bound_file(session, bindings["regions"])
    if "full_source" in bindings:
        bound_file(session, bindings["full_source"], ("artifacts",))
    frozen = load_json(session/"reports/tool-probe.json")["tools"]["siril_cli"]
    if bindings["runtime"] != frozen or not fingerprint_matches(frozen["fingerprint"]):
        fail("Parameter report runtime differs from frozen Siril")
    selected = report["transfer_chain"]
    if len(selected) != len(request["operators"]) or [c["command"] for c in selected] != [s["method"] for s in request["operators"]]:
        fail("Parameter report changed the Agent-selected methods/order")
    if len(report.get("steps", [])) != len(selected):
        fail("Incomplete parameter step evidence")
    for index, step in enumerate(selected):
        trace = report["steps"][index]
        constraints = request["operators"][index]["constraints"]
        if report['schema'] in {REPORT, DERIVED}:
            from deep_sky_siril_stretch_v2 import full_is_diagnostic, subject_constraints, noise_is_diagnostic
            constraints = subject_constraints(constraints, regions,
                full_diagnostic=bool(bindings.get('full_source') or bindings.get('derived_source')) and full_is_diagnostic(payload))
        sky = {region['id'] for region in regions if region['role'] == 'background'}
        required_rows = [row for row in trace.get('constraints', []) if not (
            report['schema'] in {REPORT, DERIVED} and noise_is_diagnostic(payload)
            and row.get('region') in sky and row.get('statistic') in {'mad', 'hf_sigma2'})]
        if (trace.get("selected_parameters") != step
            or len(trace.get("constraints", [])) != len(constraints)
            or not all(r.get("satisfied") is True for r in required_rows)):
            fail("Selected parameters disagree with solved step evidence")
        evidence_records = [trial.get("evidence", {}) for trial in trace.get("trials", [])]
        evidence_records += [trace.get("selected_replay", {}), trace.get("selected_full_replay", {})]
        for evidence in evidence_records:
            for domain in (evidence, evidence.get("full_source", {})):
                for key in ("script", "log"):
                    if key in domain:
                        bound_file(session, domain[key])
        from deep_sky_siril_processing import validate_parameters, script_commands, transfer_chain
        validate_parameters([step["command"], *step["parameters"]], "stretch")
        for key, expected_source in (("selected_replay", bound_source),
                                     ("selected_full_replay", session/bindings["full_source"]["path"] if "full_source" in bindings else None)):
            if expected_source is None:
                continue
            evidence = trace.get(key)
            if not isinstance(evidence, dict) or "script" not in evidence or "log" not in evidence:
                fail("Missing selected native replay evidence")
            script_path = bound_file(session, evidence["script"])
            text = script_path.read_text(encoding="utf-8")
            commands = script_commands(text)
            loads = [row for row in commands if row[0] == "load"]
            saves = [row for row in commands if row[0] == "save"]
            if (len(loads) != 1 or Path(loads[0][1]) != expected_source or len(saves) != 1
                or transfer_chain(text, Path(saves[0][1]).resolve()) != selected[:index+1]):
                fail("Selected parameters disagree with the native replay script")
    if source is not None and source != bound_source or chain is not None and chain != selected:
        fail("SSF source or transfer chain differs from frozen parameters")
    if report['schema'] in {REPORT, DERIVED}:
        from deep_sky_siril_stretch_v2 import verify
        verify(session, report, bound_source, regions)
    return report, parameters_path, bound_source


def adaptive_binding(session, payload, source, text, chain, run_id):
    lines = [line.partition(":")[2].strip() for line in text.splitlines() if line.strip().startswith("# adaptive-stretch:")]
    if not lines:
        return None
    if len(lines) != 1:
        fail("Duplicate adaptive-stretch declarations")
    declaration = strict_json_bytes(lines[0].encode(), document="adaptive stretch declaration")
    parameters_path = bound_file(session, declaration)
    report, _, _ = load_parameters(session, payload, parameters_path, source, chain)
    from deep_sky_siril_stretch_profiles import routing_required
    if report["run_id"] != run_id and not routing_required(payload):
        fail("Adaptive parameter report belongs to another run")
    result = {"parameters": record(session, parameters_path), "bindings": report["bindings"]}
    comparisons = [line.partition(':')[2].strip() for line in text.splitlines()
                   if line.strip().startswith('# stretch-comparison:')]
    from deep_sky_siril_stretch_profiles import REQUEST as PROFILE_REQUEST, verify_comparison, TAIL_COMPARISON_SCHEMAS
    from deep_sky_siril_stretch_profiles import routing_required, verify_candidate_comparisons
    if routing_required(payload):
        bindings = [strict_json_bytes(value.encode(), document='stretch comparison declaration') for value in comparisons]
        verify_candidate_comparisons(session, payload, report, result['parameters'], bindings)
        result['comparisons'] = bindings
    elif report['request']['schema'] == PROFILE_REQUEST or 'strategy' in report['request'] or comparisons:
        if len(comparisons) != 1:
            fail('Profile-guided stretch requires its two-candidate comparison')
        binding = strict_json_bytes(comparisons[0].encode(), document='stretch comparison declaration')
        comparison = verify_comparison(session, payload, binding, result['parameters'])
        if requires_tail_comparison(payload,report,source) and comparison['schema'] not in TAIL_COMPARISON_SCHEMAS:
            fail('RGB MTF tail requires a same-prefix compare-tail report')
        result['comparison'] = binding
    elif requires_tail_comparison(payload, report, source):
        fail('RGB MTF tail requires a same-prefix compare-tail report')
    return result


def measure_adaptive_output(session, source, primary, binding):
    payload = load_json(session/"session.json")
    report, _, _ = load_parameters(session, payload, bound_file(session, binding["parameters"]), source)
    if binding["bindings"] != report["bindings"]:
        fail("Adaptive evidence changed during execution")
    if 'comparison' in binding:
        from deep_sky_siril_stretch_profiles import verify_comparison
        verify_comparison(session, payload, binding['comparison'], binding['parameters'])
    if 'comparisons' in binding:
        from deep_sky_siril_stretch_profiles import verify_candidate_comparisons
        verify_candidate_comparisons(session, payload, report, binding['parameters'], binding['comparisons'])
    from deep_sky_siril_stretch_v2 import REPORT, measure_output
    if report['schema'] in {REPORT, 'starun-siril.stretch-parameters.v3'}:
        return measure_output(session, source, primary, report)
    regions = load_json(bound_file(session, report["request"]["regions"]))["regions"]
    data = pixels(primary)
    raw = pixels(source)
    if data.shape != raw.shape:
        fail("Adaptive output changed geometry")
    rows = check_constraints(statistics(data, regions), report["request"]["operators"][-1]["constraints"])
    rates = clipping(data)
    if not all(row["satisfied"] for row in rows) or any(row["shadow_clip_rate"] >= SHADOW_LIMIT for row in rates):
        fail("Actual stretch output fails the frozen scientific constraints")
    return {"status": "passed", "constraints": rows, "clipping": rates, "requires_visual_review": True}


def requires_tail_comparison(payload, report, source):
    chain = report.get('transfer_chain', [])
    return (payload.get('execution_policy', {}).get('color_preservation') == 'vivid_preservation_v1'
            and len(chain) > 1 and chain[-1]['command'] == 'mtf' and pixels(source).shape[0] == 3)


def generate_stretch_assets(session_path_value, target_type=None, target_bg=None, *, parameters=None,
                            legacy_mtf=False, run_id=None, parent_path=None, comparison=None):
    from deep_sky_siril_session import effective_session_payload, load_session, execution_policy_is_current
    session, payload, manifest = load_session(session_path_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        fail('Historical sessions are replay/finalize-only; create a current session for new generation')
    if bool(parameters) == bool(legacy_mtf):
        fail("Provide a solved parameters report or explicitly select --legacy-mtf")
    from deep_sky_siril_stretch_profiles import routing_required, verify_candidate_comparisons
    if legacy_mtf and routing_required(payload):
        fail('New stretch generation requires a bound strategy; legacy MTF is replay-only')
    if parameters and parent_path is not None:
        fail("Request-based stretch takes its parent from the frozen request")
    declaration = ""
    if parameters:
        candidate_path = session_path(session, parameters, must_exist=True, allowed_roots=('reports',))
        candidate = load_json(candidate_path)
        if candidate.get('schema') == 'starun-siril.stretch-parameters.v3' and 'main' not in candidate.get('branches', {}):
            from deep_sky_siril_stretch_stepwise import generate_hdr
            return generate_hdr(session, payload, candidate_path, run_id, comparison)
        report, parameters_path, parent = load_parameters(session, payload, parameters)
        run_id = run_id or report["run_id"]
        chain = report["transfer_chain"]
        from deep_sky_siril_stretch_profiles import validate_body_chain
        validate_body_chain(payload, chain)
        lookup = report["request"]["manual_lookup"]
        reasons = [s["purpose"] for s in report["request"]["operators"]]
        if report['request']['schema'].endswith('.v1'):
            reasons.append('Explicit v1 request: legacy gentle selection without v2 ROI protection.')
        declaration = "# adaptive-stretch: "+json.dumps(record(session, parameters_path))+"\n"
        from deep_sky_siril_stretch_profiles import REQUEST as PROFILE_REQUEST, verify_comparison, TAIL_COMPARISON_SCHEMAS
        from deep_sky_siril_stretch_v2 import PROPOSAL_REQUEST
        if routing_required(payload):
            paths = [] if comparison is None else comparison if isinstance(comparison, (list, tuple)) else [comparison]
            bindings = [record(session, session_path(session, value, must_exist=True, allowed_roots=('reports',))) for value in paths]
            verify_candidate_comparisons(session, payload, report, record(session, parameters_path), bindings)
            declaration += ''.join('# stretch-comparison: '+json.dumps(binding)+'\n' for binding in bindings)
            reasons.append('Morphology-bound strategy; actual reference visibility and explicit comparison evidence govern adoption.')
        elif report['request']['schema'] == PROFILE_REQUEST or 'strategy' in report['request'] or comparison is not None:
            if isinstance(comparison, (list, tuple)):
                if len(comparison) != 1:
                    fail('Historical morphology generation requires one comparison')
                comparison = comparison[0]
            if comparison is None:
                fail('Review the baseline and morphology chain comparison before generation')
            comparison_path = session_path(session, comparison, must_exist=True, allowed_roots=('reports',))
            comparison_binding = record(session, comparison_path)
            compared = verify_comparison(session, payload, comparison_binding, record(session, parameters_path))
            if requires_tail_comparison(payload,report,parent) and compared['schema'] not in TAIL_COMPARISON_SCHEMAS:
                fail('RGB MTF tail requires a same-prefix compare-tail report')
            declaration += '# stretch-comparison: '+json.dumps(comparison_binding)+'\n'
            reasons.append('Morphology chain and single-method control share final constraints; actual comparative pixel review is required.')
        elif requires_tail_comparison(payload, report, parent):
            fail('RGB MTF tail requires a same-prefix compare-tail report before generation')
        elif report['request']['schema'].endswith('.v2'):
            reasons.append('Explicit v22-compatible request: ROI protection without v23 chain comparison.')
        elif report['request']['schema'] == PROPOSAL_REQUEST:
            declared = report['request'].get('profile')
            if declared == 'custom':
                reasons.append('Explicit custom-profile request: no morphology chain is defined or compared.')
            else:
                reasons.append(f'Declared {declared} profile is unbound: the operator chain was not derived '
                               'from preferred_chain and no v23 chain comparison exists.')
        if report.get('generation') == 'agent_proposal_v1':
            reasons.append('One explicit Agent factor proposal per operator; no parameter search or automatic adoption.')
        result_parameters = report
    else:
        run_id = run_id or '070-stretch'
        if comparison is not None:
            fail('Legacy MTF cannot declare a profile comparison')
        from deep_sky_siril_processing import source_state
        from deep_sky_siril_session import _verified_success_receipts
        if parent_path is not None:
            parent = Path(parent_path).expanduser()
        else:
            candidates = [r for r in _verified_success_receipts(session) if r["protocol"] == "stars.separate"]
            parent = session / candidates[-1]["primary_output"] if candidates else Path(payload["input"]["path"])
        if source_state(session, payload, parent)["domain"] != "linear":
            fail("Legacy stretch requires a linear accepted parent")
        result_parameters = calculate_adaptive_mtf(parent, target_type or payload["context"]["target_type"], target_bg)
        chain = [{"command": "mtf", "parameters": [format(result_parameters[k], ".17g") for k in ("shadow", "midtones", "highlight")]}]
        lookup = {"status": "not_needed", "reason": "Explicit legacy MTF protocol skeleton", "evidence": []}
        reasons = ["Explicit legacy-mtf: global statistics and fixed target background; actual visual review required."]
    full = accepted_parent(session, payload, parent)
    if parameters and report["bindings"].get("full_source") != (record(session, full) if full else None):
        fail("Parameter full-source binding differs from the accepted separation branch")
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        fail("Invalid stretch run ID")
    script = session_path(session, f"scripts/{run_id}.ssf", allowed_roots=("scripts",))
    provenance = script.with_suffix(".provenance.json")
    stem = "070-stretched" if run_id == "070-stretch" else run_id+"-stretched"
    primary = session_path(session, f"artifacts/{stem}.fit", allowed_roots=("artifacts",))
    preview = session_path(session, f"previews/{stem}.jpg", allowed_roots=("previews",))
    for path in (script, provenance, primary, preview, session/"runs"/(run_id+".json")):
        if path.exists() or path.is_symlink():
            fail(f"Output/evidence already exists: {path}")
    commands = "\n".join(" ".join([c["command"], *c["parameters"]]) for c in chain)
    content = ("requires 1.4.4 1.5.0\nset32bits\n"+declaration+"load "+json.dumps(str(parent))+"\n"+
               commands+"\nicc_assign sRGB\nstat main\nsave "+json.dumps(str(primary))+
               " -chksum\nsavejpg "+json.dumps(str(preview.with_suffix("")))+" 95\nclose\n")
    import hashlib
    prov = {"schema": "starun-siril.ssf-provenance.v1", "contract_version": "1", "run_id": run_id,
            "protocol": "stretch", "script": {"path": script.relative_to(session).as_posix(),
                                             "sha256": hashlib.sha256(content.encode()).hexdigest()},
            "references": protocol_reference_bindings("stretch"),
            "command_policy": {"path": "references/command-policy.json", "sha256": sha256_file(ROOT/"references/command-policy.json")},
            "manual_lookup": lookup, "rationale": {"applicability": f"Agent-selected stretch on {parent.name}",
                                                  "parameter_choices": reasons}}
    if parameters and report.get('schema') == 'starun-siril.stretch-parameters.v3':
        prov['references'].append({'path': 'references/stage7-stepwise.md',
            'sha256': sha256_file(ROOT/'references/stage7-stepwise.md'), 'role': 'supporting'})
    with script.open("x", encoding="utf-8") as stream:
        stream.write(content)
    with provenance.open("x", encoding="utf-8") as stream:
        json.dump(prov, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return {"script_path": str(script), "provenance_path": str(provenance), "parent_path": str(parent),
            "primary_output": str(primary), "preview_output": str(preview), "parameters": result_parameters}


def calculate_adaptive_hdr_parameters(
    parent_path,
    target_type=None,
    target_name=None,
    shadow_sigma_factor=2.8,
    gentle_target_bg=None,
    faint_target_bg=None,
    mask_low=0.15,
    mask_high=0.75,
    sigma=2.0,
):
    """Calculate dual MTF chains and smoothstep mask parameters for Masked Dual HDR."""
    data = pixels(Path(parent_path))
    med = float(np.median(data))
    mad = float(np.median(np.abs(data - med)))
    sigma_stat = 1.4826 * mad
    factor = number(shadow_sigma_factor)
    shadow = max(0.0, med - factor * sigma_stat)
    if shadow >= med:
        shadow = max(0.0, med - 1e-4)

    # 针对超高动态天体，温和分支保护核心，微弱分支提升暗弱云气/尘埃带
    t_type = (target_type or "").lower()
    if gentle_target_bg is None:
        gentle_bg = 0.12 if "galaxy" in t_type else 0.14
    else:
        gentle_bg = number(gentle_target_bg)

    if faint_target_bg is None:
        faint_bg = 0.24 if "galaxy" in t_type else 0.26
    else:
        faint_bg = number(faint_target_bg)

    x = min(max((med - shadow) / (1 - shadow), 1e-6), 1 - 1e-6)

    # 计算 gentle 中灰
    mid_gentle = x * (gentle_bg - 1) / (2 * x * gentle_bg - gentle_bg - x)
    mid_gentle = min(max(mid_gentle, PROTOCOL_MIN_MTF), PROTOCOL_MAX_MTF)

    # 计算 faint 中灰
    mid_faint = x * (faint_bg - 1) / (2 * x * faint_bg - faint_bg - x)
    mid_faint = min(max(mid_faint, PROTOCOL_MIN_MTF), PROTOCOL_MAX_MTF)

    return {
        "shadow": shadow,
        "gentle": {"shadow": shadow, "midtones": mid_gentle, "highlight": 1.0, "target_bg": gentle_bg},
        "faint": {"shadow": shadow, "midtones": mid_faint, "highlight": 1.0, "target_bg": faint_bg},
        "mask": {
            "mask_low": number(mask_low),
            "mask_high": number(mask_high),
            "sigma": number(sigma),
        },
        "diagnostics": {
            "median": med,
            "mad": mad,
            "sigma": sigma_stat,
            "shadow": shadow,
            "target_type": target_type,
            "target_name": target_name,
            "gentle_target_bg": gentle_bg,
            "faint_target_bg": faint_bg,
        },
    }


def generate_hdr_stretch_assets(
    session_path_value,
    parent_path=None,
    target_type=None,
    target_name=None,
    run_id="070-stretch",
    gentle_target_bg=None,
    faint_target_bg=None,
    mask_low=0.15,
    mask_high=0.75,
    sigma=2.0,
):
    """Generate SSF script, provenance and expected paths for Masked Dual HDR."""
    from deep_sky_siril_session import effective_session_payload, load_session, _verified_success_receipts
    from deep_sky_siril_processing import source_state
    from deep_sky_siril_artifacts import fits_geometry
    import hashlib

    session, payload, manifest = load_session(session_path_value)
    payload = effective_session_payload(session, payload, manifest)
    from deep_sky_siril_stretch_profiles import routing_required
    if routing_required(payload):
        fail('New HDR execution requires explicit native branch chains and a morphology strategy; automatic HDR generation is legacy-only')
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        fail("Invalid stretch run ID")

    if parent_path is None:
        candidates = [r for r in _verified_success_receipts(session) if r["protocol"] == "stars.separate"]
        parent = session / candidates[-1]["primary_output"] if candidates else Path(payload["input"]["path"])
    else:
        parent = Path(parent_path).resolve()

    if source_state(session, payload, parent)["domain"] != "linear":
        fail("HDR stretch requires a linear accepted parent")

    t_type = target_type or payload["context"].get("target_type")
    t_name = target_name or payload["context"].get("target_name")

    hdr_params = calculate_adaptive_hdr_parameters(
        parent,
        target_type=t_type,
        target_name=t_name,
        gentle_target_bg=gentle_target_bg,
        faint_target_bg=faint_target_bg,
        mask_low=mask_low,
        mask_high=mask_high,
        sigma=sigma,
    )

    geom = fits_geometry(parent)
    channels = geom["channels"]
    if channels not in (1, 3):
        fail("HDR stretch requires mono or RGB geometry")

    shadow = hdr_params["shadow"]
    gentle_mid = hdr_params["gentle"]["midtones"]
    faint_mid = hdr_params["faint"]["midtones"]
    m_low = hdr_params["mask"]["mask_low"]
    m_high = hdr_params["mask"]["mask_high"]
    m_sigma = hdr_params["mask"]["sigma"]
    m_span = m_high - m_low

    script = session_path(session, f"scripts/{run_id}.ssf", allowed_roots=("scripts",))
    provenance = script.with_suffix(".provenance.json")

    primary = session_path(session, "artifacts/070-hdr.fit", allowed_roots=("artifacts",))
    final_preview = session_path(session, "previews/070-hdr.jpg", allowed_roots=("previews",))

    decl_json = json.dumps({"mask_low": m_low, "mask_high": m_high, "sigma": m_sigma})
    declaration_line = f"# hdr-stretch: {decl_json}\n"

    if channels == 3:
        ssf_content = f"""requires 1.4.4 1.5.0
set32bits
{declaration_line}load "{parent}"
mtf {shadow:.17g} {gentle_mid:.17g} 1
icc_assign sRGB
save "{session}/artifacts/070-gentle" -chksum
savejpg "{session}/previews/070-gentle" 95
split "{session}/artifacts/070-gentle-r" "{session}/artifacts/070-gentle-g" "{session}/artifacts/070-gentle-b"
pm "0.2126 * $artifacts/070-gentle-r$ + 0.7152 * $artifacts/070-gentle-g$ + 0.0722 * $artifacts/070-gentle-b$" -nosum
save "{session}/artifacts/070-luminance" -chksum
close
load "{parent}"
mtf {shadow:.17g} {faint_mid:.17g} 1
icc_assign sRGB
save "{session}/artifacts/070-faint" -chksum
savejpg "{session}/previews/070-faint" 95
close
pm "min(1, max(0, ($artifacts/070-luminance$ - {m_low:.17g}) / {m_span:.17g}))^2 * (3 - 2 * min(1, max(0, ($artifacts/070-luminance$ - {m_low:.17g}) / {m_span:.17g})))" -nosum
gauss {m_sigma}
icc_assign sRGB
save "{session}/artifacts/070-mask" -chksum
savejpg "{session}/previews/070-mask" 95
rgbcomp "{session}/artifacts/070-mask.fit" "{session}/artifacts/070-mask.fit" "{session}/artifacts/070-mask.fit" "-out={session}/artifacts/070-mask-rgb" -nosum
pm "$artifacts/070-gentle$ * $artifacts/070-mask-rgb$ + $artifacts/070-faint$ * (1 - $artifacts/070-mask-rgb$)" -nosum
icc_assign sRGB
save "{session}/artifacts/070-hdr" -chksum
stat main
savejpg "{session}/previews/070-hdr" 95
close
"""
        expected_outputs = [
            session / "artifacts" / "070-gentle.fit",
            session / "artifacts" / "070-gentle-r.fit",
            session / "artifacts" / "070-gentle-g.fit",
            session / "artifacts" / "070-gentle-b.fit",
            session / "artifacts" / "070-luminance.fit",
            session / "artifacts" / "070-faint.fit",
            session / "artifacts" / "070-mask.fit",
            session / "artifacts" / "070-mask-rgb.fit",
            session / "artifacts" / "070-hdr.fit",
            session / "previews" / "070-gentle.jpg",
            session / "previews" / "070-faint.jpg",
            session / "previews" / "070-mask.jpg",
            session / "previews" / "070-hdr.jpg",
        ]
        preview_outputs = [
            session / "previews" / "070-gentle.jpg",
            session / "previews" / "070-faint.jpg",
            session / "previews" / "070-mask.jpg",
            session / "previews" / "070-hdr.jpg",
        ]
    else:
        ssf_content = f"""requires 1.4.4 1.5.0
set32bits
{declaration_line}load "{parent}"
mtf {shadow:.17g} {gentle_mid:.17g} 1
icc_assign sRGB
save "{session}/artifacts/070-gentle" -chksum
savejpg "{session}/previews/070-gentle" 95
close
load "{parent}"
mtf {shadow:.17g} {faint_mid:.17g} 1
icc_assign sRGB
save "{session}/artifacts/070-faint" -chksum
savejpg "{session}/previews/070-faint" 95
close
pm "min(1, max(0, ($artifacts/070-gentle$ - {m_low:.17g}) / {m_span:.17g}))^2 * (3 - 2 * min(1, max(0, ($artifacts/070-gentle$ - {m_low:.17g}) / {m_span:.17g})))" -nosum
gauss {m_sigma}
icc_assign sRGB
save "{session}/artifacts/070-mask" -chksum
savejpg "{session}/previews/070-mask" 95
pm "$artifacts/070-gentle$ * $artifacts/070-mask$ + $artifacts/070-faint$ * (1 - $artifacts/070-mask$)" -nosum
icc_assign sRGB
save "{session}/artifacts/070-hdr" -chksum
stat main
savejpg "{session}/previews/070-hdr" 95
close
"""
        expected_outputs = [
            session / "artifacts" / "070-gentle.fit",
            session / "artifacts" / "070-faint.fit",
            session / "artifacts" / "070-mask.fit",
            session / "artifacts" / "070-hdr.fit",
            session / "previews" / "070-gentle.jpg",
            session / "previews" / "070-faint.jpg",
            session / "previews" / "070-mask.jpg",
            session / "previews" / "070-hdr.jpg",
        ]
        preview_outputs = [
            session / "previews" / "070-gentle.jpg",
            session / "previews" / "070-faint.jpg",
            session / "previews" / "070-mask.jpg",
            session / "previews" / "070-hdr.jpg",
        ]

    for p in expected_outputs + [script, provenance]:
        if p.exists() or p.is_symlink():
            p.unlink()

    script.write_text(ssf_content, encoding="utf-8")

    prov = {
        "schema": "starun-siril.ssf-provenance.v1",
        "contract_version": "1",
        "run_id": run_id,
        "protocol": "stretch",
        "script": {
            "path": script.relative_to(session).as_posix(),
            "sha256": hashlib.sha256(ssf_content.encode()).hexdigest(),
        },
        "references": protocol_reference_bindings("stretch", variant="hdr"),
        "command_policy": {
            "path": "references/command-policy.json",
            "sha256": sha256_file(ROOT / "references/command-policy.json"),
        },
        "manual_lookup": {
            "status": "not_needed",
            "reason": "Explicit Masked Dual HDR protocol skeleton",
            "evidence": [],
        },
        "rationale": {
            "applicability": f"Adaptive Masked Dual HDR on {parent.name} ({t_name or t_type})",
            "parameter_choices": [
                f"Gentle chain targets core at bg={hdr_params['gentle']['target_bg']:.2f}; "
                f"Faint chain boosts periphery at bg={hdr_params['faint']['target_bg']:.2f}; "
                f"Smoothstep mask [{m_low}, {m_high}] with sigma={m_sigma}"
            ],
        },
    }
    provenance.write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")

    return {
        "script_path": str(script),
        "provenance_path": str(provenance),
        "parent_path": str(parent),
        "primary_output": str(primary),
        "expected_outputs": [str(p) for p in expected_outputs],
        "preview_outputs": [str(p) for p in preview_outputs],
        "parameters": hdr_params,
        "mode": "hdr",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    context = commands.add_parser('morphology-context', help='Prepare native-parent ROI context before choosing a profile')
    context.add_argument('--session', required=True, type=Path)
    context.add_argument('--source', required=True)
    context.add_argument('--regions', required=True, type=Path)
    context.add_argument('--preview', required=True, type=Path)
    context.add_argument('--run-id', required=True)
    focus = commands.add_parser('artifact-focus', help='Prepare review-only artifact areas using existing native previews')
    focus.add_argument('--session', required=True, type=Path)
    focus.add_argument('--focus', required=True, type=Path)
    focus.add_argument('--run-id', required=True)
    focus_target = focus.add_mutually_exclusive_group(required=True)
    focus_target.add_argument('--source')
    focus_target.add_argument('--parameters', type=Path)
    focus_target.add_argument('--run')
    baseline = commands.add_parser('baseline', help='Historical baseline inversion; new standalone body trials are retired')
    baseline.add_argument('--session', required=True, type=Path)
    baseline.add_argument('--request', required=True, type=Path)
    baseline.add_argument('--anchor-region', required=True)
    baseline.add_argument('--run-id', required=True)
    baseline.add_argument('--timeout', type=int, default=1800)
    suggest = commands.add_parser('suggest-mtf', help='Predict MTF parameters without running Siril')
    suggest.add_argument('--session', required=True, type=Path)
    suggest.add_argument('--source', required=True)
    suggest.add_argument('--regions', required=True, type=Path)
    suggest.add_argument('--anchor-region', required=True)
    suggest.add_argument('--target-median', required=True, type=float)
    for name, help_text in [('init-request', 'Freeze a morphology-bound stretch proposal'),
                            ('preflight', 'Review every step constraint without creating a plan or running Siril')]:
        initialize = commands.add_parser(name, help=help_text)
        initialize.add_argument('--session', required=True, type=Path)
        initialize.add_argument('--source', required=True)
        initialize.add_argument('--regions', required=True, type=Path)
        initialize.add_argument('--goals', required=True, type=Path)
        initialize.add_argument('--run-id', required=True)
        execution = initialize.add_mutually_exclusive_group()
        execution.add_argument('--stepwise', action='store_true', default=None, help='Use Agent-reviewed prefix feedback with v4 goals (default except HOO)')
        execution.add_argument('--batch', dest='stepwise', action='store_false', help='Explicitly evaluate a complete multistage chain with v3 goals')
        initialize.add_argument('--search', action='store_true', help='Explicit bounded-search strategy using v1 goals')
        initialize.add_argument('--mode', choices=('profile', 'exception'), default='profile')
    compare = commands.add_parser('compare', help='Compare both native strategy attempts without accepting either')
    compare.add_argument('--session', required=True, type=Path)
    compare.add_argument('--strategy', required=True, type=Path)
    compare.add_argument('--parameters', required=True, action='append', type=Path)
    tail = commands.add_parser('compare-tail', help='Compare an explicit MTF tail with a same-prefix luminance GHS tail')
    tail.add_argument('--session', required=True, type=Path)
    tail.add_argument('--mtf-parameters', required=True, type=Path)
    tail.add_argument('--luminance-parameters', required=True, type=Path)
    tail.add_argument('--run-id', required=True)
    solve = commands.add_parser("solve", help="Evaluate explicit v4 factors once, or search an explicit legacy v1/v2/v3 request")
    solve.add_argument("--session", required=True, type=Path)
    solve.add_argument("--request", required=True, type=Path)
    solve.add_argument("--run-id", required=True)
    solve.add_argument("--timeout", type=int, default=1800)
    finish = commands.add_parser('finish', help='Explicitly complete an ordinary candidate or HDR branch')
    finish.add_argument('--session', required=True, type=Path)
    finish.add_argument('--request', required=True, type=Path)
    finish.add_argument('--run-id', required=True)
    generate = commands.add_parser("generate", help="Generate SSF without executing Siril")
    generate.add_argument("--session", required=True, type=Path)
    mode = generate.add_mutually_exclusive_group(required=True)
    mode.add_argument("--parameters", type=Path)
    mode.add_argument("--legacy-mtf", action="store_true")
    generate.add_argument("--run-id", default=None)
    generate.add_argument("--type", default=None)
    generate.add_argument("--target-bg", type=float)
    generate.add_argument('--comparison', type=Path, action='append')
    calculate = commands.add_parser("calculate", help="Explicit legacy MTF calculation")
    calculate.add_argument("--legacy-mtf", action="store_true", required=True)
    calculate.add_argument("--input", required=True, type=Path)
    calculate.add_argument("--type", default="unknown")
    calculate.add_argument("--target-bg", type=float)
    args = parser.parse_args()
    try:
        if args.command == 'morphology-context':
            from deep_sky_siril_morphology import prepare_context
            result = prepare_context(args.session,args.source,args.regions,args.preview,args.run_id)
        elif args.command == 'artifact-focus':
            from deep_sky_siril_artifact_focus import prepare as prepare_focus
            result = prepare_focus(args.session, args.focus, args.run_id, source=args.source, parameters=args.parameters, run=args.run)
        elif args.command == 'baseline':
            from deep_sky_siril_visibility import propose
            result = propose(args.session, args.request, args.anchor_region, args.run_id, args.timeout)
        elif args.command == 'suggest-mtf':
            from deep_sky_siril_stretch_preflight import suggest_mtf
            result = suggest_mtf(args.session, args.source, args.regions, args.anchor_region, args.target_median)
        elif args.command == 'init-request':
            from deep_sky_siril_stretch_profiles import init_requests
            result = init_requests(args.session, args.source, args.regions, args.goals, args.run_id, search=args.search, mode=args.mode, stepwise=args.stepwise)
        elif args.command == 'preflight':
            from deep_sky_siril_stretch_preflight import preflight_requests
            result = preflight_requests(args.session, args.source, args.regions, args.goals, args.run_id, search=args.search, mode=args.mode, stepwise=args.stepwise)
        elif args.command == 'compare':
            from deep_sky_siril_stretch_profiles import compare as compare_candidates
            result = compare_candidates(args.session, args.strategy, args.parameters)
        elif args.command == 'compare-tail':
            from deep_sky_siril_stretch_profiles import compare_tail
            result = compare_tail(args.session, args.mtf_parameters, args.luminance_parameters, args.run_id)
        elif args.command == "solve":
            result = solve_stretch_parameters(args.session, args.request, args.run_id, args.timeout)
        elif args.command == 'finish':
            from deep_sky_siril_stretch_stepwise import finish as finish_stepwise
            result = finish_stepwise(args.session, args.request, args.run_id)
        elif args.command == "generate":
            result = generate_stretch_assets(args.session, args.type, args.target_bg, parameters=args.parameters,
                                             legacy_mtf=args.legacy_mtf, run_id=args.run_id, comparison=args.comparison)
        else:
            result = calculate_adaptive_mtf(args.input, args.type, args.target_bg)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result.get("status", "solved") in {'solved', 'prepared', 'comparison_ready', 'proposed', 'ready', 'awaiting_next_step', 'branch_complete'} else 1
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps(getattr(exc, 'preflight', {"status": "failed", "reason": str(exc)}), allow_nan=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
