"""Morphology declarations bind real material without classifying pixels."""
import copy
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageCms
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_morphology as morphology
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_preflight as preflight
from ._stretch_factory import with_morphology, with_constraint_review, source_evidence
from .test_stretch_routing_v29 import routing_case, goals_for, prepare, failed_profile
from .test_deep_sky_siril_stretch import context, write
from ._policy_pins import replay_and_finalize_without_migration
from . import test_deep_sky_siril as support

ROOT=Path(__file__).parents[1]


def design(case,profile='high_dynamic_range'):
    session,_,_,_,request,_=case
    return with_morphology(session,request['source'],request['regions'],with_constraint_review(goals_for(case,profile)))


def inspect(case,goals,run_id='071-morph-check'):
    session,_,_,_,request,_=case
    path=session/'reports'/(run_id+'-goals.json');contract.atomic_write_json(path,goals)
    return preflight.preflight_requests(session,request['source']['path'],request['regions']['path'],path,run_id, stepwise=False),path


def test_faint_core_overlap_warns_without_blocking_or_claiming_attempt(routing_case):
    session, _, payload, _, request, _ = routing_case
    path = session/request['regions']['path']
    data = contract.load_json(path)
    next(r for r in data['regions'] if r['id'] == 'core')['rect'] = [45, 45, 60, 60]
    contract.atomic_write_json(path, data)
    request['regions']['sha256'] = contract.sha256_file(path)
    result, _ = inspect(routing_case, design(routing_case))
    overlaps = [row for row in result['warnings'] if row['code'] == 'faint_core_overlap']
    assert result['status'] == 'ready'
    assert overlaps == [{'code': 'faint_core_overlap', 'faint': 'faint', 'core': 'core',
                         'intersection': [45, 45, 50, 50], 'faint_fraction': 25/324}]
    assert not list(session.glob('reports/*/scientific-attempt.json'))


def test_disjoint_regions_have_no_overlap_warning(routing_case):
    result, _ = inspect(routing_case, design(routing_case))
    assert not [row for row in result['warnings'] if row['code'] == 'faint_core_overlap']


@pytest.mark.parametrize('profile',[v[0] for v in morphology.GOALS.values()])
def test_five_declared_goals_keep_the_canonical_chains(routing_case,profile):
    goals=design(routing_case,profile)
    result,_=inspect(routing_case,goals)
    assert result['status']=='ready',result['issues']
    assert result['morphology']['supporting_primary_rois']
    prepared=prepare(routing_case,goals)
    plan=contract.load_json(Path(prepared['strategy']))
    request=contract.load_json(Path(prepared['candidates']['profile']['request']))
    assert [s['method'] for s in request['operators']]==[s['method'] for s in contract.load_json(ROOT/'references/stretch-profiles.json')[profile]['preferred_chain']]
    assert plan['schema']==routes.BOUND_PLAN and request['schema'].endswith('.v4')
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


@pytest.mark.parametrize('fault',['missing','duplicate','unknown','background','stars','unresolved','companion-core','stellar-residual','wrong-profile'])
def test_declaration_inconsistencies_do_not_claim_a_trial(routing_case,fault):
    goals=design(routing_case);decl=goals['morphology']
    if fault=='missing':decl['regions'].pop()
    elif fault=='duplicate':decl['regions'].append(copy.deepcopy(decl['regions'][0]))
    elif fault=='unknown':decl['regions'][0]['region']='not-a-roi'
    elif fault=='wrong-profile':decl['processing_goal']='galaxy_structure'
    else:
        target=next(r for r in decl['regions'] if r['region']==('sky' if fault=='background' else 'stars' if fault=='stars' else 'core'))
        if fault=='background':target['relation']='primary'
        elif fault=='stars':target['features']=['galaxy_disk']
        elif fault=='unresolved':target['features']=['unresolved']
        elif fault=='companion-core':target['relation']='companion'
        else:target['features']=['stellar_residual']
    result,path=inspect(routing_case,goals)
    assert result['status']=='needs_revision'
    with pytest.raises(contract.ContractError):
        routes.init_requests(routing_case[0],routing_case[4]['source']['path'],routing_case[4]['regions']['path'],path,'071-morph-check', stepwise=False)
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))
    assert not (routing_case[0]/'reports/071-morph-check').exists()


def test_name_does_not_choose_profile_or_hide_primary_goal(routing_case):
    goals=design(routing_case);goals['morphology']['primary_subject']='M81-like synthetic object'
    assert inspect(routing_case,goals)[0]['status']=='ready'
    goals['morphology']['processing_goal']='galaxy_structure'
    result,_=inspect(routing_case,goals)
    assert any(r['code']=='morphology_profile_mismatch' for r in result['issues'])
    galaxy=design(routing_case,'galaxy_halo');galaxy['morphology']['primary_subject']='M81-like synthetic object'
    assert inspect(routing_case,galaxy)[0]['status']=='ready'


def test_unresolved_companion_is_preserved_without_becoming_profile_proof(routing_case):
    goals=design(routing_case,'emission_nebula')
    core=next(r for r in goals['morphology']['regions'] if r['region']=='core')
    core.update(relation='companion',features=['unresolved'],observation='The companion cannot be identified at this display level; retain its existing protection.')
    result,_=inspect(routing_case,goals)
    assert result['status']=='ready'
    assert 'core' not in result['morphology']['supporting_primary_rois']


@pytest.mark.parametrize('target',['context','panel','preview','regions','parent-receipt','parent-review'])
def test_changed_context_material_is_rejected_at_load_and_native_claim(routing_case,target):
    session,_,payload,_,_,_=routing_case
    goals=design(routing_case);prepared=prepare(routing_case,goals)
    item=prepared['candidates']['profile'];request=contract.load_json(Path(item['request']))
    binding=goals['morphology']['context'];ctx=contract.load_json(session/binding['path'])
    path=session/(binding['path'] if target=='context' else ctx['parent'][target.split('-')[1]]['path'] if target.startswith('parent-') else ctx[target]['path'])
    path.write_bytes(path.read_bytes()+b'drift')
    with pytest.raises((contract.ContractError,OSError,ValueError)):
        stretch.read_request(session,payload,item['request'])
    with pytest.raises((contract.ContractError,OSError,ValueError)):
        routes.claim_attempt(session,payload,item['run_id'],request)
    assert not list(session.glob('reports/*/scientific-attempt.json'))


def test_verification_uses_pinned_parent_without_recursive_source_walk(routing_case,monkeypatch):
    goals=design(routing_case)
    monkeypatch.setattr(stretch,'accepted_parent',lambda *args:pytest.fail('Context replay must not recursively enumerate successful descendants'))
    morphology.verify_context(routing_case[0],routing_case[2],goals['morphology']['context'])


def test_preparation_precedes_classification_and_preserves_coordinate_orientation(routing_case):
    session,source,payload,_,request,_=routing_case
    data=np.full((96,96),.02,dtype=np.float32);data[16:29,7:18]=.4
    write(source,data)
    payload['input'].update(contract.fingerprint(source,include_path=False));contract.atomic_write_json(session/'session.json',payload)
    source_binding={'path':'@input','sha256':contract.sha256_file(source)}
    preview=source_evidence(session,source_binding)[1]
    regions=session/'reports/independent-regions.json'
    contract.atomic_write_json(regions,{'schema':'starun-siril.regions.v1',
        'reference':{'sha256':source_binding['sha256'],'width':96,'height':96},
        'regions':[{'id':'asymmetric','role':'faint','rect':[7,67,18,80]}]})
    prepared=morphology.prepare_context(session,'@input',regions,preview['path'],'069-independent')
    ctx=contract.load_json(session/prepared['context']['path'])
    with Image.open(session/preview['path']) as parent,Image.open(session/prepared['panel']['path']) as panel:
        assert panel.size==parent.size
        assert panel.info.get('icc_profile')==parent.info.get('icc_profile')
        assert panel.getpixel((7,67))==(102,217,195)
        assert panel.getpixel((75,75))==parent.getpixel((75,75))
    measured=stretch.statistics(stretch.pixels(source),[{'id':'asymmetric','rect':[7,67,18,80]}])
    assert measured['asymmetric']['median']==pytest.approx(.4)
    assert ctx['geometry']['coordinates']=='preview_top_left'
    assert not list((session/'reports').glob('.stretch-profile-*'))
    assert not list(session.glob('reports/*/scientific-attempt.json'))
    assert not list(session.glob('reports/*/strategy.json'))
    with pytest.raises(contract.ContractError,match='already exists'):
        morphology.prepare_context(session,'@input',regions,preview['path'],'069-independent')


@pytest.mark.parametrize('fault',['symlink','foreign-preview','size'])
def test_context_preparation_rejects_unsafe_or_mismatched_material(routing_case,fault):
    session,_,_,_,request,_=routing_case
    source_evidence(session,request['source'])
    preview=routes.source_previews(session,routing_case[2],request['source'])[0]['path']
    if fault=='symlink':(session/'reports/069-rejected').symlink_to(session/'previews',target_is_directory=True)
    elif fault=='foreign-preview':
        bad=session/'previews/uncommitted.jpg';Image.new('RGB',(96,96)).save(bad);preview=bad
    else:
        # Keep the stub native receipt consistent; the independent geometry check must reject it.
        path=session/preview;Image.new('RGB',(12,8)).save(path)
        manifest=contract.load_json(session/'manifest.json')
        entry=manifest['runs'][-1];receipt_path=session/'runs'/entry['receipt'];receipt=contract.load_json(receipt_path)
        receipt['outputs']=[stretch.record(session,path)];contract.atomic_write_json(receipt_path,receipt)
        entry['receipt_sha256']=contract.sha256_file(receipt_path);contract.atomic_write_json(session/'manifest.json',manifest)
    with pytest.raises((contract.ContractError,OSError)):
        morphology.prepare_context(session,request['source']['path'],request['regions']['path'],preview,'069-rejected')
    assert not list(session.glob('reports/*/scientific-attempt.json'))


def test_cli_prepares_context_and_preserves_nonempty_icc(routing_case,monkeypatch,capsys):
    session,_,_,_,request,_=routing_case
    preview=source_evidence(session,request['source'])[1]
    path=session/preview['path']
    profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    with Image.open(path) as image:
        image.save(path,format='JPEG',icc_profile=profile)
    manifest=contract.load_json(session/'manifest.json');entry=manifest['runs'][-1]
    receipt_path=session/'runs'/entry['receipt'];receipt=contract.load_json(receipt_path)
    receipt['outputs']=[stretch.record(session,path)];contract.atomic_write_json(receipt_path,receipt)
    entry['receipt_sha256']=contract.sha256_file(receipt_path);contract.atomic_write_json(session/'manifest.json',manifest)
    monkeypatch.setattr(sys,'argv',['stretch','morphology-context','--session',str(session),
        '--source',request['source']['path'],'--regions',request['regions']['path'],
        '--preview',preview['path'],'--run-id','069-cli-context'])
    assert stretch.main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='prepared'
    with Image.open(session/result['panel']['path']) as panel:
        assert panel.info['icc_profile']==profile
    assert not list(session.glob('reports/*/strategy.json'))
    assert not list(session.glob('reports/*/scientific-attempt.json'))


def test_morphology_revision_lists_changes_and_requires_all_affected_rois(routing_case):
    result=failed_profile(routing_case);session=routing_case[0]
    old=contract.load_json(session/result['report']['request']['strategy']['plan']['path'])
    goals=contract.load_json(session/old['goals']['path'])
    goals['morphology']['primary_subject']='Clarified synthetic primary object'
    goals['revision']={'parameters':stretch.record(session,Path(result['parameters_path'])),
        'reason':'Clarify the primary subject using the same observed structures.',
        'affected_regions':['faint'],'evidence':[result['report']['steps'][-1]['local_panel']],'target_change':None}
    assert inspect(routing_case,goals,'072-bad-revision')[0]['status']=='needs_revision'
    goals['revision']['affected_regions']=[r['region'] for r in goals['morphology']['regions'] if r['relation']=='primary']
    result,path=inspect(routing_case,goals,'072-clarified')
    assert result['status']=='ready',result['issues']
    prepared=routes.init_requests(session,routing_case[4]['source']['path'],routing_case[4]['regions']['path'],path,'072-clarified', stepwise=False)
    assert contract.load_json(Path(prepared['strategy']))['revision_diff']['morphology_changes']


def test_context_survives_default_prune_and_committed_replay(tmp_path,monkeypatch):
    session,source,_=support._session(tmp_path,monkeypatch)
    write_jpeg=support._write_jpeg
    monkeypatch.setattr(support,'_write_jpeg',lambda path,color=(18,24,32),**kwargs:write_jpeg(path,color,size=(12,8)))
    run,jpeg=support._delivery_run(session,source,monkeypatch)
    review=support._review(session,run['id'],jpeg)
    support._review(session,'010-input',session/'previews/010-input-autostretch.jpg')
    # This persistence-only fixture supplies the accepted-domain dependency; morphology checks stay enabled.
    monkeypatch.setattr(stretch,'accepted_parent',lambda *args:None)
    regions=session/'reports/context-regions.json'
    contract.atomic_write_json(regions,{'schema':'starun-siril.regions.v1','reference':{'sha256':contract.sha256_file(source),'width':12,'height':8},
        'regions':[{'id':'body','role':'faint','rect':[2,2,6,6]}]})
    prepared=morphology.prepare_context(session,'@input',regions,'previews/010-input-autostretch.jpg','069-context')
    paths=morphology.retained_paths(session,contract.load_json(session/'session.json'))
    selection=support._selection(session,run['id'],jpeg,review)
    result=core.finalize_session(session,selection_value=selection,keep_intermediates=False)
    assert all((session/p).is_file() for p in paths)
    audit=contract.load_json(session/'reports/final-audit.json')
    assert paths <= {r['path'] for r in audit['evidence_files']}
    assert not paths.intersection(audit['pruned_intermediate_images'])
    assert core.finalize_session(session,selection_value=selection,keep_intermediates=False)==result
    panel=session/prepared['panel']['path'];panel.write_bytes(panel.read_bytes()+b'drift')
    with pytest.raises(contract.ContractError):core.finalize_session(session,selection_value=selection,keep_intermediates=False)


def test_prior_policy_replays_without_morphology_migration(tmp_path,monkeypatch):
    prior=ROOT/'tests/fixtures/stretch-morphology-prior.json'
    assert prior.read_bytes()==(ROOT/'references/stretch-morphology-history.json').read_bytes()
    replay_and_finalize_without_migration(tmp_path,monkeypatch,prior)
