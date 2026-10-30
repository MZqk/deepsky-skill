"""Source-bound admission and Image 0 evidence regressions."""
import struct
import subprocess
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
from .test_deep_sky_siril import _session, _write_fits
from . import test_deep_sky_siril as support
from .test_xisf_metrics import xisf


def _history(path, image_index):
    raw = path.read_bytes()
    size = struct.unpack('<I', raw[8:12])[0]
    root = ET.fromstring(raw[16:16+size])
    images = [element for element in root if element.tag.endswith('Image')]
    ET.SubElement(images[image_index], '{http://www.pixinsight.com/xisf}Property',
                  {'id': 'PixInsight:ProcessingHistory', 'type': 'String'}).text = '<instance class="HistogramTransformation"/>'
    header = ET.tostring(root)
    prefix = b'XISF0100' + struct.pack('<I', len(header)) + bytes(4) + header
    assert len(prefix) <= 4096
    path.write_bytes(prefix.ljust(4096, b'\0') + raw[4096:])


@pytest.mark.parametrize('image_index,state', [(0, 'nonlinear'), (1, 'unknown')])
def test_only_scientific_image_history_changes_state(tmp_path, image_index, state):
    source = tmp_path/'stack.xisf'
    xisf(source, np.full((3, 16, 16), .01, dtype=np.float32), extra=True,
         keywords=[('IMAGETYP', "'Master Light'")])
    _history(source, image_index)
    detected, evidence = sessions.detect_master_input_state(source)
    assert detected == state
    assert bool(evidence) == (state == 'nonlinear')


def test_explicit_linear_conflicts_with_image_zero_history(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, input_state='unknown')
    source = tmp_path/'stretched.xisf'
    xisf(source, np.full((3, 16, 16), .01, dtype=np.float32), extra=True)
    _history(source, 0)
    with pytest.raises(contract.ContractError, match='Image 0 stretching history'):
        sessions.init_session(str(source), str(tmp_path/'conflict'), input_state='linear',
            state_evidence=['user confirmed input_state=linear; actual source declaration'],
            channel_mode='broadband', channel_map=None, target_name=None, target_type='unknown',
            style='natural', stars='adaptive', offline=True, keep_intermediates=False)


def test_explicit_declaration_conflicts_with_detected_channel(tmp_path, monkeypatch):
    _, source, _ = _session(tmp_path, monkeypatch, input_state='unknown')
    with pytest.raises(contract.ContractError, match='Conflicting channel_mode evidence'):
        sessions.init_session(str(source), str(tmp_path/'conflict'), input_state='linear',
            state_evidence=['user confirmed input_state=linear channel_mode=broadband'],
            channel_mode='unknown', channel_map=None, target_name=None, target_type='unknown',
            style='natural', stars='adaptive', offline=True, keep_intermediates=False)


def test_inferred_legacy_state_is_not_recovered(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch, input_state='linear')
    payload = contract.load_json(session/'session.json')
    payload['context']['state_evidence'] = ["fits header: IMAGETYP='Master Light' (s50); PixInsight preprocessing integration; verified unstretched linear master; input_state=linear"]
    payload['session_sha256'] = contract.stable_hash({k: v for k, v in payload.items() if k != 'session_sha256'})
    contract.atomic_write_json(session/'session.json', payload)
    recovered, evidence = sessions._recover_input_evidence(source, contract.fingerprint(source, role='stacked_master'))
    assert 'input_state' not in recovered and evidence == []
    kwargs = {k: v for k, v in payload['context'].items() if k != 'filter_profile'}
    kwargs.update(input_state='auto', state_evidence=[], channel_mode='unknown')
    result = sessions.init_session(str(source), str(tmp_path/'new-session'), **kwargs)
    assert result['context']['input_state'] == 'unknown'


def _pcc(tmp_path, source_type='acquisition'):
    source = tmp_path/'input.fit'
    _write_fits(source, color_ready=True)
    record = {'value': 'standard_rgb', 'source': source_type,
              'evidence': 'https://example.org/response' if source_type == 'manufacturer' else 'Recorded RGB and IR blocking'}
    declaration = {'equipment': {'pcc_compatibility': record}}
    def check(policy=contract.EXECUTION_POLICY):
        return processing.pcc_evidence(declaration, source, 'broadband', session=tmp_path, execution_policy=policy)
    return record, check


@pytest.mark.parametrize('source_type', ['acquisition', 'manufacturer'])
def test_pcc_source_requires_nonempty_unchanged_bound_evidence(tmp_path, source_type):
    record, check = _pcc(tmp_path, source_type)
    with pytest.raises(contract.ContractError, match='bound nonempty'):
        check()
    reports = tmp_path/'reports'; reports.mkdir()
    evidence = reports/'source.txt'; evidence.write_text('Actual source record')
    record['reference'] = {'path': 'reports/source.txt', **contract.fingerprint(evidence, include_path=False)}
    assert check()['compatibility_evidence'] == record
    evidence.write_text('Modified source record')
    with pytest.raises(contract.ContractError, match='evidence changed'):
        check()
    evidence.unlink(); evidence.symlink_to(tmp_path/'input.fit')
    with pytest.raises(contract.ContractError):
        check()


def test_pcc_user_and_historical_evidence_keep_original_contract(tmp_path):
    record, check = _pcc(tmp_path)
    assert check({})['compatibility_evidence'] == record
    record.update(source='user', evidence='User confirmed ordinary RGB response and IR blocking')
    assert check()['compatibility_evidence'] == record


def test_pcc_receipt_rejects_source_drift_on_replay(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch, input_state='linear', color_ready=True)
    script, candidate = support._color_calibration_script(session, source, run_id='040-color', catalogue='gaia')
    evidence_file = session/'reports/pcc-source.txt'
    evidence_file.write_text('Recorded ordinary RGB passbands and IR blocking')
    lines = script.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('# color-calibration:'))
    declaration = json.loads(lines[index].split(':', 1)[1])
    declaration['equipment']['pcc_compatibility'] = {
        'value': 'standard_rgb', 'source': 'acquisition', 'evidence': 'Recorded passbands',
        'reference': {'path': 'reports/pcc-source.txt', **contract.fingerprint(evidence_file, include_path=False)}}
    lines[index] = '# color-calibration: ' + json.dumps(declaration)
    script.write_text('\n'.join(lines) + '\n')
    support._write_ssf_provenance(session, script, 'color.calibrate')
    def native(command, **kwargs):
        _write_fits(candidate, color_ready=True, nonzero=True)
        return subprocess.CompletedProcess(command, 0, support._COLOR_FITS_LOG)
    monkeypatch.setattr(support.core.subprocess, 'run', native)
    args = dict(protocol='color.calibrate', script_value=script, source_value=source,
                expected_values=[candidate], timeout=30)
    assert support.core.run_script(session, **args)['status'] == 'success'
    assert support.core.run_script(session, **args)['replayed']
    evidence_file.write_text('Different source evidence')
    with pytest.raises(contract.ContractError):
        support.core.run_script(session, **args)


def test_outgoing_policy_replays_without_migration(tmp_path, monkeypatch):
    from ._policy_pins import replay_and_finalize_without_migration
    history = Path(__file__).resolve().parents[1]/'references/evidence-admission-history.json'
    prior = contract.load_json(history)
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, history)
