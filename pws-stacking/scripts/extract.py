#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把仓库源码**提取**进 skill 的 scripts/vendor/（可重复执行、幂等）。

设计要点：为什么是"提取"而不是"复制一个包"
--------------------------------------------
上游 core/pws.py 用 `_HERE = Path(__file__).parent` / `ROOT = _HERE.parent` 定位仓库根，
再 `sys.path.insert` 后以**顶层模块**方式导入 `pws_params` / `DWT_DetailCore` / `xisf_io`
（不是相对导入，也没有 __init__.py）。因此只要把四个 .py 平铺放进**同一个目录**，
`ROOT` 自然解析为该目录，四个模块的相互导入即全部成立——**源码无需任何改动**。
本脚本据此把文件原样提取，并校验字节一致，确保 skill 里的引擎与仓库逐位相同。

依赖闭包（上游 core/pws.py 实际 import 到的本仓库文件）：
    core/pws.py          引擎本体（含 CLI 入口；GUI 在 ui/ 下，不在闭包内）
    core/pws_params.py   参数定义（PwsParams / PARAMS），引擎与 CLI 共用
    DWT_DetailCore.py    帧读取（FITS/XISF）与延展源定位
    xisf_io.py           XISF 读写（DWT_DetailCore 依赖）
**不含** main.py 与 ui/：那两处是界面入口，正是 skill 要避开的 GUI 调用路径。

用法：
    python extract.py                 # 从默认仓库路径提取
    python extract.py --src <仓库根>   # 指定仓库根
    python extract.py --check         # 只校验，不写入（CI 用）
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE / 'vendor'

# (仓库内相对路径, vendor 内文件名) —— 平铺，保证上游的顶层导入与 ROOT 推导成立
FILES = (
    ('core/pws.py', 'pws.py'),
    ('core/pws_params.py', 'pws_params.py'),
    ('DWT_DetailCore.py', 'DWT_DetailCore.py'),
    ('xisf_io.py', 'xisf_io.py'),
)
# 上游 MIT 授权文件与依赖清单：随提取的源码一并带入，满足再分发与安装需要
EXTRA = (
    ('LICENSE', 'LICENSE'),
    ('THIRD_PARTY_LICENSES.md', 'THIRD_PARTY_LICENSES.md'),
    ('requirements.txt', 'requirements.txt'),
)
# 源码仓库的已知位置。skill 可能被**搬离仓库**（例如暂存到 ~/dev/skill/），
#   此时无法再从 __file__ 推导仓库根，故按下列顺序探测；都不中就要求显式 --src。
SRC_CANDIDATES = (
    Path.home() / 'dev' / 'Partition-Weighted-Stacking',
    Path.home() / 'Partition-Weighted-Stacking',
)
PROVENANCE = _HERE / 'VENDOR.json'      # 记录提取来源，便于日后核对漂移


def _is_repo_root(p: Path) -> bool:
    return (p / 'core' / 'pws.py').is_file() and (p / 'xisf_io.py').is_file()


def find_src() -> Path | None:
    """定位上游仓库根：① 从本文件向上找 ② 已知候选路径 ③ 上次记录的来源。"""
    for parent in _HERE.parents:                 # ① 仍位于仓库内时（.agents/skills/…）
        if _is_repo_root(parent):
            return parent
    if PROVENANCE.is_file():                     # ③ 上次提取时记录的来源
        try:
            import json
            prev = Path(json.loads(PROVENANCE.read_text(encoding='utf-8'))['src'])
            if _is_repo_root(prev):
                return prev
        except Exception:
            pass
    for c in SRC_CANDIDATES:                     # ② 已知候选
        if _is_repo_root(c):
            return c
    return None


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='extract',
                                 description='把 PWS 引擎源码提取进 skill scripts/vendor/')
    ap.add_argument('--src', default=None,
                    help='上游仓库根目录（缺省时自动探测；探测失败须显式给出）')
    ap.add_argument('--check', action='store_true', help='只校验一致性，不写入')
    a = ap.parse_args(argv)

    src = Path(a.src).expanduser().resolve() if a.src else find_src()
    if src is None:
        print('[错误] 未能自动定位上游仓库根。本 skill 可被搬离仓库，'
              '搬离后请用 --src <仓库根> 指定。\n'
              '        例如：--src ~/dev/Partition-Weighted-Stacking', file=sys.stderr)
        return 20
    if not _is_repo_root(src):
        print(f'[错误] {src} 下没有 core/pws.py 或 xisf_io.py，不是 PWS 仓库根',
              file=sys.stderr)
        return 20
    print(f'[来源] {src}')

    if not a.check:
        _VENDOR.mkdir(parents=True, exist_ok=True)

    bad = []
    for rel, name in FILES + EXTRA:
        s = src / rel
        d = _VENDOR / name
        if not s.is_file():
            print(f'[缺失] 上游没有 {rel}', file=sys.stderr)
            bad.append(rel)
            continue
        if a.check:
            if not d.is_file():
                print(f'[缺失] vendor/{name} 不存在')
                bad.append(name)
            elif sha(s) != sha(d):
                print(f'[不一致] {rel} ≠ vendor/{name}')
                bad.append(name)
            else:
                print(f'[一致] {name}')
            continue
        shutil.copyfile(s, d)
        print(f'[提取] {rel} → vendor/{name}  ({sha(d)[:12]})')

    if a.check:
        if bad:
            print(f'\n[结果] {len(bad)} 个文件不一致：{bad}', file=sys.stderr)
            return 20
        print(f'\n[结果] vendor/ 与上游（{src}）逐位一致')
        return 0

    # 记录来源，供 skill 被搬离仓库后仍能定位上游做漂移核对
    import json
    PROVENANCE.write_text(json.dumps({
        'src': str(src),
        'files': {name: sha(_VENDOR / name) for _rel, name in FILES + EXTRA},
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'[记录] 来源已写入 {PROVENANCE.name}')

    print(f'\n[完成] {len(FILES)} 个源文件 + {len(EXTRA)} 个声明文件已提取到 {_VENDOR}')
    print('[提示] 上游源码更新后请重跑本脚本；--check 可在 CI 中防止漂移。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
