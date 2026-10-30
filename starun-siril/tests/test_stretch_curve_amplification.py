"""Diagnostic-only Stage 7 curve amplification: analytic slope and span gain."""
import json
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_preflight as checks
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_v2 as protected
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration
from ._stretch_factory import with_constraint_review
from .test_deep_sky_siril_stretch import context
from .test_stretch_constraint_preflight import inspect
from .test_stretch_curve_effects import short_goals
from .test_stretch_routing_v29 import routing_case

ROOT = Path(__file__).parents[1]


def reference_slope(u, low, mid, high):
    x = (u-low)/(high-low)
    if x <= 0 or x >= 1:
        return 0.
    denominator = mid + (1-2*mid)*x
    return mid*(1-mid)/(denominator*denominator*(high-low))


@pytest.mark.parametrize('low,mid,high', [(.01, .1, 1.), (0., .5, 1.), (.02, .03, .9)])
def test_mtf_slope_matches_its_float64_reference(low, mid, high):
    for u in np.linspace(low+1e-6, high-1e-6, 5):
        slope, clamped = checks.mtf_slope(u, low, mid, high)
        assert not clamped
        assert slope == pytest.approx(reference_slope(u, low, mid, high), rel=1e-12, abs=1e-15)


def test_mtf_slope_is_flat_and_flagged_outside_the_supported_interval():
    assert checks.mtf_slope(.005, .01, .1, 1.) == (0., True)
    assert checks.mtf_slope(1., .01, .1, 1.) == (0., True)


def test_channel_medians_and_luminance_aggregation_keep_channel_order():
    data = np.zeros((3, 4, 4), dtype=np.float64)
    data[0], data[1], data[2] = .1, .2, .3
    regions = [{'id': 'sky', 'role': 'background', 'rect': [0, 0, 4, 4]}]
    assert checks.channel_medians(data, regions)['sky'] == pytest.approx([.1, .2, .3])
    assert checks.channel_medians(data[:1], regions)['sky'] == pytest.approx([.1])
    row = checks._slope_row([.2, .3, .4], 0., .5, 1.)
    expected = sum(w*s for w, s in zip(checks.LUMINANCE_WEIGHTS, row['channels']))
    assert row['luminance'] == pytest.approx(expected)
    mono = checks._slope_row([.2], 0., .5, 1.)
    assert mono['luminance'] == mono['channels'][0]


def test_mtf_amplification_matches_predicted_span_gain():
    rng = np.random.default_rng(4)
    data = np.clip(rng.normal(.08, .02, (3, 48, 48)), 0, 1)
    regions = [{'id': 'sky', 'role': 'background', 'rect': [8, 8, 24, 24]},
               {'id': 'faint', 'role': 'faint', 'rect': [24, 24, 40, 40]}]
    before = protected.statistics(data, regions)
    mapped = checks.mtf_pixels(data, .01, .1, 1.)
    values = protected.statistics(mapped, regions)
    block = checks.mtf_amplification(values, before, regions, {'faint': 'bright'},
                                     checks.channel_medians(data, regions), .01, .1, 1.)
    for rid in ('sky', 'faint'):
        assert block['span_gain']['regions'][rid]['ratio'] == pytest.approx(
            values[rid]['span']/before[rid]['span'])
        assert block['slope']['regions'][rid]['clamped'] is False
    assert block['aggregate']['subject_over_background']['span_gain'] == pytest.approx(
        values['faint']['span']/before['faint']['span'] / (values['sky']['span']/before['sky']['span']))


def test_zero_span_baseline_and_missing_subject_are_null_with_reasons():
    flat = np.full((3, 24, 24), .1)
    regions = [{'id': 'sky', 'role': 'background', 'rect': [8, 8, 16, 16]}]
    before = protected.statistics(flat, regions)
    values = protected.statistics(flat, regions)
    block = checks.mtf_amplification(values, before, regions, {},
                                     checks.channel_medians(flat, regions), .01, .1, 1.)
    assert block['span_gain']['regions']['sky']['ratio'] is None
    assert block['span_gain']['regions']['sky']['reason'] == 'zero_span_baseline'
    assert block['aggregate']['subject_over_background']['reason'] == 'no_subject_roi'
    json.dumps(block, allow_nan=False)


def test_ghs_reuses_measured_span_and_stays_pending_without_a_prefix():
    regions = [{'id': 'sky', 'role': 'background', 'rect': [0, 0, 4, 4]},
               {'id': 'faint', 'role': 'faint', 'rect': [4, 0, 8, 4]}]
    diagnostics = {'regions': {
        'sky': {'span': {'before': .01, 'after': .03, 'delta': .02, 'relative_delta': 2.}},
        'faint': {'span': {'before': .02, 'after': .05, 'delta': .03, 'relative_delta': 1.5}}}}
    block = checks.ghs_amplification(diagnostics, regions, {'faint': 'bright'})
    assert block['slope']['regions'] is None
    assert block['slope']['reason'] == 'ghs_has_no_analytic_derivative'
    assert block['span_gain']['regions']['sky']['ratio'] == pytest.approx(3.)
    assert block['aggregate']['subject_over_background']['span_gain'] == pytest.approx(2.5/3.)
    pending = checks.ghs_amplification(None, regions, {})
    assert pending['span_gain']['regions'] is None
    assert pending['span_gain']['reason'] == 'pending_native_measurement'


def test_amplification_adds_no_gate_and_v2_reproduces_its_frozen_report(routing_case):
    goals = short_goals(routing_case)
    current, path = inspect(routing_case, with_constraint_review(goals, 'exception'), mode='exception')
    goals = contract.load_json(path)
    assert current['curve_effects'] and all('curve_amplification' in row for row in current['curve_effects'])
    session, _, payload, _, request, _ = routing_case
    prior = contract.load_json(ROOT/'tests/fixtures/stretch-curve-amplification-prior.json')
    plan = {'mode': 'exception', 'run_id': '071-amplification', 'source': request['source'],
            'regions': base.record(session, session/request['regions']['path']),
            'goals': base.record(session, path), 'full_source': None}
    requests = routes.bound_requests_for(plan, goals, {})
    legacy_payload = {**payload, 'execution_policy': prior['execution_policy']}
    frozen = checks.assess(session, legacy_payload, plan, goals, requests)
    assert all('curve_amplification' not in row for row in frozen['curve_effects'])
    for key in ('status', 'issues', 'warnings', 'output_reachability', 'constraints', 'source', 'regions', 'goals'):
        assert frozen[key] == current[key]
    report = session/'reports/071-amplification/constraint-preflight.json'
    report.parent.mkdir(parents=True)
    contract.atomic_write_json(report, frozen)
    plan['constraint_preflight'] = base.record(session, report)
    checks.verify(session, legacy_payload, plan, goals, requests)


def test_outgoing_v2_preflight_policy_is_preserved(tmp_path, monkeypatch):
    fixture = ROOT/'tests/fixtures/stretch-curve-amplification-prior.json'
    prior = contract.load_json(fixture)
    assert fixture.read_bytes() == (ROOT/'references/stretch-curve-amplification-history.json').read_bytes()
    assert sessions._PRE_CURVE_AMPLIFICATION_BINDINGS == prior
    assert checks.enabled({'execution_policy': prior['execution_policy']})
    assert checks.effects_enabled({'execution_policy': prior['execution_policy']})
    assert not checks.amplification_enabled({'execution_policy': prior['execution_policy']})
    assert_contract_domains(prior['execution_policy'])
    replay_and_finalize_without_migration(tmp_path, monkeypatch, fixture)
