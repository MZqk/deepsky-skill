"""Bounded highlight admission, conservative blackpoints and MTF suggestions."""
import numpy as np
import pytest
import time
import json

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_preflight as preflight
import deep_sky_siril_stretch_v2 as protected
from .test_stretch_routing_v29 import routing_case, prepare
from .test_deep_sky_siril_stretch import context, BINARY
from .test_deep_sky_siril import _fake_probe


def test_highlight_limit_is_per_channel_and_includes_boundary():
    before = np.full((3, 100, 100), .2)
    before[:, 40:60, 40:60] = .3
    regions = [{'id': 'sky', 'role': 'background', 'rect': [10, 10, 30, 30]},
               {'id': 'faint', 'role': 'faint', 'rect': [40, 40, 60, 60]}]
    after = before.copy()
    after[0, 0, 0] = 1
    after[1, 0, 1] = 1
    result = protected.audit(before, after, regions, [], {'faint': 'bright'}, highlight_budget=True)
    assert result['eligible'] and result['highlight_budget']['rates'] == [.0001, .0001, 0]
    assert result['highlight_budget']['warning']
    assert 'new_highlight_crossing' in protected.audit(before, after, regions, [], {'faint': 'bright'})['reasons']
    after[0, 0, 2] = 1
    assert 'new_highlight_crossing' in protected.audit(before, after, regions, [], {'faint': 'bright'}, highlight_budget=True)['reasons']


def test_blackpoint_reserves_remaining_shadow_budget():
    data = np.linspace(.001, .1, 1_000_000).reshape(1, 1000, 1000)
    data[0, 0, :2] = 0
    conservative = stretch.safe_blackpoint([data], headroom=True)
    legacy = stretch.safe_blackpoint([data])
    existing = int((data <= 0).sum())
    assert conservative < legacy
    assert int(((data > 0) & (data <= conservative)).sum()) <= (99-existing)//10
    assert int((data <= legacy).sum()) <= 99


def test_cumulative_prefix_cannot_hide_two_individually_permitted_crossings():
    before = np.full((1, 100, 100), .2)
    before[:, 40:60, 40:60] = .3
    regions = [{'id': 'sky', 'role': 'background', 'rect': [10, 10, 30, 30]},
               {'id': 'faint', 'role': 'faint', 'rect': [40, 40, 60, 60]}]
    first = before.copy(); first[0, 0, 0] = 1
    second = first.copy(); second[0, 0, 1] = 1
    assert protected.audit(before, first, regions, [], {'faint': 'bright'}, highlight_budget=True)['eligible']
    assert protected.audit(first, second, regions, [], {'faint': 'bright'}, highlight_budget=True)['eligible']
    assert 'new_highlight_crossing' in protected.audit(before, second, regions, [], {'faint': 'bright'}, highlight_budget=True)['reasons']


def test_formal_overlay_is_remeasured_and_cannot_be_replaced(tmp_path):
    from PIL import Image
    (tmp_path/'reports').mkdir()
    preview = tmp_path/'preview.jpg'
    Image.new('RGB', (100, 100)).save(preview)
    before = np.full((3, 100, 100), .2)
    after = before.copy(); after[0, 10, 20] = 1
    primary = tmp_path/'candidate.fit'
    binding = protected.formal_highlight_overlay(tmp_path, before, after, preview, primary, write=True)
    assert protected.formal_highlight_overlay(tmp_path, before, after, preview, primary) == binding
    (tmp_path/binding['path']).write_bytes(b'changed')
    with pytest.raises(contract.ContractError, match='location evidence changed'):
        protected.formal_highlight_overlay(tmp_path, before, after, preview, primary)


@pytest.mark.parametrize('crossings,passes', [(1, True), (2, False)])
def test_hdr_protection_uses_the_same_per_channel_budget(tmp_path, monkeypatch, crossings, passes):
    from .test_deep_sky_siril_stretch import write
    (tmp_path/'reports').mkdir()
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': contract.EXECUTION_POLICY}))
    before = np.full((1, 100, 100), .2, dtype=np.float32)
    before[:, 40:60, 40:60] = .3
    after = before.copy(); after[0, 0, :crossings] = 1
    source = write(tmp_path/'source.fit', before)
    candidate = write(tmp_path/'candidate.fit', after)
    regions = [{'id': 'sky', 'role': 'background', 'rect': [10, 10, 30, 30]},
               {'id': 'faint', 'role': 'faint', 'rect': [40, 40, 60, 60]}]
    region_path = tmp_path/'reports/regions.json'
    region_path.write_text(json.dumps({'regions': regions}))
    request_path = tmp_path/'reports/request.json'
    request_path.write_text(json.dumps({'constraints': [], 'targets': {'faint': 'bright'}}))
    binding = {'request': stretch.record(tmp_path, request_path), 'regions': stretch.record(tmp_path, region_path),
               'implementation': {}, 'profiles': {}, 'full_source': None}
    monkeypatch.setattr(protected, 'implementation_matches', lambda *_: True)
    monkeypatch.setattr(protected, 'profiles_fingerprint', lambda *_: {})
    if passes:
        result = protected.measure_protection(tmp_path, source, candidate, binding)
        assert result['candidate']['highlight_budget']['rates'] == [.0001]
    else:
        with pytest.raises(contract.ContractError, match='HDR blend fails'):
            protected.measure_protection(tmp_path, source, candidate, binding)


@pytest.mark.parametrize('crossings,passes', [(1, True), (2, False)])
def test_hoo_final_palette_uses_the_same_per_channel_budget(tmp_path, monkeypatch, crossings, passes):
    import deep_sky_siril_dualband as dual
    from PIL import Image
    (tmp_path/'reports').mkdir()
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': contract.EXECUTION_POLICY}))
    data = np.full((3, 100, 100), .1)
    ha = data[:1].copy(); oiii = data[1:2].copy()
    hs = ha.copy(); hs[0, 0, :crossings] = 1
    candidate = np.concatenate((hs, .8*oiii+.2*hs, oiii))
    paths = {'source.fit': data, 'ha.fit': ha, 'oiii.fit': oiii, 'hs.fit': hs,
             'os.fit': oiii, 'candidate.fit': candidate, 'rgb.fit': data}
    monkeypatch.setattr(dual, 'pixels', lambda path: paths[path.name])
    monkeypatch.setattr(dual, 'bound_file', lambda session, binding, *args: session/binding['path'])
    monkeypatch.setattr(protected, 'measure_output', lambda *_: {'audit': {'constraints': []}})
    regions = [{'id': 'sky', 'role': 'background', 'rect': [10, 10, 30, 30]}]
    (tmp_path/'reports/regions.json').write_text(json.dumps({'regions': regions}))
    (tmp_path/'reports/child.json').write_text(json.dumps({'bindings': {'derived_source': {'path': 'ha.fit'}}}))
    report = {'request': {'schema': 'starun-siril.dualband-request.v2'},
              'derived_sources': {'ha': {'path': 'ha.fit'}, 'oiii': {'path': 'oiii.fit'}},
              'chains': {key: {'path': 'reports/child.json'} for key in ('rgb', 'ha', 'oiii')}}
    (tmp_path/'reports/parameters.json').write_text(json.dumps(report))
    Image.new('RGB', (100, 100)).save(tmp_path/'candidate.jpg')
    binding = {'parameters': {'path': 'reports/parameters.json'}, 'regions': {'path': 'reports/regions.json'},
               'previews': {'candidate': 'candidate.jpg'},
               'layers': {'ha': 'ha.fit', 'oiii': 'oiii.fit', 'ha-stretched': 'hs.fit',
                          'oiii-stretched': 'os.fit', 'oiii-control': 'os.fit',
                          'rgb-baseline': 'rgb.fit', 'candidate': 'candidate.fit'}}
    if passes:
        result = dual.measure_outputs(tmp_path, tmp_path/'source.fit', tmp_path/'candidate.fit', binding, write_diagnostics=True)
        assert result['protection']['highlight_budget']['rates'] == [.0001, 0, 0]
        assert result['protection']['highlight_overlay']
        assert dual.measure_outputs(tmp_path, tmp_path/'source.fit', tmp_path/'candidate.fit', binding) == result
    else:
        with pytest.raises(contract.ContractError, match='new highlight endpoint'):
            dual.measure_outputs(tmp_path, tmp_path/'source.fit', tmp_path/'candidate.fit', binding, write_diagnostics=True)


@pytest.mark.parametrize('channels', [1, 3])
def test_mtf_suggestion_is_read_only_and_uses_real_channel_pixels(routing_case, monkeypatch, channels):
    root, source, payload, _, request, _ = routing_case
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda _session, value, _manifest: value)
    real_pixels = stretch.pixels
    if channels == 3:
        monkeypatch.setattr(stretch, 'pixels', lambda path: np.concatenate([real_pixels(path), real_pixels(path)*1.05, real_pixels(path)*.95]))
    monkeypatch.setattr(stretch.subprocess, 'run', lambda *args, **kwargs: pytest.fail('No native trial is permitted'))
    before = set(root.rglob('*'))
    result = preflight.suggest_mtf(root, '@input', request['regions']['path'], 'faint', .2)
    assert result['status'] == 'proposed'
    assert result['predicted_statistics']['faint']['median'] == pytest.approx(.2, abs=preflight.MAPPING_TOLERANCE)
    assert result['requires_native_verification'] and result['requires_visual_review']
    assert set(root.rglob('*')) == before
    unreachable = preflight.suggest_mtf(root, '@input', request['regions']['path'], 'faint', .999999)
    assert unreachable['status'] == 'no_solution'
    assert not list(root.glob('reports/*/scientific-attempt.json'))


def test_highlight_overlay_is_bound_and_drift_is_rejected(routing_case, monkeypatch):
    root, source, _, _, _, regions = routing_case
    native = stretch.NativeTrials.run
    def crossing(self, parent, chain):
        output, evidence = native(self, parent, chain)
        from astropy.io import fits
        data = fits.getdata(output)
        data.flat[0] = 1
        fits.writeto(output, data, overwrite=True)
        evidence['output_sha256'] = contract.sha256_file(output)
        return output, evidence
    monkeypatch.setattr(stretch.NativeTrials, 'run', crossing)
    item = prepare(routing_case)['candidates']['profile']
    result = stretch.solve_stretch_parameters(root, item['request'], item['run_id'])
    assert result['status'] == 'ineligible'
    row = result['report']['steps'][0]['trials'][0]
    overlay = root/row['retained']['candidate-highlight.png']['path']
    assert overlay.is_file()
    protected.verify(root, result['report'], source, regions, incomplete=True)
    overlay.write_bytes(b'changed')
    with pytest.raises(contract.ContractError):
        protected.verify(root, result['report'], source, regions, incomplete=True)


def test_suggested_mtf_matches_native_siril(context, monkeypatch):
    if not BINARY.exists():
        pytest.skip('Native Siril is unavailable')
    root, source, _, _, request, _ = context
    from .test_stretch_v22 import make_v2
    make_v2(root, source, request)
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda _session, value, _manifest: value)
    result = preflight.suggest_mtf(root, '@input', request['regions']['path'], 'faint', .2)
    assert result['status'] == 'proposed'
    directory = root/'reports/native-mtf-suggestion'; directory.mkdir()
    oracle = stretch.NativeTrials(root, _fake_probe(BINARY)['tools']['siril_cli'], directory, directory, time.monotonic()+60)
    output, _ = oracle.run(source, [{'command': 'mtf', 'parameters': [str(result['blackpoint']), str(result['midtones']), '1']}])
    expected = preflight.mtf_pixels(stretch.pixels(source), result['blackpoint'], result['midtones'], 1.)
    assert np.max(np.abs(expected-stretch.pixels(output))) < preflight.MAPPING_TOLERANCE


def test_outgoing_policy_replays_without_migration(tmp_path, monkeypatch):
    from ._policy_pins import replay_and_finalize_without_migration
    history = stretch.ROOT/'references/stage7-adaptive-history.json'
    prior = contract.load_json(history)
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, history)
