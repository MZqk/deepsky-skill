"""Stage routing delegates shared color and tone rules without losing evidence."""
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
BASELINE = ROOT/'tests/fixtures/shared-color-routing-prior.json'
PRIOR = contract.load_json(BASELINE)
DENOISE_BASELINE = ROOT/'tests/fixtures/stage10-denoise-link-prior.json'
DENOISE_PRIOR = contract.load_json(DENOISE_BASELINE)
DENOISE_REDIRECT = '\n来源与 SSF 示例见[共用非线性降噪正文](../shared-protocols/denoise-nonlinear.md#restoration-denoise-nonlinear-ssf-knowledge-and-skeleton)。\n'
DISPLAY_STAGE8 = {
    'panel_sha256': '879c9e4ae717607a4283330c29fde67362f648eebee22e883ad413e584808e5e',
    'section_sha256': 'd9d9a75efc7305c06cef61ad161a227a03390285701d1d911c8739d4d0f7abe3',
    'size': 45712,
}


@pytest.mark.parametrize('name', PRIOR['documents'])
def test_stage_routing_preserves_fragments_examples_and_owned_contracts(name):
    text = (ROOT/name).read_text().removesuffix(DENOISE_REDIRECT)
    blocks = re.findall(r'^```[^\n]*\n.*?^```', text, re.M | re.S)
    expected = list(PRIOR['code_blocks_sha256'][name])
    display_scale = name == 'references/stages/stage-08-starless-enhance.md'
    if display_scale:
        expected[4] = DISPLAY_STAGE8['panel_sha256']
    assert [hashlib.sha256(block.encode()).hexdigest() for block in blocks] == expected
    prose = re.sub(r'^```[^\n]*\n.*?^```', '', text, flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', prose))
    counts = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', prose, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
        counts[slug] += 1
    assert set(PRIOR['anchors'][name]) <= anchors
    for index, section in enumerate(PRIOR['owned_contracts'][name]):
        start = text.index(section['start'])
        end = text.index(section['end'], start) if section['end'] else len(text)
        expected = DISPLAY_STAGE8['section_sha256'] if display_scale and index == 0 else section['sha256']
        assert hashlib.sha256(text[start:end].encode()).hexdigest() == expected
    assert len(text.encode()) <= (DISPLAY_STAGE8['size'] if display_scale else PRIOR['documents'][name]['size']-1)


def test_shared_rules_stay_in_the_canonical_bodies_and_stages_only_route():
    for name, digest in PRIOR['shared_documents'].items():
        assert contract.sha256_file(ROOT/name) == digest
    for protocol in ('color.finish', 'tone.adjust'):
        assert contract.PROTOCOL_STAGE_REFERENCES[protocol] == (
            f"references/shared-protocols/{protocol.replace('.', '-')}.md",)
    for name in PRIOR['documents']:
        text = (ROOT/name).read_text()
        intro = text.split(PRIOR['owned_contracts'][name][0]['start'], 1)[0]
        assert '../shared-protocols/color-finish.md#unmasked-first-color' in intro
        assert '../shared-protocols/tone-adjust.md#remaining-goal-tone' in intro
        assert 'color_assessment' not in intro
        assert 'saturation_protect_start' not in intro
        assert '目标为零' in intro and '跳过' in intro
        assert 'primary' in intro and 'SHA-256' in intro


def test_legacy_denoise_anchors_link_to_the_shared_example_without_other_edits():
    name, binding = next(iter(DENOISE_PRIOR['documents'].items()))
    text = (ROOT/name).read_text()
    original = text.removesuffix(DENOISE_REDIRECT).encode()
    assert {'sha256': hashlib.sha256(original).hexdigest(), 'size': len(original)} == binding
    anchors = ('ssf-knowledge-and-skeleton',
               'restoration-denoise-nonlinear-ssf-knowledge-and-skeleton', '来源与-ssf-示例')
    assert text.endswith('\n\n'.join(f'<a id="{anchor}"></a>' for anchor in anchors)+'\n'+DENOISE_REDIRECT)
    target = re.search(r'\]\(([^)]+)\)', DENOISE_REDIRECT).group(1)
    path, fragment = target.split('#')
    body = (ROOT/name).parent/path
    assert body.resolve() == ROOT/contract.PROTOCOL_STAGE_REFERENCES['restoration.denoise-nonlinear'][0]
    assert f'<a id="{fragment}"></a>' in body.read_text()


@pytest.mark.parametrize('prior, registry', [
    (PRIOR, '_SHARED_COLOR_ROUTING_REPLAY'), (DENOISE_PRIOR, '_STAGE10_DENOISE_LINK_REPLAY')])
def test_shared_routing_registration_is_exact_without_policy_changes(prior, registry):
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(prior['execution_policy'])
    assert contract.stable_hash(prior['execution_policy']) == prior['execution_policy_hash']
    assert getattr(sessions, registry+'_POLICY_HASH') == prior['execution_policy_hash']
    assert getattr(sessions, registry+'_REFERENCES') == prior['documents']
    for name, binding in prior['documents'].items():
        record = {'path': name, **binding}
        assert sessions.registered_guidance_reference(prior, record)
        assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        for change in ({'path': name+'.unknown'}, {'sha256': '0'*64},
                       {'size': binding['size']+1}, {'size': True}):
            assert not sessions.registered_guidance_reference(prior, {**record, **change})
        for policy in ({}, {**prior['execution_policy'], 'reporting': 'unknown'}):
            assert not sessions.registered_guidance_reference({'execution_policy': policy}, record)


@pytest.mark.parametrize('name, binding', [*PRIOR['documents'].items(), *DENOISE_PRIOR['documents'].items()])
def test_old_stage_routing_cannot_authorize_new_ssf(provenance_case, name, binding):
    document = copy.deepcopy(provenance_case['payload'])
    references = [row for row in document['references'] if row['path'] == name]
    if not references:
        references = [{'path': name, 'role': 'supporting'}]
        document['references'].extend(references)
    references[0]['sha256'] = binding['sha256']
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


@pytest.mark.parametrize('baseline', [BASELINE, DENOISE_BASELINE])
def test_committed_stage_routing_replays_and_finalizes_without_migration(tmp_path, monkeypatch, baseline):
    references = sessions.protocol_reference_bindings

    def with_stage_references(protocol, **kwargs):
        rows = references(protocol, **kwargs)
        present = {row['path'] for row in rows}
        return [*rows, *[
            {'path': name, 'sha256': contract.sha256_file(ROOT/name), 'role': 'supporting'}
            for name in PRIOR['documents'] if name not in present]]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_stage_references)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, baseline)
