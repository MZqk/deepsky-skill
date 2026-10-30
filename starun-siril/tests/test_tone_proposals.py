"""Remaining-goal tone decisions and mocked scientific execution; no native Siril."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
import deep_sky_siril_tone as tone
from . import test_deep_sky_siril as support
from . import test_recomposition_proposals as recomposition
from .test_recomposition_proposals import case, write
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence
from .test_tone_adjust import _tone_text

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/tone-adjust-prior.json').read_text())


def assets(case, *, source=None, run_id='085-tone', midtones=.47, revision=None):
    session, parent, *_ = case
    source = Path(source or parent)
    values = tone.read_scientific_pixels(source)[0]; height, width = values.shape[1:]
    regions = session/'reports'/(run_id+'-regions.json')
    contract.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': width, 'height': height},
        'regions': [{'id': 'subject', 'role': 'faint', 'rect': [28, 28, 44, 44]},
                    {'id': 'core', 'role': 'core', 'rect': [30, 30, 38, 38]},
                    {'id': 'sky', 'role': 'background', 'rect': [48, 48, 60, 60]},
                    {'id': 'stars', 'role': 'stars', 'rect': [12, 40, 24, 52]}]})
    p = {'schema': 'starun-siril.tone-proposal.v1', 'regions': tone.record(session, regions),
         'goal': 'Resolve the observed remaining subject transition',
         'upstream_assessment': 'The accepted chain already reveals the outer shell; inspect its actual bound review',
         'remaining_issue': 'The current subject transition remains below the explicitly requested display goal',
         'parameter_reason': 'One explicit MTF selected for this synthetic regression scene'}
    if revision is not None:
        p['revision'] = revision
    script = session/'scripts'/(run_id+'.ssf')
    script.write_text('# tone-adjust: '+json.dumps(p)+'\n'+_tone_text(session, source, f'mtf 0 {midtones} 1', run_id))
    support._write_ssf_provenance(session, script, 'tone.adjust')
    return {'protocol': 'tone.adjust', 'script_value': script, 'source_value': source, 'timeout': 30,
            'primary_output_value': session/'artifacts'/(run_id+'.fit'),
            'expected_values': [session/'artifacts'/(run_id+'.fit'), session/'previews'/(run_id+'-parent.jpg'),
                                session/'previews'/(run_id+'-candidate.jpg')]}


def run(case, arguments):
    result = core.run_script(case[0], **arguments)
    assert result['status'] == 'success', result.get('processing_checks')
    return result


def accept(session, result):
    draft = core.check_review(session, result['id'], None)['review']
    draft.update(verdict='accept', gates={key: 'pass' for key in draft['gates']}, notes='Known synthetic tone fixture inspected')
    block = draft['tone_assessment']
    block.update(goal_status='met', additional_benefit='observed', benefit_observation='Known fixture gain beyond the accepted parent',
                 adoption_reason='Known synthetic added benefit with every protected region acceptable')
    for row in block['regions']:
        row.update(assessment='acceptable', observation='Actual bound synthetic region retained')
    return draft


def save_review(session, result, draft):
    path = session/'reviews'/(result['id']+'.json'); contract.atomic_write_json(path, draft)
    return path


@pytest.mark.parametrize('midtones', [.43, .55, .4999])
def test_explicit_parameters_have_no_fixed_microadjustment_strength(case, midtones):
    session, *_ = case
    arguments = assets(case, midtones=midtones)
    result = run(case, arguments)
    assert float(result['tone_chain'][0]['parameters'][1]) == midtones
    checked = result['processing_checks']['tone_adjustment']
    report = contract.load_json(session/checked['report']['path'])
    assert report['recommended_verdict'] == 'uncertain' and report['requires_visual_review']
    assert report['regional']['reference']['sha256'] == contract.sha256_file(arguments['source_value'])
    assert any(row['max_abs_change'] for row in report['changes'])
    draft = core.check_review(session, result['id'], None)['review']
    assert draft['tone_assessment']['additional_benefit'] is None
    assert len(draft['inspected_materials']) <= 32


def test_two_step_accepted_chain_is_context_and_does_not_forbid_a_remaining_goal(case):
    session, *_ = case
    _, prior = core._load_run(session, '070-stretch')
    prior['transfer_chain'].append({'command': 'mtf', 'parameters': ['0', '.48', '1']})
    path = session/'runs/070-stretch.json'; contract.atomic_write_json(path, prior)
    manifest = contract.load_json(session/'manifest.json')
    next(row for row in manifest['runs'] if row['id'] == prior['id'])['receipt_sha256'] = contract.sha256_file(path)
    contract.atomic_write_json(session/'manifest.json', manifest)
    review_path = session/'reviews/070-stretch.json'; review = contract.load_json(review_path)
    review['run_receipt_sha256'] = contract.sha256_file(path); contract.atomic_write_json(review_path, review)
    result = run(case, assets(case))
    report = contract.load_json(session/result['processing_checks']['tone_adjustment']['report']['path'])
    upstream = next(row for row in report['upstream'] if row['protocol'] == 'stretch')
    assert len(upstream['transfer_chain']) == 2 and upstream['review']['verdict'] == 'accept'


def test_after_recomposition_tone_uses_the_actual_full_star_parent(case):
    session, *_ = case
    combined = recomposition.execute(case, recomposition.generate(case))
    review = session/'reviews'/(combined['id']+'.json')
    contract.atomic_write_json(review, recomposition.accepted_draft(session, combined))
    assert core.check_review(session, combined['id'], review)['status'] == 'review_valid'
    source = session/combined['primary_output']
    result = run(case, assets(case, source=source))
    assert result['branch_binding']['separation_run'] is None
    report = contract.load_json(session/result['processing_checks']['tone_adjustment']['report']['path'])
    assert report['source']['sha256'] == contract.sha256_file(source)
    assert report['upstream'][-1]['protocol'] == 'stars.recompose'
    assert any(row['role'] == 'stars' for row in report['regional']['regions'])


@pytest.mark.parametrize('change', ['missing', 'remaining_issue', 'upstream_assessment', 'parameter_reason', 'regions_hash', 'regions_size', 'geometry'])
def test_proposal_and_current_roi_evidence_are_required(case, change):
    session, *_ = case
    arguments = assets(case); script = arguments['script_value']
    value = script.read_text(); p = tone.declaration(value)
    if change == 'missing': value = '\n'.join(value.splitlines()[1:])
    else:
        if change in ('remaining_issue', 'upstream_assessment', 'parameter_reason'): p[change] = ''
        elif change == 'regions_hash': p['regions']['sha256'] = '0'*64
        elif change == 'regions_size': p['regions']['size'] += 1
        else:
            path = session/p['regions']['path']; rows = contract.load_json(path)
            rows['reference']['width'] += 1; contract.atomic_write_json(path, rows)
            p['regions'] = tone.record(session, path)
        value = '# tone-adjust: '+json.dumps(p)+'\n'+'\n'.join(value.splitlines()[1:])+'\n'
    script.write_text(value); support._write_ssf_provenance(session, script, 'tone.adjust')
    with pytest.raises(contract.ContractError): core.run_script(session, **arguments)
    assert not arguments['primary_output_value'].exists()


@pytest.mark.parametrize('field,value', [('goal_status', 'not_met'), ('goal_status', 'uncertain'),
    ('additional_benefit', 'not_observed'), ('additional_benefit', 'uncertain'),
    ('subject', 'degraded'), ('core', 'degraded'), ('sky', 'degraded'), ('stars', 'uncertain')])
def test_endpoint_success_does_not_replace_additional_benefit_and_region_review(case, field, value):
    session, *_ = case
    result = run(case, assets(case)); draft = accept(session, result)
    if field in ('goal_status', 'additional_benefit'): draft['tone_assessment'][field] = value
    else:
        next(row for row in draft['tone_assessment']['regions'] if row['region']['id'] == field)['assessment'] = value
    path = save_review(session, result, draft)
    with pytest.raises(contract.ContractError, match='additional benefit'): core.check_review(session, result['id'], path)
    draft['verdict'] = 'uncertain'; path = save_review(session, result, draft)
    assert core.check_review(session, result['id'], path)['status'] == 'review_valid'


def test_unchanged_scientific_pixels_cannot_claim_added_benefit(case, monkeypatch):
    session, parent, _, _, native = case
    arguments = assets(case)
    def unchanged(argv, **kwargs):
        completed = native(argv, **kwargs)
        if '--script='+str(arguments['script_value']) in argv:
            write(arguments['primary_output_value'], tone.read_scientific_pixels(parent)[0])
        return completed
    monkeypatch.setattr(core.subprocess, 'run', unchanged)
    result = run(case, arguments); path = save_review(session, result, accept(session, result))
    with pytest.raises(contract.ContractError, match='Unchanged pixels'): core.check_review(session, result['id'], path)


def test_observed_revision_uses_same_parent_and_replay_never_accumulates(case):
    session, parent, *_ = case
    initial = assets(case); result = run(case, initial)
    with pytest.raises(contract.ContractError, match='observation-bound'):
        core.run_script(session, **assets(case, run_id='086-unobserved'))
    revision = {'run_id': result['id'], 'evidence': [result['processing_checks']['tone_adjustment']['report']],
                'reason': 'Actual comparison shows the subject transition still needs revision'}
    revised = assets(case, run_id='087-revised', midtones=.49, revision=revision)
    newer = run(case, revised)
    assert newer['source_path'] == str(parent.relative_to(session))
    expected = tone.read_scientific_pixels(parent)[0]; m = .49
    expected = (m-1)*expected/((2*m-1)*expected-m)
    np.testing.assert_allclose(tone.read_scientific_pixels(revised['primary_output_value'])[0], expected, atol=1e-7)
    committed = support._session_bytes(session)
    assert core.run_script(session, **initial)['replayed'] and core.run_script(session, **revised)['replayed']
    assert support._session_bytes(session) == committed


def test_diagnostic_output_cannot_escape_through_a_symlink(case, tmp_path):
    session, parent, *_ = case
    arguments = assets(case)
    validated = core.run_script(session, validate_only=True, **arguments)
    write(arguments['primary_output_value'], tone.read_scientific_pixels(parent)[0]*1.01)
    outside = tmp_path/'outside-diagnostics'; outside.mkdir()
    (session/'reports/085-tone').symlink_to(outside, target_is_directory=True)
    with pytest.raises(contract.ContractError, match='symlink'):
        tone.measure(session, contract.load_json(session/'session.json'), parent,
                     arguments['primary_output_value'], validated['tone_proposal'], write=True)
    assert not list(outside.iterdir())


@pytest.mark.parametrize('what', ['report', 'regions', 'upstream'])
def test_drifting_diagnostics_or_upstream_acceptance_reject_readonly_replay(case, what):
    session, *_ = case
    arguments = assets(case); result = run(case, arguments)
    binding = result['tone_proposal']
    path = session/(result['processing_checks']['tone_adjustment']['report']['path'] if what == 'report'
                    else binding['proposal']['regions']['path'] if what == 'regions' else binding['upstream'][0]['review']['path'])
    path.write_bytes(path.read_bytes()+b'\n'); before = support._session_bytes(session)
    with pytest.raises(contract.ContractError): core.run_script(session, **arguments)
    assert support._session_bytes(session) == before


@pytest.mark.parametrize('case', ['single', 'hdr', 'hoo'], indirect=True)
def test_tone_preserves_frozen_rgb_star_replay_for_each_branch(case):
    session, _, path, p, _ = case
    arguments = assets(case); result = run(case, arguments)
    review = save_review(session, result, accept(session, result))
    assert core.check_review(session, result['id'], review)['status'] == 'review_valid'
    prepared = recomposition.reduction.prepare(session, arguments['primary_output_value'], '088-toned-stars')
    regions = session/p['regions']['path']; data = contract.load_json(regions)
    data['reference']['sha256'] = contract.sha256_file(arguments['primary_output_value']); contract.atomic_write_json(regions, data)
    p.update(source=prepared['source'], prepared=prepared['prepared'], regions=tone.record(session, regions))
    contract.atomic_write_json(path, p)
    toned_case = (session, arguments['primary_output_value'], *case[2:])
    combined = recomposition.execute(toned_case, recomposition.generate(toned_case))
    assert combined['status'] == 'success'
    assert combined['source_path'] == result['primary_output']
    _, frozen = core._load_run(session, '070-stretch')
    assert combined['transfer_chain'] == frozen['transfer_chain']
    if frozen.get('hdr_transfer'):
        assert combined['hdr_transfer'] == frozen['hdr_transfer']
    assert 'tone_chain' not in combined


@pytest.mark.parametrize('history', [False, True])
def test_tone_replay_review_and_finalize_preserve_new_and_historical_evidence(case, monkeypatch, history):
    session, *_ = case
    arguments = assets(case); result = run(case, arguments)
    review = save_review(session, result, accept(session, result))
    script, final = support._delivery_script(session, arguments['primary_output_value'])
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **kwargs:
        {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(path, include_path=False)}})
    delivered = core.run_script(session, protocol='delivery.render', script_value=script,
        source_value=arguments['primary_output_value'], expected_values=[final, final.with_suffix('.fit')], timeout=30)
    assert delivered['status'] == 'success'
    final_review = support._review(session, delivered['id'], final, parent_preview=arguments['expected_values'][-1])
    if history:
        manifest = contract.load_json(session/'manifest.json')
        synthetic = {row['id']: row for row in manifest['runs']
                     if 'knowledge_validation' not in contract.load_json(session/'runs'/row['receipt'])}
        manifest['runs'] = [row for row in manifest['runs'] if row['id'] not in synthetic]
        contract.atomic_write_json(session/'manifest.json', manifest)
        _freeze_historical_evidence(session, snapshot=PRIOR)
        manifest = contract.load_json(session/'manifest.json'); manifest['runs'].extend(synthetic.values())
        contract.atomic_write_json(session/'manifest.json', manifest)
    before = support._session_bytes(session)
    assert core.run_script(session, **arguments)['replayed']
    assert core.check_review(session, result['id'], review)['status'] == 'review_valid'
    assert support._session_bytes(session) == before
    selected = support._selection(session, delivered['id'], final, final_review)
    assert core.finalize_session(session, selection_value=selected, keep_intermediates=True)['status'] == 'success'
    for p in (session/'runs').glob('*.json'):
        assert p.read_bytes() == before[str(p.relative_to(session))]


def test_complete_outgoing_policy_and_implementation_are_pinned(tmp_path):
    # prior=tone-adjust-prior.json: 9 个域标签
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._PRE_TONE_EXECUTION_POLICY == PRIOR['execution_policy']
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = copy.deepcopy(PRIOR['implementation']); mixed['deep_sky_siril_core.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_core.py', include_path=False)
    assert not stretch.implementation_matches(tmp_path, mixed)
    for name, fp in PRIOR['references'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp})
