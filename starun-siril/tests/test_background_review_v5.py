"""Reviewable small gains, explicitly constrained plane layouts and immutable v4 evidence."""
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

import deep_sky_siril_background as bg
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_metrics as metrics
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
import siril_auto_samples as samples
from deep_sky_siril_tooling import _subprocess_environment
from .test_background_validation import measure, display_tail
from .test_native_processing import write_fits
from .test_xisf_metrics import xisf
from .test_protected_background_sampling import manual_run
from ._policy_pins import replay_and_finalize_without_migration

ROOT = Path(__file__).parents[1]
HISTORY = ROOT/'references/background-review-history.json'
LAYOUT = 'limited_plane_v1'
REASON = 'Only three independently inspected sky controls fit outside the protected shell.'


def limited_case(tmp_path, channels=3, container='fits'):
    y, x = np.indices((256, 256))
    values = np.stack([.1+(x+.4*y)*.00003*(c+1) for c in range(channels)]).astype('float32')
    source = tmp_path/f'source.{container}'
    if container == 'fits':
        write_fits(source, values[:, ::-1])
    else:
        xisf(source, values, attributes={'imageType': 'MasterLight'}, keywords=[('IMAGETYP', "'Master Light'")])
    folder = tmp_path/'reports/030-background'
    folder.mkdir(parents=True)
    regions = {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': 256, 'height': 256},
        'regions': [{'id': f'sky-{i}', 'role': 'background', 'rect': rect} for i, rect in enumerate(
            [[10,10,60,60], [196,10,246,60], [10,196,60,246]])] +
            [{'id': 'shell', 'role': 'faint', 'rect': [90,90,166,166]}]}
    regions_path = folder/'regions.json'
    contract.atomic_write_json(regions_path, regions)
    fit = {'schema': 'starun-siril.background-sample-contract.v1', 'source': {'path': str(source), **regions['reference']},
        'fit_samples': [{'id': f'fit-{i}', 'x': px, 'y': py} for i, (px, py) in enumerate(
            [(100,25), (155,25), (25,125), (230,125), (100,230), (155,230)])]}
    fit_path = folder/'samples.json'
    contract.atomic_write_json(fit_path, fit)
    return source, regions_path, regions, fit_path, fit


def script_for(tmp_path, source, regions_path, fit_path, **changes):
    primary = tmp_path/'artifacts/030-background.fit'
    primary.parent.mkdir(exist_ok=True)
    previews = tmp_path/'previews'
    previews.mkdir(exist_ok=True)
    injection = fit_path.parent/'injection.json'
    declaration = {'regions': str(regions_path.relative_to(tmp_path)), 'sha256': contract.sha256_file(regions_path),
                   'layout': LAYOUT, 'layout_reason': REASON, **changes}
    rows = [['requires', '1.4.4', '1.5.0'], ['set32bits'], ['load', str(source)], ['fmul', '1'],
        ['pyscript', str(ROOT/'scripts/siril_background_samples_scientific.py'), '--contract', str(fit_path),
         '--contract-sha256', contract.sha256_file(fit_path), '--expected-source', str(source), '--receipt', str(injection)],
        ['subsky', '1', '-existing'], ['save', str(primary), '-chksum'],
        *display_tail(source, primary, previews/'parent', previews/'candidate')]
    text = '# background-validation: '+json.dumps(declaration)+'\n'+'\n'.join(
        ' '.join(json.dumps(token) if any(c.isspace() for c in token) else token for token in row) for row in rows)+'\n'
    return primary, injection, rows, text


def test_small_positive_gain_is_reviewable_only_under_v5(tmp_path):
    y, x = np.indices((600, 600))
    gradient = (x+.4*y)/600*.02
    old = measure(tmp_path, .1+gradient, .1+gradient*.99999)
    new = bg.measure_background(tmp_path/'parent.fit', tmp_path/'candidate.fit', tmp_path/'regions.json',
        regions_sha256=contract.sha256_file(tmp_path/'regions.json'), policy=bg.REVIEW_POLICY)
    assert old['decision'] == 'skip'
    assert new['status'] == 'passed' and new['decision'] == 'review_required'
    assert new['dimensions'] == old['dimensions'] and new['uncertainty'] == old['uncertainty']
    assert not new['constant_offset_only']
    assert new['warnings'][0]['code'] == 'low_significance_benefit'
    assert new['benefit_dimensions'] == ['luminance']
    histogram = {'measurement_domain': 'scientific', 'channels': [{'shadow_clip_rate': .1}]}
    gates, verdict = metrics.evaluate_stage_gates('background.subtract', histogram, background_validation=new)
    assert gates['background']['verdict'] == verdict == 'uncertain'


@pytest.mark.parametrize('operation', ['identity', 'constant', 'worse'])
def test_identity_dc_and_nonpositive_gain_stay_skipped(tmp_path, operation):
    y, x = np.indices((600, 600))
    before = .1+(x+.4*y)/600*.02
    after = before if operation == 'identity' else before+.03 if operation == 'constant' else before*1.1
    measure(tmp_path, before, after)
    result = bg.measure_background(tmp_path/'parent.fit', tmp_path/'candidate.fit', tmp_path/'regions.json',
        regions_sha256=contract.sha256_file(tmp_path/'regions.json'), policy=bg.REVIEW_POLICY)
    assert result['status'] == 'passed' and result['decision'] == 'skip'
    assert result['constant_offset_only'] is (operation != 'worse')


@pytest.mark.parametrize('count, status', [(35, 'passed'), (36, 'failed')])
def test_v5_retains_exact_new_shadow_boundary(tmp_path, count, status):
    y, x = np.indices((600,600))
    before = .1+(x+.4*y)/600*.02
    after = np.full_like(before, .1)
    after[0, :count] = 0
    measure(tmp_path, before, after)
    result = bg.measure_background(tmp_path/'parent.fit', tmp_path/'candidate.fit', tmp_path/'regions.json',
        regions_sha256=contract.sha256_file(tmp_path/'regions.json'), policy=bg.REVIEW_POLICY)
    assert result['status'] == status and result['new_shadow_crossings']['counts'] == [count]
    assert (result['decision'] == 'blocked') is (status == 'failed')


def test_limited_layout_is_explicit_and_bound_to_one_plane(tmp_path):
    source, path, regions, fit_path, fit = limited_case(tmp_path)
    with pytest.raises(ValueError, match='75x75'):
        bg.load_regions(path, source, sampling_policy=bg.SAMPLING_POLICY)
    bg.load_regions(path, source, sampling_policy=bg.SAMPLING_POLICY, layout=LAYOUT)
    coverage = bg.validate_samples(fit, regions, sampling_policy=bg.SAMPLING_POLICY, layout=LAYOUT)
    assert coverage['status'] == 'sufficient' and coverage['count'] == 6
    assert coverage['plane_geometry']['rank'] == 3
    primary, _, commands, text = script_for(tmp_path, source, path, fit_path)
    prepared = processing.prepare_background(tmp_path, source, text, commands, '030-background', primary,
        policy=bg.REVIEW_POLICY, sampling_policy=bg.SAMPLING_POLICY)
    assert prepared['layout'] == LAYOUT and prepared['layout_reason'] == REASON
    assert prepared['coverage'] == coverage
    for command in (['subsky', '2', '-existing'], ['subsky', '4', '-existing'], ['subsky', '-rbf', '-existing']):
        changed = [command if row[0] == 'subsky' else row for row in commands]
        with pytest.raises(contract.ContractError, match='only subsky 1'):
            processing.prepare_background(tmp_path, source, text, changed, '030-background', primary,
                policy=bg.REVIEW_POLICY, sampling_policy=bg.SAMPLING_POLICY)
    with pytest.raises(contract.ContractError, match='declaration fields'):
        processing.prepare_background(tmp_path, source, text, commands, '030-background', primary,
            policy=bg.POLICY, sampling_policy=bg.SAMPLING_POLICY)


@pytest.mark.parametrize('change', ['missing', 'empty', 'bool', 'unknown-layout', 'null-layout', 'unknown-field'])
def test_invalid_layout_declarations_cannot_authorize_fitting(change):
    declaration = {'regions': 'reports/030-background/regions.json', 'sha256': '0'*64,
                   'layout': LAYOUT, 'layout_reason': REASON}
    if change == 'missing': declaration.pop('layout_reason')
    elif change == 'empty': declaration['layout_reason'] = ' '
    elif change == 'bool': declaration['layout_reason'] = True
    elif change == 'unknown-layout': declaration['layout'] = 'automatic'
    elif change == 'null-layout': declaration['layout'] = None
    else: declaration['minimum_samples'] = 1
    with pytest.raises((contract.ContractError, ValueError)):
        processing.background_declaration('# background-validation: '+json.dumps(declaration),
            [['subsky', '1', '-existing']], bg.REVIEW_POLICY)


@pytest.mark.parametrize('change', ['few', 'small', 'overlap', 'subject', 'span', 'collinear', 'condition'])
def test_limited_controls_preserve_independence_and_geometry(tmp_path, change):
    source, path, doc, *_ = limited_case(tmp_path)
    if change == 'few': doc['regions'] = doc['regions'][:2]
    elif change == 'small': doc['regions'][0]['rect'][2] -= 1
    elif change == 'overlap': doc['regions'][1]['rect'] = doc['regions'][0]['rect']
    elif change == 'subject': doc['regions'][-1]['rect'] = doc['regions'][0]['rect']
    elif change == 'span':
        doc['regions'] = [{'id': str(i), 'role': 'background', 'rect': [10, y, 60, y+50]} for i, y in enumerate([10,100,196])]
    else:
        doc['regions'] = [{'id': str(i), 'role': 'background', 'rect': [a,b,a+50,b+50]}
            for i, (a,b) in enumerate([(10,10), (103,104 if change == 'condition' else 103), (196,196)])]
    contract.atomic_write_json(path, doc)
    with pytest.raises(ValueError):
        bg.load_regions(path, source, sampling_policy=bg.SAMPLING_POLICY, layout=LAYOUT)


@pytest.mark.parametrize('change', ['few', 'span', 'collinear', 'condition', 'overlap', 'padded', 'subject', 'outside'])
def test_limited_samples_keep_all_footprint_guards(tmp_path, change):
    _, _, regions, _, fit = limited_case(tmp_path)
    points = fit['fit_samples']
    if change == 'few': fit['fit_samples'] = points[:5]
    elif change == 'span':
        for point in points: point['x'] = 100+point['x']/10
    elif change in {'collinear', 'condition'}:
        for i, point in enumerate(points):
            point.update(x=20+40*i, y=20+40*i+(i == 3 and change == 'condition'))
    elif change == 'overlap': points[0].update(x=150, y=25)
    elif change == 'padded': points[0].update(x=80, y=25)
    elif change == 'subject': points[0].update(x=110, y=110)
    else: points[0].update(y=5)
    with pytest.raises(ValueError):
        bg.validate_samples(fit, regions, sampling_policy=bg.SAMPLING_POLICY, layout=LAYOUT)


def test_sampler_layout_and_proposals_do_not_relax_neutral(tmp_path):
    source, regions, *_ = limited_case(tmp_path)
    proposal = samples.propose_validation_regions(source, background_layout=LAYOUT)
    assert proposal['layout'] == LAYOUT and proposal['requires_visual_confirmation']
    assert all(r['rect'][2]-r['rect'][0] == r['rect'][3]-r['rect'][1] == 50 for r in proposal['proposals'])
    assert proposal['proposals']
    with pytest.raises(ValueError, match='exclude-regions'):
        samples.generate_background_samples(source, target_count=6, background_layout=LAYOUT)
    fit = samples.generate_background_samples(source, target_count=6, exclude_regions_path=regions, background_layout=LAYOUT)
    assert len(fit['fit_samples']) == 6
    with pytest.raises(ValueError, match='12..256'):
        samples.generate_background_samples(source, target_count=6)
    with pytest.raises(ValueError, match='Neutralization'):
        samples.main(['--source', str(source), '--output', str(tmp_path/'neutral.json'), '--regions', '--background-layout', LAYOUT])


@pytest.mark.parametrize('channels', [1, 3])
@pytest.mark.parametrize('container', ['fits', 'xisf'])
def test_native_limited_layout_uses_scientific_source_and_six_samples(tmp_path, channels, container):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file(): pytest.skip('Native Siril unavailable')
    source, regions, _, fit_path, _ = limited_case(tmp_path, channels, container)
    primary, injection, commands, text = script_for(tmp_path, source, regions, fit_path)
    prepared = processing.prepare_background(tmp_path, source, text, commands, '030-background', primary,
        policy=bg.REVIEW_POLICY, sampling_policy=bg.SAMPLING_POLICY)
    script = tmp_path/'native.ssf'; script.write_text(text)
    config = tmp_path/'s.ini'; config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}', '--offline', f'--script={script}'],
        capture_output=True, text=True, timeout=45, env=_subprocess_environment('siril_runtime'))
    assert result.returncode == 0, result.stdout+result.stderr
    assert contract.load_json(injection)['installed_count'] == 6
    checked = processing.validate_processing_outputs(tmp_path, source, primary, {'background_validation': prepared},
        execution_policy=contract.EXECUTION_POLICY)
    report = contract.load_json(tmp_path/prepared['report'])
    assert checked['background_validation']['status'] == 'passed'
    assert report['decision'] == 'review_required' and report['layout'] == LAYOUT
    assert report['fit_coverage']['count'] == 6
    assert report['validation_coverage']['complete_block_counts'] == [4,4,4]
    assert report['scientific_sources']['parent']['row_order'] == 'fits_bottom_up'
    assert report['scientific_sources']['parent']['image_index'] == 0
    assert not any(report['new_shadow_crossings']['counts'])


def test_v5_standard_receipt_replays_and_binds_layout(tmp_path, monkeypatch):
    session, receipt, args = manual_run(tmp_path, monkeypatch)
    assert receipt['background_validation']['layout'] == 'standard'
    before = {p: p.read_bytes() for p in session.rglob('*') if p.is_file()}
    assert core.run_script(session, **args)['replayed']
    assert all(p.read_bytes() == value for p, value in before.items())


def test_native_limited_receipt_review_and_replay(tmp_path, monkeypatch):
    from . import test_deep_sky_siril as support
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file(): pytest.skip('Native Siril unavailable')
    source, regions, _, fit_path, _ = limited_case(tmp_path, channels=1)
    monkeypatch.setattr(support.tooling, 'probe_tools', lambda **kwargs: support._fake_probe(binary, offline=True))
    session = tmp_path/'session'
    sessions.init_session(str(source), str(session), input_state='linear', channel_mode='mono', channel_map=None,
        state_evidence=['user confirmed input_state=linear; synthetic mono stacked master'],
        target_name=None, target_type='emission_nebula', style='natural', stars='adaptive', offline=True,
        keep_intermediates=True)
    with monkeypatch.context() as inspection:
        support._input_inspect_run(session, source, inspection)
    folder = session/'reports/030-background'; folder.mkdir()
    regions = regions.replace(folder/'regions.json')
    fit_path = fit_path.replace(folder/'samples.json')
    primary, injection, _, text = script_for(session, source, regions, fit_path)
    script = session/'scripts/030-background.ssf'; script.write_text(text)
    support._write_ssf_provenance(session, script, 'background.subtract')
    args = dict(protocol='background.subtract', script_value=script, source_value=source, timeout=45,
        expected_values=[primary, session/'previews/parent.jpg', session/'previews/candidate.jpg', injection])
    receipt = core.run_script(session, **args)
    assert receipt['status'] == 'success', receipt
    assert receipt['background_validation']['layout'] == LAYOUT
    assert receipt['processing_checks']['background_validation']['layout_reason'] == REASON
    review = support._review(session, receipt['id'], session/'previews/candidate.jpg', parent_preview=session/'previews/parent.jpg')
    document = contract.load_json(review)
    report = session/receipt['processing_checks']['background_validation']['path']
    document['inspected_materials'].append({'path': str(report.relative_to(session)), 'sha256': contract.sha256_file(report)})
    contract.atomic_write_json(review, document)
    assert core.check_review(session, receipt['id'], review)['status'] == 'review_valid'
    before = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert support._session_bytes(session) == before


@pytest.mark.parametrize('change', ['layout', 'reason', 'coverage', 'policy'])
def test_v5_receipt_rejects_layout_and_policy_drift(tmp_path, monkeypatch, change):
    session, receipt, args = manual_run(tmp_path, monkeypatch)
    if change == 'layout': receipt['background_validation']['layout'] = LAYOUT
    elif change == 'reason': receipt['background_validation']['layout_reason'] = 'Changed after execution'
    elif change == 'coverage': receipt['background_validation']['coverage']['count'] = 100
    else: receipt['background_validation']['policy'] = bg.POLICY
    path = session/f"runs/{receipt['id']}.json"
    contract.atomic_write_json(path, receipt)
    manifest = contract.load_json(session/'manifest.json')
    next(row for row in manifest['runs'] if row['id'] == receipt['id'])['receipt_sha256'] = contract.sha256_file(path)
    contract.atomic_write_json(session/'manifest.json', manifest)
    with pytest.raises(contract.ContractError):
        core.run_script(session, **args)


def test_v4_background_replays_original_measurement_and_references(tmp_path, monkeypatch):
    from . import test_ai_morphology_v13 as history
    prior = sessions._PRE_BACKGROUND_REVIEW_BINDINGS
    with monkeypatch.context() as outgoing:
        outgoing.setattr(sessions, 'EXECUTION_POLICY', dict(prior['execution_policy']))
        outgoing.setattr(contract, 'EXECUTION_POLICY', dict(prior['execution_policy']))
        session, receipt, args = manual_run(tmp_path, monkeypatch)
        assert receipt['background_validation']['policy'] == bg.POLICY
        assert 'layout' not in receipt['background_validation']
    with monkeypatch.context() as frozen:
        frozen.setattr(history, 'BASELINE', {'policy': prior['execution_policy'], 'documents': prior['documents']})
        history._freeze_v12(session)
    before = {p: p.read_bytes() for p in session.rglob('*') if p.is_file()}
    assert core.run_script(session, **args)['replayed']
    assert all(p.read_bytes() == value for p, value in before.items())


def test_prior_policy_documents_and_implementation_are_exact(tmp_path):
    prior = contract.load_json(HISTORY)
    assert sessions._PRE_BACKGROUND_REVIEW_BINDINGS == prior
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(prior['execution_policy'])
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': prior['execution_policy']})
    assert stretch.implementation(tmp_path) == prior['implementation']
    for name, binding in prior['documents'].items():
        assert sessions.registered_guidance_reference(prior, {'path': name, **binding})
        assert not sessions.registered_guidance_reference({'execution_policy': contract.EXECUTION_POLICY}, {'path': name, **binding})


def test_prior_evidence_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    replay_and_finalize_without_migration(tmp_path, monkeypatch, HISTORY)
