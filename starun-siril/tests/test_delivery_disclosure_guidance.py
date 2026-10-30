"""Delivery reports actual executed evidence without inventing a baseline."""
import copy
import hashlib
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/delivery.md'
BASELINE = ROOT/'tests/fixtures/delivery-disclosure-prior.json'
PRIOR = contract.load_json(BASELINE)


def test_actual_stage7_disclosure_preserves_the_remaining_rules_and_history():
    text = (ROOT/DOCUMENT).read_text()
    assert 'safe MTF baseline proposal' not in text
    assert 'default baseline entry' not in text
    for sentence in (
        'Report the actual Stage 7 route and executed operator chain, the adopted result or retained parent, and the verified references and comparisons used in review.',
        'Explain adoption and any actual route revision through the existing rationale/notes.'):
        assert text.count(sentence) == 1
        text = text.replace(sentence, '', 1)
    assert hashlib.sha256(text.encode()).hexdigest() == PRIOR['unchanged_body_sha256']


def test_delivery_disclosure_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == PRIOR['execution_policy_hash']
    assert sessions._DELIVERY_DISCLOSURE_REPLAY_POLICY_HASH == PRIOR['execution_policy_hash']
    assert sessions._DELIVERY_DISCLOSURE_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference(PRIOR, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
    for policy in ({}, {**PRIOR['execution_policy'], 'reporting': 'unknown'}):
        assert not sessions.registered_guidance_reference({'execution_policy': policy}, record)


def test_old_delivery_disclosure_cannot_authorize_new_ssf(provenance_case):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': DOCUMENT,
                                  'sha256': PRIOR['documents'][DOCUMENT]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_delivery_disclosure_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_delivery_reference(protocol, **kwargs):
        return [*references(protocol, **kwargs),
                {'path': DOCUMENT, 'sha256': contract.sha256_file(ROOT/DOCUMENT), 'role': 'supporting'}]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_delivery_reference)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)
