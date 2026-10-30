"""Current-policy fixtures with real, bound evidence for deliberate custom chains."""
import copy
import json
from pathlib import Path

import numpy as np
from PIL import Image
import deep_sky_siril_contract as contract
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_v2 as protected


def with_artifact_focus(session, source, goals):
    from deep_sky_siril_artifact_focus import enabled, FOCUS
    session=Path(session);goals=copy.deepcopy(goals)
    if not enabled(contract.load_json(session/'session.json')) or 'artifact_focus' in goals:
        return goals
    previews=routes.source_previews(session,contract.load_json(session/'session.json'),source)
    if not previews:
        return goals
    data=stretch.pixels(stretch.source_file(session,contract.load_json(session/'session.json'),source))
    path=session/'reports'/('069-review-focus-'+source['sha256'][:12]+'.json')
    if not path.exists():
        contract.atomic_write_json(path,{'schema':FOCUS,'source':source,'geometry':{'width':data.shape[2],'height':data.shape[1]},
            'previous':None,'areas':[],'none_reason':'No artifact focus areas are declared in this controlled synthetic fixture.',
            'evidence':[previews[0]]})
    goals['artifact_focus']=stretch.record(session,path)
    return goals


def with_morphology(session, source, regions, goals):
    """Describe the selected synthetic fixture scenario using real parent materials."""
    from deep_sky_siril_morphology import prepare_context, GOALS
    session=Path(session);goals=with_artifact_focus(session,source,goals)
    selected=next((k for k,v in GOALS.items() if v[0]==goals['profile']),None)
    if selected is None:
        return goals
    if goals.get('morphology',{}).get('processing_goal')==selected:
        return goals
    previews=routes.source_previews(session,contract.load_json(session/'session.json'),source)
    if not previews:
        return goals
    run_id='069-morphology-'+source['sha256'][:8]+'-'+regions['sha256'][:8]
    path=session/'reports'/run_id/'morphology-context.json'
    if not path.exists():prepare_context(session,source['path'],regions['path'],previews[0]['path'],run_id)
    rows=[]
    for region in contract.load_json(session/regions['path'])['regions']:
        role=region['role']
        relation={'background':'background','stars':'field_star'}.get(role,'primary')
        feature={'background':'background','stars':'field_stars','core':'bright_core',
            'faint':{'emission_nebula':'diffuse_nebulosity','high_dynamic_range':'faint_envelope',
                     'galaxy_halo':'galaxy_disk','dust_reflection':'dust_nebulosity','star_cluster':'cluster_outskirts'}[goals['profile']]}[role]
        if goals['profile']=='star_cluster' and role in {'core','stars'}:
            relation='primary';feature='cluster_center' if role=='core' else 'cluster_members'
        rows.append({'region':region['id'],'relation':relation,'features':[feature],
            'observation':f"The synthetic {region['id']} ROI represents {feature} in this declared test scenario."})
    goals['morphology']={'context':stretch.record(session,path),'primary_subject':'The main synthetic subject',
                         'processing_goal':selected,'regions':rows}
    return goals


def with_constraint_review(goals, mode='profile'):
    """Explain deliberate synthetic intermediate bounds using the actual parent control."""
    goals = copy.deepcopy(goals)
    goals['constraint_review'] = []
    if 'hdr' in goals:
        return goals
    requests = routes.bound_requests_for({'mode': mode, 'source': {}, 'regions': {}}, goals, {})
    evidence = [row for row in goals['evidence'] if Path(row.get('path','')).suffix in {'.jpg','.png'}]
    for variant, request in requests.items():
        for index, step in enumerate(request['operators'][:-1], 1):
            for row in step['constraints']:
                if row['statistic'] in {'median','p01','p99','p999','max_rgb'} and row['min'] > 0:
                    goals['constraint_review'].append({'variant': variant, 'step': index,
                        'region': row['region'], 'statistic': row['statistic'],
                        'reason': f"This test deliberately requires {row['region']} {row['statistic']} >= {row['min']} at step {index}; the actual parent is the control, and native evaluation determines reachability.",
                        'evidence': evidence})
    return goals


def source_evidence(session, binding):
    session=Path(session)
    payload=contract.load_json(session/'session.json')
    if 'schema' not in payload:
        # Unit contexts mock the accepted-parent dependency; real sessions keep their native receipts.
        source=stretch.source_file(session,payload,binding)
        run_id='001-parent-'+binding['sha256'][:8]
        preview=session/'previews'/(run_id+'.jpg')
        data=stretch.pixels(source)
        rgb=np.repeat(data,3,axis=0) if len(data)==1 else data
        Image.fromarray(np.uint8(np.clip(rgb[:,::-1].transpose(1,2,0),0,1)*255)).save(preview)
        script=session/'scripts'/(run_id+'.ssf')
        script.write_text('requires 1.4.4 1.5.0\nset32bits\nload '+json.dumps(str(source))+
            '\nautostretch -linked -2.8 0.22\nsavejpg '+json.dumps(str(preview.with_suffix('')))+' 95\nclose\n')
        receipt=session/'runs'/(run_id+'.json')
        contract.atomic_write_json(receipt,{'schema':'starun-siril.run-receipt.v1','id':run_id,
            'protocol':'input.inspect','status':'success','source_path':binding['path'],
            'source':{**binding,'size':source.stat().st_size},'primary_output':None,'image_domain':'linear',
            'script':stretch.record(session,script),'outputs':[stretch.record(session,preview)]})
        contract.atomic_write_json(session/'reviews'/(run_id+'.json'),{'schema':'starun-siril.review.v1',
            'run_id':run_id,'verdict':'accept','notes':'Accepted synthetic parent for unit evidence binding.'})
        path=session/'manifest.json'
        manifest=contract.load_json(path) if path.exists() else {'schema':'starun-siril.manifest.v1','runs':[]}
        manifest['runs']=[e for e in manifest['runs'] if e['id']!=run_id]+[{
            'id':run_id,'status':'success','receipt':receipt.name,'receipt_sha256':contract.sha256_file(receipt)}]
        contract.atomic_write_json(path,manifest)
    previews=routes.source_previews(session,payload,binding)
    assert previews,'Fixture requires a committed native view of its actual scientific parent'
    return [binding,*previews]


def explicit_request(session, request):
    request=copy.deepcopy(request)
    regions=contract.load_json(Path(session)/request['regions']['path'])['regions']
    request.update(schema=protected.PROPOSAL_REQUEST, profile=request.get('profile','custom'),
        targets=request.get('targets',{r['id']:'bright' for r in regions if r['role']=='faint'}),
        core_absence=request.get('core_absence',None if any(r['role']=='core' for r in regions) else 'No compact core in the synthetic fixture'))
    for step in request['operators']:
        factors={'mtf':{'midtones':.05},'asinh':{'stretch':5.},'ght':{'D':.2,'B':.5},
                 'autostretch':{'shadowsclip':-2.8,'targetbg':.18}}
        step.setdefault('factors',copy.deepcopy(factors[step['method']]))
        for r in regions:
            extra={'background':[('mad',.04),('hf_sigma2',.04)],'core':[('p999',.999),('span',1.)],
                   'stars':[('p999',.999),('span',1.)]}.get(r['role'],[])
            for statistic,maximum in extra:
                if not any(c['region']==r['id'] and c['statistic']==statistic for c in step['constraints']):
                    step['constraints'].append(dict(region=r['id'],statistic=statistic,min=0.,max=maximum))
    return request


def bind_exception(session, request, run_id, *, timeout=60, hdr=None, tail=None, protection=None):
    session = Path(session)
    if (session/'reports'/run_id).exists():
        run_id += '-plan'
    request = explicit_request(session, request)
    regions = contract.load_json(session / request['regions']['path'])['regions']
    protected.validate(request, regions)
    for step in request['operators']:
        protected.proposal_tokens(step, 0., {'LP': 0., 'SP': .5, 'HP': 1.})
    marker = session/'reports'/('.stretch-profile-'+request['source']['sha256']+'.json')
    if marker.exists():
        initial = contract.load_json(session/contract.load_json(marker)['path'])
        previous = session/'reports'/routes.candidate_run_id(initial['run_id'], 'profile')/'stretch-parameters.json'
    else:
        first_id = run_id+'-initial'
        goals = {'schema': routes.BOUND_GOALS, 'profile': 'high_dynamic_range' if any(r['role'] == 'core' for r in regions) else 'dust_reflection',
            'reason': 'The synthetic source contains a faint envelope and compact protected highlights.',
            'evidence': source_evidence(session,request['source']), 'targets': request['targets'], 'core_absence': request['core_absence'],
            'protection': copy.deepcopy(protection if protection is not None else request['operators'][-1]['constraints']),
            'manual_lookup': request['manual_lookup'], 'revision': None}
        faint = next(row for row in goals['protection'] if row['region'] in goals['targets'])
        failed_target = {**faint, 'min': faint['max']}
        goals['phases'] = [{'factors': {'stretch': 1.}, 'constraints': [failed_target]},
            {'factors': {'D': .1, 'B': 0.}, 'constraints': [],
             'landmarks': {k: {'region': r, 'statistic': s} for k,r,s in
                [('LP', next(r['id'] for r in regions if r['role'] == 'background'), 'p99'),
                 ('SP', next(iter(goals['targets'])), 'median'),
                 ('HP', next((r['id'] for r in regions if r['role'] == 'core'), next(iter(goals['targets']))), 'p99')]}}]
        goal_path = session/'reports'/(first_id+'-goals.json')
        goals = with_morphology(session,request['source'],request['regions'],with_constraint_review(goals))
        contract.atomic_write_json(goal_path, goals)
        prepared = routes.init_requests(session, request['source']['path'], request['regions']['path'], goal_path, first_id, stepwise=False)
        item = prepared['candidates']['profile']
        result = stretch.solve_stretch_parameters(session, item['request'], item['run_id'], timeout)
        assert result['status'] == 'ineligible', result['report'].get('reason')
        previous = Path(result['parameters_path'])
    original = contract.load_json(previous)
    initial_plan = contract.load_json(session/original['request']['strategy']['plan']['path'])
    initial_goals = contract.load_json(session/initial_plan['goals']['path'])
    affected = set(request['targets']) | {row['region'] for step in original['steps']
        for trial in step.get('trials', []) for row in trial['audit'].get('constraints', [])
        if not row['satisfied'] and row['statistic'] not in {'mad','hf_sigma2'}}
    goals = {key: copy.deepcopy(value) for key, value in initial_goals.items() if key != 'phases'}
    goals.update(operators=request['operators'], protection=copy.deepcopy(protection if protection is not None else request['operators'][-1]['constraints']),
        revision={'parameters': stretch.record(session, previous), 'affected_regions': sorted(affected),
            'reason': 'The measured synthetic first-phase display requirement failed. Test this explicit alternative; this does not disqualify the operator family.',
            'target_change': None, 'evidence': [original['steps'][-1]['local_panel']]})
    if [step['constraints'] for step in request['operators']] != [step['constraints'] for step in original['request']['operators']]:
        goals['revision']['target_change']={'kind':'science_protection',
            'reason':'This regression case explicitly changes a measured regional constraint.',
            'tradeoff':'Keep endpoint and highlight protection; review the actual retained response independently.',
            'evidence':[original['steps'][-1]['local_panel']]}
    if hdr is not None:
        goals['hdr'] = hdr
    if tail is not None:
        goals['tail'] = tail
    path = session/'reports'/(run_id+'-goals.json')
    goals = with_morphology(session,request['source'],request['regions'],with_constraint_review(goals, 'exception'))
    contract.atomic_write_json(path, goals)
    prepared = routes.init_requests(session, request['source']['path'], request['regions']['path'], path, run_id, mode='exception', stepwise=False)
    return prepared['candidates']['exception']


def solve_bound(session, path, run_id, timeout=60, **kwargs):
    request = contract.load_json(Path(path))
    if 'strategy' in request:
        return stretch.solve_stretch_parameters(session, path, run_id, timeout, **kwargs)
    item = bind_exception(session, request, run_id, timeout=timeout)
    return stretch.solve_stretch_parameters(session, item['request'], item['run_id'], timeout, **kwargs)


def bind_hoo(session, request, run_id, *, body_stretch=5.):
    session=Path(session)
    body=copy.deepcopy(request['operators']['ha'])
    constraints=copy.deepcopy(body[-1]['constraints'])
    targets=request['targets']
    sky=next(r['id'] for r in contract.load_json(session/request['regions']['path'])['regions'] if r['role']=='background')
    anchors={k:{'region':r,'statistic':s} for k,r,s in [('LP',sky,'p99'),('SP',next(iter(targets)),'median'),('HP','core','p99')]}
    phases=[{'factors':{'stretch':body_stretch},'constraints':constraints},{'factors':{'D':.2,'B':.5},'constraints':constraints,'landmarks':anchors}]
    channels={'rgb':copy.deepcopy(request['operators']['rgb'])}
    for role in ('ha','oiii'):
        channel_constraints=request['operators'][role][-1]['constraints']
        channels[role]=[{'method':'asinh','purpose':'Synthetic channel dynamic range','factors':phases[0]['factors'],'constraints':channel_constraints},
            {'method':'ght','purpose':'Synthetic channel envelope separation','factors':phases[1]['factors'],'landmarks':anchors,'constraints':channel_constraints}]
    common=[row for row in constraints if all(row in request['operators'][role][-1]['constraints'] for role in ('rgb','ha','oiii'))]
    goals={'schema':routes.BOUND_GOALS,'profile':'high_dynamic_range','reason':'Observed synthetic bright core and faint envelope in the actual linear source.',
        'evidence':source_evidence(session,request['source']),'targets':targets,'core_absence':request['core_absence'],'protection':common,
        'manual_lookup':request['manual_lookup'],'revision':None,'phases':phases,'channels':channels}
    goals=with_morphology(session,request['source'],request['regions'],with_constraint_review(goals))
    path=session/'reports'/(run_id+'-goals.json');contract.atomic_write_json(path,goals)
    prepared=routes.init_requests(session,request['source']['path'],request['regions']['path'],path,run_id)
    plan_binding=stretch.record(session,Path(prepared['strategy']))
    plan,requests=routes.load_plan(session,contract.load_json(session/'session.json'),plan_binding)
    request={**copy.deepcopy(request),'strategy':plan_binding,'schema':'starun-siril.dualband-request.v4',
        'source':plan['source'],'regions':plan['regions'],'profile':goals['profile'],
        'operators':{role:requests[role]['operators'] for role in ('rgb','ha','oiii')}}
    path=session/'reports'/(run_id+'-hoo.json');contract.atomic_write_json(path,request)
    return path,request


def historical_parameters(session, result, snapshot, run_id='071-historical-fixture'):
    """Construct a prior-contract fixture copy; never convert a user report in place."""
    session=Path(session)
    report=copy.deepcopy(result['report'])
    report.pop('trial_artifacts',None)
    request=report['request'];request.pop('strategy',None);request.pop('artifact_focus',None)
    if snapshot['execution_policy'].get('stretch_parameters')!='agent_proposal_v1':
        request['schema']=protected.REQUEST
        for step in request['operators']: step.pop('factors',None)
        report.pop('generation',None)
    directory=session/'reports'/run_id;directory.mkdir()
    path=directory/'request.json';contract.atomic_write_json(path,request)
    report.update(run_id=run_id)
    report['bindings']['request']=stretch.record(session,path)
    report['bindings']['implementation']=copy.deepcopy(snapshot.get('implementation',snapshot.get('bindings',{}).get('implementation')))
    profiles=snapshot.get('profiles',snapshot.get('bindings',{}).get('profiles'))
    if profiles is not None:report['bindings']['profiles']=copy.deepcopy(profiles)
    path=directory/'stretch-parameters.json';contract.atomic_write_json(path,report)
    return path,report
