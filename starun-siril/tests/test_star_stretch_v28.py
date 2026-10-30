"""Strict stellar Modified Asinh, native paired replay and immutable pre-v28 evidence."""
import copy
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
import deep_sky_star_reduction as stars
from .test_recomposition_proposals import case, execute
from .test_star_curves import request_for
from .test_stellar_color import test_native_prepare_formal_and_replay as native_flow
from ._policy_pins import (assert_command_policy_hashed, assert_contract_domains,
                           assert_document_fingerprints_replayable,
                           replay_and_finalize_without_migration)

ROOT = Path(__file__).parents[1]
PRIOR = contract.load_json(ROOT/'tests/fixtures/star-stretch-prior.json')
PARAMETERS = ['-D=.4', '-LP=0', '-SP=.1', '-HP=.7', '-human', '-clipmode=rgbblend']
CURVE = [{'command': 'modasinh', 'parameters': PARAMETERS}]


@pytest.mark.parametrize('parameters', [PARAMETERS, list(reversed(PARAMETERS)),
    ['-D=10', '-LP=0', '-SP=0', '-HP=1', '-human', '-clipmode=rgbblend'],
    ['-D=.1', '-LP=.5', '-SP=.5', '-HP=.5', '-human', '-clipmode=rgbblend']])
def test_modasinh_accepts_explicit_ordered_finite_parameters(parameters):
    processing.validate_parameters(['modasinh', *parameters], 'stars.recompose')


@pytest.mark.parametrize('parameters',
    [PARAMETERS[:i]+PARAMETERS[i+1:] for i in range(len(PARAMETERS))]
    + [PARAMETERS+[token] for token in PARAMETERS+['-B=1', '-sat', '-independent', 'R']]
    + [[token if not token.startswith('-D=') else '-D='+value for token in PARAMETERS]
       for value in ('0', '-.1', '10.1', 'nan', 'inf', 'true', '')]
    + [[token.replace('-LP=0', '-LP=-.1') for token in PARAMETERS],
       [token.replace('-LP=0', '-LP=.2') for token in PARAMETERS],
       [token.replace('-HP=.7', '-HP=.05') for token in PARAMETERS],
       [token.replace('-HP=.7', '-HP=1.1') for token in PARAMETERS],
       [token.replace('-SP=.1', '-SP=nan') for token in PARAMETERS],
       [token.replace('-D=.4', '-D') for token in PARAMETERS],
       [token.replace('-SP=.1', '-LP=0') for token in PARAMETERS],
       [token.replace('-human', '-even') for token in PARAMETERS],
       [token.replace('-human', '-human=true') for token in PARAMETERS],
       [token.replace('rgbblend', 'rescale') for token in PARAMETERS]])
def test_modasinh_rejects_missing_duplicate_unknown_and_invalid_parameters(parameters):
    with pytest.raises(contract.ContractError):
        processing.validate_parameters(['modasinh', *parameters], 'stars.recompose')


def test_modasinh_permission_does_not_expand_body_transforms():
    policy = contract.load_json(ROOT/'references/command-policy.json')
    for protocol, commands in policy['protocol_commands'].items():
        assert ('modasinh' in commands) == (protocol == 'stars.recompose')
        if protocol != 'stars.recompose':
            with pytest.raises(contract.ContractError, match='restricted'):
                processing.validate_parameters(['modasinh', *PARAMETERS], protocol)
    assert processing.TRANSFORMS == {'mtf', 'asinh', 'ght'}
    before = copy.deepcopy(policy)
    before['protocol_commands']['stars.recompose'].remove('rgbcomp')
    before['protocol_commands']['stars.recompose'].remove('update_key')
    before['protocol_commands']['stars.recompose'].remove('modasinh')
    before['protocol_commands']['color.finish'].remove('invght')
    import json
    assert before == json.loads(PRIOR['command_policy'])


@pytest.mark.parametrize('parameters', [PARAMETERS[:-1], PARAMETERS+['-B=1'],
    [token.replace('-D=.4', '-D=nan') for token in PARAMETERS],
    [token.replace('-HP=.7', '-HP=.05') for token in PARAMETERS]])
def test_bad_star_request_fails_before_native_or_assets(case, parameters):
    session, parent, _, _, native = case
    request = request_for(case, transfer_chain=[{'command': 'modasinh', 'parameters': parameters}])
    with pytest.raises(contract.ContractError):
        stars.prepare(session, parent, '089-invalid-modasinh', star_request=request)
    assert len(native.calls) == 1
    assert not (session/'reports/089-invalid-modasinh').exists()


@pytest.mark.parametrize('change', ['source', 'parameters', 'historical-policy'])
def test_modasinh_source_request_and_policy_bindings_are_not_interchangeable(case, change):
    session, _, _, _, native = case
    request = request_for(case, transfer_chain=CURVE)
    payload = contract.load_json(session/'session.json')
    binding = stars.record(session, request)
    stars.star_curve_request(session, payload, binding)
    value = contract.load_json(request)
    if change == 'source':
        value['source']['sha256'] = '0'*64
        contract.atomic_write_json(request, value)
        binding = stars.record(session, request)
    elif change == 'parameters':
        value['transfer_chain'][0]['parameters'][0] = '-D=.5'
        contract.atomic_write_json(request, value)
    else:
        payload['execution_policy'] = PRIOR['execution_policy']
    with pytest.raises(contract.ContractError):
        stars.star_curve_request(session, payload, binding)
    assert len(native.calls) == 1


@pytest.mark.parametrize('case', ['single', 'mono', 'hdr', 'hoo'], indirect=True)
def test_native_modasinh_preserves_paired_replay_catalog_geometry_and_closure(case, monkeypatch):
    native_flow(case, monkeypatch, CURVE)
    session, parent, _, _, _ = case
    baseline = contract.load_json(session/'reports/089-native-base/recomposition-prepared.json')
    prepared = contract.load_json(session/'reports/089-native-star/recomposition-prepared.json')
    receipt = contract.load_json(session/'runs/090-native.json')
    assert prepared['geometry_catalog'] == baseline['geometry_catalog']
    assert prepared['bindings'] == baseline['bindings']
    assert prepared['star_curve']['transfer_chain'] == receipt['transfer_chain'] == CURVE
    script = (session/receipt['script']['path']).read_text()
    assert script.count('modasinh '+' '.join(PARAMETERS)) == 2
    assert 'findstar' not in script
    shape = stars.read_scientific_pixels(parent)[0].shape
    for role in ('full_baseline', 'original_starless', 'display_stars'):
        path = session/receipt['branch_binding'][role]
        pixels = stars.read_scientific_pixels(path)[0]
        assert pixels.shape == shape and np.isfinite(pixels).all()
        assert stars.read_fits_header(path)['BITPIX'] == -32
        np.testing.assert_allclose(pixels, stars.read_scientific_pixels(
            session/prepared['layers'][role]['path'])[0], rtol=0, atol=1e-5)
    closure = receipt['processing_checks']['closure']
    assert max(closure['per_channel_max_abs']) <= 1e-5
    assert max(closure['per_channel_final_max_abs']) <= 1e-5
    report = contract.load_json(session/receipt['processing_checks']['recomposition']['report']['path'])
    assert report['stellar_color']['status'] == ('not_applicable' if shape[0] == 1 else 'measured')
    request = session/'reports/star-curve.json'
    value = contract.load_json(request)
    value['transfer_chain'][0]['parameters'][0] = '-D=.5'
    contract.atomic_write_json(request, value)
    with pytest.raises(contract.ContractError):
        processing.verify_recomposition_receipt(session, contract.load_json(session/'session.json'), receipt)


def test_outgoing_policy_bytes_and_implementation_replay_exactly(tmp_path, monkeypatch):
    assert sessions._PRE_STAR_STRETCH_BINDINGS == PRIOR
    assert_contract_domains(PRIOR['execution_policy'])
    assert_command_policy_hashed(PRIOR)
    assert_document_fingerprints_replayable(PRIOR['documents'])
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': PRIOR['execution_policy']})
    assert stretch.implementation(tmp_path) == PRIOR['implementation']
    assert stretch.profiles_fingerprint(tmp_path) == PRIOR['profiles']
    assert not sessions.execution_policy_is_current({'execution_policy': PRIOR['execution_policy']})
    replay_and_finalize_without_migration(tmp_path/'delivery', monkeypatch,
                                          ROOT/'tests/fixtures/star-stretch-prior.json')


def test_new_sessions_reject_outgoing_prepared_helper_and_implementation(case):
    session, _, _, proposal, _ = case
    path = session/proposal['prepared']['path']
    before = path.read_bytes()
    for key, value in (('helper', PRIOR['helpers']['deep_sky_star_reduction.py']),
                       ('implementation', PRIOR['implementation'])):
        prepared = contract.load_json(path)
        prepared['bindings'][key] = value
        contract.atomic_write_json(path, prepared)
        with pytest.raises(contract.ContractError, match='implementation'):
            stars.prepared_input(session, contract.load_json(session/'session.json'), stars.record(session, path))
        path.write_bytes(before)


@pytest.mark.parametrize('independent', [False, True])
def test_outgoing_prepared_reports_remain_immutable_and_cannot_generate(case, independent):
    session, parent, request, proposal, _ = case
    if independent:
        star_request = request_for(case)
        result = stars.prepare(session, parent, '089-outgoing-curve', star_request=star_request)
        proposal['prepared'] = result['prepared']
        contract.atomic_write_json(request, proposal)
    run = execute(case, stars.generate_recompose_assets(session, request=request, run_id='090-outgoing'))
    assert run['status'] == 'success'
    payload = contract.load_json(session/'session.json')
    payload['execution_policy'] = PRIOR['execution_policy']
    payload.pop('session_sha256')
    payload['session_sha256'] = contract.stable_hash(payload)
    contract.atomic_write_json(session/'session.json', payload)
    path = session/proposal['prepared']['path']
    prepared = contract.load_json(path)
    if independent:
        baseline = session/prepared['star_curve']['baseline']['path']
        value = contract.load_json(baseline)
        value['bindings'].update(helper=PRIOR['helpers']['deep_sky_star_reduction.py'],
                                 implementation=PRIOR['implementation'])
        contract.atomic_write_json(baseline, value)
        value = contract.load_json(star_request)
        value['baseline'] = stars.record(session, baseline)
        contract.atomic_write_json(star_request, value)
        prepared['star_curve'].update(baseline=value['baseline'], request=stars.record(session, star_request))
    prepared['bindings'].update(helper=PRIOR['helpers']['deep_sky_star_reduction.py'],
                                implementation=PRIOR['implementation'])
    contract.atomic_write_json(path, prepared)
    proposal['prepared'] = stars.record(session, path)
    contract.atomic_write_json(request, proposal)
    _, binding, _, _ = stars.request_binding(session, payload, request, run['id'], verify_parent=False)
    run['recomposition_proposal'] = binding
    for key in ('report', 'panel'):
        (session/run['processing_checks']['recomposition'][key]['path']).unlink()
    run['processing_checks']['recomposition'] = stars.measure(session, parent,
        session/run['primary_output'], run, write=True)
    paths = [session/'session.json', path, *[session/run['processing_checks']['recomposition'][key]['path']
                                          for key in ('report', 'panel')]]
    frozen = {str(path): path.read_bytes() for path in paths}
    assert stars.measure(session, parent, session/run['primary_output'], run) == run['processing_checks']['recomposition']
    assert frozen == {str(path): path.read_bytes() for path in paths}
    with pytest.raises(contract.ContractError, match='new session'):
        stars.prepare(session, parent, '089-forbidden')
