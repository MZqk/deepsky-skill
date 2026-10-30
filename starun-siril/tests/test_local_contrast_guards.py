"""Frozen CLAHE eligibility, native candidates, bound review and history."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from . import test_deep_sky_siril as support
from .test_native_processing import write_fits, _local_contrast_script
from .test_recomposition_sources_v11 import source_case, accept, recompose_assets
from .test_starnet_linear_bridge import native
from .test_tone_adjust import _tone_script
import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_local_contrast as guards

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT/'tests/fixtures/local-contrast-v13.json').read_text())


def regions_file(session, source, *, size=384):
    edge = size-80
    mid = size//2
    rows = [
        {'id': 'sky-a', 'role': 'background', 'rect': [48, 48, 72, 72]},
        {'id': 'sky-b', 'role': 'background', 'rect': [edge, 48, edge+24, 72]},
        {'id': 'sky-c', 'role': 'background', 'rect': [48, edge, 72, edge+24]},
        {'id': 'faint-a', 'role': 'faint', 'rect': [mid-16, mid-16, mid+16, mid+16]},
    ]
    path = session/'reports/080-local-contrast-regions.json'
    core.atomic_write_json(path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': size, 'height': size}, 'regions': rows})
    return path


def script_assets(session, source):
    placeholder, primary, text, _ = _local_contrast_script(session, {'regions': 'reports/080-local-contrast-regions.json'})
    text = text.replace(str(placeholder), str(source))
    script = session/'scripts/080-local-contrast.ssf'
    script.parent.mkdir(exist_ok=True)
    script.write_text(text)
    commands = processing.script_commands(text)
    from deep_sky_siril_validation import _normalized_write
    expected = set()
    for row in commands:
        if row[0] in {'save', 'savejpg'}:
            expected.add(_normalized_write(row[0], row[1]))
        elif row[0] == 'split':
            expected.update(_normalized_write('split', v) for v in row[1:])
        elif row[0] == 'rgbcomp':
            expected.add(_normalized_write('save', next(v[5:] for v in row if v.startswith('-out='))))
    return script, primary, sorted(expected)


def unit_case(tmp_path):
    for name in ('artifacts', 'reports', 'runs', 'scripts', 'previews'):
        (tmp_path/name).mkdir(exist_ok=True)
    rng = np.random.default_rng(8)
    lum = .2+rng.normal(0, .0005, (384, 384))
    y, x = np.mgrid[:384, :384]
    lum[168:216, 168:216] += (.015*np.sin(x*.7)*np.cos(y*.9))[168:216, 168:216]
    source = tmp_path/'artifacts/unusual-parent.fit'
    values = np.stack([lum*1.05, lum, lum*.95])
    write_fits(source, values)
    linear = tmp_path/'artifacts/linear.fit'
    write_fits(linear, values)
    lineage = [
        {'id': '060-separate', 'status': 'success', 'protocol': 'stars.separate',
         'source_path': '@input', 'primary_output': 'artifacts/linear.fit'},
        {'id': '070-stretch', 'status': 'success', 'protocol': 'stretch',
         'source_path': 'artifacts/linear.fit', 'primary_output': 'artifacts/unusual-parent.fit'},
    ]
    for record in lineage:
        core.atomic_write_json(tmp_path/'runs'/f"{record['id']}.json", record)
    regions = regions_file(tmp_path, source)
    script, primary, _ = script_assets(tmp_path, source)
    return source, primary, processing.script_commands(script.read_text()), regions, lineage


def prepare_case(session, case):
    source, primary, commands, regions, lineage = case
    return guards.prepare(session, source, primary, commands, regions.relative_to(session).as_posix(), lineage)


def test_complete_history_and_unchanged_policy_content():
    # prior=local-contrast-v13.json: 全部 20 个域标签
    assert_contract_domains(BASELINE['policy'])
    assert sessions._AI_MORPHOLOGY_EXECUTION_POLICY == BASELINE['policy']
    assert sessions.known_execution_policy(BASELINE['policy'])
    assert not sessions.execution_policy_is_current({'execution_policy': BASELINE['policy']})
    for name, record in BASELINE['documents'].items():
        assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
    from .test_knowledge_contract import assert_v18_command_policy
    assert_v18_command_policy(BASELINE['documents']['references/command-policy.json'])


@pytest.mark.parametrize('value,blocked', [(1.699999999, True), (1.7, False), (1.700000001, False)])
def test_texture_threshold_uses_unrounded_minimum(value, blocked):
    rows = [{'id': str(i), 'role': 'background', 'sigma': 2, 'amplitude': 1.0} for i in range(3)]
    rows += [{'id': 'bright', 'role': 'faint', 'sigma': 2, 'amplitude': 10.0},
             {'id': 'weak', 'role': 'faint', 'sigma': 2, 'amplitude': value}]
    if blocked:
        with pytest.raises(contract.ContractError, match='weak'):
            guards.texture(rows)
    else:
        assert guards.texture(rows)['texture_snr'] == value


@pytest.mark.parametrize('floor', [0.0, 1e-12, float('nan'), float('inf')])
def test_missing_or_invalid_noise_floor_is_not_a_bypass(floor):
    rows = [{'id': 'sky', 'role': 'background', 'sigma': 2, 'amplitude': floor},
            {'id': 'target', 'role': 'faint', 'sigma': 2, 'amplitude': 1.0}]
    with pytest.raises(contract.ContractError):
        guards.texture(rows)


def test_numpy_gaussian_coordinates_and_context_match_reference(tmp_path):
    source, _, _, regions, _ = unit_case(tmp_path)
    values, _ = core.artifacts.read_scientific_pixels(source)
    rows = guards.energies(values, core.load_json(regions)['regions'])
    # Independent full-frame convolution checks crop coordinates without SciPy.
    lum = np.einsum('c,cyx->yx', [.2126, .7152, .0722], values.astype(float))
    for sigma in guards.SCALES:
        radius = 4*sigma
        axis = np.arange(-radius, radius+1)
        kernel = np.exp(-.5*(axis/sigma)**2); kernel /= kernel.sum()
        padded = np.pad(lum, radius, mode='reflect')
        blurred = np.apply_along_axis(lambda a: np.convolve(a, kernel, 'valid'), 1, padded)
        blurred = np.apply_along_axis(lambda a: np.convolve(a, kernel, 'valid'), 0, blurred)
        for region in core.load_json(regions)['regions']:
            x0, y0, x1, y1 = region['rect']
            measured = next(r['amplitude'] for r in rows if r['id'] == region['id'] and r['sigma'] == sigma)
            expected = np.abs(lum-blurred)[384-y1:384-y0, x0:x1].mean()
            assert measured == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize('issue', ['missing-sky', 'missing-faint', 'overlap', 'center-protected',
    'halo-protected', 'faint-protected', 'edge', 'too-small', 'wrong-source', 'geometry', 'nonfinite',
    'symlink', 'missing', 'invalid-json'])
def test_region_errors_fail_closed_without_outputs(tmp_path, issue):
    case = unit_case(tmp_path)
    source, primary, _, regions, _ = case
    doc = core.load_json(regions)
    if issue == 'missing-sky': doc['regions'] = doc['regions'][1:]
    elif issue == 'missing-faint': doc['regions'].pop()
    elif issue == 'overlap': doc['regions'][1]['rect'] = doc['regions'][0]['rect']
    elif issue in {'center-protected', 'halo-protected', 'faint-protected'}:
        rect = [50, 50, 60, 60] if issue == 'center-protected' else [76, 50, 84, 58] if issue == 'halo-protected' else [184, 184, 192, 192]
        doc['regions'].append({'id': 'protected', 'role': 'stars', 'rect': rect})
    elif issue == 'edge': doc['regions'][0]['rect'] = [0, 0, 24, 24]
    elif issue == 'too-small': doc['regions'][0]['rect'] = [48, 48, 55, 55]
    elif issue == 'wrong-source': doc['reference']['sha256'] = '0'*64
    elif issue == 'geometry': doc['reference']['width'] += 1
    elif issue == 'nonfinite':
        values, _ = core.artifacts.read_scientific_pixels(source)
        values = values.copy(); values[0, 330, 50] = np.nan; write_fits(source, values)
        doc['reference']['sha256'] = contract.sha256_file(source)
    core.atomic_write_json(regions, doc)
    if issue == 'symlink':
        actual = regions.with_name('actual.json'); regions.rename(actual); regions.symlink_to(actual)
    elif issue == 'missing': regions.unlink()
    elif issue == 'invalid-json': regions.write_text('{')
    with pytest.raises((contract.ContractError, ValueError)):
        prepare_case(tmp_path, case)
    assert not primary.exists()


@pytest.mark.parametrize('issue', ['hdr', 'hdr-through-tone', 'cycle', 'wrong-owner', 'receipt-drift'])
def test_lineage_blocks_hdr_hidden_by_adjustments_and_invalid_ancestry(tmp_path, issue):
    source, _, _, _, lineage = unit_case(tmp_path)
    if issue.startswith('hdr'):
        lineage[-1]['hdr_transfer'] = {'mask': 'bound'}
        if issue == 'hdr-through-tone':
            earlier = tmp_path/'artifacts/hdr.fit'; earlier.write_bytes(source.read_bytes())
            lineage[-1]['primary_output'] = 'artifacts/hdr.fit'
            lineage.append({'id': '075-tone', 'status': 'success', 'protocol': 'tone.adjust',
                'source_path': 'artifacts/hdr.fit', 'primary_output': source.relative_to(tmp_path).as_posix()})
    elif issue == 'cycle': lineage[-1]['primary_output'] = lineage[0]['primary_output']
    elif issue == 'wrong-owner': lineage[-1]['source_path'] = '@input'
    for record in lineage:
        core.atomic_write_json(tmp_path/'runs'/f"{record['id']}.json", record)
    if issue == 'receipt-drift': lineage[-1]['id'] = '999-missing'
    with pytest.raises(contract.ContractError): guards.lineage_binding(tmp_path, source, lineage)


def test_candidate_amplification_is_warning_and_unavailable_is_uncertain(tmp_path):
    case = unit_case(tmp_path)
    binding = prepare_case(tmp_path, case)
    source, primary, *_ = case
    values, _ = core.artifacts.read_scientific_pixels(source)
    write_fits(primary, .2+2*(values-.2))
    measured = guards.measure(tmp_path, source, primary, binding)
    assert measured['status'] == 'measured' and measured['warnings']
    assert all(row['amplification'] == pytest.approx(2, rel=.00001) for row in measured['comparisons'])
    assert all(row['sigma'] in guards.SCALES for row in measured['warnings'])


def test_unavailable_scale_is_uncertain(tmp_path, monkeypatch):
    case = unit_case(tmp_path)
    source, primary, *_ = case
    primary.write_bytes(source.read_bytes())
    original = guards.energies
    def unavailable(*args, **kwargs):
        rows = original(*args, **kwargs)
        for row in rows:
            if row['sigma'] == 1 and row['id'] == 'sky-a':
                row['amplitude'] = 0.0
        return rows
    monkeypatch.setattr(guards, 'energies', unavailable)
    binding = prepare_case(tmp_path, case)
    measured = guards.measure(tmp_path, source, primary, binding)
    assert measured['status'] == 'uncertain' and measured['unavailable']
    assert measured['unavailable'][0]['id'] == 'sky-a'


def test_other_protocol_cannot_bypass_unaccepted_clahe(tmp_path, monkeypatch):
    source, primary, commands, *_ = unit_case(tmp_path)
    ancestor = {'id': '080-uncertain', 'local_contrast_guard': {'policy': guards.POLICY}}
    monkeypatch.setattr(processing, 'source_state', lambda *args: {
        'domain': 'nonlinear', 'run': ancestor, 'separation_run': '060-separate', 'lineage': [ancestor]})
    def require_review(run_id):
        assert run_id == '080-uncertain'
        raise contract.ContractError('skill_output_invalid', 'CLAHE accepted review required')
    with pytest.raises(contract.ContractError, match='CLAHE accepted review required'):
        processing.prepare_processing(tmp_path, {'execution_policy': contract.EXECUTION_POLICY},
            'color.map', source, primary, '', {}, None, None, None, require_review)


def test_candidate_clipping_uses_the_existing_shadow_gate(tmp_path):
    case = unit_case(tmp_path)
    binding = prepare_case(tmp_path, case)
    _, primary, *_ = case
    write_fits(primary, np.zeros((3, 384, 384)))
    assert guards.measure(tmp_path, case[0], primary, binding)['clipping_verdict'] == 'reject'


def test_native_clahe_preserves_existing_wcs_and_rejects_loss(tmp_path):
    from .test_stage4_xisf import WCS
    from .test_hdr_stretch import run_native
    case = unit_case(tmp_path)
    source, primary, *_ = case
    values, _ = core.artifacts.read_scientific_pixels(source)
    write_fits(source, values, extra=list(WCS.items()))
    regions_file(tmp_path, source)
    binding = prepare_case(tmp_path, case)
    run_native(tmp_path, (tmp_path/'scripts/080-local-contrast.ssf').read_text())
    assert guards.measure(tmp_path, source, primary, binding)['wcs'] == 'preserved'
    output, _ = core.artifacts.read_scientific_pixels(primary)
    write_fits(primary, output)
    with pytest.raises(contract.ContractError, match='lost the existing WCS'):
        guards.measure(tmp_path, source, primary, binding)


def test_uncertain_native_evidence_cannot_be_accepted(source_case, monkeypatch):
    session, parent, *_ = source_case
    regions_file(session, parent, size=512)
    script, primary, expected = script_assets(session, parent)
    support._write_ssf_provenance(session, script, 'structure.local-contrast')
    original = guards.energies
    def unavailable(*args, **kwargs):
        rows = original(*args, **kwargs)
        for row in rows:
            if row['sigma'] == 1 and row['id'] == 'sky-a': row['amplitude'] = 0.0
        return rows
    monkeypatch.setattr(guards, 'energies', unavailable)
    run = core.run_script(session, protocol='structure.local-contrast', script_value=script, source_value=parent,
                          expected_values=expected, primary_output_value=primary, timeout=30)
    assert run['status'] == 'success' and run['processing_checks']['local_contrast']['status'] == 'uncertain'
    with pytest.raises(contract.ContractError, match='evidence is uncertain'):
        accept(session, run['id'])


@pytest.mark.parametrize('source_case', ['flat-noise'], indirect=True)
def test_low_texture_cli_allows_static_candidate_with_warning(source_case, monkeypatch):
    session, parent, *_ = source_case
    regions_file(session, parent, size=512)
    script, primary, expected = script_assets(session, parent)
    support._write_ssf_provenance(session, script, 'structure.local-contrast')
    immutable = {p: p.read_bytes() for p in (session/'session.json', session/'manifest.json', parent, script)}
    monkeypatch.setattr(core.subprocess, 'run', lambda *a, **k: pytest.fail('Static validation must not run Siril'))
    monkeypatch.setattr(core.tooling, 'probe_tools', lambda **k: pytest.fail('Static validation must not probe live tools'))
    result = core.run_script(session, protocol='structure.local-contrast', script_value=script, source_value=parent,
                             expected_values=expected, primary_output_value=primary, timeout=30, validate_only=True)
    assert result['status'] == 'success' and result['executed'] is False
    assert result['local_contrast_guard']['eligibility']['warnings'][0]['code'] == 'low_texture_ratio'
    assert all(p.read_bytes() == content for p, content in immutable.items()) and not primary.exists()


@pytest.mark.parametrize('source_case', ['hdr'], indirect=True)
def test_hdr_after_tone_still_blocks_before_siril(source_case, monkeypatch):
    session, parent, *_ = source_case
    tone, outputs = _tone_script(session, parent)
    run = core.run_script(session, protocol='tone.adjust', script_value=tone, source_value=parent,
                          expected_values=outputs, primary_output_value=outputs[0], timeout=30)
    assert run['status'] == 'success'
    accept(session, run['id'])
    parent = outputs[0]
    regions_file(session, parent, size=512)
    script, primary, expected = script_assets(session, parent)
    support._write_ssf_provenance(session, script, 'structure.local-contrast')
    before = support._session_bytes(session)
    monkeypatch.setattr(core.subprocess, 'run', lambda *a, **k: pytest.fail('Hidden HDR must block Siril'))
    monkeypatch.setattr(core.tooling, 'probe_tools', lambda **k: pytest.fail('HDR gate must precede live tool probing'))
    with pytest.raises(contract.ContractError, match='HDR and CLAHE'):
        core.run_script(session, protocol='structure.local-contrast', script_value=script, source_value=parent,
                        expected_values=expected, primary_output_value=primary, timeout=30)
    assert support._session_bytes(session) == before


def test_heteroscedastic_noise_does_not_prove_structure(tmp_path):
    case = unit_case(tmp_path)
    source, _, _, regions, _ = case
    rng = np.random.default_rng(16)
    y, x = np.mgrid[:384, :384]
    sigma = np.where((x > 160) & (x < 216) & (y > 160) & (y < 216), .005, .0005)
    plane = .2+rng.normal(size=x.shape)*sigma
    write_fits(source, np.stack([plane]*3))
    regions_file(tmp_path, source)
    binding = prepare_case(tmp_path, case)
    assert binding['eligibility']['texture_snr'] > 1.7
    assert binding['policy'] == guards.POLICY  # Eligibility never creates an accepted review.


@pytest.mark.parametrize('source_case', ['single', 'hdr'], indirect=True)
def test_native_run_review_replay_and_finalization(source_case, monkeypatch):
    session, parent, original, *_ = source_case
    regions_file(session, parent, size=512)
    script, primary, expected = script_assets(session, parent)
    support._write_ssf_provenance(session, script, 'structure.local-contrast')
    args = dict(protocol='structure.local-contrast', script_value=script, source_value=parent,
                primary_output_value=primary, expected_values=expected, timeout=30)
    if core.load_json(session/'runs/070-stretch.json').get('hdr_transfer'):
        before = support._session_bytes(session)
        monkeypatch.setattr(core.subprocess, 'run', lambda *a, **k: pytest.fail('Siril must not start for an HDR parent'))
        with pytest.raises(contract.ContractError, match='HDR and CLAHE'):
            core.run_script(session, **args)
        assert support._session_bytes(session) == before
        return
    validation = core.run_script(session, **args, validate_only=True)
    assert validation['local_contrast_guard']['eligibility']['texture_snr'] >= 1.7
    run = core.run_script(session, **args)
    assert run['status'] == 'success', run
    assert run['processing_checks']['local_contrast']['status'] == 'measured'
    before = support._session_bytes(session)
    draft = core.check_review(session, run['id'], None)['review']
    assert support._session_bytes(session) == before
    assert draft['verdict'] is None and draft['notes'] == ''
    assert all(v is None for v in draft['gates'].values())
    names = {r['path'] for r in draft['inspected_materials']}
    assert {'reports/080-local-contrast-regions.json', 'runs/080-local-contrast.json',
            'previews/080-parent.jpg', 'previews/080-mask.jpg', 'previews/080-local-contrast.jpg'} <= names
    assert core._delivery_parent_preview_paths(session, {'id': '110-final', 'source_path': run['primary_output']}) == {
        session/run['local_contrast_guard']['previews']['candidate']}
    review = accept(session, run['id'])
    record = core.load_json(review)
    record['inspected_materials'] = [r for r in record['inspected_materials'] if r['path'] != 'reports/080-local-contrast-regions.json']
    core.atomic_write_json(review, record)
    with pytest.raises(contract.ContractError, match='missing bound materials'):
        core.check_review(session, run['id'], review)
    review = accept(session, run['id'])
    before = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert support._session_bytes(session) == before
    # Recomposition and delivery consume the accepted branch through existing helpers.
    import deep_sky_star_reduction as reduction
    from .test_recomposition_sources_v11 import generated_run
    generated = recompose_assets(session_path=session, source=primary, strength=.5)
    recomposed = generated_run(session, generated)
    assert recomposed['status'] == 'success'
    accept(session, recomposed['id'])
    execute = core.subprocess.run
    inspection, direct, diagnostic = support._input_inspect_run(session, original, monkeypatch)
    support._review(session, inspection['id'], diagnostic, parent_preview=direct)
    monkeypatch.setattr(core.subprocess, 'run', execute)
    delivery_parent = session/recomposed['primary_output']
    delivery_script, final = support._delivery_script(session, delivery_parent)
    delivery = core.run_script(session, protocol='delivery.render', script_value=delivery_script,
        source_value=delivery_parent, expected_values=[final, final.with_suffix('.fit')], timeout=30)
    assert delivery['status'] == 'success'
    review = accept(session, delivery['id'])
    selection = support._selection(session, delivery['id'], final, review)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    for relative in ('artifacts/080-mask.fit', 'previews/080-parent.jpg',
                     'reports/080-local-contrast-regions.json', 'runs/070-stretch.json'):
        target = session/relative
        frozen = target.read_bytes()
        target.write_bytes(frozen+b'changed')
        with pytest.raises(contract.ContractError): core._load_run(session, run['id'])
        target.write_bytes(frozen)
        target.unlink()
        with pytest.raises(contract.ContractError): core._load_run(session, run['id'])
        target.write_bytes(frozen)
        actual = target.with_name(target.name+'.original')
        target.rename(actual); target.symlink_to(actual)
        with pytest.raises(contract.ContractError): core._load_run(session, run['id'])
        target.unlink(); actual.rename(target)


@pytest.mark.parametrize('issue', ['regions', 'source', 'ancestor', 'candidate', 'mask', 'delete', 'symlink'])
def test_guard_replay_rejects_material_drift(tmp_path, issue):
    case = unit_case(tmp_path)
    binding = prepare_case(tmp_path, case)
    source, primary, *_ = case
    primary.write_bytes(source.read_bytes())
    if issue == 'regions': (tmp_path/binding['regions']['path']).write_text('{}')
    elif issue == 'source': source.write_bytes(b'changed')
    elif issue == 'ancestor': (tmp_path/binding['lineage'][0]['path']).write_text('{}')
    elif issue == 'candidate': write_fits(primary, np.zeros((3, 8, 8)))
    elif issue == 'mask':
        binding['eligibility']['texture_snr'] += 1
    elif issue == 'delete': (tmp_path/binding['lineage'][0]['path']).unlink()
    elif issue == 'symlink':
        actual = source.with_name('actual.fit'); source.rename(actual); source.symlink_to(actual)
    with pytest.raises((contract.ContractError, ValueError, KeyError)):
        guards.measure(tmp_path, source, primary, binding)


def test_historical_v13_clahe_replays_and_finalizes_without_new_gate(linear_case, monkeypatch):
    session, _, original, starless, *_ = linear_case
    payload = core.load_json(session/'session.json')
    payload['execution_policy'] = BASELINE['policy']
    payload.pop('session_sha256')
    payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(session/'session.json', payload)
    # Mint an old-rule fixture, then restore the current policy before replay.
    with monkeypatch.context() as old:
        old.setattr(sessions, 'EXECUTION_POLICY', BASELINE['policy'])
        parent = session/'artifacts/070-stretch.fit'
        view = session/'previews/070-stretch.jpg'
        legacy = session/'scripts/070-stretch.ssf'
        regions = session/'reports/legacy-stretch-regions.json'
        contract.atomic_write_json(regions, {'schema':'starun-siril.regions.v1',
            'reference':{'sha256':contract.sha256_file(starless),'width':512,'height':512},
            'regions':[{'id':'shell','role':'faint','rect':[240,240,272,272]},
                       {'id':'core','role':'core','rect':[250,250,262,262]}]})
        legacy.write_text('# stretch-visibility-regions: '+json.dumps({
            'path':regions.relative_to(session).as_posix(),**contract.fingerprint(regions,include_path=False)})+
            f'\nrequires 1.4.4 1.5.0\nset32bits\nload "{starless}"\nmtf 0 0.1 1\n'
            f'save "{parent}" -chksum\nicc_assign sRGB\nsavejpg "{view.with_suffix("")}" 95\nclose\n')
        support._write_ssf_provenance(session, legacy, 'stretch')
        assert core.run_script(session,protocol='stretch',script_value=legacy,source_value=starless,
            expected_values=[parent,view],timeout=30)['status']=='success'
        accept(session,'070-stretch')
        script, primary, expected = script_assets(session, parent)
        text = script.read_text()
        line = next(line for line in text.splitlines() if line.startswith('# local-contrast:'))
        declaration = json.loads(line.split(':', 1)[1]); declaration.pop('regions')
        script.write_text(text.replace(line, '# local-contrast: '+json.dumps(declaration)))
        support._write_ssf_provenance(session, script, 'structure.local-contrast')
        args = dict(protocol='structure.local-contrast', script_value=script, source_value=parent,
                    expected_values=expected, primary_output_value=primary, timeout=30)
        run = core.run_script(session, **args)
        assert run['status'] == 'success' and 'local_contrast_guard' not in run
        accept(session, run['id'])
        execute = core.subprocess.run
        inspection, direct, diagnostic = support._input_inspect_run(session, original, old)
        support._review(session, inspection['id'], diagnostic, parent_preview=direct)
        old.setattr(core.subprocess, 'run', execute)
        delivery_script, final = support._delivery_script(session, primary)
        delivery = core.run_script(session, protocol='delivery.render', script_value=delivery_script,
            source_value=primary, expected_values=[final, final.with_suffix('.fit')], timeout=30)
        assert delivery['status'] == 'success'
        review = accept(session, delivery['id'])
    manifest = core.load_json(session/'manifest.json')
    for entry in manifest['runs']:
        receipt_path = session/'runs'/entry['receipt']
        receipt = core.load_json(receipt_path)
        for reference in receipt['knowledge_validation']['references']:
            if reference['path'] in BASELINE['documents']:
                prior = BASELINE['documents'][reference['path']]
                reference.update({key: prior[key] for key in ('sha256', 'size')})
        provenance = session/receipt['script_provenance']['path']
        doc = core.load_json(provenance)
        doc['references'] = [{key: r[key] for key in ('path', 'role', 'sha256')}
                             for r in receipt['knowledge_validation']['references']]
        core.atomic_write_json(provenance, doc)
        receipt['script_provenance'].update(contract.fingerprint(provenance, include_path=False))
        for bound in receipt.get('review_bindings', []):
            bound['sha256'] = contract.sha256_file(session/bound['path'])
        core.atomic_write_json(receipt_path, receipt)
        entry['receipt_sha256'] = contract.sha256_file(receipt_path)
        review_path = session/'reviews'/entry['receipt']
        if review_path.exists():
            doc = core.load_json(review_path); doc['run_receipt_sha256'] = entry['receipt_sha256']
            for material in doc['inspected_materials']:
                if material['path'] == receipt_path.relative_to(session).as_posix():
                    material['sha256'] = entry['receipt_sha256']
            core.atomic_write_json(review_path, doc)
    core.atomic_write_json(session/'manifest.json', manifest)
    before = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert support._session_bytes(session) == before
    with pytest.raises(contract.ContractError, match='Legacy standalone'):
        core.run_script(session, **args, validate_only=True)
    assert support._session_bytes(session) == before
    selection = support._selection(session, delivery['id'], final, review)
    frozen = {p: p.read_bytes() for p in [session/'session.json', *list((session/'runs').glob('*.json')),
                                       *list((session/'reviews').glob('*.json'))]}
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert all(p.read_bytes() == raw for p, raw in frozen.items())
