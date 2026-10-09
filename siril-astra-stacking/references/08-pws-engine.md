# 08 · PWS 叠加引擎（--stack-engine=pws）

> **定位**：`stack` 一步的可选替换引擎。Siril 负责校准/配准/去拜耳/背景，PWS 负责最终权重融合。
> **加载时机**：用户要求 PWS / 分区权重叠加、询问闸门为何拒绝、排查 `stack_engine` 相关 report 字段时。
> **对应代码**：`scripts/pws_bridge.py`（闸门 / 交接 / XISF 转换）、`scripts/astra_stack.py`（`_run_pws_engine_stage` / `_invoke_pws_runner`）

## 8.1 方法与出处

**分区权重叠加法（Partition-Weighted Stacking，PWS）** 由 **D.Cikey** 提出：

> 本文/本项目的方法基于 **D.Cikey** 提出的**分区权重叠加法（Partition-Weighted Stacking，PWS）**，原文见 <https://github.com/DDCikey/Partition-Weighted-Stacking/tree/main/docs>；PDF 版可在微信公众号「小丁的星空」下载。

方法论文 CC BY 4.0，参考实现 MIT（由相邻 `pws-stacking` skill 逐位 vendor，本 skill 不复制其代码）。融合式：

```
W[i,r] = C_i^(A·R_r) · S_i^(B·(1−R_r))    逐像素归一后加权平均
```

- `C_i` 帧清晰度权重（帧级标量）；`S_i` 帧信噪比权重；`R_r` 逐像素分区场，1=细节型 0=朦胧型。
- `B=2` 由逆方差最优性直接给出，不标定；`A=20` 为论文八数据集共用工作点。
- 关键性质：朦胧区（占全画面 99%+ 像素）退化为逆方差加权，清晰度加权严格局限于星点与结构核心。

## 8.2 为什么是「替换 stack 一步」而不是前置或后置

Siril 1.4 的 `stack` 权重只有帧级标量（官方文档与源码 `compute_noise_weights` / `compute_wfwhm_weights` 均证实）：`-weight={noise|wfwhm|nbstars|nbstack}`。PWS 的 `W[i,r]` 是逐帧×逐像素的场，**在 Siril 内没有表达入口**。同时「在 Siril 之前给帧加权再交给 Siril 叠加」不存在中间态——权重必须在融合那一步生效。因此唯一干净的接缝是：

```
calibrate → register -2pass → seqapplyreg (+ seqsubsky)   ← Siril 前端（原七阶段 1–6 前半）
                                   ↓ ap_c_<stem> 或 ap_d_c_<stem>（已校准、已对齐）
                              PWS 引擎（子进程）           ← 替换 stack
                                   ↓ stack_astra.xisf
                        XISF→FITS 转换 → preview → commit  ← 后半不变
```

`seqsubsky` 的加入（实测新增）：PWS 引擎对每帧做加性天空平移（论文式 2），假设帧间天空一致且接近公共底。Siril debayer 后的帧带完整天空背景，先跑 `seqsubsky 1 -prefix=sk_` 把合成背景（1.4 只有 1–2 阶多项式/RBF，足以压平）减掉，引擎的天空平移才有正确着落。

## 8.3 闸门（evaluate_gate）

PWS 的收益与**帧间清晰度离散度**同步（论文表 3/4：C_i 跨 1.35× 的 M81 只改善 3.7%，跨 2.81× 的 NGC 6888 改善 43.8%）。闸门用 probe 阶段已有的 regdata 计算，零额外成本：

| 检查 | 阈值 | 依据 |
|---|---|---|
| 帧数 | `N ≥ 20` | 论文 3.4.1 节；个位数帧的清晰度差异无法表达 |
| 离散度 | wfwhm `p90/p10 > 1.4` | 论文实测：≤1.35× 时收益落在测量噪声内 |
| 链路 | 仅 debayer/RGB | drizzle 写出的每帧画布尺寸互不相同（实测 218×176/205×156/200×150 混存），引擎要求统一 shape |

**拒绝即退出（exit 5），绝不静默回落 Siril stack**——用户显式要求 PWS，回落等于伪造决策。拒绝理由逐条写入 `report.stack_engine.reasons`。

`report.frame_quality` 同时新增两个与引擎无关的字段：`weighted_fwhm_p90_over_p10` 与 `dispersion_note`（p90/p10 ≤ 1.4 时提示「帧质量过于一致，离散度驱动加权无利可图」）。默认引擎下这两个字段就是 PWS 可行性的预演。报告直接复用 `metrics.weighted_fwhm` 已排序的 p10、p90，比值保留四位小数，提示依据未舍入比值。对同一组正常正值样本，报告比值与闸门一致；PWS 闸门本身已按有效正值排序，修复报告不会改变准入规则。

## 8.4 交接的三个实测陷阱

### ① astropy 是 channels-first，引擎按 channels-last 折叠

引擎的 FITS 读取器对 3 轴数据执行 `data[..., :3].mean(axis=-1)`，而 astropy 返回 `(NAXIS3, NAXIS2, NAXIS1)` = 通道在最前。实测：Siril 去拜耳后的 200×150 RGB 帧被折叠成 `(3, 150)`——宽和高各被错误地平均，星点检测为零。

**vendor 不可以改**（`pws-stacking` 的 `extract.py --check` 保证逐位一致），所以桥接层自己折叠：`prepare_pws_input` 把每个注册帧重写为 **2 轴 float32 灰度 FITS**（通道取均值，FITS 大端直读直写，头里只留 `SIMPLE/BITPIX/NAXIS/NAXIS1/NAXIS2/BUNIT`）。亮度折叠也是论文单色相机工作流的正确口径——`C_i` 本就应在亮度上测量。

### ② XISF 数据是小端，FITS 是大端

引擎成品 `stack_astra.xisf` 的 attachment 是**小端** float32（实测：已知像素值的 LE 字节模式在文件中，BE 模式不存在），FITS 要求大端。`convert_xisf_master` 以 `<f` 解包、`>f` 重打包，并用真实引擎产物做像素级往返断言（maxerr=0）。

该转换器**只解析引擎实际写出的子集**：`XISF0100` 签名、单 `Image`、`Float32/Gray/Planar`、未压缩 `attachment:`。压缩、多图、cube 等一律报错并说明缺什么——桥接器不是通用 XISF 实现。

### ③ FITS 头 8 字符键长截断

`STACKMODE=PWS A=20 B=2 rej_k=3.5` 共 9 字符，FITS 固定格式只保留前 8 字符（`STACKMOD`）；且字符串值超 20 列会截断收尾引号导致解析器静默丢键（实测）。处理：值文本截到 18 字符内加引号，`STACKMOD` 键名截断在 SKILL.md 已知局限中声明，完整参数以 XISF 关键字与 `report.stacking` 为准。

## 8.5 调用契约（_invoke_pws_runner）

引擎以**子进程**调用（`sys.executable pws_run.py ...`），不 import：引擎的第三方栈永不进入本进程，且运行器的 precheck/JSON 契约是已审计接口。

```
python pws_run.py --photos <work>/pws_in --out <work>/pws_out \
    --frames-dir <work>/pws_frames --tag astra
```

- 退出码 0/10 视为成功；20/1 为失败，stderr 尾 8 行进 `report.notes`。
- `--frames-dir` 必须显式（引擎拒绝自动选盘，40 GiB 级落盘不允许静默选址）。
- 引擎自身 precheck 仍会全量运行（对齐/校准/星表/FWHM 一致性）：Siril 已保证前两项，星表不足（<5 颗）或帧内背景结构 >12% 的数据会被引擎侧拒绝——这是第二道独立防线，不是重复。

## 8.6 report 新增字段

```json
{
  "stack_engine": {
    "eligible": true, "verdict": "pass",
    "frame_count": 20, "wfwhm_p10": 4.39, "wfwhm_p90": 12.12,
    "dispersion_ratio": 2.15, "outlier_frames": 0,
    "reasons": ["..."], "input_dir": "...", "input_frame_count": 20
  },
  "stack_engine_run": {
    "command": ["python3", ".../pws_run.py", "..."],
    "exit_code": 0, "summary": { "result": { "A": 20.0, "B": 2.0, "..." : 0 } },
    "stderr_notes": ["pws: ..."],
    "master_conversion": { "width": 400, "height": 300,
                           "stackmode": [["STACKMODE", "PWS A=20 B=2 rej_k=3.5", "..."]] }
  },
  "stacking": {
    "method": "pws",
    "engine": "pws-stacking (Partition-Weighted Stacking, D.Cikey)",
    "A": 20.0, "B": 2.0, "frames_stacked": 20,
    "fwhm_median_px": 4.40, "fwhm_master_px": 5.23, "fwhm_improve_pct": -18.9,
    "neff_median": null, "rej_rate": 0.0, "outputs": { "stack": "..." },
    "notes": ["weights are per-frame PWS clarity/SNR weights; ..."]
  }
}
```

注意 `fwhm_improve_pct` 为**负**不一定是异常：引擎的 `fwhm_out` 用它自己的固定核（fwhm0=6.0）在成品上重测，而 PWS 成品的等效 PSF 由锐/糊帧混合决定。合成双峰数据（4.4 px 与 10.8 px 两组混叠）实测改善为 −18.9%，真实离散数据的解读见论文 6.3 节的核/尾双尺子。

## 8.7 端到端验证（selftest --real）

`test_real_pws_pipeline` 用 20 帧合成数据（10 锐 + 10 糊，`blur_per_frame` 双批）跑完整链路并断言：pipeline exit 0、闸门 pass、三件套齐、`stacking.method == "pws"`。合成场景同时满足引擎 precheck 的两条独立红线：≥5 颗孤立星（STARS 表已扩到 12 颗含远角星）、帧内背景结构 ≤12%（`vignetting=False`——未校准数据本就该在闸门前被拦）。

## 8.8 真实数据回归：81 帧 M8（2026-07-15，33° 旋转）测出的三处异常

用真实 Seestar M8 数据（`~/SeeStar/M 8_sub`，81 帧 20s IRCUT，`CCD-TEMP` 34.2→45.5°C 连续升温）做了双引擎对照，逐条排查出以下问题——**前两处是 1.0.0 就存在的潜在缺陷，第三处是本次 PWS 接线的边界条件**：

**① 探测与正式处理重复执行（按数据状态复用修复）。** 旧代码的 `calibrate_ran=True` 实际表示生成校准命令，调用点又在已校准后传入 True；旧文档称已反转该参数，但本次修复前实际代码仍重复处理。仅把调用点反转不能覆盖 RGB 探测后切换 CFA drizzle 的情况。现在直接控制是否包含校准以及注册数据是否可复用：数据状态不变时复用；无平场 CFA 选择 debayer 时仅去拜耳并重新注册；有平场且切换链路时保留必要的校准重建。PWS 交接接收实际 `registered_seq`，因此 `ap_d_c_<stem>` 与原有 `ap_c_<stem>` 均可处理。

**② 桥接灰度帧的 END 卡不合规（已修复）。** `pws_bridge.build_card` 的本地复制版缺 END 特判，写出 `END     = `（带 `= ` 与值字段），`data_offset` 识别不到 END → 黑角检测读不到数据（`sampled=0` 静默失效）。修复：删除本地复制，直接委托 `fits_probe.build_card`。

**③ 大旋转 + 固定画布 = 黑角污染引擎输入（闸门已补）。** `framing=current` 下 33° 旋转使帧角旋转出界，无数据黑区随帧序从 **3.5% 单调涨到 23.9%**。引擎没有 coverage 掩膜，其 precheck 的 `_bg_spread` 把黑块中值 0 当「帧内背景结构」，在黑角 ~20% 的尾段帧上算出 **107.2%（阈值 12%）** 并以「帧未校准」拒绝——**理由文本有误导性，但拒绝的决定是对的**（引擎前提是已配准且无画布缺口的帧，论文 7.4 节明确把这类处理排除在方法范围外）。M8 逐帧 `background_lvl` 从 0.147 单调滑到 0.075（透明度改善），而分块中值里 M8 延展云气占比随之飙升，进一步推高该误判比值。

据此新增**黑角闸门**（`check_black_corners`，阈值 10%）：桥接灰度化后抽样首/中/尾帧测非正像素占比，超阈值即以准确理由拒绝——「旋转序列在固定画布上携带三角缺口，PWS 引擎无法掩膜；请用默认 Siril stack（drizzle 链路原生处理旋转）或先裁剪到公共覆盖区」。实测 M8：黑角逐帧 3.5%→4.2%→11.0%→14.9%→17.9%→21.5%→23.5% 被完整记录，拒绝理由与 107% 误判不同因，果同源。合成 3.8° 序列（黑角 2.4%）不受影响，仍在 10% 线内正常走完引擎。

**结论**：M8 数据的 PWS 路径在闸门处以准确理由拒绝是**预期行为**；其默认 drizzle 链路（status ok，69/81 帧，77s，与已知局限记录一致）不受影响。黑角 ~20% 以内的温和旋转序列仍可走 PWS。
