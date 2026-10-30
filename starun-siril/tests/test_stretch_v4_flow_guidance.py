"""Executable v4 guidance, prefix preflight and exact outgoing document replay."""
import copy
import json
from pathlib import Path
import re
import subprocess

import pytest

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_v2 as protected
from . import test_deep_sky_siril as support
from .test_deep_sky_siril_stretch import context
from .test_stretch_v22 import case as stretch_case
from .test_recomposition_proposals import case as replay_case
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stages/stage-07-stretch.md'
PRIOR = contract.load_json(ROOT/'tests/fixtures/stretch-v4-flow-guidance-prior.json')


def test_current_example_uses_bound_default_and_explicit_modes():
    text=(ROOT/DOCUMENT).read_text()
    section=text.split('<a id="default-v4-flow"></a>',1)[1].split('<a id="reference-driven-baseline"></a>',1)[0]
    assert '--mode profile' in section
    assert 'goals.v4' in text and 'strategy.v3' in text
    assert '--batch' in section and '--stepwise' in text
    assert 'profile-request.json' in section
    assert '--comparison' in section and 'target_change' in section
    assert 'conflict_review' in section and 'affected_regions' in section
    assert 'new_image_benefit_from_revision=false' in section
    assert 'goals.v1/v2' in text and '历史' in text


def test_outgoing_registration_is_exact_and_preservation_is_explicit():
    # prior=stretch-v4-flow-guidance-prior.json: chroma_denoise, color_preservation, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._V4_FLOW_REPLAY_REFERENCES == PRIOR['documents']
    assert sessions._V4_FLOW_REPLAY_POLICY_HASH == contract.stable_hash(PRIOR['execution_policy'])
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64}, {'size': record['size']+1}):
        assert not sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_outgoing_stretch_replay_and_finalize_preserve_evidence(replay_case, monkeypatch):
    from ._policy_pins import replay_and_finalize_without_migration
    session=replay_case[0]
    directory=session.parent/'historical-document-fixture';directory.mkdir()
    snapshot=directory/'snapshot.json';contract.atomic_write_json(snapshot,PRIOR)
    replay_and_finalize_without_migration(directory,monkeypatch,snapshot)
