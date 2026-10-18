import unittest
from pathlib import Path
import sys
import os
import numpy as np
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import star_tools

class TestStarnetBridge(unittest.TestCase):
    @patch("external_tools.shutil.which")
    @patch("external_tools._is_executable_file")
    @patch.dict(os.environ, {}, clear=True)
    def test_find_starnet_executable(self, mock_isexec, mock_which):
        # 探测逻辑已统一到 external_tools.find_executable，判定「可执行」
        # 用的是 _is_executable_file（POSIX 上要求 os.access(X_OK)），故这里
        # mock 它而不是 os.path.isfile —— 后者为 True 时文件仍可能不可执行。
        #
        # 1. 显式传入 user_path 且存在时，应该直接返回
        mock_isexec.return_value = True
        path = star_tools.find_starnet_executable(user_path="/custom/starnet++")
        self.assertEqual(path, "/custom/starnet++")

        # 2. 从环境变量读取
        mock_isexec.reset_mock()
        with patch.dict(os.environ, {"STARNET_PATH": "/env/starnet++"}):
            path = star_tools.find_starnet_executable()
            self.assertEqual(path, "/env/starnet++")

        # 3. 从系统 PATH 检索
        mock_isexec.return_value = False
        mock_which.return_value = "/bin/starnet++"
        path = star_tools.find_starnet_executable()
        self.assertEqual(path, "/bin/starnet++")

    @patch("sys.platform", "darwin")
    @patch("platform.machine")
    @patch("subprocess.run")
    def test_run_starnet_cli_env_darwin_arm64(self, mock_run, mock_machine):
        # 测试在 macOS arm64 上的执行命令、环境变量和 xattr 隔离清除
        mock_machine.return_value = "arm64"
        mock_run.return_value = MagicMock(returncode=0)

        # 构造输入
        image = np.zeros((16, 16, 3), dtype=np.float32)

        # 运行
        with patch("skimage.io.imsave") as mock_imsave, \
             patch("cv2.imread", return_value=np.zeros((16, 16, 3), dtype=np.uint16)), \
             patch("skimage.io.imread", return_value=np.zeros((16, 16, 3), dtype=np.float32)):
            # 伪造 starnet 路径在 /Applications/StarNet/starnet2
            success, starless = star_tools.run_starnet_cli(image, "/Applications/StarNet/starnet2", stride=256)

        self.assertTrue(success)

        # 验证是否执行了 xattr 清除隔离和可执行权限
        # 应该会有 3 次 subprocess.run 调用：
        # 1. xattr -r -d com.apple.quarantine
        # 2. chmod +x
        # 3. starnet2 本身
        self.assertEqual(mock_run.call_count, 3)

        first_call = mock_run.call_args_list[0][0][0]
        self.assertEqual(first_call[:4], ["xattr", "-r", "-d", "com.apple.quarantine"])

        last_call_args, last_call_kwargs = mock_run.call_args_list[-1]
        self.assertEqual(last_call_args[0][0], "/Applications/StarNet/starnet2")

        # 验证 DYLD_LIBRARY_PATH 环境变量是否被正确包含
        env = last_call_kwargs.get("env", {})
        self.assertIn("DYLD_LIBRARY_PATH", env)
        self.assertIn("/Applications/StarNet", env["DYLD_LIBRARY_PATH"])

    @patch("sys.platform", "linux")
    @patch("subprocess.run")
    def test_run_starnet_cli_env_linux_amd64(self, mock_run):
        # 测试在 Linux 上的执行命令和 LD_LIBRARY_PATH 环境变量
        mock_run.return_value = MagicMock(returncode=0)
        image = np.zeros((16, 16, 3), dtype=np.float32)

        with patch("skimage.io.imsave"), \
             patch("cv2.imread", return_value=np.zeros((16, 16, 3), dtype=np.uint16)), \
             patch("skimage.io.imread", return_value=np.zeros((16, 16, 3), dtype=np.float32)):
            success, starless = star_tools.run_starnet_cli(image, "/usr/local/bin/starnet2", stride=256)

        self.assertTrue(success)

        # Linux 不需要 xattr，只有 chmod +x 和 starnet2，一共 2 次调用
        self.assertEqual(mock_run.call_count, 2)

        last_call_args, last_call_kwargs = mock_run.call_args_list[-1]
        self.assertEqual(last_call_args[0][0], "/usr/local/bin/starnet2")
        env = last_call_kwargs.get("env", {})
        self.assertIn("LD_LIBRARY_PATH", env)
        self.assertIn("/usr/local/bin", env["LD_LIBRARY_PATH"])

    @patch("sys.platform", "linux")
    @patch("subprocess.run")
    def test_run_starnet_does_not_retry_legacy_format(self, mock_run):
        """失败时**不得**回退到位置参数语法。

        v0.1.43 变更：实测 StarNet2 2.6.2 的位置参数语法已不存在
        （StarNet2 <in> <out> 128 → PARSE ERROR），保留回退只会让
        每次失败**再白跑一遍整帧**，且 legacy 的第 4 个位置参数在
        新 CLI 里语义完全不同（-n 是 unscreen 输出路径）。
        """
        failed = MagicMock(returncode=2, stderr="unknown option -i", stdout="")
        mock_run.side_effect = [
            MagicMock(returncode=0),  # chmod
            failed,
        ]
        image = np.zeros((16, 16, 3), dtype=np.float32)

        with patch("skimage.io.imsave"):
            success, _starless, report = star_tools.run_starnet_cli(
                image,
                "/usr/local/bin/starnet2",
                return_report=True,
            )

        self.assertFalse(success)
        self.assertEqual(len(report["attempts"]), 1,
                         "失败后不应再试一次 legacy 格式")
        self.assertEqual(report["attempts"][0]["command_format"], "flags")

    @patch("sys.platform", "linux")
    @patch("subprocess.run")
    def test_run_starnet_rejects_invalid_stride_without_spawning(self, mock_run):
        """非法 stride 必须在**启动子进程之前**被拒绝。

        StarNet2 自身也要等推理开始后才报错（实测 -s 3 / -s 0 / -s 513
        全部 rc=255），所以前置校验是唯一能避免整帧白跑的手段。
        """
        image = np.zeros((16, 16, 3), dtype=np.float32)
        for bad in (3, 0, 1, 513, 1024, -4):
            with self.subTest(stride=bad):
                mock_run.reset_mock()
                mock_run.side_effect = [MagicMock(returncode=0)]
                with self.assertRaises(ValueError):
                    star_tools.run_starnet_cli(
                        image, "/usr/local/bin/starnet2", stride=bad)
                # linux 下不应有任何子进程被拉起（darwin 才会走 xattr）。
                self.assertEqual(mock_run.call_count, 0)

    @patch("sys.platform", "linux")
    @patch("subprocess.run")
    def test_run_starnet_accepts_documented_stride_range(self, mock_run):
        """官方范围 2~512 的偶数都应被接受（不擅自加 2 的幂约束）。

        实测 -s 100（偶数但非 2 的幂）被 StarNet2 接受并正常出图，
        官方文档亦未要求 2 的幂。
        """
        image = np.zeros((16, 16, 3), dtype=np.float32)
        output = np.zeros((16, 16, 3), dtype=np.uint16)
        for good in (2, 64, 100, 128, 512):
            with self.subTest(stride=good):
                mock_run.reset_mock()
                mock_run.side_effect = [
                    MagicMock(returncode=0),  # chmod
                    MagicMock(returncode=0, stderr="", stdout=""),
                ]
                with patch("skimage.io.imsave"), \
                     patch("cv2.imread", return_value=output):
                    success, _out, report = star_tools.run_starnet_cli(
                        image, "/usr/local/bin/starnet2", stride=good,
                        return_report=True)
                self.assertTrue(success, "stride=%d 应被接受" % good)
                self.assertEqual(report["stride"], good)

    @patch("sys.platform", "linux")
    @patch("subprocess.run")
    def test_run_starnet_rejects_wrong_output_shape(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # chmod
            MagicMock(returncode=0, stderr="", stdout=""),
        ]
        image = np.zeros((16, 16, 3), dtype=np.float32)

        with patch("skimage.io.imsave"), \
             patch("cv2.imread", return_value=np.zeros((8, 8, 3), dtype=np.uint16)):
            success, starless, report = star_tools.run_starnet_cli(
                image,
                "/usr/local/bin/starnet2",
                return_report=True,
            )

        self.assertFalse(success)
        self.assertIsNone(starless)
        self.assertIn("输出形状", report["output_error"])

    @patch("star_tools.find_starnet_executable")
    def test_separate_stars_fallback_on_missing_cli(self, mock_find):
        # 模拟 starnet 未找到，应能优雅 fallback 到形态学去星
        mock_find.return_value = None
        image = np.zeros((16, 16, 3), dtype=np.float32)

        # 运行并请求报告
        starless, stars, mask, report = star_tools.separate_stars(
            image, method='starnet', return_report=True
        )

        # 检查是否成功 fallback
        self.assertTrue(report.get("fallback_applied"))
        self.assertEqual(report.get("fallback_reason"), "starnet_executable_not_found")
        self.assertEqual(starless.shape, image.shape)
        self.assertEqual(stars.shape, image.shape)


class TestBuiltinStarnetDomain(unittest.TestCase):
    """内置 --use-starnet 路径的载荷域与 stride 默认值。

    背景（实测）：极暗线性母版直接量化为 16-bit 时星云区 G 只有 23 counts，
    StarNet2 输出把 G 归零 99.4%。旧版内置路径因此被接受门拒绝
    （`nebula_damage_ratio` 0.5521 > 0.20）→ 回退形态学。改用 MTF 域后
    实测 damage 降到 **0.0**、score 0.996、accepted=True、不再回退 ——
    **不需要放宽接受门**。
    """

    def test_run_starnet_cli_defaults_to_mtf_domain_and_stride_128(self):
        import inspect
        sig = inspect.signature(star_tools.run_starnet_cli)
        self.assertEqual(sig.parameters["domain"].default, "mtf")
        self.assertEqual(sig.parameters["stride"].default, 128)
        self.assertIn("midtones", sig.parameters)

    def test_separate_stars_defaults_to_mtf_domain(self):
        import inspect
        sig = inspect.signature(star_tools.separate_stars)
        self.assertEqual(sig.parameters["starnet_domain"].default, "mtf")
        self.assertEqual(sig.parameters["starnet_stride"].default, 128)

    def test_execution_report_records_domain(self):
        """即使可执行文件不存在，报告也要带上 domain/stride 供排查。"""
        image = np.full((16, 16, 3), 0.01, np.float32)
        ok, starless, report = star_tools.run_starnet_cli(
            image, "/nonexistent/starnet2", stride=128,
            timeout=1, return_report=True, domain="mtf",
        )
        self.assertFalse(ok)
        self.assertEqual(report["domain"], "mtf")
        self.assertEqual(report["stride"], 128)

    def test_linear_domain_is_recorded_too(self):
        image = np.full((16, 16, 3), 0.01, np.float32)
        _ok, _sl, report = star_tools.run_starnet_cli(
            image, "/nonexistent/starnet2", stride=64,
            timeout=1, return_report=True, domain="linear",
        )
        self.assertEqual(report["domain"], "linear")
        self.assertEqual(report["stride"], 64)
        self.assertNotIn("midtones", report)   # linear 域不做 MTF

    def test_mtf_quantization_preserves_weak_channel_span(self):
        """核心依据：MTF 域下弱通道在 16-bit 里保留的级数远多于线性域。

        这是"接受门不再拒绝"的根因 —— 输出变健康了，不是门放宽了。
        """
        from stretch import derive_mtf_midtones, mtf_stretch

        rng = np.random.default_rng(4)
        yy, xx = np.mgrid[:120, :160]
        neb = np.exp(-(((yy - 66) ** 2 + (xx - 80) ** 2) / (2 * 28.0 ** 2)))
        img = np.full((120, 160, 3), 0.0004, np.float32)
        img += neb[..., None] * np.array([0.0030, 0.0011, 0.0009], np.float32)
        img = np.clip(img + rng.normal(0, 2e-5, img.shape), 0, 1).astype(np.float32)

        lum = img.mean(axis=2)
        mask = lum > np.percentile(lum, 60)
        m = derive_mtf_midtones(img)

        def span(arr):
            u = (np.clip(arr, 0, 1) * 65535.0).round().astype(np.uint16)
            reg = u[..., 1][mask].astype(np.int64)
            return int(np.percentile(reg, 99) - np.percentile(reg, 1))

        self.assertGreater(span(mtf_stretch(img, midtones=m)), span(img) * 3)


if __name__ == "__main__":
    unittest.main()
