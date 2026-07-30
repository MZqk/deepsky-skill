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
    @patch("shutil.which")
    @patch("os.path.isfile")
    @patch.dict(os.environ, {}, clear=True)
    def test_find_starnet_executable(self, mock_isfile, mock_which):
        # 1. 显式传入 user_path 且存在时，应该直接返回
        mock_isfile.return_value = True
        path = star_tools.find_starnet_executable(user_path="/custom/starnet++")
        self.assertEqual(path, "/custom/starnet++")

        # 2. 从环境变量读取
        mock_isfile.reset_mock()
        with patch.dict(os.environ, {"STARNET_PATH": "/env/starnet++"}):
            path = star_tools.find_starnet_executable()
            self.assertEqual(path, "/env/starnet++")

        # 3. 从系统 PATH 检索
        mock_isfile.return_value = False
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
    def test_run_starnet_retries_legacy_format(self, mock_run):
        failed = MagicMock(returncode=2, stderr="unknown option -i", stdout="")
        succeeded = MagicMock(returncode=0, stderr="", stdout="")
        mock_run.side_effect = [
            MagicMock(returncode=0),  # chmod
            failed,
            succeeded,
        ]
        image = np.zeros((16, 16, 3), dtype=np.float32)
        output = np.zeros((16, 16, 3), dtype=np.uint16)

        with patch("skimage.io.imsave"), \
             patch("cv2.imread", return_value=output):
            success, _starless, report = star_tools.run_starnet_cli(
                image,
                "/usr/local/bin/starnet2",
                return_report=True,
            )

        self.assertTrue(success)
        self.assertEqual(len(report["attempts"]), 2)
        self.assertEqual(report["attempts"][0]["command_format"], "flags")
        self.assertEqual(report["attempts"][1]["command_format"], "legacy")

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

if __name__ == "__main__":
    unittest.main()
