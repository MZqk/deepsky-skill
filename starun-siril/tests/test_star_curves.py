"""Independent star transfer preserves frozen source pairs, signed closure and fixed catalogs."""
import copy
from pathlib import Path
import numpy as np
import pytest

import deep_sky_star_reduction as reduction
import deep_sky_siril_contract as contract
import deep_sky_siril_processing as processing
from .test_recomposition_proposals import case,execute


def request_for(case, **changes):
    session,parent,_,proposal,_=case
    path=session/'reports/star-curve.json'
    request={'schema':'starun-siril.star-curve-request.v1','source':proposal['source'],
             'baseline':proposal['prepared'],'regions':proposal['regions'],
             'transfer_chain':[{'command':'mtf','parameters':['0','.6','1']}],
             'goal':'Protect bright stellar cores while retaining star color',
             'parameter_reason':'One explicit curve after observing the original prepared baseline'}
    contract.atomic_write_json(path,{**request,**changes})
    return path


@pytest.mark.parametrize('case',['single','hdr','hoo'],indirect=True)
def test_star_curve_prepare_formal_and_replay_keep_body_and_catalog(case):
    session,parent,path,proposal,native=case
    old=contract.load_json(session/proposal['prepared']['path'])
    request=request_for(case)
    prepared=reduction.prepare(session,parent,'089-independent',star_request=request)
    actual=contract.load_json(session/prepared['prepared']['path'])
    assert actual['geometry_catalog']==old['geometry_catalog']
    assert len(native.calls)==2
    script=Path(native.calls[-1]).read_text()
    assert 'findstar' not in script and script.count('mtf 0 .6 1')==2
    assert actual['bindings']==old['bindings']
    proposal={**proposal,'prepared':prepared['prepared']}
    contract.atomic_write_json(path,proposal)
    assets=reduction.generate_recompose_assets(session,request=path,run_id='090-independent')
    receipt=execute(case,assets)
    assert receipt['status']=='success',receipt
    assert receipt['transfer_chain']==actual['star_curve']['transfer_chain']
    assert processing.verify_recomposition_receipt(session,contract.load_json(session/'session.json'),receipt)
    report=contract.load_json(session/receipt['processing_checks']['recomposition']['report']['path'])
    assert report['star_curve']['baseline']==actual['star_curve']['baseline']
    assert 'original_baseline' in report and 'baseline_geometry' in report
    assert report['stellar_color']['regions']
    assert 'original_baseline' in report['stellar_color']['regions'][0]['raw']
    for role in ('full_baseline','original_starless','display_stars'):
        np.testing.assert_allclose(reduction.read_scientific_pixels(session/receipt['branch_binding'][role])[0],
                                   reduction.read_scientific_pixels(session/actual['layers'][role]['path'])[0],atol=1e-5)


@pytest.mark.parametrize('chain',[
    [{'command':'ght','parameters':['-D=.5','-sat','-clipmode=rgbblend']}],
    [{'command':'asinh','parameters':['5','0']}],
    [{'command':'autostretch','parameters':[]}],
    [{'command':'mtf','parameters':['0','nan','1']}],
])
def test_invalid_or_residual_color_curve_rejected_before_native(case,chain):
    session,parent,_,_,native=case
    request=request_for(case,transfer_chain=chain)
    with pytest.raises(contract.ContractError):reduction.prepare(session,parent,'089-invalid',star_request=request)
    assert len(native.calls)==1
    assert not (session/'reports/089-invalid').exists()


def test_star_curve_roi_drift_and_nested_baseline_are_rejected(case):
    session,parent,_,proposal,native=case
    request=request_for(case)
    first=reduction.prepare(session,parent,'089-independent',star_request=request)
    nested=request_for(case,baseline=first['prepared'])
    with pytest.raises(contract.ContractError,match='original body-curve'):reduction.prepare(session,parent,'089-nested',star_request=nested)
    request=request_for(case)
    regions=contract.load_json(session/proposal['regions']['path'])
    regions['reference']['sha256']='0'*64
    contract.atomic_write_json(session/proposal['regions']['path'],regions)
    with pytest.raises(contract.ContractError):reduction.prepare(session,parent,'089-drift',star_request=request)
