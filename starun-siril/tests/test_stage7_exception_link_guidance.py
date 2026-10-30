"""The Stage 7 summary delegates exception conditions without changing evidence."""
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
DOCUMENT = 'references/stages/stage-07-stretch.md'
BASELINE = ROOT/'tests/fixtures/stage7-exception-link-prior.json'
PRIOR = contract.load_json(BASELINE)


def test_exception_summary_links_to_the_rules_and_preserves_the_remaining_contract():
    text = (ROOT/DOCUMENT).read_text()
    summary = '例外按[统一例外证据规则](#stretch-routing-v29)执行。'
    assert text.count(summary) == 1
    assert '例外须绑定实际失败或已审查的同源保护冲突。' not in text
    assert text.count('<a id="stretch-routing-v29"></a>') == 1
    assert contract.fingerprint(ROOT/DOCUMENT, include_path=False) != sessions._PRE_HDR_ROUTING_BINDINGS['documents'][DOCUMENT]
    prose = re.sub(r'^```[^\n]*\n.*?^```', '', text, flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', prose))
    counts = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', prose, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
        counts[slug] += 1
    assert set(PRIOR['anchors']) <= anchors


def test_exception_link_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == PRIOR['execution_policy_hash']
    assert sessions._STAGE7_EXCEPTION_LINK_REPLAY_POLICY_HASH == PRIOR['execution_policy_hash']
    assert sessions._STAGE7_EXCEPTION_LINK_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference(PRIOR, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
    for policy in ({}, {**PRIOR['execution_policy'], 'reporting': 'unknown'}):
        assert not sessions.registered_guidance_reference({'execution_policy': policy}, record)


def test_old_exception_summary_cannot_authorize_new_ssf(provenance_case):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': DOCUMENT,
                                  'sha256': PRIOR['documents'][DOCUMENT]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_exception_summary_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_stage7_reference(protocol, **kwargs):
        return [*references(protocol, **kwargs),
                {'path': DOCUMENT, 'sha256': contract.sha256_file(ROOT/DOCUMENT), 'role': 'supporting'}]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_stage7_reference)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)
