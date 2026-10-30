"""Optional subject evidence and complete signed recomposition diagnostics."""
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image

from .test_native_processing import metrics, native, region_contract, recompose_steps, write_fits
from .test_deep_sky_siril import _delivery_run, _session, _write_ssf_provenance, core, session_state
from deep_sky_siril_contract import sha256_file


def comparison_steps(root):
    full, starless, stars = [root / 'artifacts' / name for name in ('full.fit', 'starless.fit', 'stars.fit')]
    commands = [['save', str(path)] for path in (full, starless, stars)]
    for name, source in [('full-fixed', full), ('starless-fixed', starless)]:
        commands += [['load', str(source)], ['mtf', '0', '.1', '1'],
                     ['savejpg', str(root / 'previews' / name), '95'], ['close']]
    return commands, full, starless, stars


def test_matching_native_previews_freeze_the_same_display_chain(tmp_path):
    commands, full, starless, _ = comparison_steps(tmp_path)
    display = native._validate_separation_previews(tmp_path, commands, full, starless)
    assert display == {'full_preview': 'previews/full-fixed.jpg', 'starless_preview': 'previews/starless-fixed.jpg',
                       'transfer_chain': [{'command': 'mtf', 'parameters': ['0', '.1', '1']}]}
    commands[8][2] = '.10'
    assert native._validate_separation_previews(tmp_path, commands, full, starless)
    assert native._validate_separation_previews(tmp_path, commands[:3], full, starless) is None


@pytest.mark.parametrize('issue', ['mismatch', 'early', 'scientific-save', 'unpaired', 'wrong-source', 'autostretch', 'duplicate-output'])
def test_comparison_mtf_cannot_change_scientific_layers_or_mislabel_views(tmp_path, issue):
    commands, full, starless, stars = comparison_steps(tmp_path)
    if issue == 'mismatch':
        commands[8][2] = '.2'
    elif issue == 'early':
        commands[:0] = [['load', str(full)], ['mtf', '0', '.1', '1'], ['savejpg', str(tmp_path / 'previews/early')]]
    elif issue == 'scientific-save':
        commands.append(['save', str(stars)])
    elif issue == 'unpaired':
        commands = commands[:7]
    elif issue == 'wrong-source':
        commands[7][1] = str(stars)
    elif issue == 'autostretch':
        commands.insert(5, ['autostretch', '-linked', '-2.8', '.22'])
    else:
        commands[9][1] = commands[5][1]
    with pytest.raises(core.ContractError, match='Comparison'):
        native._validate_separation_previews(tmp_path, commands, full, starless)


def separation_case(root, kind='local'):
    parent = np.full((3, 4, 4), .1)
    residual = np.zeros_like(parent)
    residual[:, 3, 1] = .04
    if kind == 'cancellation':
        residual[:, 0, 3] = -.04
    elif kind == 'weak':
        parent[1:] *= .01
        residual[1:] *= .01
    paths = [root / name for name in ('parent.fit', 'starless.fit', 'stars.fit', 'regions.json')]
    for path, array in zip(paths[:3], (parent, parent - residual, residual)):
        write_fits(path, array, bitpix=-64)
    document = region_contract(paths[3], paths[0], rect=(0, 0, 4, 4))
    document['regions'][0]['role'] = 'stars' if kind == 'stellar' else 'core'
    paths[3].write_text(json.dumps(document))
    return paths


@pytest.mark.parametrize('kind', ['local', 'cancellation', 'weak', 'stellar'])
def test_optional_subject_report_measures_without_accepting_or_changing_pixels(tmp_path, kind):
    parent, candidate, stars, regions = separation_case(tmp_path, kind)
    paths = (parent, candidate, stars, regions)
    before = [sha256_file(p) for p in paths]
    report = metrics.generate_metric_report(
        run_id='060-stars', protocol='stars.separate', parent_path=parent, candidate_path=candidate,
        regions_path=regions, star_layer_path=stars, output_path=tmp_path / 'subject-metrics.json')
    roi = report['region_metrics']
    assert roi['status'] == 'measured' and report['recommended_verdict'] == 'uncertain'
    assert roi['images']['star_layer']['sha256'] == before[2]
    diagnostic = roi['regions'][0]['separation_diagnostics']
    assert diagnostic['requires_visual_review'] is True
    assert diagnostic['policy'] == 'subject_roi_residual_v2'
    for channel in diagnostic['channels']:
        assert channel['median'] == 0 and channel['absolute_mass'] > 0
        assert channel['max_abs_closure'] < 1e-12
        assert 'loss_flag' not in channel and 'target_leakage' not in channel
        if kind == 'cancellation':
            assert channel['positive_count'] == channel['negative_count'] == 1
            assert channel['positive_mass'] == pytest.approx(channel['negative_mass'])
        else:
            assert channel['positive_count'] == 1 and channel['negative_count'] == 0
            assert channel['max_abs_difference_position'] == [1, 0]
    assert [sha256_file(p) for p in paths] == before
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('issue', ['geometry', 'channels', 'nonfinite', 'unsupported'])
def test_optional_star_layer_reports_unavailable_evidence(tmp_path, issue):
    parent, candidate, stars, regions = separation_case(tmp_path)
    if issue == 'geometry':
        write_fits(stars, np.zeros((3, 3, 4)))
    elif issue == 'channels':
        write_fits(stars, np.zeros((1, 4, 4)))
    elif issue == 'nonfinite':
        values = np.zeros((3, 4, 4)); values[0, 3, 1] = np.nan
        write_fits(stars, values)
    else:
        stars = tmp_path / 'stars.jpg'; Image.new('RGB', (4, 4)).save(stars)
    report = metrics.generate_metric_report(
        run_id='060-stars', protocol='stars.separate', parent_path=parent, candidate_path=candidate,
        regions_path=regions, star_layer_path=stars)
    assert report['region_metrics']['status'] == ('unsupported' if issue == 'unsupported' else 'uncertain')
    if issue == 'nonfinite':
        channels = report['region_metrics']['regions'][0]['separation_diagnostics']['channels']
        assert channels[0]['closure_nonfinite_count'] == 1 and channels[0]['status'] == 'uncertain'
        assert all(row['status'] == 'measured' for row in channels[1:])
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('missing', ['parent', 'regions', 'protocol'])
def test_star_layer_cli_requires_explicit_separation_context(tmp_path, missing):
    parent, candidate, stars, regions = separation_case(tmp_path)
    output = tmp_path / 'report.json'
    argv = ['--run-id', '060-stars', '--protocol', 'stretch' if missing == 'protocol' else 'stars.separate',
            '--candidate', str(candidate), '--star-layer', str(stars), '--output', str(output)]
    if missing != 'parent':
        argv += ['--parent', str(parent)]
    if missing != 'regions':
        argv += ['--regions', str(regions)]
    with pytest.raises(SystemExit) as exc:
        metrics.main(argv)
    assert exc.value.code == 2 and not output.exists()


def test_star_layer_cli_and_evidence_write_protection(tmp_path):
    parent, candidate, stars, regions = separation_case(tmp_path)
    arguments = dict(run_id='060-stars', protocol='stars.separate', parent_path=parent,
                     candidate_path=candidate, regions_path=regions, star_layer_path=stars)
    with pytest.raises(ValueError, match='overwrite'):
        metrics.generate_metric_report(**arguments, output_path=stars)
    output = tmp_path / 'subject-metrics.json'
    assert metrics.main(['--run-id', '060-stars', '--protocol', 'stars.separate', '--parent', str(parent),
                         '--candidate', str(candidate), '--regions', str(regions), '--star-layer', str(stars),
                         '--output', str(output)]) == 0
    assert json.loads(output.read_text())['region_metrics']['images']['star_layer']['sha256'] == sha256_file(stars)


def test_star_layer_change_during_measurement_is_detected(tmp_path, monkeypatch):
    import deep_sky_siril_artifacts as artifacts
    parent, candidate, stars, regions = separation_case(tmp_path)
    reader = artifacts.read_fits_pixels
    def changing_reader(path):
        result = reader(path)
        if path == stars:
            write_fits(stars, np.full((3, 4, 4), .01))
        return result
    monkeypatch.setattr(artifacts, 'read_fits_pixels', changing_reader)
    with pytest.raises(ValueError, match='changed during measurement'):
        metrics.measure_regions(candidate, parent, regions, star_layer_path=stars)


def recomposition_case(root, failed_channel=0, ratio=.008601):
    base = np.full((3, 100, 100), .25)
    residual = np.full_like(base, .01)
    residual[failed_channel].flat[:100] = -(9900 * .01 * ratio / (100 * (1 - ratio)))
    branch = {role: name for role, name in zip(
        ('full_baseline', 'original_starless', 'display_stars'), ('full.fit', 'base.fit', 'stars.fit'))}
    for role, array in zip(branch, (base + residual, base, residual)):
        write_fits(root / branch[role], array, bitpix=-64)
    return {'target_type': 'nebula', 'branch_binding': branch}, base, residual


@pytest.mark.parametrize('failed_channel', [0, 2])
def test_recomposition_failure_retains_all_channels_and_residual_locations(tmp_path, failed_channel):
    metadata, base, residual = recomposition_case(tmp_path, failed_channel)
    before = [sha256_file(tmp_path / p) for p in metadata['branch_binding'].values()]
    with pytest.raises(native.ProcessingEvidenceError, match='Recomposition rejected') as exc:
        native.validate_processing_outputs(tmp_path, tmp_path / 'base.fit', tmp_path / 'candidate.fit', metadata, execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})
    assert exc.value.code == 'processing_contract_invalid'
    closure = exc.value.processing_checks['closure']
    assert len(closure['channels']) == len(closure['per_channel_max_abs']) == 3
    assert max(closure['per_channel_max_abs']) < 1e-5
    assert min(closure['per_channel_min_residual']) > -.10
    assert closure['per_channel_neg_energy_ratio'][failed_channel] == pytest.approx(.008601)
    failed = closure['channels'][failed_channel]
    assert failed['reasons'] == ['negative_energy_ratio_exceeded']
    assert failed['negative_pixel_count'] == 100 and failed['minimum_residual_position'] == [0, 99]
    tiles = failed['negative_energy_grid']['tiles']
    assert len(tiles) == 64 and sum(t['negative_pixel_count'] for t in tiles) == 100
    assert sum(t['negative_energy'] for t in tiles) == pytest.approx(float(np.maximum(-residual[failed_channel], 0).sum()))
    assert all(t['rect'][1] >= 87 for t in tiles if t['negative_pixel_count'])
    assert [sha256_file(tmp_path / p) for p in metadata['branch_binding'].values()] == before
    json.dumps(exc.value.processing_checks, allow_nan=False)


@pytest.mark.parametrize('issue', ['geometry', 'channels', 'nonfinite', 'unreadable'])
def test_recomposition_records_unmeasurable_layers_without_losing_other_channels(tmp_path, issue):
    metadata, base, residual = recomposition_case(tmp_path)
    if issue == 'geometry':
        write_fits(tmp_path / 'base.fit', base[:, :-1])
    elif issue == 'channels':
        write_fits(tmp_path / 'base.fit', base[:1])
    elif issue == 'nonfinite':
        residual[0, 0, 0] = np.nan; write_fits(tmp_path / 'stars.fit', residual)
    else:
        (tmp_path / 'base.fit').unlink()
    with pytest.raises(native.ProcessingEvidenceError) as exc:
        native.validate_processing_outputs(tmp_path, tmp_path / 'base.fit', tmp_path / 'candidate.fit', metadata, execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})
    closure = exc.value.processing_checks['closure']
    assert len(closure['channels']) == 3 and closure['violations']
    assert closure['channels'][0]['status'] == 'uncertain'
    if issue == 'unreadable':
        assert 'original_starless' in closure['read_errors']
    if issue == 'nonfinite':
        assert closure['channels'][0]['nonfinite_counts']['display_stars'] == 1
        assert all(c['status'] == 'measured' for c in closure['channels'][1:])
    json.dumps(closure, allow_nan=False)


def test_recomposition_collects_different_violations_in_every_channel(tmp_path):
    metadata, base, residual = recomposition_case(tmp_path, failed_channel=2)
    residual[0, 0, 0] = -.125
    full = base + residual; full[1, 0, 0] += .00002
    write_fits(tmp_path / 'full.fit', full, bitpix=-64)
    write_fits(tmp_path / 'stars.fit', residual, bitpix=-64)
    with pytest.raises(native.ProcessingEvidenceError) as exc:
        native.validate_processing_outputs(tmp_path, tmp_path / 'base.fit', tmp_path / 'candidate.fit', metadata, execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})
    channels = exc.value.processing_checks['closure']['channels']
    assert 'negative_residual_exceeded' in channels[0]['reasons']
    assert channels[1]['reasons'] == ['closure_exceeded']
    assert channels[2]['reasons'] == ['negative_energy_ratio_exceeded']


@pytest.mark.parametrize('failed_channel', [0, 2])
@pytest.mark.parametrize('native_failure', [False, True])
def test_run_commits_complete_failed_recomposition_evidence(tmp_path, monkeypatch, failed_channel, native_failure):
    session, original, _ = _session(tmp_path, monkeypatch, input_state='nonlinear')
    delivery, _ = _delivery_run(session, original, monkeypatch)
    source = session/delivery['delivery_export']['fits']
    chain = [{'command': 'mtf', 'parameters': ['0', '.1', '1']}]
    steps, outputs = recompose_steps(session, chain, original=source, starless=source, source=source)
    for row in steps:
        if row[0] == 'save':
            row.append('-chksum')
    script = session / 'scripts/090-recompose.ssf'
    script.write_text('requires 1.4.4 1.5.0\nset32bits\n' + '\n'.join(
        ' '.join(json.dumps(v) for v in row) for row in steps) + '\nclose\n')
    _write_ssf_provenance(session, script, 'stars.recompose')
    branch = native.validate_recomposition(session, steps, source, source, source, outputs[-1], chain)
    # Isolate persistence from already-covered lineage checks and real Siril execution.
    monkeypatch.setattr(native, 'prepare_processing', lambda *a, **k: {'branch_binding': branch})
    def siril_run(command, **kwargs):
        base = np.full((3, 100, 100), .25); residual = np.full_like(base, .01)
        residual[failed_channel].flat[:100] = -.125
        for path, values in zip(outputs, (base + residual, base, residual, base + residual)):
            write_fits(path, values, bitpix=-64)
        return subprocess.CompletedProcess(command, 0, 'Traceback (most recent call last):\nValueError: native failure' if native_failure else '')
    monkeypatch.setattr(core.subprocess, 'run', siril_run)
    monkeypatch.setattr(core, '_validate_output', lambda path, **kwargs: {
        'passed': True, 'decoder': {'artifact_fingerprint': core.fingerprint(path, include_path=False)}})
    receipt = core.run_script(str(session), protocol='stars.recompose', script_value=str(script), source_value=str(source),
                              expected_values=list(map(str, outputs)), primary_output_value=str(outputs[-1]), timeout=30)
    assert receipt['status'] == 'failed' and receipt['exit_code'] == 0
    assert receipt['processing_checks']['status'] == 'failed'
    assert len(receipt['processing_checks']['closure']['channels']) == 3
    assert all(row['passed'] for row in receipt['output_validations'].values())
    assert core.load_json(session / 'runs/090-recompose.json') == receipt
    assert outputs[-1].resolve() not in session_state._verified_run_outputs(session)
    with pytest.raises(core.ContractError, match='Run receipt is invalid'):
        core._load_run(session, '090-recompose')
