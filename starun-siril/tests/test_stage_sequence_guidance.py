"""Stage routing stays complete while detailed rules remain in canonical bodies."""
import copy
from collections import Counter
from pathlib import Path
import re

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case, _stage_routes, _validate_provenance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stage-sequence.md'
BASELINE = ROOT/'tests/fixtures/stage-sequence-prior.json'
PRIOR = contract.load_json(BASELINE)


def test_stage_order_fragments_and_direct_protocol_routes_are_preserved():
    text = (ROOT/DOCUMENT).read_text()
    routes = {str(stage): [guide, protocols] for stage, (guide, protocols) in _stage_routes(text).items()}
    assert routes == PRIOR['routes']
    anchors = set(re.findall(r'<a id="([^"]+)"', text))
    counts = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', text, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
        counts[slug] += 1
    assert set(PRIOR['anchors']) <= anchors
    for line in text.splitlines():
        if not re.match(r'^\| \d', line):
            continue
        for protocol, body in re.findall(r'\[`([^`]+)`\]\(([^)#]+)#[^)]+\)', line.split('|')[4]):
            assert ('references/'+body,) == contract.PROTOCOL_STAGE_REFERENCES[protocol]
    assert len(text.encode()) < PRIOR['documents'][DOCUMENT]['size']


def test_stage_sequence_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == PRIOR['execution_policy_hash']
    assert sessions._STAGE_SEQUENCE_REPLAY_POLICY_HASH == PRIOR['execution_policy_hash']
    assert sessions._STAGE_SEQUENCE_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference(PRIOR, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_old_stage_sequence_cannot_authorize_new_ssf(provenance_case):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': DOCUMENT,
                                  'sha256': PRIOR['documents'][DOCUMENT]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_stage_sequence_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_sequence_reference(protocol, **kwargs):
        return [*references(protocol, **kwargs),
                {'path': DOCUMENT, 'sha256': contract.sha256_file(ROOT/DOCUMENT), 'role': 'supporting'}]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_sequence_reference)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)
