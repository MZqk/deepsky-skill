"""Opt-in native regression on two SHA-bound external linear masters."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from .test_native_processing import native, read_fits_pixels
from deep_sky_siril_contract import sha256_file
from deep_sky_siril_tooling import discover_siril, _subprocess_environment

FIXTURES = Path(__file__).parent / 'fixtures' / 'neutral'
CORPUS = json.loads((FIXTURES / 'corpus.json').read_text())


@pytest.mark.parametrize('case', CORPUS['cases'], ids=lambda case: case['id'])
def test_native_neutral_real_corpus(tmp_path, case):
    root = os.environ.get(CORPUS['source_root_environment'])
    if not root:
        pytest.skip('External neutral corpus root not configured; no real-image acceptance claimed')
    source = Path(root).expanduser().resolve() / case['source_filename']
    if not source.is_file():
        pytest.skip('External SHA-bound neutral corpus source unavailable')
    assert sha256_file(source) == case['source_sha256']
    binary = discover_siril()
    if binary is None:
        pytest.skip('Native Siril unavailable; real corpus not validated')
    session = tmp_path / case['id']
    for folder in ('artifacts','previews','reports/040-neutral'):
        (session / folder).mkdir(parents=True)
    regions = session / 'reports/040-neutral/regions.json'
    shutil.copyfile(FIXTURES / case['id'] / 'regions.json', regions)
    text = (FIXTURES / case['id'] / '040-neutral.ssf').read_text().replace('__SESSION__',str(session)).replace('__SOURCE__',str(source))
    script = session / 'neutral.ssf';script.write_text(text)
    candidate = session / 'artifacts/040-neutral.fit'
    payload = {'input': {'path': str(source)}, 'context': {'input_state':case['input_state'], 'channel_mode':case['channel_mode'],'offline':True}}
    metadata = native.prepare_processing(session,payload,'color.calibrate',source,candidate,text,{},None,None,None,None)
    coefficients = metadata['color_calibration']['coefficients']
    for key in ('background','target','gains'):
        assert coefficients[key] == case['coefficients'][key]
    config = session / 'siril.ini'
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nuse_spcc_repository=false\n')
    def execute(path):
        result = subprocess.run([str(binary),f'--initfile={config}',f'--directory={session}','--offline',f'--script={path}'],
                                capture_output=True,text=True,timeout=120,env=_subprocess_environment('siril_runtime'))
        assert result.returncode == 0, result.stdout + result.stderr
    execute(script)
    checks = native.validate_processing_outputs(session,source,candidate,metadata)['color_calibration']
    assert max(checks['per_channel_max_abs']) <= 1e-6
    report = checks['neutral_validation']
    assert report['recommended_verdict'] == 'uncertain'
    assert report['method'] == 'pure_additive'
    assert report['background_partition'] == case['background_partition']
    assert all(channel['shadow']['new'] == 0 for channel in report['safety']['channels'])
    if case['id'] == 'm31':
        assert report['benefit']['summary']['candidate']['median'] < report['benefit']['summary']['parent']['median']
        assert report['safety']['channels'][1]['highlight']['new'] > 0
        assert report['safety']['channels'][2]['highlight']['new'] > 0
    else:
        assert report['benefit']['numerically_improved_fraction'] == .5
        assert all(channel['highlight']['new'] == 0 for channel in report['safety']['channels'])
    before = (sha256_file(source),sha256_file(candidate))
    display = session / 'matched-preview.ssf'
    display.write_text((FIXTURES / case['id'] / 'matched-preview.ssf').read_text().replace('__SESSION__',str(session)).replace('__SOURCE__',str(source)))
    execute(display)
    assert before == (sha256_file(source),sha256_file(candidate))
    assert (session / 'previews/040-neutral-matched-parent.jpg').is_file()
    assert (session / 'previews/040-neutral-matched-candidate.jpg').is_file()
    assert read_fits_pixels(candidate)[0].shape == read_fits_pixels(source)[0].shape
