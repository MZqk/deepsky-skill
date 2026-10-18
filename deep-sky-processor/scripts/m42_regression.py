#!/usr/bin/env python3
"""Rebuild M42 candidates from one linear master; retain evidence without auto-accepting images."""

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

from agent_workflow import initialize_session, apply_action
from fits_io import read_image, write_image
from pipeline import run_pipeline
from regional_metrics import M42_REGIONS, region_masks, regional_metrics, saturation_evidence, native_ring_profiles
from runtime_info import runtime_info
from star_tools import detect_stars
from quality_state import build_quality_state, persist_quality_state
from stretch import render_protected_stretch


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def image_panel(image, label):
    rgb = np.repeat(image[..., None], 3, axis=2) if image.ndim == 2 else image[..., :3]
    panel = Image.fromarray(np.round(np.clip(rgb, 0, 1)*255).astype(np.uint8))
    framed = Image.new('RGB', (panel.width, panel.height+32), '#181818')
    framed.paste(panel, (0,32))
    ImageDraw.Draw(framed).text((8,10), label, fill='white')
    return framed


def montage(path, panels):
    images = [image_panel(image,label) for label,image in panels]
    canvas = Image.new('RGB',(sum(image.width for image in images),max(image.height for image in images)))
    offset=0
    for image in images:
        canvas.paste(image,(offset,0)); offset+=image.width
    canvas.save(path,quality=95)


def compare_acceptance(baseline, candidate, stretch_result, safe_reference=None):
    base, new = baseline['regions'], candidate['regions']
    background_difference = abs(new['background']['median']-base['background']['median'])/max(abs(base['background']['median']),1e-8)
    outer_base=base['outer']['background_subtracted_median']
    outer_ratio=new['outer']['background_subtracted_median']/outer_base if outer_base>1e-8 else None
    core_base,core_new=base['core'],new['core']
    span_ratio=core_new['relative_contrast']/max(core_base['relative_contrast'],1e-8)
    plateau_reduced=core_new['plateau']['value']['new_area_px'] < core_base['plateau']['value']['new_area_px']
    plateau_reduced = plateau_reduced or any(
        core_new['channels'][name]['plateau']['new_area_px'] < band['plateau']['new_area_px']
        for name, band in core_base['channels'].items())
    display=stretch_result.get('stretch_diagnostics',{}).get('display_gates',{})
    ringing=[gate for gate in display.get('gates',[]) if gate['code']=='STAR_RINGING']
    channels_clipped=any(region['channels'][name]['clip_ratio']>0
                         for region in new.values() for name in region['channels'])
    global_clipped=any(channel['clip_ratio']>0 for channel in candidate['channel_clipping']['value'].values())
    new_plateau=any(region['channels'][name]['plateau']['new_area_px']>0
                    for region in new.values() for name in region['channels'])
    checks={'background_matched':background_difference<=.05,
            'outer_signal_preserved':outer_ratio is not None and outer_ratio>=.95,
            'core_structure_improved':plateau_reduced or span_ratio>1.05,
            'no_new_core_plateau':not new_plateau,
            'no_channel_clipping':not (channels_clipped or global_clipped),
            'no_new_dark_rings':not any(gate['status']=='failed' or
                                       (gate.get('value') or {}).get('n_ring', 0)>0 for gate in ringing),
            'standard_algorithm_applied':stretch_result['effective_config']['stretch_method']=='masked_ghs_standard'}
    safe_ratios = None
    if safe_reference is not None:
        safe_core = safe_reference['regions']['core']
        safe_ratios = {key: core_new[key] / max(safe_core[key], 1e-8)
                       for key in ('relative_contrast', 'mean_gradient_sigma2')}
        checks['safe_core_structure_preserved'] = all(ratio >= .95 for ratio in safe_ratios.values())
    return {'numeric_pass':all(checks.values()),'checks':checks, 'safe_core_structure_ratios': safe_ratios,
            'background_difference_ratio':background_difference,'outer_signal_ratio':outer_ratio,
            'core_relative_contrast_ratio':span_ratio,'core_plateau_reduced':plateau_reduced,
            'ringing_evidence':ringing,'visual_review':'pending',
            'accepted':False,'tolerance_note':'5% is an engineering comparison tolerance, not physical SNR'}


def run_regression(input_path, output_dir, emission_control=None, cluster_control=None, second_cluster_control=None):
    input_path, output_dir=Path(input_path).resolve(),Path(output_dir).resolve()
    output_dir.mkdir(parents=True,exist_ok=False)
    log_path=output_dir/'execution.log'
    def quiet(call,*args,**kwargs):
        with log_path.open('a') as log,contextlib.redirect_stdout(log):
            return call(*args,**kwargs)
    print('Initialize a new session from original FITS',flush=True)
    raw,meta=quiet(read_image,str(input_path))
    session=output_dir/'session'
    state=quiet(initialize_session,input_path,session,target_type='emission_nebula',target_name='M42')
    analysis=json.loads(Path(state['analysis_report']).read_text())
    gradient=analysis.get('gradient',{})
    dbe_decision=gradient.get('dbe_decision')
    linear_steps=['dbe','color'] if dbe_decision=='apply' else ['color']
    if dbe_decision not in ('apply','skip',None):
        write_json(output_dir/'background_review.json',gradient)
    for step in linear_steps:
        event=quiet(apply_action,session,{'operation':'run_step','step':step,'preset':'medium',
                                        'color_mode':'emission','auto_crop_edges':False,
                                        'background_neutralize_lock':False})
    master=event['result']['artifact']
    linear=quiet(read_image,master)[0]
    masks=region_masks(raw.shape)
    star_mask=quiet(detect_stars,linear.mean(axis=2),star_threshold=.88)
    write_image(star_mask,output_dir/'fixed_stars.tif')
    write_json(output_dir/'regions.json',{'shape':list(raw.shape),'coordinate_convention':'zero_based_xy',
                                        'definitions':M42_REGIONS,'fixed_star_mask':str(output_dir/'fixed_stars.tif')})
    raw_metrics=regional_metrics(raw,raw,masks,star_mask,processing_stage='linear')
    linear_metrics=regional_metrics(linear,raw,masks,star_mask,processing_stage='linear')
    source_display=render_protected_stretch(raw,'pi_mtf',{'target_bg':.05})[0]
    overlay=image_panel(source_display,'Fixed M42 regions / source MTF preview only')
    draw=ImageDraw.Draw(overlay)
    for name,spec in M42_REGIONS.items():
        color={'core':'yellow','inner':'cyan','outer':'orange','background':'lime'}[name]
        if 'radius' in spec:
            cx,cy=spec['center_xy'];radius=spec['radius']
            draw.ellipse((cx-radius,cy-radius+32,cx+radius,cy+radius+32),outline=color,width=3)
        else:
            x,y,w,h=spec['bbox_xywh'];draw.rectangle((x,y+32,x+w,y+h+32),outline=color,width=3)
    overlay.save(output_dir/'regions_overlay.jpg',quality=95)
    def pipeline(source,name,steps,method='auto',params=None,target_name='M42',target_type='emission_nebula',color='emission'):
        directory=output_dir/name;directory.mkdir(parents=True)
        result=quiet(run_pipeline,str(source),str(directory/'output.tif'),steps=steps,preset='medium',
                     target_type=target_type,target_name=target_name,color_mode=color,stretch_method=method,
                     override_params=params,auto_crop_edges=False,background_neutralize_lock=False,
                     keep_all=True,work_dir=str(directory/'artifacts'),result_json=str(directory/'result.json'))
        return result,quiet(read_image,result['outputs']['image'])[0]
    def comparison_source(result, expected):
        if result['effective_config']['stretch_method'] == expected:
            return result['outputs']['image']
        rejected = Path(result['outputs']['image']).parent/'artifacts'/'05_rejected_stretch.tif'
        if not rejected.exists():
            raise RuntimeError(f'No retained {expected} evidence: {result["outputs"]["result_json"]}')
        # Rejected renders are diagnostic evidence, never accepted processing masters.
        write_json(str(rejected)+'.meta.json', {'is_linear':False})
        quality = build_quality_state(rejected, master, result['quality_state'], 'rejected_stretch',
                    [{'code': 'DIAGNOSTIC_REJECTED_RENDER', 'status': 'failed',
                      'evidence': {'source_result': result['outputs']['result_json']}}])
        persist_quality_state(rejected, quality)
        result['comparison_evidence'] = {'expected_method':expected,'path':str(rejected),
                                        'accepted_by_pipeline':False,
                                        'applied_method':result['effective_config']['stretch_method']}
        return str(rejected)
    print('Render emission baseline with fixed final color',flush=True)
    baseline_stretch,baseline_stretched=pipeline(master,'emission_stretch','stretch','emission',
                 {'shadow_pctl':0.,'highlight_pctl':99.95,'stretch_gamma':.43,
                  'target_bg':.10,'ghs_protect_strength':.75})
    baseline_source=comparison_source(baseline_stretch,'emission')
    baseline_stretched=quiet(read_image,baseline_source)[0]
    baseline_result,baseline=pipeline(baseline_source,'emission_natural','final_color',params={'saturation':1.1})
    baseline_metrics=regional_metrics(baseline,linear,masks,star_mask)
    desired_bg = baseline_metrics['regions']['background']['median']
    background_mask = masks['background'] & ~(star_mask > .5)
    def fit_branch(name, method, parameters):
        attempts = []
        target_bg = .10
        for attempt in range(1, 9):
            print(f'{name} attempt {attempt}: target_bg={target_bg:.6f}', flush=True)
            params = {**parameters, 'target_bg': target_bg}
            stretched, image = pipeline(master, f'{name}/{attempt:02d}_stretch', 'stretch', method, params)
            source = comparison_source(stretched, method)
            if 'comparison_evidence' in stretched:
                image = quiet(read_image, source)[0]
            natural, rendered = pipeline(source, f'{name}/{attempt:02d}_natural', 'final_color',
                                         params={'saturation': 1.1})
            actual_bg = float(np.median(rendered.mean(axis=2)[background_mask]))
            error = abs(actual_bg-desired_bg) / max(abs(desired_bg), 1e-8)
            attempts.append({'attempt': attempt, 'target_bg': target_bg, 'actual_background': actual_bg,
                             'relative_background_error': error, 'result': stretched['outputs']['result_json']})
            if error <= .05:
                break
            next_bg = float(np.clip(target_bg*desired_bg/max(actual_bg, 1e-8), .001, .45))
            if abs(next_bg-target_bg) < 1e-7:
                break
            target_bg = next_bg
        regional = regional_metrics(rendered, linear, masks, star_mask)
        return {'stretch': stretched, 'natural': natural, 'source': source,
                'metrics': regional, 'attempts': attempts, 'parameters': params}

    safe = fit_branch('safe_mtf', 'pi_mtf', {})
    safe_reference = quiet(read_image, safe['natural']['outputs']['image'])[0]
    profiles = []
    for b in (4, 8):
        for protection in (.35, .5, .7, .85):
            profile_id = f'b{b}_protect{protection:g}'
            branch = fit_branch('profiles/'+profile_id, 'masked_ghs_standard',
                                {'ghs_standard_b': b, 'ghs_protect_strength': protection})
            branch['id'] = profile_id
            branch['acceptance'] = compare_acceptance(baseline_metrics, branch['metrics'], branch['stretch'], safe['metrics'])
            _, diagnostic = render_protected_stretch(linear, 'masked_ghs_standard',
                                branch['stretch']['effective_config']['params'], diagnostic_regions=masks)
            branch['curve_diagnostics'] = diagnostic
            profiles.append(branch)
            write_json(output_dir/'profile_comparison.json', profiles)
    def rank(branch):
        checks = branch['acceptance']['checks']
        eligible = all(checks[key] for key in ('background_matched', 'outer_signal_preserved',
                       'no_new_core_plateau', 'no_channel_clipping', 'no_new_dark_rings',
                       'standard_algorithm_applied', 'safe_core_structure_preserved'))
        return (eligible, branch['metrics']['regions']['core']['relative_contrast'],
                -branch['attempts'][-1]['relative_background_error'])
    chosen = max(profiles, key=rank)
    candidate_stretch, candidate_result = chosen['stretch'], chosen['natural']
    candidate_source, candidate_metrics = chosen['source'], chosen['metrics']
    candidate_stretched = quiet(read_image, candidate_stretch['outputs']['image'])[0]
    candidate = quiet(read_image, candidate_result['outputs']['image'])[0]
    trials = chosen['attempts']
    applied_params = chosen['parameters']
    applied_bg = applied_params['target_bg']
    print('Verify API / CLI / Agent use identical installed code and pixels',flush=True)
    cli_dir=output_dir/'cli_standard';cli_dir.mkdir()
    command=[sys.executable,'-B',str(Path(__file__).with_name('pipeline.py')),master,str(cli_dir/'output.tif'),
             '--steps','stretch','--strength','medium','--target-type','emission_nebula','--target-name','M42',
             '--color-mode','emission','--override-params',json.dumps(applied_params),
             '--no-auto-crop-edges','--no-background-neutralize','--keep-all','--work-dir',str(cli_dir/'artifacts'),
             '--result-json',str(cli_dir/'result.json')]
    with log_path.open('a') as log:
        subprocess.run(command,check=True,stdout=log,stderr=log)
    cli=json.loads((cli_dir/'result.json').read_text())
    agent_stretch=quiet(apply_action,session,{'operation':'run_step','step':'stretch','preset':'medium',
                         'color_mode':'emission','params':applied_params,
                         'auto_crop_edges':False,'background_neutralize_lock':False})['result']
    agent=agent_stretch['pipeline_result']
    entrypoints={name:{'method':result['effective_config']['stretch_method'],
                      'runtime':result['runtime'], 'equal_api_pixels':bool(np.array_equal(
                          quiet(read_image,result['outputs']['image'])[0],candidate_stretched))}
                 for name,result in [('api',candidate_stretch),('cli',cli),('agent',agent)]}
    agent_natural=quiet(apply_action,session,{'operation':'run_step','step':'final_color','preset':'medium',
                         'color_mode':'emission','params':{'saturation':1.1},
                         'auto_crop_edges':False,'background_neutralize_lock':False})['result']
    agent_final=quiet(read_image,agent_natural['artifact'])[0]
    if 'comparison_evidence' not in candidate_stretch:
        assert np.array_equal(agent_final,candidate), 'Agent final color differs from the shared candidate'
    assert all(item['equal_api_pixels'] for item in entrypoints.values()), 'Entrypoint pixel mismatch'
    write_image(baseline,output_dir/'M42_emission_natural.jpg')
    write_image(candidate,output_dir/'M42_protected_natural.jpg')
    montage(output_dir/'full_comparison.jpg',[('Emission / rejected diagnostic',baseline),('Safe MTF reference',safe_reference),('Protected candidate / unaccepted',candidate)])
    cx,cy=M42_REGIONS['core']['center_xy'];radius=M42_REGIONS['core']['radius']
    crop=(slice(cy-radius,cy+radius),slice(cx-radius,cx+radius))
    montage(output_dir/'core_1to1.jpg',[('Source fixed MTF / evidence',source_display[crop]),
                                     ('Emission / rejected diagnostic',baseline[crop]),
                                     ('Safe MTF / native pixels',safe_reference[crop]),
                                     ('Protected candidate / native pixels',candidate[crop])])
    metrics={'raw':raw_metrics,'linear_master':linear_metrics,
             'emission_stretch':regional_metrics(baseline_stretched,linear,masks,star_mask),
             'protected_stretch':regional_metrics(quiet(read_image,candidate_source)[0],linear,masks,star_mask),
             'emission_natural':baseline_metrics,'safe_mtf_natural':safe['metrics'],'protected_natural':candidate_metrics}
    write_json(output_dir/'regional_metrics.json',metrics)
    acceptance=compare_acceptance(baseline_metrics,candidate_metrics,candidate_stretch,safe['metrics'])
    display = candidate_stretch.get('stretch_diagnostics', {}).get('display_gates', {})
    ring_gate = next((g for g in display.get('gates', []) if g['code'] == 'STAR_RINGING'), {})
    factor = max(raw.shape[:2]) / 1400
    native_fwhm = max(2., float(ring_gate.get('value', {}).get('fwhm_px', 3))*factor)
    centers = [(1154, 2192)]
    for item in ring_gate.get('evidence', {}).get('ringing_stars', []):
        row, col = item['center_rc']
        center = (round(col*factor), round(row*factor))
        if center not in centers:
            centers.append(center)
    ring_reports = [native_ring_profiles({'linear': linear, 'safe_mtf': safe_reference,
                    'emission_diagnostic': baseline, 'candidate': candidate}, center, native_fwhm, star_mask)
                    for center in centers]
    write_json(output_dir/'native_ring_profiles.json', ring_reports)
    rx, ry = centers[0]
    ring_crop = (slice(ry-64, ry+64), slice(rx-64, rx+64))
    montage(output_dir/'ringing_1to1.jpg', [('Safe MTF / native', safe_reference[ring_crop]),
             ('Emission / diagnostic', baseline[ring_crop]), ('Protected / native', candidate[ring_crop])])
    print('Run emission and star-cluster controls',flush=True)
    controls={}
    for source,name,kind,color,params,expected in [
            (emission_control,'C50','emission_nebula','emission',
             {'stretch_gamma':.25,'target_bg':.045,'stretch_min_p99':.15,
              'shadow_pctl':0.,'highlight_pctl':100.,'saturation':1.1},'emission'),
            (cluster_control,'M45','open_cluster','auto',{'saturation':1.1},'pi_mtf'),
            (second_cluster_control,'M45_seestar','open_cluster','auto',{'saturation':1.1},'pi_mtf')]:
        if source:
            control,image=pipeline(source,'control_'+name,'color,stretch,final_color,star_remove,star_reduce'
                                    if name.startswith('M45') else 'color,stretch,final_color',params=params,
                                    target_name='M45' if name.startswith('M45') else name,target_type=kind,color=color)
            write_image(image,output_dir/f'{name}_control.jpg')
            controls[name]={'input':str(Path(source).resolve()),'expected_method':expected,
                            'selected_method':control['stretch_diagnostics']['selected_method'],
                            'selection_matches':control['stretch_diagnostics']['selected_method']==expected,
                            'applied_method':control['effective_config']['stretch_method'],
                            'method_matches':control['effective_config']['stretch_method']==expected,
                            'executed_steps':control['execution']['executed_steps'],
                            'skipped_steps':control['execution']['skipped_steps'],
                            'status':control['status'],'runtime':control['runtime'],
                            'metrics':control['metrics'],'result_json':control['outputs']['result_json']}
    with input_path.open('rb') as handle:
        source_sha256=hashlib.file_digest(handle,'sha256').hexdigest()
    attribution={}
    for branch in ('emission','protected'):
        phases=['raw','linear_master',branch+'_stretch',branch+'_natural']
        attribution[branch]={'first_new_core_plateau':next((phase for phase in phases
            if any(band['plateau']['new_area_px']>0
                   for band in metrics[phase]['regions']['core']['channels'].values())),None),
            'first_display_core_clipping':next((phase for phase in phases[2:]
            if any(band['clip_ratio']>0
                   for band in metrics[phase]['regions']['core']['channels'].values())),None)}
    report={'runtime':runtime_info(__file__),'input':str(input_path),'input_sha256':source_sha256,
            'session':str(session),'linear_master':master,'linear_steps':linear_steps,
            'gradient_decision':dbe_decision,'gradient_evidence':gradient,
            'baseline_comparison_evidence':baseline_stretch.get('comparison_evidence'),
            'candidate_comparison_evidence':candidate_stretch.get('comparison_evidence'),
            'saturation_evidence':saturation_evidence(meta),'background_trials':trials,
            'selected_profile':chosen['id'],'safe_mtf_master':safe['natural']['outputs']['image'],
            'profile_comparison':str(output_dir/'profile_comparison.json'),
            'selection_is_acceptance':False,'production_defaults_changed':False,
            'entrypoints':entrypoints,'cli_command':command,'acceptance':acceptance,'controls':controls,
            'stage_attribution':attribution,
            'candidate_master':candidate_result['outputs']['image'],
            'agent_applied_master':agent_natural['artifact'],'enhanced':None,
            'status':'awaiting_visual_review' if acceptance['numeric_pass'] else 'quality_gain_unconfirmed',
            'outputs':{'full_comparison':str(output_dir/'full_comparison.jpg'),
                       'core_1to1':str(output_dir/'core_1to1.jpg'),
                       'regional_metrics':str(output_dir/'regional_metrics.json'),
                       'ringing_1to1':str(output_dir/'ringing_1to1.jpg'),
                       'native_ring_profiles':str(output_dir/'native_ring_profiles.json')}}
    write_json(output_dir/'regression.json',report)
    print(json.dumps({'report':str(output_dir/'regression.json'),'acceptance':acceptance},ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input');parser.add_argument('output_dir')
    parser.add_argument('--emission-control');parser.add_argument('--cluster-control')
    parser.add_argument('--second-cluster-control')
    args=parser.parse_args()
    run_regression(args.input,args.output_dir,args.emission_control,args.cluster_control,args.second_cluster_control)


if __name__=='__main__':
    main()
