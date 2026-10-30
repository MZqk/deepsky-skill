"""Executable path examples preserve native-neutral science and CLI bindings."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import re
import shlex

import pytest

from . import test_deep_sky_siril as support
import deep_sky_siril_processing as processing
import deep_sky_siril_validation as validation


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / 'references/stages/stage-04-astrometry-color.md'


@pytest.mark.parametrize('directory', ['session', 'session with spaces'])
@pytest.mark.parametrize('run_id', ['046-neutral', '047-neutral-retry'])
def test_documented_paths_arguments_and_neutral_tail_agree(tmp_path, directory, run_id):
    text = DOCUMENT.read_text()
    code, = re.findall(r'^```python\n(.*?)^```', text, re.M | re.S)
    session = tmp_path / directory
    source = tmp_path / 'source with spaces.xisf'
    cli = ROOT / 'scripts/deep_sky_siril.py'
    for before, after in [
        ('/abs/session', session),
        ('/abs/current-pre-calibration-parent.fit', source),
        ('/abs/starun-siril/scripts/deep_sky_siril.py', cli),
    ]:
        code = code.replace(f'Path("{before}")', f'Path({str(after)!r})')
    code = code.replace('run_id = "046-neutral"\n', f'run_id = "{run_id}"\n', 1)
    namespace = {}
    output = io.StringIO()
    with redirect_stdout(output):
        exec(compile(code, str(DOCUMENT), 'exec'), namespace)
    commands = [shlex.split(line) for line in output.getvalue().splitlines()]
    paths, expected = namespace['paths'], namespace['expected']
    assert len(expected) == len(set(expected)) == 9
    assert [path.suffix for path in expected] == ['.fit'] * 7 + ['.jpg'] * 2
    assert all(path.is_relative_to(session) for path in expected)
    assert paths['provenance'] == paths['script'].with_suffix('.provenance.json')
    assert paths['regions'] == session / 'reports/046-neutral/regions.json'
    assert commands[0] == commands[1] + ['--validate-only']
    assert commands[1] == namespace['run_args']
    assert commands[2] == namespace['review_args']
    assert commands[3] == commands[2] + ['--review', str(paths['review'])]
    assert commands[1][commands[1].index('--source') + 1] == str(source)
    assert [commands[1][i + 1] for i, value in enumerate(commands[1]) if value == '--expect'] == list(map(str, expected))
    for command in commands:
        parsed = support.cli.build_parser().parse_args(command[3:])
        assert parsed.session == str(session)
    assert not session.exists() and not source.exists()

    native, = [block for block in re.findall(r'^```ssf\n(.*?)^```', text, re.M | re.S)
               if '"method":"neutral"' in block]
    native = native.replace('/abs/current-parent.xisf', str(source))
    for index, channel in enumerate('rgb'):
        raw = paths['raw'][index]
        native = native.replace(f'/abs/session/artifacts/040-raw-{channel}', str(raw.with_suffix('')))
        native = native.replace(f'artifacts/040-raw-{channel}', raw.relative_to(session).with_suffix('').as_posix())
        native = native.replace(f'/abs/session/artifacts/040-corrected-{channel}.fit', str(paths['corrected'][index]))
    for before, after in [
        ('/abs/session/artifacts/040-neutral.fit', paths['primary']),
        ('/abs/session/previews/040-neutral-parent', paths['parent_preview'].with_suffix('')),
        ('/abs/session/previews/040-neutral-candidate', paths['candidate_preview'].with_suffix('')),
    ]:
        native = native.replace(before, str(after))
    coefficients = {'background': [.02, .04, .03], 'gains': [1, 1, 1], 'target': .04}
    rows = [shlex.split(line) for line in native.splitlines() if line and not line.startswith('#')]
    for index, row in enumerate(row for row in rows if row[0] == 'pm'):
        variable = paths['raw'][index].relative_to(session).with_suffix('').as_posix()
        row[1] = f'(${variable}$ - {coefficients["background"][index]:.17g}) * 1 + {coefficients["target"]:.17g}'
    display = processing._validate_neutral_script(
        session, source, paths['primary'], rows, coefficients, policy='scientific_native_flow_v2')
    assert display['parent_preview'] == paths['parent_preview'].relative_to(session).as_posix()
    assert display['candidate_preview'] == paths['candidate_preview'].relative_to(session).as_posix()
    writes = []
    for row in rows:
        if row[0] == 'split':
            writes.extend(validation._normalized_write('split', value) for value in row[1:])
        elif row[0] in {'save', 'savejpg'}:
            writes.append(validation._normalized_write(row[0], row[1]))
        elif row[0] == 'rgbcomp':
            writes.append(Path(next(value.removeprefix('-out=') for value in row if value.startswith('-out='))))
    assert writes == expected
    assert not session.exists()
