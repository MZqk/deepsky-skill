"""Independent contribution and contour controls, native science and immutable evidence."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_star_reduction as stars
from .test_recomposition_proposals import case, generate, execute, accepted_draft
from .test_recomposition_geometry import synthetic
from .test_stellar_color import test_native_prepare_formal_and_replay as native_flow
from ._policy_pins import (assert_contract_domains, assert_command_policy_hashed,
    assert_document_fingerprints_replayable, replay_and_finalize_without_migration)

ROOT = Path(__file__).parents[1]
HISTORY = ROOT/'references/star-contour-history.json'
PARAMETERS = {'amount': .4, 'radius_px': 7}


@pytest.mark.parametrize('profile', ['Gaussian', 'Moffat'])
@pytest.mark.parametrize('strength', [.3, .7, 1.])
def test_contour_contracts_profiles_while_contribution_scales_only_amplitude(profile, strength):
    parent, full, _, table, regions = synthetic(profile=profile)
    raw = (full-parent).astype('float32')
    gain = stars.contour_weights(raw, PARAMETERS)
    control = parent+strength*raw
    candidate = parent+strength*raw*gain
    before = stars.geometry_profiles(parent, full, control, table, regions)['regions'][0]
    after = stars.geometry_profiles(parent, control, candidate, table, regions)['regions'][0]
    assert before['comparisons'] == pytest.approx({'fwhm_ratio': 1, 'r90_ratio': 1}, abs=1e-7)
    assert after['comparisons']['fwhm_ratio'] < 1 and after['comparisons']['r90_ratio'] < 1
    np.testing.assert_allclose((raw*gain)[1], (raw*gain)[0]*.9, atol=2e-8)
    np.testing.assert_allclose((raw*gain)[2], (raw*gain)[0]*.7, atol=2e-8)


def test_zero_continuity_signed_pixels_peaks_and_zero_signal():
    parent, full, _, _, _ = synthetic()
    raw = (full-parent).astype('float32')
    raw[:, 0, 0] = [-.01, .02, .03]
    raw[:, 0, 1] = 0
    identity = stars.contour_weights(raw, {**PARAMETERS, 'amount': 0})
    almost = stars.contour_weights(raw, {**PARAMETERS, 'amount': 1e-8})
    gain = stars.contour_weights(raw, PARAMETERS)
    assert np.array_equal(identity, np.ones_like(identity))
    assert np.max(np.abs(almost-identity)) < 1e-6
    assert gain[0, 0] == gain[0, 1] == 1
    assert np.array_equal((raw*gain)[:, 0, 0], raw[:, 0, 0])
    peak = np.unravel_index(np.argmax(raw[0]), raw.shape[1:])
    assert gain[peak] == 1 and np.all((gain > 0) & (gain <= 1))
    assert np.all(stars.contour_weights(np.zeros_like(raw), PARAMETERS) == 1)


def test_two_pass_maximum_handles_edges_and_crowded_peaks():
    raw = np.zeros((1, 17, 19), dtype='float32')
    raw[0, 0, 0], raw[0, 0, 1] = .7, .2
    raw[0, 8, 8], raw[0, 8, 10] = .8, .3
    gain = stars.contour_weights(raw, {'amount': .6, 'radius_px': 3})
    assert gain[0, 0] == gain[8, 8] == 1
    assert gain[0, 1] < 1 and gain[8, 10] < 1
    assert gain[16, 18] == 1


def test_signed_pixels_keep_their_weight_but_still_define_the_local_luminance_peak():
    raw = np.zeros((3, 9, 9), dtype='float32')
    raw[:, 4, 4] = [-.1, 1., .3]
    raw[:, 4, 5] = .2
    gain = stars.contour_weights(raw, {'amount': .4, 'radius_px': 2})
    peak = float(stars._luminance(raw.astype(float))[4, 4])
    m = .5+.4*.4
    assert gain[4, 4] == 1
    assert gain[4, 5] == pytest.approx((1-m)/(m-(2*m-1)*(.2/peak)))


@pytest.mark.parametrize('declaration', [[], None, {'path': 'weights.tif'},
    {'path': 'reports/contour.json', 'sha256': '0'*64, 'size': True}])
def test_malformed_contour_declarations_fail_closed(tmp_path, declaration):
    payload = {'execution_policy': {'recomposition_contour': stars.CONTOUR_POLICY}}
    with pytest.raises(contract.ContractError, match='path/hash/size'):
        stars.contour_input(tmp_path, payload, '# star-contour: '+json.dumps(declaration))


def test_nonfinite_scientific_pixels_cannot_generate_weights():
    data = np.ones((3, 32, 32), dtype='float32')
    data[0, 5, 5] = np.nan
    with pytest.raises(contract.ContractError, match='finite'):
        stars.contour_weights(data, PARAMETERS)


@pytest.mark.parametrize('contour', [None, {}, {'amount': True, 'radius_px': 2},
    {'amount': float('nan'), 'radius_px': 2}, {'amount': float('inf'), 'radius_px': 2},
    {'amount': -.1, 'radius_px': 2}, {'amount': 1.1, 'radius_px': 2},
    {'amount': .4, 'radius_px': True}, {'amount': .4, 'radius_px': 2.5},
    {'amount': .4, 'radius_px': 0}, {'amount': .4, 'radius_px': 32},
    {'amount': .4, 'radius_px': 2, 'method': 'other'}])
def test_invalid_proposals_fail_before_native_execution_or_assets(case, contour):
    session, _, path, proposal, native = case
    path.write_text(json.dumps({**proposal, 'contour': contour}))
    with pytest.raises(contract.ContractError):
        generate(case)
    assert len(native.calls) == 1
    assert not (session/'reports/090-recompose').exists()
    assert not (session/'scripts/090-recompose.ssf').exists()


@pytest.mark.parametrize('contour', [None, {'amount': 0, 'radius_px': 5}])
def test_disabled_contour_retains_the_original_scientific_recipe(case, contour):
    session, _, path, proposal, _ = case
    if contour is not None:
        contract.atomic_write_json(path, {**proposal, 'contour': contour})
    assets = generate(case)
    text = Path(assets['script_path']).read_text()
    assert '# star-contour:' not in text and 'rgbcomp' not in text
    assert len(assets['expected_outputs']) == 5
    assert not (session/'reports/090-recompose').exists()
    run = execute(case, assets)
    assert run['status'] == 'success', run
    report = contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    assert 'contour' not in report


@pytest.mark.parametrize('case', ['single', 'mono', 'hdr', 'hoo'], indirect=True)
def test_bound_contour_recipe_runs_replays_and_separates_diagnostics(case):
    session, parent, path, proposal, native = case
    contract.atomic_write_json(path, {**proposal, 'contour': PARAMETERS})
    assets = generate(case)
    assert len(native.calls) == 1
    run = execute(case, assets)
    assert run['status'] == 'success', run
    report = contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    assert report['contour']['errors']['multiply'] < 1e-6
    assert report['contour']['contour_geometry']['matching'] == report['geometry']['matching']
    assert report['contour']['contribution_only']['path'] == run['branch_binding']['contribution_only']
    assert 'raw_signed_stars' in report['contour']['panel_labels']
    assert processing.verify_recomposition_receipt(session, contract.load_json(session/'session.json'), run)
    control = stars.read_scientific_pixels(session/run['branch_binding']['contribution_only'])[0]
    a = stars.read_scientific_pixels(parent)[0]
    raw = stars.read_scientific_pixels(session/run['branch_binding']['display_stars'])[0]
    np.testing.assert_allclose(control, a+proposal['strength']*raw, atol=1e-6)
    draft = core.check_review(session, run['id'], None)['review']
    assert draft['recomposition_assessment']['contribution_assessment']['assessment'] is None
    assert draft['recomposition_assessment']['contour_assessment']['benefit'] is None
    assert len(draft['inspected_materials']) <= 32
    assert execute(case, assets)['replayed']


@pytest.mark.parametrize('case', ['single', 'mono', 'hdr', 'hoo'], indirect=True)
@pytest.mark.parametrize('independent', [False, True])
def test_native_contour_and_independent_star_curve_preserve_exact_science(case, monkeypatch, independent):
    curve = [{'command': 'asinh', 'parameters': ['-human', '2', '0', '-clipmode=rgbblend']}] if independent else None
    native_flow(case, monkeypatch, curve, contour=PARAMETERS)
    session = case[0]
    run = contract.load_json(session/'runs/090-native.json')
    report = contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    assert max(report['contour']['errors'].values()) < 1e-6
    if independent:
        assert run['recomposition_proposal']['star_curve']['transfer_chain'] == curve


@pytest.mark.parametrize('target', ['contour-weights.tif', 'contour-weights.png', 'contour.json'])
def test_auxiliary_tampering_is_rejected_before_scientific_execution(case, target):
    session, _, path, proposal, native = case
    contract.atomic_write_json(path, {**proposal, 'contour': PARAMETERS})
    assets = generate(case)
    asset = session/'reports/090-recompose'/target
    if target.endswith('.tif'):
        with Image.open(asset) as image:
            values = np.asarray(image).copy()
        values[0, 0] = .123
        Image.fromarray(values).save(asset)
    else:
        asset.write_bytes(asset.read_bytes()+b' ')
    with pytest.raises(contract.ContractError):
        execute(case, assets)
    assert len(native.calls) == 1


@pytest.mark.parametrize('change', ['source', 'regions', 'amount', 'radius'])
def test_proposal_and_auxiliary_sources_cannot_drift(case, change):
    _, _, path, proposal, native = case
    value = {**proposal, 'contour': dict(PARAMETERS)}
    contract.atomic_write_json(path, value)
    assets = generate(case)
    if change in ('source', 'regions'):
        value[change] = {**value[change], 'sha256': '0'*64}
    else:
        value['contour']['amount' if change == 'amount' else 'radius_px'] = .8 if change == 'amount' else 3
    contract.atomic_write_json(path, value)
    with pytest.raises(contract.ContractError):
        execute(case, assets)
    assert len(native.calls) == 1


@pytest.mark.parametrize('change', ['extra-command', 'metadata-override', 'missing-output', 'missing-declaration'])
def test_incomplete_or_expanded_contour_scripts_are_rejected(case, change):
    _, _, path, proposal, native = case
    contract.atomic_write_json(path, {**proposal, 'contour': PARAMETERS})
    assets = generate(case)
    script = Path(assets['script_path'])
    if change == 'missing-output':
        assets['expected_outputs'] = [p for p in assets['expected_outputs'] if 'contribution-only' not in p]
    else:
        text = script.read_text()
        if change == 'extra-command':
            text += 'mtf 0 .4 1\n'
        elif change == 'metadata-override':
            text = text.replace('stat main', 'update_key EXPTIME 999\nstat main')
        else:
            text = '\n'.join(row for row in text.splitlines() if not row.startswith('# star-contour:'))+'\n'
        script.write_text(text)
        provenance = Path(assets['provenance_path'])
        value = contract.load_json(provenance); value['script']['sha256'] = contract.sha256_file(script)
        contract.atomic_write_json(provenance, value)
    with pytest.raises(contract.ContractError):
        execute(case, assets)
    assert len(native.calls) == 1


def test_contour_review_requires_measured_benefit_and_inspected_mask(case):
    session, _, path, proposal, _ = case
    contract.atomic_write_json(path, {**proposal, 'contour': PARAMETERS})
    run = execute(case, generate(case))
    assert run['status'] == 'success', run
    draft = accepted_draft(session, run)
    block = draft['recomposition_assessment']
    block['contour_assessment'].update(benefit='observed', observation='Compared the same-contribution contour panel')
    review = session/'reviews/090-recompose.json'
    contract.atomic_write_json(review, draft)
    with pytest.raises(contract.ContractError, match='measurable contraction'):
        core.check_review(session, run['id'], review)
    block['contour_assessment']['regions'] = ['nonexistent']
    contract.atomic_write_json(review, draft)
    with pytest.raises(contract.ContractError, match='fixed regions'):
        core.check_review(session, run['id'], review)
    block['contour_assessment']['regions'] = ['stellar-core']
    contract.atomic_write_json(review, draft)
    assert core.check_review(session, run['id'], review)['status'] == 'review_valid'
    draft['verdict'] = 'uncertain'; block['contour_assessment'].update(benefit='uncertain', regions=[])
    contract.atomic_write_json(review, draft)
    assert core.check_review(session, run['id'], review)['status'] == 'review_valid'
    draft['inspected_materials'] = [r for r in draft['inspected_materials'] if not r['path'].endswith('contour-weights.png')]
    contract.atomic_write_json(review, draft)
    with pytest.raises(contract.ContractError, match='missing'):
        core.check_review(session, run['id'], review)


def test_outgoing_policy_and_full_implementation_are_frozen(tmp_path, monkeypatch):
    before = contract.load_json(HISTORY)
    assert sessions._PRE_CONTOUR_BINDINGS == before
    assert not sessions.execution_policy_is_current({'execution_policy': before['execution_policy']})
    assert_contract_domains(before['execution_policy'])
    assert_document_fingerprints_replayable(before['documents'])
    assert_command_policy_hashed(before)
    current = contract.load_json(ROOT/'references/command-policy.json')
    current['protocol_commands']['stars.recompose'].remove('rgbcomp')
    current['protocol_commands']['stars.recompose'].remove('update_key')
    assert current == json.loads(before['command_policy'])
    replay_and_finalize_without_migration(tmp_path, monkeypatch, HISTORY)


def test_outgoing_stage9_prepared_and_diagnostics_replay_without_new_fields(case):
    session, parent, path, proposal, _ = case
    run = execute(case, generate(case))
    assert run['status'] == 'success', run
    old = contract.load_json(HISTORY)
    payload = contract.load_json(session/'session.json')
    payload['execution_policy'] = old['execution_policy']
    payload.pop('session_sha256'); payload['session_sha256'] = contract.stable_hash(payload)
    contract.atomic_write_json(session/'session.json', payload)
    prepared_path = session/proposal['prepared']['path']
    prepared = contract.load_json(prepared_path)
    prepared['bindings'].update(helper=old['helpers']['deep_sky_star_reduction.py'], implementation=old['implementation'])
    contract.atomic_write_json(prepared_path, prepared)
    proposal = {**proposal, 'schema': stars.LEGACY_REQUEST, 'prepared': stars.record(session, prepared_path)}
    contract.atomic_write_json(path, proposal)
    _, binding, _, _ = stars.request_binding(session, payload, path, run['id'], verify_parent=False)
    run['recomposition_proposal'] = binding
    for key in ('report', 'panel'):
        (session/run['processing_checks']['recomposition'][key]['path']).unlink()
    run['processing_checks']['recomposition'] = stars.measure(session, parent, session/run['primary_output'], run, write=True)
    materials = [session/'session.json', prepared_path, path,
        *(session/run['processing_checks']['recomposition'][key]['path'] for key in ('report', 'panel'))]
    frozen = {p: p.read_bytes() for p in materials}
    assert stars.measure(session, parent, session/run['primary_output'], run) == run['processing_checks']['recomposition']
    assert 'contribution_assessment' not in stars.assessment_skeleton(session, run)
    with pytest.raises(contract.ContractError, match='new session'):
        stars.generate_recompose_assets(session, request=path, run_id='091-no-upgrade')
    assert frozen == {p: p.read_bytes() for p in materials}


@pytest.mark.parametrize('channels', [1, 3])
def test_native_precision_signed_pixels_wcs_and_exposure(tmp_path, channels):
    from astropy.io import fits
    from deep_sky_siril_chroma import run_native
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():
        pytest.skip('Native Siril unavailable')
    y, x = np.indices((64, 80))
    raw = np.stack([(.6-c*.1)*np.exp(-((x-35)**2+(y-23)**2)/18) for c in range(channels)]).astype('float32')
    raw[:, 10, 15] = -.01 if channels == 1 else [-.01, .02, .03]
    parent = np.full_like(raw, .9)
    work = tmp_path/'reports/native'; work.mkdir(parents=True)
    (tmp_path/'artifacts').mkdir()
    header = fits.Header({'CTYPE1':'RA---TAN', 'CTYPE2':'DEC--TAN', 'CRVAL1':83., 'CRVAL2':22.,
        'CRPIX1':40., 'CRPIX2':32., 'CD1_1':-.001, 'CD1_2':0., 'CD2_1':0., 'CD2_2':.001,
        'EXPTIME':120., 'LIVETIME':240., 'STACKCNT':2})
    source, residual = (tmp_path/'artifacts'/name for name in ('parent.fit', 'raw.fit'))
    for file, values in ((source,parent),(residual,raw)):
        fits.writeto(file, values.squeeze(), header)
    gain = stars.contour_weights(raw, PARAMETERS)
    weight = work/'weights.tif'; Image.fromarray(gain[::-1]).save(weight)
    paths = stars.contour_paths(tmp_path, '090-native', channels)
    candidate = tmp_path/'artifacts/candidate.fit'
    rows = ['requires 1.4.4 1.5.0', 'set32bits',
        *stars.contour_rows(tmp_path, source, residual, paths, weight, .8),
        'pm "$artifacts/parent$ + 0.8 * $artifacts/090-native-contour-stars$" -nosum',
        *stars.exposure_rows(source), 'save '+json.dumps(str(candidate))+' -chksum', 'close']
    rows.insert(2, 'cd '+json.dumps(str(tmp_path)))
    run_native(tmp_path, work, 'native-contour', '\n'.join(rows)+'\n', {'path':str(binary)})
    shaped = stars.read_scientific_pixels(paths['contour_stars'])[0]
    actual = stars.read_scientific_pixels(candidate)[0]
    np.testing.assert_array_equal(stars.read_scientific_pixels(paths['contour_weight'])[0][0], gain)
    np.testing.assert_allclose(shaped, raw*gain, rtol=0, atol=1e-7)
    np.testing.assert_array_equal(shaped[:,10,15], raw[:,10,15])
    np.testing.assert_array_max_ulp(actual, (parent.astype(float)+.8*shaped.astype(float)).astype('float32'), maxulp=2)
    assert actual.max() > 1 and fits.getheader(candidate)['BITPIX'] == -32
    processing.verify_color_metadata(source, candidate)
    processing.verify_color_metadata(source, paths['contribution_only'])
    processing.verify_color_metadata(residual, paths['contour_stars'])
