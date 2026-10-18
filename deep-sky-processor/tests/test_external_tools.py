"""外部工具探测器（external_tools）测试。

三组断言：
  1. **四级优先链** —— 显式 > 环境变量 > PATH > 安装目录，且「显式不存在」
     必须返回 None 不回退（防静默换工具）。
  2. **跨平台路径表** —— Windows / Linux / macOS 三个平台的安装目录都必须
     非空且含 ``.exe``（Windows）或可执行名（POSIX）。这是「技能迁到 Windows
     会不会又踩工具链缺失」的可回归保证。
  3. **诊断器** —— 逐级展开必须能指出断在哪一级；「工具找不到」靠猜很难定位。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import external_tools as et

def _make_executable(path):
    """建一个带可执行位的空文件。

    必须 chmod：探测器的 ``_is_executable_file`` 在 POSIX 上要求
    ``os.access(X_OK)``，``NamedTemporaryFile`` 写出来的文件默认 0600 不可执行，
    会被正确跳过 —— 那是探测器的正确行为，测试要配合而非绕过。
    """
    path = Path(path)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o755)
    return str(path)




class PriorityChainTest(unittest.TestCase):
    def test_explicit_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit = _make_executable(os.path.join(tmp, 'deepsnr'))
            with patch.dict(os.environ, {'DEEPSNR_PATH': '/nonexistent'}):
                self.assertEqual(
                    et.find_executable('deepsnr', user_path=explicit),
                    explicit)

    def test_explicit_missing_does_not_fall_back(self):
        """显式指定却不存在 ⇒ None。静默回退会让用户以为跑的是 A 工具。"""
        with patch.dict(os.environ, {'DEEPSNR_PATH': '/usr/local/bin'}):
            self.assertIsNone(
                et.find_executable('deepsnr', user_path='/nonexistent/deepsnr'))

    def test_env_var_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _make_executable(os.path.join(tmp, 'starnet2'))
            with patch.dict(os.environ, {'STARNET_PATH': tmp}):
                self.assertEqual(et.find_executable('starnet'), target)

    def test_env_var_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _make_executable(os.path.join(tmp, 'deepsnr'))
            with patch.dict(os.environ, {'DEEPSNR_PATH': target}):
                self.assertEqual(et.find_executable('deepsnr'), target)

    def test_invalid_env_var_falls_through(self):
        """环境变量是最弱的显式意图，无效时不该一票否决。"""
        with patch.dict(os.environ, {'DEEPSNR_PATH': '/nonexistent'}):
            # 本机装有 /usr/local/bin/deepsnr ⇒ 应回退到安装目录并命中
            self.assertIsNotNone(et.find_executable('deepsnr'))

    def test_which_level(self):
        sentinel = '/synthetic/which/deepsnr'
        with patch.dict(os.environ, {}, clear=True), \
                patch('external_tools.shutil.which',
                      side_effect=lambda name, _s=sentinel: _s if name == 'deepsnr' else None), \
                patch('external_tools.os.path.isfile', return_value=False):
            self.assertEqual(et.find_executable('deepsnr'), sentinel)

    def test_custom_env_var_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _make_executable(os.path.join(tmp, 'deepsnr'))
            with patch.dict(os.environ, {'MY_DSNR': tmp}):
                self.assertEqual(
                    et.find_executable('deepsnr', env_var='MY_DSNR'), target)

    def test_unknown_tool_raises(self):
        with self.assertRaises(KeyError):
            et.find_executable('no-such-tool')

    def test_thin_wrappers_delegate(self):
        """三处业务模块的探测器必须都委托给通用实现，不能各自硬编码。"""
        import inspect

        import denoise
        import plate_solve
        import star_tools
        for finder, tool in ((denoise.find_deepsnr_executable, 'deepsnr'),
                             (star_tools.find_starnet_executable, 'starnet'),
                             (plate_solve.find_solve_field, 'solve-field')):
            with self.subTest(tool=tool):
                # 只看函数体本身：模块别处仍可能有其它平台分支（如 star_tools
                # 自己的 CLI 参数解析），不能因此判失败。
                body = inspect.getsource(finder)
                self.assertIn('find_executable', body)
                self.assertNotIn('sys.platform', body)
                self.assertLess(len(body.splitlines()), 20,
                                '%s 的探测器应是无逻辑的薄封装' % tool)


class CrossPlatformTableTest(unittest.TestCase):
    """路径表本身就是「迁到 Windows 会不会再踩坑」的可回归保证。"""

    def test_all_tools_have_all_platforms(self):
        for tool in et.INSTALL_DIRS:
            for platform in ('darwin', 'linux', 'win32'):
                self.assertIn(platform, et.INSTALL_DIRS[tool],
                              '%s 缺 %s 路径表' % (tool, platform))
                self.assertTrue(et.INSTALL_DIRS[tool][platform],
                                '%s 的 %s 路径表为空' % (tool, platform))

    def test_windows_entries_are_exe(self):
        """Windows 上可执行文件必须带 .exe，否则 os.path.isfile 找不到。"""
        for tool, paths in et.INSTALL_DIRS.items():
            for raw in paths['win32']:
                self.assertTrue(raw.lower().endswith('.exe'),
                                '%s 的 Windows 路径缺 .exe 后缀: %s'
                                % (tool, raw))

    def test_posix_entries_have_no_backslash(self):
        for tool, paths in et.INSTALL_DIRS.items():
            for platform in ('darwin', 'linux'):
                for raw in paths[platform]:
                    self.assertNotIn('\\', raw,
                                     '%s 的 %s 路径混入了反斜杠: %s'
                                     % (tool, platform, raw))

    def test_windows_program_files_covered(self):
        """官方安装包默认落在 Program Files，必须被覆盖。"""
        for tool in ('deepsnr', 'starnet', 'solve-field'):
            joined = ' '.join(et.INSTALL_DIRS[tool]['win32']).lower()
            self.assertIn('program files', joined, tool)

    def test_binary_names_cover_windows_suffixes(self):
        for tool, names in et.BINARY_NAMES.items():
            self.assertTrue(
                any(name.lower().endswith('.exe') for name in names),
                '%s 的候选名里没有 .exe' % tool)

    def test_env_vars_declared_for_every_tool(self):
        for tool in et.INSTALL_DIRS:
            self.assertIn(tool, et.ENV_VARS)
            self.assertTrue(et.ENV_VARS[tool].endswith('_PATH'))

    def test_no_lone_backslash_in_source(self):
        """防止 Windows 路径写成普通字符串时 \\b \\t 被当转义序列。"""
        source = (SCRIPTS / 'external_tools.py').read_text(encoding='utf-8')
        for tool, paths in et.INSTALL_DIRS.items():
            for platform, entries in paths.items():
                for raw in entries:
                    if platform != 'win32':
                        continue
                    # 该字面量必须以 r 前缀或含双反斜杠出现在源码里
                    self.assertTrue(
                        ('"%s"' % raw.replace('\\', '\\\\')) in source
                        or ("r'%s'" % raw) in source
                        or ('"%s"' % raw) in source,
                        'Windows 路径 %s 未正确转义' % raw)

    def test_platform_detection(self):
        with patch('external_tools.sys.platform', 'darwin'):
            self.assertEqual(et.current_platform(), 'darwin')
        with patch('external_tools.sys.platform', 'win32'):
            self.assertEqual(et.current_platform(), 'win32')
        with patch('external_tools.sys.platform', 'linux'):
            self.assertEqual(et.current_platform(), 'linux')


class NormalizeTest(unittest.TestCase):
    """``_normalize`` 必须跨平台安全 —— 这是迁移到 Windows 的关键。

    ``os.path.abspath`` 是**平台相关**的：在 POSIX 上它会给
    ``C:\\Program Files\\x`` 拼上当前工作目录，变成永不命中的路径，而且是
    **静默** miss。路径表同时含三平台条目，所以任何平台都不能无条件用它。
    """

    def test_windows_drive_letter_preserved(self):
        raw = r'C:\Program Files\DeepSNR\deepsnr.exe'
        self.assertEqual(et._normalize(raw), raw)

    def test_unc_path_preserved(self):
        raw = r'\\server\share\deepsnr.exe'
        self.assertEqual(et._normalize(raw), raw)

    def test_posix_absolute_preserved(self):
        self.assertEqual(et._normalize('/usr/local/bin/deepsnr'),
                         '/usr/local/bin/deepsnr')

    def test_tilde_expanded(self):
        self.assertNotIn('~', et._normalize('~/bin/deepsnr'))

    def test_relative_gets_cwd(self):
        self.assertTrue(os.path.isabs(et._normalize('relative/deepsnr')))

    def test_looks_absolute(self):
        for raw in (r'C:\x', r'c:/x', '/x', r'\x', r'\\srv\share'):
            self.assertTrue(et._looks_absolute(raw), raw)
        for raw in ('x', 'x/y', './x', '../x'):
            self.assertFalse(et._looks_absolute(raw), raw)

    def test_no_abspath_expanduser_in_paths_table_scan(self):
        """路径表扫描不得直接用 abspath(expanduser(...)) —— 会破坏盘符。"""
        import ast

        source = (SCRIPTS / 'external_tools.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        # 只看真正执行的代码，避开 docstring 里对这些 API 的文字说明
        abspath_calls = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'abspath'):
                abspath_calls.add(node.lineno)
        # abspath 只允许出现在 _normalize 内部与 __file__ 相关处
        allowed_lines = {
            node.body[0].lineno
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == '_normalize'
            and node.body
            and isinstance(node.body[-1], ast.Return)
        }
        normalize_body = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == '_normalize')
        allowed_lines.update(
            line for line in range(normalize_body.lineno,
                                   normalize_body.end_lineno + 1))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'abspath'
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == 'path'):
                inner = node.args[0] if node.args else None
                is_dunder_file = (
                    isinstance(inner, ast.Name) and inner.id == '__file__')
                self.assertTrue(
                    is_dunder_file or node.lineno in allowed_lines,
                    '第 %d 行对非 __file__ 使用 abspath，跨平台会破坏路径: %s'
                    % (node.lineno,
                       ast.get_source_segment(source, node)))
        self.assertTrue(abspath_calls, '未找到 abspath 调用，测试本身可能失效')

    def test_windows_program_files_layout_is_found(self):
        """端到端模拟：win32 平台 + Program Files 安装布局必须能命中。"""
        with tempfile.TemporaryDirectory() as tmp:
            progfiles = Path(tmp) / 'Program Files' / 'DeepSNR'
            progfiles.mkdir(parents=True)
            exe = progfiles / 'deepsnr.exe'
            exe.write_text('')
            table = dict(et.INSTALL_DIRS)
            table['deepsnr'] = dict(table['deepsnr'])
            table['deepsnr']['win32'] = [str(exe)]
            with patch.dict(os.environ, {}, clear=True), \
                    patch('external_tools.current_platform',
                          return_value='win32'), \
                    patch('external_tools.shutil.which', return_value=None), \
                    patch.dict(et.INSTALL_DIRS, table, clear=True):
                report = et.probe_environment('deepsnr')
            self.assertEqual(report['resolved'], str(exe))
            install_step = [s for s in report['steps']
                            if s['level'].startswith('install_dirs')][0]
            self.assertTrue(install_step['hit'])

    def test_windows_existence_check_ignores_x_ok(self):
        """Windows 上 os.access(X_OK) 语义不同，只判存在 + 是文件。"""
        with patch('external_tools.current_platform', return_value='win32'), \
                patch('external_tools.os.path.isfile', return_value=True), \
                patch('external_tools.os.access', return_value=False):
            self.assertTrue(et._is_executable_file('C:/x/deepsnr.exe'))

    def test_posix_existence_check_requires_x_ok(self):
        with patch('external_tools.current_platform', return_value='darwin'), \
                patch('external_tools.os.path.isfile', return_value=True), \
                patch('external_tools.os.access', return_value=False):
            self.assertFalse(et._is_executable_file('/usr/local/bin/deepsnr'))


class DiagnosticTest(unittest.TestCase):
    def test_probe_reports_every_level(self):
        report = et.probe_environment('deepsnr')
        levels = [step['level'] for step in report['steps']]
        self.assertEqual(levels, [
            'explicit', 'env:DEEPSNR_PATH', 'which',
            'install_dirs:%s' % et.current_platform(), 'script_dir'])
        self.assertIn('resolved', report)
        self.assertIn('platform', report)

    def test_probe_stops_at_bad_explicit(self):
        report = et.probe_environment('deepsnr', user_path='/nonexistent/x')
        self.assertIsNone(report['resolved'])
        self.assertEqual(len(report['steps']), 1)
        self.assertIn('note', report)

    def test_probe_which_level_can_diagnose_path_absence(self):
        """PATH 缺失时 which 必 miss —— 这正是 C50 那个 bug 的根因层。"""
        with patch.dict(os.environ, {}, clear=True), \
                patch('external_tools.shutil.which', return_value=None):
            report = et.probe_environment('deepsnr')
        which_step = [s for s in report['steps'] if s['level'] == 'which'][0]
        self.assertFalse(which_step['hit'])
        # 安装目录仍可能命中（本机装有）
        install_step = [s for s in report['steps']
                        if s['level'].startswith('install_dirs')][0]
        self.assertTrue(install_step['hit'])


class RealWorldTest(unittest.TestCase):
    """本机实际状态（安装位置因机器而异，用「不抛异常 + 类型正确」验收）。"""

    def test_all_probes_run_without_raising(self):
        for tool in et.INSTALL_DIRS:
            with self.subTest(tool=tool):
                report = et.probe_environment(tool)
                self.assertIsInstance(report['resolved'], (str, type(None)))

    def test_deepsnr_resolvable_on_this_machine(self):
        """本机装有 DeepSNR（1.3.2）⇒ 必须能发现。这是回归保护。"""
        self.assertIsNotNone(et.find_executable('deepsnr'))

    def test_starnet_resolvable_on_this_machine(self):
        self.assertIsNotNone(et.find_executable('starnet'))


if __name__ == '__main__':
    unittest.main()
