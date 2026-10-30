"""The compact entrypoint preserves exact committed reference evidence."""
import copy
from pathlib import Path
import re

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
from . import test_deep_sky_siril as support
from .test_knowledge_contract import provenance_case, _validate_provenance
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
POLICY_HASH = '56d8ecad050853f53058fcad1990761759032eb6dd474754933d18d67f4f18e0'
PRIOR_POLICY = sessions._PRE_STAGE7_DOCUMENT_BINDINGS['execution_policy']
OUTGOING = {'sha256': '585e6c9c5d1baf5ed57acb0922984416fb1ada017b78518d185f4bd5959720a5', 'size': 36231}
DIRECT_OUTGOING = {'sha256': 'd6891a5c9281d0af6f495ee8604c28e88bdb950b5b558d8e4b16043d4360d62a', 'size': 8693}


def test_direct_protocol_routes_match_canonical_bodies():
    routes = {}
    bodies = set()
    for line in (ROOT/'SKILL.md').read_text().splitlines():
        if not line.startswith('| `'):
            continue
        cells = line.split('|')
        body, = re.findall(r'\]\((references/[^)#]+\.md)\)', cells[2])
        assert body not in bodies
        bodies.add(body)
        for protocol in re.findall(r'`([^`]+)`', cells[1]):
            assert protocol not in routes
            routes[protocol] = body
    assert routes == {protocol: paths[0] for protocol, paths in contract.PROTOCOL_STAGE_REFERENCES.items()}


def test_direct_protocol_guidance_keeps_exact_previous_entrypoint():
    assert contract.stable_hash(PRIOR_POLICY) == POLICY_HASH
    assert sessions._DIRECT_PROTOCOL_REPLAY_POLICY_HASH == POLICY_HASH
    assert sessions._DIRECT_PROTOCOL_REPLAY_REFERENCES == {'SKILL.md': DIRECT_OUTGOING}
    assert contract.fingerprint(ROOT/'SKILL.md', include_path=False) != DIRECT_OUTGOING
    assert (DIRECT_OUTGOING['sha256'], DIRECT_OUTGOING['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS['SKILL.md']
    record = {'path': 'SKILL.md', **DIRECT_OUTGOING}
    payload = {'execution_policy': PRIOR_POLICY}
    assert sessions.registered_guidance_reference(payload, record)
    for change in ({'path': 'SKILL.md.unknown'}, {'sha256': '0'*64},
                   {'size': DIRECT_OUTGOING['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(payload, {**record, **change})
    for policy in ({}, {**contract.EXECUTION_POLICY, 'guidance': 'unknown'}):
        assert not sessions.registered_guidance_reference({'execution_policy': policy}, record)


def test_outgoing_entrypoint_is_registered_without_changing_policy():
    assert contract.stable_hash(PRIOR_POLICY) == POLICY_HASH
    assert sessions._ENTRYPOINT_REPLAY_POLICY_HASH == POLICY_HASH
    assert sessions._ENTRYPOINT_REPLAY_REFERENCES == {'SKILL.md': OUTGOING}
    assert contract.fingerprint(ROOT/'SKILL.md', include_path=False) != OUTGOING
    assert (OUTGOING['sha256'], OUTGOING['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS['SKILL.md']
    assert sessions.registered_guidance_reference(
        {'execution_policy': PRIOR_POLICY}, {'path': 'SKILL.md', **OUTGOING})


@pytest.mark.parametrize('change', [
    {'path': 'SKILL.md.unknown'}, {'sha256': '0'*64}, {'size': OUTGOING['size']+1}, {'size': True}])
def test_outgoing_entrypoint_requires_exact_reference(change):
    assert not sessions.registered_guidance_reference(
        {'execution_policy': PRIOR_POLICY}, {'path': 'SKILL.md', **OUTGOING, **change})


@pytest.mark.parametrize('policy', [{}, {**contract.EXECUTION_POLICY, 'guidance': 'unknown'}])
def test_outgoing_entrypoint_requires_exact_policy(policy):
    assert not sessions.registered_guidance_reference(
        {'execution_policy': policy}, {'path': 'SKILL.md', **OUTGOING})


@pytest.mark.parametrize('binding', [OUTGOING, DIRECT_OUTGOING])
def test_outgoing_entrypoint_cannot_authorize_new_ssf(provenance_case, binding):
    payload = copy.deepcopy(provenance_case['payload'])
    payload['references'].append({'path': 'SKILL.md', 'sha256': binding['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='Reference path is unsafe'):
        _validate_provenance(provenance_case, payload)


@pytest.mark.parametrize('historical', [False, True])
def test_sessions_replay_and_finalize_unchanged(tmp_path, monkeypatch, historical):
    session, source, _ = support._session(tmp_path, monkeypatch)
    receipt, candidate = support._delivery_run(session, source, monkeypatch)
    receipt_path = session/f"runs/{receipt['id']}.json"
    if historical:
        baseline = contract.load_json(ROOT/'tests/fixtures/serial-agent-v14.json')
        receipt['knowledge_validation']['references'].append(
            {'path': 'SKILL.md', 'role': 'supporting', **baseline['documents']['SKILL.md']})
        contract.atomic_write_json(receipt_path, receipt)
        _freeze_historical_evidence(session, snapshot=baseline)
    review = support._review(session, receipt['id'], candidate)
    frozen = support._session_bytes(session)
    assert core.run_script(session, protocol='delivery.render', script_value=session/receipt['script']['path'],
                           source_value=source, expected_values=[candidate, candidate.with_suffix('.fit')],
                           timeout=30)['replayed']
    assert support._session_bytes(session) == frozen
    selection = support._selection(session, receipt['id'], candidate, review)
    immutable = {path: path.read_bytes() for path in
                 (session/'session.json', receipt_path, session/receipt['script_provenance']['path'], review)}
    result = core.finalize_session(session, selection_value=selection, keep_intermediates=True)
    assert result['status'] == 'success'
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True) == result
    assert all(path.read_bytes() == data for path, data in immutable.items())
