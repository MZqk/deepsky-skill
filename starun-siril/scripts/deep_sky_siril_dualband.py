#!/usr/bin/env python3
"""Evidence-bound Ha/OIII display estimates with independent curves and RGB stars."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np

from deep_sky_siril_contract import (
    ContractError, RUN_ID_PATTERN, atomic_write_json, fingerprint_matches, load_json,
    protocol_reference_bindings, session_path, sha256_file,
)
from deep_sky_siril_stretch import (
    accepted_parent, bound_file, check_constraints, clipping, load_parameters, pixels,
    read_request, record, solve_stretch_parameters, source_file, statistics,
)
from siril_manual_bundle import strict_json_bytes

POLICY = 'hoo_proxy_rgb_stars_v1'
SCHEMA = 'starun-siril.dualband-parameters.v1'
ROLES = ('rgb', 'ha', 'oiii')
V4 = 'starun-siril.dualband-request.v4'


def prepare(session_value, source_value, run_id, display_mtf, filter_evidence=None, timeout=1800):
    """Freeze the actual native OIII estimate before any denoise proposal exists."""
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    from deep_sky_siril_processing import source_state
    from deep_sky_siril_denoise import display_parameters
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        fail('Historical sessions are replay/finalize-only')
    display_parameters(display_mtf)
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        fail('Invalid preparation run ID')
    if type(timeout) not in (int, float) or not np.isfinite(timeout) or not 1 <= timeout <= 86400:
        fail('Invalid preparation timeout')
    source = session_path(session, source_value, must_exist=True, allowed_roots=('artifacts',))
    state = source_state(session, payload, source)
    reason = applicability(payload, state, filter_evidence)
    if reason:
        fail(reason)
    full = accepted_parent(session, payload, source)
    if pixels(source).shape[0] != 3 or full is None:
        fail('Preparation requires the original RGB separation pair')
    directory = session_path(session, 'reports/'+run_id, allowed_roots=('reports',))
    if directory.exists() or (session/'runs'/(run_id+'.json')).exists():
        fail('Prepared input already exists; use a new run ID')
    directory.mkdir()
    h, g, b, o = [directory/(k+'.fit') for k in ('ha', 'g', 'b', 'oiii')]
    preview = directory/'oiii-parent.jpg'
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    rows = _extraction_rows(source, h, g, b, o)
    rows += ['close', 'load '+json.dumps(str(o)), 'mtf '+' '.join(format(v, '.17g') for v in display_mtf),
             'savejpg '+json.dumps(str(preview.with_suffix('')))+' 95']
    extraction = _native(session, runtime, directory, 'prepare', rows, preview, time.monotonic()+timeout)
    report = {'schema': 'starun-siril.dualband-prepared.v1', 'source': record(session, source),
              'full_source': record(session, full), 'runtime': runtime, 'filter_evidence': filter_evidence,
              'separation': record(session, session/'runs'/(state['run']['id']+'.json')),
              'separation_review': record(session, session/'reviews'/(state['run']['id']+'.json')),
              'derived_sources': {'ha': record(session, h), 'oiii': record(session, o)},
              'extraction': extraction, 'display_mtf': display_mtf, 'preview': record(session, preview)}
    path = directory/'dualband-prepared.json'
    atomic_write_json(path, report)
    prepared_input(session, payload, record(session, path))
    return {'prepared': record(session, path), 'oiii': {k: report['derived_sources']['oiii'][k] for k in ('path', 'sha256')},
            'preview': str(preview)}


def _extraction_rows(source, h, g, b, o):
    q = lambda p: json.dumps(str(p))
    v = lambda p: '$'+str(p.with_suffix(''))+'$'
    return ['load '+q(source), 'fmul 1', 'split '+' '.join(q(p.with_suffix('')) for p in (h, g, b)),
            'pm '+q(f'({v(g)} + {v(b)}) * 0.5')+' -nosum', 'save '+q(o.with_suffix(''))+' -chksum']


def prepared_input(session, payload, binding, *, verify_parent=True):
    from deep_sky_siril_processing import script_commands
    from deep_sky_siril_denoise import display_parameters
    path = bound_file(session, binding)
    report = load_json(path)
    if report.get('schema') != 'starun-siril.dualband-prepared.v1':
        fail('Invalid prepared OIII context')
    source = bound_file(session, report['source'], ('artifacts',))
    separation_path = bound_file(session, report['separation'], ('runs',))
    separation = load_json(separation_path)
    review = load_json(bound_file(session, report['separation_review'], ('reviews',)))
    if (applicability(payload, {'domain': 'linear', 'run': separation}, report.get('filter_evidence'))
        or separation.get('primary_output') != report['source']['path'] or review.get('verdict') != 'accept'
        or review.get('run_receipt_sha256') != sha256_file(separation_path)):
        fail('Prepared OIII context lost its accepted separation parent')
    full = bound_file(session, report['full_source'], ('artifacts',))
    if verify_parent and accepted_parent(session, payload, source) != full:
        fail('Prepared OIII full source changed')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if report['runtime'] != runtime or not fingerprint_matches(runtime['fingerprint']):
        fail('Prepared OIII runtime changed')
    derived = {key: bound_file(session, value) for key, value in report['derived_sources'].items()}
    original = pixels(source)
    if (set(derived) != {'ha', 'oiii'} or np.max(np.abs(pixels(derived['ha'])-original[:1])) > 1e-7
        or np.max(np.abs(pixels(derived['oiii'])-(original[1:2]+original[2:3])*.5)) > 1e-7):
        fail('Prepared signed OIII/Ha formula changed')
    script, log = [bound_file(session, report['extraction'][key]) for key in ('script', 'log')]
    preview = bound_file(session, report['preview'])
    display_parameters(report['display_mtf'])
    directory = path.parent
    expected = ['requires 1.4.4 1.5.0', 'set32bits', *_extraction_rows(source, derived['ha'], directory/'g.fit', directory/'b.fit', derived['oiii']),
                'close', 'load '+json.dumps(str(derived['oiii'])),
                'mtf '+' '.join(format(v, '.17g') for v in report['display_mtf']),
                'savejpg '+json.dumps(str(preview.with_suffix('')))+' 95', 'close']
    if script_commands(script.read_text()) != script_commands('\n'.join(expected)):
        fail('Prepared OIII recipe changed')
    return report, path


def fail(message):
    raise ContractError('dualband_invalid', message)


def _explicit_filter(filter_evidence):
    return (isinstance(filter_evidence, dict) and set(filter_evidence) == {'source', 'lines', 'evidence'}
                and filter_evidence['source'] in {'user', 'acquisition'}
                and isinstance(filter_evidence['lines'], list)
                and all(isinstance(line, str) for line in filter_evidence['lines']) and set(filter_evidence['lines']) == {'H-alpha', 'OIII'}
                and isinstance(filter_evidence['evidence'], str) and bool(filter_evidence['evidence'].strip()))


def applicability(payload, state, filter_evidence=None):
    context = payload['context']
    profile = context.get('filter_profile', {})
    if payload.get('execution_policy', {}).get('dualband') != POLICY or context.get('dualband') != 'auto':
        return 'Dualband processing is disabled or unavailable in this frozen policy'
    if state['domain'] != 'linear' or context.get('channel_mode') != 'dualband-osc':
        return 'A confirmed linear dualband OSC parent is required'
    explicit = _explicit_filter(filter_evidence)
    if not explicit and (profile.get('class') != 'dual_band' or set(profile.get('lines', [])) != {'H-alpha', 'OIII'}
        or profile.get('source') != 'header'):
        return 'Trusted Ha/OIII filter provenance is unavailable'
    if context.get('channel_map') is not None:
        return 'Approximate extraction requires native OSC RGB, without an existing line mapping'
    if not state.get('run') or state['run']['protocol'] != 'stars.separate':
        return 'An accepted original linear starless separation parent is required'
    return None


def declaration(text):
    rows = [s.strip() for s in text.splitlines() if s.strip().startswith('# dualband-stretch')]
    if not rows:
        return None
    if len(rows) != 1 or not rows[0].startswith('# dualband-stretch:'):
        fail('Exactly one dualband-stretch declaration is required')
    value = strict_json_bytes(rows[0].split(':', 1)[1].encode(), document='dualband declaration')
    if not isinstance(value, dict) or set(value) != {'parameters'} or not isinstance(value['parameters'], str):
        fail('Dualband declaration must bind one parameter report')
    return value['parameters']


def _native(session, runtime, directory, name, rows, output, deadline, *, scientific_attempt=None):
    from deep_sky_siril_artifacts import diagnose_siril_log
    from deep_sky_siril_tooling import _subprocess_environment
    if not fingerprint_matches(runtime['fingerprint']):
        fail('Frozen Siril runtime changed')
    script, log = directory/(name+'.ssf'), directory/(name+'.log')
    script.write_text('\n'.join(['requires 1.4.4 1.5.0', 'set32bits', *rows, 'close'])+'\n')
    config = directory/'trial.ini'
    if not config.exists():
        config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\nscript_check_requires=true\n'
                          '[gui]\nicc_autoassignment=0\nuse_scripts_repository=false\n'
                          'use_spcc_repository=false\nauto_update_scripts=false\nauto_update_spcc=false\n')
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Dualband solve timed out')
    if scientific_attempt is not None:
        from deep_sky_siril_stretch_profiles import claim_attempt
        claim_attempt(*scientific_attempt)
    try:
        result = subprocess.run([runtime['path'], '--initfile='+str(config), '--directory='+str(directory),
                                 '--offline', '--script='+str(script)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                timeout=remaining, env=_subprocess_environment('siril_runtime'))
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b''
        log.write_text(raw.decode(errors='replace') if isinstance(raw, bytes) else raw)
        raise TimeoutError('Dualband solve timed out') from exc
    log.write_text(result.stdout)
    diagnostics = diagnose_siril_log(result.stdout, exit_code=result.returncode, timed_out=False,
                                     execution_valid=output.is_file())
    if result.returncode or diagnostics['status'] == 'failed' or not output.is_file():
        fail(f'Native dualband trial failed: {log}')
    if not fingerprint_matches(runtime['fingerprint']):
        fail('Frozen Siril runtime changed during trial')
    return {'script': record(session, script), 'log': record(session, log)}


def _request(session, payload, path):
    path = session_path(session, path, must_exist=True, allowed_roots=('reports',))
    request = strict_json_bytes(path.read_bytes(), document='dualband request')
    required = {'schema', 'source', 'regions', 'operators', 'oiii_denoise', 'manual_lookup'}
    protected = isinstance(request, dict) and request.get('schema') in {'starun-siril.dualband-request.v2', 'starun-siril.dualband-request.v3', V4}
    if protected:
        required |= {'profile', 'targets', 'core_absence'}
    from deep_sky_siril_stretch_profiles import routing_required, validate_external_strategy
    if routing_required(payload):
        if not isinstance(request, dict) or request.get('schema') != V4 or 'strategy' not in request:
            fail('New HOO requests require v4 factors and a bound morphology strategy')
        required.add('strategy')
    if not isinstance(request, dict) or not required <= set(request) <= required | {'filter_evidence'} or request['schema'] not in {'starun-siril.dualband-request.v1', 'starun-siril.dualband-request.v2', 'starun-siril.dualband-request.v3', V4}:
        fail('Invalid dualband request fields or schema')
    if not isinstance(request['operators'], dict) or set(request['operators']) != set(ROLES):
        fail('Declare RGB, Ha and OIII operator constraints')
    if 'filter_evidence' in request and not _explicit_filter(request['filter_evidence']):
        fail('Explicit filter evidence requires a real user/acquisition source and the Ha/OIII lines')
    denoise = request['oiii_denoise']
    fields = {'enabled', 'reason', 'proposal'} if request['schema'] == V4 and isinstance(denoise, dict) and denoise.get('enabled') is True else {'enabled', 'reason'}
    if (not isinstance(denoise, dict) or set(denoise) != fields
        or type(denoise['enabled']) is not bool or not isinstance(denoise['reason'], str) or not denoise['reason'].strip()):
        fail('Declare whether OIII needs denoising and the actual noise evidence')
    if payload.get('execution_policy', {}).get('linear_denoise') == 'agent_proposal_v1' and request['schema'] != V4:
        fail('New HOO execution requires v4; historical v1-v3 requests remain replay-only')
    if request['schema'] == V4 and payload.get('execution_policy', {}).get('linear_denoise') != 'agent_proposal_v1':
        fail('HOO v4 requires the frozen Agent-proposed denoise policy')
    proposal = request['schema'] in {'starun-siril.dualband-request.v3', V4}
    if proposal:
        from deep_sky_siril_stretch_v2 import PROPOSAL_POLICY, proposal_tokens
        if payload.get('execution_policy', {}).get('stretch_parameters') != PROPOSAL_POLICY:
            fail('Dualband factor proposals require their frozen parameter policy')
        for operators in request['operators'].values():
            if not isinstance(operators, list):
                fail('Each dualband proposal needs an operator list')
            for step in operators:
                if not isinstance(step, dict):
                    fail('Proposed operators must be objects')
                proposal_tokens(step, 0., {'LP': 0., 'SP': .5, 'HP': 1.})
    source = source_file(session, payload, request['source'])
    region_path = bound_file(session, request['regions'])
    if routing_required(payload):
        plan, prepared = validate_external_strategy(session, payload, source, request['strategy'], channels=request['operators'])
        if (request['source'] != plan['source'] or request['regions'] != plan['regions']
            or any(request[key] != prepared['ha'][key] for key in ('profile', 'targets', 'core_absence', 'manual_lookup'))):
            fail('HOO strategy source and regions differ from the frozen plan')
    if protected:
        from deep_sky_siril_stretch_v2 import validate
        from deep_sky_siril_metrics import measure_regions
        if measure_regions(source, None, region_path)['status'] != 'measured':
            fail('Protected dualband regions are not finite geometry-bound measurements')
        regions = load_json(region_path)['regions']
        for role in ROLES:
            validate({**request, 'operators': request['operators'][role]}, regions, full=role == 'rgb', full_diagnostic=proposal)
    return request, source, path


def _denoise_proposal(session, payload, request, source, *, verify_parent=True):
    from deep_sky_siril_denoise import request_binding
    path = bound_file(session, request['oiii_denoise']['proposal'])
    proposal, binding, parent, command = request_binding(session, payload, path, verify_parent=verify_parent)
    if command is None or 'derived_context' not in proposal or binding['request'] != request['oiii_denoise']['proposal']:
        fail('Enabled OIII denoise requires an executable common proposal bound to a prepared OIII input')
    prepared, _ = prepared_input(session, payload, proposal['derived_context'], verify_parent=verify_parent)
    if prepared['source'] != record(session, source) or prepared['display_mtf'] != binding['display_mtf']:
        fail('OIII proposal differs from the actual HOO parent or prepared display')
    return binding, parent, command, prepared


def solve(session_value, request_value, run_id, timeout=1800):
    from deep_sky_siril_session import effective_session_payload, load_session, execution_policy_is_current
    from deep_sky_siril_processing import source_state
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        fail('Historical sessions are replay/finalize-only; create a current session for new dualband processing')
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id) or len(run_id) > 55:
        fail('Invalid dualband run ID')
    if type(timeout) not in (int, float) or not np.isfinite(timeout) or not 1 <= timeout <= 86400:
        fail('Invalid dualband timeout')
    deadline = time.monotonic()+timeout
    request, source, request_path = _request(session, payload, request_value)
    state = source_state(session, payload, source)
    reason = applicability(payload, state, request.get('filter_evidence'))
    directory = session_path(session, f'reports/{run_id}', allowed_roots=('reports',))
    if directory.exists():
        fail('Dualband evidence already exists; use a new run ID')
    directory.mkdir()
    report_path = directory/'dualband-parameters.json'
    report = {'schema': SCHEMA.replace('.v1', '.'+request['schema'].rsplit('.', 1)[1]),
              'policy': POLICY, 'run_id': run_id, 'status': 'skipped',
              'requires_visual_review': True, 'band_semantics': 'display_estimates',
              'star_color_route': 'original_dualband_rgb', 'request': request,
              'bindings': {'request': record(session, request_path), 'source': record(session, source),
                           'regions': request['regions']}, 'chains': {}}
    try:
        if reason:
            report['reason'] = reason
        else:
            full = accepted_parent(session, payload, source)
            if pixels(source).shape[0] != 3 or full is None:
                fail('Dualband estimates require the original RGB separation pair')
            runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
            from deep_sky_siril_stretch_profiles import routing_required, load_plan, candidate_run_id
            report['bindings'].update(runtime=runtime, full_source=record(session, full),
                separation=record(session, session/'runs'/(state['run']['id']+'.json')),
                separation_review=record(session, session/'reviews'/(state['run']['id']+'.json')))
            trial_dir = directory/'extraction-trials'
            trial_dir.mkdir()
            h, g, b, o, d = [trial_dir/(k+'.fit') for k in ('ha', 'g', 'b', 'oiii', 'oiii-denoised')]
            q = lambda p: json.dumps(str(p))
            v = lambda p: '$'+str(p.with_suffix(''))+'$'
            rows = ['load '+q(source), 'fmul 1', 'split '+' '.join(q(p.with_suffix('')) for p in (h, g, b)),
                    'pm '+q(f'({v(g)} + {v(b)}) * 0.5')+' -nosum', 'save '+q(o.with_suffix(''))+' -chksum']
            report['extraction'] = _native(session, runtime, trial_dir, 'extract', rows, o, deadline,
                scientific_attempt=(session, payload, run_id, request))
            original = pixels(source)
            if (np.max(np.abs(pixels(h)-original[:1])) > 1e-7
                or np.max(np.abs(pixels(o)-(original[1:2]+original[2:3])*.5)) > 1e-7):
                fail('Native extraction altered the signed linear estimates')
            report['derived_sources'] = {'ha': record(session, h), 'oiii': record(session, o)}
            if request['oiii_denoise']['enabled']:
                if request['schema'] == V4:
                    binding, prepared_o, command, prepared = _denoise_proposal(session, payload, request, source)
                    if np.max(np.abs(pixels(o)-pixels(prepared_o))) > 1e-7:
                        fail('OIII trial does not match the prepared input')
                    report['linear_denoise'] = binding
                    view = trial_dir/'denoise-candidate.jpg'
                    report['denoise_display'] = {'parent_preview': prepared['preview']['path'],
                                                 'candidate_preview': str(view.relative_to(session))}
                    rows = ['load '+q(o), ' '.join(command), 'save '+q(d.with_suffix(''))+' -chksum',
                            'close', 'load '+q(d), 'mtf '+' '.join(format(v, '.17g') for v in binding['display_mtf']),
                            'savejpg '+q(view.with_suffix(''))+' 95']
                else:
                    rows = ['load '+q(o), 'denoise -mod=0.35', 'save '+q(d.with_suffix(''))+' -chksum']
                report['denoise_trial'] = _native(session, runtime, trial_dir, 'denoise', rows, d, deadline)
                report['derived_sources']['oiii_denoised'] = record(session, d)
                if request['schema'] == V4:
                    from deep_sky_siril_denoise import measure
                    report['denoise_diagnostics'] = measure(session, o, d,
                        {**binding, 'run_id': run_id+'-oiii-trial', 'display': report['denoise_display']}, write=True)
            for role in ROLES:
                child_id = run_id+'-'+role
                child_request = {k: request[k] for k in ('source', 'regions', 'manual_lookup')}
                child_request.update(schema='starun-siril.stretch-request.v1', operators=request['operators'][role])
                if request['schema'] in {'starun-siril.dualband-request.v2', 'starun-siril.dualband-request.v3', V4}:
                    child_request.update(schema='starun-siril.stretch-request.v4' if request['schema'] in {'starun-siril.dualband-request.v3', V4} else 'starun-siril.stretch-request.v2',
                                         **{k: request[k] for k in ('profile', 'targets', 'core_absence')})
                child_path = directory/(role+'-request.json')
                if routing_required(payload):
                    plan, prepared_requests = load_plan(session, payload, request['strategy'])
                    child_id = candidate_run_id(plan['run_id'], role)
                    child_path = session/'reports'/plan['run_id']/(role+'-request.json')
                    child_request = prepared_requests[role]
                else:
                    atomic_write_json(child_path, child_request)
                read_request(session, payload, child_path)
                if any(s.get('method') == 'autostretch' for s in child_request['operators']):
                    fail('All three dualband curves must be explicit replayable operators')
                derived = None if role == 'rgb' else (h if role == 'ha' else d if request['oiii_denoise']['enabled'] else o)
                result = solve_stretch_parameters(session, child_path, child_id,
                    max(.001, deadline-time.monotonic()), _derived_source=derived, _shared_attempt=run_id if routing_required(payload) else None)
                report['chains'][role] = record(session, Path(result['parameters_path']))
                if result['status'] != 'solved':
                    report.update(status=result['status'], reason=f'{role}: '+result['report'].get('reason', 'No feasible parameters'))
                    break
            else:
                report['status'] = 'solved'
            source_file(session, payload, request['source'])
            bound_file(session, report['bindings']['request'])
            bound_file(session, report['bindings']['regions'])
    except (ContractError, OSError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        report.update(status='timed_out' if isinstance(exc, TimeoutError) else 'failed', reason=str(exc))
    atomic_write_json(report_path, report)
    return {'status': report['status'], 'report': str(report_path), 'reason': report.get('reason')}


def parameter_binding(session, payload, path, source=None, run_id=None, *, verify_parent=True):
    path = session_path(session, path, must_exist=True, allowed_roots=('reports',))
    report = strict_json_bytes(path.read_bytes(), document='dualband parameters')
    if (not isinstance(report, dict) or report.get('schema') not in {SCHEMA, SCHEMA.replace('.v1', '.v2'), SCHEMA.replace('.v1', '.v3'), SCHEMA.replace('.v1', '.v4')} or report.get('policy') != POLICY
        or report.get('status') != 'solved' or report.get('requires_visual_review') is not True
        or report.get('band_semantics') != 'display_estimates' or report.get('star_color_route') != 'original_dualband_rgb'
        or not RUN_ID_PATTERN.fullmatch(str(report.get('run_id', '')))
        or path != session/'reports'/report['run_id']/'dualband-parameters.json'
        or run_id is not None and run_id != report['run_id']):
        fail('Only the matching solved dualband report can authorize this run')
    bindings = report['bindings']
    request, parent, _ = _request(session, payload, bound_file(session, bindings['request']))
    if report['schema'].rsplit('.', 1)[1] != request['schema'].rsplit('.', 1)[1]:
        fail('Dualband report cannot downgrade the protected request version')
    if request != report['request'] or record(session, parent) != bindings['source'] or source is not None and parent != source:
        fail('Dualband request or scientific source changed')
    separation_path = bound_file(session, bindings['separation'], ('runs',))
    separation = load_json(separation_path)
    review = load_json(bound_file(session, bindings['separation_review'], ('reviews',)))
    state = {'domain': 'linear', 'run': separation}
    reason = applicability(payload, state, request.get('filter_evidence'))
    if reason or separation.get('primary_output') != parent.relative_to(session).as_posix():
        fail(reason or 'Dualband separation parent changed')
    if review.get('verdict') != 'accept' or review.get('run_receipt_sha256') != sha256_file(separation_path):
        fail('Dualband separation is not accepted')
    full = bound_file(session, bindings['full_source'], ('artifacts',))
    if verify_parent:
        if accepted_parent(session, payload, parent) != full:
            fail('Dualband full source differs from accepted separation')
    if bindings['regions'] != request['regions']:
        fail('Dualband regions changed')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if bindings['runtime'] != runtime or not fingerprint_matches(runtime['fingerprint']):
        fail('Dualband runtime differs from frozen Siril')
    for name in ('extraction', 'denoise_trial'):
        if name in report:
            for item in report[name].values():
                bound_file(session, item)
    data = pixels(parent)
    derived = {k: bound_file(session, item) for k, item in report['derived_sources'].items()}
    if (np.max(np.abs(pixels(derived['ha'])-data[:1])) > 1e-7
        or np.max(np.abs(pixels(derived['oiii'])-(data[1:2]+data[2:3])*.5)) > 1e-7):
        fail('Derived signed linear estimates changed')
    if request['schema'] == V4 and request['oiii_denoise']['enabled']:
        from deep_sky_siril_denoise import measure
        from deep_sky_siril_processing import script_commands
        proposal, _, command, prepared = _denoise_proposal(session, payload, request, parent, verify_parent=verify_parent)
        trial = bound_file(session, report['denoise_trial']['script'])
        candidate_preview = session_path(session, report['denoise_display']['candidate_preview'], must_exist=True, allowed_roots=('reports',))
        q = lambda p: json.dumps(str(p))
        expected = ['requires 1.4.4 1.5.0', 'set32bits', 'load '+q(derived['oiii']), ' '.join(command),
                    'save '+q(derived['oiii_denoised'].with_suffix(''))+' -chksum', 'close',
                    'load '+q(derived['oiii_denoised']), 'mtf '+' '.join(format(v, '.17g') for v in proposal['display_mtf']),
                    'savejpg '+q(candidate_preview.with_suffix(''))+' 95', 'close']
        if (proposal != report.get('linear_denoise') or report['denoise_display']['parent_preview'] != prepared['preview']['path']
            or script_commands(trial.read_text()) != script_commands('\n'.join(expected))
            or measure(session, derived['oiii'], derived['oiii_denoised'],
                {**proposal, 'run_id': report['run_id']+'-oiii-trial', 'display': report['denoise_display']}) != report['denoise_diagnostics']):
            fail('OIII trial parameters, recipe or diagnostics differ from the common proposal')
    chains = {}
    for role in ROLES:
        prepared = None if role == 'rgb' else derived['ha' if role == 'ha' else
            'oiii_denoised' if request['oiii_denoise']['enabled'] else 'oiii']
        child, _, _ = load_parameters(session, payload, bound_file(session, report['chains'][role]),
                                       derived_source=prepared)
        if (child['request']['operators'] != request['operators'][role] or child['request']['source'] != request['source']
            or child['request']['regions'] != request['regions'] or child['request']['manual_lookup'] != request['manual_lookup']):
            fail('Dualband chain differs from Agent-selected operators')
        expected_child = {'v1': 'v1', 'v2': 'v2', 'v3': 'v4', 'v4': 'v4'}[request['schema'].rsplit('.', 1)[1]]
        if not child['request']['schema'].endswith('.'+expected_child):
            fail('Dualband child cannot downgrade its parameter generation mode')
        if expected_child != 'v1' and (
            any(child['request'][key] != request[key] for key in ('profile', 'targets', 'core_absence'))):
            fail('Protected dualband chain changed morphology or target directions')
        if role == 'rgb' and child['bindings'].get('full_source') != bindings['full_source']:
            fail('RGB stellar curve must bind the same original separation full source')
        chains[role] = child['transfer_chain']
    return report, chains, path, parent


def script_text(session, source, run_id, report_path, report, chains):
    from deep_sky_siril_processing import _variable
    keys = ('ha', 'g', 'b', 'oiii', 'ha-stretched', 'oiii-stretched', 'oiii-control', 'rgb-baseline', 'green', 'candidate')
    paths = {k: session/'artifacts'/(run_id+'-'+k+'.fit') for k in keys}
    if report['request']['oiii_denoise']['enabled']:
        paths['oiii-denoised'] = session/'artifacts'/(run_id+'-oiii-denoised.fit')
    views = {k: session/'previews'/(run_id+'-'+k+'.jpg') for k in
             ('ha-stretched', 'oiii-stretched', 'oiii-control', 'rgb-baseline', 'candidate')}
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    v = lambda p: _variable(p, session)
    rows = ['requires 1.4.4 1.5.0', 'set32bits', '# dualband-stretch: '+json.dumps({'parameters': str(report_path)}),
            'load '+q(source), 'fmul 1', 'split '+' '.join(q(paths[k].with_suffix('')) for k in ('ha', 'g', 'b')),
            'pm '+q(f'({v(paths["g"])} + {v(paths["b"])}) * 0.5')+' -nosum',
            'save '+q(paths['oiii'].with_suffix(''))+' -chksum']

    def branch(parent, role, key, denoise=False):
        rows.append('load '+q(parent))
        if denoise:
            if report['request']['schema'] == V4:
                from deep_sky_siril_denoise import operator
                command = ' '.join(operator(report['linear_denoise']['parameters']))
            else:
                command = 'denoise -mod=0.35'
            rows.extend([command, 'save '+q(paths['oiii-denoised'].with_suffix(''))+' -chksum'])
        rows.extend(' '.join([s['command'], *s['parameters']]) for s in chains[role])
        rows.extend(['icc_assign sRGB', 'save '+q(paths[key].with_suffix(''))+' -chksum',
                     'savejpg '+q(views[key].with_suffix(''))+' 95', 'close'])

    branch(paths['ha'], 'ha', 'ha-stretched')
    branch(paths['oiii'], 'oiii', 'oiii-control')
    branch(paths['oiii'], 'oiii', 'oiii-stretched', report['request']['oiii_denoise']['enabled'])
    branch(source, 'rgb', 'rgb-baseline')
    rows += ['pm '+q(f'0.8 * {v(paths["oiii-stretched"])} + 0.2 * {v(paths["ha-stretched"])}')+' -nosum',
             'save '+q(paths['green'].with_suffix(''))+' -chksum',
             'rgbcomp '+' '.join(q(paths[k]) for k in ('ha-stretched', 'green', 'oiii-stretched'))+
             ' '+q('-out='+str(paths['candidate'].with_suffix('')))+' -nosum',
             'load '+q(paths['candidate']), 'icc_assign sRGB',
             'savejpg '+q(views['candidate'].with_suffix(''))+' 95', 'close']
    if report.get('linear_denoise'):
        for key, name in (('oiii', 'denoise-parent'), ('oiii-denoised', 'denoise-candidate')):
            view = session/'previews'/(run_id+'-'+name+'.jpg')
            views[name] = view
            rows += ['load '+q(paths[key]), 'mtf '+' '.join(format(v, '.17g') for v in report['linear_denoise']['display_mtf']),
                     'savejpg '+q(view.with_suffix(''))+' 95', 'close']
    regions = load_json(bound_file(session, report['request']['regions']))['regions']
    selected = []
    for role in ('background', 'faint', 'faint', 'core', 'stars'):
        match = next((r for r in regions if r['role'] == role and r not in selected), None)
        if match:
            selected.append(match)
    # Common denoise panels cover all protected ROIs within the 32-material contract.
    if report.get('linear_denoise'):
        selected = selected[:2]
    for index, region in enumerate(selected):
        x0, y0, x1, y1 = region['rect']
        for key in ('rgb-baseline', 'candidate'):
            view = session/'previews'/f'{run_id}-roi-{index}-{key}.jpg'
            views[f'roi-{index}-{key}'] = view
            rows += ['load '+q(paths[key]), f'crop {x0} {y0} {x1-x0} {y1-y0}', 'icc_assign sRGB',
                     'savejpg '+q(view.with_suffix(''))+' 95', 'close']
    rel = lambda p: p.relative_to(session).as_posix()
    binding = {'policy': POLICY, 'band_semantics': 'display_estimates', 'star_color_route': 'original_dualband_rgb',
               'parameters': record(session, report_path), 'regions': report['request']['regions'],
               'request': report['bindings']['request'], 'chains': chains,
               'oiii_denoise': report['request']['oiii_denoise'],
               'layers': {k: rel(p) for k, p in paths.items()}, 'previews': {k: rel(p) for k, p in views.items()}}
    if report.get('linear_denoise'):
        binding['linear_denoise'] = {**report['linear_denoise'], 'run_id': run_id,
            'display': {'parent_preview': rel(views['denoise-parent']), 'candidate_preview': rel(views['denoise-candidate'])}}
    return '\n'.join(rows)+'\n', paths['candidate'], [*paths.values(), *views.values()], binding


def validate_stretch(session, payload, source, primary, text, run_id, *, verify_parent=True):
    from deep_sky_siril_processing import script_commands
    report, chains, path, _ = parameter_binding(session, payload, declaration(text), source, run_id, verify_parent=verify_parent)
    expected, output, _, binding = script_text(session, source, report['run_id'], path, report, chains)
    if primary != output or script_commands(text) != script_commands(expected):
        fail('Dualband SSF differs from its frozen native extraction, denoise, three curves or palette')
    return binding


def measure_outputs(session, source, primary, binding, *, write_diagnostics=False):
    layers = binding['layers']
    data = pixels(source)
    h, o = pixels(session/layers['ha']), pixels(session/layers['oiii'])
    if h.shape != data[:1].shape or o.shape != h.shape:
        fail('Dualband extraction geometry changed')
    errors = {'ha': float(np.max(np.abs(h-data[:1]))),
              'oiii': float(np.max(np.abs(o-(data[1:2]+data[2:3])*.5)))}
    hs, os, candidate = (pixels(session/layers[k]) for k in ('ha-stretched', 'oiii-stretched', 'candidate'))
    expected = np.concatenate((hs, .8*os+.2*hs, os))
    if candidate.shape != data.shape:
        fail('HOO candidate geometry changed')
    errors['palette'] = float(np.max(np.abs(candidate-expected)))
    if max(errors.values()) > 1e-6:
        fail('Native signed extraction or HOO blend differs from the frozen formula')
    report = load_json(bound_file(session, binding['parameters']))
    for key, derived_key in (('ha', 'ha'), ('oiii', 'oiii'), ('oiii-denoised', 'oiii_denoised')):
        if key in layers and np.max(np.abs(pixels(session/layers[key])-pixels(bound_file(session, report['derived_sources'][derived_key])))) > 1e-7:
            fail('Formal extraction or denoise differs from the native trial')
    regions = load_json(bound_file(session, binding['regions']))['regions']
    constraints = {}
    noise_warnings = {}
    protected = report['request']['schema'] in {'starun-siril.dualband-request.v2', 'starun-siril.dualband-request.v3', V4}
    for role, key in (('rgb', 'rgb-baseline'), ('ha', 'ha-stretched'), ('oiii', 'oiii-stretched')):
        values = pixels(session/layers[key])
        if protected:
            from deep_sky_siril_stretch_v2 import measure_output
            child = load_json(bound_file(session, report['chains'][role]))
            origin = source if role == 'rgb' else bound_file(session, child['bindings']['derived_source'])
            checked = measure_output(session, origin, session/layers[key], child)
            rows = checked['audit']['constraints']
            if checked['audit'].get('noise_diagnostic'):
                noise_warnings[role] = checked['audit']['noise_warnings']
        else:
            rows = check_constraints(statistics(values, regions), report['request']['operators'][role][-1]['constraints'])
            if not all(r['satisfied'] for r in rows):
                fail('Formal dualband output fails the frozen regional constraints')
        constraints[role] = rows
    protection = {'noise_warnings': noise_warnings} if noise_warnings else {}
    if protected:
        from deep_sky_siril_stretch_v2 import endpoints
        protection['endpoints'] = endpoints(data, candidate)
        from deep_sky_siril_stretch import optimization_enabled
        budget = optimization_enabled(load_json(session/'session.json'))
        size = data.shape[1]*data.shape[2]
        if budget:
            protection['highlight_budget'] = {'limit': 0.0001, 'pixel_count': size,
                'rates': [row['new_highlight']/size for row in protection['endpoints']],
                'warning': any(row['new_highlight'] for row in protection['endpoints'])}
        if any(row['new_highlight'] * (10000 if budget else 1) > (size if budget else 0) for row in protection['endpoints']):
            fail('Final HOO palette introduces a new highlight endpoint')
        if budget:
            from deep_sky_siril_stretch_v2 import formal_highlight_overlay
            protection['highlight_overlay'] = formal_highlight_overlay(session, data, candidate,
                session/binding['previews']['candidate'], primary, write=write_diagnostics)
    denoise = {}
    if binding.get('linear_denoise'):
        from deep_sky_siril_denoise import measure
        denoise['linear_denoise'] = measure(session, session/layers['oiii'], session/layers['oiii-denoised'],
                                            binding['linear_denoise'], write=write_diagnostics)
    return {'policy': POLICY, 'status': 'measured', 'requires_visual_review': True, **denoise,
            **({'protection': protection} if protected else {}),
            'band_semantics': 'display_estimates', 'star_color_route': 'original_dualband_rgb',
            'formula_max_abs': errors, 'constraints': constraints, 'clipping': clipping(candidate),
            'regions': {key: statistics(pixels(session/layers[key]), regions) for key in
                        ('ha', 'oiii', 'ha-stretched', 'oiii-control', 'oiii-stretched', 'rgb-baseline', 'candidate')}}


def review_paths(session, receipt):
    binding = receipt['dualband_transfer']
    paths = set(binding['layers'].values()) | set(binding['previews'].values())
    if binding.get('linear_denoise'):
        # G/B remain bound outputs; review their measured OIII estimate and preserve room for the Stage 7 panel.
        paths.difference_update(binding['layers'][key] for key in ('g', 'b'))
    paths.update(binding[key]['path'] for key in ('parameters', 'regions', 'request'))
    paths.add('runs/'+receipt['id']+'.json')
    paths.add(receipt['source_path'])
    overlay = receipt.get('processing_checks', {}).get('dualband', {}).get('protection', {}).get('highlight_overlay')
    if overlay:
        paths.add(overlay['path'])
    report = load_json(bound_file(session, binding['parameters']))
    if report['request']['schema'] in {'starun-siril.dualband-request.v2', 'starun-siril.dualband-request.v3', V4}:
        child = load_json(bound_file(session, report['chains']['rgb']))
        item = child['steps'][-1]['selected-full']['.jpg']
        paths.add(item['path'])
    return {p: session_path(session, p, must_exist=True) for p in sorted(paths)}


def generate(session_value, parameters):
    from deep_sky_siril_session import effective_session_payload, load_session, execution_policy_is_current
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        fail('Historical sessions are replay/finalize-only; create a current session for new dualband processing')
    report, chains, path, source = parameter_binding(session, payload, parameters)
    run_id = report['run_id']
    text, primary, outputs, _ = script_text(session, source, run_id, path, report, chains)
    script = session/'scripts'/(run_id+'.ssf')
    provenance = script.with_suffix('.provenance.json')
    if any(p.exists() or p.is_symlink() for p in (script, provenance, *outputs)):
        fail('Dualband generation must preserve existing scripts and outputs')
    with script.open('x', encoding='utf-8') as stream:
        stream.write(text)
    root = Path(__file__).resolve().parents[1]
    document = {'schema': 'starun-siril.ssf-provenance.v1', 'contract_version': '1', 'run_id': run_id,
        'protocol': 'stretch', 'script': {'path': script.relative_to(session).as_posix(), 'sha256': sha256_file(script)},
        'references': protocol_reference_bindings('stretch', variant='hoo'),
        'command_policy': {'path': 'references/command-policy.json', 'sha256': sha256_file(root/'references/command-policy.json')},
        'manual_lookup': report['request']['manual_lookup'],
        'rationale': {'applicability': 'Native OSC display estimates from an accepted linear separation',
            'parameter_choices': ['Freeze separate Ha/OIII curves and the original RGB star transfer',
                                  report['request']['oiii_denoise']['reason']]}}
    if report.get('linear_denoise'):
        document['references'].extend({**item, 'role': 'supporting'} for item in protocol_reference_bindings('restoration.denoise'))
    atomic_write_json(provenance, document)
    return {'script': str(script), 'primary_output': str(primary), 'expected': list(map(str, outputs))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--session', required=True)
    prepare_parser.add_argument('--source', required=True)
    prepare_parser.add_argument('--run-id', required=True)
    prepare_parser.add_argument('--display-mtf', nargs=3, type=float, required=True)
    prepare_parser.add_argument('--filter-evidence', type=json.loads)
    prepare_parser.add_argument('--timeout', type=float, default=1800)
    solve_parser = commands.add_parser('solve')
    solve_parser.add_argument('--session', required=True)
    solve_parser.add_argument('--request', required=True)
    solve_parser.add_argument('--run-id', required=True)
    solve_parser.add_argument('--timeout', type=float, default=1800)
    generate_parser = commands.add_parser('generate')
    generate_parser.add_argument('--session', required=True)
    generate_parser.add_argument('--parameters', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.session, args.source, args.run_id, args.display_mtf, args.filter_evidence, args.timeout)
        else:
            result = (solve(args.session, args.request, args.run_id, args.timeout) if args.command == 'solve'
                      else generate(args.session, args.parameters))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (ContractError, OSError, ValueError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
