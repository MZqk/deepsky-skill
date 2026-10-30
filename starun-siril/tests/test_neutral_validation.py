"""Independent neutral benefit, paired safety and receipt binding regressions."""
import copy
import json

import numpy as np
import pytest

import deep_sky_siril_processing as native
import deep_sky_siril_metrics as metrics
from deep_sky_siril_contract import ContractError, sha256_file
from deep_sky_siril_neutral import (
    endpoint_transitions, measure_neutral_validation, propose_partition,
    validate_neutral_binding, validate_partition,
)
from .test_native_processing import neutral_case, write_fits


def case(tmp_path, white=False):
    source, candidate, payload, probe, text, regions, coefficients = neutral_case(tmp_path, white=white)
    from deep_sky_siril_artifacts import read_fits_pixels
    raw, scale, zero = read_fits_pixels(source)
    values = np.asarray(raw, dtype=float)*scale+zero
    return source, candidate, regions, coefficients['background_partition'], values


def measure(source, candidate, regions, partition, values, *, white=False):
    contract = json.loads(regions.read_text())
    write_fits(source, values)
    contract['reference']['sha256'] = sha256_file(source)
    regions.write_text(json.dumps(contract))
    reference = {'source': 'user', 'evidence': 'Known synthetic white reference', 'region': 'white'} if white else None
    coefficients = native.neutral_coefficients(source, regions, reference, background_partition=partition)
    corrected = (values-np.asarray(coefficients['background'])[:, None, None])*np.asarray(coefficients['gains'])[:, None, None]+coefficients['target']
    write_fits(candidate, corrected)
    return coefficients, measure_neutral_validation(source, candidate, regions, coefficients, partition)


def test_equal_fit_and_all_coefficients_still_have_independent_benefit(tmp_path):
    source, candidate, regions, partition, values = case(tmp_path)
    coefficients, report = measure(source, candidate, regions, partition, values)
    assert coefficients['background'] == native.neutral_coefficients(source, regions)['background']
    benefit = report['benefit']
    assert benefit['summary']['parent']['median'] == pytest.approx(.02)
    assert benefit['summary']['candidate']['median'] < 1e-7
    assert benefit['numerically_improved_fraction'] == 1
    assert report['recommended_verdict'] == 'uncertain'
    assert report['method'] == 'pure_additive'
    assert report['safety']['channels'][1]['shadow']['parent'] == 1
    assert report['safety']['channels'][1]['shadow']['new'] == 0


def test_validation_pixels_do_not_change_background_or_white_gains(tmp_path):
    source, candidate, regions, partition, values = case(tmp_path, white=True)
    first, _ = measure(source, candidate, regions, partition, values, white=True)
    contract = json.loads(regions.read_text())
    for r in contract['regions']:
        if r['id'] in partition['validation']:
            x0,y0,x1,y1 = r['rect']
            values[:, 32-y1:32-y0, x0:x1] += np.asarray((.1,.05,.08))[:,None,None]
    second, report = measure(source, candidate, regions, partition, values, white=True)
    assert second['background'] == first['background']
    assert second['gains'] == first['gains']
    assert report['method'] == 'reference_white_balance'
    star = report['safety']['regions'][0]
    assert np.max(np.abs(star['gain_adjusted_signal_delta'])) < 1e-7


def test_neutral_background_has_no_gain_and_invalid_signal_ratio_is_unavailable(tmp_path):
    source, candidate, regions, partition, values = case(tmp_path)
    values[:] = .03
    _, report = measure(source, candidate, regions, partition, values)
    assert report['benefit']['numerically_improved_fraction'] == 0
    assert report['benefit']['summary']['parent']['median'] == 0
    assert report['safety']['regions'][0]['signal_chromaticity']['parent']['status'] == 'unavailable'


def test_contaminated_fit_can_worsen_heldout_sky_without_auto_accept(tmp_path):
    source, candidate, regions, partition, values = case(tmp_path)
    values[:] = .03
    contract = json.loads(regions.read_text())
    for r in contract['regions']:
        if r['id'] in partition['fit']:
            x0,y0,x1,y1 = r['rect']
            values[0, 32-y1:32-y0, x0:x1] += .05
    _, report = measure(source, candidate, regions, partition, values)
    assert report['benefit']['numerically_worsened_fraction'] == 1
    assert report['benefit']['paired_reduction']['median'] < 0
    assert report['recommended_verdict'] == 'uncertain'


def test_spatial_tail_worsening_is_not_hidden_by_improved_median(tmp_path):
    source, candidate, regions, partition, values = case(tmp_path)
    contract = json.loads(regions.read_text())
    r = next(r for r in contract['regions'] if r['id'] == partition['validation'][0])
    x0,y0,x1,y1 = r['rect']
    values[:,32-y1:32-y0,x0:x1] = np.asarray((.05,.03,.04))[:,None,None]
    _, report = measure(source, candidate, regions, partition, values)
    assert report['benefit']['numerically_improved_fraction'] == pytest.approx(2/3)
    assert report['benefit']['numerically_worsened_fraction'] == pytest.approx(1/3)
    assert report['benefit']['summary']['candidate']['p90'] > report['benefit']['summary']['parent']['p90']


@pytest.mark.parametrize('invalid', ['duplicate', 'missing', 'foreign', 'too_few', 'overlap', 'subject_overlap'])
def test_partition_rejects_bad_bindings(tmp_path, invalid):
    _,_,regions,partition,_ = case(tmp_path)
    contract = json.loads(regions.read_text())
    if invalid == 'duplicate': partition['validation'][0] = partition['fit'][0]
    if invalid == 'missing': partition['fit'].append('absent')
    if invalid == 'foreign': partition['validation'][0] = 'white'
    if invalid == 'too_few': partition['fit'].pop()
    if invalid == 'overlap': contract['regions'][3]['rect'] = contract['regions'][0]['rect']
    if invalid == 'subject_overlap': contract['regions'][0]['rect'] = [20,8,28,16]
    with pytest.raises(ValueError): validate_partition(contract['regions'], partition)


def test_geometry_partition_is_order_invariant_and_ignores_pixel_values(tmp_path):
    _,_,regions,_,_ = case(tmp_path)
    contract = json.loads(regions.read_text())
    a = propose_partition(contract)
    contract['regions'].reverse()
    assert propose_partition(contract) == a
    assert len(a['fit']) == len(a['validation']) == 3


def test_paired_endpoint_recovery_cannot_cancel_new_damage():
    parent = np.full((8,8), .5)
    candidate = parent.copy()
    parent[0,0] = 0; candidate[0,0] = .2
    parent[1,1] = 1; candidate[1,1] = .8
    candidate[2,2] = 0; candidate[3,3] = 1
    record = endpoint_transitions(parent,candidate)
    for role in ('shadow','highlight'):
        assert record[role]['parent'] == record[role]['candidate'] == 1
        assert record[role]['new'] == record[role]['recovered'] == 1
        assert record[role]['maximum_new_overshoot'] == 0
    assert sum(t['new_shadow'] for t in record['new_crossing_grid']['tiles']) == 1
    assert sum(t['new_highlight'] for t in record['new_crossing_grid']['tiles']) == 1
    assert next(t for t in record['new_crossing_grid']['tiles'] if t['new_shadow'])['rect'] == [2,5,3,6]


def test_float_endpoint_rounding_is_counted_without_claiming_clipping():
    parent = np.full((8,8), .5); candidate = parent.copy()
    parent[0,0] = np.nextafter(0., 1.)
    candidate[0,0] = 0
    parent[0,1] = np.nextafter(1., 0.)
    candidate[0,1] = 1
    record = endpoint_transitions(parent,candidate)
    assert record['shadow']['new'] == record['highlight']['new'] == 1
    assert 'not proof of hard clipping' in record['interpretation']
    candidate[0,0] = np.nan
    with pytest.raises(ValueError, match='finite'): endpoint_transitions(parent,candidate)


def test_bound_neutral_metrics_preserve_inherited_endpoints_and_refuse_drift(tmp_path):
    source,candidate,regions,partition,values = case(tmp_path)
    coefficients, report = measure(source,candidate,regions,partition,values)
    args = dict(run_id='040-neutral',protocol='color.calibrate',candidate_path=candidate,parent_path=source)
    assert metrics.generate_metric_report(**args)['recommended_verdict'] == 'reject'
    assert metrics.generate_metric_report(**args,neutral_validation=report)['recommended_verdict'] == 'uncertain'
    incomplete = copy.deepcopy(report)
    del incomplete['safety']
    with pytest.raises(ValueError, match='complete'):
        metrics.generate_metric_report(**args, neutral_validation=incomplete)
    damaged = copy.deepcopy(report)
    damaged['benefit']['summary']['candidate']['median'] += .1
    with pytest.raises(ValueError, match='differs'): validate_neutral_binding(damaged,source,candidate,regions,coefficients,partition)
    regions.write_text(regions.read_text()+' ')
    with pytest.raises(ValueError, match='partition'): validate_neutral_binding(report,source,candidate,regions,coefficients,partition)
    write_fits(candidate,values)
    with pytest.raises(ValueError, match='bound'): metrics.generate_metric_report(**args,neutral_validation=report)


def test_new_neutral_requires_partition_before_native_execution(tmp_path):
    source,candidate,payload,probe,text,regions,_ = neutral_case(tmp_path)
    evidence = json.loads(next(line.split(':',1)[1] for line in text.splitlines() if line.startswith('# color-calibration:')))
    del evidence['background_partition']
    text = '\n'.join('# color-calibration: '+json.dumps(evidence) if line.startswith('# color-calibration:') else line for line in text.splitlines())
    with pytest.raises(ContractError,match='background_partition'):
        native.prepare_color_calibration(tmp_path,payload,source,candidate,native.script_commands(text),text,probe,{'run':None})


@pytest.mark.parametrize('role', ['fit', 'validation'])
def test_nonfinite_sky_cannot_enter_coefficients_or_heldout_report(tmp_path, role):
    source,candidate,regions,partition,values = case(tmp_path)
    contract = json.loads(regions.read_text())
    r = next(r for r in contract['regions'] if r['id'] == partition[role][0])
    x0,y0,x1,y1 = r['rect']
    values[0,32-y1,x0] = np.nan
    with pytest.raises(ContractError, match='finite'):
        measure(source,candidate,regions,partition,values)
