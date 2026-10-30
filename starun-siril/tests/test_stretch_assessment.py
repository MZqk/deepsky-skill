"""Agent-authored per-ROI decisions, real detail controls and immutable old reviews."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_visibility as visibility
import deep_sky_star_reduction as reduction
from . import test_deep_sky_siril as support
from .test_recomposition_sources_v11 import source_case, accept, generated_run, recompose_assets
from .test_starnet_linear_bridge import native
from .test_dualband_hoo import dual_case
from .test_deep_sky_siril_stretch import context
from .test_stretch_v22 import case
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

from ._stretch_factory import solve_bound, bind_hoo, historical_parameters

ROOT = Path(__file__).parents[1]
PRIOR = contract.load_json(ROOT/'tests/fixtures/stretch-assessment-prior.json')


def regression(block, *, demonstrated=False):
    core_region = next(row['region'] for row in block['structures'] if row['region']['id'] == 'core')
    return {'loss': 'Synthetic outer structure is below the reference visibility.',
            'core_conflict': 'demonstrated' if demonstrated else 'not_demonstrated',
            'core_region': core_region if demonstrated else None, 'evidence': [],
            'tradeoff_reason': 'Agent explicitly retains this measured display tradeoff; no automatic visual ranking.'}


def test_missing_or_inconsistent_structural_conclusions_are_rejected(source_case):
    session, _, _, parent, *_ = source_case
    run = contract.load_json(session/'runs/070-stretch.json')
    report = contract.load_json(session/run['stretch_visibility']['path'])
    review_path = session/'reviews/070-stretch.json'
    original = review_path.read_bytes()
    valid = json.loads(original)
    block = valid['stretch_assessment']
    assert report['review_contract'] == visibility.BOUND_REVIEW_CONTRACT
    assert report['controls']
    assert all(row['detail_effect'] == 'unchanged' for row in block['structures'])
    draft = core.check_review(session, run['id'], None)['review']['stretch_assessment']
    assert draft['adoption_reason'] == ''
    assert all(row['reference_visibility'] is None and row['observation'] == '' for row in draft['structures'])

    def rejected(change, match):
        value = copy.deepcopy(valid)
        change(value)
        contract.atomic_write_json(review_path, value)
        with pytest.raises(contract.ContractError, match=match):
            core.check_review(session, run['id'], review_path)
        review_path.write_bytes(original)

    rejected(lambda r: r.pop('stretch_assessment'), 'must bind')
    rejected(lambda r: r['stretch_assessment'].update(adoption_reason=' '), 'adoption reason')
    rejected(lambda r: r['stretch_assessment']['structures'].pop(), 'omitted')
    rejected(lambda r: r['stretch_assessment']['structures'].append(copy.deepcopy(block['structures'][0])), 'Duplicate')
    alias = copy.deepcopy(block['structures'][0])
    alias['region']['regions']['path'] = str((session/alias['region']['regions']['path']).resolve())
    rejected(lambda r: r['stretch_assessment']['structures'].append(alias), 'Duplicate')
    rejected(lambda r: r['stretch_assessment']['structures'][0]['region'].update(id='missing'), 'Unknown')
    rejected(lambda r: r['stretch_assessment'].update(visibility={}), 'must bind')
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(observation=' '), 'observations')
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(reference_visibility='uncertain'), 'cannot leave')
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(detail_effect='not_applicable'), 'actual presence')
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(reference_visibility='not_met'), 'not_met')

    loss = regression(block, demonstrated=True)
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(reference_visibility='partial', regression=loss),
             'actual comparison evidence')
    loss['evidence'] = [report['panel']]
    wrong_core = {**loss, 'core_region': block['structures'][0]['region']}
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(reference_visibility='partial', regression=wrong_core),
             'actual core ROI')
    foreign = session/'reports/foreign-proof.json'
    contract.atomic_write_json(foreign, {'source': 'unrelated'})
    wrong_proof = {**loss, 'evidence': [base.record(session, foreign)]}
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(reference_visibility='partial', regression=wrong_proof),
             'not a bound same-source')
    rejected(lambda r: r['stretch_assessment']['structures'][0].update(reference_visibility='partial',
        regression={**loss, 'tradeoff_reason': ' '}), 'tradeoff reason')
    changed = session/'reports/changed-regions.json'
    contract.atomic_write_json(changed, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': '0'*64, 'width': 512, 'height': 512},
        'regions': [{'id': 'shell', 'role': 'faint', 'rect': [240, 240, 272, 272]}]})
    rejected(lambda r: r['stretch_assessment']['structures'][0]['region'].update(regions=base.record(session, changed)),
             'Malformed Stage 7 assessment')
    extra = session/'reports/extra-regions.json'
    contract.atomic_write_json(extra, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(parent), 'width': 512, 'height': 512},
        'regions': [{'id': 'filament', 'role': 'faint', 'rect': [240, 240, 248, 248]}]})
    value = copy.deepcopy(valid)
    value['stretch_assessment']['structures'].append({**copy.deepcopy(block['structures'][0]),
        'region': {'regions': base.record(session, extra), 'id': 'filament'}})
    contract.atomic_write_json(review_path, value)
    assert core.check_review(session, run['id'], review_path)['status'] == 'review_valid'
    saved = extra.read_bytes();extra.write_bytes(saved+b'drift')
    with pytest.raises(contract.ContractError, match='hash changed'):
        core.check_review(session, run['id'], review_path)
    extra.write_bytes(saved);review_path.write_bytes(original)


def test_regression_tradeoffs_remain_agent_decisions(source_case):
    session, *_ = source_case
    run = contract.load_json(session/'runs/070-stretch.json')
    report = contract.load_json(session/run['stretch_visibility']['path'])
    path = session/'reviews/070-stretch.json'
    original = path.read_bytes()
    for conclusion in ('demonstrated', 'not_demonstrated', 'uncertain'):
        review = json.loads(original)
        block = review['stretch_assessment']
        loss = regression(block, demonstrated=conclusion == 'demonstrated')
        loss['core_conflict'] = conclusion
        loss['evidence'] = [{key: report['panel'][key] for key in ('path', 'sha256')}]
        block['structures'][0].update(reference_visibility='partial', regression=loss)
        contract.atomic_write_json(path, review)
        assert core.check_review(session, run['id'], path)['status'] == 'review_valid'
    review = json.loads(original)
    review.update(verdict='uncertain', gates={**review['gates'], 'structure': 'uncertain'})
    review['stretch_assessment']['structures'][0]['reference_visibility'] = 'uncertain'
    contract.atomic_write_json(path, review)
    assert core.check_review(session, run['id'], path)['status'] == 'review_valid'
    path.write_bytes(original)


@pytest.mark.parametrize('source_case', ['chain', 'hdr'], indirect=True)
def test_real_prefix_and_gentle_controls_do_not_change_formal_pixels(source_case):
    session, primary, _, source, *_ = source_case
    run = contract.load_json(session/'runs/070-stretch.json')
    report = contract.load_json(session/run['stretch_visibility']['path'])
    control, = report['controls']
    assert control['kind'] == ('hdr_gentle' if 'hdr_transfer' in run else 'first_prefix')
    assert control['candidate'] == base.record(session, primary)
    if 'hdr_transfer' in run:
        assert control['input']['path'] == run['hdr_transfer']['layers']['gentle']
    else:
        parameters=contract.load_json(session/run['adaptive_parameters']['parameters']['path'])
        assert control['input'] == parameters['steps'][0]['selected']['.fit']
        assert control['transfer'] == []
    assert np.array_equal(base.pixels(session/control['candidate_view']['science']['path']), base.pixels(primary))
    assert len(core.check_review(session, run['id'], None)['review']['inspected_materials']) <= 32
    assert core.run_script(session, protocol='stretch', script_value=session/run['script']['path'], source_value=source,
        primary_output_value=primary, expected_values=[session/row['path'] for row in run['outputs']], timeout=60)['replayed']
    review_path = session/'reviews/070-stretch.json';original = review_path.read_bytes()
    review = json.loads(original);block = review['stretch_assessment']
    block['structures'][0].update(detail_effect='regressed', regression=regression(block))
    block['structures'][0]['regression']['evidence']=[report['panel']]
    block['structures'][0]['regression']['loss'] = 'Synthetic local contrast is weaker than the actual base control.'
    contract.atomic_write_json(review_path, review)
    assert core.check_review(session, run['id'], review_path)['status'] == 'review_valid'
    review_path.write_bytes(original)


def test_exact_pre_assessment_bindings_remain_immutable(case, monkeypatch):
    session, _, payload, path, request, _ = case
    request['schema'] = protected.PROPOSAL_REQUEST
    request['operators'][0]['factors'] = {'midtones': .05}
    path.write_text(json.dumps(request))
    with monkeypatch.context() as legacy:
        from ._policy_pins import outgoing_stretch_producer
        outgoing_stretch_producer(legacy)
        legacy.setitem(payload, 'execution_policy', copy.deepcopy(contract.load_json(ROOT/'references/stretch-multistage-history.json')['execution_policy']))
        contract.atomic_write_json(session/'session.json', payload)
        legacy.setattr(base, 'optimization_enabled', lambda payload: False)
        solved = solve_bound(session,path,'071-prior-assessment')
    contract.atomic_write_json(session/'session.json', payload)
    report_path,report=historical_parameters(session,solved,PRIOR)
    assert protected._PRE_ASSESSMENT_BINDINGS == {k: PRIOR[k] for k in ('execution_policy', 'implementation', 'profiles')}
    report['bindings']['implementation'] = copy.deepcopy(PRIOR['implementation'])
    contract.atomic_write_json(report_path, report);before = report_path.read_bytes()
    payload['execution_policy'] = copy.deepcopy(PRIOR['execution_policy'])
    contract.atomic_write_json(session/'session.json', payload)
    base.load_parameters(session, payload, report_path)
    assert report_path.read_bytes() == before
    report['bindings']['implementation']['deep_sky_siril_visibility.py'] = protected.implementation()['deep_sky_siril_visibility.py']
    contract.atomic_write_json(report_path, report)
    with pytest.raises(contract.ContractError, match='implementation'):
        base.load_parameters(session, payload, report_path)


def test_unmarked_review_replay_and_finalize_preserve_original_bytes(source_case,monkeypatch):
    from ._policy_pins import replay_and_finalize_without_migration
    session,*_=source_case
    run=contract.load_json(session/'runs/070-stretch.json')
    report=contract.load_json(session/run['stretch_visibility']['path'])
    report.pop('review_contract');report.pop('controls')
    path=session/'reports/unmarked-visibility-fixture.json';contract.atomic_write_json(path,report)
    before=path.read_bytes()
    assert visibility.assessment_skeleton(session,base.record(session,path)) is None
    assert path.read_bytes()==before
    directory=session.parent/'prior-assessment-fixture';directory.mkdir()
    snapshot=directory/'snapshot.json';contract.atomic_write_json(snapshot,PRIOR)
    replay_and_finalize_without_migration(directory,monkeypatch,snapshot)


def test_native_hoo_detail_controls_use_actual_monochrome_prefixes(dual_case):
    from .test_stretch_v22 import field_request
    import deep_sky_siril_dualband as dual
    session, _, _, parent, *_ = dual_case
    path, request = field_request(session, parent, name='075-detail-hoo')
    request['schema'] = 'starun-siril.dualband-request.v4'
    first = request['operators'][0]
    first['factors'] = {'midtones': .1}
    second = {'method': 'asinh', 'purpose': 'Review measured monochrome detail relative to the first prefix',
              'factors': {'stretch': 1.2}, 'constraints': copy.deepcopy(first['constraints'])}
    request['operators'] = {'rgb': [copy.deepcopy(first)],
        'ha': [copy.deepcopy(first), copy.deepcopy(second)], 'oiii': [copy.deepcopy(first), copy.deepcopy(second)]}
    request['oiii_denoise'] = {'enabled': False, 'reason': 'No extra denoising in this controlled comparison'}
    path.write_text(json.dumps(request))
    path,request=bind_hoo(session,request,'074-hoo-detail-plan')
    solved = dual.solve(session,path,'075-detail-hoo',90)
    assert solved['status'] == 'solved', solved
    assets = dual.generate(session, solved['report'])
    run = core.run_script(session, protocol='stretch', script_value=assets['script'], source_value=parent,
        expected_values=assets['expected'], primary_output_value=assets['primary_output'], timeout=60)
    assert run['status'] == 'success', run
    report = contract.load_json(session/run['stretch_visibility']['path'])
    assert {control['id'] for control in report['controls']} == {'ha', 'oiii'}
    parameters = contract.load_json(Path(solved['report']))
    for control in report['controls']:
        child = contract.load_json(session/parameters['chains'][control['id']]['path'])
        assert control['input'] == child['steps'][0]['selected']['.fit']
        assert control['kind'] == 'monochrome_first_prefix' and control['transfer'] == []
        final = session/run['dualband_transfer']['layers'][control['id']+'-stretched']
        assert control['candidate'] == base.record(session, final)
        assert np.array_equal(base.pixels(session/control['candidate_view']['science']['path']), base.pixels(final))
    draft = core.check_review(session, run['id'], None)['review']
    assert len(draft['inspected_materials']) <= 32
    assert all(row['detail_effect'] is None for row in draft['stretch_assessment']['structures'])
    accept(session, run['id'])
