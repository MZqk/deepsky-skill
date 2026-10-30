"""Minimum mask composition, native color equivalence and frozen product replay."""
import json
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_color as color
import deep_sky_siril_contract as contract
import deep_sky_siril_local_contrast as local
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
from .test_hdr_stretch import run_native
from .test_native_processing import write_fits
from .test_multiscale_competition import request_file, run_candidate
from .test_recomposition_sources_v11 import source_case, accept
from .test_starnet_linear_bridge import native
from . import test_deep_sky_siril as support
from ._policy_pins import assert_contract_domains

ROOT = Path(__file__).parents[1]


def test_mask_policy_preserves_exact_predecessor_bindings(tmp_path):
    history = ROOT/'references/local-contrast-mask-history.json'
    assert contract.fingerprint(history, include_path=False) == {
        'sha256': 'f980b0140dc5ca7e3c2da69efb354df0338e8883dea050595f1dddb1b15267ee', 'size': 5671}
    prior = contract.load_json(history)
    assert sessions._PRE_LOCAL_CONTRAST_MASK_BINDINGS == prior
    assert_contract_domains(prior['execution_policy'])
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': prior['execution_policy']})
    assert stretch.implementation_matches(tmp_path, prior['implementation'])
    mixed = {**prior['implementation'], 'deep_sky_siril_local_contrast.py':
             contract.fingerprint(ROOT/'scripts/deep_sky_siril_local_contrast.py', include_path=False)}
    assert not stretch.implementation_matches(tmp_path, mixed)
    for name, record in prior['documents'].items():
        assert sessions.registered_guidance_reference(prior, {'path': name, **record})
        assert not sessions.registered_guidance_reference({'execution_policy': contract.EXECUTION_POLICY}, {'path': name, **record})


def test_predecessor_product_report_and_receipt_replay_unchanged(request, monkeypatch, tmp_path):
    prior = sessions._PRE_LOCAL_CONTRAST_MASK_BINDINGS
    fingerprint = local.fingerprint
    helper = ROOT/'scripts/deep_sky_siril_local_contrast.py'
    with monkeypatch.context() as outgoing:
        from . import _stretch_factory
        outgoing.setattr(_stretch_factory, 'with_morphology', lambda session, source, regions, goals: goals)
        for key in list(contract.EXECUTION_POLICY):
            outgoing.delitem(contract.EXECUTION_POLICY, key)
        for key, value in prior['execution_policy'].items():
            outgoing.setitem(contract.EXECUTION_POLICY, key, value)
        profiles = contract.load_json(stretch.PROFILE_PATH)
        profiles['high_dynamic_range']['guidance'] = 'Evaluate masked dual HDR only after a measured single-chain conflict. Protect the final blend.'
        profile_path = tmp_path/'historical-stretch-profiles.json'
        profile_path.write_text(json.dumps(profiles, indent=2)+'\n')
        assert contract.fingerprint(profile_path, include_path=False) == prior['profiles']
        outgoing.setattr(stretch, 'PROFILE_PATH', profile_path)
        outgoing.setattr(stretch, 'implementation', lambda *args: prior['implementation'])
        outgoing.setattr(local, 'fingerprint', lambda path, **kwargs:
                         prior['helpers'][helper.name] if Path(path).resolve() == helper else fingerprint(path, **kwargs))
        session, parent, *_ = request.getfixturevalue('source_case')
        path = request_file(session, parent)
        value = contract.load_json(path)
        value.update(schema=local.PROTECTED_REQUEST, wavelet={'amplitude': .4, 'fine_detail': False}, clahe={'strength': .1})
        contract.atomic_write_json(path, value)
        solved = local.solve(session, path, '080-product', timeout=60)
        assets = local.generate(session, solved['parameters'], 'wavelet')
        run = run_candidate(session, assets)
        assert run['status'] == 'success', run
        assert 'mask_product' in run['processing_checks']['local_contrast']['native']['max_errors']
        accept(session, run['id'])
    frozen = support._session_bytes(session)
    report = local.load_parameters(session, solved['parameters'])
    assert report['candidates']['wavelet']['parameters']['mask_support'] == 'source_exclusion_v1'
    assert run_candidate(session, assets)['replayed']
    assert support._session_bytes(session) == frozen


@pytest.mark.parametrize('method', ['wavelet', 'clahe'])
def test_native_minimum_matches_color_preserves_zeros_and_retains_weak_enhancement(tmp_path, monkeypatch, method):
    for name in ('artifacts', 'previews', 'reports'):
        (tmp_path/name).mkdir()
    y, x = np.mgrid[:512, :512]
    faint = .065+.002*np.sin(x/3)*np.cos(y/5)
    before = np.stack([faint*.8, faint, faint*.6]).astype('float32')
    before[:, :32, :32] = .01
    before[0, :32, -32:] = .95
    before[1:, :32, -32:] = .1
    source = tmp_path/'source.fit'
    write_fits(source, before)
    bounds = dict(low_start=.03, low_end=.10, high_start=.65, high_end=.90, sigma=2)
    operator = ({'coefficients': local.wavelet_coefficients(.4, False)} if method == 'wavelet'
                else {'cliplimit': 1.5, 'grid': 8, 'strength': .1})
    results = {}
    for label, support in [('product', 'source_exclusion_v1'), ('minimum', 'source_exclusion_v2')]:
        parameters = {**bounds, **operator, 'method': method, 'mask_support': support}
        primary = tmp_path/f'artifacts/080-{label}.fit'
        rows = local.recipe(tmp_path, source, primary, parameters)
        run_native(tmp_path, '\n'.join(['requires 1.4.4 1.5.0', 'set32bits', *rows])+'\n')
        files = local.native_paths(tmp_path, primary, method, protected=True)['files']
        layers = {name: color.pixels(tmp_path/files[name])[0]
                  for name in ('mask-base', 'mask-smoothed', 'mask', 'l', 'l-enhanced')}
        report_file = tmp_path/'reports/parameters.json'
        contract.atomic_write_json(report_file, {})
        report = {'candidates': {label: {'parameters': parameters, 'output': {'path': primary.relative_to(tmp_path).as_posix()}}},
                  'region_document': {'regions': []}}
        monkeypatch.setattr(local, 'load_parameters', lambda *args: report)
        binding = {'parameters': {**local._record(tmp_path, report_file), 'candidate': label}, 'native': {'files': files}}
        checks = local.measure_native(tmp_path, source, primary, binding)
        assert max(checks['max_errors'].values()) < 2e-6
        assert ('mask_product' if label == 'product' else 'mask_minimum') in checks['max_errors']
        if label == 'product':
            parameters['mask_support'] = 'source_exclusion_v2'
            with pytest.raises(contract.ContractError, match='arithmetic or reconstruction changed'):
                local.measure_native(tmp_path, source, primary, binding)
            parameters['mask_support'] = support
        results[label] = {**layers, 'after': color.pixels(primary)}
    current, old = results['minimum'], results['product']
    base, blurred, mask = [current[name] for name in ('mask-base', 'mask-smoothed', 'mask')]
    np.testing.assert_array_equal(mask, np.minimum(base, blurred))
    np.testing.assert_allclose(old['mask'], old['mask-base']*old['mask-smoothed'], rtol=0, atol=1e-7)
    np.testing.assert_array_equal(current['l-enhanced'], old['l-enhanced'])
    excluded = mask == 0
    assert excluded.any()
    for result in results.values():
        np.testing.assert_array_equal(result['after'][:, excluded], before[:, excluded])
    weak = (base > .15) & (base < .85) & (blurred > .15) & (blurred < .85) & (np.abs(current['l-enhanced']-current['l']) > 1e-5)
    assert weak.any() and np.all(mask[weak] > old['mask'][weak])
    assert np.mean(np.abs(current['after']-before)[:, weak]) > 1.2*np.mean(np.abs(old['after']-before)[:, weak])
    text, _, _, binding = color.script_text(tmp_path, source, '088-color',
        {**bounds, 'D': .1, 'mask_mode': 'luminance', 'mask_policy': color.PRESERVATION}, previews=False)
    run_native(tmp_path, text)
    np.testing.assert_allclose(mask, color.pixels(tmp_path/binding['layers']['mask'])[0], rtol=0, atol=2e-7)
