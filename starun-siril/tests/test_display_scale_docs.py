"""Display-scale guidance keeps outgoing Stage 7/8 references replayable."""
import copy
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
PRIOR = contract.load_json(ROOT/'tests/fixtures/display-scale-prior.json')
DOCS = tuple(PRIOR['documents'])


def test_outgoing_registration_is_exact_and_policy_is_unchanged():
    assert sessions._DISPLAY_SCALE_REPLAY_POLICY_HASH == contract.stable_hash(PRIOR['execution_policy'])
    assert sessions._DISPLAY_SCALE_REPLAY_REFERENCES == PRIOR['documents']
    for name, binding in PRIOR['documents'].items():
        record = {'path': name, **binding}
        assert contract.fingerprint(ROOT/name, include_path=False) != binding
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
        assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        for change in ({'path': name+'.unknown'}, {'sha256': '0'*64}, {'size': binding['size']+1}):
            assert not sessions.registered_guidance_reference(
                {'execution_policy': PRIOR['execution_policy']}, {**record, **change})
    assert not sessions.registered_guidance_reference(
        {'execution_policy': {}}, {'path': DOCS[0], **PRIOR['documents'][DOCS[0]]})


def test_both_stage_documents_carry_the_display_scale_rule():
    stage7 = (ROOT/'references/stages/stage-07-stretch.md').read_text()
    stage8 = (ROOT/'references/stages/stage-08-starless-enhance.md').read_text()
    assert 'deep_sky_siril_display_scale.py' in stage7 and 'display-scale.png' in stage7
    assert 'display-scale.png' in stage8 and 'full-frame display' in stage8
    assert '1024' in stage7 and '1024' in stage8
    assert '<a id="reference-visibility-review"></a>' in stage7
    assert '<a id="protected-local-contrast"></a>' in stage8


def test_outgoing_reference_cannot_authorize_new_execution(provenance_case):
    payload = copy.deepcopy(provenance_case['payload'])
    name = DOCS[0]
    payload['references'].append({'path': name, 'sha256': PRIOR['documents'][name]['sha256'],
                                  'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, payload)
