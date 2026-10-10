#!/usr/bin/env python3
"""Runnable P0/P1 regression: python scripts/test_video_optimizations.py."""
import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
from astropy.io import fits

import moon_stack as moon


def _video(path, codec, count=12):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), 15, (512, 384))
    assert writer.isOpened()
    yy, xx = np.mgrid[:384, :512]
    rng = np.random.default_rng(14)
    for index in range(count):
        phase = index % 12
        cx = 330 + phase
        disc = (xx-cx)**2 + (yy-210)**2 < 76**2
        dark = (xx-(cx-43))**2 + (yy-206)**2 < 71**2
        crescent = disc & ~dark
        green = 16 + rng.normal(0, 8, (384, 512))
        terrain = 150 + 24*np.sin(xx/3)*np.cos(yy/4)
        green[crescent] = terrain[crescent]*(0.8+0.02*phase)
        if phase >= 8:
            green = cv2.GaussianBlur(green.astype(np.float32), (9, 9), 2)
        image = np.stack([green*0.7, green, green*1.5], axis=-1)
        image = np.clip(image, 0, 255).astype(np.uint8)
        cv2.circle(image, (40, 50), 3, (250, 250, 250), -1)
        writer.write(image)
    writer.release()


def test_p0_p1():
    rng = np.random.default_rng(42)
    texture = cv2.GaussianBlur(rng.uniform(0.2, 0.6, (128, 128)).astype(np.float32), (3, 3), 0.5)
    quality = moon._structure_quality(texture, 0, 1e-4, 1)
    darker = moon._structure_quality(texture*0.5, 0, 5e-5, 1)
    blurred = moon._structure_quality(cv2.GaussianBlur(texture, (9, 9), 2), 0, 1e-4, 1)
    assert abs(quality/darker-1) < 0.01
    assert quality > blurred*2
    assert moon._structure_quality(np.full((64, 64), 0.04, np.float32), 0, 0.02, 1) == 0
    assert moon._structure_quality(np.ones((64, 64), np.float32), 0, 1e-4, 1) == 0
    assert moon._pixel_full_scale(np.full((64,64),1.1,np.float32)) == 1
    assert moon._pixel_full_scale(np.full((64,64),255,np.uint8)) == 255
    assert moon._pixel_full_scale(np.full((64,64),65535,np.uint16)) == 65535
    assert moon._feedback_accept({'relative_noise':1, 'detail':1}, {'relative_noise':0.9, 'detail':0.99})
    assert not moon._feedback_accept({'relative_noise':1, 'detail':1}, {'relative_noise':0.9, 'detail':0.90})

    noise = rng.normal(0, 0.001, (3, 256, 256)).astype(np.float32)
    noise += np.array([0.01, 0.02, 0.03])[:, None, None]
    noise[:, 70:180, 70:180] = 0.7
    bg = moon._background_metrics(noise)
    assert bg['status'] == 'measured' and max(bg['std_channels']) < 0.002
    assert noise[:, :64, :64].std() > bg['std_green']*5
    assert moon._background_metrics(np.ones((128, 128), np.float32))['std_green'] is None
    fixed_sky = np.ones((256,256),bool);fixed_sky[62:188,62:188] = False
    assert moon._background_metrics(noise,fixed_sky)['pixels'] == np.count_nonzero(fixed_sky)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        args = moon.build_parser().parse_args(['import', '--input', str(root/'x.mp4'), '--work', str(root)])
        free = 24*1024**3
        with patch.object(moon.shutil, 'disk_usage', return_value=Mock(free=free)):
            count, budget = moon._disk_frame_budget(root, args, 2160, 3840, 3, 1000, scale=2)
            assert 3 <= count < 1000 and budget['reduced']
            assert budget['estimated_peak_additional_bytes'] <= budget['available_bytes']
            with patch.object(moon.shutil, 'disk_usage', return_value=Mock(free=5*1024**3)), contextlib.redirect_stderr(io.StringIO()):
                try:
                    moon._disk_frame_budget(root, args, 2160, 3840, 3, 100)
                except SystemExit:
                    pass
                else:
                    raise AssertionError('A missing safety reserve must fail before FITS writes')

        for suffix, codec in (('.avi', 'MJPG'), ('.mp4', 'mp4v')):
            video = root/f'moon{suffix}'
            _video(video, codec)
            original_hash = moon._source_sha256(video)
            work = root/f'work{suffix}'
            args = moon.build_parser().parse_args(['import', '--input', str(video), '--work', str(work),
                '--keep-percent', '100', '--limit', '4', '--roi-margin', '12', '--video-debayer', 'none'])
            captures = []
            original_capture = cv2.VideoCapture
            def capture(filename):
                result = Mock(wraps=original_capture(filename))
                captures.append(result)
                return result
            with patch.object(cv2, 'VideoCapture', side_effect=capture):
                moon.cmd_import(args)
            assert all(cap.set.call_count == 0 for cap in captures)
            first = moon.load_json(work/'import_receipt.json')['video_metadata']
            assert first['seeing_probe']['total_probed'] == 12 and first['newly_extracted'] == 4
            assert first['crop_box'] is not None, first['crop_reason']
            x0, y0, x1, y1 = first['crop_box']
            assert x0 > 40 and x1-x0 < 512 and y1-y0 < 384
            profile = moon.load_json(work/'video_scores.json')
            for i,a in enumerate(profile['anchors']):
                for b in profile['anchors'][i+1:]:
                    assert a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3]
            rows = moon.load_json(work/'video_scores.json')['rows']
            assert all(b['pts'] > a['pts'] for a,b in zip(rows,rows[1:]))
            assert all(row['box'][0] >= x0 and row['box'][1] >= y0 and row['box'][2] <= x1 and row['box'][3] <= y1 for row in rows)
            original_files = {p.name:moon._source_sha256(p) for p in work.glob('moon_*.fit')}
            args.limit = 8
            with patch.object(moon, '_scan_video_profile', side_effect=AssertionError('Warm scores must not decode pass 1')):
                moon.cmd_import(args)
            second = moon.load_json(work/'import_receipt.json')['video_metadata']
            assert second['newly_extracted'] == 4 and second['score_cache_hit']
            assert second['crop_box'] == first['crop_box']
            assert all(moon._source_sha256(work/name) == digest for name,digest in original_files.items())
            mapping = second['source_mapping']
            source = original_capture(str(video))
            inverse = {index:int(number) for number,index in mapping.items()}
            for index in range(12):
                ok, frame = source.read()
                assert ok
                if index in inverse:
                    expected = frame[y0:y1,x0:x1,::-1].transpose(2,0,1).astype(np.uint16)*257
                    actual = fits.getdata(work/f'moon_{inverse[index]:05d}.fit')
                    assert np.array_equal(actual, expected)
            source.release()
            assert moon._source_sha256(video) == original_hash
            register = moon.build_parser().parse_args(['register', '--work', str(work), '--min-confidence', '0'])
            moon.cmd_register(register)
            ranked = moon.load_json(work/'ranking.json')
            assert ranked['selection_meta']['mode'] == 'import_selection' and ranked['kept_frames'] == 8
            old_reference = ranked['reference_index']
            args.limit = 12
            moon.cmd_import(args)
            moon.cmd_register(register)
            ranked = moon.load_json(work/'ranking.json')
            assert ranked['reused_frames'] == 8 and ranked['reference_index'] == old_reference
            assert ranked['kept_frames'] == 12
            changed = dict(args.__dict__);changed['force_mono'] = True
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    moon.cmd_import(type(args)(**changed))
                except SystemExit:
                    pass
                else:
                    raise AssertionError('Incompatible channel reuse must require a new work directory')
            cached = moon.load_json(work/'video_scores.json')
            cached['identity']['version'] = -1
            moon.dump_json(work/'video_scores.json', cached)
            with patch.object(moon, '_scan_video_profile', wraps=moon._scan_video_profile) as scan:
                moon._cached_video_profile(video, work, 1)
                assert scan.call_count == 1
            with patch.object(moon, '_scan_video_profile', wraps=moon._scan_video_profile) as scan:
                moon._cached_video_profile(video, work, 1, min_signal_ratio=0.1)
                assert scan.call_count == 1
            with patch.object(moon, '_source_sha256', return_value='changed-source'), patch.object(moon, '_scan_video_profile', wraps=moon._scan_video_profile) as scan:
                moon._cached_video_profile(video, work, 1, min_signal_ratio=0.1)
                assert scan.call_count == 1
            args.sample_mode = 'head'
            with patch.object(moon, '_disk_frame_budget', return_value=(4,{'reduced':True})):
                moon.cmd_import(args)
            profile = moon.load_json(work/'video_scores.json')
            expected = [r['index'] for r in sorted(profile['rows'],key=lambda r:r['sharpness'],reverse=True)[:4]]
            assert moon.load_json(work/'import_receipt.json')['video_metadata']['active_sources'] == expected
            frame_path = work/'moon_00001.fit'
            frame_path.touch()
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    moon.cmd_import(args)
                except SystemExit:
                    pass
                else:
                    raise AssertionError('Modified FITS must fail reuse without overwriting data')

        matrices = ['H 1 0 0 0 1 0 0 0 1', 'H 1 0 -2 0 1 -1 0 0 1', 'H 1 0 -2 0 1 -1 0 0 1', 'H 1 0 1.3 0 1 0.45 0 0 1']
        compatible, offset = moon._siril_homographies(matrices)
        assert offset == 1
        original = [np.array(list(map(float,h.split()[1:]))).reshape(3,3) for h in matrices]
        shifted = [np.array(list(map(float,h.split()[1:]))).reshape(3,3) for h in compatible]
        for a,b in zip(original,shifted):
            assert np.allclose(np.linalg.inv(original[0])@a, np.linalg.inv(shifted[0])@b, atol=1e-12)
        if Path(moon.DEFAULT_SIRIL).exists():
            case = root/'homography';case.mkdir()
            for index in range(1,5):
                fits.writeto(case/f'test_{index:05d}.fit', rng.uniform(0.01,0.8,(64,64)).astype(np.float32))
            seq = ["S 'test_' 1 4 4 5 0 6 0 0 0",'L 1']+[f'I {i} 1' for i in range(1,5)]+['R0 1 1 1 0 0 1 '+h for h in compatible]
            (case/'test_.seq').write_text('\n'.join(seq)+'\n')
            result = moon.run_siril_script(moon.DEFAULT_SIRIL, ['seqapplyreg test_ -framing=min -interp=cu -filter-incl','exit'], case, case/'registration.log', 60)
            assert result['exit_code'] == 0 and len(list(case.glob('r_test_*.fit'))) == 4
            assert 'Some images were not registered' not in result['output']

            mono = root/'mono';mono.mkdir()
            yy,xx = np.mgrid[:256,:256]
            master = rng.uniform(0.005,0.008,(256,256)).astype(np.float32)
            master[(xx-185)**2+(yy-126)**2<68**2] = 0.99
            fits.writeto(mono/'moon_master.fit', master)
            post = moon.build_parser().parse_args(['postprocess', '--work', str(mono), '--no-adc'])
            moon.cmd_postprocess(post)
            moon.cmd_verify(moon.build_parser().parse_args(['verify','--work',str(mono)]))
            receipt = moon.load_json(mono/'postprocess_receipt.json')
            assert receipt['deconvolution']['applied'] == 'none'
            assert (mono/'moon_sharp.fit').exists()
            for line in (mono/'logs/03_postprocess.ssf').read_text().splitlines():
                if line.startswith('mtf '):
                    low, middle, high = map(float,line.split()[1:4])
                    assert 0 <= low < high <= 1 and 0 < middle < 1
            verify = moon.load_json(mono/'verify_report.json')
            assert all(verify.get(key) is not None for key in ('dark_halo_ratio','chalky_saturation_index','gradient_kurtosis'))
            full = cv2.imread(str(mono/'moon_natural.jpg'),0)
            square = cv2.imread(str(mono/'moon_natural_square.jpg'),0)
            assert square.shape[0] == square.shape[1]
            assert np.count_nonzero(square>50) >= np.count_nonzero(full>50)*0.98
            feedback_video = root/'feedback.mp4'
            _video(feedback_video,'mp4v',count=300)
            feedback_work = root/'feedback'
            feedback_args = moon.build_parser().parse_args(['all','--input',str(feedback_video),'--work',str(feedback_work),
                '--candidate-mode','feedback','--limit','300','--keep-percent','100','--roi-margin','64','--drizzle','off','--force-mono'])
            moon.cmd_all(feedback_args)
            growth = moon.load_json(feedback_work/'candidate_feedback.json')
            assert len(growth['rounds']) == 2, growth
            assert all(r['metrics']['pixels'] == growth['sky_pixels'] for r in growth['rounds'])
            assert growth['rounds'][1]['candidates'] == 300
            final = moon.load_json(feedback_work/'verify_report.json')
            assert final['stacked_frames'] == growth['rounds'][growth['winner']-1]['actually_stacked']
            assert moon.load_json(feedback_work/'registration_cache.json')['reused_frames'] == 256
            assert len(moon.load_json(feedback_work/'import_receipt.json')['video_metadata']['source_mapping']) == 300
            unknown = moon._infer_optical_parameters(fits.Header(),master,post)
            assert not unknown['trusted'] and unknown['airy_radius_px'] is None
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    moon._stack_counts(mono,'moon_','ok',4)
                except SystemExit:
                    pass
                else:
                    raise AssertionError('Unconfirmed native counts must not claim success')
    print('P0/P1 scoring, ROI, budget, cache, registration, native transforms and mono checks passed')


if __name__ == '__main__':
    test_p0_p1()
