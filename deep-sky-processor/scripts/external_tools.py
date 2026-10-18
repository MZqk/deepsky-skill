#!/usr/bin/env python3
"""外部可执行工具的跨平台定位（统一探测器）。

**为什么需要这个模块**：deep-sky-processor 要调用三个外部 CLI ——
StarNet2（去星）、DeepSNR（AI 降噪）、solve-field（astrometry.net 天解算）。
三处各写一份探测逻辑时出现过一个真实缺陷：StarNet 侧有默认安装路径兜底，
DeepSNR 侧只依赖 ``shutil.which``，于是同一台机器、同一时刻 StarNet 能被发现
而 DeepSNR 报「executable not found」并**静默降级**（2026-10-03 C50 验收）。
本模块把三处统一，并把平台安装目录收敛到一张表。

**为什么必须有默认安装路径兜底**：GUI / IDE / 宿主应用启动的进程常常**不继承**
``/usr/local/bin`` 与 ``/opt/homebrew/bin``（实测 WorkBuddy 的 PATH 前 10 项全是
应用自身的 shim 与托管运行时）。此时 ``shutil.which('deepsnr')`` 与
``shutil.which('starnet2')`` **都返回 None**，而二进制明明装着。
注意 shell 内建的 ``where``/``which`` 能找到 —— 它们解析自己的路径配置，
与 ``os.environ['PATH']`` 无关，所以「终端里能跑」不能证明 Python 也能发现。

优先级链（四级，与直觉顺序一致，逐级收紧）：

  1. **显式路径** —— 调用方给的 ``user_path``。指定了却不��在 ⇒ **返回 None
     不静默回退**：否则用户以为指定了 A 工具、实际跑的是 B 工具，产物里的
     executable 记录与预期不符，这类「静默换工具」比直接报错更难查。
  2. **环境变量** —— ``<ENV_PREFIX>_PATH``，可为目录或文件。
     目录时在其中按候选文件名查找。无效时**继续往下**（环境变量是最弱的
     显式意图，不该一票否决）。
  3. **系统 PATH** —— ``shutil.which``，附带 Windows 的 ``PATHEXT`` 语义。
  4. **平台安装目录 + 脚本同目录** —— 见 ``INSTALL_DIRS`` 与 ``_script_dir_candidates``。

只读、无副作用、不写环境变量，符合「零额外依赖、纯标准库」承诺。
"""

from __future__ import annotations

import os
import shutil
import sys


# 各平台的默认安装目录。**只列「装完就得到可执行文件」的位置**，
# 不含源码目录与文档目录。
#
# Windows 的条目依据实际安装形态：DeepSNR 与 StarNet2 的官方包会把 CLI 放在
# ``C:\\Program Files\\<Product>\\`` 或用户目录下的同名文件夹；astrometry.net
# 的 Windows 安装器默认落在 ``C:\\Program Files\\astrometry\\``。
INSTALL_DIRS = {
    "deepsnr": {
        "darwin": [
            "/usr/local/bin/deepsnr",
            "/opt/homebrew/bin/deepsnr",
            "~/Applications/DeepSNR/deepsnr",
            "~/Applications/DeepSNR/DeepSNR",
            "/Applications/DeepSNR/deepsnr",
        ],
        "linux": [
            "/usr/local/bin/deepsnr",
            "/opt/homebrew/bin/deepsnr",
            "~/.local/bin/deepsnr",
            "~/bin/deepsnr",
        ],
        "win32": [
            r"C:\Program Files\DeepSNR\deepsnr.exe",
            r"C:\Program Files\DeepSNR\DeepSNR.exe",
            r"C:\Program Files (x86)\DeepSNR\deepsnr.exe",
            "~/AppData/Local/Programs/DeepSNR/deepsnr.exe",
            "~/AppData/Local/DeepSNR/deepsnr.exe",
        ],
    },
    "starnet": {
        "darwin": [
            "/usr/local/bin/starnet2",
            "/opt/homebrew/bin/starnet2",
            "/opt/homebrew/bin/starnet++",
            "/usr/local/bin/starnet++",
            "~/Applications/StarNet2/starnet2",
            "~/Applications/StarNet2/starnet++",
            "~/Applications/StarNet/starnet2",
            "/Applications/StarNet2/starnet++",
            "/Applications/StarNet2/StarNet2.app/Contents/MacOS/starnet2",
            "/Applications/StarNet/starnet2",
        ],
        "linux": [
            "/usr/local/bin/starnet++",
            "/usr/local/bin/starnet2",
            "~/.local/bin/starnet++",
            "~/bin/starnet++",
        ],
        "win32": [
            r"C:\Program Files\StarNet2\starnet2.exe",
            r"C:\Program Files\StarNet2\starnet++.exe",
            r"C:\Program Files\StarNet2\starnet.exe",
            r"C:\Program Files (x86)\StarNet2\starnet++.exe",
            r"C:\Program Files\StarNet2\bin\starnet2.exe",
            "~/AppData/Local/Programs/StarNet2/starnet2.exe",
        ],
    },
    "solve-field": {
        "darwin": [
            "/usr/local/bin/solve-field",
            "/opt/homebrew/bin/solve-field",
            "~/.local/bin/solve-field",
        ],
        "linux": [
            "/usr/local/bin/solve-field",
            "/usr/bin/solve-field",
            "~/.local/bin/solve-field",
        ],
        "win32": [
            r"C:\Program Files\astrometry\bin\solve-field.exe",
            r"C:\Program Files\astrometry\solve-field.exe",
            r"C:\Program Files (x86)\astrometry\bin\solve-field.exe",
            r"C:\astrometry\bin\solve-field.exe",
            # 写成正斜杠：Windows 的 os.path 两者都接受，且避免 "\b" 被
            # Python 当成退格转义（\b in str literal 是合法转义但语义错误）。
            "~/AppData/Local/astrometry/bin/solve-field.exe",
        ],
    },
}

# 每个工具在 PATH / 目录内查找时尝试的文件名（按优先级）。
BINARY_NAMES = {
    "deepsnr": ("deepsnr", "DeepSNR", "deepsnr.exe", "DeepSNR.exe"),
    "starnet": ("starnet2", "starnet++", "starnet2.exe", "starnet++.exe",
                "starnet.exe"),
    "solve-field": ("solve-field", "solve-field.exe"),
}

# 各工具的环境变量名。
ENV_VARS = {
    "deepsnr": "DEEPSNR_PATH",
    "starnet": "STARNET_PATH",
    "solve-field": "SOLVE_FIELD_PATH",
}


def current_platform():
    """返回 ``darwin`` / ``linux`` / ``win32`` 之一。"""
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win") or os.name == "nt":
        return "win32"
    return "linux"


def _is_executable_file(path):
    """文件存在且可执行。Windows 上 ``os.access(X_OK)`` 语义不同，
    只判存在 + 是文件，避免误报。"""
    if not path or not os.path.isfile(path):
        return False
    if current_platform() == "win32":
        return True
    return os.access(path, os.X_OK)


def _normalize(path):
    """展开 ``~`` 并转绝对路径，但**不破坏 Windows 盘符/UNC 路径**。

    ``os.path.abspath`` 是平台相关的：在 POSIX 上它会给``C:\\Program Files\\x``
    加上当前工作目录前缀，变成 ``/Users/me/C:\\Program Files\\x``。本模块的
    路径表同时包含三平台的条目，所以在任何平台上都不能无条件用 ``abspath`` ——
    否则在 macOS 上处理 Windows 表项（或反过来）会得到永不命中的路径，
    而且是**静默** miss。

    规则：已带盘符（``C:``）、UNC 前缀（``\\\\``）或以分隔符开头的绝对路径
    原样返回，只做 ``~`` 展开。
    """
    expanded = os.path.expanduser(str(path))
    if _looks_absolute(expanded):
        return expanded
    return os.path.abspath(expanded)


def _looks_absolute(path):
    """跨平台的「绝对路径」判定（不依赖当前平台的 os.path 语义）。"""
    if path.startswith(("/", "\\")):
        return True
    # Windows 盘符，如 C:\ 或 C:/
    return len(path) >= 2 and path[1] == ":" and path[0].isalpha()


def _script_dir_candidates(tool):
    """脚本同目录下的候选（便携部署：把二进制放在 scripts/ 旁边）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    names = BINARY_NAMES.get(tool, ())
    out = []
    for name in names:
        out.append(os.path.join(here, name))
        out.append(os.path.join(here, "bin", name))
    return out


def find_executable(tool, user_path=None, env_var=None):
    """定位外部工具可执行文件，返回绝对路径或 None。

    参数
    ----
    tool : str
        ``INSTALL_DIRS`` / ``BINARY_NAMES`` / ``ENV_VARS`` 里的键名
        （``deepsnr`` / ``starnet`` / ``solve-field``）。
    user_path : str,可选
        显式路径。**指定了却不存在 ⇒ 直接返回 None，不静默回退。**
    env_var : str,可选
        覆盖该工具默认的环境变量名。

    返回
    ----
    str 或 None。返回的路径不保证可执行 —— 调用方仍需在调用时处理
    ``OSError`` / 非零退出码（本模块只负责「找得到」，不负责「跑得通」）。
    """
    if tool not in INSTALL_DIRS:
        raise KeyError("unknown external tool: %r" % (tool,))

    # 1. 显式路径：存在才用，不存在直接失败（不静默换工具）
    if user_path:
        expanded = _normalize(str(user_path))
        if _is_executable_file(expanded):
            return expanded
        return None

    names = BINARY_NAMES.get(tool, ())

    # 2. 环境变量：可为目录（在其中按候选名查找）或直接文件路径。
    #    无效时继续往下 —— 环境变量是最弱的显式意图，不一票否决。
    var = env_var or ENV_VARS.get(tool)
    if var and os.environ.get(var):
        raw = _normalize(os.environ[var])
        if os.path.isdir(raw):
            for name in names:
                candidate = os.path.join(raw, name)
                if _is_executable_file(candidate):
                    return candidate
        elif _is_executable_file(raw):
            return raw

    # 3. 系统 PATH。Windows 上 shutil.which 已按 PATHEXT 处理 .exe/.cmd/.bat，
    #    这里再按候选名逐个试是為了覆盖「只有 .exe 后缀」的打包方式。
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    # 4a. 平台默认安装目录
    platform = current_platform()
    for raw in INSTALL_DIRS[tool].get(platform, []):
        candidate = _normalize(raw)
        if _is_executable_file(candidate):
            return candidate

    # 4b. 脚本同目录（便携部署）
    for candidate in _script_dir_candidates(tool):
        if _is_executable_file(candidate):
            return candidate

    return None


def probe_environment(tool, user_path=None):
    """诊断用：报告每一级探测的结果，用于排障时定位「到底哪一级失效」。

    返回 dict::

        {
          "tool": "deepsnr",
          "resolved": "/usr/local/bin/deepsnr" or None,
          "platform": "darwin",
          "steps": [{"level": "explicit", "detail": ..., "hit": bool}, ...],
        }

    「为什么工具找不到」这个问题靠猜很难定位 —— PATH 缺失、安装位置 unusual、
    权限不足的表现都是 None。逐级展开能一眼看出断在哪一级。
    """
    steps = []
    names = BINARY_NAMES.get(tool, ())
    var = ENV_VARS.get(tool)

    if user_path:
        expanded = _normalize(str(user_path))
        hit = _is_executable_file(expanded)
        steps.append({"level": "explicit", "detail": expanded, "hit": hit})
        if not hit:
            return {"tool": tool, "resolved": None, "platform": current_platform(),
                    "steps": steps,
                    "note": "显式路径不存在 —— 已按契约停止，不回退到其他工具"}
    else:
        steps.append({"level": "explicit", "detail": None,
                      "hit": None, "skipped": True})

    env_value = os.environ.get(var) if var else None
    if env_value:
        raw = _normalize(env_value)
        if os.path.isdir(raw):
            hit = any(_is_executable_file(os.path.join(raw, name))
                      for name in names)
            detail = "dir:%s" % raw
        else:
            hit = _is_executable_file(raw)
            detail = "file:%s" % raw
        steps.append({"level": "env:%s" % var, "detail": detail, "hit": hit})
    else:
        steps.append({"level": "env:%s" % var, "detail": None,
                      "hit": None, "skipped": True})

    which_hits = [name for name in names if shutil.which(name)]
    steps.append({"level": "which", "detail": names, "hit": bool(which_hits),
                  "resolved": which_hits})

    platform = current_platform()
    install_hits = [
        _normalize(raw)
        for raw in INSTALL_DIRS[tool].get(platform, [])
        if _is_executable_file(_normalize(raw))
    ]
    steps.append({"level": "install_dirs:%s" % platform,
                  "detail": INSTALL_DIRS[tool].get(platform, []),
                  "hit": bool(install_hits), "resolved": install_hits})

    script_hits = [path for path in _script_dir_candidates(tool)
                   if _is_executable_file(path)]
    steps.append({"level": "script_dir",
                  "detail": os.path.dirname(os.path.abspath(__file__)),
                  "hit": bool(script_hits), "resolved": script_hits})

    return {
        "tool": tool,
        "resolved": find_executable(tool, user_path=user_path),
        "platform": platform,
        "steps": steps,
    }


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="外部工具探测诊断（列出每一级命中情况）")
    parser.add_argument("tool", nargs="?", default=None,
                        choices=sorted(INSTALL_DIRS),
                        help="要诊断的工具；省略则诊断全部")
    parser.add_argument("--path", default=None, help="显式路径（用于验证该级）")
    args = parser.parse_args()

    tools = [args.tool] if args.tool else sorted(INSTALL_DIRS)
    reports = [probe_environment(tool, user_path=args.path)
               for tool in tools]
    print(json.dumps(reports if len(reports) > 1 else reports[0],
                     ensure_ascii=False, indent=2))
    return 0 if any(r["resolved"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
