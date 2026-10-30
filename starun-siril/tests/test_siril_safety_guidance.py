"""Safety guidance delegates adapter authorization without changing permissions."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/siril-safety.md'
BASELINE = ROOT/'tests/fixtures/siril-safety-prior.json'
PRIOR = contract.load_json(BASELINE)


def test_safety_uses_the_authoritative_pyscript_map_and_preserves_other_guards():
    text = (ROOT/DOCUMENT).read_text()
    bullets = [line for line in text.splitlines(keepends=True) if line.startswith('- `pyscript`')]
    assert len(bullets) == 1
    assert '[command policy](command-policy.json)' in bullets[0]
    assert '`pyscript_protocols`' in bullets[0]
    assert hashlib.sha256(text.replace(bullets[0], '', 1).encode()).hexdigest() == PRIOR['unchanged_body_sha256']
    policy_path = ROOT/'references/command-policy.json'
    frozen = sessions._PRE_CONTOUR_BINDINGS['command_policy']
    assert hashlib.sha256(frozen.encode()).hexdigest() == PRIOR['command_policy_sha256']
    policy = contract.load_json(policy_path)
    expected = json.loads(frozen)
    expected['protocol_commands']['stars.recompose'] += ['rgbcomp', 'update_key']
    assert policy == expected
    assert 'pyscript' in policy['protocol_commands']['restoration.denoise']
    assert policy['pyscript_protocols']['restoration.denoise'] == 'siril_deepsnr.py'


def test_safety_guidance_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == PRIOR['execution_policy_hash']
    assert sessions._SIRIL_SAFETY_REPLAY_POLICY_HASH == PRIOR['execution_policy_hash']
    assert sessions._SIRIL_SAFETY_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference(PRIOR, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
    for policy in ({}, {**PRIOR['execution_policy'], 'reporting': 'unknown'}):
        assert not sessions.registered_guidance_reference({'execution_policy': policy}, record)


def test_old_safety_guidance_cannot_authorize_new_ssf(provenance_case):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': DOCUMENT,
                                  'sha256': PRIOR['documents'][DOCUMENT]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_safety_guidance_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_safety_reference(protocol, **kwargs):
        return [*references(protocol, **kwargs),
                {'path': DOCUMENT, 'sha256': contract.sha256_file(ROOT/DOCUMENT), 'role': 'supporting'}]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_safety_reference)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)
