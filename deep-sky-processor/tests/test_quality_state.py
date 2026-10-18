"""Branch quality inheritance, artifact-bound review and sparse-star regressions."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from fits_io import write_image
from pipeline import run_pipeline
from agent_workflow import initialize_session, apply_action, apply_review, load_state, artifact_quality
from agent_protocol import write_json_atomic
from quality_state import (build_quality_state, persist_quality_state, read_quality_state,
                           resolve_quality_state, artifact_digest)
from stretch import is_stretch_collapsed
from artifact_gates import evaluate_star_artifact_gates


def source_file(tmp_path):
    path = tmp_path / 'source.tif'
    write_image(np.full((64, 64, 3), .1, dtype=np.float32), path)
    write_json_atomic({'is_linear': False}, str(path) + '.meta.json')
    return path


def issue_state(path, status='warning'):
    return build_quality_state(path, step='stretch', gates=[
        {'code': 'CORE_TEST', 'status': status, 'escalate': True}])


def fake_review(before, after, directory, context=None):
    report = {'status': 'success', 'quality_gates': [],
              'source': {'before': str(before), 'after': str(after)},
              'report_path': str(Path(directory) / 'review.json')}
    write_json_atomic(report, report['report_path'])
    return report


def test_shared_pipeline_keeps_prior_issues_and_binds_sidecar(tmp_path):
    source = source_file(tmp_path)
    parent = issue_state(source)
    persist_quality_state(source, parent)
    output = tmp_path / 'final.tif'
    result = run_pipeline(str(source), str(output), steps='final_color', preset='light',
                          auto_crop_edges=False, background_neutralize_lock=False,
                          keep_all=True, work_dir=str(tmp_path / 'work'))
    assert result['status'] == 'review_required'
    assert result['quality_state']['issues'][0]['id'] == parent['issues'][0]['id']
    assert result['quality_state']['issues'][0]['origin_step'] == 'stretch'
    assert read_quality_state(output) == result['quality_state']
    assert json.loads(Path(result['outputs']['manifest']).read_text())['quality_state'] == result['quality_state']
    output.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        read_quality_state(output)


def test_accept_rejects_foreign_changed_and_unresolved_artifacts(tmp_path):
    source = source_file(tmp_path)
    session = tmp_path / 'session'
    initialize_session(source, session)
    foreign = tmp_path / 'foreign.tif'
    write_image(np.ones((32, 32), dtype=np.float32), foreign)
    with pytest.raises(ValueError, match='belong'):
        apply_action(session, {'operation': 'accept', 'artifact': str(foreign)})
    state, path = load_state(session)
    state['artifact_state'][str(source)]['quality_state'] = issue_state(source)
    write_json_atomic(state, path)
    with pytest.raises(ValueError, match='unresolved'):
        apply_review(session, {'verdict': 'accept'})
    assert load_state(session)[0]['accepted_artifact'] is None
    write_image(np.full((64, 64, 3), .2, dtype=np.float32), source)
    with pytest.raises(ValueError, match='changed'):
        apply_action(session, {'operation': 'accept'})


def test_explicit_resolution_requires_identity_reason_and_evidence(tmp_path):
    source = source_file(tmp_path)
    state = issue_state(source, 'failed')
    issue_id = state['issues'][0]['id']
    evidence = tmp_path / 'check.json'
    resolution = {'issue_id': issue_id, 'outcome': 'fixed', 'reason': 'Rechecked current pixels',
                  'evidence': [str(evidence)]}
    review = {'artifact': str(source), 'artifact_sha256': artifact_digest(source), 'resolutions': [resolution]}
    evidence.write_text(json.dumps({'artifact_sha256': artifact_digest(source),
                                   'quality_gates': [{'code': 'CORE_TEST', 'status': 'failed'}]}))
    with pytest.raises(ValueError, match='passing check'):
        resolve_quality_state(source, state, review)
    evidence.write_text(json.dumps({'artifact_sha256': artifact_digest(source),
                                   'quality_gates': [{'code': 'CORE_TEST', 'status': 'passed'}]}))
    resolved = resolve_quality_state(source, state, review)
    assert resolved['issues'] == [] and resolved['resolutions'][0]['issue_id'] == issue_id
    with pytest.raises(ValueError, match='SHA-256'):
        resolve_quality_state(source, state, {**review, 'artifact_sha256': 'old'})
    evidence.write_text(json.dumps({'artifact_sha256': artifact_digest(source),
                                   'issue_id': issue_id, 'outcome': 'inherited'}))
    inherited = {**review, 'resolutions': [{**resolution, 'outcome': 'inherited'}]}
    assert not resolve_quality_state(source, state, inherited)['issues']


def test_variants_inherit_independently_and_rollback_restores_pending(tmp_path):
    source = source_file(tmp_path)
    session = tmp_path / 'session'
    parent = issue_state(source)
    persist_quality_state(source, parent)
    initialize_session(source, session)
    with patch('agent_workflow.create_review_bundle', side_effect=fake_review):
        event = apply_action(session, {'operation': 'create_variants', 'step': 'final_color',
                                     'preset': 'light', 'auto_crop_edges': False,
                                     'background_neutralize_lock': False,
                                     'variants': [{'id': 'a'}, {'id': 'b'}]})
    candidates = event['result']['candidates']
    a, b = [candidate['artifact'] for candidate in candidates]
    state, _ = load_state(session)
    qa, qb = [state['artifact_state'][path]['quality_state'] for path in (a, b)]
    assert qa['parent_artifact'] == qb['parent_artifact'] == str(source)
    assert qa['issues'][0]['id'] == qb['issues'][0]['id'] == parent['issues'][0]['id']
    evidence = tmp_path / 'inspection.txt'
    evidence.write_text('Native-pixel inspection confirms an inherited diagnostic finding.')
    review = {'verdict': 'accept', 'artifact': a, 'artifact_sha256': qa['artifact_sha256'],
              'resolutions': [{'issue_id': issue['id'], 'outcome': 'false_positive',
                               'reason': 'Reviewed native evidence', 'evidence': [str(evidence)]}
                              for issue in qa['issues']]}
    apply_review(session, review)
    state, _ = load_state(session)
    assert not state['artifact_state'][a]['quality_state']['issues']
    assert state['artifact_state'][b]['quality_state']['issues']
    apply_review(session, {'verdict': 'rollback', 'artifact': b})
    state, _ = load_state(session)
    assert state['current_artifact'] == b and state['pending_review']['issues']


def test_old_session_recovers_history_and_missing_evidence_stays_pending(tmp_path):
    source = source_file(tmp_path)
    session = tmp_path / 'session'
    initialize_session(source, session)
    old = tmp_path / 'old.tif'
    unknown = tmp_path / 'unknown.tif'
    for path in (old, unknown):
        write_image(np.full((64, 64, 3), .1, dtype=np.float32), path)
    state, state_path = load_state(session)
    state.pop('stretch_policy_version')
    state['artifact_state'][str(old)] = {'is_linear': False}
    state['artifact_state'][str(unknown)] = {'is_linear': False}
    state['history'] = [{'operation': 'run_step', 'action': {'step': 'stretch'}, 'result': {
        'artifact': str(old), 'pipeline_result': {'input': str(source), 'status': 'review_required',
        'quality_gates': [{'code': 'OLD_DEFECT', 'status': 'failed'}]}}}]
    write_json_atomic(state, state_path)
    assert artifact_quality(state, str(old))['issues'][0]['code'] == 'OLD_DEFECT'
    assert artifact_quality(state, str(unknown))['issues'][0]['code'] == 'LEGACY_QUALITY_UNKNOWN'
    assert 'stretch_policy_version' not in state


def test_sparse_cluster_uses_stellar_percentile_without_weakening_thresholds():
    before = np.full((100, 100, 3), .2865, dtype=np.float32)
    after = np.full_like(before, .0503)
    before[:1, :40] = .607
    after[:1, :40] = .171
    assert is_stretch_collapsed(before, after)[0]
    collapsed, hb, ha = is_stretch_collapsed(before, after, stellar_subject=True)
    assert not collapsed and ha['span_percentile'] == 99.9
    assert ha['span'] >= max(.02, hb['span']*.25)
    assert is_stretch_collapsed(before, np.full_like(before, .1), stellar_subject=True)[0]


def test_independent_display_curves_cannot_attribute_paired_ring_depth():
    image = np.full((128, 128, 3), .05, dtype=np.float32)
    with patch('artifact_gates.check_ringing', return_value={
        'code': 'STAR_RINGING', 'status': 'passed', 'escalate': False,
        'value': {'n_sampled': 20}, 'evidence': {}}) as ring:
        evaluate_star_artifact_gates(image, image**.5, display_reference=True)
    assert ring.call_args.kwargs['reference_comparable'] is False


def test_native_profiles_retain_per_channel_deficits_and_domain(tmp_path):
    from regional_metrics import native_ring_profiles
    yy, xx = np.mgrid[:160, :160]
    radius = np.hypot(xx-80, yy-80)
    clean = .1 + .5*np.exp(-radius**2/4)
    image = np.repeat(clean[...,None], 3, axis=2).astype(np.float32)
    ringed = image.copy()
    ringed[...,2] -= .04*np.exp(-((radius-6)/2)**2)
    report = native_ring_profiles({'input': image, 'candidate': ringed}, (80,80), 3)
    assert report['attribution'] == 'unconfirmed'
    blue = report['profiles']['candidate']['b']
    red = report['profiles']['candidate']['r']
    assert blue['minimum_ring_relative_residual'] < -.12
    assert red['minimum_ring_relative_residual'] > -.12


def test_identical_display_pixels_remain_comparable():
    image = np.full((128, 128, 3), .05, dtype=np.float32)
    with patch('artifact_gates.check_ringing', return_value={
        'code': 'STAR_RINGING', 'status': 'passed', 'escalate': False,
        'value': {'n_sampled': 20}, 'evidence': {}}) as ring:
        result = evaluate_star_artifact_gates(image, image.copy(), display_reference=True)
    assert ring.call_args.kwargs['reference_comparable'] is True
    assert result['gates'][0]['evidence']['attribution'] == 'identical_display_pixels'


def test_legacy_missing_artifact_map_recovers_membership_but_not_quality(tmp_path):
    source = source_file(tmp_path)
    session = tmp_path / 'session'
    initialize_session(source, session)
    old = tmp_path / 'old.tif'
    write_image(np.full((64,64,3), .1, dtype=np.float32), old)
    state, state_path = load_state(session)
    state.pop('artifact_state')
    state['current_artifact'] = str(old)
    state['history'] = [{'operation': 'run_step', 'action': {'step': 'color'}, 'result': {
        'artifact': str(old), 'pipeline_result': {'input': str(source), 'status': 'success',
        'effective_config': {'output_is_linear': True}}}}]
    write_json_atomic(state, state_path)
    recovered, _ = load_state(session)
    assert recovered['artifact_state'][str(old)]['is_linear'] is True
    assert any(issue['code']=='LEGACY_QUALITY_UNKNOWN' for issue in artifact_quality(recovered, str(old))['issues'])
    with pytest.raises(ValueError, match='unresolved'):
        apply_action(session, {'operation': 'accept'})


def test_explicit_pending_review_is_inherited_and_cannot_be_silently_accepted(tmp_path):
    source = source_file(tmp_path)
    session = tmp_path/'session'
    initialize_session(source,session)
    apply_review(session, {'verdict': 'review_required', 'reason': 'Need native evidence'})
    with pytest.raises(ValueError, match='unresolved'):
        apply_action(session, {'operation': 'accept'})
    with patch('agent_workflow.create_review_bundle', side_effect=fake_review):
        result=apply_action(session, {'operation':'run_step','step':'final_color','preset':'light',
                                     'auto_crop_edges':False,'background_neutralize_lock':False})['result']
    assert any(issue['code']=='AGENT_REVIEW_PENDING' for issue in result['review']['quality_state']['issues'])
    with pytest.raises(ValueError, match='Only an accept verdict'):
        apply_review(session, {'verdict':'review_required', 'actions':[{'operation':'accept'}]})


def test_pending_variant_review_does_not_poison_its_parent(tmp_path):
    source=source_file(tmp_path)
    session=tmp_path/'session'
    initialize_session(source,session)
    child=tmp_path/'variant.tif'
    write_image(np.full((64,64,3),.1,dtype=np.float32),child)
    state,state_path=load_state(session)
    state['artifact_state'][str(child)]={'quality_state':build_quality_state(child,source)}
    write_json_atomic(state,state_path)
    with pytest.raises(ValueError,match='SHA-256'):
        apply_review(session,{'verdict':'review_required','artifact':str(child),'artifact_sha256':'stale'})
    apply_review(session,{'verdict':'review_required','artifact':str(child),
                         'artifact_sha256':artifact_digest(child)})
    state,_=load_state(session)
    assert not state['artifact_state'][str(source)]['quality_state']['issues']
    assert state['artifact_state'][str(child)]['quality_state']['issues'][0]['code']=='AGENT_REVIEW_PENDING'


def test_legacy_passed_checks_without_file_identity_still_require_review(tmp_path):
    source=source_file(tmp_path)
    session=tmp_path/'session'
    initialize_session(source,session)
    old=tmp_path/'old.tif'
    write_image(np.full((64,64,3),.1,dtype=np.float32),old)
    state,path=load_state(session)
    state['artifact_state'][str(old)]={}
    state['current_artifact']=str(old)
    state['history']=[{'operation':'run_step','action':{'step':'color'},'result':{
        'artifact':str(old),'pipeline_result':{'input':str(source),'status':'success','quality_gates':[]}}}]
    write_json_atomic(state,path)
    with pytest.raises(ValueError,match='unresolved'):
        apply_action(session,{'operation':'accept'})
    assert artifact_quality(state,str(old))['issues'][0]['code']=='LEGACY_ARTIFACT_IDENTITY_UNKNOWN'
