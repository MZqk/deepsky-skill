"""Coverage, background MTF and opt-in native composition regression tests."""
from __future__ import annotations

import sys
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / 'scripts', _SKILL / 'tests'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


import argparse
import contextlib
import io
import json
import math
from pathlib import Path
import random
import statistics
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import compose_channels as composition
import test_compose_channels as fixtures
from composition_quality import background_mtf, edge_coverage, largest_rectangle, sample_statistics
from fits_probe import build_card, data_offset, pad_to_block
from test_compose_channels import make_prepared, write_values
from test_prepare_composition import values

REAL = False


def make_pattern(root):
    make_prepared(root, [(label, 0.1) for label in ("L", "R", "G", "B", "HA", "SII", "OIII")], bundle=True)
    for index, label in enumerate(("L", "R", "G", "B", "HA", "SII", "OIII")):
        pixels = [0.02 + index * 0.01 + x * 0.001 + y * 0.002
                  if 2 <= x < 13 and 3 <= y < 11 else 0.0 for y in range(12) for x in range(16)]
        pixels[5 * 16 + 5] = 0.0
        if label in ("R", "HA", "SII"):
            pixels[4 * 16 + 4] = -0.125
            pixels[6 * 16 + 6] = 1.25
        write_values(root / (label + ".fit"), pixels)


class CompositionQualityTests(unittest.TestCase):
    def test_fits_end_at_block_boundary_and_final_pixel_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'boundary.fit'
            for count in (35, 36, 71, 72):
                cards = [build_card(key, value) for key, value in
                         [('SIMPLE', 'T'), ('BITPIX', '-32'), ('NAXIS', '2'),
                          ('NAXIS1', '16'), ('NAXIS2', '12')]]
                cards.extend(['COMMENT padding'.ljust(80)] * (count - len(cards)))
                cards.append(build_card('END', ''))
                header = pad_to_block(''.join(cards).encode('ascii'))
                path.write_bytes(header + pad_to_block(struct.pack('>192f', *([0.125] * 192))))
                self.assertEqual(data_offset(str(path)), len(header))
                self.assertEqual(composition.inspect_pixels(path)[1]['min'], 0.125)
                with path.open('r+b') as handle:
                    handle.seek(len(header) + 191 * 4)
                    handle.write(struct.pack('>f', math.nan))
                with self.assertRaisesRegex(composition.FitsError, 'non-finite'):
                    composition.inspect_pixels(path)

    def test_background_statistics_failure_reports_preview_and_keeps_sources(self):
        case = fixtures.ChannelCompositionTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        before = case.snapshot()
        case.args.stretch = 'background'
        with patch.object(composition, 'sample_statistics', side_effect=composition.FitsError('invalid statistics')):
            with self.assertRaisesRegex(composition.FitsError, 'invalid statistics'):
                case.run_mocked()
        failure = json.loads(next(case.root.glob('.compose-*/failure.json')).read_text())
        self.assertEqual(failure['failed_stage'], 'preview')
        self.assertFalse(Path(case.args.out).exists())
        self.assertEqual(case.snapshot(), before)

    def test_legacy_empty_coverage_warns_and_zero_red_ratio_is_unknown(self):
        case = fixtures.ChannelCompositionTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        for label in ('R', 'G', 'B'):
            write_values(case.input / (label + '.fit'), [0.0] * 192)
        report = case.run_mocked()
        self.assertIsNone(report['coverage']['candidate_rect'])
        self.assertIn('no common coverage candidate', report['warnings'])
        self.assertEqual(report['quality']['stretch_clipping'], {})
        make_prepared(case.input, [('R', 0.1), ('G', 0.1), ('B', 0.1), ('HA', 0.1)])
        write_values(case.input / 'R.fit', [0.1 if x in (0, 15) or y in (0, 11) else 0.0
                                          for y in range(12) for x in range(16)])
        case.args.out = str(case.root / 'ha output')
        case.args.mode, case.args.ha_weight, case.args.ha_scale = 'hargb', 0.25, 1.0
        report = case.run_mocked()
        self.assertIsNone(report['ha_blend']['diagnostics']['median_ratio'])
        self.assertIsNone(report['ha_blend']['diagnostics']['median_change_pct'])

    def test_largest_rectangle_matches_brute_force_and_ties(self):
        randomizer = random.Random(27)
        for _ in range(80):
            width, height = 6, 5
            rows = [tuple(sorted((randomizer.randrange(width + 1), randomizer.randrange(width + 1))))
                    for _ in range(height)]
            expected, key = None, (0, 0, 0, 0)
            for y in range(height):
                left, right = 0, width
                for bottom in range(y, height):
                    left, right = max(left, rows[bottom][0]), min(right, rows[bottom][1])
                    w, h = right - left, bottom - y + 1
                    candidate = (w * h, -y, -left, w)
                    if w > 0 and candidate > key:
                        expected, key = dict(x=left, y=y, width=w, height=h), candidate
            self.assertEqual(largest_rectangle(rows, width), expected)
        self.assertEqual(largest_rectangle([(0, 2), (4, 6)], 6), dict(x=0, y=0, width=2, height=1))
        self.assertIsNone(largest_rectangle([(3, 1)], 6))

    def test_edge_statistics_preserve_internal_zero_negative_and_large_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "prepared"
            make_pattern(root)
            _, coverage = edge_coverage(root / "R.fit")
            rect = dict(x=2, y=1, width=11, height=8)
            self.assertEqual(coverage["candidate_rect"], rect)
            stats = sample_statistics(root / "R.fit", rect)
            expected = [values(root / "R.fit")[y * 16 + x] for y in range(3, 11) for x in range(2, 13)]
            self.assertEqual(stats["channels"]["m"]["sample_count"], len(expected))
            self.assertIn(-0.125, expected)
            self.assertIn(1.25, expected)
            self.assertIn(0.0, expected)
            self.assertAlmostEqual(stats["channels"]["m"]["median"], sorted(expected)[len(expected)//2], places=3)

    def test_background_parameters_target_zero_mad_and_invalid_statistics(self):
        stats = {"channels": {"r": dict(median=0.04, mad=0.001, sigma=0.0014826),
                               "g": dict(median=0.06, mad=0.002, sigma=0.0029652)}}
        mtf, warnings = background_mtf(stats, 0.15)
        x = (0.04 - mtf["low"]) / (1 - mtf["low"])
        actual = (mtf["mid"] - 1) * x / ((2 * mtf["mid"] - 1) * x - mtf["mid"])
        self.assertAlmostEqual(actual, 0.15, places=12)
        self.assertEqual(warnings, [])
        for median in (0.1, 0, -0.1, 1.1):
            fixed = {"channels": {"m": dict(median=median, mad=0, sigma=0)}}
            if median == 0.1:
                mtf, warnings = background_mtf(fixed, 0.15)
                self.assertEqual(mtf["low"], 0)
                self.assertTrue(warnings)
            else:
                with self.assertRaises(composition.FitsError):
                    background_mtf(fixed, 0.15)

    def test_cli_validation_and_endpoint_contributors(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stderr(io.StringIO()):
            out = Path(temporary) / "out"
            for options in (["--target-bg", "0.15"], ["--stretch", "background", "--target-bg=nan"],
                            ["--stretch", "background", "--target-bg=0"],
                            ["--stretch", "background", "--target-bg=1"]):
                with self.assertRaises(SystemExit) as caught:
                    composition.main([temporary, "--mode", "rgb", "--out", str(out)] + options)
                self.assertEqual(caught.exception.code, 2)
                self.assertFalse(list(Path(temporary).iterdir()))
        sources = dict.fromkeys(("L", "R", "G", "B", "HA"))
        for weight, excluded in ((0, "HA"), (1, "R")):
            self.assertNotIn(excluded, composition.contributing_channels(
                argparse.Namespace(mode="halrgb", ha_weight=weight), sources))

    def test_empty_coverage_failure_isolated_and_new_stage_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "prepared"
            make_prepared(inputs, [("R", 0), ("G", 0), ("B", 0)])
            args = argparse.Namespace(input=str(inputs), out=str(root / "out"), mode="rgb", siril=None,
                                      timeout=60, stretch="background", crop="none")
            with patch.object(composition, "find_siril", return_value="siril"), \
                 patch.object(composition, "siril_version", return_value="siril 1.4.4"):
                with self.assertRaisesRegex(composition.FitsError, "no common"):
                    composition.compose(args)
                self.assertFalse(Path(args.out).exists())
                failure = next(root.glob('.compose-*/failure.json'))
                self.assertEqual(json.loads(failure.read_text())["failed_stage"], "coverage")
                for label in ("R", "G", "B"):
                    write_values(inputs / (label + '.fit'), [0.1] * 192)
                args.crop = "common"
                with patch.object(composition, "run_script", side_effect=RuntimeError("crop failed")):
                    with self.assertRaisesRegex(RuntimeError, "crop failed"):
                        composition.compose(args)
                self.assertFalse(Path(args.out).exists())
                failures = [json.loads(path.read_text()) for path in root.glob('.compose-*/failure.json')]
                failed_crop = next(item for item in failures if item['failed_stage'] == 'crop')
                self.assertIsNone(failed_crop['coverage']['applied_rect'])


class RealCompositionQualityTests(unittest.TestCase):
    def setUp(self):
        if not REAL:
            self.skipTest("pass --real for native crop/background fixtures")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "prepared"
        make_pattern(self.input)
        self.args = argparse.Namespace(input=str(self.input), out='', mode='rgb', siril=None, timeout=60,
                                       stretch="background", crop="common", ha_weight=None, ha_scale=None)

    def run_mode(self, mode, name=None, weight=0.25):
        self.args.mode, self.args.out = mode, str(self.root / (name or mode))
        self.args.ha_weight = weight if mode in composition.HA_MODES else None
        self.args.ha_scale = 1.0 if mode in composition.HA_MODES else None
        return composition.compose(self.args)

    def test_six_modes_native_crops_values_and_mtf_references(self):
        before = {p: p.read_bytes() for p in self.input.rglob('*') if p.is_file()}
        rect = dict(x=2, y=1, width=11, height=8)
        cropped = {label: [values(self.input / (label+'.fit'))[y*16+x]
                           for y in range(3, 11) for x in range(2, 13)]
                   for label in ("L", "R", "G", "B", "HA", "SII", "OIII")}
        for mode in composition.MODES:
            with self.subTest(mode=mode):
                report = self.run_mode(mode)
                self.assertEqual(report["coverage"]["applied_rect"], rect)
                self.assertEqual(report["coverage"]["output_size"], [11, 8])
                master = Path(report["artifacts"]["master"])
                actual = values(master)
                h, _, _ = composition.inspect_pixels(master)
                self.assertFalse(set(composition.REMOVED_KEYS) & h.keys())
                self.assertTrue(all(math.isfinite(v) for v in actual))
                if mode not in composition.LUMINANCE_MODES:
                    expected = sum([cropped[c] for c in composition.MODES[mode]], [])
                    if mode == 'hargb':
                        expected[:88] = [0.75*r+0.25*ha for r, ha in zip(cropped['R'],cropped['HA'])]
                    for observed, wanted in zip(actual, expected):
                        self.assertAlmostEqual(observed, wanted, places=6)
                    self.assertEqual(len(actual), len(expected))
                    self.assertEqual(report['output_state'], 'linear')
                    clipping = report['quality']['stretch_clipping']['preview']
                    self.assertEqual(clipping['pixel_count'], len(expected))
                    self.assertEqual(clipping['below_low'], sum(v < report['preview']['low'] for v in expected))
                    self.assertEqual(clipping['above_high'], sum(v > 1 for v in expected))
                else:
                    self.assertEqual(report['preview'], {'method':'direct'})
                    self.assertNotIn('mtf', (master.parent/'diagnostics/preview.sir').read_text())
                    self.assertAlmostEqual(report['pre_stretch']['l']['statistics']['channels']['m']['median'],
                                           statistics.median(cropped['L']), places=7)
                if mode in composition.HA_MODES:
                    mtf = report['pre_stretch']['rgb'] if report['pre_stretch'] else report['preview']
                    mixed = [0.75*r+0.25*ha for r,ha in zip(cropped['R'],cropped['HA'])]
                    self.assertAlmostEqual(mtf['statistics']['channels']['r']['median'],
                                           statistics.median(mixed), places=7)
                self.assertFalse(list((master.parent/'diagnostics').glob('*.fit')))
                self.check_native_reference(report)
        self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))

    def check_native_reference(self, report):
        directory = self.root / (report['mode'] + '_reference')
        directory.mkdir()
        builder = composition.ScriptBuilder()
        builder.add('setext',['fit'])
        builder.add('set32bits')
        for source in report['sources']:
            label = source['channel']
            (directory/(label+'_source.fit')).symlink_to(source['path'])
            builder.add('load',[label+'_source.fit'])
            builder.add('crop',['2','1','11','8'])
            builder.add('save',[label])
        channels = list(composition.MODES[report['mode']])
        if report['ha_blend']:
            builder.add('pm',['$R$ * 0.75 + $HA$ * 0.25','-nosum'])
            builder.add('save',['R_HA'])
            channels[0] = 'R_HA'
        builder.add('rgbcomp',[c+'.fit' for c in channels]+['-out=rgb','-nosum'])
        def stretch(source, mtf, output):
            stats = mtf['statistics']['channels'].values()
            low = max(0, min(c['median']-2.8*1.4826*c['mad'] for c in stats))
            ref = min(c['median'] for c in stats)
            x = (ref-low)/(1-low)
            mid = x*0.85/(x+0.15-0.3*x)
            self.assertAlmostEqual(mtf['low'],low,places=12)
            self.assertAlmostEqual(mtf['mid'],mid,places=12)
            builder.add('load',[source])
            builder.add('mtf',[str(low),str(mid),'1'])
            builder.add('save',[output])
        if report['pre_stretch']:
            stretch('L.fit',report['pre_stretch']['l'],'l_stretched')
            stretch('rgb.fit',report['pre_stretch']['rgb'],'rgb_stretched')
            builder.add('rgbcomp',['-lum=l_stretched.fit','rgb_stretched.fit','-out=expected','-nosum'])
            composition.run_script(composition.find_siril(),directory,'reference',builder,60)
            self.assertEqual(values(directory/'expected.fit'),values(Path(report['artifacts']['master'])))
        else:
            stretch('rgb.fit',report['preview'],'display')
            composition.run_script(composition.find_siril(),directory,'reference',builder,60)
            counts = report['quality']['stretch_clipping']['preview']
            self.assertGreaterEqual(counts['below_low'],0)

    def test_weight_endpoints_extra_channels_and_legacy_compatibility(self):
        # A narrower HA field must not affect a zero-weight mixture or plain RGB.
        path = self.input/'HA.fit'
        payload = list(values(path))
        for y in range(12):
            for x in range(5):
                payload[y*16+x]=0
        payload[5*16+5] = 0.1
        write_values(path,payload)
        for base, mixed in (('rgb','hargb'),('lrgb','halrgb')):
            baseline = self.run_mode(base)
            zero = self.run_mode(mixed, mixed+'_zero',weight=0)
            self.assertEqual(baseline['coverage']['applied_rect'],zero['coverage']['applied_rect'])
            self.assertEqual(values(Path(baseline['artifacts']['master'])),values(Path(zero['artifacts']['master'])))
        full = self.run_mode('hargb','ha_only',weight=1)
        self.assertNotIn('R',full['coverage']['contributors'])
        self.assertEqual(full['coverage']['applied_rect']['x'],5)
        self.args.stretch,self.args.crop='legacy','none'
        report = self.run_mode('rgb','legacy')
        expected = [value for c in ('R', 'G', 'B') for value in values(self.input / (c + '.fit'))]
        self.assertEqual(values(Path(report['artifacts']['master'])), tuple(expected))
        self.assertNotIn('strategy',report['preview'])
        self.args.stretch = 'background'
        report = self.run_mode('rgb', 'background_without_crop')
        self.assertEqual(values(Path(report['artifacts']['master'])), tuple(expected))
        self.assertIsNone(report['coverage']['applied_rect'])
        self.assertEqual(report['preview']['statistics']['region'], dict(x=2, y=1, width=11, height=8))
        self.args.stretch, self.args.crop = 'legacy', 'common'
        report = self.run_mode('rgb', 'crop_without_background')
        self.assertEqual(values(Path(report['artifacts']['master'])),
                         values(self.root / 'rgb/composed_rgb_32bit.fits'))


if __name__ == '__main__':
    REAL = '--real' in sys.argv
    if REAL:
        sys.argv.remove('--real')
    unittest.main()
