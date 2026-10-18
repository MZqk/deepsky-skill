#!/usr/bin/env python3
"""标定母版可测性筛查（离线工具，非管线组件）。

**这个工具解决什么**：判断一份母版能不能当 ``damage_limit`` 的标定样本，
**不需要先跑管线**。跑一次管线约 8 分钟；本工具全库扫一遍约 20 秒。

**依据（实测，4 份样本逐位验证）**：``04_separation_reference.tif``
（star_remove 的输入）上的 ``subject_separation`` 与**原始母版**逐位相同 ——
NGC6888 1.308/1.308、M8 1.570/1.570、M17 1.017/1.017、NGC7000 1.019/1.019。

机制：``separation = p75 / p25`` of ``gaussian_filter(gray, sigma=8)``，
是**分位之比**；DBE 减掉的是大尺度背景梯度，对「平滑后 25 分位与 75 分位
的比值」几乎无影响（两者同乘一个近似常数）。

⚠ **这是经验观察，不是保证。** 它只在 4 份 ``emission_nebula`` 样本上
验证过。DBE 一改、判据一改，它就可能失效 ⇒ :func:`self_check` 每次运行
都会用已有标定样本**重新验证一遍**，偏差超阈值即报警。

用法::

  # 全库筛查
  python scripts/screen_calibration_corpus.py --glob '~/SeeStar/Starun/*.xisf'

  # 连同等价性自检（默认开）
  python scripts/screen_calibration_corpus.py --calib-root /tmp/calib2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import calibrate_star_damage_gate as cal
from fits_io import LOCAL_CELESTIAL_DB, read_image
from star_removal_profiles import DEFAULTS
from target_rules import STAR_DOMINANT_TYPES, resolve_target_type


#: raw vs post 的 separation 允许偏差。这是**分位统计**的噪声量级，
#: 不是严格零 —— 换一台机器、换 scipy 版本都可能差在最后一位。
RAW_POST_TOLERANCE = 0.01

#: 子窗可测率的分层边界（对齐 plan §0.3 的口径）
MEASURABLE_RATIO_STRONG = 0.8   # >= 该值：优先选用
MEASURABLE_RATIO_OK = 0.5       # >= 该值：可用


def separation_of(image):
    """``subject_separation``：平滑后 p75/p25。

    与 ``star_tools.estimate_star_removal_quality`` 同一口径 ——
    口径不一致会让筛选结果与实际门禁行为脱节。
    """
    gray = cal.to_gray(cal.downsample(image))
    smooth = gaussian_filter(gray, sigma=8.0)
    p25 = float(np.percentile(smooth, 25))
    p75 = float(np.percentile(smooth, 75))
    return p75 / max(p25, 1e-8) if p25 > 1e-8 else 0.0


def window_separations(image, n_windows=cal.DEFAULT_JITTER_WINDOWS,
                       window_frac=0.6):
    """按 V4 的 2D 网格取子窗，逐个算 separation。

    必须复用 :func:`cal.window_positions` —— V4 用什么布置子窗，
    筛选就得用什么布置，否则「可测率」与实际 V4 读数不对应。
    """
    arr = cal.downsample(image)
    h, w = arr.shape[:2]
    win_h, win_w = int(h * window_frac), int(w * window_frac)
    values = []
    for y0, x0 in cal.window_positions(h, w, win_h, win_w, n_windows):
        values.append(separation_of(arr[y0:y0 + win_h, x0:x0 + win_w]))
    return values


def catalog_status(target_name):
    """目标名在本地星表里的命中情况。

    **为什么必须报这个**：不在星表的目标会走
    ``resolve_target_type`` 的兜底猜测。实测 C50（疏散星团）被猜成
    ``emission_nebula``，于是 ``is_star_poi_target`` 返回 ``False``，
    管线的星团禁令**不会触发** —— 于是在「星点即主体」的数据上
    跑完整去星链，产出的 ``damage`` 语义不明。
    """
    entry = LOCAL_CELESTIAL_DB.get(target_name)
    resolved = resolve_target_type(None, target_name)
    return {
        'catalog_hit': entry is not None,
        'catalog_entry': list(entry) if entry else None,
        'resolved_type': resolved,
        # 兜底猜测出的类型不可信：明确标出来，别让它静默进标定集
        'type_is_guessed': entry is None,
        'star_dominant': resolved in STAR_DOMINANT_TYPES,
    }


def screen_file(path, n_windows=cal.DEFAULT_JITTER_WINDOWS):
    """筛查单份母版。"""
    path = Path(path)
    # 目标名取文件名里的目标段：SeestarS30Pro-NGC2237-60s174_IRCUT -> NGC2237
    stem = path.stem
    parts = stem.split('-')
    target = next((p for p in parts[1:]
                   if not p[0].isdigit()), None) or parts[0]
    record = {'file': path.name, 'target': target}
    try:
        image, _ = read_image(str(path))
    except Exception as exc:                      # noqa: BLE001
        # 单份读失败不该中断全库筛查 —— 记下原因继续
        record.update({'read_failed': True, 'error': str(exc)[:200]})
        return record
    h, w = image.shape[:2]
    sep = separation_of(image)
    windows = window_separations(image, n_windows)
    threshold = DEFAULTS.min_subject_separation
    ratio = float(np.mean([v >= threshold for v in windows]))
    record.update({
        'read_failed': False,
        'shape': [h, w],
        'megapixels': round(h * w / 1e6, 2),
        'separation': round(sep, 4),
        'measurable_threshold': threshold,
        'window_separations': [round(v, 4) for v in windows],
        'window_separation_min': round(min(windows), 4) if windows else None,
        'window_separation_max': round(max(windows), 4) if windows else None,
        'measurable_ratio': round(ratio, 4),
        'verdict': ('strong' if ratio >= MEASURABLE_RATIO_STRONG else
                    'usable' if ratio >= MEASURABLE_RATIO_OK else 'weak'),
        'catalog': catalog_status(target),
    })
    return record


def self_check(calib_root, tolerance=RAW_POST_TOLERANCE):
    """验证「raw sep == post sep」这个经验等价性**仍然成立**。

    对每个已有标定样本，同时算原始母版与 ``04_separation_reference.tif``
    的 separation。偏差超容差即报 ``broken``。

    这条自检是本工具的生命线：等价性一旦失效，筛查结果就是**系统性
    错误**而不是随机误差 —— 且失效时不会有任何报错，只会把不可测的
    母版报成可测。
    """
    root = Path(calib_root)
    if not root.is_dir():
        return {'applicable': False,
                'reason': '标定目录不存在：%s' % root}
    rows, broken = [], []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        reference = entry / 'work' / '04_separation_reference.tif'
        if not reference.exists():
            continue
        starless = entry / 'work' / '04_starless_linear.tif'
        if not starless.exists():
            # 回退副本不能当健康样本（v0.1.34 §3.1）
            continue
        result = json.loads((entry / 'result.json').read_text(
            encoding='utf-8')) if (entry / 'result.json').exists() else {}
        star_removal = result.get('star_removal') or {}
        target = star_removal.get('star_removal_profile', {}).get(
            'target_type') or 'unknown'
        # 找原始母版：按 result.json 的 input 字段
        source = result.get('input')
        if not source or not Path(source).exists():
            rows.append({'target': entry.name, 'skipped': '母版路径不可用'})
            continue
        post_image, _ = read_image(str(reference))
        raw_image, _ = read_image(str(source))
        post_sep = separation_of(post_image)
        raw_sep = separation_of(raw_image)
        delta = abs(post_sep - raw_sep)
        ok = delta <= tolerance
        if not ok:
            broken.append(entry.name)
        rows.append({
            'target': entry.name,
            'target_type': target,
            'raw_separation': round(raw_sep, 4),
            'post_separation': round(post_sep, 4),
            'delta': round(delta, 6),
            'equivalent': ok,
        })
    return {
        'applicable': bool(rows),
        'tolerance': tolerance,
        'checked': len([r for r in rows if 'delta' in r]),
        'rows': rows,
        'broken_samples': broken,
        'verdict': ('ok' if rows and not broken else
                    'broken' if broken else 'no_samples'),
        'note': ('等价性成立 ⇒ 可用 raw 母版预测可测性'
                 if rows and not broken else
                 '等价性失效 ⇒ raw 预测不可信，筛查结果仅供排除，'
                 '入选必须跑管线确认' if broken else
                 '无可校验的样本'),
    }


def main():
    parser = argparse.ArgumentParser(
        description='标定母版可测性筛查（不跑管线，秒级）')
    parser.add_argument('--glob', default='~/SeeStar/Starun/*.xisf',
                        help='母版 glob')
    parser.add_argument('--calib-root', default='/tmp/calib2',
                        help='已有标定产物目录（用于等价性自检）')
    parser.add_argument('--no-self-check', action='store_true',
                        help='跳过 raw/post 等价性自检（不推荐）')
    parser.add_argument('--jitter-windows', type=int,
                        default=cal.DEFAULT_JITTER_WINDOWS)
    parser.add_argument('--min-measurable-ratio', type=float, default=None,
                        help='只看可测率不低于该值的母版')
    parser.add_argument('--json', dest='json_output', default=None)
    args = parser.parse_args()

    paths = sorted(Path(os.path.expanduser(p))
                   for p in __import__('glob').glob(os.path.expanduser(
                       args.glob)))
    records = [screen_file(p, args.jitter_windows) for p in paths]
    ok = [r for r in records if not r.get('read_failed')]
    ok.sort(key=lambda r: (-r['measurable_ratio'], -r['separation']))

    report = {
        'schema': 'deep-sky-processor.corpus-screen/v1',
        'measurable_threshold': DEFAULTS.min_subject_separation,
        'ratio_bands': {'strong': MEASURABLE_RATIO_STRONG,
                        'usable': MEASURABLE_RATIO_OK},
        'jitter_windows': args.jitter_windows,
        'scanned': len(records),
        'readable': len(ok),
        'self_check': (None if args.no_self_check
                       else self_check(args.calib_root)),
        'records': records,
        'summary': {
            'strong': [r['target'] for r in ok
                       if r['verdict'] == 'strong'],
            'usable': [r['target'] for r in ok
                       if r['verdict'] == 'usable'],
            'read_failed': [r['target'] for r in records
                            if r.get('read_failed')],
            # 类型靠兜底猜测的，即使可测率达标也要标出来
            'type_guessed': [r['target'] for r in ok
                             if r['catalog']['type_is_guessed']],
        },
    }
    if args.min_measurable_ratio is not None:
        report['summary']['above_min_ratio'] = [
            r['target'] for r in ok
            if r['measurable_ratio'] >= args.min_measurable_ratio]

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_output:
        Path(args.json_output).write_text(payload, encoding='utf-8')

    print('scanned=%d readable=%d  threshold=%.2f'
          % (report['scanned'], report['readable'],
             report['measurable_threshold']))
    sc = report['self_check']
    if sc:
        print('self_check: %s (checked=%d, tolerance=%g)'
              % (sc['verdict'], sc['checked'], sc['tolerance']))
        for row in sc.get('rows', []):
            if 'delta' in row:
                print('   %-12s raw=%.4f post=%.4f Δ=%.6f %s'
                      % (row['target'], row['raw_separation'],
                         row['post_separation'], row['delta'],
                         'OK' if row['equivalent'] else 'MISMATCH'))
    print()
    print('%-34s %-9s %-8s %-16s %s' % ('target', 'sep', '可测率', '窗口范围', '备注'))
    print('-' * 88)
    for r in ok:
        notes = []
        if r['catalog']['type_is_guessed']:
            notes.append('类型兜底猜测(%s)' % r['catalog']['resolved_type'])
        if r['catalog']['star_dominant']:
            notes.append('星点即主体')
        print('%-34s %-9.4f %-8.2f [%.3f,%.3f] %s'
              % (r['target'][:34], r['separation'], r['measurable_ratio'],
                 r['window_separation_min'], r['window_separation_max'],
                 ' '.join(notes)))
    for r in records:
        if r.get('read_failed'):
            print('%-34s READ FAILED: %s' % (r['target'][:34], r['error']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
