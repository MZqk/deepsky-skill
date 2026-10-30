"""Bind nonlinear tone proposals to remaining goals, current ROIs and actual reviews."""
from pathlib import Path

import numpy as np

from deep_sky_siril_artifacts import read_scientific_pixels
from deep_sky_siril_contract import ContractError, atomic_write_json, fingerprint, load_json, session_path, sha256_file
from deep_sky_siril_metrics import measure_regions
from deep_sky_siril_stretch import bound_file, record
from siril_manual_bundle import BundleError, strict_json_bytes

POLICY = 'remaining_goal_v1'


def fail(message):
    raise ContractError('tone_proposal_invalid', message)


def text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def bound(session, item, roots=('reports',)):
    path = bound_file(session, item, roots)
    if item != record(session, path) or type(item.get('size')) is not int:
        fail('Tone evidence requires an exact path/hash/size binding')
    return path


def declaration(value):
    rows = [row.strip() for row in value.splitlines() if row.strip().startswith('# tone-adjust')]
    if len(rows) != 1 or not rows[0].startswith('# tone-adjust:'):
        fail('Exactly one # tone-adjust: proposal is required; describe the remaining goal before executing')
    try:
        p = strict_json_bytes(rows[0].split(':', 1)[1].encode(), document='tone proposal')
    except BundleError as exc:
        fail(str(exc))
    fields = {'schema', 'regions', 'goal', 'upstream_assessment', 'remaining_issue', 'parameter_reason'}
    if (not isinstance(p, dict) or not fields <= set(p) <= fields | {'revision'}
        or p['schema'] != 'starun-siril.tone-proposal.v1'
        or any(not text(p[key]) for key in fields-{'schema', 'regions'})):
        fail('A tone proposal must explain upstream results, the current remaining issue, goal and parameter rationale')
    return p


def source_record(session, payload, source):
    return {'path': '@input' if source == Path(payload['input']['path']) else str(source.relative_to(session)),
            **fingerprint(source, include_path=False)}


def context(session, payload, state):
    from deep_sky_siril_core import _accepted_review
    from deep_sky_siril_session import _verified_success_receipts
    lineage = state.get('lineage', [])
    if not lineage:
        lineage = [row for row in _verified_success_receipts(session)
                   if row['protocol'] == 'input.inspect' and row['source_path'] == '@input']
        if len(lineage) != 1:
            fail('A nonlinear input requires its unique accepted inspection')
    rows = []
    for run in lineage:
        if run['protocol'] not in {'stretch', 'tone.adjust'} and run['id'] != lineage[-1]['id']:
            continue
        _, review = _accepted_review(session, run['id'])
        rows.append({'receipt': record(session, session/'runs'/(run['id']+'.json')),
                     'review': record(session, session/review['path'])})
    return rows


def checked_context(session, rows):
    manifest = load_json(session/'manifest.json')['runs']
    summaries = []
    if not isinstance(rows, list) or not rows:
        fail('Tone proposal requires actual accepted upstream evidence')
    for item in rows:
        path = bound(session, item['receipt'], ('runs',))
        run = load_json(path); review = load_json(bound(session, item['review'], ('reviews',)))
        matches = [row for row in manifest if row['id'] == run['id']]
        if (len(matches) != 1 or matches[0].get('status') != 'success' or run.get('status') != 'success'
            or matches[0].get('receipt') != path.name or matches[0].get('receipt_sha256') != item['receipt']['sha256']
            or review.get('verdict') != 'accept' or review.get('run_receipt_sha256') != item['receipt']['sha256']):
            fail('Tone upstream receipt or acceptance changed')
        summaries.append({'evidence': item, 'run_id': run['id'], 'protocol': run['protocol'],
            'transfer_chain': run.get('transfer_chain', []), 'tone_chain': run.get('tone_chain', []),
            'hdr_transfer': run.get('hdr_transfer'), 'dualband_transfer': run.get('dualband_transfer'),
            'local_tone': run.get('local_tone'), 'proposal': run.get('tone_proposal', {}).get('proposal'), 'review': review})
    return summaries


def prepare(session, payload, source, value, chain, run_id, state):
    from deep_sky_siril_core import _load_run
    p = declaration(value)
    regions = bound(session, p['regions'])
    measured = measure_regions(source, None, regions, scientific=True)
    if measured['status'] != 'measured' or not any(r['role'] in {'core', 'faint'} for r in measured['regions']):
        fail('Tone proposal requires current scientific ROIs and a protected subject region')
    binding = {'policy': POLICY, 'proposal': p, 'source': source_record(session, payload, source),
               'chain': chain, 'run_id': run_id, 'upstream': context(session, payload, state),
               'helper': fingerprint(Path(__file__), include_path=False)}
    checked_context(session, binding['upstream'])
    revision = p.get('revision')
    if revision is None:
        for entry in load_json(session/'manifest.json')['runs']:
            if entry.get('status') == 'success' and entry['id'] != run_id:
                prior = load_json(session_path(session, 'runs/'+entry['id']+'.json', must_exist=True, allowed_roots=('runs',)))
                if prior.get('tone_proposal', {}).get('source') == binding['source']:
                    fail('Another candidate from this parent requires an observation-bound revision')
    else:
        if (not isinstance(revision, dict) or set(revision) != {'run_id', 'evidence', 'reason'}
            or not text(revision['reason']) or not isinstance(revision['evidence'], list) or not revision['evidence']
            or revision['run_id'] == run_id):
            fail('A tone revision requires a distinct prior candidate and actual observation evidence')
        path, prior = _load_run(session, revision['run_id'])
        if prior.get('status') != 'success' or prior.get('tone_proposal', {}).get('source') != binding['source']:
            fail('Revise from the same accepted parent; do not accumulate candidate adjustments')
        materials = {row['path'] for row in prior['outputs']} | set(review_paths(session, prior))
        for item in revision['evidence']:
            bound(session, item, ('reports', 'previews', 'artifacts', 'runs'))
            if item['path'] not in materials:
                fail('Tone revision evidence must belong to the actual prior candidate')
        binding['revision_receipt'] = record(session, path)
    return binding


def check_binding(session, payload, source, binding):
    if (binding.get('policy') != POLICY or binding.get('helper') != fingerprint(Path(__file__), include_path=False)
        or binding.get('source') != source_record(session, payload, source)):
        fail('Tone proposal implementation or scientific source changed')
    bound(session, binding['proposal']['regions'])
    summaries = checked_context(session, binding['upstream'])
    if 'revision_receipt' in binding:
        path = bound(session, binding['revision_receipt'], ('runs',)); prior = load_json(path)
        revision = binding['proposal']['revision']
        entries = [row for row in load_json(session/'manifest.json')['runs'] if row['id'] == revision['run_id']]
        if (prior.get('id') != revision['run_id'] or prior.get('status') != 'success' or len(entries) != 1
            or entries[0].get('receipt_sha256') != binding['revision_receipt']['sha256']
            or prior.get('tone_proposal', {}).get('source') != binding['source']):
            fail('Tone revision receipt changed')
        for item in revision['evidence']:
            bound(session, item, ('reports', 'previews', 'artifacts', 'runs'))
    return summaries


def measure(session, payload, source, primary, binding, *, write=False):
    summaries = check_binding(session, payload, source, binding)
    regions = bound(session, binding['proposal']['regions'])
    measured = measure_regions(primary, source, regions, scientific=True)
    a, _ = read_scientific_pixels(source); b, _ = read_scientific_pixels(primary)
    if measured['status'] != 'measured' or a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        fail('Tone diagnostics require aligned, finite scientific pixels')
    changes = [{'channel': i, 'max_abs_change': float(np.max(np.abs(y-x))),
        'median_change': float(np.median(y)-np.median(x)),
        'new_nonpositive': int(np.sum((x > 0) & (y <= 0))), 'new_highlight': int(np.sum((x < 1) & (y >= 1)))}
        for i, (x, y) in enumerate(zip(a, b))]
    report = {'schema': 'starun-siril.tone-diagnostics.v1', 'policy': POLICY, 'proposal': binding['proposal'],
        'source': binding['source'], 'candidate': record(session, primary), 'upstream': summaries,
        'tone_chain': binding['chain'], 'regional': measured, 'changes': changes,
        'requires_visual_review': True, 'recommended_verdict': 'uncertain',
        'limitations': 'Brightness and endpoint statistics cannot establish additional benefit or preserved structure; nonlinear curves accumulate'}
    for item in measured['images'].values():
        if sha256_file(Path(item['path'])) != item['sha256']:
            fail('Tone scientific pixels changed during diagnostics')
    path = session_path(session, 'reports/'+binding['run_id']+'/tone-diagnostics.json', allowed_roots=('reports',))
    if write:
        if path.exists():
            fail('Tone diagnostics already exist; use a new run ID')
        path.parent.mkdir(parents=True, exist_ok=True); atomic_write_json(path, report)
    elif load_json(path) != report:
        fail('Tone diagnostics changed; read-only replay cannot repair evidence')
    return {'status': 'measured', 'report': record(session, path), 'requires_visual_review': True}


def verify(session, payload, run, value, source, primary):
    binding = run.get('tone_proposal', {})
    if (binding.get('proposal') != declaration(value) or binding.get('chain') != run.get('tone_chain')
        or binding.get('run_id') != run['id']):
        fail('Tone proposal differs from the actual SSF or receipt')
    return measure(session, payload, source, primary, binding) == run.get('processing_checks', {}).get('tone_adjustment')


def review_paths(session, run):
    binding = run.get('tone_proposal')
    if not binding:
        return {}
    report = run['processing_checks']['tone_adjustment']['report']
    return {item['path']: bound(session, item) for item in (binding['proposal']['regions'], report)} | {
        'runs/'+run['id']+'.json': session/'runs'/(run['id']+'.json')}


def assessment_skeleton(session, run):
    if not run.get('tone_proposal'):
        return None
    binding = run['tone_proposal']; rows = load_json(bound(session, binding['proposal']['regions']))['regions']
    return {'diagnostics': run['processing_checks']['tone_adjustment']['report'], 'goal_status': None,
        'additional_benefit': None, 'benefit_observation': '', 'adoption_reason': '',
        'regions': [{'region': {'regions': binding['proposal']['regions'], 'id': row['id']},
                     'assessment': None, 'observation': ''} for row in rows]}


def validate_assessment(session, run, review):
    block = review.get('tone_assessment'); expected = assessment_skeleton(session, run)
    if expected is None:
        if 'tone_assessment' in review:
            fail('Tone assessment requires a proposal-bound candidate')
        return
    if (not isinstance(block, dict) or set(block) != set(expected) or block['diagnostics'] != expected['diagnostics']
        or block['goal_status'] not in ('met', 'not_met', 'uncertain')
        or block['additional_benefit'] not in ('observed', 'not_observed', 'uncertain')
        or not text(block['benefit_observation']) or not text(block['adoption_reason'])
        or not isinstance(block['regions'], list) or len(block['regions']) != len(expected['regions'])):
        fail('Assess the remaining tone goal, additional benefit and every protected region')
    for row, required in zip(block['regions'], expected['regions']):
        if (not isinstance(row, dict) or set(row) != set(required) or row['region'] != required['region']
            or row['assessment'] not in ('acceptable', 'degraded', 'uncertain') or not text(row['observation'])):
            fail('Record actual tone changes and preservation in every bound region')
    if review['verdict'] == 'accept':
        if (block['goal_status'] != 'met' or block['additional_benefit'] != 'observed'
            or any(row['assessment'] != 'acceptable' for row in block['regions'])
            or not set(review_paths(session, run)) <= {item['path'] for item in review['inspected_materials']}):
            fail('Accept tone only with additional benefit, achieved goals and acceptable protected regions')
        report = load_json(bound(session, expected['diagnostics']))
        if not any(row['max_abs_change'] > 0 for row in report['changes']):
            fail('Unchanged pixels do not demonstrate additional tone benefit')
