"""Shared review rules retain evidence while protocol bodies own their contracts."""
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
DOCUMENT = 'references/quality.md'
STAGE3 = 'references/stages/stage-03-background.md'
BASELINE = ROOT/'tests/fixtures/quality-contract-prior.json'
PRIOR = contract.load_json(BASELINE)


def test_common_review_roi_examples_comparability_and_fragments_are_preserved():
    text = (ROOT/DOCUMENT).read_text()
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
    for heading, digest in PRIOR['common_sections_sha256'].items():
        section = text.split('## '+heading+'\n', 1)[1].split('\n## ', 1)[0]
        assert hashlib.sha256(('## '+heading+'\n'+section+'\n').encode()).hexdigest() == digest
    assert len(text.encode()) < PRIOR['documents'][DOCUMENT]['size']


def test_current_background_rules_have_one_default_and_keep_report_schema_v2():
    quality = (ROOT/DOCUMENT).read_text()
    stage = (ROOT/STAGE3).read_text()
    assert 'Current sessions freeze' not in quality and '新会话冻结' not in quality
    assert re.findall(r'新会话冻结 `background=([^`]+)`', stage) == [contract.EXECUTION_POLICY['background']]
    assert 'session.execution_policy.background' in stage
    assert 'starun-siril.background-validation.v2' in stage
    assert 'policy 为 heldout_material_gate_v4' not in stage
    blocks = re.findall(r'^```[^\n]*\n.*?^```', stage, re.M | re.S)
    assert [hashlib.sha256(block.encode()).hexdigest() for block in blocks] == PRIOR['stage3_code_blocks_sha256']


def test_quality_guidance_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == PRIOR['execution_policy_hash']
    assert sessions._QUALITY_CONTRACT_REPLAY_POLICY_HASH == PRIOR['execution_policy_hash']
    assert sessions._QUALITY_CONTRACT_REPLAY_REFERENCES == PRIOR['documents']
    for name, binding in PRIOR['documents'].items():
        record = {'path': name, **binding}
        assert sessions.registered_guidance_reference(PRIOR, record)
        assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        for change in ({'path': name+'.unknown'}, {'sha256': '0'*64},
                       {'size': binding['size']+1}, {'size': True}):
            assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
        assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


@pytest.mark.parametrize('name', [DOCUMENT, STAGE3])
def test_old_quality_guidance_cannot_authorize_new_ssf(provenance_case, name):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': name,
                                  'sha256': PRIOR['documents'][name]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_quality_guidance_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_quality_references(protocol, **kwargs):
        return [*references(protocol, **kwargs), *[
            {'path': name, 'sha256': contract.sha256_file(ROOT/name), 'role': 'supporting'}
            for name in PRIOR['documents']]]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_quality_references)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)
