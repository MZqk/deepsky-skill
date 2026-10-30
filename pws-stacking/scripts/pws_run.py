#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PWS 无界面（headless）运行器 —— skill 的唯一执行入口。

为什么不能直接调 `python main.py --cli`
---------------------------------------
仓库根入口 main.py 在**没有 --cli 时**会 `from app import main`，而 app 会导入
PySide6 并创建 QApplication。虽然 `main.py --cli` 这一支不会走到 Qt，但整条链路
把 GUI 与引擎耦合在同一个入口文件里：一旦调用方误传参数（或缺 --cli），就会拉起
图形界面。skill 要求"使用技能时无额外调用 GUI"，故此处**只导入引擎本体**，
并在启动前显式断言 PySide6 / Qt 未被加载。

本运行器做的事
--------------
1. 强制 fail-fast：先跑 precheck（对齐 / 校准 / FWHM 中位一致性 / 落盘目录），
   未通过则**绝不调用引擎**。
2. 强制显式 --frames-dir：不传即报错退出，**绝不**回落到 pws.pick_frames_dir 的
   自动选盘（166 帧全幅约 40 GiB，自动选盘可能写满用户不知情的磁盘）。
3. 锁定 B=2：B 由逆方差最优性给出，非实验用途不允许改（--allow-B-override 才放行）。
4. 只导入 vendor/ 下的引擎源码，全程无 Qt。
5. stdout 只输出一份 JSON 结果；引擎日志走 stderr（供 agent 观察进度）。

用法
----
    python pws_run.py --photos <帧目录> --out <输出目录> --frames-dir <落盘目录> \
        [--tag stack] [--A 20] [--crop 0] [--limit 0] [--keep-frames] \
        [--skip-precheck] [--json-out <路径>]

Exit codes: 0 = success; 10 = precheck WARN; 20 = precheck/validation failure; 1 = engine/archive error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE / 'vendor'
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

# 引擎被禁止修改的参数（方法层硬约束）
LOCKED_B = 2.0


def _log(msg: str = '') -> None:
    print(msg, file=sys.stderr, flush=True)


def _assert_no_qt() -> None:
    """确认 Qt / 界面模块没有被导入（skill 的硬性要求：无额外调用 GUI）。"""
    loaded = [m for m in sys.modules
              if m == 'app' or m.startswith('PySide') or m.startswith('PyQt')
              or m.startswith('shiboken')]
    if loaded:
        raise SystemExit(f'检测到界面模块已被导入，拒绝继续：{loaded}')


# 引擎默认的星表检测参数（vendor/pws.py 内硬编码）
DEF_STAR_FWHM = 6.0
DEF_STAR_THR_SIGMA = 5.0


def _apply_star_detection(star_fwhm: float, thr_sigma: float) -> None:
    """把星表检测的 fwhm0 / threshold 覆盖到引擎的 build_star_table 上。

    上游把 ``fwhm0 = 6.0`` 与 ``threshold = 5.0 * nz`` 写死在 build_star_table 里，
    且没有暴露为参数。短曝光暗弱数据（Seestar 20s IRCUT 实测 FWHM≈4.7px、星点
    SNR 偏低）在这组硬编码值下会漏检弱星 —— 12 组 DAOStarFinder 参数扫描实测
    在 5σ 下仅检出噪声峰。故此处**在 skill 层包一层**，不改 vendor 源码。

    数值路径与上游逐行一致，仅 fwhm 与 threshold 两个常数不同。
    """
    if abs(float(star_fwhm) - DEF_STAR_FWHM) < 1e-9 and \
            abs(float(thr_sigma) - DEF_STAR_THR_SIGMA) < 1e-9:
        return                                   # 用默认值，不必打补丁

    import types
    import numpy as np
    from scipy.spatial import cKDTree

    def build_star_table(ref, n_star: int = 120, iso_mult: float = 5.0):
        """与 vendor/pws.py 同逻辑，仅 fwhm0 与 threshold 可配。"""
        import photutils
        if hasattr(photutils, 'future_column_names'):
            photutils.future_column_names = True
        from photutils.detection import DAOStarFinder

        sky = float(np.median(ref))
        nz = 1.4826 * float(np.median(np.abs(ref - sky)))
        tbl = DAOStarFinder(fwhm=float(star_fwhm), threshold=float(thr_sigma) * nz,
                            exclude_border=True)(ref - sky)
        if tbl is None or len(tbl) == 0:
            return []
        names = tbl.colnames
        xk = 'x_centroid' if 'x_centroid' in names else 'xcentroid'
        yk = 'y_centroid' if 'y_centroid' in names else 'ycentroid'
        x = np.asarray(tbl[xk], float)
        y = np.asarray(tbl[yk], float)
        pk = np.asarray(tbl['peak'], float)

        vmax = float(np.max(ref))
        keep = pk < 0.8 * vmax
        x, y, pk = x[keep], y[keep], pk[keep]
        order = np.argsort(pk)[::-1]
        x, y, pk = x[order], y[order], pk[order]

        tree = cKDTree(np.column_stack([x, y]))
        sel: list = []
        h, w = ref.shape
        margin = 26
        for i in range(len(x)):
            xi, yi = int(round(x[i])), int(round(y[i]))
            if xi < margin or yi < margin or xi >= w - margin or yi >= h - margin:
                continue
            if sel:
                nb = tree.query((x[i], y[i]), k=2)[0][1]
                if nb < iso_mult * 5.0:
                    continue
            sel.append((xi, yi))
            if len(sel) >= n_star:
                break
        return sel

    import pws
    pws.build_star_table = build_star_table
    _log(f'[星表] 覆盖检测参数 fwhm={star_fwhm:g} threshold={thr_sigma:g}σ'
         f'（引擎默认 {DEF_STAR_FWHM:g} / {DEF_STAR_THR_SIGMA:g}σ；vendor 源码未改）')


def build_params(a) -> 'object':
    """把命令行参数装配成引擎入参对象（字段名 == pws_params.PARAMS 的 key）。

    星表检测参数（star_fwhm / star_thr_sigma）不在 PwsParams 里 —— 上游把
    fwhm0=6.0 与 threshold=5σ 硬编码在 build_star_table 内。短曝光暗弱数据
    （如 Seestar 20s IRCUT）实际 FWHM 常低于 6px，且星点 SNR 偏低，硬编码值会
    漏检。故在本层打补丁覆盖，vendor/ 保持逐位不变（extract.py --check 仍通过）。
    """
    from pws_params import PwsParams
    _apply_star_detection(float(a.star_fwhm), float(a.star_thr_sigma))
    return PwsParams(
        photos=str(Path(a.photos).expanduser().resolve()),
        out=str(Path(a.out).expanduser().resolve()),
        tag=(a.tag or 'stack'),
        A=float(a.A),
        B=float(a.B),
        rej_k=float(a.rej_k),
        limit=int(a.limit),
        frames_dir=str(Path(a.frames_dir).expanduser().resolve()),
        keep_frames=bool(a.keep_frames),
        crop=int(a.crop),
        center=(a.center or ''),
        env_win_mult=float(a.env_win_mult),
        env_eps=float(a.env_eps),
        env_p=float(a.env_p),
        r_floor=float(a.r_floor),
        n_star=int(a.n_star),
        rej_gate=float(a.rej_gate),
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='pws_run',
        description='PWS 分区加权叠加（无界面运行器）')
    ap.add_argument('--photos', required=True, help='已校准、已对齐的单通道 XISF/FITS 帧目录')
    ap.add_argument('--out', required=True, help='成品输出目录')
    ap.add_argument('--frames-dir', required=True,
                    help='临时帧落盘目录（必须显式指定；skill 不自动选盘）')
    ap.add_argument('--tag', default='stack', help='成品名后缀（默认 stack）')

    # 方法参数：默认值即论文工作点
    ap.add_argument('--A', type=float, default=20.0,
                    help='深化强度 A（唯一需标定者，默认 20）')
    ap.add_argument('--B', type=float, default=LOCKED_B,
                    help='信噪比幂 B（默认 2，由逆方差最优性给出，不可改）')
    ap.add_argument('--rej-k', type=float, default=3.5, help='排异强度 k（默认 3.5；0=关闭）')
    ap.add_argument('--limit', type=int, default=0, help='只取前 N 帧（0=全部）')
    ap.add_argument('--crop', type=int, default=0, help='裁剪边长（0=全幅）')
    ap.add_argument('--center', default='', help='裁剪中心 y,x（留空自动定位）')
    ap.add_argument('--env-win-mult', type=float, default=2.0, help='包络窗倍数（默认 2）')
    ap.add_argument('--env-eps', type=float, default=8.0, help='包络噪声项（默认 8）')
    ap.add_argument('--env-p', type=float, default=1.0, help='包络形状幂（默认 1）')
    ap.add_argument('--r-floor', type=float, default=0.0, help='R 下限（默认 0）')
    ap.add_argument('--n-star', type=int, default=120, help='星表星数（默认 120）')
    ap.add_argument('--star-fwhm', type=float, default=6.0,
                    help='星表检测的 fwhm（默认 6.0，引擎硬编码值）。'
                         '实际星点 FWHM 更小时应调低，否则漏检弱星')
    ap.add_argument('--star-thr-sigma', type=float, default=5.0,
                    help='星表检测阈值（单位 σ，默认 5.0，引擎硬编码值）。'
                         '单帧星点 SNR 低时可调低（如 3.0），注意会显著增加噪声峰')
    ap.add_argument('--rej-gate', type=float, default=1 / 3, help='少数派闸门（默认 1/3）')
    ap.add_argument('--keep-frames', action='store_true',
                    help='运行后保留临时帧文件（反复调参时用）')

    # 预检与安全阀
    ap.add_argument('--skip-precheck', action='store_true',
                    help='跳过输入前提预检（危险：未校准/未对齐会静默产出废图）')
    ap.add_argument('--precheck-sample', type=int, default=24, help='预检抽样帧数')
    ap.add_argument('--all-frames-precheck', action='store_true', help='预检检查全部帧')
    ap.add_argument('--force', action='store_true',
                    help='预检 FAIL 时仍然继续（需用户明确要求；风险自负）')
    ap.add_argument('--allow-B-override', action='store_true',
                    help='允许把 B 改成非 2 的值（仅实验用途）')
    ap.add_argument('--json-out', default='', help='把结果 JSON 另存到该路径')
    a = ap.parse_args(argv)

    _assert_no_qt()
    photos = Path(a.photos).expanduser()
    result: dict = {'photos': str(photos), 'out': a.out, 'frames_dir': a.frames_dir,
                    'A': float(a.A), 'B': float(a.B)}

    # ---- 硬约束：B = 2 ----
    if abs(float(a.B) - LOCKED_B) > 1e-9 and not a.allow_B_override:
        result['error'] = (f'B={a.B:g} 被修改。B=2 由逆方差最优性给出（论文 3.3 节），'
                           '不是可调参数；如确为实验用途，请加 --allow-B-override。')
        _log(f"[拒绝] {result['error']}")
        return _emit(result, a.json_out, 20)

    # ---- 硬约束：--frames-dir 必须显式给出 ----
    if not (a.frames_dir or '').strip():
        result['error'] = ('必须显式指定 --frames-dir。166 帧全幅约需 40 GiB，'
                           'skill 不允许让引擎自动选盘。')
        _log(f"[拒绝] {result['error']}")
        return _emit(result, a.json_out, 20)

    if not photos.is_dir():
        result['error'] = f'素材目录不存在：{photos}'
        _log(f"[拒绝] {result['error']}")
        return _emit(result, a.json_out, 20)

    # ---- 硬约束：输出目录不得与素材目录相同 ----
    # 引擎只检查 --out 非空（其报错文案虽提到「不放在素材目录里」，但并未实现）。
    # 成品名为 stack_<tag>.xisf，与帧同为 *.xisf；若同目录，下一轮
    # photos.glob('*.xisf') 会把上一轮成品当成输入帧读入 —— 形状一致、不报错，
    # 却静默产出一张以成品为输入的废图。此处显式拦截。
    if not (a.out or '').strip():
        result['error'] = ('必须显式指定 --out。成品需与帧分目录存放，'
                           '否则下一轮会把成品当成帧读入。')
        _log(f"[拒绝] {result['error']}")
        return _emit(result, a.json_out, 20)
    out_p = Path(a.out).expanduser()
    if out_p.resolve() == photos.resolve():
        _log(f'[拒绝] 输出目录与素材目录相同：{photos.resolve()}。'
             '成品 stack_<tag>.xisf 会被下一轮 glob(\'*.xisf\') 当成输入帧，'
             '静默产出废图。请换一个独立的 --out 目录。')
        result['error'] = f'输出目录与素材目录相同：{photos.resolve()}'
        return _emit(result, a.json_out, 20)

    # ---- 1. fail-fast 预检 ----
    pre = None
    if a.skip_precheck:
        _log('[预检] 已按 --skip-precheck 跳过（未校准/未对齐的输入会静默产出废图）')
        result['precheck'] = {'verdict': 'SKIPPED', 'exit_code': 0}
    else:
        from precheck import run_precheck
        _log('[预检] 输入前提检查：对齐 / 校准 / FWHM 中位一致性 / 落盘目录')
        pre = run_precheck(str(photos), a.frames_dir, crop=int(a.crop),
                           center=(a.center or ''), limit=int(a.limit),
                           sample=int(a.precheck_sample),
                           all_frames=bool(a.all_frames_precheck),
                           n_star=int(a.n_star), out=str(out_p),
                           star_fwhm=float(a.star_fwhm),
                           star_thr_sigma=float(a.star_thr_sigma))
        result['precheck'] = pre
        for c in pre['checks']:
            if c['status'] != 'PASS':
                _log(f"  [{c['status']}] {c['id']}: {c['detail']}")
        for w in pre['warnings']:
            _log(f'  [注意] {w}')
        if pre['verdict'] == 'FAIL':
            for b in pre['blockers']:
                _log(f'  [阻断] {b}')
            if not a.force:
                _log('[终止] 预检未通过，未调用引擎、未写入任何成品。')
                return _emit(result, a.json_out, 20)
            _log('[继续] 已按 --force 越过预检阻断；成品可能为废图。')
        else:
            _log(f"[预检] {pre['verdict']}")

    # ---- 2. 调用引擎 ----
    import pws
    from pws_params import PwsParams  # noqa: F401  (供 build_params 使用)

    p = build_params(a)
    _log(f'[运行] A={p.A:g} B={p.B:g} rej_k={p.rej_k:g} '
         f'crop={p.crop} limit={p.limit} keep_frames={p.keep_frames}')
    _log(f'[运行] 素材 {p.photos}')
    _log(f'[运行] 成品 {p.out}')
    _log(f'[运行] 临时帧 {p.frames_dir}')

    try:
        res = pws.run(p, on_log=_log, on_progress=None, should_cancel=None)
    except SystemExit as e:
        _log(f'[引擎拒绝] {e}')
        result['error'] = str(e)
        return _emit(result, a.json_out, 20)
    except Exception as e:
        import traceback
        _log(traceback.format_exc())
        result['error'] = f'{type(e).__name__}: {e}'
        return _emit(result, a.json_out, 1)

    _assert_no_qt()

    # ---- 3. 汇总结果（不把整幅成品塞进 JSON）----
    out_dir = Path(p.out)
    tag = p.tag or 'stack'
    fw = res.get('fwhms')
    summary = {
        'status': 'ok',
        'n_frames': int(res.get('n', 0)),
        'shape': list(res.get('shape', [])),
        'fwhm_med_px': _f(res.get('fwhm_med')),
        'fwhm_out_px': _f(res.get('fwhm_out')),
        'fwhm_improve_pct': _f(_improve(res.get('fwhm_med'), res.get('fwhm_out'))),
        'sigma_psf_px': _f(res.get('sigma_psf')),
        'rej_rate': _f(res.get('rej_rate')),
        'A': _f(res.get('A')), 'B': _f(res.get('B')), 'rej_k': _f(res.get('rej_k')),
        'r_info': {k: _f(v) for k, v in (res.get('R_info') or {}).items()},
        'neff_median': _f(_neff(res)),
        'fwhm_spread': _f(_spread(fw)),
        'outputs': {
            'stack': str(out_dir / f'stack_{tag}.xisf'),
            'weights': str(out_dir / f'weights_{tag}.txt'),
            'R_field': str(out_dir / f'R_{tag}.npy'),
            'neff_field': str(out_dir / f'neff_{tag}.npy'),
        },
    }
    result['result'] = summary
    exit_code = _emit(result, a.json_out, 10 if (pre and pre['verdict'] == 'WARN') else 0)
    if exit_code == 1:
        return exit_code

    _log('')
    _log(f"[完成] 成品星点 FWHM {summary['fwhm_out_px']}px "
         f"（单帧中位 {summary['fwhm_med_px']}px，改善 {summary['fwhm_improve_pct']}%）")
    _log(f"[完成] 成品 {summary['outputs']['stack']}")
    return exit_code


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 6)          # NaN → None（JSON 无 NaN）


def _improve(med, out):
    try:
        return 100.0 * (1 - float(out) / float(med))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _spread(fw):
    try:
        import numpy as np
        a = np.asarray(fw, dtype=float)
        a = a[np.isfinite(a)]
        return float(a.max() / a.min()) if a.size else None
    except Exception:
        return None


def _neff(res):
    try:
        import numpy as np
        n = res.get('neff')
        return float(np.median(n)) if n is not None else None
    except Exception:
        return None


def _emit(result: dict, json_out: str, exit_code: int) -> int:
    result['exit_code'] = exit_code
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if json_out:
        try:
            Path(json_out).expanduser().write_text(text, encoding='utf-8')
        except (OSError, RuntimeError) as error:
            result['json_out_error'] = f'{type(error).__name__}: {error}'
            result['exit_code'] = 1
            _log(f"[archive error] {result['json_out_error']}")
            text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text, flush=True)
    return int(result['exit_code'])


if __name__ == '__main__':
    raise SystemExit(main())
