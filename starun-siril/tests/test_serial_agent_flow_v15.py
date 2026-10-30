"""Retired batch interfaces and immutable v14/v15/v16 replay under current guidance."""
from collections import Counter
import json
from pathlib import Path
import re

import pytest

from . import test_deep_sky_siril as support
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
import package_release
from ._policy_pins import assert_pinned_history, assert_document_fingerprint_replayable


ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / 'tests/fixtures/serial-agent-v14.json').read_text())
V15_BASELINE = json.loads((ROOT / 'tests/fixtures/stage3-assessment-v15.json').read_text())
V16_BASELINE = json.loads((ROOT / 'tests/fixtures/stage4-preflight-v16.json').read_text())
V18_BASELINE = json.loads((ROOT / 'tests/fixtures/vivid-policy-v18.json').read_text())
V17_BASELINE = json.loads((ROOT / 'tests/fixtures/benefit-policy-v17.json').read_text())


@pytest.mark.parametrize('baseline,policy', [
    (BASELINE, sessions._REVIEWED_BATCH_EXECUTION_POLICY),
    (V15_BASELINE, sessions._SERIAL_AGENT_EXECUTION_POLICY),
    (V16_BASELINE, sessions._ASSESSED_STAGE3_EXECUTION_POLICY),
], ids=['v14', 'v15', 'v16'])
def test_complete_history_and_unchanged_scientific_behavior(baseline, policy):
    assert_pinned_history(baseline, session_constant=policy, fingerprint_loop=False)
    for name, record in baseline['documents'].items():
        assert_document_fingerprint_replayable(name, record)
        text = (ROOT / name).read_text()
        if name == 'references/stages/stage-07-stretch.md':
            text += ''.join((ROOT/f'references/stage7-{kind}.md').read_text()
                            for kind in ('hdr', 'hoo', 'diagnostics', 'legacy'))
        current = re.findall(r'^```ssf[^\n]*\n.*?^```', text, re.M | re.S)
        current = [re.sub(r'^# tone-adjust:.*\n', '', block, flags=re.M) for block in current]
        original = baseline['scientific_blocks'][name]
        if name == 'references/stages/stage-05-linear-restoration.md':
            # Linear denoise now expands an explicit proposal; the RL recipe is unchanged.
            original = [block for block in original if '\ndenoise ' not in block]
            current = [block for block in current if '\ndenoise ' not in block]
        if name == 'references/stages/stage-09-stars-recompose.md':
            current = [re.sub(r'^# recomposition-proposal:.*\n', '', block, flags=re.M) for block in current]
        if name == 'references/stages/stage-04-astrometry-color.md':
            current = [block.replace(',"reference":{"path":"reports/pcc-evidence.txt","sha256":"ACTUAL_SHA256","size":123}', '') for block in current]
        if name == 'references/shared-protocols/color-finish.md':
            current = [block.replace('min($artifacts/088-mask-smoothed$, $artifacts/088-mask-base$)', '$artifacts/088-mask-smoothed$ * $artifacts/088-mask-base$') for block in current]
        if name == 'references/stages/stage-10-delivery-finishing.md':
            original = [block for block in original if '\ndenoise ' not in block]
        if name == 'references/stages/stage-07-stretch.md':
            assert Counter(current) == Counter(original)
        else:
            assert current == original
    for name, record in baseline['unchanged_runtime'].items():
        if name == 'scripts/siril_starnet_linear.py':
            # v18 replaces only the above-one rejection with a documented native advisory.
            assert record == V17_BASELINE['adapter']
            lock = contract.load_json(ROOT / 'references/starnet-linear-source.json')
            assert lock['adapter_sha256'] == contract.sha256_file(ROOT / name)
            continue
        if baseline is BASELINE and name == 'scripts/deep_sky_siril.py':
            # v15 clarified the filesystem source argument without changing CLI options.
            record = V15_BASELINE['unchanged_runtime'][name]
        if name in {'scripts/deep_sky_siril.py', 'scripts/deep_sky_siril_core.py', 'scripts/deep_sky_siril_processing.py', 'scripts/deep_sky_siril_validation.py', 'scripts/query_siril_manual.py'}:
            assert V18_BASELINE['runtime'][name] == record
            continue
        if name in {'scripts/deep_sky_siril_stretch.py', 'scripts/deep_sky_star_reduction.py'}:
            prior = json.loads((ROOT/'tests/fixtures/dualband-policy-v19.json').read_text())
            assert prior['runtime'][name] == record
            continue
        if name == 'scripts/deep_sky_siril_local_contrast.py':
            prior = json.loads((ROOT/'tests/fixtures/multiscale-policy-v20.json').read_text())
            assert prior['runtime'][name] == record
            continue
        if name in {'scripts/deep_sky_siril_hdr.py', 'scripts/package_release.py'}:
            prior = json.loads((ROOT/'tests/fixtures/stretch-policy-v21.json').read_text())
            assert prior['runtime'][name] == record
            continue
        if name == 'scripts/deep_sky_siril_tooling.py':
            prior = json.loads((ROOT/'tests/fixtures/deepsnr-prior.json').read_text())
            assert prior['documents'][name] == record
            continue
        if name in {'scripts/deep_sky_siril_background.py', 'scripts/deep_sky_siril_metrics.py',
                    'scripts/siril_auto_samples.py', 'scripts/filter_metadata.py'}:
            assert_document_fingerprint_replayable(name, record)
            continue
        assert contract.fingerprint(ROOT / name, include_path=False) == record
    from .test_knowledge_contract import assert_v18_command_policy
    assert_v18_command_policy(baseline['command_policy'])


def test_retired_batch_modules_are_absent_from_sources_and_release():
    files = package_release.STATIC_RELEASE_FILES
    allowlist = (ROOT / 'release-files.txt').read_text().splitlines()
    for name in ('batch_starun_runner.py', 'deep_sky_siril_batch_materials.py'):
        assert not (ROOT / 'scripts' / name).exists()
        assert 'scripts/' + name not in files
        assert 'scripts/' + name not in allowlist
    assert support.cli.PUBLIC_COMMANDS == ('probe', 'init', 'run', 'review-check', 'confirm-input', 'finalize', 'status')


@pytest.mark.parametrize('tamper', ['provenance', 'input'])
@pytest.mark.parametrize('baseline', [BASELINE, V15_BASELINE, V16_BASELINE], ids=['v14', 'v15', 'v16'])
def test_history_replays_and_finalizes_but_rejects_new_work_and_drift(tmp_path, monkeypatch, tamper, baseline):
    session, source, _ = support._session(tmp_path, monkeypatch)
    receipt, candidate = support._delivery_run(session, source, monkeypatch)
    payload = contract.load_json(session / 'session.json')
    assert payload['execution_policy'] == contract.EXECUTION_POLICY
    payload['execution_policy'] = baseline['execution_policy']
    payload.pop('session_sha256')
    payload['session_sha256'] = contract.stable_hash(payload)
    contract.atomic_write_json(session / 'session.json', payload)
    manifest = contract.load_json(session / 'manifest.json')
    for entry in manifest['runs']:
        path = session / 'runs' / entry['receipt']
        run = contract.load_json(path)
        references = run['knowledge_validation']['references']
        if run['id'] == receipt['id']:
            references.append({'path': 'SKILL.md', 'role': 'supporting', **baseline['documents']['SKILL.md']})
        for record in references:
            previous = baseline['documents'].get(record['path'])
            if previous:
                record.update(previous)
        provenance_path = session / run['script_provenance']['path']
        provenance = contract.load_json(provenance_path)
        provenance['references'] = [{key: record[key] for key in ('path', 'sha256', 'role')} for record in references]
        contract.atomic_write_json(provenance_path, provenance)
        run['script_provenance'].update(contract.fingerprint(provenance_path, include_path=False))
        contract.atomic_write_json(path, run)
        entry['receipt_sha256'] = contract.sha256_file(path)
    contract.atomic_write_json(session / 'manifest.json', manifest)
    args = dict(protocol='delivery.render', script_value=session / receipt['script']['path'],
                source_value=source, expected_values=[candidate, candidate.with_suffix('.fit')], timeout=30)
    before = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert support._session_bytes(session) == before
    review = support._review(session, receipt['id'], candidate)
    assert core.check_review(session, receipt['id'], review)['status'] == 'review_valid'
    fresh = session / 'scripts/999-new.ssf'
    fresh.write_text('requires 1.4.4 1.5.0\nset32bits\nclose\n')
    before = support._session_bytes(session)
    for validate_only in (False, True):
        with pytest.raises(contract.ContractError) as raised:
            core.run_script(session, **{**args, 'script_value': fresh}, validate_only=validate_only)
        assert raised.value.code == 'legacy_session_read_only'
    assert support._session_bytes(session) == before
    selection = support._selection(session, receipt['id'], candidate, review)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    before = support._session_bytes(session)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert support._session_bytes(session) == before
    changed = source if tamper == 'input' else session / receipt['script_provenance']['path']
    changed.write_bytes(changed.read_bytes() + b' ')
    with pytest.raises(contract.ContractError):
        core.run_script(session, **args)
    with pytest.raises(contract.ContractError):
        core.finalize_session(session, selection_value=selection, keep_intermediates=True)
