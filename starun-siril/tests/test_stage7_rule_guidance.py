"""Stage 7 rule clarification preserves exact committed evidence."""
import copy
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
POLICY_HASH = '56d8ecad050853f53058fcad1990761759032eb6dd474754933d18d67f4f18e0'
PRIOR_POLICY = sessions._PRE_STAGE7_DOCUMENT_BINDINGS['execution_policy']
OUTGOING = {
    'SKILL.md': {'sha256': '06caedf3a851b2b70c161b1fdd68fc08e8514db477774142765717646ef94447', 'size': 36170},
    'references/stages/stage-07-stretch.md': {
        'sha256': 'b5b913ffca7abc17d4856162610f6aebb6dd594b2cb4d6723e515680036c0140', 'size': 124309},
    'references/cli-contract.md': {
        'sha256': '072888abbd855269872627fa47305761b12e69faf7bad48290d9bb2b4997481d', 'size': 73675},
    'references/target-morphology-review.md': {
        'sha256': '54670f41a94f68d2f9f3c851e57c7c15323f0d59370c079b35b793a4401c61d3', 'size': 10102}}


@pytest.mark.parametrize('name', OUTGOING)
def test_outgoing_registration_is_exact_and_policy_is_unchanged(name):
    assert contract.stable_hash(PRIOR_POLICY) == POLICY_HASH
    assert sessions._STAGE7_RULES_REPLAY_POLICY_HASH == POLICY_HASH
    assert sessions._STAGE7_RULES_REPLAY_REFERENCES == OUTGOING
    binding = OUTGOING[name]
    assert contract.fingerprint(ROOT/name, include_path=False) != binding
    record = {'path': name, **binding}
    payload = {'execution_policy': PRIOR_POLICY}
    assert sessions.registered_guidance_reference(payload, record)
    assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
    for change in ({'path': name+'.unknown'}, {'sha256': '0'*64},
                   {'size': binding['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(payload, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


@pytest.mark.parametrize('name, message', [
    (name, 'path is unsafe' if name == 'SKILL.md' else 'reference hash changed')
    for name in OUTGOING])
def test_outgoing_reference_cannot_authorize_new_execution(provenance_case, name, message):
    payload = copy.deepcopy(provenance_case['payload'])
    payload['references'].append({'path': name, 'sha256': OUTGOING[name]['sha256'],
                                  'role': 'supporting'})
    with pytest.raises(contract.ContractError, match=message):
        _validate_provenance(provenance_case, payload)


def test_outgoing_replay_and_finalize_preserve_evidence(tmp_path, monkeypatch):
    snapshot = tmp_path/'stage7-rules-snapshot.json'
    contract.atomic_write_json(snapshot, {'execution_policy': PRIOR_POLICY,
                                          'documents': OUTGOING})
    replay_and_finalize_without_migration(tmp_path, monkeypatch, snapshot)
