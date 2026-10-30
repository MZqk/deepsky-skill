#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PWS 输入前提预检（fail-fast 闸门）。

为什么需要这道闸门
------------------
PWS 假设输入**已校准、已对齐**（论文 7.4 节：帧预处理不在方法范围内）。引擎本身
不做校准、不做配准，也**不做未对齐检测**。实测表明前提不成立时引擎不会报错：
misaligned 数据上引擎以 exit 0 正常结束，逐帧 FWHM 中位（1.77~2.92px）与正常数据
几乎一致，但成品星点从 2.35px 劣化到 9.81px——即静默产出废图。
因此本预检必须在引擎开跑**之前**独立判定前提是否成立。

三道闸门
--------
1. 输入完整性：帧可读、形状一致、目标/滤镜一致、星表足够。
2. FWHM 中位一致性：逐帧星点 FWHM 中位全部有限、每帧拟合成功星数足够。
3. 对齐与校准（决定性）：跨帧星点质心散布判对齐；帧内背景大尺度结构判校准。

**重要实测结论**：逐帧 FWHM 中位一致性**无法**发现未对齐。在 aligned / sub-pixel /
misaligned 三组数据上，逐帧 FWHM 极差完全相同（1.76×），而跨帧星点质心散布分别为
0.017px / 0.285px / 5.061px。故第 3 道闸门（质心散布）才是对齐的判据；FWHM 一致性
是必要的健全性检查，但不是充分条件。

输出：stdout 只输出一份 JSON（供 agent 解析），人类可读日志走 stderr。
退出码：0 = PASS，10 = WARN（可继续，但须告知用户），20 = FAIL（禁止开跑）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE / 'vendor'
for _p in (_VENDOR, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# 判据阈值（在合成数据上标定；偏保守，可用命令行覆盖）
# ---------------------------------------------------------------------------
MIN_STARS = 5                 # 引擎自身要求星表 >= 5 颗（pws._run）
MIN_OK_PER_FRAME = 5          # 每帧至少拟合成功这么多星，C_i 才可信
ALIGN_WARN_PX = 0.15          # 全局平移中位（px）
ALIGN_FAIL_PX = 0.50          # 全局平移中位（px）—— 真正的帧间错位
COADD_K = 5                   # 共加检验：取最锐 K 帧做等权平均
COADD_FAIL_PCT = 25.0         # 共加后 FWHM 扩大超过此百分比 → 判未对齐
FWHM_SPREAD_WARN = 3.0        # 逐帧 FWHM 中位极差；超出提示数据异常（方法本身可容大离散）
SKY_WARN_FRAC = 0.02          # 帧间天空水平极差 / 天空
SKY_FAIL_FRAC = 0.10
BG_WARN_FRAC = 0.03           # 帧内背景大尺度结构（渐晕/光害梯度残留）/ 天空
BG_FAIL_FRAC = 0.12
SAMPLE_DEFAULT = 24           # 默认抽样帧数（全帧读取成本高；--all-frames 可全检）
CENTROID_HALF = 8             # 质心窗口半宽（px）


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _read(path: Path):
    """读一帧；xisf_io 会向 stdout 打印解析信息，此处重定向以保持 stdout 干净。"""
    from DWT_DetailCore import read_frame
    with contextlib.redirect_stdout(io.StringIO()):
        return read_frame(path)


def _bg_spread(img: np.ndarray, sky: float, bin_px: int = 64) -> float:
    """帧内背景大尺度结构：分块中值（滤除星点）后的 P95−P5，除以天空水平。

    校准良好的帧接近 0；未除净的平场（渐晕）与光害梯度会显著抬高该值。
    引擎只做**加性逐帧常数**的天空平移（论文式 2），无法修正空间结构，故此项必须预检。
    """
    h, w = img.shape
    ny, nx = h // bin_px, w // bin_px
    if ny < 2 or nx < 2:
        return 0.0
    t = (img[:ny * bin_px, :nx * bin_px]
         .reshape(ny, bin_px, nx, bin_px)
         .transpose(0, 2, 1, 3)
         .reshape(ny, nx, -1))
    med = np.median(t, axis=2)
    lo, hi = np.percentile(med, [5, 95])
    return float((hi - lo) / max(abs(sky), 1e-30))


def _centroids(img: np.ndarray, stars, half: int = CENTROID_HALF) -> np.ndarray:
    """星表上各星的亚像素质心 (dy, dx)，相对窗口中心；越界或无效为 NaN。"""
    h, w = img.shape
    out = np.full((len(stars), 2), np.nan, dtype=np.float64)
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    for j, (xi, yi) in enumerate(stars):
        if xi - half < 0 or yi - half < 0 or xi + half >= w or yi + half >= h:
            continue
        win = img[yi - half:yi + half + 1, xi - half:xi + half + 1].astype(np.float64)
        win = np.maximum(win - float(np.median(win)), 0.0)
        s = float(win.sum())
        if s <= 0:
            continue
        out[j, 0] = float((win * yy).sum() / s)
        out[j, 1] = float((win * xx).sum() / s)
    return out


class PrecheckInputError(Exception):
    """输入不可用（无匹配帧 / 首帧不可读）。

    预检的对外契约是「stdout 纯 JSON + 退出码」，用户最可能遇到的输入错误
    （路径写错、目录没拷进去、首帧损坏）必须在该契约内表达，不得抛栈。
    """


def _crop_region(photos: Path, crop: int, center_text: str):
    """按引擎同一规则确定读取区域（与 pws.prepare_frames 保持一致）。

    抛 PrecheckInputError 而非 IndexError / 底层读帧异常：调用方据此产出 input FAIL。
    """
    import pws
    files = sorted(photos.glob('*.xisf')) or sorted(photos.glob('*.fit*'))
    if not files:
        raise PrecheckInputError(f'{photos} 下没有 XISF / FITS 帧')
    try:
        probe, _ = _read(files[0])
    except Exception as e:
        raise PrecheckInputError(
            f'首帧不可读：{files[0].name}（{type(e).__name__}: {e}）') from e
    fh, fw = probe.shape[:2]
    del probe
    if crop and crop > 0:
        center = pws.resolve_center(photos, crop, pws.parse_center(center_text))
        h = w = min(int(crop), fh, fw)
        cy, cx = (h // 2, w // 2) if center is None else (int(center[0]), int(center[1]))
        half = crop // 2
        y0 = max(0, min(cy - half, fh - crop))
        x0 = max(0, min(cx - half, fw - crop))
    else:
        h, w, y0, x0 = fh, fw, 0, 0
    return files, (h, w), (y0, x0)


def _check_frames_dir(frames_dir: str, need_bytes: int) -> dict:
    """临时帧落盘目录的容量校验（引擎要求 1.25× 余量，见 pws.pick_frames_dir）。

    skill 约束：--frames-dir 必须由用户显式指定，绝不自动选盘。此处只校验不代选。
    """
    import shutil
    need = int(need_bytes * 1.25)
    p = Path(frames_dir).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    anchor = p if p.exists() else p.parent
    if not anchor.exists():
        return {'ok': False, 'path': str(p), 'need': need, 'free': 0,
                'detail': f'落盘目录的父目录不存在：{anchor}'}
    try:
        free = int(shutil.disk_usage(str(anchor)).free)
    except OSError as e:
        return {'ok': False, 'path': str(p), 'need': need, 'free': 0,
                'detail': f'无法读取磁盘余量：{e}'}
    ok = free >= need
    return {
        'ok': ok, 'path': str(p), 'need': need, 'free': free,
        'detail': (f'{p} 可用 {free / 2 ** 30:.1f} GiB，'
                   f'需 {need / 2 ** 30:.1f} GiB（含 1.25× 余量）'
                   + ('' if ok else '  ← 空间不足')),
    }


def run_precheck(photos: str, frames_dir: str, crop: int = 0, center: str = '',
                 limit: int = 0, sample: int = SAMPLE_DEFAULT,
                 all_frames: bool = False, n_star: int = 120,
                 out: str = '', star_fwhm: float = 0.0,
                 star_thr_sigma: float = 0.0) -> dict:
    """执行三道闸门，返回结果字典（不抛异常，失败以 verdict/exit_code 表达）。

    out 给出时校验输出目录不与素材目录重合（成品会被下一轮当成帧读入）。
    """
    checks: list = []
    metrics: dict = {}
    blockers: list = []
    warnings: list = []

    def add(cid: str, status: str, detail: str, **extra) -> None:
        checks.append({'id': cid, 'status': status, 'detail': detail, **extra})

    photos_p = Path(photos).expanduser().resolve()
    if not photos_p.is_dir():
        add('input', 'FAIL', f'素材目录不存在：{photos_p}')
        return _result('FAIL', checks, metrics, [f'素材目录不存在：{photos_p}'], warnings)

    # 输出目录不得与素材目录重合：成品 stack_<tag>.xisf 会被下一轮
    # photos.glob('*.xisf') 当成输入帧，形状一致故不报错，却静默产出废图。
    if (out or '').strip():
        out_p = Path(out).expanduser().resolve()
        if out_p == photos_p:
            add('outdir', 'FAIL', f'输出目录与素材目录相同：{photos_p}')
            return _result(
                'FAIL', checks, metrics,
                [f'输出目录与素材目录相同（{photos_p}）：成品 stack_<tag>.xisf '
                 f'会被下一轮当成帧读入。请为 --out 指定独立目录'], warnings)
        add('outdir', 'PASS', f'输出目录 {out_p} 与素材目录分离')
    else:
        add('outdir', 'PASS', '未指定 --out（单独运行预检时不校验输出目录）')

    # 星表检测参数须与引擎一致，否则预检判据对引擎不具预测性
    if star_fwhm and star_thr_sigma:
        from pws_run import _apply_star_detection
        _apply_star_detection(float(star_fwhm), float(star_thr_sigma))

    try:
        import photutils  # noqa: F401
    except Exception:
        add('deps', 'FAIL', '缺少 photutils（星点检测与 PSF 拟合必需）')
        return _result('FAIL', checks, metrics,
                       ['缺少 photutils：请先安装 requirements.txt 中的依赖'], warnings)

    import pws
    from DWT_DetailCore import EXPTIME_REF

    # 引擎的默认日志出口是 stdout（pws.log 未绑定钩子时 print）。
    #   precheck 的 stdout 是**纯 JSON 协议**，故此处必须把引擎日志改道到 stderr，
    #   否则裁剪自动定位等引擎日志会混入 JSON 使其不可解析。
    pws.set_hooks(on_log=_log)
    try:
        files, (h, w), (y0, x0) = _crop_region(photos_p, crop, center)
    except PrecheckInputError as e:
        pws.set_hooks()
        add('input', 'FAIL', str(e))
        return _result('FAIL', checks, metrics, [str(e)], warnings)
    finally:
        pws.set_hooks()
    if limit:
        files = files[:limit]
    n = len(files)
    if n == 0:
        add('input', 'FAIL', f'{photos_p} 下没有 XISF / FITS 帧')
        return _result('FAIL', checks, metrics, [f'{photos_p} 下没有帧'], warnings)

    add('input', 'PASS', f'{n} 帧  读取区域 {h}×{w}（y0={y0}, x0={x0}）')
    metrics['n_frames'] = n
    metrics['region'] = [h, w]

    # 抽样：默认均匀抽样，首末必取
    if all_frames or sample >= n:
        idx = list(range(n))
    else:
        idx = sorted(set(np.linspace(0, n - 1, max(2, sample)).astype(int).tolist()))
    metrics['sampled'] = len(idx)
    _log(f'[预检] {n} 帧，抽样 {len(idx)} 帧检查')

    # ---- 读帧 + 逐帧统计（读一次，缓存裁剪后的帧，供星表/测量复用）----
    frames: list = []
    sky, bg, sig, shapes, exps = [], [], [], [], []
    objs, filts, bad = set(), set(), []
    for i in idx:
        f = files[i]
        try:
            data, meta = _read(f)
        except Exception as e:
            bad.append(f'{f.name}: {type(e).__name__}: {e}')
            continue
        shapes.append(tuple(data.shape))
        exp = float(meta.get('exptime', EXPTIME_REF) or EXPTIME_REF)
        exps.append(exp)
        if meta.get('object'):
            objs.add(str(meta['object']))
        if meta.get('filter'):
            filts.add(str(meta['filter']))
        d = np.ascontiguousarray(data[y0:y0 + h, x0:x0 + w]).astype(np.float32)
        del data
        d *= np.float32(EXPTIME_REF / max(exp, 1e-9))
        med = float(np.median(d))
        sky.append(med)
        # σ_i 与引擎同式（论文式 3）：曝光归一后测，故可跨帧直接比较
        sig.append(1.4826 * float(np.median(np.abs(d - med))))
        bg.append(_bg_spread(d, med))
        frames.append(d)

    if bad:
        add('read', 'FAIL', f'{len(bad)} 帧读取失败：' + '；'.join(bad[:3]))
        blockers.append(f'{len(bad)} 帧无法读取')
        return _result('FAIL', checks, metrics, blockers, warnings)
    add('read', 'PASS', f'{len(idx)} 帧全部可读')

    # 形状一致
    uniq = sorted(set(shapes))
    if len(uniq) > 1:
        add('shape', 'FAIL', f'帧形状不一致：{uniq}')
        blockers.append(f'帧形状不一致：{uniq}')
    else:
        add('shape', 'PASS', f'全部 {uniq[0][0]}×{uniq[0][1]}')

    # 目标 / 滤镜一致（README：同一目录内不要混入不同目标的帧）
    if len(objs) > 1 or len(filts) > 1:
        add('target', 'FAIL', f'目标 {sorted(objs)} / 滤镜 {sorted(filts)} 混装')
        blockers.append('同一目录混装了不同目标或滤镜的帧')
    else:
        add('target', 'PASS', f'目标 {sorted(objs) or "—"}  滤镜 {sorted(filts) or "—"}')

    # ---- 星表 ----
    # 复刻引擎的选帧规则：引擎取 **S_i 最大**（即 σ_i 最小）的一帧建星表
    #   （pws._run: seed = int(np.argmax(S))）。
    #   不同帧的 DAOStarFinder 检出数差异可以很大（实测同一批数据上 2 颗 vs 13 颗），
    #   故必须用同一规则，否则预检结论对引擎不具预测性。
    #
    # 抽样带来的偏差：预检默认只读一部分帧，若引擎真正选中的帧不在样本内，
    #   预检的 seed 会与之不同。因此**对全部被抽样的帧都建一次星表，取最小值**——
    #   这样预检只可能比引擎更保守（宁可误报 FAIL，不可误报 PASS 而放过废图）。
    #
    # 已知局限（见 SKILL.md 步骤 1）：上面的保守化覆盖的是「抽样内的星表数」，但引擎的
    #   seed 取全体帧的 argmin(σ)，可能落在样本外且其星表 < MIN_STARS —— 此时预检会
    #   PASS 而引擎随后拒绝。这是抽样唯一的漏判方向，用 --all-frames / --all-frames-precheck
    #   全检即可消除。对齐/校准/背景结构三项基于抽样统计量，不受此影响。
    star_counts = []
    for j, d in enumerate(frames):
        with contextlib.redirect_stdout(io.StringIO()):
            s_j = pws.build_star_table(d, n_star=n_star)
        star_counts.append(len(s_j))
        if j == int(np.argmin(sig)):
            seed, stars = idx[j], s_j
    worst = int(min(star_counts)) if star_counts else 0
    metrics['seed_frame'] = seed
    metrics['n_stars'] = len(stars)
    metrics['n_stars_min_sampled'] = worst
    metrics['n_stars_per_sampled_frame'] = [int(v) for v in star_counts]
    if len(stars) < MIN_STARS or worst < MIN_STARS:
        detail = (f'参考帧 #{seed}（σ 最小）上星表 {len(stars)} 颗；'
                  f'抽样帧中最少 {worst} 颗（需 ≥ {MIN_STARS}）')
        add('stars', 'FAIL', detail + '→ 引擎可能拒绝运行')
        blockers.append(
            f'星表不足 {MIN_STARS} 颗：{detail}。'
            f'C_i 依赖星点 FWHM；窄带极暗场或纯星云特写需改用其他帧级清晰度判据'
            f'（论文 7.4 节）')
        return _result('FAIL', checks, metrics, blockers, warnings)
    add('stars', 'PASS',
        f'星表 {len(stars)} 颗（参考帧 #{seed}，σ 最小）；'
        f'抽样各帧 {worst}~{max(star_counts)} 颗')

    # ---- 闸门 2：FWHM 中位一致性（固定星表，逐帧测量）----
    fwhm_med, n_ok, cent = [], [], []
    for d in frames:
        with contextlib.redirect_stdout(io.StringIO()):
            v = pws.measure_fwhm_each(d, stars)
        ok = v[np.isfinite(v)]
        n_ok.append(int(len(ok)))
        fwhm_med.append(float(np.median(ok)) if len(ok) else float('nan'))
        cent.append(_centroids(d, stars))
    # 注：frames 的释放推迟到闸门 3a 之后 —— 共加检验需要用到这些帧。

    fw = np.array(fwhm_med, dtype=np.float64)
    nk = np.array(n_ok, dtype=np.int64)
    metrics['fwhm_med_px'] = [round(float(v), 3) for v in fw]
    metrics['fwhm_spread'] = (round(float(np.nanmax(fw) / np.nanmin(fw)), 3)
                              if np.isfinite(fw).all() and np.nanmin(fw) > 0 else None)
    metrics['n_ok_per_frame'] = [int(v) for v in nk]

    if not np.isfinite(fw).all():
        add('fwhm', 'FAIL', f'{int((~np.isfinite(fw)).sum())} 帧的 FWHM 中位无法测得')
        blockers.append('部分帧的星点 FWHM 无法测得，C_i 不可信')
    elif int(nk.min()) < MIN_OK_PER_FRAME:
        add('fwhm', 'FAIL',
            f'最少的一帧仅拟合成功 {int(nk.min())} 颗星（需 ≥ {MIN_OK_PER_FRAME}）')
        blockers.append(f'每帧至少需 {MIN_OK_PER_FRAME} 颗成功拟合，实测最少 {int(nk.min())}')
    else:
        add('fwhm', 'PASS',
            f'逐帧 FWHM 中位 {np.nanmin(fw):.2f}~{np.nanmax(fw):.2f}px'
            f'（极差 {metrics["fwhm_spread"]}×，每帧成功 {int(nk.min())}~{int(nk.max())} 颗）')
        if metrics['fwhm_spread'] and metrics['fwhm_spread'] > FWHM_SPREAD_WARN:
            warnings.append(f'逐帧 FWHM 极差 {metrics["fwhm_spread"]}× 偏大，'
                            f'请确认星点测量未被污染')

    # ---- 闸门 3a：对齐 ----
    # 判据分两层：
    #   (1) 全局平移 —— 真正决定叠加质量的可校正量；
    #   (2) 共加检验 —— 最锐 k 帧平均后星点是否变糊，直接测相干叠加效果。
    # 逐星标准差仅作参考：它同时包含质心估计自身的抖动噪声，在暗弱星场
    # （星点 FWHM ≲ 5px、星云背景不均）可达 0.7px 而帧实际已配准，
    # 故不能单独作为 FAIL 依据（见 SKILL.md 约束 1）。
    C = np.array(cent)                       # (m, nstar, 2)，(dy, dx)
    ref_cent = C[0]
    gl = np.full(len(C), np.nan)
    for k in range(len(C)):
        d = C[k] - ref_cent
        if np.isfinite(d).any():
            gl[k] = float(np.hypot(*np.nanmedian(d, axis=0)))
    g_med = float(np.nanmedian(gl)) if np.isfinite(gl).any() else None
    g_max = float(np.nanmax(gl)) if np.isfinite(gl).any() else None

    if C.size:
        scat = np.nanstd(C, axis=0)
        per = np.hypot(scat[:, 0], scat[:, 1])
        per = per[np.isfinite(per)]
    else:
        per = np.array([])
    a_med = float(np.median(per)) if per.size else None

    # 共加检验：取最锐 k 帧做等权平均，用同一星表量 FWHM。
    # 帧已配准时星点相干叠加，FWHM 应基本保持或变锐；未配准则显著变糊。
    coadd = None
    if len(frames) >= 3:
        fw_all = np.array(fwhm_med, dtype=np.float64)
        kk = min(COADD_K, len(frames))
        sharp = np.argsort(np.nan_to_num(fw_all, nan=1e9))[:kk]
        acc = None
        for j in sharp:
            d = frames[j].astype(np.float64)
            d = d - float(np.median(d))
            acc = d if acc is None else acc + d
        if acc is not None:
            import pws as _pws
            with contextlib.redirect_stdout(io.StringIO()):
                v1 = _pws.measure_fwhm_each(frames[int(sharp[0])], stars)
                v2 = _pws.measure_fwhm_each((acc / kk).astype(np.float32), stars)
            o1 = v1[np.isfinite(v1)]
            o2 = v2[np.isfinite(v2)]
            if len(o1) and len(o2):
                f1, f2 = float(np.median(o1)), float(np.median(o2))
                coadd = {'single': round(f1, 3), 'avg': round(f2, 3),
                         'broaden': round((f2 / f1 - 1.0) * 100.0, 2) if f1 else None,
                         'n_ok': int(len(o2))}

    metrics['align_global_med_px'] = round(g_med, 4) if g_med is not None else None
    metrics['align_global_max_px'] = round(g_max, 4) if g_max is not None else None
    metrics['coadd_test'] = coadd
    metrics['centroid_scatter_med_px'] = round(a_med, 4) if a_med is not None else None
    metrics['centroid_stars'] = int(per.size) if per.size else 0

    if g_med is None:
        add('align', 'FAIL', '无法测量星点质心，无法判定对齐')
        blockers.append('星点质心不可测')
    else:
        bad_global = g_med > ALIGN_FAIL_PX
        bad_coadd = coadd is not None and coadd['broaden'] is not None \
            and coadd['broaden'] > COADD_FAIL_PCT
        co_txt = (f"；最锐 {COADD_K} 帧共加后 FWHM "
                  f"{coadd['single']:.2f}→{coadd['avg']:.2f}px"
                  f"（扩大 {coadd['broaden']:+.1f}%）") if coadd else ''
        base = f'全局平移中位 {g_med:.3f}px（阈值 {ALIGN_FAIL_PX}）{co_txt}'
        if bad_global or bad_coadd:
            why = []
            if bad_global:
                why.append(f'全局平移 {g_med:.3f}px 超阈值')
            if bad_coadd:
                why.append(f'共加检验 FWHM 扩大 {coadd["broaden"]:+.1f}%（> {COADD_FAIL_PCT}%）')
            add('align', 'FAIL', base + ' → 帧未对齐',
                global_med=g_med, coadd_broaden=(coadd or {}).get('broaden'))
            blockers.append(
                f'帧未对齐（{"；".join(why)}）。'
                f'PWS 不做配准，未对齐输入会静默产出废图。请先做星点配准（旋转+平移）')
        elif g_med > ALIGN_WARN_PX:
            add('align', 'WARN', base + ' → 存在亚像素残余', measured=g_med)
            warnings.append(f'配准存在亚像素残余（{g_med:.3f}px），成品星点会轻微增肥')
        else:
            add('align', 'PASS', base, measured=g_med)
            if a_med is not None and a_med > ALIGN_WARN_PX:
                warnings.append(
                    f'逐星质心散布 {a_med:.3f}px 偏大，但全局平移 {g_med:.3f}px 且共加检验'
                    f'正常，属质心估计噪声而非帧错位，可继续')

    del frames          # 共加检验已用毕，尽早释放大数组

    # ---- 闸门 3b：校准（帧间天空 + 帧内背景结构）----
    sk = np.array(sky, dtype=np.float64)
    sky_level = float(np.median(np.abs(sk)))
    sky_frac = float((sk.max() - sk.min()) / max(sky_level, 1e-30))
    bg_max = float(np.max(bg)) if bg else 0.0
    metrics['sky_spread_frac'] = round(sky_frac, 5)
    metrics['sky_level'] = float(sky_level)
    metrics['bg_structure_frac'] = round(bg_max, 5)

    # 帧间天空为加性常数，引擎会按论文式 2 校正，故仅作提示
    if sky_frac > SKY_FAIL_FRAC:
        warnings.append(f'帧间天空极差达天空的 {sky_frac:.1%}，'
                        f'疑似未做平场/暗场；引擎会做加性天空平移，但空间结构无法修正')
    elif sky_frac > SKY_WARN_FRAC:
        warnings.append(f'帧间天空极差 {sky_frac:.1%} 偏大，建议确认校准')

    if bg_max > BG_FAIL_FRAC:
        add('calib', 'FAIL',
            f'帧内背景大尺度结构达天空的 {bg_max:.1%}（阈值 {BG_FAIL_FRAC:.0%}）'
            f'→ 平场/光害梯度未除净',
            measured=bg_max, threshold=BG_FAIL_FRAC)
        blockers.append(
            f'帧未校准：帧内背景大尺度结构 {bg_max:.1%}。引擎只做逐帧加性天空平移，'
            f'无法修正渐晕与光害梯度，会在分区过渡处留下可见台阶。请先做平场/暗场校准')
    elif bg_max > BG_WARN_FRAC:
        add('calib', 'WARN',
            f'帧内背景大尺度结构 {bg_max:.1%}（>{BG_WARN_FRAC:.0%}），建议确认平场',
            measured=bg_max, threshold=BG_WARN_FRAC)
        warnings.append(f'帧内背景结构 {bg_max:.1%} 偏大，建议确认平场与光害梯度已除净')
    else:
        add('calib', 'PASS', f'帧内背景结构 {bg_max:.1%}，帧间天空极差 {sky_frac:.2%}')

    # ---- 临时帧落盘目录（硬约束：必须显式指定）----
    need_bytes = n * h * w * 4
    metrics['frames_bytes'] = need_bytes
    if not (frames_dir or '').strip():
        add('frames_dir', 'FAIL', '未指定 --frames-dir')
        blockers.append('必须显式指定 --frames-dir（skill 不自动选盘）')
    else:
        fd = _check_frames_dir(frames_dir, need_bytes)
        if fd['ok']:
            add('frames_dir', 'PASS', fd['detail'])
        else:
            add('frames_dir', 'FAIL', fd['detail'])
            blockers.append(fd['detail'])
        metrics['frames_dir_free'] = fd['free']
        metrics['frames_dir_need'] = fd['need']

    verdict = 'FAIL' if blockers else ('WARN' if warnings else 'PASS')
    return _result(verdict, checks, metrics, blockers, warnings)


def _result(verdict, checks, metrics, blockers, warnings) -> dict:
    return {
        'verdict': verdict,
        'exit_code': {'PASS': 0, 'WARN': 10, 'FAIL': 20}[verdict],
        'checks': checks,
        'metrics': metrics,
        'blockers': blockers,
        'warnings': warnings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='precheck',
        description='PWS 输入前提预检（fail-fast）：对齐 / 校准 / FWHM 中位一致性 / 落盘目录')
    ap.add_argument('--photos', required=True, help='素材目录')
    ap.add_argument('--frames-dir', required=True, help='临时帧落盘目录（必须显式指定）')
    ap.add_argument('--out', default='',
                    help='成品输出目录；给出时校验其不与素材目录重合')
    ap.add_argument('--crop', type=int, default=0, help='裁剪边长（与正式运行一致）')
    ap.add_argument('--center', default='', help='裁剪中心 y,x')
    ap.add_argument('--limit', type=int, default=0, help='只取前 N 帧')
    ap.add_argument('--n-star', type=int, default=120, help='星表规模')
    ap.add_argument('--star-fwhm', type=float, default=6.0,
                    help='星表检测 fwhm（须与 pws_run.py 相同）')
    ap.add_argument('--star-thr-sigma', type=float, default=5.0,
                    help='星表检测阈值 σ 倍数（须与 pws_run.py 相同）')
    ap.add_argument('--sample', type=int, default=SAMPLE_DEFAULT, help='抽样帧数')
    ap.add_argument('--all-frames', action='store_true', help='检查全部帧')
    ap.add_argument('--pretty', action='store_true', help='缩进输出（人类阅读）')
    a = ap.parse_args(argv)

    res = run_precheck(a.photos, a.frames_dir, crop=a.crop, center=a.center,
                       limit=a.limit, sample=a.sample, all_frames=a.all_frames,
                       n_star=a.n_star, out=a.out,
                       star_fwhm=a.star_fwhm, star_thr_sigma=a.star_thr_sigma)
    print(json.dumps(res, ensure_ascii=False,
                     indent=2 if a.pretty else None))
    return int(res['exit_code'])


if __name__ == '__main__':
    raise SystemExit(main())
