"""Native chroma arithmetic, color-boundary protection and immutable pipeline evidence."""
import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

import deep_sky_siril_chroma as chroma
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
from .test_recomposition_proposals import case, write
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration

ROOT = Path(__file__).parents[1]
BINARY = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
REAL_RUN = subprocess.run
P = {'schema':chroma.SCHEMA,'source':'artifacts/parent.fit','regions':'reports/regions.json',
     'goal':'Remove observed red-green and blue-purple blotches before saturation',
     'parameter_reason':'One bounded joint-color kernel preserves the observed color boundary',
     'strength':.5,'diameter':9,'sigma_spatial':2,'sigma_range':.04,
     'color_probe':{'D':.42,'saturation_protect_start':.65,'saturation_protect_end':.9}}


def scene_values():
    rng = np.random.default_rng(431)
    y,x = np.indices((64,64))
    brightness = .15+.015*np.sin(x*1.3)*np.cos(y*.9)
    truth = np.repeat(brightness[None],3,axis=0)
    truth[0,:,32:] += .12
    truth[1,:,32:] -= .12*.2126/.7152
    noise = rng.normal(0,.009,truth.shape)
    noise -= np.einsum('c,cyx->yx',chroma.WEIGHTS,noise)[None]
    return truth, truth+noise


def bilateral_reference(a, p):
    image = a.transpose(1,2,0)
    radius = p['diameter']//2
    padded = np.pad(image,((radius,radius),(radius,radius),(0,0)),mode='reflect')
    total = np.zeros_like(image); weights = np.zeros(image.shape[:2])
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            if dx*dx+dy*dy > radius*radius: continue
            neighbor = padded[radius+dy:radius+dy+64,radius+dx:radius+dx+64]
            distance = np.abs(neighbor-image).sum(axis=2)
            weight = np.exp(-.5*(distance/p['sigma_range'])**2-.5*(dx*dx+dy*dy)/p['sigma_spatial']**2)
            total += neighbor*weight[:,:,None]; weights += weight
    return (total/weights[:,:,None]).transpose(2,0,1)


@pytest.fixture
def native_scene(tmp_path):
    if not BINARY.exists(): pytest.skip('Siril 1.4.4 unavailable')
    (tmp_path/'artifacts').mkdir(); trial=tmp_path/'reports/087-trial'
    (trial/'artifacts').mkdir(parents=True); (trial/'previews').mkdir()
    truth, values = scene_values()
    values[:,0,0]=[-.01,.1,.2];values[:,0,1]=[0,.2,.3];values[:,0,2]=[1.1,.2,.3]
    source=write(tmp_path/'artifacts/parent.fit',values)
    text,layers,views=chroma.script_text(trial,source,'087-trial',P)
    chroma.run_native(tmp_path,trial,'probe',text,{'path':str(BINARY)})
    return tmp_path,source,layers,views,truth


def test_native_projection_kernel_units_protected_pixels_and_noise_benefit(native_scene):
    session,source,layers,_,truth=native_scene
    result=chroma.measure_recipe(source,layers,P)
    assert max(result['max_abs_errors'].values()) < 1e-6
    a,d,c=map(chroma.pixels,(source,layers['filtered'],layers['candidate']))
    np.testing.assert_allclose(d,bilateral_reference(a,P),atol=2e-7,rtol=0)
    assert result['protected_pixel_count']==3 and result['changed']
    np.testing.assert_array_equal(a[:,:1,:3],c[:,:1,:3])
    region=(slice(None),slice(12,52),slice(10,24))
    assert np.mean((c[region]-truth[region])**2) < .7*np.mean((a[region]-truth[region])**2)
    # A same-brightness, different-color boundary must survive the joint RGB kernel.
    assert abs(np.mean(c[0,12:52,32:35]-truth[0,12:52,32:35])) < .003


@pytest.mark.parametrize('key,value',[('strength',True),('diameter',4),('diameter',21),
    ('sigma_range',0),('sigma_spatial',33),('sigma_range',float('nan'))])
def test_invalid_parameters(key,value):
    with pytest.raises(contract.ContractError): chroma.validate_request({**P,key:value})


def test_common_gamut_weight_preserves_luma_and_existing_endpoints():
    a=np.array([[.01,.2,.5],[.03,.1,.6],[.95,.3,.2]])[:,:,None]
    d=np.array([[.9,.1,.1],[.2,.3,.7],[.1,.8,.2]])[:,:,None]
    c,delta,weight=chroma.arithmetic(a,d,P)
    assert weight[0,0] < P['strength']
    assert np.all(c>=chroma.EPS) and np.all(c<=1-chroma.EPS)
    np.testing.assert_allclose(np.einsum('c,cyx->yx',chroma.WEIGHTS,c-a),0,atol=1e-16)
    np.testing.assert_allclose(c,a+weight*delta)


@pytest.fixture
def pipeline(case,monkeypatch):
    session,parent,*_=case
    monkeypatch.setattr(subprocess,'run',REAL_RUN)
    probe_path=session/'reports/tool-probe.json';probe=contract.load_json(probe_path)
    probe['tools']['siril_cli'].update(path=str(BINARY),version='1.4.4',compatible=True,
                                     fingerprint=contract.fingerprint(BINARY))
    contract.atomic_write_json(probe_path,probe)
    monkeypatch.setattr(core.tooling,'probe_tools',lambda *,offline=False:probe)
    payload=contract.load_json(session/'session.json')
    payload['tool_probe']['sha256']=contract.sha256_file(probe_path)
    payload.pop('session_sha256')
    payload['session_sha256']=contract.stable_hash(payload)
    contract.atomic_write_json(session/'session.json',payload)
    regions=session/'reports/chroma-regions.json'
    contract.atomic_write_json(regions,{'schema':'starun-siril.regions.v1',
        'reference':{'sha256':contract.sha256_file(parent),'width':64,'height':64},
        'regions':[{'id':'faint-color-boundary','role':'faint','rect':[20,20,44,44]}]})
    p={**P,'source':str(parent.relative_to(session)),'regions':str(regions.relative_to(session))}
    request=session/'reports/chroma-request.json';contract.atomic_write_json(request,p)
    return session,parent,request


def trial(pipeline):
    session,parent,request=pipeline
    solved=chroma.solve(session,request,'087-trial')
    generated=chroma.generate(session,solved['parameters'],'088-chroma')
    return solved,generated


def test_native_full_pipeline_review_replay_and_drift(pipeline):
    session,parent,_=pipeline
    solved,generated=trial(pipeline)
    receipt=core.run_script(session,protocol=chroma.PROTOCOL,script_value=generated['script'],
        source_value=parent,primary_output_value=generated['primary_output'],expected_values=generated['expected'],timeout=60)
    assert receipt['status']=='success', receipt
    assert receipt['processing_checks']['chroma_denoise']['new_endpoints']==0
    draft=core.check_review(session,receipt['id'],None)['review']
    assert 'denoise_assessment' in draft
    draft.update(verdict='accept',notes='Inspected native paired color and noise evidence',gates={k:'pass' for k in draft['gates']})
    draft['denoise_assessment'].update(noise_benefit='observed',noise_observation='Blotches decrease after the same color probe',adoption_reason='Color boundaries and faint structures are retained')
    for row in draft['denoise_assessment']['structures']:
        row.update(retention='retained',observation='Inspected original-resolution color boundary and fine texture')
    chroma.validate_assessment(session,receipt,draft)
    bad=copy.deepcopy(draft);bad['denoise_assessment']['structures'][0]['retention']='loss'
    with pytest.raises(contract.ContractError): chroma.validate_assessment(session,receipt,bad)
    review=session/'reviews/088-chroma.json';contract.atomic_write_json(review,draft)
    core.check_review(session,receipt['id'],str(review))
    previews=core._delivery_parent_preview_paths(session,{'id':'100-delivery','source_path':receipt['primary_output']})
    assert previews=={session/receipt['chroma_denoise']['previews']['candidate']}
    assert receipt['branch_binding']['separation_run']=='060-stars'
    payload=contract.load_json(session/'session.json')
    assert sessions._receipt_bindings_unchanged(session,payload,receipt)
    binding=receipt['chroma_denoise']['parameters']
    report=contract.load_json(session/binding['path'])
    image=session/report['trial_layers']['candidate'];image.write_bytes(image.read_bytes()+b'changed')
    with pytest.raises(contract.ContractError): chroma.load_parameters(session,binding)


def test_request_roi_and_untrusted_parent_rejected(pipeline):
    session,parent,request=pipeline
    p=contract.load_json(request);p['source']='reports/untrusted.fit'
    write(session/'reports/untrusted.fit',chroma.pixels(parent))
    contract.atomic_write_json(request,p)
    with pytest.raises(contract.ContractError): chroma.solve(session,request,'087-trial')


def test_outgoing_exact_history_and_replay(tmp_path,monkeypatch):
    prior=contract.load_json(ROOT/'tests/fixtures/chroma-denoise-prior.json')
    assert sessions._PRE_CHROMA_BINDINGS==prior
    # prior=chroma-denoise-prior.json: chroma_denoise, recomposition_review
    assert_contract_domains(prior['execution_policy'])
    assert sessions.known_execution_policy(prior['execution_policy'])
    replay_and_finalize_without_migration(tmp_path,monkeypatch,ROOT/'tests/fixtures/chroma-denoise-prior.json')


def test_native_weak_red_blue_filaments_and_brightness_boundary(tmp_path):
    if not BINARY.exists(): pytest.skip('Siril unavailable')
    trial=tmp_path/'reports/087-edges'
    (trial/'artifacts').mkdir(parents=True);(trial/'previews').mkdir()
    a=np.full((3,64,64),.15);a[:,:,32:]=.3
    for column,color in ((18,np.array([.012,-.012*.2126/.7152,0])),
                         (45,np.array([0,-.012*.0722/.7152,.012]))):
        a[:,12:52,column]+=color[:,None]
    source=write(tmp_path/'artifacts/source.fit',a)
    from astropy.io import fits
    with fits.open(source,mode='update') as hdus:
        for key,value in {'EXPTIME':120.,'LIVETIME':3600.,'STACKCNT':30,'CTYPE1':'RA---TAN',
            'CTYPE2':'DEC--TAN','CRPIX1':32.,'CRPIX2':32.,'CRVAL1':12.,'CRVAL2':25.,
            'CD1_1':-.001,'CD1_2':0.,'CD2_1':0.,'CD2_2':.001,'CUNIT1':'deg','CUNIT2':'deg'}.items():hdus[0].header[key]=value
    params={**P,'sigma_range':.002}
    value,layers,_=chroma.script_text(trial,source,'087-edges',params)
    chroma.run_native(tmp_path,trial,'edges',value,{'path':str(BINARY)})
    chroma.measure_recipe(source,layers,params)
    np.testing.assert_allclose(chroma.pixels(layers['candidate']),chroma.pixels(source),atol=1e-6,rtol=0)


@pytest.mark.parametrize('domain,starless,accumulated',[('linear',True,False),('nonlinear',False,False),('nonlinear',True,True)])
def test_source_domain_branch_and_accumulation_are_gated(pipeline,monkeypatch,domain,starless,accumulated):
    session,parent,_=pipeline
    monkeypatch.setattr(processing,'source_state',lambda *args:{'domain':domain,'separation_run':'060-stars' if starless else None,
        'run':{'id':'080-subject'},'lineage':[{'chroma_denoise':{ 'policy':chroma.POLICY}}] if accumulated else []})
    with pytest.raises(contract.ContractError):
        chroma.source_context(session,contract.load_json(session/'session.json'),parent)


def test_roi_fingerprint_drift_is_rejected(pipeline):
    session,parent,request=pipeline
    p=contract.load_json(request);regions=session/p['regions'];doc=contract.load_json(regions)
    doc['reference']['sha256']='0'*64;contract.atomic_write_json(regions,doc)
    with pytest.raises((contract.ContractError,ValueError)):chroma.solve(session,request,'087-trial')


def test_nonfinite_weight_is_rejected(native_scene):
    _,source,layers,_,_=native_scene
    weight=processing.read_scientific_pixels(layers['weight'])[0].copy()
    weight[0,5,5]=float('nan');write(layers['weight'],weight)
    with pytest.raises(contract.ContractError,match='weight'):
        chroma.measure_recipe(source,layers,P)


def test_undeclared_epf_cannot_bypass_whole_image_denoise(pipeline):
    from . import test_deep_sky_siril as support
    session,parent,_=pipeline
    script=session/'scripts/088-undeclared.ssf';primary=session/'artifacts/088-undeclared.fit'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{parent}"\nepf -d=9 -si=10 -ss=2 -mod=1\nsave "{primary}" -chksum\nclose\n')
    support._write_ssf_provenance(session,script,chroma.PROTOCOL)
    with pytest.raises(contract.ContractError,match='explicit variant'):
        core.run_script(session,protocol=chroma.PROTOCOL,script_value=script,source_value=parent,
            primary_output_value=primary,expected_values=[primary],timeout=60)


def test_original_whole_image_contract_is_retained(pipeline):
    session,parent,_=pipeline
    payload=contract.load_json(session/'session.json')
    primary=session/'artifacts/088-whole.fit'
    text=f'requires 1.4.4 1.5.0\nset32bits\nload "{parent}"\ndenoise -nocosmetic -mod=0.20\nstat main\nsave "{primary}" -chksum\nclose\n'
    metadata=processing.prepare_processing(session,payload,chroma.PROTOCOL,parent,primary,text,{},
        separation_run=None,stretch_run=None,load_run=lambda name:core._load_run(session,name),
        review_run=lambda name:core._accepted_review(session,name),run_id='088-whole')
    assert 'chroma_denoise' not in metadata
    processing.validate_parameters(['denoise','-nocosmetic','-mod=0.20'],chroma.PROTOCOL)
