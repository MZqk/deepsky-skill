#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pws-stacking 回归测试。

覆盖输入前提闸门在「用户最可能遇到的错误输入」上的契约：预检必须以
**纯 JSON + 退出码**表达失败，不得抛栈（stdout 是 agent 解析用的 JSON 协议）。
"""
from __future__ import annotations

import errno
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / 'scripts'

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def run_precheck(photos: Path, frames_dir: Path, out: Path | None = None) -> dict:
    """以 import 方式调用 run_precheck；失败契约由断言校验。"""
    import precheck

    return precheck.run_precheck(str(photos), str(frames_dir),
                                 out=str(out) if out is not None else '')


def assert_clean_fail(result: dict, *, must_mention: str | None = None,
                      check_id: str = 'input') -> None:
    """失败必须以契约内表达：无异常、verdict FAIL、exit 20、blockers 非空。"""
    assert result['verdict'] == 'FAIL', result
    assert result['exit_code'] == 20, result
    assert result['blockers'], result
    assert any(c['id'] == check_id and c['status'] == 'FAIL'
               for c in result['checks']), result
    if must_mention:
        assert any(must_mention in b for b in result['blockers']), result


def test_empty_dir_fails_cleanly(tmp_path: Path) -> None:
    """空目录：此前 _crop_region 对 files[0] 抛 IndexError。"""
    result = run_precheck(tmp_path, tmp_path / 'frames')
    assert_clean_fail(result, must_mention='没有 XISF / FITS 帧')


def test_non_frame_files_only_fails_cleanly(tmp_path: Path) -> None:
    """目录里只有非帧文件（如误指到文档目录）同样走契约内失败。"""
    (tmp_path / 'readme.txt').write_text('not a frame', encoding='utf-8')
    (tmp_path / 'notes.md').write_text('# notes', encoding='utf-8')
    result = run_precheck(tmp_path, tmp_path / 'frames')
    assert_clean_fail(result, must_mention='没有 XISF / FITS 帧')


def test_unreadable_first_frame_fails_cleanly(tmp_path: Path) -> None:
    """首帧损坏：探针读帧此前会把底层异常直接抛到用户面前。"""
    (tmp_path / 'bad.xisf').write_bytes(b'not-an-xisf')
    result = run_precheck(tmp_path, tmp_path / 'frames')
    assert_clean_fail(result, must_mention='首帧不可读')


def test_missing_photos_dir_fails_cleanly(tmp_path: Path) -> None:
    """路径写错：最常见的入口错误，必须是契约内 FAIL 而非异常。"""
    result = run_precheck(tmp_path / 'does-not-exist', tmp_path / 'frames')
    assert_clean_fail(result, must_mention='素材目录不存在')


def test_cli_stdout_is_parseable_json(tmp_path: Path) -> None:
    """CLI 契约：stdout 纯 JSON、退出码 20、stderr 无 traceback。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / 'precheck.py'),
         '--photos', str(tmp_path), '--frames-dir', str(tmp_path / 'frames')],
        capture_output=True, text=True,
    )
    assert proc.returncode == 20, proc.stderr
    payload = json.loads(proc.stdout)          # 若 stdout 混入日志或栈则抛异常 → 测试失败
    assert payload['verdict'] == 'FAIL'
    assert 'Traceback' not in proc.stderr


def test_missing_frames_dir_is_rejected_by_argparse() -> None:
    """--frames-dir 为必填：缺失时 argparse 以 exit 2 拒绝（SKILL.md 故障表已注明）。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / 'precheck.py'), '--photos', '.'],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2, proc.stdout
    assert '--frames-dir' in proc.stderr


# --------------------------------------------------------------------------
# 输出目录不得与素材目录重合：成品 stack_<tag>.xisf 会被下一轮 glob('*.xisf')
# 当成输入帧，形状一致故不报错，却静默产出一张以成品为输入的废图。
# --------------------------------------------------------------------------

def test_out_equal_photos_is_blocked(tmp_path: Path) -> None:
    """--out 与 --photos 同目录必须阻断，且在读帧之前就拦下。"""
    result = run_precheck(tmp_path, tmp_path / 'frames', out=tmp_path)
    assert_clean_fail(result, must_mention='输出目录与素材目录相同', check_id='outdir')
    assert any(c['id'] == 'outdir' for c in result['checks']), result


def test_out_equal_photos_blocked_despite_trailing_slash_and_dot(tmp_path: Path) -> None:
    """等价路径写法（尾斜杠、`.` 段、未解析相对路径）同样命中，不得绕过。"""
    for variant in (f'{tmp_path}/', f'{tmp_path}/./', f'{tmp_path}/./.'):
        result = run_precheck(tmp_path, tmp_path / 'frames', out=variant)
        assert_clean_fail(result, must_mention='输出目录与素材目录相同', check_id='outdir')


def test_distinct_out_dir_passes_outdir_gate(tmp_path: Path) -> None:
    """独立的 --out 目录不应被误拦（此处随后因空目录 FAIL，但 outdir 项须 PASS）。"""
    result = run_precheck(tmp_path, tmp_path / 'frames', out=tmp_path / 'out')
    assert any(c['id'] == 'outdir' and c['status'] == 'PASS' for c in result['checks']), result


def test_out_omitted_is_not_an_error(tmp_path: Path) -> None:
    """未给 --out 时不校验输出目录（单独运行预检的常见用法）。"""
    result = run_precheck(tmp_path, tmp_path / 'frames')
    assert any(c['id'] == 'outdir' and c['status'] == 'PASS' for c in result['checks']), result


def test_runner_rejects_out_equal_photos(tmp_path: Path) -> None:
    """pws_run.py 侧同样拦截，且 exit 20、stdout 仍为可解析 JSON。"""
    frames = tmp_path / 'frames'
    frames.mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / 'pws_run.py'),
         '--photos', str(frames), '--out', str(frames),
         '--frames-dir', str(tmp_path / 'fd')],
        capture_output=True, text=True,
    )
    assert proc.returncode == 20, proc.stdout
    assert '输出目录与素材目录相同' in proc.stderr
    json.loads(proc.stdout)          # stdout 契约不得因新增拒绝分支而破坏


@pytest.mark.parametrize(('extra_args', 'error'), [
    (['--B', '3'], 'B=3'),
    (['--frames-dir', ''], '--frames-dir'),
    (['--photos', 'missing'], '素材目录不存在'),
    (['--out', ''], '--out'),
])
def test_runner_validation_errors_emit_json(tmp_path: Path,
                                           extra_args: list[str], error: str) -> None:
    """Validation failures must emit one JSON error and preserve exit code 20."""
    photos = tmp_path / 'photos'
    photos.mkdir()
    json_out = tmp_path / 'result.json'
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / 'pws_run.py'),
         '--photos', str(photos), '--out', str(tmp_path / 'out'),
         '--frames-dir', str(tmp_path / 'frames'), '--json-out', str(json_out),
         *extra_args],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proc.returncode == 20, proc.stderr
    payload = json.loads(proc.stdout)
    assert error in payload['error'], payload
    assert error in proc.stderr
    assert 'Traceback' not in proc.stderr
    assert json.loads(json_out.read_text(encoding='utf-8')) == payload
    assert not (tmp_path / 'out').exists()
    assert not (tmp_path / 'frames').exists()


def test_runner_missing_required_argument_keeps_argparse_contract(tmp_path: Path) -> None:
    """Argument parsing errors keep exit code 2 and stderr diagnostics."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / 'pws_run.py'),
         '--photos', str(tmp_path), '--out', str(tmp_path / 'out')],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert proc.stdout == ''
    assert '--frames-dir' in proc.stderr
    assert 'Traceback' not in proc.stderr


@pytest.mark.parametrize('destination', ['missing/result.json', '.'])
def test_runner_archive_errors_preserve_validation_error(tmp_path: Path,
                                                        destination: str) -> None:
    """Invalid archive paths must preserve the original error in stdout JSON."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / 'pws_run.py'),
         '--photos', str(tmp_path), '--out', str(tmp_path / 'out'),
         '--frames-dir', str(tmp_path / 'frames'), '--B', '3',
         '--json-out', str(tmp_path / destination)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload['exit_code'] == proc.returncode
    assert 'B=3' in payload['error']
    assert payload['json_out_error']
    assert '[archive error]' in proc.stderr
    assert 'Traceback' not in proc.stderr
    assert not (tmp_path / 'missing').exists()


@pytest.mark.parametrize('error_number', [errno.EACCES, errno.ENOSPC])
def test_archive_io_errors_emit_json(tmp_path: Path, monkeypatch, capsys,
                                     error_number: int) -> None:
    """Permission and disk-full failures must emit JSON without a traceback."""
    import pws_run

    def fail_write(path, *args, **kwargs):
        raise OSError(error_number, 'archive write failed', str(path))

    monkeypatch.setattr(Path, 'write_text', fail_write)
    code = pws_run._emit({'result': {'status': 'ok'}}, str(tmp_path / 'result.json'), 0)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == payload['exit_code'] == 1
    assert payload['result'] == {'status': 'ok'}
    assert f'[Errno {error_number}]' in payload['json_out_error']
    assert '[archive error]' in captured.err
    assert 'Traceback' not in captured.err


@pytest.mark.parametrize('archive_available', [True, False])
def test_runner_archive_status_after_stack(tmp_path: Path, monkeypatch, capsys,
                                          archive_available: bool) -> None:
    """A completed stack must not hide archive failure behind a success exit."""
    import pws_run

    monkeypatch.setitem(sys.modules, 'pws', SimpleNamespace(run=lambda *args, **kwargs: {}))
    monkeypatch.setitem(sys.modules, 'pws_params', SimpleNamespace(PwsParams=object))
    monkeypatch.setattr(pws_run, 'build_params', lambda args: args)
    json_out = tmp_path / 'archive' / 'result.json'
    if archive_available:
        json_out.parent.mkdir()
    code = pws_run.main([
        '--photos', str(tmp_path), '--out', str(tmp_path / 'out'),
        '--frames-dir', str(tmp_path / 'frames'), '--skip-precheck',
        '--json-out', str(json_out),
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == payload['exit_code'] == (0 if archive_available else 1)
    assert payload['result']['status'] == 'ok'
    assert ('[完成]' in captured.err) == archive_available
    assert 'Traceback' not in captured.err
    if archive_available:
        assert json.loads(json_out.read_text(encoding='utf-8')) == payload
    else:
        assert payload['json_out_error']
