"""Fixed-catalog geometry using synthetic pixels and simulated Siril only."""
import copy
from contextlib import contextmanager
import json
import math
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
import deep_sky_star_reduction as reduction
from . import test_deep_sky_siril as support
from .test_recomposition_proposals import case, generate, execute, accepted_draft, write
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/recomposition-geometry-prior.json').read_text())


@contextmanager
def legacy_producer(monkeypatch):
    """Construct old-format fixtures; production replay must run without these shims."""
    current, context = sessions.execution_policy_is_current, reduction._generation_context
    def old_context(*args, **kwargs):
        values = context(*args, **kwargs)
        values[3]['helper'] = PRIOR['helper']
        return values
    with monkeypatch.context() as scoped:
        scoped.setattr(sessions, 'execution_policy_is_current', lambda p:
            p.get('execution_policy') == PRIOR['execution_policy'] or current(p))
        scoped.setattr(reduction, '_generation_context', old_context)
        yield


def catalog(*stars):
    rows = [dict(id=i+1, line_number=i+3, layer=1, profile='Gaussian', has_saturated=False,
                 duplicate=False, xpos=x, ypos=y, fwhmx=5.88705, fwhmy=5.88705)
            for i, (x, y) in enumerate(stars)]
    return {'status': 'measured', 'rows': rows, 'invalid_rows': []}


def synthetic(profile='Gaussian', strength=.3, centre=(70.3, 50.7)):
    yy, xx = np.mgrid[:128, :144]
    radius = np.hypot(xx-centre[0], yy-centre[1])
    star = np.exp(-radius**2/(2*2.5**2)) if profile == 'Gaussian' else (1+(radius/3)**2)**-3
    parent = np.full((3, 128, 144), .03)
    residual = .3*np.stack([star, .9*star, .7*star])[:, ::-1]
    table = catalog(centre); table['rows'][0]['profile'] = profile
    regions = [{'id': 'isolated', 'role': 'stars', 'rect': [35, 15, 105, 85]}]
    return parent, parent+residual, parent+strength*residual, table, regions


@pytest.mark.parametrize('profile', ['Gaussian', 'Moffat'])
@pytest.mark.parametrize('strength', [1., .654321, .3])
def test_finite_aperture_profiles_preserve_width_when_only_amplitude_changes(profile, strength):
    inputs = synthetic(profile, strength)
    measured = reduction.geometry_profiles(*inputs)
    row = measured['regions'][0]
    assert row['status'] == 'measured'
    full, candidate = (row['profiles'][key] for key in ('full_contribution', 'candidate'))
    assert candidate['peak']/full['peak'] == pytest.approx(strength)
    assert candidate['positive_flux']/full['positive_flux'] == pytest.approx(strength)
    assert row['comparisons'] == pytest.approx({'fwhm_ratio': 1., 'r90_ratio': 1.}, abs=1e-12)
    aperture = row['aperture_radius_px']
    if profile == 'Gaussian':
        fwhm = 2*math.sqrt(2*math.log(2))*2.5
        r90 = 2.5*math.sqrt(-2*math.log(1-.9*(1-math.exp(-aperture**2/(2*2.5**2)))))
    else:
        fwhm = 2*3*math.sqrt(2**(1/3)-1)
        r90 = 3*math.sqrt((1-.9*(1-(1+(aperture/3)**2)**-2))**(-.5)-1)
    assert full['fwhm_px'] == pytest.approx(fwhm, abs=.35)
    assert full['r90_px'] == pytest.approx(r90, abs=.2)
    assert measured['requires_visual_review'] and 'strength' not in measured
    json.dumps(measured, allow_nan=False)


def test_fixed_subpixel_top_left_position_converts_to_bottom_up_science():
    inputs = synthetic(centre=(65.2, 33.4))
    row = reduction.geometry_profiles(*inputs)['regions'][0]
    assert row['native_position'] == [65.2, 33.4]
    assert row['array_position'] == [65.2, 127-33.4]
    assert row['profiles']['full_contribution']['peak'] > .2
    assert row['linear_psf']['fwhmx_px'] == 5.88705
    assert row['profiles']['full_contribution']['fwhm_px'] != row['linear_psf']['fwhmx_px']


@pytest.mark.parametrize('issue', ['empty', 'crowded', 'saturated', 'duplicate', 'invalid', 'unsupported', 'edge', 'tiny'])
def test_unmeasurable_samples_are_retained_without_zero_width_or_reselection(issue):
    a, c, b, table, regions = synthetic()
    if issue == 'empty': table['rows'] = []
    elif issue == 'crowded': table = catalog((70.3, 50.7), (72., 52.))
    elif issue == 'saturated': table['rows'][0]['has_saturated'] = True
    elif issue == 'duplicate': table['rows'].append(copy.deepcopy(table['rows'][0]))
    elif issue == 'invalid': table['invalid_rows'] = [{'xpos': 69., 'ypos': 50.}]
    elif issue == 'unsupported': table['status'] = 'unsupported'
    elif issue == 'edge':
        table = catalog((2., 30.)); regions[0]['rect'] = [0, 20, 20, 40]
    elif issue == 'tiny': regions[0]['rect'] = [70, 50, 71, 51]
    row = reduction.geometry_profiles(a, c, b, table, regions)['regions'][0]
    assert row['status'] == 'not_measurable' and row['reasons']
    assert row['comparisons'] == {'fwhm_ratio': None, 'r90_ratio': None}
    assert 'profiles' not in row


@pytest.mark.parametrize('values,radii,reason', [([1., .4, .8, .2], [0., 1., 2., 3.], 'ambiguous'),
                                               ([1., .8, .7, .65], [0., 1., 2., 3.], 'truncated'),
                                               ([1., .2], [0., 2.], 'empty rings'),
                                               ([-1., -.2], [0., 1.], 'positive')])
def test_missing_or_ambiguous_half_height_is_not_filled(values, radii, reason):
    profile = reduction._display_profile(np.array(values), np.array(radii), 4., 0.)
    assert profile['fwhm_px'] is None and any(reason in r for r in profile['reasons'])


def test_background_structure_and_signed_negative_flux_remain_diagnostics():
    a, c, b, table, regions = synthetic()
    yy, xx = np.mgrid[:128, :144]
    gradient = (.0002*xx+.0004*yy)[None, ::-1]
    a, c, b = (v+gradient for v in (a, c, b))
    b[:, 70:78, 67:73] -= .1
    result = reduction.geometry_profiles(a, c, b, table, regions)
    row = result['regions'][0]
    assert row['background']['mad'] > 0
    low, high = row['background']['subject_aperture_range']; assert high > low
    assert row['profiles']['candidate']['negative_flux'] > 0
    assert result['requires_visual_review'] and 'accepted' not in result


def test_zero_flux_rings_do_not_move_the_ninetieth_percentile_outward():
    profile = reduction._display_profile(np.array([9., 0., -1., 0., 1.]), np.arange(5), 5., 0.)
    assert profile['r90_px'] == 0.
    assert profile['negative_flux'] == 1.


@pytest.mark.parametrize('issue', ['geometry', 'nonfinite'])
def test_invalid_scientific_geometry_and_values_fail(issue):
    args = list(synthetic())
    if issue == 'geometry': args[2] = args[2][:, :-1]
    else: args[2][0, 0, 0] = np.nan
    with pytest.raises(contract.ContractError): reduction.geometry_profiles(*args)


@pytest.mark.parametrize('channels,layer', [(1, 0), (3, 1)])
def test_catalog_recipe_selects_the_frozen_scientific_source_and_channel(tmp_path, channels, layer):
    source = write(tmp_path/'artifacts/linear.fit', np.ones((channels, 40, 40))*.03)
    commands, actual = reduction.catalog_recipe(tmp_path, {}, {'original': reduction.record(tmp_path, source)}, tmp_path/'stars.tsv')
    assert actual == layer
    assert processing.script_commands('\n'.join(commands))[:2] == [['load', str(source)], ['setfindstar', 'reset']]
    assert f'-layer={layer}' in commands[2] and '-maxstars=500' in commands[2]
    assert not any('makepsf' in r for r in commands)


@pytest.mark.parametrize('case', ['single', 'hdr', 'hoo'], indirect=True)
def test_prepared_analysis_and_candidate_share_catalog_current_subject_and_aperture(case):
    session, parent, _, proposal, native = case
    prepared = contract.load_json(session/proposal['prepared']['path'])
    catalog_record = prepared['geometry_catalog']
    assert catalog_record['source'] == prepared['bindings']['original']
    assert catalog_record['detection'] == {'setfindstar': ['reset'], 'layer': 1, 'maxstars': 500}
    assert catalog_record['coverage']['eligible'] == 1
    source = reduction.science_path(session, contract.load_json(session/'session.json'), catalog_record['source'])
    commands = processing.script_commands((session/prepared['execution']['script']['path']).read_text())
    index = next(i for i, r in enumerate(commands) if r[0] == 'findstar')
    assert commands[index-2] == ['load', str(source)] and source != parent
    analysis, *_ = reduction.analyze(session, contract.load_json(session/'session.json'), proposal['prepared'], proposal['regions'])
    assert analysis['geometry']['comparison_role'] == 'full_contribution_identity_before_proposal'
    assets = generate(case); run = execute(case, assets)
    assert run['status'] == 'success'
    diagnostics = contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    assert diagnostics['geometry']['catalog'] == catalog_record
    for before, after in zip(analysis['geometry']['regions'], diagnostics['geometry']['regions']):
        assert before['native_position'] == after['native_position']
        assert before['aperture_radius_px'] == after['aperture_radius_px']
        assert before['profiles']['full_contribution'] == after['profiles']['full_contribution']
    assert diagnostics['source'] == proposal['source']
    assert diagnostics['recommended_verdict'] == 'uncertain'
    assert catalog_record['table']['path'] in reduction.review_paths(session, run)
    assert len(reduction.review_paths(session, run)) <= 32
    assert not any(r[0] == 'findstar' for r in processing.script_commands(Path(assets['script_path']).read_text()))
    before = support._session_bytes(session); calls = len(native.calls)
    assert execute(case, assets)['replayed']
    assert len(native.calls) == calls and support._session_bytes(session) == before


@pytest.mark.parametrize('field', ['table', 'source', 'settings', 'geometry', 'implementation', 'recipe', 'roi', 'absent', 'badtable', 'oldhelper'])
def test_catalog_and_measurement_bindings_cannot_drift(case, field):
    session, _, path, proposal, _ = case
    prepared_path = session/proposal['prepared']['path']; prepared = contract.load_json(prepared_path)
    if field == 'table':
        table_path = session/prepared['geometry_catalog']['table']['path']; table_path.write_text(table_path.read_text()+'\n')
    elif field == 'recipe':
        script = session/prepared['execution']['script']['path']; script.write_text(script.read_text().replace('setfindstar reset', 'setfindstar reset -sigma=3'))
    elif field == 'roi':
        regions = session/proposal['regions']['path']; regions.write_text(regions.read_text()+'\n')
    else:
        if field == 'source': prepared['geometry_catalog']['source'] = prepared['bindings']['source']
        elif field == 'settings': prepared['geometry_catalog']['detection']['layer'] = 0
        elif field == 'geometry': prepared['geometry_catalog']['geometry']['width'] += 1
        elif field == 'implementation': prepared['bindings']['implementation']['siril_psf_samples.py']['size'] += 1
        elif field == 'absent': prepared.pop('geometry_catalog')
        elif field == 'badtable': prepared['geometry_catalog']['table'] = None
        elif field == 'oldhelper': prepared['bindings']['helper'] = PRIOR['helper']
        contract.atomic_write_json(prepared_path, prepared)
        proposal['prepared'] = reduction.record(session, prepared_path); contract.atomic_write_json(path, proposal)
    with pytest.raises(contract.ContractError): generate(case)


def test_report_tampering_cannot_be_repaired_or_used_for_acceptance(case):
    session, *_ = case
    assets = generate(case); run = execute(case, assets)
    path = session/run['processing_checks']['recomposition']['report']['path']
    report = contract.load_json(path); report['geometry']['regions'][0]['comparisons']['fwhm_ratio'] = .1
    contract.atomic_write_json(path, report)
    with pytest.raises(contract.ContractError): execute(case, assets)
    with pytest.raises(contract.ContractError): core.check_review(session, run['id'], None)


def test_geometry_materials_are_mandatory_but_do_not_fill_the_agent_assessment(case):
    session, *_ = case
    run = execute(case, generate(case))
    draft = core.check_review(session, run['id'], None)['review']
    assert draft['recomposition_assessment']['goal_status'] is None
    assert all(r['assessment'] is None and not r['observation'] for r in draft['recomposition_assessment']['regions'])
    accepted = accepted_draft(session, run)
    table = contract.load_json(session/run['recomposition_proposal']['prepared']['path'])['geometry_catalog']['table']['path']
    accepted['inspected_materials'] = [r for r in accepted['inspected_materials'] if r['path'] != table]
    review = session/'reviews/090-recompose.json'; contract.atomic_write_json(review, accepted)
    with pytest.raises(contract.ContractError): core.check_review(session, run['id'], review)


@pytest.mark.parametrize('case', ['single', 'hdr', 'hoo'], indirect=True)
def test_outgoing_recomposition_replays_and_finalizes_without_geometry_or_migration(case, monkeypatch):
    session, parent, request_path, request, native = case
    _freeze_historical_evidence(session, snapshot={**PRIOR, 'documents': PRIOR['references']}, run_id='010-input')
    with legacy_producer(monkeypatch):
        prepared = reduction.prepare(session, parent, '088-legacy')
        report = contract.load_json(session/prepared['prepared']['path'])
        assert report['bindings']['helper'] == PRIOR['helper'] and 'geometry_catalog' not in report
        request.update(prepared=prepared['prepared']); contract.atomic_write_json(request_path, request)
        assets = generate(case); run = execute(case, assets)
        assert run['status'] == 'success'
        review = session/'reviews/090-recompose.json'; contract.atomic_write_json(review, accepted_draft(session, run))
        script, final = support._delivery_script(session, Path(assets['primary_output']))
        monkeypatch.setattr(core, '_decode_scientific_output', lambda p, **kwargs:
            {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(p, include_path=False)}})
        delivery = core.run_script(session, protocol='delivery.render', script_value=script,
            source_value=assets['primary_output'], expected_values=[final, final.with_suffix('.fit')], timeout=30)
        assert delivery['status'] == 'success'
        final_review = support._review(session, delivery['id'], final,
            parent_preview=session/next(r['path'] for r in run['outputs'] if r['path'].endswith('.jpg')))
    frozen = support._session_bytes(session); calls = len(native.calls)
    assert execute(case, assets)['replayed']
    assert core.check_review(session, run['id'], review)['status'] == 'review_valid'
    assert len(native.calls) == calls and support._session_bytes(session) == frozen
    for node in (session/'reports/090-recompose/recomposition-diagnostics.json',):
        assert 'geometry' not in contract.load_json(node)
    with pytest.raises(contract.ContractError, match='new session'):
        reduction.prepare(session, parent, '089-forbidden')
    selection = support._selection(session, delivery['id'], final, final_review)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert (session/'session.json').read_bytes() == frozen['session.json']
    for name, data in frozen.items():
        if name.split('/')[0] in ('runs', 'reviews', 'scripts', 'reports'):
            assert (session/name).read_bytes() == data


def test_exact_outgoing_policy_references_and_complete_implementation_are_registered(tmp_path):
    # prior=recomposition-geometry-prior.json: guidance, chroma_denoise, color_preservation,
    # recomposition_geometry, linear_denoise_backend, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY == PRIOR['execution_policy']
    assert reduction.PRE_GEOMETRY_HELPER == PRIOR['helper']
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = {**PRIOR['implementation'], 'deep_sky_star_reduction.py': contract.fingerprint(ROOT/'scripts/deep_sky_star_reduction.py', include_path=False)}
    assert not stretch.implementation_matches(tmp_path, mixed)
    for name, fp in PRIOR['references'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp})
    unknown = {**PRIOR['execution_policy'], 'recomposition_geometry': 'unknown'}
    assert not sessions.known_execution_policy(unknown)
    with pytest.raises(contract.ContractError): reduction.geometry_enabled({'execution_policy': unknown})
