"""CLI reference reduction preserves interfaces and exact committed evidence."""
import copy
from collections import Counter
from pathlib import Path
import re

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/cli-contract.md'
BASELINE = ROOT/'tests/fixtures/cli-contract-prior.json'
PRIOR = contract.load_json(BASELINE)
POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'


def test_cli_options_examples_and_fragments_are_preserved():
    text = (ROOT/DOCUMENT).read_text()
    flags = set(re.findall(r'(?<![\w-])--[a-z][a-z0-9-]*', text))
    assert set(PRIOR['flags']) <= flags
    blocks = re.findall(r'^```[^\n]*\n.*?^```', text, re.M | re.S)
    assert blocks == PRIOR['code_blocks']
    prose = re.sub(r'^```[^\n]*\n.*?^```', '', text, flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', prose))
    counts = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', prose, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
        counts[slug] += 1
    assert set(PRIOR['anchors']) <= anchors
    assert text.replace('\\|', '|').count('--mode profile|exception') == 1
    assert '--batch' in flags and '--stepwise' in flags
    init_section = text.split('## init\n', 1)[1].split('## confirm-input\n', 1)[0]
    assert '`init --dualband auto|off`' in init_section
    assert len(text.encode()) < PRIOR['documents'][DOCUMENT]['size']


def test_cli_guidance_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == POLICY_HASH
    assert sessions._CLI_CONTRACT_REPLAY_POLICY_HASH == POLICY_HASH
    assert sessions._CLI_CONTRACT_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference(PRIOR, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_old_cli_guidance_cannot_authorize_new_ssf(provenance_case):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': DOCUMENT,
                                  'sha256': PRIOR['documents'][DOCUMENT]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_cli_guidance_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_cli_reference(protocol, **kwargs):
        return [*references(protocol, **kwargs),
                {'path': DOCUMENT, 'sha256': contract.sha256_file(ROOT/DOCUMENT), 'role': 'supporting'}]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_cli_reference)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)
