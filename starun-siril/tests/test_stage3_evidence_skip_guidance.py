"""Stage 3 guidance keeps exact old evidence replayable without new authority."""
import copy
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration, assert_contract_domains
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
PRIOR_PATH = ROOT/'tests/fixtures/stage3-evidence-skip-prior.json'
PRIOR = contract.load_json(PRIOR_PATH)


def test_outgoing_registration_is_exact_and_policy_is_unchanged():
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._STAGE3_SKIP_REPLAY_POLICY_HASH == contract.stable_hash(PRIOR['execution_policy'])
    assert sessions._STAGE3_SKIP_REPLAY_REFERENCES == PRIOR['documents']
    for name, binding in PRIOR['documents'].items():
        record = {'path': name, **binding}
        assert contract.fingerprint(ROOT/name, include_path=False) != binding
        assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
        for change in ({'path': name+'.unknown'}, {'sha256': '0'*64}, {'size': binding['size']+1}):
            assert not sessions.registered_guidance_reference(
                {'execution_policy': PRIOR['execution_policy']}, {**record, **change})
        assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


@pytest.mark.parametrize('name', [name for name in PRIOR['documents'] if name.startswith('references/')])
def test_outgoing_reference_cannot_authorize_new_execution(provenance_case, name):
    payload = copy.deepcopy(provenance_case['payload'])
    payload['references'].append({'path': name, 'sha256': PRIOR['documents'][name]['sha256'],
                                  'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, payload)


def test_outgoing_replay_and_finalize_preserve_evidence(tmp_path, monkeypatch):
    replay_and_finalize_without_migration(tmp_path, monkeypatch, PRIOR_PATH)
