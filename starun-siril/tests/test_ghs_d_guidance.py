"""Corrected GHS D guidance retains exact committed evidence without migration."""
import copy
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_session as sessions
from .test_knowledge_contract import provenance_case, _validate_provenance
from .test_recomposition_proposals import case as replay_case
from . import test_stretch_v4_flow_guidance as flow_guidance

ROOT = Path(__file__).parents[1]
PRIOR = contract.load_json(ROOT/'tests/fixtures/ghs-d-semantics-prior.json')


def test_outgoing_registration_is_exact_and_policy_is_unchanged():
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._GHS_D_GUIDANCE_REPLAY_POLICY_HASH == contract.stable_hash(PRIOR['execution_policy'])
    assert sessions._GHS_D_GUIDANCE_REPLAY_REFERENCES == PRIOR['documents']
    for name, binding in PRIOR['documents'].items():
        record = {'path': name, **binding}
        assert contract.fingerprint(ROOT/name, include_path=False) != binding
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
        assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        for change in ({'path': name+'.unknown'}, {'sha256': '0'*64},
                       {'size': binding['size']+1}, {'size': True}):
            assert not sessions.registered_guidance_reference(
                {'execution_policy': PRIOR['execution_policy']}, {**record, **change})
        assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_outgoing_reference_cannot_authorize_new_execution(provenance_case):
    name = 'references/stages/stage-07-stretch.md'
    payload = copy.deepcopy(provenance_case['payload'])
    payload['references'].append({'path': name, 'sha256': PRIOR['documents'][name]['sha256'],
                                  'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, payload)


def test_outgoing_replay_and_finalize_preserve_evidence(replay_case, monkeypatch):
    monkeypatch.setattr(flow_guidance, 'PRIOR', PRIOR)
    flow_guidance.test_outgoing_stretch_replay_and_finalize_preserve_evidence(replay_case, monkeypatch)
