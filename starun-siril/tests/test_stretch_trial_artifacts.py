"""Stage 7 trial copies, retention, authority boundaries and immutable history."""
import copy
import json
from pathlib import Path
import shutil

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_trials as archive
import deep_sky_siril_stretch_v2 as protected
from . import test_deep_sky_siril as support
from ._policy_pins import replay_and_finalize_without_migration
from .test_deep_sky_siril_stretch import context
from .test_stretch_routing_v29 import routing_case, prepare, failed_profile

ROOT = Path(__file__).parents[1]


def test_solved_prefixes_are_archived_with_native_bytes(routing_case):
    session = routing_case[0]
    prepared = prepare(routing_case)
    item = prepared['candidates']['profile']
    result = stretch.solve_stretch_parameters(session,item['request'],item['run_id'])
    assert result['status']=='solved'
    report = result['report']
    index = contract.load_json(session/report['trial_artifacts']['path'])
    assert index['status']=='solved' and not index['scientific_parent']
    assert len(index['images'])==6
    assert len(list(session.glob('reports/*/scientific-attempt.json')))==1
    for image in index['images']:
        assert (session/image['source']['path']).read_bytes()==(session/image['artifact']['path']).read_bytes()
        if image['transfer_chain']:
            assert len(image['transfer_chain'])==image['step']
    stretch.load_parameters(session,routing_case[2],result['parameters_path'])
    copied = session/index['images'][0]['artifact']['path']
    copied.write_bytes(copied.read_bytes()+b'drift')
    with pytest.raises(contract.ContractError):
        stretch.load_parameters(session,routing_case[2],result['parameters_path'])


def test_failed_prefix_retains_status_and_actual_trial(routing_case):
    result = failed_profile(routing_case)
    session = routing_case[0]
    index = contract.load_json(session/result['report']['trial_artifacts']['path'])
    assert index['status']=='ineligible' and len(index['images'])==3
    assert all(image['eligible'] is False for image in index['images'] if image['trial'])
    assert all(len(image['transfer_chain'])==1 for image in index['images'] if image['trial'])
    assert archive.verify(session,result['report'])==archive.retained_paths(session,routing_case[2])


def test_prelaunch_failure_has_no_fabricated_images(routing_case,monkeypatch):
    session = routing_case[0]
    item = prepare(routing_case)['candidates']['profile']
    def timeout(*args):
        raise TimeoutError('Synthetic prelaunch failure')
    monkeypatch.setattr(stretch.NativeTrials,'run',timeout)
    result = stretch.solve_stretch_parameters(session,item['request'],item['run_id'])
    assert result['status']=='timed_out'
    index = contract.load_json(session/result['report']['trial_artifacts']['path'])
    assert index['images']==[]
    assert not list(session.glob('reports/*/scientific-attempt.json'))


@pytest.mark.parametrize('collision',['directory','symlink'])
def test_archive_collision_rejects_before_native_launch(routing_case,collision):
    session = routing_case[0]
    item = prepare(routing_case)['candidates']['profile']
    target = archive.directory(session,item['run_id'])
    target.parent.mkdir()
    if collision=='symlink': target.symlink_to(session/'reports',target_is_directory=True)
    else: target.mkdir()
    with pytest.raises(contract.ContractError):
        stretch.solve_stretch_parameters(session,item['request'],item['run_id'])
    assert not list(session.glob('reports/*/scientific-attempt.json'))


def trial_evidence(session,source):
    """The persistence test supplies native evidence; it does not test stretching."""
    directory = session/'reports/071-trial/parameter-trials';directory.mkdir(parents=True)
    fits = directory/'step-1-1-candidate.fit';shutil.copyfile(source,fits)
    preview = directory/'step-1-1-candidate.jpg';support._write_jpeg(preview)
    request = directory.parent/'request.json';contract.atomic_write_json(request,{'source':'@input'})
    report = {'schema':protected.REPORT,'run_id':'071-trial','status':'ineligible',
        'reason':'Known synthetic persistence fixture',
        'bindings':{'source':{'path':'@input',**contract.fingerprint(source,include_path=False)},
                    'request':stretch.record(session,request)},
        'steps':[{'method':'mtf','trials':[{'parameters':{'command':'mtf','parameters':['0','.1','1']},
            'audit':{'eligible':False},'retained':{'candidate.fit':stretch.record(session,fits),
                                               'candidate.jpg':stretch.record(session,preview)}}]}]}
    report['trial_artifacts'] = archive.create(session,report)
    path = directory.parent/'stretch-parameters.json';contract.atomic_write_json(path,report)
    return report


def test_default_prune_retains_failed_trial_archives_and_checks_replay(tmp_path,monkeypatch):
    session,source,_ = support._session(tmp_path,monkeypatch)
    run,jpeg = support._delivery_run(session,source,monkeypatch)
    review = support._review(session,run['id'],jpeg)
    evidence = trial_evidence(session,source)
    retained = archive.retained_paths(session,contract.load_json(session/'session.json'))
    copied = session/next(path for path in retained if path.endswith('.fit'))
    assert not sessions._source_is_bound(session,contract.load_json(session/'session.json'),copied)
    selection = support._selection(session,run['id'],jpeg,review)
    result = core.finalize_session(session,selection_value=selection,keep_intermediates=False)
    assert result['retention_policy']=='prune'
    assert all((session/path).is_file() for path in retained)
    original = evidence['steps'][0]['trials'][0]['retained']['candidate.fit']['path']
    assert not (session/original).exists()
    audit = contract.load_json(session/'reports/final-audit.json')
    assert retained <= {row['path'] for row in audit['evidence_files']}
    assert not retained.intersection(audit['pruned_intermediate_images'])
    assert core.finalize_session(session,selection_value=selection,keep_intermediates=False)==result
    copied.write_bytes(copied.read_bytes()+b'drift')
    with pytest.raises(contract.ContractError):
        core.finalize_session(session,selection_value=selection,keep_intermediates=False)


def test_hdr_output_archive_binds_receipt_and_visibility_panel(tmp_path,monkeypatch):
    session,source,_ = support._session(tmp_path,monkeypatch)
    output = session/'artifacts/070-hdr.fit';shutil.copyfile(source,output)
    preview = session/'previews/070-hdr.jpg';support._write_jpeg(preview)
    receipt = {'schema':'starun-siril.run-receipt.v1','id':'070-hdr','protocol':'stretch',
        'status':'failed','source_path':'@input','source':contract.fingerprint(source,include_path=False),
        'hdr_transfer':{'gentle_chain':[]},'outputs':[stretch.record(session,output),stretch.record(session,preview)]}
    receipt['trial_artifacts'] = archive.create(session,receipt)
    contract.atomic_write_json(session/'runs/070-hdr.json',receipt)
    assert contract.load_json(session/receipt['trial_artifacts']['path'])['hdr_transfer']==receipt['hdr_transfer']
    assert len(archive.verify(session,receipt))==3
    assert archive.retained_paths(session,contract.load_json(session/'session.json'))
    forged = copy.deepcopy(receipt);forged['status']='success'
    with pytest.raises(contract.ContractError,match='index differs'):
        archive.verify(session,forged)


def test_v29_original_bindings_still_replay_and_finalize(tmp_path,monkeypatch):
    prior = ROOT/'tests/fixtures/stretch-trial-artifacts-prior.json'
    assert prior.read_bytes()==(ROOT/'references/stretch-trial-artifacts-history.json').read_bytes()
    payload = {'execution_policy':contract.load_json(prior)['execution_policy']}
    assert sessions.known_execution_policy(payload['execution_policy'])
    assert not archive.enabled(payload)
    assert not sessions.execution_policy_is_current(payload)
    replay_and_finalize_without_migration(tmp_path,monkeypatch,prior)
