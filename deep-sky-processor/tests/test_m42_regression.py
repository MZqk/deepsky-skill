"""M42 execution, provenance, stage and regional regression contracts."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pipeline import run_pipeline
from fits_io import read_image, write_image
from analyze import analyze_image
from agent_workflow import initialize_session, apply_action
from quality_metrics import calculate_metrics
from agent_protocol import evaluate_quality_gates
from regional_metrics import regional_metrics, region_masks, saturation_evidence
from runtime_info import runtime_info, source_fingerprint


def scene():
    yy, xx = np.mgrid[:96, :96]
    gray = 2e-5 + .3 * np.exp(-((xx-48)**2+(yy-48)**2)/80)
    return np.stack([gray, gray*.65, gray*.38], axis=-1).astype(np.float32)


def source_file(tmp_path, linear=True):
    source = tmp_path / 'source.tif'
    write_image(scene(), source)
    Path(str(source)+'.meta.json').write_text(json.dumps({'is_linear': linear}))
    return source


def test_color_rejected_before_any_pixel_operation(tmp_path):
    source = source_file(tmp_path, False)
    original = source.read_bytes()
    with patch('pipeline.remove_gradient', side_effect=AssertionError('Pixel mutation')), \
         patch('pipeline.emission_nebula_calibrate', side_effect=AssertionError('Pixel mutation')):
        with pytest.raises(ValueError, match='requires linear'):
            run_pipeline(str(source), str(tmp_path/'out.tif'), steps='dbe,color', preset='medium',
                         target_type='emission_nebula', target_name='M42', color_mode='emission')
    assert source.read_bytes() == original and not (tmp_path/'out.tif').exists()
    with pytest.raises(ValueError, match='sidecar'):
        run_pipeline(str(source), str(tmp_path/'forced.tif'), steps='color', input_is_linear=True)


def test_fits_sidecar_overrides_format_and_analysis_cannot_override(tmp_path):
    source = tmp_path/'processed.fits'
    fits.writeto(source, np.full((3,32,32), .2, dtype=np.float32))
    Path(str(source)+'.meta.json').write_text(json.dumps({'is_linear': False}))
    assert read_image(source)[1]['is_linear'] is False
    with pytest.raises(ValueError, match='sidecar'):
        analyze_image(str(source), input_is_linear=True)


def test_agent_rejects_false_stage_override(tmp_path):
    source = source_file(tmp_path)
    initialize_session(source, tmp_path/'session', target_type='emission_nebula', target_name='M42')
    apply_action(tmp_path/'session', {'operation':'run_step', 'step':'stretch', 'preset':'light',
                                    'auto_crop_edges':False, 'background_neutralize_lock':False})
    with pytest.raises(ValueError, match='recorded Agent'):
        apply_action(tmp_path/'session', {'operation':'run_step', 'step':'color',
                                         'params':{'input_is_linear':True}})


def test_quality_stage_nested_manifest_and_explicit_stage():
    metrics = calculate_metrics(scene(), {'effective_config':{'output_is_linear':True}})
    assert metrics['processing_stage'] == 'linear'
    assert not any(g['code'] == 'BACKGROUND_CRUSHED' for g in evaluate_quality_gates(metrics)[1])
    assert calculate_metrics(scene(), {'metrics':{'processing_stage':'linear'}})['processing_stage']=='linear'
    assert calculate_metrics(scene(), processing_stage='final')['processing_stage']=='final'


def test_quality_cli_uses_sidecar_and_rejects_unrelated_manifest(tmp_path):
    source = source_file(tmp_path)
    (tmp_path/'manifest.json').write_text(json.dumps({'output':str(tmp_path/'other.tif'),
                                                     'processing_stage':'final'}))
    script = Path(__file__).resolve().parents[1]/'scripts/quality_metrics.py'
    result = subprocess.run([sys.executable, '-B', str(script), str(source)], check=True, capture_output=True, text=True)
    payload=json.loads(result.stdout[result.stdout.index('{'):])
    assert payload['processing_stage']=='linear'
    assert 'Ignoring manifest' in result.stdout
    result=subprocess.run([sys.executable, '-B', str(script), str(source), '--stage','final'], check=True, capture_output=True,text=True)
    assert json.loads(result.stdout[result.stdout.index('{'):])['processing_stage']=='final'


def test_emission_reports_unused_protection_and_no_hdr(tmp_path):
    source=source_file(tmp_path)
    from stretch import render_protected_stretch
    safe=render_protected_stretch(scene(),'pi_mtf',{'target_bg':.1})[0]
    with patch('pipeline.apply_luminance_stretch', return_value=safe) as dispatch:
        result=run_pipeline(str(source),str(tmp_path/'out.tif'),steps='stretch',preset='medium',
                            target_type='emission_nebula',target_name='M42',stretch_method='emission',
                            override_params={'ghs_protect_strength':.75,'stretch_factor':44},
                            auto_crop_edges=False, background_neutralize_lock=False,keep_all=True,
                            work_dir=str(tmp_path/'work'))
    assert 'protect_strength' not in dispatch.call_args.kwargs
    assert 'factor' not in dispatch.call_args.kwargs
    assert result['hdr'] is None
    assert 'enhance' not in result['execution']['executed_steps']
    assert 'ghs_protect_strength' in result['execution']['unused_params']
    assert 'stretch_factor' in result['execution']['unused_params']
    assert 'target_bg' in result['execution']['used_params']['stretch']
    manifest=json.loads((tmp_path/'work/manifest.json').read_text())
    assert manifest['stretch_diagnostics']==result['stretch_diagnostics']
    assert manifest['execution']==result['execution']
    assert manifest['runtime']==result['runtime']
    assert result['runtime']['source_sha256']==source_fingerprint(Path(__file__).resolve().parents[1])


def test_m42_auto_three_entrypoints_and_provenance(tmp_path):
    source=source_file(tmp_path)
    common={'target_bg':.04}
    api=run_pipeline(str(source),str(tmp_path/'api.tif'),steps='stretch',preset='medium',
                     target_type='emission_nebula',target_name='M42',override_params=common,
                     auto_crop_edges=False,background_neutralize_lock=False)
    script=Path(__file__).resolve().parents[1]/'scripts/pipeline.py'
    subprocess.run([sys.executable,'-B',str(script),str(source),str(tmp_path/'cli.tif'),
                    '--steps','stretch','--strength','medium','--target-type','emission_nebula',
                    '--target-name','M42','--override-params',json.dumps(common),
                    '--no-auto-crop-edges','--no-background-neutralize','--result-json',str(tmp_path/'cli.json')],
                   check=True,capture_output=True)
    cli=json.loads((tmp_path/'cli.json').read_text())
    initialize_session(source,tmp_path/'session',target_type='emission_nebula',target_name='M42')
    agent=apply_action(tmp_path/'session',{'operation':'run_step','step':'stretch','preset':'medium',
                                        'params':common,'auto_crop_edges':False,
                                        'background_neutralize_lock':False})['result']['pipeline_result']
    for result in (api,cli,agent):
        assert result['effective_config']['stretch_method']=='masked_ghs_standard'
        assert result['runtime']['stretch_policy_version']==3
        assert result['execution']['used_params']['stretch']['target_bg']==common['target_bg']
        assert result['runtime']['source_sha256']==api['runtime']['source_sha256']
        np.testing.assert_array_equal(read_image(result['outputs']['image'])[0],read_image(api['outputs']['image'])[0])


def test_regional_subwhite_plateau_inheritance_and_input_saturation():
    source=scene()
    definitions={'core':{'center_xy':[48,48],'radius':20},'inner':{'center_xy':[48,48],'inner_radius':20,'radius':30},
                 'outer':{'center_xy':[48,48],'inner_radius':30,'radius':40},'background':{'bbox_xywh':[1,1,15,15]}}
    masks=region_masks(source.shape,definitions)
    candidate=source.copy(); candidate[40:56,40:56]=.3
    result=regional_metrics(candidate,source,masks)
    assert result['regions']['core']['plateau']['value']['new_area_px']>25
    inherited=regional_metrics(candidate,candidate,masks)
    assert not inherited['regions']['core']['plateau']['escalate']
    assert inherited['regions']['core']['plateau']['value']['inherited_area_px']>25
    evidence=saturation_evidence({'original_max':53291,'data_scale':53291,'header':{'BITPIX':16,'BZERO':32768}})
    assert evidence['sensor_saturation']=='suspected'
    with pytest.raises(ValueError,match='geometry'):
        regional_metrics(candidate,source,masks,np.ones((4,4)))


def test_comparison_does_not_accept_images_from_numbers_alone():
    from m42_regression import compare_acceptance
    region={'median':.1,'background_subtracted_median':.02,'relative_contrast':1.,
            'plateau':{'value':{'new_area_px':100}},
            'channels':{'r':{'clip_ratio':0.,'plateau':{'new_area_px':0}}}}
    baseline={'regions':{name:dict(region) for name in ('core','outer','background')}}
    candidate={'regions':{name:dict(region) for name in ('core','outer','background')},
               'channel_clipping':{'value':{'r':{'clip_ratio':0.}}}}
    candidate['regions']['core']={**region,'relative_contrast':1.2,'plateau':{'value':{'new_area_px':0}}}
    result=compare_acceptance(baseline,candidate,{'effective_config':{'stretch_method':'masked_ghs_standard'}})
    assert result['numeric_pass'] and result['visual_review']=='pending' and not result['accepted']
    candidate['regions']['outer']={**region,'background_subtracted_median':.01}
    result=compare_acceptance(baseline,candidate,{'effective_config':{'stretch_method':'masked_ghs_standard'}})
    assert not result['numeric_pass'] and not result['checks']['outer_signal_preserved']


def test_mixed_plateau_counts_and_ringing_warning_require_review():
    from artifact_gates import check_core_plateau
    from m42_regression import compare_acceptance
    yy,xx=np.mgrid[:32,:32]
    source=(.4+.001*(xx+yy)).astype(np.float32)
    source[8:24,8:16]=.6
    candidate=source.copy();candidate[8:24,8:24]=.6
    gate=check_core_plateau(candidate,source,minimum_level=.5)
    value=gate['value'];regions=value['new_regions']+value['inherited_regions']
    assert value['new_area_px']==sum(r['new_area_px'] for r in regions)
    assert value['inherited_area_px']>0
    assert value['new_area_px']+value['inherited_area_px']==sum(r['area_px'] for r in regions)
    region={'median':.1,'background_subtracted_median':.02,'relative_contrast':1.,
            'plateau':{'value':{'new_area_px':0}},
            'channels':{'r':{'clip_ratio':0.,'plateau':{'new_area_px':100}}}}
    baseline={'regions':{name:dict(region) for name in ('core','outer','background')}}
    clean={**region,'channels':{'r':{'clip_ratio':0.,'plateau':{'new_area_px':0}}}}
    updated={'regions':{name:dict(clean) for name in ('core','outer','background')},
             'channel_clipping':{'value':{'r':{'clip_ratio':0.}}}}
    result=compare_acceptance(baseline,updated,{
        'effective_config':{'stretch_method':'masked_ghs_standard'},
        'stretch_diagnostics':{'display_gates':{'gates':[
            {'code':'STAR_RINGING','status':'warning','value':{'n_ring':1}}]}}})
    assert result['core_plateau_reduced'] and result['checks']['core_structure_improved']
    assert not result['checks']['no_new_dark_rings'] and not result['numeric_pass']


def test_platform_reduction_does_not_hide_loss_against_safe_structure():
    from copy import deepcopy
    from m42_regression import compare_acceptance
    region={'median':.1,'background_subtracted_median':.02,'relative_contrast':1.,
            'mean_gradient_sigma2':.01, 'plateau':{'value':{'new_area_px':10}},
            'channels':{'r':{'clip_ratio':0., 'plateau':{'new_area_px':0}}}}
    baseline={'regions':{name:deepcopy(region) for name in ('background','outer','core')},
              'channel_clipping':{'value':{'r':{'clip_ratio':0.}}}}
    candidate=deepcopy(baseline)
    candidate['regions']['core'].update(relative_contrast=.4, mean_gradient_sigma2=.003,
                                         plateau={'value':{'new_area_px':0}})
    for data in (baseline, candidate):
        for roi in data['regions'].values():
            roi['plateau']['value']['new_area_px']=0
    baseline['regions']['core']['plateau']['value']['new_area_px']=10
    result=compare_acceptance(baseline,candidate,
                              {'effective_config':{'stretch_method':'masked_ghs_standard'}},baseline)
    assert result['checks']['core_structure_improved']
    assert not result['checks']['safe_core_structure_preserved'] and not result['numeric_pass']
