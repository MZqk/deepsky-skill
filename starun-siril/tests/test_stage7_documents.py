"""Split Stage 7 references retain scientific examples and exact variant evidence."""
from collections import Counter
import copy
import json
from pathlib import Path
import re

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_validation as validation
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case
from .test_starnet_linear_bridge import native

ROOT = Path(__file__).parents[1]
MAIN = 'references/stages/stage-07-stretch.md'
DOCUMENTS = [MAIN, *(f'references/stage7-{kind}.md' for kind in ('hdr', 'hoo', 'diagnostics', 'legacy'))]
HISTORY = ROOT/'references/stage7-documents-history.json'
PRIOR = contract.load_json(HISTORY)


def test_original_examples_and_fragments_are_preserved():
    blocks = [block for name in DOCUMENTS for block in
              re.findall(r'^```[^\n]*\n.*?^```', (ROOT/name).read_text(), re.M | re.S)]
    expected = [block.replace('deep_sky_siril_stretch.py init-request \\\n',
                              'deep_sky_siril_stretch.py init-request --batch \\\n')
                for block in PRIOR['code_blocks']]
    assert Counter(blocks) == Counter(expected)
    text = re.sub(r'^```[^\n]*\n.*?^```', '', (ROOT/MAIN).read_text(), flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', text))
    counts = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', text, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
        counts[slug] += 1
    assert set(PRIOR['anchors']) <= anchors


def test_prior_policy_and_implementation_are_replay_only(tmp_path, monkeypatch):
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    assert not sessions.execution_policy_is_current(PRIOR)
    assert sessions._PRE_STAGE7_DOCUMENT_BINDINGS == PRIOR
    session = tmp_path/'old';session.mkdir()
    contract.atomic_write_json(session/'session.json', {'execution_policy': PRIOR['execution_policy']})
    assert protected.implementation(session) == PRIOR['implementation']
    assert protected.profiles_fingerprint(session) == PRIOR['profiles']
    for name, record in PRIOR['documents'].items():
        assert sessions.registered_guidance_reference(PRIOR, {'path': name, **record})
        assert not sessions.registered_guidance_reference(PRIOR, {'path': name, **record, 'sha256': '0'*64})
        assert not sessions.registered_guidance_reference(
            {'execution_policy': contract.EXECUTION_POLICY}, {'path': name, **record})
    replay_and_finalize_without_migration(tmp_path, monkeypatch, HISTORY)


@pytest.mark.parametrize('variant', [None, 'hdr', 'hoo'])
def test_variant_bindings_keep_one_primary(variant):
    rows = contract.protocol_reference_bindings('stretch', variant=variant)
    assert rows[0]['path'] == MAIN and rows[0]['role'] == 'primary'
    assert len(rows) == (1 if variant is None else 2)
    for row in rows:
        assert row['sha256'] == contract.sha256_file(ROOT/row['path'])
    if variant:
        assert rows[1]['path'] == f'references/stage7-{variant}.md'
        assert rows[1]['role'] == 'supporting'
    with pytest.raises(KeyError):
        contract.protocol_reference_bindings('stretch', variant='unknown')


@pytest.mark.parametrize('variant, issue', [(None, None), *(
    (variant, issue) for variant in ('hdr', 'hoo')
    for issue in (None, 'missing', 'wrong_document', 'wrong_role', 'hash', 'both'))])
def test_variant_provenance_requires_its_exact_supporting_body(provenance_case, variant, issue):
    case = provenance_case
    declarations = {'hdr': '# hdr-stretch: {"mask_low":0.15,"mask_high":0.75,"sigma":2}\n',
                    'hoo': '# dualband-stretch: {"parameters":"reports/parameters.json"}\n'}
    declaration = declarations.get(variant, '')
    if issue == 'both':
        declaration += declarations['hoo' if variant == 'hdr' else 'hdr']
    script = case['script']
    script.write_text(declaration + script.read_text())
    record = {**contract.fingerprint(script, include_path=False), 'path': 'scripts/120-delivery.ssf'}
    document = copy.deepcopy(case['payload'])
    document.update(protocol='stretch', script={'path': record['path'], 'sha256': record['sha256']},
                    references=contract.protocol_reference_bindings('stretch', variant=variant))
    if issue == 'missing': document['references'].pop()
    elif issue == 'wrong_document':
        other = 'hoo' if variant == 'hdr' else 'hdr'
        document['references'] = contract.protocol_reference_bindings('stretch', variant=other)
    elif issue == 'wrong_role': document['references'][-1]['role'] = 'primary'
    elif issue == 'hash': document['references'][-1]['sha256'] = '0'*64
    case['provenance'].write_text(json.dumps(document))
    payload = {**case['session_payload'], 'execution_policy': contract.EXECUTION_POLICY}
    def check():
        return validation.validate_ssf_provenance(
            case['provenance'], session=case['session'], session_payload=payload, protocol='stretch',
            script=script, script_fingerprint=record, commands=('requires','set32bits','load','savejpg'),
            policy_fingerprint=case['policy_fingerprint'])
    if issue:
        with pytest.raises(contract.ContractError):check()
    else:
        rows = check()['knowledge_validation']['references']
        assert [row['role'] for row in rows] == (['primary'] if variant is None else ['primary','supporting'])


@pytest.mark.parametrize('mode', ['single', 'hdr', 'hoo'])
def test_pre_split_native_sessions_replay_and_finalize(native, monkeypatch, mode):
    from types import SimpleNamespace
    from . import test_recomposition_sources_v11 as sources
    from . import test_dualband_hoo as hoo_support
    from . import test_deep_sky_siril as support
    from .test_shared_protocol_docs_v12 import _freeze_historical_evidence
    import deep_sky_siril_core as core
    import deep_sky_siril_dualband as dual
    import deep_sky_siril_stretch as stretch
    init = sessions.init_session
    references = contract.protocol_reference_bindings
    def init_starless(*args, **kwargs):
        kwargs['stars'] = 'standalone-starless'
        return init(*args, **kwargs)
    # Build synthetic pre-split evidence with the frozen implementation identity.
    with monkeypatch.context() as old:
        for key in list(contract.EXECUTION_POLICY):
            old.delitem(contract.EXECUTION_POLICY, key)
        for key, value in PRIOR['execution_policy'].items():
            old.setitem(contract.EXECUTION_POLICY, key, value)
        profiles = contract.load_json(protected.PROFILE_PATH)
        profiles['high_dynamic_range']['guidance'] = 'Evaluate masked dual HDR only after a measured single-chain conflict. Protect the final blend.'
        profile_path = native[0]/'historical-stretch-profiles.json'
        profile_path.write_text(json.dumps(profiles, indent=2)+'\n')
        assert contract.fingerprint(profile_path, include_path=False) == PRIOR['profiles']
        old.setattr(protected, 'PROFILE_PATH', profile_path)
        old.setattr(protected, 'implementation', lambda session=None: copy.deepcopy(PRIOR['implementation']))
        old.setattr(sessions, 'init_session', init_starless)
        old.setattr(sessions, 'protocol_reference_bindings', lambda protocol, **_: references(protocol))
        old.setattr(dual, 'protocol_reference_bindings', lambda protocol, **_: references(protocol))
        request = SimpleNamespace(param='single')
        if mode == 'hoo':
            case = hoo_support.dual_case.__wrapped__(native, old, request)
            session, _, original, source, *_ = case
            solved = dual.solve(session, hoo_support.request_for(session, source), '075-hoo', 180)
            assert solved['status'] == 'solved'
            generated = dual.generate(session, solved['report'])
            arguments = dict(protocol='stretch', script_value=generated['script'], source_value=source,
                             primary_output_value=generated['primary_output'], expected_values=generated['expected'], timeout=60)
            receipt = core.run_script(session, **arguments)
            sources.accept(session, receipt['id'])
            parent = Path(generated['primary_output'])
        else:
            linear = sources.linear_case.__wrapped__(native, old, request)
            session, parent, original, source, *_ = sources.source_case.__wrapped__(linear, SimpleNamespace(param=mode))
            receipt = contract.load_json(session/'runs/070-stretch.json')
            arguments = dict(protocol='stretch', script_value=session/receipt['script']['path'], source_value=source,
                             primary_output_value=parent, expected_values=[session/row['path'] for row in receipt['outputs']], timeout=60)
        with old.context() as inspection_patch:
            inspection, direct, diagnostic = support._input_inspect_run(session, original, inspection_patch)
            support._review(session, inspection['id'], diagnostic, parent_preview=direct)
        script, candidate = support._delivery_script(session, parent)
        delivered = core.run_script(session, protocol='delivery.render', script_value=script, source_value=parent,
                                    primary_output_value=candidate.with_suffix('.fit'),
                                    expected_values=[candidate, candidate.with_suffix('.fit')], timeout=60)
        assert delivered['status'] == 'success'
        _freeze_historical_evidence(session, run_id=receipt['id'], snapshot=PRIOR)
    payload = sessions.load_session(session)[1]
    assert not sessions.execution_policy_is_current(payload)
    review = sources.accept(session, delivered['id'])
    if mode == 'hoo':
        dual.parameter_binding(session, payload, solved['report'])
    elif mode == 'single':
        binding = receipt['adaptive_parameters']['parameters']
        stretch.load_parameters(session, payload, session/binding['path'])
    frozen = support._session_bytes(session)
    assert core.run_script(session, **arguments)['replayed']
    assert support._session_bytes(session) == frozen
    selection = support._selection(session, delivered['id'], candidate, review)
    selected = contract.load_json(selection)
    selected.update(stars_required=False, output_contains_stars=False)
    contract.atomic_write_json(selection, selected)
    immutable = {path: path.read_bytes() for path in [session/'session.json', *list((session/'runs').glob('*.json')),
                                                   *list((session/'reviews').glob('*.json')),
                                                   *list((session/'reports').rglob('*.json'))]}
    final = core.finalize_session(session, selection_value=selection, keep_intermediates=True)
    assert final['status'] == 'success'
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True) == final
    assert all(path.read_bytes() == data for path, data in immutable.items())
    fresh = session/'scripts/999-new.ssf';fresh.write_text(script.read_text())
    with pytest.raises(contract.ContractError, match='replay/finalize only'):
        core.run_script(session, protocol='delivery.render', script_value=fresh, source_value=parent,
                        expected_values=[candidate, candidate.with_suffix('.fit')], timeout=30, validate_only=True)
