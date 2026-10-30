"""Full-footprint foreground exclusions and frozen manual sampling rules."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from .test_background_validation import regions_for, write_fits, display_tail
from .test_xisf_metrics import xisf
from .test_deep_sky_siril import core
import deep_sky_siril_background as background
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import siril_auto_samples as samples
from siril_background_samples import install_declared_samples
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, sha256_file


def manual_case(tmp_path, container='fits'):
    y, x = np.indices((600, 600))
    values = (.1 + .02*x/600 + .008*y/600).astype(np.float32)[None]
    source = tmp_path / ('source.' + container)
    if container == 'fits':
        write_fits(source, values[:, ::-1])
    else:
        xisf(source, values)
    directory = tmp_path / 'reports/030-background'
    directory.mkdir(parents=True)
    regions_path = directory / 'regions.json'
    regions = regions_for(source, regions_path)
    regions['regions'].append({'id': 'nebula', 'role': 'faint', 'rect': [220, 220, 380, 380]})
    regions_path.write_text(json.dumps(regions))
    coordinates = [(150, 70), (450, 70), (50, 160), (550, 160),
                   (150, 260), (450, 260), (150, 340), (450, 340),
                   (50, 440), (550, 440), (150, 530), (450, 530)]
    contract = {'schema': 'starun-siril.background-sample-contract.v1',
                'source': {'path': str(source), **regions['reference']},
                'fit_samples': [{'id': f'sky-{i}', 'x': x, 'y': y} for i, (x, y) in enumerate(coordinates)]}
    contract_path = directory / 'samples.json'
    contract_path.write_text(json.dumps(contract))
    return source, regions_path, regions, contract_path, contract


@pytest.mark.parametrize('role', ['core', 'faint', 'stars'])
@pytest.mark.parametrize('x', [300, 210, 207.1])
def test_manual_full_footprint_must_avoid_every_protected_role(tmp_path, role, x):
    _, _, regions, _, contract = manual_case(tmp_path)
    regions['regions'][-1]['role'] = role
    contract['fit_samples'].append({'id': 'blocked-sample', 'x': x, 'y': 300})
    assert background.validate_samples(contract, regions)['status'] == 'sufficient'
    with pytest.raises(ValueError, match=f'blocked-sample.*nebula.*{role}'):
        background.validate_samples(contract, regions, sampling_policy=background.SAMPLING_POLICY)


@pytest.mark.parametrize('container', ['fits', 'xisf'])
@pytest.mark.parametrize('role', ['core', 'faint', 'stars'])
def test_complete_heldout_roi_cannot_overlap_protected_region(tmp_path, container, role):
    source, path, regions, _, _ = manual_case(tmp_path, container)
    regions['regions'].append({'id': 'sky-contaminant', 'role': role, 'rect': [95, 90, 110, 110]})
    path.write_text(json.dumps(regions))
    before = path.read_bytes()
    background.load_regions(path, source, scientific=True)
    with pytest.raises(ValueError, match=f'ROI sky-0.*sky-contaminant.*{role}'):
        background.load_regions(path, source, scientific=True, sampling_policy=background.SAMPLING_POLICY)
    assert path.read_bytes() == before


@pytest.mark.parametrize('container', ['fits', 'xisf'])
def test_sparse_manual_layout_and_automatic_samples_share_safety_validation(tmp_path, container):
    source, path, regions, _, contract = manual_case(tmp_path, container)
    observed, _ = background.load_regions(path, source, scientific=True, sampling_policy=background.SAMPLING_POLICY)
    coverage = background.validate_samples(contract, observed, sampling_policy=background.SAMPLING_POLICY)
    assert coverage['status'] == 'sufficient' and coverage['count'] == 12
    contract['fit_samples'].append({'id': 'boundary-adjacent', 'x': 207, 'y': 300})
    assert background.validate_samples(contract, regions, sampling_policy=background.SAMPLING_POLICY)['status'] == 'sufficient'
    automatic = samples.generate_background_samples(source, exclude_regions_path=path)
    assert background.validate_samples(automatic, regions, sampling_policy=background.SAMPLING_POLICY)['status'] == 'sufficient'


def test_four_corners_cannot_replace_middle_sky_constraints(tmp_path):
    _, _, regions, _, contract = manual_case(tmp_path)
    contract['fit_samples'] = [{'id': f'corner-{i}', 'x': x, 'y': y} for i, (x, y) in enumerate(
        [(x, y) for y in (50, 100, 500, 550) for x in (50, 100, 500, 550)])]
    with pytest.raises(ValueError, match='middle third'):
        background.validate_samples(contract, regions, sampling_policy=background.SAMPLING_POLICY)


def test_field_filling_foreground_blocks_automatic_contract_publication(tmp_path):
    source, path, regions, contract_path, _ = manual_case(tmp_path)
    regions['regions'][-1]['rect'] = [0, 0, 600, 600]
    path.write_text(json.dumps(regions))
    contract_path.unlink()
    with pytest.raises(ValueError, match='ROI sky-0.*nebula'):
        samples.main(['--source', str(source), '--output', str(contract_path), '--exclude-regions', str(path)])
    assert not contract_path.exists()


def prepare_manual(tmp_path, source, regions_path, contract_path, *, sampling_policy=background.SAMPLING_POLICY, order=1):
    primary = tmp_path / 'artifacts/030-background.fit'
    injection = contract_path.parent / 'injection.json'
    bridge = ['pyscript', 'bridge', '--contract', str(contract_path), '--receipt', str(injection)]
    commands = [['load', str(source)], ['fmul', '1'], bridge, ['subsky', str(order), '-existing'],
                ['save', str(primary)], *display_tail(source, primary, tmp_path/'previews/p.jpg', tmp_path/'previews/c.jpg')]
    text = '# background-validation: ' + json.dumps({'regions': str(regions_path.relative_to(tmp_path)), 'sha256': sha256_file(regions_path)})
    binding = processing.prepare_background(tmp_path, source, text, commands, '030-background', primary,
        policy=background.POLICY, sampling_policy=sampling_policy)
    return primary, injection, binding


@pytest.mark.parametrize('container', ['fits', 'xisf'])
@pytest.mark.parametrize('kind', ['inside', 'footprint', 'roi'])
def test_static_background_rejects_contamination_without_success_receipt(tmp_path, container, kind):
    source, path, regions, contract_path, contract = manual_case(tmp_path, container)
    if kind == 'roi':
        regions['regions'].append({'id': 'sky-contaminant', 'role': 'core', 'rect': [95, 90, 110, 110]})
        path.write_text(json.dumps(regions))
    else:
        contract['fit_samples'].append({'id': 'blocked-sample', 'x': 300 if kind == 'inside' else 210, 'y': 300})
        contract_path.write_text(json.dumps(contract))
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ContractError, match='intersects protected region'):
        prepare_manual(tmp_path, source, path, contract_path)
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not (contract_path.parent/'background-validation.json').exists()
    assert not (tmp_path/'runs/030-background.json').exists()


def test_postprocessing_rechecks_manual_footprints_and_sampling_policy(tmp_path):
    from deep_sky_siril_artifacts import read_scientific_pixels
    source, path, regions, contract_path, contract = manual_case(tmp_path)
    primary, injection, binding = prepare_manual(tmp_path, source, path, contract_path)
    primary.parent.mkdir()
    write_fits(primary, np.full((600, 600), .1))
    contract['fit_samples'].append({'id': 'late-contamination', 'x': 210, 'y': 300})
    contract_path.write_text(json.dumps(contract))
    binding['sample_contract_sha256'] = sha256_file(contract_path)
    requested = [[p['x'], p['y']] for p in contract['fit_samples']]
    injection.write_text(json.dumps({'schema': 'starun-siril.background-sample-injection.v1', 'status': 'verified',
        'source': str(source), 'contract': {'sha256': sha256_file(contract_path)},
        'requested_positions': requested, 'installed_positions': requested,
        'image_precision': {'scientific_source': read_scientific_pixels(source)[1]}}))
    checks = processing.validate_processing_outputs(tmp_path, source, primary, {'background_validation': binding},
                                                   execution_policy={**EXECUTION_POLICY, 'background': background.POLICY})
    report = json.loads((tmp_path/binding['report']).read_text())
    assert report['sampling_policy'] == background.SAMPLING_POLICY
    assert checks['background_validation']['sampling_policy'] == background.SAMPLING_POLICY
    assert report['status'] == 'uncertain' and report['decision'] == 'blocked'
    assert any('late-contamination' in reason and 'nebula' in reason for reason in report['reasons'])


def test_sample_installation_clears_preexisting_grid_and_keeps_only_manual_positions():
    class Siril:
        def __init__(self):
            self.points = [(300, 300)]
            self.cleared = False
        def clear_image_bgsamples(self):
            self.cleared = True
            self.points = []
        def set_image_bgsamples(self, points, **kwargs):
            assert self.cleared and not self.points
            self.points = points
            return True
        def get_image_bgsamples(self):
            return [SimpleNamespace(position=p, valid=True) for p in self.points]
    siril = Siril()
    requested = [(100, 100), (500, 500)]
    result = install_declared_samples(siril, requested)
    assert siril.points == requested and result['installed_count'] == 2
    assert result['installed_positions'] == [[100, 100], [500, 500]]


def test_complete_outgoing_filter_policy_stays_historical():
    assert {**{k: v for k, v in {k:v for k,v in sessions._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}.items() if k != 'background_sampling'}, 'neutral': 'heldout_benefit_safety_v1', 'guidance': 'stage4_xisf_inputs_v10'} == sessions._DWARF_FILTER_EXECUTION_POLICY
    assert sessions.known_execution_policy(sessions._DWARF_FILTER_EXECUTION_POLICY)
    assert not sessions.execution_policy_is_current({'execution_policy': sessions._DWARF_FILTER_EXECUTION_POLICY})


def manual_run(tmp_path, monkeypatch):
    from .test_deep_sky_siril import tooling, _fake_probe, _write_jpeg, _write_ssf_provenance, _input_inspect_run
    from siril_background_samples_scientific import verify_image_precision
    from deep_sky_siril_artifacts import read_scientific_pixels
    source, regions_path, _, contract_path, contract = manual_case(tmp_path)
    binary = tmp_path/'siril-cli'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    monkeypatch.setattr(tooling, 'probe_tools', lambda **kwargs: _fake_probe(binary, offline=True))
    session = tmp_path/'session'
    sessions.init_session(str(source), str(session), input_state='linear',
        state_evidence=['user confirmed input_state=linear; channel_mode=mono; synthetic calibrated stacked master'],
        channel_mode='mono', channel_map=None, target_name=None, target_type='emission_nebula',
        style='natural', stars='adaptive', offline=True, keep_intermediates=True)
    directory = session/'reports/030-background'
    directory.mkdir()
    regions_path = regions_path.replace(directory/'regions.json')
    contract_path = contract_path.replace(directory/'samples.json')
    primary = session/'artifacts/030-background.fit'
    injection = directory/'injection.json'
    parent_preview, preview = session/'previews/parent.jpg', session/'previews/candidate.jpg'
    script = session/'scripts/030-background.ssf'
    adapter = Path(__file__).resolve().parents[1]/'scripts/siril_background_samples_scientific.py'
    script.write_text('requires 1.4.4 1.5.0\n# background-validation: ' + json.dumps(
        {'regions': str(regions_path.relative_to(session)), 'sha256': sha256_file(regions_path)}) +
        f'\nset32bits\nload "{source}"\nfmul 1\npyscript "{adapter}" --contract "{contract_path}" '
        f'--contract-sha256 {sha256_file(contract_path)} --expected-source "{source}" --receipt "{injection}"\n'
        f'subsky 1 -existing\nstat main\nsave "{primary}" -chksum\nclose\nload "{source}"\n'
        f'mtf 0 0.006 1\nsavejpg "{parent_preview}" 95\nclose\nload "{primary}"\n'
        f'mtf 0 0.006 1\nsavejpg "{preview}" 95\nclose\n')
    _write_ssf_provenance(session, script, 'background.subtract')
    _input_inspect_run(session, source, monkeypatch)
    requested = [[p['x'], p['y']] for p in contract['fit_samples']]
    precision = verify_image_precision(SimpleNamespace(data=read_scientific_pixels(source)[0].astype(np.float32),
        bitpix=-32, top_down=False), source)
    def run(command, **kwargs):
        write_fits(primary, np.full((600, 600), .1))
        _write_jpeg(parent_preview)
        _write_jpeg(preview)
        core.atomic_write_json(injection, {'schema': 'starun-siril.background-sample-injection.v1', 'status': 'verified',
            'source': str(source), 'contract': {'sha256': sha256_file(contract_path)}, 'image_precision': precision,
            'requested_positions': requested, 'installed_positions': requested})
        return subprocess.CompletedProcess(command, 0, '')
    monkeypatch.setattr(core.subprocess, 'run', run)
    monkeypatch.setattr(core, '_validate_output', lambda path, **kwargs:
        {'passed': True, 'decoder': {'artifact_fingerprint': core.fingerprint(path, include_path=False)}})
    args = dict(protocol='background.subtract', script_value=str(script), source_value=str(source),
        expected_values=[str(p) for p in [primary, parent_preview, preview, injection]], timeout=30)
    receipt = core.run_script(str(session), **args)
    assert receipt['status'] == 'success'
    return session, receipt, args


@pytest.mark.parametrize('tamper', ['metadata', 'checks', 'report', 'session'])
def test_receipt_replay_rejects_sampling_label_downgrade(tmp_path, monkeypatch, tamper):
    session, receipt, args = manual_run(tmp_path, monkeypatch)
    report_path = session/receipt['background_validation']['report']
    assert receipt['background_validation']['sampling_policy'] == background.SAMPLING_POLICY
    assert receipt['processing_checks']['background_validation']['sampling_policy'] == background.SAMPLING_POLICY
    assert json.loads(report_path.read_text())['sampling_policy'] == background.SAMPLING_POLICY
    before = {p: p.read_bytes() for p in session.rglob('*') if p.is_file()}
    assert core.run_script(str(session), **args)['replayed']
    assert all(p.read_bytes() == data for p, data in before.items())
    if tamper == 'session':
        payload = core.load_json(session/'session.json')
        payload['execution_policy'] = dict(sessions._DWARF_FILTER_EXECUTION_POLICY)
        payload.pop('session_sha256')
        payload['session_sha256'] = core.stable_hash(payload)
        core.atomic_write_json(session/'session.json', payload)
    elif tamper == 'report':
        document = core.load_json(report_path)
        document.pop('sampling_policy')
        core.atomic_write_json(report_path, document)
        receipt['processing_checks']['background_validation'].update(core.fingerprint(report_path, include_path=False))
    else:
        record = receipt['background_validation'] if tamper == 'metadata' else receipt['processing_checks']['background_validation']
        record.pop('sampling_policy')
    receipt_path = session/f"runs/{receipt['id']}.json"
    core.atomic_write_json(receipt_path, receipt)
    manifest = core.load_json(session/'manifest.json')
    next(r for r in manifest['runs'] if r['id'] == receipt['id'])['receipt_sha256'] = sha256_file(receipt_path)
    core.atomic_write_json(session/'manifest.json', manifest)
    with pytest.raises(ContractError):
        core.run_script(str(session), **args)


def freeze_historical_background(session, receipt):
    payload = core.load_json(session/'session.json')
    payload['execution_policy'] = dict(sessions._DWARF_FILTER_EXECUTION_POLICY)
    payload.pop('session_sha256')
    payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(session/'session.json', payload)
    report_path = session/receipt['background_validation']['report']
    document = core.load_json(report_path)
    bg = receipt['background_validation']
    historical = background.measure_background(Path(payload['input']['path']), session/receipt['primary_output'],
        session/bg['regions'], regions_sha256=bg['regions_sha256'], policy=payload['execution_policy']['background'],
        sampling_policy=None, layout=bg.get('layout', 'standard'), layout_reason=bg.get('layout_reason'))
    historical.update(image_precision=document['image_precision'], display=bg['display'])
    document = historical
    core.atomic_write_json(report_path, document)
    receipt['background_validation'].pop('sampling_policy')
    receipt['processing_checks']['background_validation'].pop('sampling_policy')
    receipt['processing_checks']['background_validation'].update(core.fingerprint(report_path, include_path=False))
    provenance_path = session/receipt['script_provenance']['path']
    provenance = core.load_json(provenance_path)
    digest, size = '71f6d96f719bf83f55ec3f47a96822f2eb8bef974f6d53579412a6225fcf2fe7', 17915
    for record in provenance['references']:
        if record['path'] == 'references/stages/stage-03-background.md':
            record['sha256'] = digest
    for record in receipt['knowledge_validation']['references']:
        if record['path'] == 'references/stages/stage-03-background.md':
            record.update(sha256=digest, size=size)
    core.atomic_write_json(provenance_path, provenance)
    receipt['script_provenance'].update(core.fingerprint(provenance_path, include_path=False))
    receipt_path = session/f"runs/{receipt['id']}.json"
    core.atomic_write_json(receipt_path, receipt)
    manifest = core.load_json(session/'manifest.json')
    next(r for r in manifest['runs'] if r['id'] == receipt['id'])['receipt_sha256'] = sha256_file(receipt_path)
    core.atomic_write_json(session/'manifest.json', manifest)


def test_historical_background_without_label_replays_unchanged(tmp_path, monkeypatch):
    session, receipt, args = manual_run(tmp_path, monkeypatch)
    freeze_historical_background(session, receipt)
    before = {p: p.read_bytes() for p in session.rglob('*') if p.is_file()}
    assert core.run_script(str(session), **args)['replayed']
    recovery = core._metric_recovery_inputs(session, receipt['id'], allow_legacy=True)
    assert recovery
    assert all(p.read_bytes() == data for p, data in before.items())
    fresh = session/'scripts/999-new.ssf'
    fresh.write_text('requires 1.4.4 1.5.0\nset32bits\nclose\n')
    with pytest.raises(ContractError, match='Legacy'):
        core.run_script(str(session), **{**args, 'script_value': str(fresh)}, validate_only=True)


def test_manual_background_requires_bound_stretch_before_delivery(tmp_path, monkeypatch):
    from .test_deep_sky_siril import (_review, _assert_unbound_stretch_rejected)
    session, receipt, _ = manual_run(tmp_path, monkeypatch)
    primary = session/receipt['primary_output']
    display = receipt['background_validation']['display']
    bg_review = _review(session, receipt['id'], session/display['candidate_preview'], parent_preview=session/display['parent_preview'])
    document = core.load_json(bg_review)
    report_path = session/receipt['background_validation']['report']
    document['inspected_materials'].append({'path': str(report_path.relative_to(session)), 'sha256': sha256_file(report_path)})
    core.atomic_write_json(bg_review, document)
    core.check_review(session, receipt['id'], bg_review)
    _assert_unbound_stretch_rejected(session, primary, monkeypatch)
    assert not (session/'artifacts/070-followup.fit').exists()
    assert not list((session/'outputs').iterdir())
