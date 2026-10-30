"""Shared stellar color evidence, explicit review and native frozen star transfers."""
import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

import deep_sky_star_reduction as stars
import deep_sky_siril_contract as contract
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_core as core
from .test_recomposition_proposals import case, write, execute, accepted_draft
from .test_recomposition_geometry import synthetic
from .test_star_curves import request_for
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration

ROOT=Path(__file__).parents[1]
BINARY=Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
REAL_RUN=subprocess.run
PRIOR=contract.load_json(ROOT/'tests/fixtures/star-color-prior.json')


def diagnostic(inputs, images=None):
    a,c,b,table,regions=inputs
    return stars.stellar_color_diagnostics(images or {'subject':a,'full_contribution':c,'candidate':b},table,regions,
                                           stars.geometry_profiles(*inputs))


def test_background_subtraction_keeps_color_and_signed_flux():
    inputs=synthetic()
    first=diagnostic(inputs)['regions'][0]
    shift=np.array([.12,.03,.2])[:,None,None]
    shifted=(*[v+shift for v in inputs[:3]],*inputs[3:])
    second=diagnostic(shifted)['regions'][0]
    for zone in ('core','halo'):
        one=first['zones'][zone]['full_contribution'];two=second['zones'][zone]['full_contribution']
        assert one['raw']['hue_circular_mean_deg'] != two['raw']['hue_circular_mean_deg']
        assert one['sky_subtracted']['hue_circular_mean_deg'] == pytest.approx(two['sky_subtracted']['hue_circular_mean_deg'],abs=1e-8)
        np.testing.assert_allclose(one['sky_subtracted']['signed_flux'],two['sky_subtracted']['signed_flux'],atol=1e-10)
    assert second['background']['pixels']>3


def test_neutral_negative_and_cancelling_hue_is_not_filled():
    assert stars._color_sample(np.ones((3,50)))['hue_circular_mean_deg'] is None
    assert stars._color_sample(-np.ones((3,50)))['hue_circular_mean_deg'] is None
    result=stars._color_sample(np.array([[1,0],[0,1],[0,1.]]))
    assert result['hue_circular_mean_deg'] is None
    neutral=np.ones((3,1000))*.1
    colored=np.array([[1.,1.],[0,.01],[.01,0]])
    hue=stars._color_sample(np.concatenate([neutral,colored],axis=1))['hue_circular_mean_deg']
    assert min(hue,360-hue)<1e-8
    assert stars._color_sample(np.array([[-.1],[.1],[.2]]))['signed_flux'][0]==-.1


@pytest.mark.parametrize('issue',['saturated','crowded','empty','edge'])
def test_raw_endpoints_survive_unmeasurable_geometry(issue):
    a,c,b,table,regions=synthetic()
    if issue=='saturated':table['rows'][0]['has_saturated']=True
    elif issue=='crowded':table['rows'].append({**table['rows'][0],'line_number':9,'xpos':72.})
    elif issue=='empty':table['rows']=[]
    else:regions[0]['rect']=[0,0,144,128]
    b[:,60,70]=1.02
    row=diagnostic((a,c,b,table,regions))['regions'][0]
    assert row['status']=='not_measurable' and row['reasons'] and not row['zones']
    crossing=row['raw']['candidate']['new_endpoints']['full_contribution'][0]['highlight']
    assert crossing['count']==1 and crossing['positions']==[[70,67]]


def test_mono_color_is_not_applicable():
    a,c,b,table,regions=synthetic()
    result=diagnostic((a[:1],c[:1],b[:1],table,regions))
    assert result['status']=='not_applicable'
    assert result['regions'][0]['raw']['candidate']['pixels']>0


@pytest.mark.parametrize('facet',stars.STELLAR_CHECKS)
@pytest.mark.parametrize('verdict',['degraded','uncertain','missing'])
def test_each_stellar_check_is_required_for_accept(case,facet,verdict):
    session,*_=case
    assets=stars.generate_recompose_assets(session,request=case[2],run_id='090-color')
    run=execute(case,assets);assert run['status']=='success',run
    draft=accepted_draft(session,run)
    row=next(r for r in draft['recomposition_assessment']['regions'] if 'stellar_checks' in r)
    if verdict=='missing':row['stellar_checks'].pop(facet)
    else:row['stellar_checks'][facet]['assessment']=verdict
    with pytest.raises(contract.ContractError):stars.validate_assessment(session,run,draft)


def test_analyze_final_share_color_and_drift_is_rejected(case):
    session,parent,path,proposal,_=case
    req=request_for(case)
    prepared=stars.prepare(session,parent,'089-color',star_request=req)
    proposal={**proposal,'prepared':prepared['prepared']};contract.atomic_write_json(path,proposal)
    payload=contract.load_json(session/'session.json')
    before,*_=stars.analyze(session,payload,prepared['prepared'],proposal['regions'])
    assert 'original_baseline' in before['stellar_color']['regions'][0]['raw']
    assets=stars.generate_recompose_assets(session,request=path,run_id='090-color')
    run=execute(case,assets);assert run['status']=='success',run
    report=session/run['processing_checks']['recomposition']['report']['path']
    final=contract.load_json(report)
    one=before['stellar_color']['regions'][0]['raw']['full_contribution']
    two=final['stellar_color']['regions'][0]['raw']['full_contribution']
    assert {k:v for k,v in one.items() if k!='new_endpoints'}=={k:v for k,v in two.items() if k!='new_endpoints'}
    draft=accepted_draft(session,run);stars.validate_assessment(session,run,draft)
    final['stellar_color']['regions'][0]['raw']['candidate']['rgb_median'][0]+=.01
    contract.atomic_write_json(report,final)
    with pytest.raises(contract.ContractError):processing.verify_recomposition_receipt(session,payload,run)


def test_outgoing_policy_and_immutable_delivery(tmp_path,monkeypatch):
    assert sessions._PRE_STAR_COLOR_BINDINGS==PRIOR
    # prior=star-color-prior.json: recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    replay_and_finalize_without_migration(tmp_path,monkeypatch,ROOT/'tests/fixtures/star-color-prior.json')


@pytest.mark.parametrize('curve',[
    [{'command':'mtf','parameters':['0','.6','1']}],
    [{'command':'asinh','parameters':['-human','2','0','-clipmode=rgbblend']}],
    [{'command':'ght','parameters':['-D=.3','-B=1','-LP=0','-SP=0','-HP=1','-human','-clipmode=rgbblend']}],
])
def test_native_prepare_formal_and_replay(case,monkeypatch,curve,contour=None):
    if not BINARY.exists():pytest.skip('Native Siril unavailable')
    session,parent,path,proposal,_=case
    monkeypatch.setattr(subprocess,'run',REAL_RUN)
    probe_path=session/'reports/tool-probe.json';probe=contract.load_json(probe_path)
    probe['tools']['siril_cli'].update(path=str(BINARY),version='1.4.4',compatible=True,fingerprint=contract.fingerprint(BINARY))
    contract.atomic_write_json(probe_path,probe)
    monkeypatch.setattr(core.tooling,'probe_tools',lambda *,offline=False:probe)
    payload=contract.load_json(session/'session.json');payload['tool_probe']['sha256']=contract.sha256_file(probe_path)
    payload.pop('session_sha256');payload['session_sha256']=contract.stable_hash(payload);contract.atomic_write_json(session/'session.json',payload)
    baseline=stars.prepare(session,parent,'089-native-base')
    prepared = baseline
    if curve is not None:
        req=request_for((session,parent,path,{**proposal,'prepared':baseline['prepared']},None),transfer_chain=curve)
        prepared=stars.prepare(session,parent,'089-native-star',star_request=req)
    contract.atomic_write_json(path,{**proposal,'prepared':prepared['prepared'],
        **({'contour':contour} if contour is not None else {})})
    assets=stars.generate_recompose_assets(session,request=path,run_id='090-native')
    run=execute(case,assets);assert run['status']=='success',run
    assert processing.verify_recomposition_receipt(session,payload,run)


@pytest.mark.parametrize('command',[
    'mtf 0 .6 1', 'asinh -human 2 0 -clipmode=rgbblend',
    'ght -D=.3 -B=1 -LP=0 -SP=0 -HP=1 -human -clipmode=rgbblend',
    'modasinh -D=.4 -LP=0 -SP=0 -HP=.7 -human -clipmode=rgbblend'])
def test_native_color_benefit_and_final_addition_crossing(tmp_path,command):
    if not BINARY.exists():pytest.skip('Native Siril unavailable')
    from deep_sky_siril_chroma import run_native
    y,x=np.indices((64,64));a=np.full((3,64,64),.03)
    residual=np.array([1,.6,.3])[:,None,None]*.6*np.exp(-((x-18)**2+(y-18)**2)/8)
    residual+=np.array([.3,.6,1])[:,None,None]*.3*np.exp(-((x-46)**2+(y-46)**2)/8)
    full=write(tmp_path/'full.fit',a+residual);base=write(tmp_path/'base.fit',a)
    bright=write(tmp_path/'bright.fit',np.full_like(a,.85))
    q=lambda p:json.dumps(str(p));v=lambda p:'$'+str(p.with_suffix(''))+'$'
    rows=['requires 1.4.4 1.5.0','set32bits'];layers={}
    for route,curve in (('body','mtf 0 .2 .25'),('star',command)):
        for name,source in (('full',full),('base',base)):
            output=tmp_path/(route+'-'+name+'.fit');layers[route,name]=output
            rows+=['load '+q(source),curve,'save '+q(output),'close']
        output=tmp_path/(route+'-residual.fit');layers[route,'residual']=output
        rows+=['pm '+q(v(layers[route,'full'])+' - '+v(layers[route,'base'])), 'save '+q(output),'close']
    final=tmp_path/'final.fit'
    rows+=['pm '+q(v(bright)+' + 1 * '+v(layers['star','residual']))+' -nosum','save '+q(final),'close']
    work=tmp_path/'reports/native';work.mkdir(parents=True)
    run_native(tmp_path,work,'native-color','\n'.join(rows)+'\n',{'path':str(BINARY)})
    pixels=lambda p:stars.read_scientific_pixels(p)[0]
    old,new=pixels(layers['body','full']),pixels(layers['star','full'])
    assert np.count_nonzero(old>=1)>np.count_nonzero(new>=1)==0
    for cy,cx in ((18,18),(46,46)):
        truth=residual[:,cy,cx];truth/=truth.sum()
        errors=[]
        for route in ('body','star'):
            net=pixels(layers[route,'residual'])[:,cy,cx];net=net/net.sum()
            errors.append(np.abs(net-truth).mean())
        assert errors[1]<=errors[0]
    candidate=pixels(final)
    assert np.any((candidate>=1)&(new<1))


def test_outgoing_prepared_and_reports_replay_without_new_fields(case):
    session,parent,path,proposal,_=case
    run=execute(case,stars.generate_recompose_assets(session,request=path,run_id='090-old'))
    assert run['status']=='success'
    payload=contract.load_json(session/'session.json')
    payload['execution_policy']=PRIOR['execution_policy']
    payload.pop('session_sha256');payload['session_sha256']=contract.stable_hash(payload)
    contract.atomic_write_json(session/'session.json',payload)
    prepared_path=session/proposal['prepared']['path'];prepared=contract.load_json(prepared_path)
    prepared['bindings'].update(helper=PRIOR['helpers']['deep_sky_star_reduction.py'],implementation=PRIOR['implementation'])
    contract.atomic_write_json(prepared_path,prepared)
    proposal['prepared']=stars.record(session,prepared_path);contract.atomic_write_json(path,proposal)
    _,binding,_,_=stars.request_binding(session,payload,path,run['id'],verify_parent=False)
    run['recomposition_proposal']=binding
    # Construct the outgoing v1 format, then require the normal replay path to preserve it.
    for key in ('report','panel'):
        (session/run['processing_checks']['recomposition'][key]['path']).unlink()
    run['processing_checks']['recomposition']=stars.measure(session,parent,session/run['primary_output'],run,write=True)
    report=contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    assert 'stellar_color' not in report and any('color' in r for r in report['geometry']['regions'])
    paths=[session/'session.json',prepared_path,*[session/run['processing_checks']['recomposition'][k]['path'] for k in ('report','panel')]]
    frozen={str(p):p.read_bytes() for p in paths}
    assert stars.measure(session,parent,session/run['primary_output'],run)==run['processing_checks']['recomposition']
    draft=stars.assessment_skeleton(session,run)
    assert all('stellar_checks' not in r for r in draft['regions'])
    assert frozen=={str(p):p.read_bytes() for p in paths}


@pytest.mark.parametrize('case',['mono'],indirect=True)
def test_monochrome_recomposition_keeps_existing_review(case):
    session,parent,path,_,_=case
    run=execute(case,stars.generate_recompose_assets(session,request=path,run_id='090-mono'))
    assert run['status']=='success',run
    report=contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    assert report['stellar_color']['status']=='not_applicable'
    draft=accepted_draft(session,run)
    assert all('stellar_checks' not in r for r in draft['recomposition_assessment']['regions'])
    review=session/'reviews/090-mono.json';contract.atomic_write_json(review,draft)
    assert core.check_review(session,run['id'],review)['status']=='review_valid'
    assert processing.verify_recomposition_receipt(session,contract.load_json(session/'session.json'),run)
