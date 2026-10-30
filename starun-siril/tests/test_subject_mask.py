"""Geometry, native spatial enhancement, evidence drift and historical replay."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_color as color
import deep_sky_siril_local_contrast as local
import deep_sky_siril_subject_mask as subject
from .test_recomposition_proposals import case, write
from .test_color_evaluation import scene, execute, review, save_review, rebind_fixture_parent
from .test_color_region_goals import regional, native_regional
from .test_starnet_linear_bridge import native
from .test_recomposition_sources_v11 import source_case, accept
from .test_multiscale_competition import request_file, run_candidate
from ._policy_pins import replay_and_finalize_without_migration

ROOT = Path(__file__).parents[1]


def request(source, session=None):
    _,h,w = color.pixels(source).shape
    return {'schema':subject.REQUEST,'source':source.relative_to(session).as_posix() if session else str(source),
            'reference':{'sha256':contract.sha256_file(source),'width':w,'height':h},
            'include':[[[0,0],[w,0],[w,h],[0,h]]],'exclude':[],'feather_px':2.}


def prepare_mask(session, source, **changes):
    value = request(source,session)|changes
    path = session/'reports/079-mask-request.json'
    contract.atomic_write_json(path,value)
    return subject.prepare(session,path,'079-mask',timeout=30)


def test_geometry_centers_holes_overlap_and_thin_shell(tmp_path):
    source = write(tmp_path/'source.fit',np.full((3,12,20),.1))
    value = request(source)
    value.update(include=[[[2,1],[14,1],[14,11],[2,11]]],exclude=[[[6,4],[10,4],[10,8],[6,8]]])
    subject.validate_request(value,source)
    mask = subject.rasterize(value)
    assert mask.shape == (12,20)
    assert np.all(mask[:1] == 0) and np.all(mask[:,:2] == 0) and np.all(mask[:,14:] == 0)
    assert mask[1,3] == pytest.approx(.15625)
    t = np.sqrt(2.5)/2
    assert mask[3,4] == pytest.approx(t*t*(3-2*t))
    assert np.all(mask[4:8,6:10] == 0)
    assert 0 < mask[5,5] < 1
    duplicate = copy.deepcopy(value); duplicate['include'] *= 2
    np.testing.assert_array_equal(subject.rasterize(duplicate),mask)
    value['exclude'] = copy.deepcopy(value['include'])
    assert not subject.rasterize(value).any()
    value['exclude'] = []; value['include'] = [[[1,1],[19,1],[19,2],[1,2]]]
    thin = subject.rasterize(value)
    assert 0 < thin.max() < 1


@pytest.mark.parametrize('change',['nan','bool','outside','line','cross','repeat','overlap','feather','reference'])
def test_bad_geometry_is_rejected(tmp_path,change):
    source = write(tmp_path/'source.fit',np.full((3,12,20),.1))
    value = request(source)
    if change == 'nan': value['include'][0][0][0] = float('nan')
    elif change == 'bool': value['include'][0][0][0] = True
    elif change == 'outside': value['include'][0][0][0] = -1
    elif change == 'line': value['include'] = [[[1,1],[2,2],[3,3]]]
    elif change == 'cross': value['include'] = [[[1,1],[10,8],[2,9],[9,1]]]
    elif change == 'repeat': value['include'][0].append(value['include'][0][0])
    elif change == 'overlap': value['include'] = [[[1,1],[8,1],[4,1],[8,8],[1,8]]]
    elif change == 'feather': value['feather_px'] = 0
    else: value['reference']['width'] += 1
    with pytest.raises(contract.ContractError): subject.validate_request(value,source)


@pytest.mark.parametrize('curve',['ght','invght'])
def test_native_color_spatial_support_and_review(native_regional,case,monkeypatch,curve):
    scene,path,goals = native_regional
    session,parent,regions,_ = scene
    values = np.tile(np.array([.13,.12,.1])[:,None,None],(1,64,64))
    values[:,40:44,24:28] = np.array([.95,.8,.7])[:,None,None]
    values[:,32:36,24:28] = np.array([.3,.01,.01])[:,None,None]
    rebind_fixture_parent(scene,values)
    goals['regions'] = color.record(session,regions); contract.atomic_write_json(path,goals)
    prepared = prepare_mask(session,parent,include=[[[10,10],[54,10],[54,54],[10,54]]],
                            exclude=[[[12,12],[20,12],[20,20],[12,20]]])
    report,binding = subject.load(session,parent,prepared['binding'])
    mask = color.pixels(session/report['mask']['path'])[0]
    np.testing.assert_array_equal(mask,subject.rasterize(core.load_json(session/'reports/079-mask-request.json'))[::-1])
    options = {'curve':'invght','sp':.5,'hp':.7} if curve=='invght' else {}
    solved = color.solve(session,parent,regions,'085-spatial',region_goals=path,strength=.5,
        saturation_protect_start=.7,saturation_protect_end=.95,
        subject_mask=prepared['subject_mask'],high_start=.65,high_end=.9,
        goal='Enhance the selected shell',parameter_reason='Separate equal-color sky by position',**options)
    assert solved['status'] == 'solved',solved
    run,args = execute(scene,solved)
    assert run['status'] == 'success',run
    effective = color.pixels(session/run['color_curve']['layers']['mask'])[0]
    before,after = color.pixels(parent),color.pixels(session/run['primary_output'])
    np.testing.assert_array_equal(after[:,effective == 0],before[:,effective == 0])
    assert np.any(np.abs(after[:,effective > 0]-before[:,effective > 0]) > 1e-6)
    assert not effective[40:44,24:28].any() and not effective[32:36,24:28].any()
    assert run['processing_checks']['color_curve']['subject_mask']['excluded_max_abs'] == 0
    draft = review(session,run)
    assert len(draft['inspected_materials']) <= 32
    assert set(subject.review_paths(session,parent,binding)) <= {r['path'] for r in draft['inspected_materials']}
    missing = copy.deepcopy(draft)
    missing['inspected_materials'] = [r for r in missing['inspected_materials'] if not r['path'].endswith('/mask-review.png')]
    with pytest.raises(contract.ContractError): color.validate_assessment(session,run,missing)
    save_review(session,run,draft)
    assert core.run_script(session,**args)['replayed']
    from . import test_recomposition_proposals as recomposition
    from . import test_deep_sky_siril as support
    monkeypatch.setattr(core.subprocess,'run',case[-1])
    source = session/run['primary_output']
    prepared_stars = recomposition.reduction.prepare(session,source,'089-spatial-stars')
    star_request = copy.deepcopy(case[3]); star_regions = session/star_request['regions']['path']
    document = contract.load_json(star_regions); document['reference']['sha256'] = contract.sha256_file(source)
    contract.atomic_write_json(star_regions,document)
    star_request.update(source=prepared_stars['source'],prepared=prepared_stars['prepared'],regions=color.record(session,star_regions))
    contract.atomic_write_json(case[2],star_request)
    next_case = (session,source,*case[2:])
    combined = recomposition.execute(next_case,recomposition.generate(next_case))
    assert combined['status'] == 'success',combined
    contract.atomic_write_json(session/'reviews/090-recompose.json',recomposition.accepted_draft(session,combined))
    monkeypatch.setattr(core,'_decode_scientific_output',lambda p,**k:{'passed':True,'decoder':{'artifact_fingerprint':contract.fingerprint(p,include_path=False)}})
    script,final = support._delivery_script(session,session/combined['primary_output'])
    delivered = core.run_script(session,protocol='delivery.render',script_value=script,source_value=session/combined['primary_output'],expected_values=[final,final.with_suffix('.fit')],timeout=30)
    assert delivered['status'] == 'success',delivered
    final_review = support._review(session,delivered['id'],final,parent_preview=session/next(r['path'] for r in combined['outputs'] if r['path'].endswith('.jpg')))
    evidence = {p:p.read_bytes() for p in (session/'reports/079-mask').iterdir() if p.is_file()}
    selection = support._selection(session,delivered['id'],final,final_review)
    assert core.finalize_session(session,selection_value=selection,keep_intermediates=True)['status'] == 'success'
    assert all(p.read_bytes() == content for p,content in evidence.items())
    image = session/next(r['path'] for r in report['evidence'] if r['path'].endswith('/overlay.png'))
    image.write_bytes(b'changed')
    with pytest.raises(contract.ContractError): subject.load(session,parent,binding)


@pytest.mark.parametrize('source_case,key',[('single','wavelet'),('single','clahe'),('hdr','wavelet')],indirect=['source_case'])
def test_native_local_spatial_review_and_replay(source_case,key):
    session,parent,*_ = source_case
    prepared = prepare_mask(session,parent,include=[[[190,190],[320,190],[320,320],[190,320]]],
                            exclude=[[[200,200],[215,200],[215,215],[200,215]]],feather_px=8)
    path = request_file(session,parent)
    value = core.load_json(path)
    value.update(schema=local.SPATIAL_REQUEST,subject_mask=prepared['binding'],
                 mask={'high_start':.65,'high_end':.9,'sigma':2},
                 wavelet={'amplitude':.4,'fine_detail':False},clahe={'strength':.1})
    contract.atomic_write_json(path,value)
    solved = local.solve(session,path,'080-spatial',timeout=60)
    hdr = bool(core.load_json(session/'runs/070-stretch.json').get('hdr_transfer'))
    assert solved['candidates'] == (['wavelet'] if hdr else ['wavelet','clahe'])
    assets = local.generate(session,solved['parameters'],key)
    run = run_candidate(session,assets)
    assert run['status'] == 'success',run
    checks = run['processing_checks']['local_contrast']['native']
    assert checks['subject_mask']['excluded_max_abs'] == 0
    assert max(checks['max_errors'].values()) < 2e-6
    before,after = color.pixels(parent),color.pixels(session/run['primary_output'])
    space = subject.weights(session,parent,prepared['binding'])[0]
    files = run['local_contrast_guard']['native']['files']
    base,blurred,mask = [color.pixels(session/files[name])[0] for name in ('mask-base','mask-smoothed','mask')]
    np.testing.assert_allclose(mask,np.minimum(base,blurred)*space,rtol=0,atol=1e-7)
    np.testing.assert_array_equal(before[:,space == 0],after[:,space == 0])
    draft = core.check_review(session,run['id'],None)['review']
    assert len(draft['inspected_materials']) <= 32
    assert set(subject.review_paths(session,parent,prepared['binding'])) <= {r['path'] for r in draft['inspected_materials']}
    accept(session,run['id'])
    assert run_candidate(session,assets)['replayed']


def test_zero_effective_color_skips_without_candidate(native_regional):
    scene,path,_ = native_regional
    session,parent,regions,_ = scene
    prepared = prepare_mask(session,parent,exclude=[[[0,0],[64,0],[64,64],[0,64]]])
    assert prepared['status'] == 'skipped'
    result = color.solve(session,parent,regions,'085-empty',region_goals=path,strength=.5,
        saturation_protect_start=.7,saturation_protect_end=.95,subject_mask=prepared['subject_mask'],
        high_start=.65,high_end=.9,goal='Enhance shell',parameter_reason='Explicit spatial proposal')
    assert result['status'] == 'skipped'
    assert not (Path(result['report']).parent/'trial').exists()


def test_historical_snapshot_replays_without_migration(tmp_path,monkeypatch):
    replay_and_finalize_without_migration(tmp_path/'old',monkeypatch,ROOT/'tests/fixtures/subject-mask-prior.json')


@pytest.mark.parametrize('change',['request','source','mask','report','runtime'])
def test_prepared_mask_drift_is_rejected(native_regional,change):
    scene,*_ = native_regional
    session,parent,*_ = scene
    prepared = prepare_mask(session,parent)
    report = core.load_json(Path(prepared['subject_mask']))
    if change == 'request':
        path = session/report['request']['path']
    elif change == 'source':
        path = parent
    elif change == 'mask':
        path = session/report['mask']['path']
    elif change == 'runtime':
        path = session/'reports/tool-probe.json'
    else:
        path = Path(prepared['subject_mask'])
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises((contract.ContractError,ValueError)):
        subject.load(session,parent,prepared['binding'])


def test_mask_cannot_be_a_scientific_parent(native_regional):
    import deep_sky_siril_processing as processing
    scene,*_ = native_regional
    session,parent,*_ = scene
    prepared = prepare_mask(session,parent)
    report = subject.load(session,parent,prepared['binding'])[0]
    with pytest.raises(contract.ContractError):
        processing.source_state(session,core.load_json(session/'session.json'),session/report['mask']['path'])


def test_zero_local_coverage_skips_trials(source_case):
    session,parent,*_ = source_case
    prepared = prepare_mask(session,parent,exclude=[[[0,0],[512,0],[512,512],[0,512]]])
    path = request_file(session,parent)
    value = core.load_json(path)
    value.update(schema=local.SPATIAL_REQUEST,subject_mask=prepared['binding'],
                 mask={'high_start':.65,'high_end':.9,'sigma':2},
                 wavelet={'amplitude':.4,'fine_detail':False},clahe={'strength':.1})
    contract.atomic_write_json(path,value)
    result = local.solve(session,path,'080-empty',timeout=60)
    assert result['status'] == 'skipped' and result['candidates'] == []
    assert not list((session/'reports/080-empty').glob('*/artifacts/*.fit'))


def test_predecessor_helper_and_implementation_are_exact(tmp_path):
    import deep_sky_siril_session as sessions
    import deep_sky_siril_stretch_v2 as stretch
    before = core.load_json(ROOT/'tests/fixtures/subject-mask-prior.json')
    assert sessions._PRE_SUBJECT_MASK_BINDINGS == before
    contract.atomic_write_json(tmp_path/'session.json',{'execution_policy':before['execution_policy']})
    assert color.helper_fingerprint(tmp_path) == before['helpers']['deep_sky_siril_color.py']
    assert stretch.implementation(tmp_path) == before['implementation']
    assert not sessions.execution_policy_is_current({'execution_policy':before['execution_policy']})
