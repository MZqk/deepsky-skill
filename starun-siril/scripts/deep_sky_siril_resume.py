"""Read-only recovery facts and source-bound ROI reuse proposals."""
from pathlib import Path
import re

from deep_sky_siril_contract import ContractError, HASH_PATTERN, RUN_ID_PATTERN, fingerprint, fingerprint_matches, load_json, session_path, sha256_file
from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current

SCHEMA = 'starun-siril.recovery-summary.v1'
DECISION = 'starun-siril.stage-decision.v1'
STAGES = {'input.inspect': 1, 'geometry.crop-near-black': 2, 'background.subtract': 3,
          'astrometry.solve': 4, 'color.calibrate': 4, 'restoration.denoise': 5,
          'restoration.deconvolve': 5, 'stars.separate': 6, 'stretch': 7,
          'structure.local-contrast': 8, 'color.map': 8, 'stars.recompose': 9, 'delivery.render': 10}
NEXT_STAGE = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 8, 9: 10, 10: 10}
GEOMETRY_PRESERVING = set(STAGES)-{'input.inspect', 'geometry.crop-near-black', 'delivery.render'} | {
    'tone.adjust', 'color.finish', 'restoration.denoise-nonlinear'}


def fail(message):
    raise ContractError('recovery_evidence_invalid', message)


def binding(session, path):
    return {'path': path.relative_to(session).as_posix(), **fingerprint(path, include_path=False)}


def stage_hint(run):
    stage = STAGES.get(run['protocol'])
    if run['protocol'] == 'astrometry.solve':
        return stage, 4
    if run['protocol'] == 'restoration.denoise':
        return stage, 5
    return stage, NEXT_STAGE.get(stage)


def decision_summary(session, run_id, review, review_path):
    """A decision describes Agent intent; it never authorizes scientific work."""
    run_path = session_path(session, 'runs/'+run_id+'.json', must_exist=True, allowed_roots=('runs',))
    run = load_json(run_path)
    stage, next_stage = stage_hint(run)
    expected = {'schema': DECISION, 'run_receipt': binding(session, run_path),
                'review': binding(session, review_path), 'stage': stage,
                'action': None, 'next_stage': None, 'reason': ''}
    if review.get('run_id') != run_id or review.get('run_receipt_sha256') != expected['run_receipt']['sha256']:
        fail('Decision requires a review bound to the unchanged run receipt')
    path = session_path(session, 'reports/'+run_id+'/decision.json', allowed_roots=('reports',))
    revisions = sorted(path.parent.glob('decision-*.json'))
    if not path.exists():
        if revisions:
            fail('Stage decision revisions require their original decision')
        return {'status': 'not_recorded', 'next_stage': next_stage, 'path': str(path), 'template': expected}
    for index, candidate in enumerate([path, *revisions]):
        if index and candidate.name != f'decision-{index:04d}.json':
            fail('Stage decision revisions must be consecutive')
        path = session_path(session, candidate, must_exist=True, allowed_roots=('reports',))
        value = load_json(path)
        invalid_next = value.get('action') == 'continue' and (
            type(value.get('next_stage')) is not int or not 1 <= value['next_stage'] <= 10)
        if (set(value) != set(expected) or value['schema'] != DECISION
            or any(value[key] != expected[key] for key in ('run_receipt', 'review'))
            or value.get('supersedes') != expected.get('supersedes')
            or type(value['stage']) is not int or not 1 <= value['stage'] <= 10
            or not isinstance(value['action'], str) or value['action'] not in {'continue', 'stop'}
            or not isinstance(value['reason'], str) or not value['reason'].strip() or len(value['reason']) > 800
            or invalid_next):
            fail('Stage decision is malformed or differs from its reviewed evidence')
        if value['action'] == 'stop' and value['next_stage'] is not None:
            fail('A stop decision requires a concrete reason and no requested next stage')
        expected = {**expected, 'stage': value['stage'], 'supersedes': binding(session,path)}
    return {'status': 'recorded', 'path': str(path),
            **{key: value[key] for key in ('stage', 'action', 'next_stage', 'reason')},
            'next_path': str(path.parent/f'decision-{len(revisions)+1:04d}.json'), 'next_template': expected}


def _manifest_receipts(session, payload, manifest):
    """Project immutable recorded outcomes without replaying scientific audits."""
    entries = manifest.get('runs')
    if not isinstance(entries,list):
        fail('Manifest has no valid run index')
    runs,seen = [],set()
    for entry in entries:
        run_id = entry.get('id') if isinstance(entry,dict) else None
        if (not isinstance(run_id,str) or not RUN_ID_PATTERN.fullmatch(run_id) or run_id in seen
            or entry.get('receipt') != run_id+'.json'):
            fail('Manifest run identity is missing or ambiguous')
        seen.add(run_id)
        path = session_path(session,'runs/'+entry['receipt'],must_exist=True,allowed_roots=('runs',))
        if sha256_file(path) != entry.get('receipt_sha256'):
            fail('Run receipt changed: '+run_id)
        run = load_json(path)
        if (run.get('schema') != 'starun-siril.run-receipt.v1' or run.get('id') != run_id
            or run.get('status') != entry.get('status') or not isinstance(run.get('protocol'),str)):
            fail('Run identity or recorded outcome changed: '+run_id)
        if run['status'] == 'success':
            source = _source(session,payload,run['source_path'])
            if fingerprint(source,include_path=False) != run.get('source'):
                fail('Recorded scientific source changed: '+run_id)
            runs.append(run)
    return runs


def _reviews(session, payload, receipts):
    rows = {}
    for path in sorted((session/'reviews').glob('*.json')):
        path = session_path(session, path, must_exist=True, allowed_roots=('reviews',))
        review = load_json(path)
        run_id = review.get('run_id')
        if not isinstance(run_id,str):
            fail('Review run identity is malformed')
        if run_id not in receipts:
            continue
        if run_id in rows:
            fail('Multiple review records make the recovery position ambiguous: '+run_id)
        if (review.get('schema') != 'starun-siril.review.v1' or not isinstance(review.get('verdict'),str)
            or review['verdict'] not in {'accept','reject','uncertain'}
            or review.get('protocol') != receipts[run_id]['protocol']
            or review.get('run_receipt_sha256') != sha256_file(session/'runs'/(run_id+'.json'))):
            fail('Review binding differs from its run: '+run_id)
        gates = review.get('gates')
        if (not isinstance(gates,dict) or set(gates) != {'structure','background','color','stars','geometry'}
            or any(not isinstance(value,str) or value not in {'pass','fail','not_applicable','uncertain'} for value in gates.values())
            or review['verdict']=='accept' and any(value not in {'pass','not_applicable'} for value in gates.values())):
            fail('Recorded acceptance conflicts with its review gates: '+run_id)
        materials = review.get('inspected_materials')
        if not isinstance(materials,list) or not 1 <= len(materials) <= 32:
            fail('Review has no bound inspected materials: '+run_id)
        names = set()
        for material in materials:
            if (not isinstance(material,dict) or set(material) != {'path','sha256'}
                or not isinstance(material['path'],str) or material['path'] in names):
                fail('Malformed or duplicate reviewed material: '+run_id)
            names.add(material['path'])
            inspected = Path(payload['input']['path']) if material['path']=='@input' else session_path(
                session,material['path'],must_exist=True)
            if sha256_file(inspected) != material['sha256']:
                fail('Reviewed material changed: '+material['path'])
        rows[run_id] = (review, path)
    return rows


def _source(session, payload, relative):
    return Path(payload['input']['path']).resolve() if relative == '@input' else session_path(
        session, relative, must_exist=True, allowed_roots=('artifacts',))


def _region_proposal(session, payload, receipts, reviews, parent, regions_path):
    from deep_sky_siril_artifacts import read_scientific_pixels
    path = session_path(session, regions_path, must_exist=True, allowed_roots=('reports',))
    doc = load_json(path)
    if (set(doc) != {'schema','reference','regions'} or doc['schema'] != 'starun-siril.regions.v1'
        or not isinstance(doc['reference'], dict) or set(doc['reference']) != {'sha256','width','height'}
        or not isinstance(doc['reference'].get('sha256'), str) or not HASH_PATTERN.fullmatch(doc['reference']['sha256'])
        or any(type(doc['reference'][k]) is not int or doc['reference'][k] <= 0 for k in ('width','height'))):
        fail('ROI reuse requires an unchanged regions.v1 source contract')
    owners = {}
    for run in receipts.values():
        if run.get('primary_output'):
            if run['primary_output'] in owners:
                fail('Scientific artifact ownership is ambiguous')
            owners[run['primary_output']] = run
    current, lineage, seen = parent['relative_path'], [], set()
    while True:
        source = _source(session, payload, current)
        if sha256_file(source) == doc['reference']['sha256']:
            break
        if current in seen or current not in owners:
            fail('ROI reference is not an ancestor of the accepted parent')
        seen.add(current);run = owners[current]
        if (run['protocol'] not in GEOMETRY_PRESERVING or run['id'] not in reviews
            or reviews[run['id']][0]['verdict'] != 'accept'):
            fail('ROI coordinates cannot cross unaccepted or geometry-changing processing')
        lineage.append(run['id']);current = run['source_path']
    accepted = {run_id for run_id in [*lineage, owners[current]['id'] if current in owners else None] if run_id}
    if current == '@input':
        accepted.update(run['id'] for run in receipts.values() if run['protocol'] == 'input.inspect' and run['source_path'] == '@input')
    if not any(review['verdict'] == 'accept' and run_id in accepted
               and {'path': path.relative_to(session).as_posix(), 'sha256': sha256_file(path)} in review.get('inspected_materials', [])
               for run_id, (review, _) in reviews.items()):
        fail('ROI reuse requires the source contract in an accepted same-lineage review')
    before = read_scientific_pixels(source)[0]
    target = Path(parent['path']);after = read_scientific_pixels(target)[0]
    if before.shape != after.shape or before.shape[1:] != (doc['reference']['height'], doc['reference']['width']):
        fail('ROI reference geometry differs from the accepted parent')
    rows = doc['regions'];seen = set();h,w = after.shape[1:]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        fail('ROI reuse requires 1..64 explicit regions')
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'id','role','rect'} or not isinstance(row['id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',row['id']) or row['id'] in seen
            or not isinstance(row['role'], str) or row['role'] not in {'background','core','faint','stars'}
            or not isinstance(row['rect'], list) or len(row['rect']) != 4 or any(type(x) is not int for x in row['rect'])):
            fail('Invalid ROI coordinates or roles')
        x0,y0,x1,y1 = row['rect']
        if not 0 <= x0 < x1 <= w or not 0 <= y0 < y1 <= h:
            fail('ROI coordinates exceed the accepted geometry')
        seen.add(row['id'])
    return {'source_contract': binding(session,path), 'verified_lineage': list(reversed(lineage)),
            'proposed_contract': {**doc, 'reference': {**doc['reference'], 'sha256': parent['sha256']}},
            'coordinate_frame':'preview_top_left_zero_based_half_open',
            'requires_measurement': True, 'requires_visual_review': True, 'scientific_parent': False}


def session_status(session_value, regions=None, parameters=None):
    from deep_sky_siril_core import _load_committed_finalization
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session,payload,manifest)
    if manifest.get('finalization') is not None:
        if regions is not None or parameters is not None:
            fail('ROI and prefix proposals require an active accepted parent, not finalized evidence')
        final,_,_,_ = _load_committed_finalization(session,manifest,manifest['finalization'].get('selection_sha256'))
        return {'schema':SCHEMA,'status':'finalized','session':str(session),'last_accepted_parent':final.get('final') or final.get('reference'),
                'input_reference':payload['input'],'next_stage':None,'next_stage_basis':'committed_finalization',
                'blocking_reasons':[],'continuation':None,'scientific_parent':False,
                'validation':'committed_finalization','execution_validation_required':False,
                'stretch_attempts':{'limit':3,'used':len(list(session.glob('reports/*/scientific-attempt.json'))),
                    'remaining':max(0,3-len(list(session.glob('reports/*/scientific-attempt.json'))))}}
    runs = _manifest_receipts(session,payload,manifest)
    receipts = {run['id']: run for run in runs}
    if len(receipts) != len(runs):
        fail('Manifest contains duplicate run identities')
    reviews = _reviews(session,payload,receipts)
    parent_run = next((run for run in reversed(runs) if run['id'] in reviews and reviews[run['id']][0]['verdict'] == 'accept'), None)
    relative = '@input' if parent_run is None or not parent_run.get('primary_output') else parent_run['primary_output']
    parent_path = _source(session,payload,relative)
    parent = {'path': str(parent_path), 'relative_path': relative, 'run_id': None if parent_run is None else parent_run['id'],
              **fingerprint(parent_path,include_path=False)}
    if parent_run is not None and relative != '@input':
        expected = [record for record in parent_run.get('outputs',[]) if record.get('path')==relative]
        if (len(expected)!=1 or any(expected[0].get(key)!=parent[key] for key in ('sha256','size'))):
            fail('Accepted primary output differs from its recorded file binding')
    blockers = []
    def block(code, message):
        blockers.append({'code': code, 'message': message})
    if not execution_policy_is_current(payload):
        block('legacy_session_read_only','The current runtime cannot start new scientific processing in this frozen session')
    runtime = load_json(session/'reports/tool-probe.json').get('tools',{}).get('siril_cli',{})
    if not runtime.get('compatible') or not fingerprint_matches(runtime.get('fingerprint')):
        block('siril_runtime_unavailable','Frozen Siril executable is unavailable or changed; status does not reprobe or install it')
    unknown = [key for key in ('input_state','channel_mode') if payload['context'][key] == 'unknown']
    if unknown:
        block('input_context_unknown','Reliable input confirmation is required: '+', '.join(unknown))
    last = runs[-1] if runs else None
    continuation = None;next_stage = 1
    if last is not None:
        if last['id'] not in reviews:
            next_stage = stage_hint(last)[0]
            block('review_required','Review the latest successful run before accepting it: '+last['id'])
        else:
            review, review_path = reviews[last['id']]
            continuation = decision_summary(session,last['id'],review,review_path)
            next_stage = continuation['next_stage']
            if continuation['status'] == 'not_recorded':
                if review['verdict'] != 'accept':
                    next_stage = stage_hint(last)[0]
                block('continuation_not_recorded','No bound next-step or concrete stop reason was recorded after the last review')
            elif continuation['action'] == 'stop':
                block('agent_stopped',continuation['reason'])
    attempts = list(session.glob('reports/*/scientific-attempt.json'))
    for path in attempts:
        path = session_path(session,path,must_exist=True,allowed_roots=('reports',))
        if not isinstance(load_json(path).get('source_sha256'), str):
            fail('Malformed scientific-attempt ledger; its slot cannot be treated as free')
    if len(attempts) > 3:
        block('attempt_ledger_invalid','Stage 7 scientific-attempt ledger exceeds its frozen limit')
    if manifest.get('runs') and manifest['runs'][-1].get('status') != 'success':
        entry = manifest['runs'][-1]
        run_id = entry.get('id')
        if not isinstance(run_id,str) or not RUN_ID_PATTERN.fullmatch(run_id) or entry.get('receipt') != run_id+'.json':
            fail('Malformed failed-run reference')
        path = session_path(session,'runs/'+entry['receipt'],must_exist=True,allowed_roots=('runs',))
        if sha256_file(path) != entry.get('receipt_sha256'):
            fail('Failed-run receipt changed')
        failed = load_json(path)
        if failed.get('id') != run_id or failed.get('status') != entry['status']:
            fail('Failed-run identity or status differs from its manifest')
        block('run_failed','Latest recorded run did not succeed: '+run_id)
        details = [row.get('text') for row in failed.get('log_diagnostics',{}).get('findings',[])
                   if row.get('severity') in {'fatal','error'} and isinstance(row.get('text'),str)]
        if failed.get('timed_out'):
            details.insert(0,'Native execution timed out; frozen timeout seconds: '+str(failed.get('timeout_seconds')))
        blockers[-1].update(receipt=binding(session,path),details=details[:3])
        next_stage = STAGES.get(failed.get('protocol'))
    if parent_run is None:
        block('accepted_parent_missing','The original input still requires an accepted inspection')
        next_stage = 1
    if unknown:
        next_stage = 1
    result = {'schema': SCHEMA, 'status': 'blocked' if blockers else 'ready', 'session': str(session),
              'last_accepted_parent': parent if parent_run is not None else None, 'input_reference':payload['input'],
              'next_stage': next_stage, 'next_stage_basis': 'input_prerequisite' if unknown or parent_run is None else (
                  'failed_run_stage' if any(row['code']=='run_failed' for row in blockers) else
                  'agent_decision' if continuation and continuation['status']=='recorded' else 'default_stage_evaluation_hint'),
              'blocking_reasons': blockers, 'stretch_attempts': {'limit':3,'used':len(attempts),'remaining':max(0,3-len(attempts))},
              'continuation': continuation, 'validation':'receipt_review_and_file_bindings_only',
              'execution_validation_required':True, 'scientific_parent':False}
    if regions is not None:
        if parent_run is None:
            fail('ROI reuse requires an accepted scientific parent')
        result['roi_reuse'] = _region_proposal(session,payload,receipts,reviews,parent,regions)
    from deep_sky_siril_stretch_stepwise import REPORT as STEPWISE_REPORT, status as prefix_status
    from deep_sky_siril_stretch_stepwise import candidate_statuses
    try:
        stepwise = candidate_statuses(session, payload)
    except ContractError as exc:
        stepwise = []
        block('stepwise_prefix_changed', str(exc))
        result['status'] = 'blocked'
    if stepwise:
        result['stepwise_candidates'] = stepwise
    from deep_sky_siril_artifact_focus import enabled as artifact_enabled, run_frames, pending_review
    if artifact_enabled(payload):
        pending_artifacts = []
        for run in runs:
            if run.get('protocol') != 'stretch' or not run.get('stretch_visibility'):
                continue
            try:
                if run['id'] in reviews:
                    from deep_sky_siril_artifact_focus import formal_validate
                    formal_validate(session, payload, run, reviews[run['id']][0])
                    continue
                plan, goals, _, _, _, _ = run_frames(session, payload, run)
                from deep_sky_siril_stretch import record
                pending_artifacts.append({'run_id': run['id'], **pending_review(session, payload, plan, goals, record(session, session/'runs'/(run['id']+'.json')))})
            except ContractError as exc:
                block('artifact_review_incomplete', str(exc))
                result['status'] = 'blocked'
        if pending_artifacts:
            result['artifact_reviews'] = pending_artifacts
    if parameters is not None and load_json(session_path(session, parameters, must_exist=True, allowed_roots=('reports',))).get('schema') == STEPWISE_REPORT:
        try:
            result['prefix_reuse'] = prefix_status(session, payload, parameters)
        except ContractError as exc:
            result['prefix_reuse'] = {'status':'blocked', 'reason':str(exc), 'scientific_parent':False}
            block('stepwise_prefix_changed', str(exc))
            result['status'] = 'blocked'
        return result
    if parameters is not None:
        from deep_sky_siril_stretch import load_parameters, record
        try:
            report,path,_ = load_parameters(session,payload,parameters,source=parent_path)
        except ContractError as exc:
            result['prefix_reuse'] = {'status':'blocked','code':exc.code,'reason':str(exc),'scientific_parent':False}
            block('prefix_reuse_unavailable','Prefix evidence needs its compatible frozen runtime and complete scientific validation: '+str(exc))
            result['status'] = 'blocked'
        else:
            result['prefix_reuse'] = {'status':'verified','parameters':record(session,path), 'source':report['bindings']['source'],
                'transfer_chain':report['transfer_chain'], 'prefixes':[{'step':index,'fit':step['selected']['.fit'],
                    'jpeg':step['selected']['.jpg']} for index,step in enumerate(report['steps'],1)],
                'requires_exact_prefix_match':True, 'scientific_parent':False}
    return result
