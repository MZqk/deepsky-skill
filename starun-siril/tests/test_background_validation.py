"""Held-out science regression cases, including lineage failure isolation."""
import json

import numpy as np
import pytest

import deep_sky_siril_background as bg
import siril_auto_samples as samples
import deep_sky_siril_processing as processing
from deep_sky_siril_contract import sha256_file, ContractError
from .test_native_processing import write_fits
import deep_sky_siril_metrics as metrics


def regions_for(source, path):
    rectangles = [[25, 25, 100, 100], [500, 25, 575, 100],
                  [25, 260, 100, 335], [500, 260, 575, 335],
                  [25, 500, 100, 575], [500, 500, 575, 575]]
    doc = {'schema': 'starun-siril.regions.v1',
           'reference': {'sha256': sha256_file(source), 'width': 600, 'height': 600},
           'regions': [{'id': f'sky-{i}', 'role': 'background', 'rect': r} for i, r in enumerate(rectangles)]}
    path.write_text(json.dumps(doc))
    return doc


def display_tail(source, candidate, parent_preview, candidate_preview):
    return [['close'], ['load', str(source)], ['mtf', '0', '0.006', '1'],
            ['savejpg', str(parent_preview), '95'], ['close'], ['load', str(candidate)],
            ['mtf', '0', '0.006', '1'], ['savejpg', str(candidate_preview), '95'], ['close']]


def measure(tmp_path, before, after):
    p, c, r = [tmp_path / n for n in ('parent.fit', 'candidate.fit', 'regions.json')]
    write_fits(p, before, bitpix=-64); write_fits(c, after, bitpix=-64)
    regions_for(p, r)
    hashes = [sha256_file(q) for q in (p, c, r)]
    report = bg.measure_background(p, c, r, regions_sha256=hashes[2])
    assert hashes == [sha256_file(q) for q in (p, c, r)]
    return report


@pytest.fixture
def gradient():
    y, x = np.indices((600, 600))
    return (x + .4*y) / 600 * .02


def test_true_additive_gradient_improves_with_flat_channels(tmp_path, gradient):
    parent = np.stack([.1 + gradient, np.full_like(gradient, .1), .1 + gradient*.5])
    result = measure(tmp_path, parent, np.full_like(parent, .1))
    assert result['status'] == 'passed'
    assert result['dimensions']['R']['improved']
    assert not result['dimensions']['G']['improved']
    assert all(not d['worsened'] for d in result['dimensions'].values())
    assert result == bg.measure_background(tmp_path/'parent.fit', tmp_path/'candidate.fit', tmp_path/'regions.json', regions_sha256=sha256_file(tmp_path/'regions.json'))


def test_luminance_improvement_with_red_worsening_requires_visual_review(tmp_path, gradient):
    parent = .1 + np.stack([gradient*.1, gradient, gradient])
    candidate = .1 + np.stack([gradient*.5, gradient*.1, gradient*.1])
    result = measure(tmp_path, parent, candidate)
    assert result['status'] == 'passed'
    assert result['decision'] == 'review_required'
    assert any(w['dimension'] == 'R' for w in result['warnings'])
    assert result['dimensions']['luminance']['improved']
    assert result['dimensions']['R']['worsened']


@pytest.mark.parametrize('offset', [0, .03])
def test_flat_and_pure_dc_cannot_pass(tmp_path, offset):
    parent = np.full((3, 600, 600), .1)
    result = measure(tmp_path, parent, parent+offset)
    assert result['status'] == 'passed'
    assert result['decision'] == 'skip'
    assert 'No identifiable' in result['reasons'][0]
    assert result['dimensions']['R']['constant_offset'] == pytest.approx(offset)
    assert result['dimensions']['R']['offset_spatial_span'] < 1e-12


def test_gradient_pure_dc_cannot_pass(tmp_path, gradient):
    result = measure(tmp_path, .1+gradient, .12+gradient)
    assert result['status'] == 'passed'
    assert result['decision'] == 'skip'
    assert abs(result['dimensions']['mono']['improvement']) < 1e-12


def test_subthreshold_improvement_is_rejected(tmp_path, gradient):
    result = measure(tmp_path, .1+gradient, .1+gradient*.99999)
    d = result['dimensions']['mono']
    assert 0 < d['improvement'] < d['threshold']
    assert result['status'] == 'passed'
    assert result['decision'] == 'skip'


def test_block_uncertainty_exceeds_floor(tmp_path, gradient):
    rng = np.random.Generator(np.random.PCG64(42))
    noise = np.repeat(np.repeat(rng.normal(0, .02, (24, 24)), 25, axis=0), 25, axis=1)
    result = measure(tmp_path, .1+gradient+noise, .1+gradient*.95+noise+np.repeat(np.repeat(rng.normal(0,.001,(24,24)),25,axis=0),25,axis=1))
    d = result['dimensions']['mono']
    assert d['threshold'] > 1e-6
    assert abs(d['improvement']) < d['threshold']
    assert result['status'] == 'passed'
    assert result['decision'] == 'skip'


def test_legal_chromatic_additive_gradient(tmp_path, gradient):
    parent = .1 + np.stack([gradient, -gradient*.4, gradient*.6])
    candidate = .1 + np.stack([gradient*.1, -gradient*.04, gradient*.06])
    assert measure(tmp_path, parent, candidate)['status'] == 'passed'


def test_common_luminance_gradient_is_a_benefit_without_chromatic_change(tmp_path, gradient):
    parent = .1 + np.stack([gradient]*3)
    result = measure(tmp_path, parent, np.full_like(parent, .1))
    assert result['decision'] == 'review_required'
    assert result['benefit_dimensions'] == ['luminance']


def test_chromatic_estimate_uses_pixel_differences(tmp_path):
    a = np.full((3,600,600), .1)
    for y in range(0,600,25):
        for x in range(0,600,25):
            for channel, values in ((0,(0,.01,.02)), (1,(.01,.02,0))):
                block = np.full(625,.1)
                for i, value in enumerate(values):
                    block[i*208:(i+1)*208] += value
                a[channel,y:y+25,x:x+25] = block.reshape(25,25)
    row = measure(tmp_path,a,a)['heldout_regions'][0]['parent']
    assert row['R_minus_G'] == pytest.approx(-.01)
    assert row['R_minus_G'] != pytest.approx(row['R']-row['G'])


def test_inherited_nonpositive_pixels_do_not_block_benefit(tmp_path, gradient):
    parent, candidate = .1+gradient, np.full_like(gradient,.1)
    parent[0,:100] = candidate[0,:100] = -.01
    result = measure(tmp_path,parent,candidate)
    assert result['status'] == 'passed'
    assert result['decision'] == 'review_required'
    assert result['new_shadow_crossings']['counts'] == [0]


@pytest.mark.parametrize('count, status', [(35,'passed'), (36,'failed')])
def test_new_shadow_crossing_threshold(tmp_path, gradient, count, status):
    candidate = np.full_like(gradient,.1)
    candidate[0,:count] = 0
    result = measure(tmp_path,.1+gradient,candidate)
    assert result['status'] == status
    assert result['new_shadow_crossings']['counts'] == [count]
    if status == 'failed':
        assert result['decision'] == 'blocked'


def test_recovered_pixels_cannot_cancel_new_shadow_crossings(tmp_path, gradient):
    parent, candidate = .1+gradient, np.full_like(gradient,.1)
    parent[0,:36] = 0
    candidate[0,36:72] = 0
    result = measure(tmp_path,parent,candidate)
    assert result['clipping_counts_shadow_highlight']['parent'] == result['clipping_counts_shadow_highlight']['candidate']
    assert result['new_shadow_crossings']['counts'] == [36]
    assert result['decision'] == 'blocked'


def test_bound_v2_metrics_use_background_decision_without_relaxing_other_stages():
    histogram = {'measurement_domain':'scientific','channels':[{'shadow_clip_rate':.02}]}
    binding = {'policy':bg.POLICY,'status':'passed','decision':'review_required'}
    gates, verdict = metrics.evaluate_stage_gates('background.subtract',histogram,background_validation=binding)
    assert gates['background']['verdict'] == 'uncertain'
    assert verdict == 'uncertain'
    assert metrics.evaluate_stage_gates('stretch',histogram,background_validation=binding)[1] == 'reject'
    binding.update(status='failed',decision='blocked')
    assert metrics.evaluate_stage_gates('background.subtract',histogram,background_validation=binding)[1] == 'reject'


@pytest.mark.parametrize('failure', ['mismatch', 'before_save', 'rewrite_science', 'missing_parent', 'duplicate_preview'])
def test_display_contract_blocks_uncomparable_or_nonlinear_science(tmp_path, failure):
    source, candidate = tmp_path/'source.fit', tmp_path/'artifacts/candidate.fit'
    commands = [['load',str(source)], ['subsky','1','-existing'], ['save',str(candidate)],
                *display_tail(source,candidate,tmp_path/'previews/parent.jpg',tmp_path/'previews/candidate.jpg')]
    assert processing.validate_background_display(tmp_path,source,candidate,commands,policy='heldout_material_gate_v2')['transfer']['parameters'] == [0,.006,1]
    if failure == 'mismatch': commands[-3] = ['mtf','0','.01','1']
    elif failure == 'before_save': commands.insert(2,['mtf','0','.006','1'])
    elif failure == 'rewrite_science': commands.insert(-1,['save',str(candidate)])
    elif failure == 'missing_parent': del commands[3:8]
    else: commands[-2] = commands[6].copy()
    with pytest.raises(ContractError):
        processing.validate_background_display(tmp_path,source,candidate,commands,policy='heldout_material_gate_v2')


def test_luminance_is_pixel_derived_not_weighted_channel_medians(tmp_path):
    a = np.full((3, 600, 600), .1)
    # Each 25x25 block has three disjoint primary-color populations.
    for y in range(0, 600, 25):
        for x in range(0, 600, 25):
            for c in range(3):
                block = a[c, y:y+25, x:x+25].copy().reshape(-1)
                block[c*208:(c+1)*208] += .1
                a[c, y:y+25, x:x+25] = block.reshape(25, 25)
    result = measure(tmp_path, a, a)
    row = result['heldout_regions'][0]['parent']
    weighted = sum(row[n]*w for n, w in zip(('R','G','B'), (.2126,.7152,.0722)))
    assert row['luminance'] != pytest.approx(weighted)


@pytest.mark.parametrize('failure', ['geometry', 'nonfinite', 'hash', 'roi_overlap', 'distribution', 'small_roi', 'unknown_fields'])
def test_insufficient_evidence_blocks_measurement(tmp_path, failure):
    p, c, r = [tmp_path/n for n in ('p.fit', 'c.fit', 'regions.json')]
    a = np.full((600,600), .1); write_fits(p, a); write_fits(c, a)
    doc = regions_for(p, r)
    if failure == 'geometry': write_fits(c, np.full((601,600), .1))
    elif failure == 'nonfinite':
        a[300,300] = np.nan; write_fits(c, a)
    elif failure == 'hash': doc['reference']['sha256'] = '0'*64
    elif failure == 'roi_overlap': doc['regions'][1]['rect'] = doc['regions'][0]['rect']
    elif failure == 'distribution': doc['regions'] = doc['regions'][:4]
    elif failure == 'small_roi': doc['regions'][0]['rect'][2] = 99
    elif failure == 'unknown_fields': doc['extra'] = True
    r.write_text(json.dumps(doc))
    result = bg.measure_background(p,c,r,regions_sha256=sha256_file(r))
    assert result['status'] == 'uncertain'
    assert result['reasons']


def test_region_drift_during_execution(tmp_path):
    p, c, r = [tmp_path/n for n in ('p.fit', 'c.fit', 'regions.json')]
    write_fits(p, np.full((600,600), .1)); write_fits(c, np.full((600,600), .1))
    regions_for(p,r); digest=sha256_file(r); r.write_text(r.read_text()+' ')
    assert bg.measure_background(p,c,r,regions_sha256=digest)['status'] == 'uncertain'


def test_no_overlap_below_and_above_cap():
    proposals = [(x,y,0) for y in range(20,280,10) for x in range(20,280,10)]
    for limit, candidates in ((256, proposals[:100]), (40,proposals)):
        selected = samples._spatial_selection(candidates,limit,300,300)
        boxes = [bg.sample_rect(*p[:2]) for p in selected]
        assert all(not bg.intersects(a,b) for i,a in enumerate(boxes) for b in boxes[i+1:])
        assert selected == samples._spatial_selection(candidates,limit,300,300)


def test_exclusion_and_validation_contamination(tmp_path):
    p, r = tmp_path/'p.fit', tmp_path/'regions.json'
    write_fits(p, np.full((600,600), .1)); doc=regions_for(p,r)
    diagnostics={}
    contract=samples.generate_background_samples(p,exclude_regions_path=r,diagnostics=diagnostics)
    bg.validate_samples(contract,doc)
    assert diagnostics['exclusions']['heldout_positions'] > 0
    bad=json.loads(json.dumps(contract)); bad['fit_samples'].append({'id':'extra','x':150,'y':150})
    bad['fit_samples'].append({'id':'duplicate-footprint','x':151,'y':151})
    with pytest.raises(ValueError,match='overlap'): bg.validate_samples(bad,doc)
    bad=json.loads(json.dumps(contract)); bad['fit_samples'].append({'id':'polluted','x':110,'y':110})
    with pytest.raises(ValueError,match='contaminates'): bg.validate_samples(bad,doc)


def test_declaration_unique_and_directory_bound(tmp_path):
    source=tmp_path/'p.fit'; write_fits(source,np.full((600,600),.1))
    run='030-background'; directory=tmp_path/'reports'/run;directory.mkdir(parents=True)
    r=directory/'regions.json';regions_for(source,r)
    c=directory/'samples.json'; contract=samples.generate_background_samples(source,exclude_regions_path=r);c.write_text(json.dumps(contract))
    command=['pyscript','bridge','--contract',str(c),'--contract-sha256',sha256_file(c),'--expected-source',str(source),'--receipt',str(directory/'injection.json')]
    declaration='# background-validation: '+json.dumps({'regions':str(r.relative_to(tmp_path)),'sha256':sha256_file(r)})
    candidate = tmp_path/'artifacts/candidate.fit'
    commands = [['load', str(source)], ['fmul', '1'], command, ['subsky', '1', '-existing'], ['save', str(candidate)],
                *display_tail(source, candidate, tmp_path/'previews/parent.jpg', tmp_path/'previews/candidate.jpg')]
    assert processing.prepare_background(tmp_path,source,declaration,commands,run,candidate)['regions_sha256']==sha256_file(r)
    for text in ('',declaration+'\n'+declaration):
        with pytest.raises(ContractError,match='exactly one'): processing.prepare_background(tmp_path,source,text,[command],run)
    with pytest.raises(ContractError,match='directory'): processing.prepare_background(tmp_path,source,declaration,[command],'032-background')


def test_missing_execution_evidence_retains_uncertain_report(tmp_path, gradient):
    parent, candidate = tmp_path/'parent.fit', tmp_path/'candidate.fit'
    write_fits(parent, .1+gradient); write_fits(candidate, np.full_like(gradient,.1))
    directory = tmp_path/'reports/030-background'; directory.mkdir(parents=True)
    r = directory/'regions.json'; regions_for(parent,r)
    metadata = {'background_validation': {'regions':str(r.relative_to(tmp_path)), 'regions_sha256':sha256_file(r),
        'sample_contract':'reports/030-background/deleted-samples.json', 'sample_contract_sha256':'0'*64,
        'bridge_receipt':'reports/030-background/missing-injection.json', 'report':'reports/030-background/background-validation.json'}}
    result = processing.validate_processing_outputs(tmp_path,parent,candidate,metadata)
    assert result['background_validation']['status'] == 'uncertain'
    report = directory/'background-validation.json'
    assert json.loads(report.read_text())['status'] == 'uncertain'
    assert result['background_validation']['sha256'] == sha256_file(report)
