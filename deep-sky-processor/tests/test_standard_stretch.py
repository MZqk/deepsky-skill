"""Formula, domain and compatibility regressions for protected stretching."""
from pathlib import Path
import sys
from unittest.mock import patch
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stretch import ghs_standard_stretch, render_protected_stretch, ghs_stretch
from target_rules import recommend_stretch
from artifact_gates import check_core_plateau, evaluate_star_artifact_gates
from fits_io import read_image, write_image
from pipeline import run_pipeline
from agent_workflow import initialize_session, apply_action


@pytest.mark.parametrize('b', [-5, -1, -.5, 0, 1, 8, 15])
def test_standard_curve_identity_boundaries_monotonicity_and_tangents(b):
    x = np.linspace(0, 1, 4001, dtype=np.float32)
    assert np.array_equal(ghs_standard_stretch(x, 0, b, .1, .02, .8), x)
    out = ghs_standard_stretch(x, 2, b, .1, .02, .8)
    assert out.dtype == np.float32 and np.isfinite(out).all()
    assert out[0] == 0 and out[-1] == 1
    assert np.min(np.diff(out)) >= -1e-7
    assert np.max(np.abs(np.diff(out[x < .019], n=2))) < 1e-7
    assert np.max(np.abs(np.diff(out[x > .81], n=2))) < 2e-7
    eps = 1e-5
    for boundary in (.02, .1, .8):
        three = ghs_standard_stretch(np.array([boundary-eps, boundary, boundary+eps]), 2, b, .1, .02, .8)
        assert abs(float(three[1]-three[0])-float(three[2]-three[1])) < 2e-6


@pytest.mark.parametrize('b', [0, 1, -1, -2, 8])
def test_standard_against_closed_form_author_equations(b):
    x = np.linspace(0, 1, 501, dtype=np.float64)
    d = np.expm1(2.)
    if b == 0:
        expected = -np.expm1(-d*x) / -np.expm1(-d)
    elif b == -1:
        expected = np.log1p(d*x) / np.log1p(d)
    elif b == -2:
        expected = (np.sqrt(1+2*d*x)-1) / (np.sqrt(1+2*d)-1)
    elif b == 1:
        m = 1/(d+2)
        expected = (m-1)*x/((2*m-1)*x-m)
    else:
        expected = (1-(1+b*d*x)**(-1/b))/(1-(1+b*d)**(-1/b))
    assert np.allclose(ghs_standard_stretch(x, 2., b, 0, 0, 1), expected, atol=8e-8)


def scene(size=96):
    yy, xx = np.mgrid[:size, :size]
    y = 2e-5 + .3*np.exp(-((xx-size/2)**2+(yy-size/2)**2)/80)
    return np.stack([y, y*.65, y*.38], axis=-1).astype(np.float32)


@pytest.mark.parametrize('method', ['pi_mtf', 'ghs_standard', 'masked_ghs_standard'])
def test_rgb_extreme_dark_bright_core_and_background_fit(method):
    source = scene()
    out, report = render_protected_stretch(source, method, {'target_bg': .035})
    assert out.dtype == np.float32 and np.isfinite(out).all()
    assert (out > 0).all() and out.max() <= .950001
    assert np.allclose(out[..., 0]/out[..., 1], source[..., 0]/source[..., 1], rtol=2e-6)
    assert np.allclose(out[..., 2]/out[..., 1], source[..., 2]/source[..., 1], rtol=2e-6)
    assert report['background_target_reached']
    assert report.get('fit_iterations', 0) <= 24
    assert not check_core_plateau(out, source)['escalate']


@pytest.mark.parametrize('params', [dict(log_d=float('nan')), dict(b=float('inf')), dict(lp=.2, sp=.1), dict(hp=.01, sp=.1), dict(log_d=-1), dict(b=16)])
def test_invalid_standard_parameters(params):
    with pytest.raises(ValueError):
        ghs_standard_stretch(np.array([.01, .5]), **params)


def test_legacy_sinh_is_unchanged():
    x = np.linspace(0, 1, 100, dtype=np.float32)
    expected = (np.sinh(8*(x-.01))-np.sinh(-.08))/(np.sinh(7.92)-np.sinh(-.08))
    assert np.allclose(ghs_stretch(x, .01, 8), np.clip(expected, 0, 1), rtol=1e-7, atol=1e-12)


@pytest.mark.parametrize('kind,name,color,method', [
    ('galaxy','M31','emission','masked_ghs_standard'),
    ('emission_nebula','M42','emission','masked_ghs_standard'),
    ('planetary_nebula',None,'emission','masked_ghs_standard'),
    ('emission_nebula','C50','emission','emission'),
    ('open_cluster','M45','emission','pi_mtf'),
    ('reflection_nebula',None,'emission','arcsinh'),
    ('dark_nebula',None,'emission','arcsinh'),
    (None,None,'standard','pi_mtf')])
def test_target_policy(kind, name, color, method):
    assert recommend_stretch(kind, name, color)[0] == method


def test_emission_honors_stretch_headroom_and_reports_it_consumed(tmp_path):
    """emission 必须把 stretch_headroom 传进算法，且三条 unused 通道都不再报它。

    缺 headroom 时 emission 峰值触顶 → R 通道成片裁切 → CHANNEL_CLIPPING 判
    failed → 整条链回退 pi_mtf，首选方法永远用不上（实测同一数据 R 裁切
    0.55% → 0.00%）。

    `unused_params` 有两条独立通道（硬编码语法扫描 + 按方法的白名单差集），
    加上全局 `execution['unused_params']` 共三条；只改传参会被另外两条
    仍然报为「未使用」。

    输入亮度需足够：极暗合成图会先触发 masked_stretch 归一化并被门禁回退，
    `executed_steps` 为空时三条通道都无从验证。
    """
    yy, xx = np.mgrid[0:96, 0:96]
    base = .05 + .25 * np.exp(-((xx - 48) ** 2 + (yy - 48) ** 2) / 300.)
    source = np.stack([base * 1.2, base * .8, base * .9], axis=-1).clip(0, 1).astype(np.float32)
    path = tmp_path / 'source.tif'
    write_image(source, path)
    result = run_pipeline(
        str(path), str(tmp_path / 'out.tif'), steps='stretch', preset='light',
        target_type='emission_nebula', target_name='C50', stretch_method='emission',
        override_params={'target_bg': 0.10, 'stretch_headroom': 0.05},
        input_is_linear=True, auto_crop_edges=False, recognize=False,
        background_neutralize_lock=False,
    )
    # emission 走 legacy 分支，不设 applied_method（那是 new_stretch_path 的
    # 字段）；用「stretch 确实执行了」+「参数已消费」两条共同证明按 emission 出图。
    assert result['execution']['executed_steps'] == ['stretch']
    assert result['stretch_diagnostics']['selected_method'] == 'emission'
    assert 'stretch_headroom' not in result['execution']['unused_params']
    assert 'stretch_headroom' not in result['stretch_diagnostics']['unused_params']
    assert result['execution']['used_params']['stretch']['stretch_headroom'] == 0.05


def test_emission_default_headroom_matches_protected_path():
    """emission 与 pi_mtf 必须用同一份高光余量预算（默认 0.05）。

    否则 emission 能过裁切门禁只是因为它比 pi_mtf 更狠，那是架空门禁。
    """
    from stretch import emission_stretch, render_protected_stretch

    image = scene()
    emission = emission_stretch(image, shadow_pctl=0.0, target_bg=0.10, headroom=0.05)
    protected = render_protected_stretch(
        image, 'pi_mtf', {'target_bg': 0.10, 'stretch_headroom': 0.05}
    )[0]
    assert float(emission.max()) <= 0.95 + 1e-6
    assert float(protected.max()) <= 0.95 + 1e-6


def test_float_plateau_below_clip_and_inherited_plateau():
    source = scene()
    out = source.copy()
    out[40:56,40:56] = .8
    gate = check_core_plateau(out, source, np.ones(source.shape[:2]))
    assert gate['escalate'] and gate['value']['new_regions']
    assert not check_core_plateau(out, out, np.ones(source.shape[:2]))['escalate']


def test_cross_domain_does_not_call_paired_physical_measurement():
    image = scene()
    with patch('artifact_gates.check_star_bloat', side_effect=AssertionError('cross-domain call')):
        result = evaluate_star_artifact_gates(image, image**.4, reference_comparable=False)
    assert next(g for g in result['gates'] if g['code']=='STAR_BLOAT')['status'] == 'skipped'


def test_failed_new_stretch_restores_input_and_domain(tmp_path):
    source = scene()
    path = tmp_path/'source.tif'
    write_image(source, path)
    bad = np.full_like(source, .8)
    with patch('pipeline.render_protected_stretch', return_value=(bad, {'algorithm':'linked_mtf'})):
        result = run_pipeline(str(path), str(tmp_path/'out.tif'), steps='stretch,style', preset='light',
                              input_is_linear=True, auto_crop_edges=False, recognize=False,
                              background_neutralize_lock=False, keep_all=True)
    out, _ = read_image(str(tmp_path/'out.tif'))
    assert np.array_equal(out, source)
    assert result['status']=='review_required' and result['effective_config']['output_is_linear']
    assert not result['effective_config']['steps']
    assert result['stretch_diagnostics']['skipped_after_failure'] == ['stretch','style']


def test_new_and_missing_version_agent_sessions(tmp_path):
    path=tmp_path/'source.tif'
    write_image(scene(), path)
    state=initialize_session(str(path), tmp_path/'session', target_type='galaxy', target_name='M31')
    assert state['stretch_policy_version']==3
    result=apply_action(tmp_path/'session', {'operation':'run_step','step':'stretch','params':{'input_is_linear':True},
                       'auto_crop_edges':False, 'background_neutralize_lock':False})['result']
    assert result['pipeline_result']['effective_config']['stretch_method']=='masked_ghs_standard'
    assert result['review']['star_artifact_gates']['context']['measurement_domain']=='display'
    assert result['review']['star_artifact_gates']['context']['reference_type']=='fixed_mtf'
    import json
    apply_action(tmp_path/'session', {'operation':'rollback', 'artifact':str(path)})
    state_path=tmp_path/'session/session.json'
    state=json.loads(state_path.read_text());state.pop('stretch_policy_version')
    state_path.write_text(json.dumps(state))
    result=apply_action(tmp_path/'session', {'operation':'run_step','step':'stretch','params':{'input_is_linear':True},
                       'auto_crop_edges':False,'background_neutralize_lock':False})['result']
    assert result['pipeline_result']['stretch_diagnostics']['policy_version']==1
    assert result['pipeline_result']['effective_config']['stretch_method'] not in ('ghs_standard','masked_ghs_standard','pi_mtf')


@pytest.mark.parametrize('method', ['masked_ghs_standard', 'pi_mtf'])
def test_protected_three_entrypoints_explicit_overrides(tmp_path, method):
    import json
    import subprocess
    source=tmp_path/'source.tif'
    write_image(scene(), source)
    Path(str(source)+'.meta.json').write_text(json.dumps({'is_linear':True}))
    params={'target_bg':.04,'ghs_standard_b':5.,'ghs_b':6.,'stretch_factor':64.}
    direct=run_pipeline(str(source),str(tmp_path/'api.tif'),steps='stretch',preset='adaptive',
                        target_type='galaxy',target_name='M31',stretch_method=method,override_params=params,
                        auto_crop_edges=False,background_neutralize_lock=False)
    result_path=tmp_path/'cli.json'
    subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/pipeline.py'),
                    str(source),str(tmp_path/'cli.tif'),'--steps','stretch','--strength','adaptive',
                    '--target-type','galaxy','--target-name','M31','--stretch-method',method,
                    '--override-params',json.dumps(params),'--no-auto-crop-edges','--no-background-neutralize',
                    '--result-json',str(result_path)],check=True,capture_output=True)
    cli=json.loads(result_path.read_text())
    initialize_session(source,tmp_path/'session',target_type='galaxy',target_name='M31')
    agent=apply_action(tmp_path/'session',{'operation':'run_step','step':'stretch','stretch_method':method,
                      'params':params,'auto_crop_edges':False,'background_neutralize_lock':False})['result']['pipeline_result']
    expected,_=read_image(str(tmp_path/'api.tif'))
    for result in (cli,agent):
        actual,_=read_image(result['outputs']['image'])
        assert np.array_equal(actual,expected)
        assert result['effective_config']['stretch_method']==method
        for key in ('algorithm','actual_background','target_bg'):
            assert result['stretch_diagnostics'][key]==direct['stretch_diagnostics'][key]
        assert result['effective_config']['params']['stretch_factor']==96.
    assert direct['stretch_diagnostics']['target_bg']==.04
    assert 'ghs_b' in direct['stretch_diagnostics']['unused_legacy_params']
    if method == 'pi_mtf':
        assert 'ghs_standard_b' in direct['stretch_diagnostics']['unused_standard_params']


def test_invalid_display_reference_requires_review(tmp_path):
    from agent_protocol import create_review_bundle
    source = tmp_path/'source.tif'
    after = tmp_path/'after.tif'
    write_image(scene(), source)
    write_image(scene()**.4, after)
    result = create_review_bundle(source, after, tmp_path/'review',
        {'steps':['stretch'], 'input_is_linear':True, 'output_is_linear':False,
         'stretch_diagnostics':{'display_reference':{'path':str(after), 'source_sha256':'invalid'}}})
    assert result['status'] == 'review_required'
    assert result['star_artifact_gates']['status'] == 'error'


def test_failed_stretch_restores_separated_layer_state(tmp_path):
    source, external = tmp_path/'source.tif', tmp_path/'external.tif'
    original = scene()
    write_image(original, source)
    write_image(original*.8, external)
    separated = run_pipeline(str(source), str(tmp_path/'separated.tif'), steps='star_remove',
        input_is_linear=True, external_starless=str(external), auto_crop_edges=False, recognize=False)
    bad = np.full_like(original, .8)
    with (patch('pipeline.render_protected_stretch', return_value=(bad, {'algorithm':'linked_mtf'})),
          patch('pipeline.background_compressed_stretch', return_value=(bad, {
              'algorithm': 'background_compressed', 'method': 'background_compressed',
              'actual_background': .8}))):
        rejected = run_pipeline(separated['outputs']['image'], str(tmp_path/'rejected.tif'),
            steps='stretch,star_combine', star_layers=separated['star_layers'],
            auto_crop_edges=False, background_neutralize_lock=False, recognize=False)
    assert rejected['star_layers']['domain'] == 'linear'
    assert rejected['star_layers']['processed_stars'] is None
    for key in ('source', 'stars'):
        assert np.array_equal(read_image(rejected['star_layers'][key])[0], read_image(separated['star_layers'][key])[0])
    assert np.array_equal(read_image(rejected['outputs']['image'])[0], read_image(separated['outputs']['image'])[0])


def test_standard_fit_finds_first_root_when_endpoint_background_falls():
    y = np.linspace(.025, .1, 96*96, dtype=np.float32).reshape(96,96)
    y[-1,-1] = 1
    image = np.repeat(y[...,None], 3, axis=2)
    out, report = render_protected_stretch(image, 'ghs_standard', {'target_bg':.05})
    assert report['background_target_reached']
    assert 0 < report['log_d'] < 2
    assert report['fit_iterations'] <= 24
    assert np.isfinite(out).all()


def test_display_stretch_insufficient_stars_retains_review():
    image = scene()
    result = evaluate_star_artifact_gates(image, image, steps=['stretch'])
    gate = next(g for g in result['gates'] if g['code']=='STAR_RINGING')
    assert gate['evidence']['insufficient_samples']
    assert result['status'] == 'review_required'


def test_display_reference_does_not_attribute_signal_loss_to_curve_brightness():
    image = scene()
    result = evaluate_star_artifact_gates(image, image*.4, steps=['stretch'], display_reference=True)
    loss = next(g for g in result['gates'] if g['code']=='STAR_LAYER_LOSS')
    assert loss['status']=='skipped' and not loss['escalate']


def test_display_width_failure_retries_once_with_mtf(tmp_path):
    source=tmp_path/'source.tif'
    original=scene()
    write_image(original,source)
    failed={'schema':'artifact_gates/1.0','status':'review_required', 'context':{},
            'gates':[{'code':'STAR_BLOAT','status':'failed','escalate':True}]}
    passed={'schema':'artifact_gates/1.0','status':'success', 'context':{},
            'gates':[{'code':'STAR_BLOAT','status':'passed','escalate':False}]}
    with patch('artifact_gates.evaluate_star_artifact_gates',side_effect=[failed,passed]) as gates:
        result=run_pipeline(str(source),str(tmp_path/'output.tif'),steps='stretch',
            stretch_method='ghs_standard', input_is_linear=True, recognize=False,
            auto_crop_edges=False,background_neutralize_lock=False,
            override_params={'target_bg':.05},keep_all=True)
    assert gates.call_count==2
    assert result['stretch_recovery']=='conservative_pi_mtf'
    assert result['effective_config']['stretch_method']=='pi_mtf'
    assert result['stretch_diagnostics']['rejected_initial']['display_gates']['status']=='review_required'
    expected,_=render_protected_stretch(original,'pi_mtf',{'target_bg':.05})
    assert np.array_equal(read_image(tmp_path/'output.tif')[0],expected)


def test_linear_width_measurement_accepts_grayscale_review(tmp_path):
    from agent_protocol import create_review_bundle
    before=tmp_path/'before.tif';after=tmp_path/'after.tif'
    gray=scene().mean(axis=2)
    write_image(gray,before)
    write_image(gray**.4,after)
    result=create_review_bundle(before,after,tmp_path/'review',
        {'steps':['stretch'],'input_is_linear':True,'output_is_linear':False})
    assert result['linear_star_measurement']['domain']=='linear'
    assert result['linear_star_measurement']['status']=='insufficient_samples'


def test_zero_signal_still_validates_explicit_standard_parameters():
    zero=np.zeros((16,16,3),np.float32)
    with pytest.raises(ValueError):
        render_protected_stretch(zero,'ghs_standard',{'ghs_standard_b':float('nan')})
    out,report=render_protected_stretch(zero,'ghs_standard')
    assert np.array_equal(out,zero)
    assert report['background_reason']=='no_positive_signal'


def test_regional_curve_diagnostics_do_not_change_rendered_pixels():
    source = scene()
    mask = np.zeros(source.shape[:2], dtype=bool)
    mask[40:56,40:56] = True
    cfg = {'target_bg': .035, 'ghs_protect_strength': .35}
    expected, _ = render_protected_stretch(source, 'masked_ghs_standard', cfg)
    actual, report = render_protected_stretch(source, 'masked_ghs_standard', cfg,
                                              diagnostic_regions={'core': mask})
    assert np.array_equal(actual, expected)
    core = report['regional_diagnostics']['core']
    assert 0 < core['protection_mask']['p90'] <= .350001
    assert core['after_shoulder']['median'] <= core['mixed_luminance']['median']
    with pytest.raises(ValueError, match='geometry'):
        render_protected_stretch(source, 'pi_mtf', cfg, diagnostic_regions={'wrong': mask[:10]})


def test_unattributed_display_rings_keep_candidate_pending_without_algorithm_fallback(tmp_path):
    source = tmp_path/'linear.tif'
    write_image(scene(), source)
    ring = {'code': 'STAR_RINGING', 'status': 'failed', 'escalate': True,
            'message': 'Suspected rings; independent display curves',
            'value': {'n_ring': 2}, 'evidence': {'reference_comparable': False}}
    display = {'status': 'review_required', 'gates': [ring], 'context': {}}
    with patch('artifact_gates.evaluate_star_artifact_gates', return_value=display):
        result = run_pipeline(str(source),str(tmp_path/'out.tif'),steps='stretch',preset='light',
                              target_name='M42',target_type='emission_nebula',input_is_linear=True,
                              override_params={'target_bg':.035},auto_crop_edges=False,
                              background_neutralize_lock=False)
    assert result['effective_config']['stretch_method']=='masked_ghs_standard'
    assert result['status']=='review_required'
    assert any(issue['code']=='STAR_RINGING' for issue in result['quality_state']['issues'])
