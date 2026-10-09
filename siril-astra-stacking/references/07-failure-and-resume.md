# 07 · 失败语义与恢复

> **定位**：批处理场景下最糟的不是失败，而是失败留下看起来正常其实已损毁的产物。
> **加载时机**：处理失败退出码、排查半成品、实现 resume。
> **对应代码**：`scripts/astra_stack.py`（`run_pipeline` / `atomic_publish` / `stage_workdir` / `main`）

## 7.1 退出码

| 码 | 含义 | 触发条件 |
|---|---|---|
| 0 | 成功 | 三件套全部发布 |
| 1 | Siril 不可用 | 未找到 `siril-cli`，消息中列出已探测的路径 |
| 2 | 设备无法识别 | 签名表未命中，`report.device.evidence` 给出线索 |
| 3 | 校准帧缺失 | 传统设备缺 master dark，不可协商 |
| 4 | 无可用帧 | 少于 2 帧 |
| 5 | Siril 执行失败 | 任一阶段非 0 退出 |
| 6 | 输入格式不支持 | 目录内只有视频 / SER / 预览图 |
| 7 | 产物发布失败 | 原子 rename 失败 |
| — | 内部异常 | 捕获后写 `failed_stage: "internal"` + traceback 尾 12 行，**绝不返回 0** |

## 7.2 work 目录

```
<out_dir>/<target>.work/
├── <stem>0000.fit ... <stem>NNNN.fit    源帧的符号链接
├── .astra-state.json                   目标名、序列 stem、输入路径
├── c_<stem>NNNN.fit                     校准输出
├── d_c_<stem>NNNN.fit                   单独去拜耳输出（自动 CFA 无平场选择 debayer 时）
├── ap_c_<stem>NNNN.fit                  变换后序列；单独去拜耳时为 ap_d_c_<stem>
├── sk_ap_c_<stem>NNNN.fit               seqsubsky 输出（仅 --stack-engine=pws）
├── pws_in/pwsNNNN.fit                   PWS 引擎输入（灰度 float32，仅 pws）
├── pws_out/stack_astra.xisf 等           引擎四件套（仅 pws）
├── pws_frames/                          引擎临时帧落盘（仅 pws）
├── <stem>.seq  c_<stem>.seq             注册数据
├── astra_calibrate.sir / .log
├── astra_probe.sir / .log
├── astra_<target>.sir / .log
├── astra_preview.sir
└── <target>_stacked_32bit.fits          待发布母版
```

**为什么用符号链接**：不复制原始数据（可能几十 GB），且保证源文件不被修改。

**为什么每帧重命名**：Siril 把帧名尾部数字当序号（见 06），必须统一为 `<stem>NNNN.fit` 才能被识别为一个序列。

**为什么每次重建**：Siril 在目录有多个序列时会忽略请求的名字、只取第一个（见 06）。复用目录有选中错误序列的风险。`stage_workdir` 检测到非本工具创建的目录时会拒绝并提示 `--force`。

## 7.3 原子提交

```python
published = atomic_publish(work_dir, out_dir, target_name, artifacts)
```

实现要点：

1. 三件套全部在 work 目录内**生成完毕**后才调用。
2. `os.rename` 在 `out_dir` 内部完成——同一文件系统上的原子操作。
3. 目标已存在时先 `os.remove` 再 rename。
4. 任一环节抛错 → 抛出 `RuntimeError` → 退出码 7，已发布的文件保持不变。

含义：**上一份完好的母版要么完整保留，要么被新版本完整替换，绝不出现半写状态。**

`selftest.py` 有两项对应测试：正常发布、以及失败时旧产物不被触碰。

## 7.4 失败时的产物状态

| 情况 | 结果 |
|---|---|
| Siril 未安装 | 退出 1，输出目录只有 `_report.json` |
| 设备无法识别 | 退出 2，report 含 `device.evidence` |
| 校准缺失 | 退出 3，report 含 `calibration.kind` |
| probe 失败 | 退出 5，**work 目录保留**供排查 |
| stack 失败 | 退出 5，**work 目录保留** |
| 发布失败 | 退出 7，work 目录保留 |

**成功时 work 目录被删除**；失败时保留。这样失败后可以直接查看 `.log` 与中间 FITS。

## 7.5 report.json 始终写出

无论成功失败，`<out_dir>/<target>_report.json` 一定存在：

```json
{
  "status": "failed",
  "failed_stage": "probe-reg",
  "completed_stages": ["probe", "identify", "plan"],
  "error": "transform probe failed (exit 1); no registration data was produced, so the chain decision would be guesswork. Log: ...",
  "notes": ["siril: Not enough star pairs (7): Image 1 skipped"]
}
```

`failed_stage` 取值：`probe` / `identify` / `plan` / `calibrate` / `probe-reg` / `stack` / `preview` / `commit` / `internal`。

`completed_stages` 让调用方能精确知道进展到哪一步。

## 7.6 日志捕获

每个 Siril 调用都写独立日志：

| 日志 | 对应阶段 |
|---|---|
| `astra_calibrate.log` | 校准 |
| `probe.log` | transform 探测 |
| `siril.log` | 注册 + 变换 + 叠加 |
| `astra_preview.log` | 预览 |

`_parse_siril_log()` 从中提取值得注意的行（`Disabling` / `Cannot` / `not enough` / `partially succeeded` / `bad idea` 等标记），最多 12 条放入 `report.notes`。

这一步很重要：**Siril 的静默降级只出现在日志里**，不提取出来用户就看不到 median 禁用或 filter 空操作。

## 7.7 未捕获异常的兜底

```python
except Exception as exc:
    write report with failed_stage="internal" + traceback tail
    traceback.print_exc()
    return EXIT_SIRIL_FAILED
```

**未捕获异常绝不返回 0。** 早期版本曾因 `stage_workdir` 返回值数量不匹配而抛出 `ValueError`，shell 仍报 `EXIT=0`，造成"看起来成功"的假象。现在任何崩溃都会写入 report 并返回非 0。

## 7.8 `--resume` 的实际语义

```bash
python3 scripts/astra_stack.py /data/M42 --out ./results --resume
```

**注意**：`--resume` 目前不实现跨运行的断点复用，因此 `stage_workdir` 仍重建 work 目录并记录 warning。有效 `.seq` 注册矩阵可以在同次运行的不同 Siril 调用之间复用；真正限制是尚未实现跨运行的输入和处理状态校验，而不是矩阵与进程绑定。

真正有价值的 resume 场景是**失败后重新尝试**：已修复问题（比如换了校准帧、补充了序列）后重跑，此时旧 work 目录中的日志仍可用于对比。

**不要**把它当作断点续跑工具。

## 7.9 `--dry-run`

```bash
python3 scripts/astra_stack.py /data/M42 --out ./results --dry-run
```

行为：

- 完整走 probe → identify → plan
- 生成 work 目录与全部 `.sir` 脚本
- **不调用 Siril**
- 在 report 中记录 `registration.chain` 与 `stacking` 参数
- 退出码 0

用途：在没有 Siril 的环境里验证签名表与参数推导是否正确，或在真实运行前检查将要执行什么。

注意 dry-run 下链路决策**没有 probe 数据**，因此 `chain` 是基于 `transform_summary=None` 的保守判断（通常落到 debayer 或 rgb）。真实决策需要 `--force-chain` 或实际运行。

## 7.10 排查手册

| 症状 | 排查 |
|---|---|
| 退出 2 | 看 `report.device.evidence`，据此扩展签名表（见 01） |
| 退出 3 | 确认 `--calibration-dir` 下文件名含 `dark` / `masterdark`，且头部有 `CCD-TEMP`/`GAIN`/`EXPTIME` 且在容差内 |
| `Not enough star pairs` | 小视场数据在 profile 里调低 `min_pairs`；或检查绿通道信号是否过弱 |
| `identity matrices` | 检查是否漏了 `-2pass`（见 06） |
| `invalid input sequence` | 检查序列名是否以数字结尾、是否用了绝对路径、目录里是否有多个序列 |
| `drizzle weights are missing` | `-framing=max` 未伴随 `-drizzle`（生成器应已拦截） |
| 拼接被静默裁掉 | 检查是否用了 median 叠加（生成器应已拦截） |
| `PWS gate refused this run` | `report.stack_engine.reasons` 逐条给出闸门拒绝理由（帧数 / 离散度 / drizzle 链路），见 `references/08-pws-engine.md` |
| `PWS engine failed (runner exit ...)` | work 目录的 pws 运行器 stderr 已进 `report.notes`；`pws_out/` 与 `pws_run.log` 随 `--keep-work` 保留 |
| `pws-stacking skill not found` / 引擎依赖不可导入 | 安装相邻 pws-stacking skill 及其依赖，或 `--pws-engine` 显式指定路径；本 skill 据实拒绝而非降级 |
| 预览未生成 | `report.preview.produced` 为 false，母版不受影响；查 `astra_preview.log` |
| 视频 / JPEG 被拒 | 设备需导出逐帧 FITS，见 SKILL.md 铁律 2 |
| `Could not save the settings ... g_rename() failed` | macOS 受限环境的正常现象，不影响结果 |