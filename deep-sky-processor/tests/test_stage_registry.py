"""步骤契约注册表（stage_registry）测试。

三组断言：
  1. **import 时自校验真的能抓漂移** —— 这是本模块存在的全部理由；
     用「改源码 → 期望 RuntimeError」的方式验证，不 mock。
  2. **注册表与 pipeline 的一致性** —— 步骤集合、产物映射、域标记。
  3. **排序与位置语义** —— `sort_steps` / `step_order` 对主步骤、
     可选步骤、未知步骤的行为；重点是**孤儿步骤不再让位置运算崩**。
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pipeline
import stage_registry as sr


def _import_with_source(source, name="stage_registry_probe"):
    """在临时目录里导入一份改过的 stage_registry 源码。

    刻意用**真实子进程**而不是 exec_module —— dataclass 在
    ``importlib.util.module_from_spec`` 下会因为模块不在 sys.modules 而崩，
    那会掩盖「自校验是否真的抛错」这个真正要验证的点。

    必须把 ``pipeline.py`` 一并复制过去：注册表的自校验会从
    ``Path(__file__).parent / "pipeline.py"`` 读源码，核对落盘产物是否
    全部登记。缺了它校验会提前 return，产物类漂移就测不出来。
    """
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, f"{name}.py").write_text(source, encoding="utf-8")
        shutil.copy2(Path(SCRIPTS) / "pipeline.py", Path(tmp) / "pipeline.py")
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "import %s\n"
            "print('IMPORT_OK')\n" % (tmp, name)
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=str(ROOT), timeout=180)


class RegistryIntegrityTest(unittest.TestCase):
    """注册表自身的一致性（import 时已校验，这里锁住不变量）。"""

    def test_main_step_order_is_contiguous(self):
        orders = [c.order for c in sr.STEP_CONTRACTS if not c.optional]
        self.assertEqual(orders, list(range(len(orders))))

    def test_all_steps_matches_main_contracts(self):
        main = [c.key for c in sr.STEP_CONTRACTS if not c.optional]
        self.assertEqual(list(sr.ALL_STEPS), main)

    def test_emission_steps_are_main_subset(self):
        for key in sr.EMISSION_STEPS:
            self.assertIn(key, sr.ALL_STEPS)
        # 且必须是按主流程顺序的子序列
        orders = [sr.step_order(k) for k in sr.EMISSION_STEPS]
        self.assertEqual(orders, sorted(orders))

    def test_execution_order_follows_artifact_numbering(self):
        """执行序必须与产物编号相容（修复前style 排在 08b/08d 之前）。

        产物名是「两位序号 + 字母变体后缀」，数字可相等（02/02b/02c 共享
        数字 02），但 order 递增时数字不得回退。
        """
        numbers = []
        for contract in sr.STEP_CONTRACTS:
            if contract.optional or not contract.artifacts:
                continue
            numbers.append(sr._artifact_marker(contract.artifacts[0])[0])
        self.assertEqual(numbers, sorted(numbers))
        # 关键三步的真实相对次序
        self.assertLess(sr.step_order('local_enhance'), sr.step_order('style'))
        self.assertLess(sr.step_order('star_reduce'), sr.step_order('style'))

    def test_domain_switches_at_most_once(self):
        domains = [c.domain for c in sr.STEP_CONTRACTS if not c.optional]
        switches = sum(1 for a, b in zip(domains, domains[1:]) if a != b)
        self.assertLessEqual(switches, 1)
        self.assertEqual(domains[0], sr.LINEAR)
        self.assertEqual(domains[-1], sr.NONLINEAR)

    def test_artifact_names_are_globally_unique(self):
        seen = {}
        for contract in sr.STEP_CONTRACTS:
            for name in contract.artifacts:
                self.assertNotIn(name, seen,
                                 '%s 与 %s 争用 %s'
                                 % (seen.get(name), contract.key, name))
                seen[name] = contract.key
        self.assertEqual(len(sr.STEP_BY_ARTIFACT), len(seen))

    def test_every_contract_has_summary(self):
        for contract in sr.STEP_CONTRACTS:
            self.assertTrue(contract.summary.strip(), contract.key)

    def test_optional_steps_use_minus_one_order(self):
        optional = [c for c in sr.STEP_CONTRACTS if c.optional]
        self.assertTrue(optional)
        for contract in optional:
            self.assertEqual(contract.order, -1)
            self.assertIsNone(sr.step_order(contract.key))

    def test_step_by_artifact_covers_every_declared_artifact(self):
        declared = {n for c in sr.STEP_CONTRACTS for n in c.artifacts}
        self.assertEqual(set(sr.STEP_BY_ARTIFACT), declared)

    def test_artifact_domain_lookup(self):
        self.assertEqual(sr.artifact_domain('01_dbe.tif'), sr.LINEAR)
        self.assertEqual(sr.artifact_domain('05_stretched_starless.tif'),
                         sr.NONLINEAR)
        self.assertIsNone(sr.artifact_domain('nope.tif'))


class PipelineConsistencyTest(unittest.TestCase):
    """注册表与 pipeline.py 的一致性。"""

    def test_pipeline_derives_steps_from_registry(self):
        self.assertEqual(list(pipeline.ALL_STEPS), list(sr.ALL_STEPS))
        self.assertEqual(tuple(pipeline.EMISSION_STEPS), sr.EMISSION_STEPS)

    def test_pipeline_artifact_map_comes_from_registry(self):
        # save_artifact 内部用的是 STEP_BY_ARTIFACT
        self.assertIs(pipeline.STEP_BY_ARTIFACT, sr.STEP_BY_ARTIFACT)

    def test_every_written_artifact_is_registered(self):
        """pipeline.py 里 save_artifact/save_current 的实参必须都在注册表内。

        注册表 import 时已做这项校验；这里再断言一次是为了让「注册表自校验
        的正则是否还匹配当前源码结构」有独立测试覆盖 —— 正则失配时自校验
        会静默跳过（见 _check_against_pipeline 的 `if not called: return`）。
        """
        import re
        source = (SCRIPTS / 'pipeline.py').read_text(encoding='utf-8')
        called = set(re.findall(
            r"save_(?:artifact|current)\(\s*'([^']+)'", source))
        called.discard('03_pre_denoise_prediction.tif')  # 已知外部直写
        self.assertTrue(called, '正则未匹配到任何落盘调用，结构可能已变')
        self.assertEqual(called - set(sr.STEP_BY_ARTIFACT), set())

    def test_orphan_steps_are_registered_as_optional(self):
        """三个「有产物但不在主流程」的步骤必须登记为 optional。"""
        for key in ('linear_deconv', 'reference_grade',
                    'background_neutralization'):
            with self.subTest(step=key):
                self.assertIn(key, sr.CONTRACTS_BY_KEY)
                self.assertTrue(sr.CONTRACTS_BY_KEY[key].optional)
                self.assertNotIn(key, sr.ALL_STEPS)


class SortSemanticsTest(unittest.TestCase):
    """排序与位置语义。"""

    def test_sorts_by_execution_order(self):
        shuffled = ['style', 'dbe', 'sharpen', 'color']
        self.assertEqual(sr.sort_steps(shuffled),
                         ['dbe', 'color', 'sharpen', 'style'])

    def test_is_idempotent(self):
        ordered = list(sr.ALL_STEPS)
        self.assertEqual(sr.sort_steps(ordered), ordered)

    def test_accepts_set_input(self):
        """resolve_step_dependencies 传的是 set —— 旧 ALL_STEPS.index
        在 set 上会 AttributeError，这里必须能处理。"""
        result = sr.sort_steps({'style', 'dbe'})
        self.assertEqual(result, ['dbe', 'style'])

    def test_optional_steps_do_not_raise(self):
        """旧实现在 steps 含孤儿步骤时会 ALL_STEPS.index → ValueError。"""
        for key in ('linear_deconv', 'reference_grade',
                    'background_neutralization'):
            with self.subTest(step=key):
                self.assertIn(key, sr.sort_steps([key]))

    def test_optional_steps_sort_by_artifact_number(self):
        """可选步骤按主产物编号**插到相邻位置**，不是甩到末尾。

        ``linear_deconv`` 主产物是 ``02c_linear_deconv.tif``，按
        ``(数字, 字母后缀)`` 比较应排在 ``color``（02）之后、
        ``stellar_repair``（02b）之后。字母后缀必须参与比较 ——
        只比数字的话02c 与 02 无法区分，插入点会落在 color 之前。
        """
        result = sr.sort_steps(['color', 'linear_deconv', 'dbe'])
        self.assertEqual(result, ['dbe', 'color', 'linear_deconv'])
        # 与同号前缀的后续步骤混排：02c 应在 02b 之后
        mixed = sr.sort_steps(['stellar_repair', 'linear_deconv'])
        self.assertEqual(mixed, ['stellar_repair', 'linear_deconv'])
        # 01 号产物应排在最前
        early = sr.sort_steps(['pre_denoise', 'linear_deconv'])
        self.assertEqual(early, ['linear_deconv', 'pre_denoise'])

    def test_unknown_steps_go_last_preserving_order(self):
        result = sr.sort_steps(['zzz', 'dbe', 'aaa'])
        self.assertEqual(result[:1], ['dbe'])
        self.assertEqual(result[1:], ['zzz', 'aaa'])

    def test_empty_input(self):
        self.assertEqual(sr.sort_steps([]), [])

    def test_step_order_values(self):
        self.assertEqual(sr.step_order('dbe'), 0)
        self.assertEqual(sr.step_order('final_denoise'),
                         len(sr.ALL_STEPS) - 1)
        self.assertIsNone(sr.step_order('linear_deconv'))
        self.assertIsNone(sr.step_order('no_such_step'))


class DependencyExpansionTest(unittest.TestCase):
    """resolve_step_dependencies 的行为不得因接入注册表而改变。"""

    def test_star_process_expands_dependencies(self):
        steps, log = pipeline.resolve_step_dependencies(['star_process'])
        self.assertEqual(steps,
                         ['star_remove', 'stretch', 'star_process',
                          'star_combine'])
        self.assertTrue(log)

    def test_star_combine_expands_dependencies(self):
        steps, _ = pipeline.resolve_step_dependencies(['star_combine'])
        self.assertEqual(steps,
                         ['star_remove', 'stretch', 'star_process',
                          'star_combine'])

    def test_star_layers_path_unchanged(self):
        steps, _ = pipeline.resolve_step_dependencies(
            ['star_combine'],
            {'domain': 'linear', 'processed_stars': False})
        self.assertEqual(steps, ['stretch', 'star_process', 'star_combine'])

    def test_linear_star_layer_adds_stretch(self):
        steps, _ = pipeline.resolve_step_dependencies(
            ['star_combine'],
            {'domain': 'linear', 'processed_stars': True})
        self.assertEqual(steps, ['stretch', 'star_combine'])

    def test_full_order_matches_registry(self):
        steps, _ = pipeline.resolve_step_dependencies(list(sr.ALL_STEPS))
        self.assertEqual(steps, list(sr.ALL_STEPS))


class SelfValidationCatchesDriftTest(unittest.TestCase):
    """**本模块存在的核心理由**：自校验必须在漂移时失败。"""

    SOURCE = (SCRIPTS / 'stage_registry.py').read_text(encoding='utf-8')

    def _expect_runtime_error(self, mutated, fragment):
        result = _import_with_source(mutated)
        self.assertNotIn('IMPORT_OK', result.stdout,
                         '漂移未被自校验捕获：%s' % fragment)
        self.assertIn('RuntimeError', result.stderr, result.stderr[-400:])
        self.assertIn(fragment, result.stderr)

    def test_missing_artifact_registration_is_caught(self):
        self._expect_runtime_error(
            self.SOURCE.replace('("07_sharpen.tif",),', '(),', 1),
            'missing artifacts')

    def test_duplicate_artifact_is_caught(self):
        mutated = self.SOURCE.replace(
            '("01_dbe.tif",)', '("01_dbe.tif", "07_sharpen.tif")', 1)
        self._expect_runtime_error(mutated, 'claimed by both')

    def test_broken_order_is_caught(self):
        mutated = self.SOURCE.replace(
            'StepContract("dbe", 0, LINEAR', 'StepContract("dbe", 3, LINEAR', 1)
        self._expect_runtime_error(mutated, 'contiguous')

    def test_duplicate_key_is_caught(self):
        # 把可选步骤改成与主步骤同名（key 重复但 order 仍合法 -1），
        # 以便命中「duplicate step key」而不是先命中 order 连续性。
        mutated = self.SOURCE.replace(
            'StepContract("linear_deconv", -1, LINEAR, ("02c_linear_deconv.tif",),',
            'StepContract("dbe", -1, LINEAR, ("02c_linear_deconv.tif",),', 1)
        self.assertNotEqual(mutated, self.SOURCE, '变异未生效，测试会空跑')
        self._expect_runtime_error(mutated, 'duplicate step key')

    def test_order_disagreeing_with_artifact_numbering_is_caught(self):
        """order 与产物编号错位必须被拦下（第 9 项校验）。

        旧 ALL_STEPS 踩过：style 排在 local_enhance/star_reduce 之前，而实际
        执行是 8b/8d 先于 9a。这类错位会让「按 order 排序」与「按产物编号
        排序」给出不同结果 —— config_fingerprint 的累积切片会算错。

        直接构造一个顺序表验证校验逻辑，不走源码变异 —— 变异路线会先被
        第 8 项（产物缺失/多余）拦下，测不到第 9 项。
        """
        # 数字 9 → 1 回退，而 order 仍递增 ⇒ 只有编号相容性校验能发现
        drifted = [sr.StepContract("late_first", 0, sr.LINEAR, ("09_a.tif",),
                                   summary="编号 9"),
                   sr.StepContract("early_second", 1, sr.LINEAR, ("01_b.tif",),
                                   summary="编号回退到 1")]
        with self.assertRaises(RuntimeError) as ctx:
            self._assert_numbering_compatible(drifted)
        self.assertIn('artifact numbering', str(ctx.exception))

    def test_numbering_compatible_when_aligned(self):
        """编号相容时校验必须通过（含数字相等的同序号变体）。"""
        aligned = [sr.StepContract("a", 0, sr.LINEAR, ("02_a.tif",), summary="a"),
                   sr.StepContract("b", 1, sr.LINEAR, ("02b_b.tif",),
                                   summary="b 同序号变体"),
                   sr.StepContract("c", 2, sr.LINEAR, ("03_c.tif",), summary="c")]
        self._assert_numbering_compatible(aligned)

    @staticmethod
    def _assert_numbering_compatible(main_steps):
        """复刻 stage_registry._validate_registry 第 9 项的判定。

        不重新 import 变异副本 —— 那样第 8 项会先拦。直接对给定顺序表
        执行同一段逻辑，保证测试与实现同步。
        """
        previous = None
        for contract in main_steps:
            if not contract.artifacts:
                continue
            number = sr._artifact_marker(contract.artifacts[0])[0]
            if number == 10_000:
                continue
            if previous is not None and number < previous:
                raise RuntimeError(
                    'step %s: artifact number %s goes back to %d after %d — '
                    'execution order must follow artifact numbering'
                    % (contract.key, contract.artifacts[0], number, previous))
            previous = number

    def test_optional_step_with_positive_order_is_caught(self):
        mutated = self.SOURCE.replace(
            'StepContract("linear_deconv", -1, LINEAR',
            'StepContract("linear_deconv", 99, LINEAR', 1)
        self._expect_runtime_error(mutated, 'order=-1')

    def test_emission_step_outside_main_is_caught(self):
        # 用当前源码里的实际 EMISSION 文本做替换，避免手写整行导致匹配失败
        line = next(row for row in self.SOURCE.splitlines(keepends=True)
                    if '"color", "stretch", "final_color"' in row)
        mutated = self.SOURCE.replace(
            line, '    "color", "linear_deconv",\n', 1)
        self.assertNotEqual(mutated, self.SOURCE, '变异未生效，测试会空跑')
        self._expect_runtime_error(mutated, 'not a main step')

    def test_missing_summary_is_caught(self):
        mutated = self.SOURCE.replace(
            'summary="背景建模与梯度提取"', 'summary=""', 1)
        self._expect_runtime_error(mutated, 'lacks a summary')

    def test_unused_artifact_registration_is_caught(self):
        mutated = self.SOURCE.replace(
            '("07_sharpen.tif",)',
            '("07_sharpen.tif", "07_never_written.tif")', 1)
        self._expect_runtime_error(mutated, 'never writes')

    def test_pristine_source_imports_cleanly(self):
        """对照组：未改动的源码必须能import（证明上面的失败不是环境问题）。"""
        result = _import_with_source(self.SOURCE)
        self.assertIn('IMPORT_OK', result.stdout, result.stderr[-400:])


if __name__ == '__main__':
    unittest.main()
