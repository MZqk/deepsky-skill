"""Session guidance separates history without changing committed evidence."""
import copy
from collections import Counter
import hashlib
from pathlib import Path
import re

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/session-contract.md'
BASELINE = ROOT/'tests/fixtures/session-contract-prior.json'
PRIOR = contract.load_json(BASELINE)
POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'


def test_session_history_examples_and_fragments_are_preserved():
    text = (ROOT/DOCUMENT).read_text()
    history = (ROOT/'references/session-legacy.md').read_text()
    blocks = re.findall(r'^```[^\n]*\n.*?^```', text+'\n'+history, re.M | re.S)
    assert blocks == PRIOR['code_blocks']
    prose = re.sub(r'^```[^\n]*\n.*?^```', '', text, flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', prose))
    counts = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', prose, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
        counts[slug] += 1
    assert set(PRIOR['anchors']) <= anchors
    paragraphs = {' '.join(p.split()) for p in re.split(r'\n\s*\n', history)
                  if p.strip() and not p.lstrip().startswith('#')}
    assert set(PRIOR['archived_paragraph_sha256']) <= {
        hashlib.sha256(p.encode()).hexdigest() for p in paragraphs}
    assert 'New sessions freeze' not in text.split('## Independent stellar contour preservation', 1)[0]
    assert 'recomposition_contour=local_peak_mtf_v1' in text
    assert '## 恢复与继续处理' in text
    assert '## 最终提交与恢复' in text
    assert '## Paired scientific delivery' in text
    assert 'session-legacy.md' in text
    assert len(text.encode()) < PRIOR['documents'][DOCUMENT]['size']


def test_session_guidance_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == POLICY_HASH
    assert sessions._SESSION_CONTRACT_REPLAY_POLICY_HASH == POLICY_HASH
    assert sessions._SESSION_CONTRACT_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference(PRIOR, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_old_session_guidance_cannot_authorize_new_ssf(provenance_case):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': DOCUMENT,
                                  'sha256': PRIOR['documents'][DOCUMENT]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_session_guidance_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_session_reference(protocol, **kwargs):
        return [*references(protocol, **kwargs),
                {'path': DOCUMENT, 'sha256': contract.sha256_file(ROOT/DOCUMENT), 'role': 'supporting'}]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_session_reference)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)
