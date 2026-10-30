---
name: pws-stacking
description: |
  Partition-Weighted Stacking (PWS 分区加权叠加) — stack already-calibrated, already-aligned
  single-channel astrophotography frames into one linear Float32 master with the PWS method
  (D.Cikey), running fully headless with no GUI. Use when the user asks to 叠加/stack a set of
  deep-sky sub-frames (XISF/FITS), 用 PWS 叠加, 分区加权叠加, or wants a partition-weighted
  master that keeps detail-region resolution while getting inverse-variance SNR in the hazy
  regions. Do NOT use for camera RAW stacking, calibration (dark/flat/bias), or registration —
  those are prerequisites this skill verifies but never performs.
license: Proprietary
metadata:
  slug: pws-stacking
  version: "1.0.4"
  displayName: PWS Stacking
  summary: 无界面运行 PWS 分区加权叠加；开跑前强制预检已校准/已对齐前提。
  tags: "astronomy, astrophotography, stacking, fits, xisf, pws"
  homepage: https://github.com/MZqk/deepsky-skill
---

# PWS Stacking（分区加权叠加）

用 **Partition-Weighted Stacking（PWS，分区加权叠加）** 把一组**已校准、已对齐**的单通道
帧合成一幅线性 Float32 灰度成品。方法由 **D.Cikey** 提出，本 skill 封装其开源实现（MIT）。

**本 skill 全程不调用图形界面**：只导入引擎本体（`scripts/vendor/`），不导入 `main.py`
与 `ui/`（PySide6）。运行器在启动前后各断言一次 Qt 未被加载。

**命令路径**：从当前加载的 `SKILL.md` 所在目录取得技能根目录的绝对路径，并在每次执行命令的同一次 shell 调用中设置：

```bash
TASK_SKILL_DIR="/absolute/path/to/pws-stacking"
```

将示例值替换为技能的实际加载位置；所有脚本与依赖清单均通过 `TASK_SKILL_DIR` 定位。
执行时保留用户工作目录；`--photos`、`--out`、`--frames-dir`、`--json-out` 的相对路径均相对于该工作目录解析。

---

## 一、三条硬约束（违反即出错，务必先读）

### 约束 1：输入前提 fail-fast —— 帧未校准/未对齐时 PWS 会**静默产出废图**

PWS 假设输入**已校准**（暗/平/偏）且**已对齐**（旋转+平移）。引擎**不做**校准、**不做**
配准，也**不做**未对齐检测（论文 7.4 节明确把预处理排除在方法范围之外）。

**这不是"结果差一点"，而是"不报错地给你一张废图"。** 实测证据（本项目合成数据）：

| 输入 | 引擎退出码 | 逐帧 FWHM 中位 | 成品星点 FWHM |
| --- | --- | --- | --- |
| 已对齐 | 0 | 1.77~2.92 px | 2.35 px（改善 6.8%） |
| **未对齐** | **0** | **1.77~2.92 px（与正常完全一致）** | **9.81 px（劣化 317%）** |

未对齐时引擎**正常结束、日志正常、逐帧 FWHM 完全正常**，只有成品是废的。

**因此：适配层必须先验 FWHM 中位一致性再开跑。** 本 skill 的 `precheck.py` 就是这道闸门，
`pws_run.py` **默认强制执行**：预检未通过则**不调用引擎、不写入任何成品**。

> **实测警告——只看 FWHM 一致性是不够的。** 在 aligned / sub-pixel / misaligned 三组数据上，
> **逐帧 FWHM 极差完全相同（1.76×）**，而跨帧星点质心散布分别为 **0.017 / 0.285 / 5.061 px**。
> 即：**FWHM 一致性无法发现未对齐**。故 `precheck.py` 同时验两项：
> - **FWHM 中位一致性**（帧级清晰度可测性，必要健全性检查）；
> - **跨帧星点质心散布**（对齐的**决定性**判据，> 0.5 px 直接 FAIL）。

### 约束 2：A 是唯一需标定的参数（默认 20）；**B = 2 由逆方差最优性给出，不可改**

融合式（论文式 6）：`W[i,r] = C_i^(A·R_r) · S_i^(B·(1−R_r))`

- **B = 2 不是标定出来的，是推导出来的。** 加权平均 `μ̂ = Σ w̃_i D_i`（`Σ w̃_i = 1`）的方差为
  `Var(μ̂) = Σ w̃_i²σ_i²`；在约束下最小化得 `w̃_i ∝ 1/σ_i²`，此时 `Var(μ̂) = 1/Σσ_i⁻²`。
  又 `S_i ∝ 1/σ_i`，故 `S_i² ∝ 1/σ_i²`——**B = 2 时朦胧区权重恰为逆方差最优解**。
  所以 B 不需要标定，**除实验外不应修改**。`pws_run.py` 对 `--B ≠ 2` **直接拒绝**（需显式
  `--allow-B-override` 才放行，仅供实验）。
- **A 是唯一需经验标定者，默认 20。** R=1 处权重为 `C^A`，A 控制"细节区以多少信噪比换解析力"。
  论文用**同一组参数**（A=20、B=2、κ=3.5）在**八个差异极大的数据集**上完成全幅验证
  （NGC 7331 L/R/G/B、M81、NGC 6888 Hα、M15、LBN576 OIII；32~166 帧；120~900 s 混曝；
  宽带与窄带），成品星点 FWHM 相对单帧中位改善 **3.7%~43.8%**，天空背景噪声相对"无分区基线"
  仅上升 **1.4%~3.1%**。故 **A=20 是经验证的通用工作点，不是逐目标拟合值**——不要按目标去"调优"A。
- 调节方向：**调小 A** → 天空噪声增长更少、星点略软；**调大 A** → 星点更细，但天空噪声上升、
  细节区等效帧数下降。**改 A 必须先问用户**，不要自作主张偏离 20。

参数细节与完整推导见 [`references/pws-method.md`](references/pws-method.md) 第 2 节。

### 约束 3：临时帧落盘 —— `--frames-dir` 必须由 skill 显式指定到**用户确认过的路径**

166 帧全幅约需 **40 GiB** 临时空间（单帧 6388×9576×4 B ≈ 244 MiB）。帧数据超出内存时引擎会
落盘为 memmap，**若写到一半磁盘满，会失败在已读取上百帧之后**。

上游 `pws.pick_frames_dir` 在 `--frames-dir` 留空时会**自动选盘**（项目盘 → 系统 TEMP →
其余盘按余量降序，需 1.25× 余量）。**skill 不允许这个行为**：自动选盘可能写满用户不知情的
磁盘（甚至系统盘）。

- `pws_run.py` 把 `--frames-dir` 设为**必填**；不传则 argparse 直接报错退出（exit 2），
  **绝不**回落到自动选盘。
- **必须先把该路径报给用户确认**，再开跑。确认前先查余量：
  `python "$TASK_SKILL_DIR/scripts/precheck.py" --photos <帧目录> --frames-dir <路径>` 会校验
  `可用 ≥ 1.25 × 所需` 并在不足时 FAIL。
- 反复调参时加 `--keep-frames`，可省去重新落盘数十 GiB 的时间。

---

## 二、标准流程

### 步骤 0：确认输入与落盘路径（**先问用户，不要猜**）

需要向用户确认：

1. **帧目录**：已校准、已对齐的单通道 XISF/FITS，同一目录内不要混不同目标。
2. **输出目录**：不要放在帧目录里（否则成品会被下一轮当成帧读入）。**`pws_run.py` 与
   `precheck.py --out` 均强制拦截同目录情形**（exit 20），勿用 `--force` 越过。
3. **`--frames-dir` 落盘路径**：**必须让用户确认**（约束 3）。
4. **帧数**：`N ≳ 20` 时方法收益明显；个位数帧时清晰度差异有限（论文 3.4.1 节建议）。

### 步骤 1：预检（fail-fast，必做）

```bash
python "$TASK_SKILL_DIR/scripts/precheck.py" --photos <帧目录> --frames-dir <用户确认的落盘路径> \
    [--out <输出目录>]
```

stdout 是**纯 JSON**（供解析），人类可读日志在 stderr。退出码：`0` PASS / `10` WARN / `20` FAIL。

`precheck.py` 检查十项：输入存在、帧可读、形状一致、目标/滤镜不混装、星表足够、
**FWHM 中位一致性**、**跨帧星点质心散布（对齐）**、**帧内背景结构（校准）**、落盘容量、
输出目录不与素材目录重合（仅在给出 `--out` 时检查）。

- **FAIL（exit 20）→ 停下来，把 `blockers` 原样告诉用户，不要开跑。**
  典型阻断：帧未对齐（质心散布 > 0.5 px）、未校准（帧内背景结构 > 12%）、星表不足 5 颗、
  落盘空间不足、未指定 `--frames-dir`、`--out` 与 `--photos` 同目录。
- **WARN（exit 10）→ 可以继续，但必须把 `warnings` 告知用户**（如亚像素配准残余会让星点轻微增肥）。
- 大目录可加 `--sample N`（默认抽 24 帧）控制预检耗时。**注意两侧开关名不同**：
  单独跑预检用 `--all-frames`（`precheck.py`），由运行器代跑预检用 `--all-frames-precheck`
  （`pws_run.py`）。正式出图前建议全检。
- `--crop N` 时必须把同一个 `--crop` 传给预检，否则预检区域与引擎不一致。

> **抽样下「星表足够」是近似判断。** 对齐、校准、背景结构这三项基于**抽样帧的统计量**，
> 抽样是必要的成本控制，结论可靠。但「星表足够」判定的是**预检自己选出的参考帧**，
> 而引擎的参考帧是**全体帧中 σ 最小**的那一帧——它可能落在抽样之外。若该帧恰好星表
> 不足 5 颗，会出现**预检 PASS、引擎随后以「星表不足」拒绝**。这是唯一一处抽样可能
> 漏判的方向，出正式图前用 `--all-frames-precheck` 即可消除。

### 步骤 2：运行

```bash
python "$TASK_SKILL_DIR/scripts/pws_run.py" \
    --photos <帧目录> --out <输出目录> --frames-dir <用户确认的落盘路径> \
    --tag <成品名>
```

`pws_run.py` 会**再跑一次预检**（除非 `--skip-precheck`），然后才调用引擎。
stdout 为纯 JSON 结果摘要，引擎日志走 stderr。
参数解析成功后的入口校验错误以顶层 `error` 字段说明原因；指定 `--json-out` 时同步保存同一份 JSON。
归档路径的父目录须已存在。路径不可写、目标为目录或磁盘写满时，stdout 仍返回一份 JSON，保留原始错误或叠加结果，并增加 `json_out_error`；进程与顶层 `exit_code` 均为 `1`，stderr 输出诊断。归档失败后不会汇报运行成功。

退出码：`0` 成功 / `10` 预检 WARN 但已继续 / `20` 预检 FAIL 或参数错误 / `1` 引擎或 JSON 归档异常。
缺少必填参数、未知选项或参数类型错误由 argparse 以 `2` 退出，仅在 stderr 输出诊断，不返回 JSON；`--help` 输出帮助文本。

### 步骤 3：汇报结果

从 JSON 的 `result` 取关键读数向用户汇报：

| 字段 | 含义 |
| --- | --- |
| `fwhm_med_px` | 单帧星点 FWHM 中位 |
| `fwhm_out_px` | 成品星点 FWHM（同星表同口径，可直接比） |
| `fwhm_improve_pct` | 改善百分比（正数为变细） |
| `r_info.R_gt50` | 细节区占比（论文实测 0.17%~0.32%，量级相符即正常） |
| `neff_median` | 等效帧数中位 |
| `rej_rate` | 排异剔除率（论文实测约 0.9%~1.5%） |
| `outputs.*` | 四个落盘文件的绝对路径 |

**成品是线性数据，未拉伸**，必须交给后续工具拉伸后才能看——不要直接说"图很好看"，
也不要用看图工具判断质量（线性数据在屏幕上接近全黑）。量纲与素材一致，可直接与其他软件
在同一数据上的成品对比。

---

## 三、输出文件（四件套，`<tag>` = 成品名）

| 文件 | 内容 |
| --- | --- |
| `stack_<tag>.xisf` | 成品：线性 Float32 灰度，全程只有线性运算，无拉伸/归一/截断 |
| `weights_<tag>.txt` | 逐帧权重表：`idx fwhm_px C_sharp sigma S_snr` |
| `R_<tag>.npy` | 分区权重场 R（Float32，与成品同尺寸） |
| `neff_<tag>.npy` | 等效帧数场（每像素实际用上多少帧） |

成品文件头 `STACKMODE` 记录本次参数（如 `PWS A=20 B=2 rej_k=3.5`）。

---

## 四、参数速查（默认值即论文工作点，**不要随意改**）

| 参数 | 默认 | 何时才动 |
| --- | --- | --- |
| `--A` | `20` | **唯一需标定者**。改前先问用户；论文八数据集共用 20 |
| `--B` | `2` | **不可改**（逆方差最优性）。运行器直接拒绝非 2 值 |
| `--rej-k` | `3.5` | 排异强度。0 = 关闭。低于 2 会成批剔除清洁帧的噪声尾 |
| `--limit` | `0` | >0 只取前 N 帧，**仅用于小样本试参**；正式出图用 0 |
| `--crop` | `0` | >0 只叠中间 N×N，**仅用于快速试参**；成品非完整画面 |
| `--frames-dir` | **必填** | 见约束 3 |
| `--keep-frames` | 关 | 反复调参时开，省去重新落盘数十 GiB |
| `--n-star` | `120` | 星点稀少视场可调小；中位数 100 颗以上已稳定 |
| `--r-floor` | `0` | 增大可压浅星周"外圈亮环"，代价是天空等效帧数下降 |
| `--env-eps` | `8` | 增大把空白天区 R 压得更低、天空噪声增长更少 |
| `--env-win-mult` | `2` | 包络窗 = 该值 × 2σ_psf；增大更宽容，减小更贴合星点 |
| `--env-p` | `1` | >1 收窄支撑，<1 展宽 |
| `--rej-gate` | `1/3` | 少数派闸门，保护多数帧共有的真实结构 |
| `--center` | 空 | 裁剪中心 `y,x`；留空则**自动定位延展源**，一般不要写死坐标 |
| `--tag` | `stack` | 成品名后缀。**留空回落到 `stack`，不会自动跟随素材目录名**（上游参数表的「随目录名跟随」描述在实现中不存在） |
| `--json-out` | 空 | 把结果 JSON 另存到该路径，便于归档与复查 |

完整参数语义与推导见 [`references/pws-method.md`](references/pws-method.md)；
上游参数表见 `scripts/vendor/pws_params.py`。

### 仅 `precheck.py` 可用

这些开关只在单独运行预检时存在，`pws_run.py` 侧的对应物见下表（**名称不完全相同**）：

| 开关 | 默认 | 说明 |
| --- | --- | --- |
| `--sample N` | `24` | 抽样帧数；`pws_run.py` 侧名为 `--precheck-sample` |
| `--all-frames` | 关 | 全检；`pws_run.py` 侧名为 `--all-frames-precheck` |
| `--out` | 空 | 成品输出目录，仅用于校验其不与 `--photos` 同目录 |
| `--pretty` | 关 | stdout 缩进输出，供**人眼**阅读 |

> `--pretty` 只改变 JSON 的**空白缩进**，不改变任何字段与取值——两种输出解析结果完全相同。
> agent 解析结果时**不需要**加它（`json.loads` 两者皆可）；仅当你要把 stdout 直接粘给人看时才有意义。

### 预检与安全开关（`pws_run.py`）

| 开关 | 默认 | 说明 |
| --- | --- | --- |
| `--precheck-sample N` | `24` | 预检抽样帧数；与 `precheck.py --sample` 同一参数 |
| `--all-frames-precheck` | 关 | 预检全检，消除抽样对「星表足够」的近似 |
| `--skip-precheck` | 关 | **危险**：跳过预检。未校准/未对齐时会静默产出废图 |
| `--force` | 关 | 预检 FAIL 仍继续。成品可能是废图 |
| `--allow-B-override` | 关 | 允许 B ≠ 2。**仅实验用途**，结果不再具备逆方差最优性 |

后三项须用户明确要求才可使用（见第五节「危险开关」）。

---

## 五、环境与故障排查

**依赖**：`numpy` / `scipy` / `astropy` / `photutils`（必需，`photutils` 用于星点检测与 PSF
拟合）；`psutil`（可选，探测可用内存以判断是否落盘）。

```bash
python -m pip install -r "$TASK_SKILL_DIR/requirements.txt"  # Headless dependencies; no PySide6
```

`requirements.txt` 是 skill 自己的依赖清单，**不含 PySide6**（本 skill 全程不碰 GUI，
无需 Qt）。`scripts/vendor/requirements.txt` 是上游原样带入的清单，其中列有 PySide6——那是
图形界面所需，**不要照它装**。

`precheck.py` 会先查 `photutils`，缺失时直接 FAIL 并提示安装。

| 现象 | 原因与处置 |
| --- | --- |
| `[拒绝] 必须显式指定 --frames-dir` | 约束 3，向用户要路径。`--frames-dir` 是 argparse 必填项，缺失时**直接以 exit 2 退出**，不会走到运行器内部 |
| `[拒绝] 输出目录与素材目录相同` | 成品 `stack_<tag>.xisf` 会被下一轮 `glob('*.xisf')` 当成帧读入，静默产出废图。为 `--out` 换独立目录 |
| `[拒绝] B=... 被修改` | 约束 2，B 不可改 |
| 预检 FAIL：帧未对齐 | 质心散布 > 0.5 px。让用户先做星点配准（旋转+平移），**不要**用 `--force` 硬跑 |
| 预检 FAIL：帧未校准 | 帧内背景结构 > 12%（渐晕/光害梯度残留）。让用户先做平场/暗场校准 |
| 预检 FAIL：星表不足 5 颗 | 视场缺乏亮星（窄带极暗场/纯星云特写）。论文 7.4 节指出需改用其他清晰度判据 |
| 预检 FAIL：落盘空间不足 | 换更大的盘，或先 `--crop` 小样本试参 |
| 预检 WARN：亚像素残余 | 可继续，但成品星点会轻微增肥，须告知用户 |
| 引擎报"星表不足 5 颗" | 预检本应拦住；若仍出现，说明 `--n-star` 或 `--crop` 与预检不一致 |

**危险开关**（需用户明确要求，默认关闭）：

- `--skip-precheck`：跳过预检。**未校准/未对齐时会静默产出废图。**
- `--force`：预检 FAIL 仍继续。成品可能是废图。
- `--allow-B-override`：允许 B ≠ 2。**仅实验用途**，结果不再具备逆方差最优性。

---

## 六、目录结构

```
SKILL.md                    本文件
CHANGELOG.md                独立版本变更记录
RELEASING.md                发布流程与发布包边界
requirements.txt            运行时依赖清单（**不含 PySide6**）
requirements-dev.txt        开发依赖（-r requirements.txt + pytest + PyYAML）
references/pws-method.md    论文要点 + A/B 标定依据（参数语义与推导的唯一事实源）
scripts/pws_run.py          无界面运行器（唯一执行入口，强制预检 + 强制 frames-dir + 锁 B=2）
scripts/precheck.py         fail-fast 预检（对齐 / 校准 / FWHM 一致性 / 落盘容量）
scripts/extract.py          从上游仓库提取源码到 vendor/（幂等，--check 可验漂移）
scripts/VENDOR.json         提取来源与各文件 SHA256（由 extract.py 写入，勿手改）
tests/test_precheck.py      输入前提闸门的失败契约回归测试
scripts/vendor/             上游引擎源码（逐位原样提取，未做任何改动）
  pws.py                    引擎本体（含日志/进度/取消三钩子）
  pws_params.py             参数定义（PwsParams / PARAMS），引擎与 CLI 共用
  DWT_DetailCore.py         帧读取（FITS/XISF）与延展源定位
  xisf_io.py                XISF / FITS 读写
  LICENSE / THIRD_PARTY_LICENSES.md / requirements.txt   上游原样带入
```

**关于 `vendor/`：源码是提取的，不是改写的。** 上游 `core/pws.py` 以顶层模块方式导入
`pws_params` / `DWT_DetailCore` / `xisf_io`，并用 `Path(__file__).parent.parent` 推导仓库根；
把四个文件**平铺**进同一目录后，这些导入与路径推导**自然成立**，故 `vendor/` 与上游**逐位相同**
（`python "$TASK_SKILL_DIR/scripts/extract.py" --check` 可验证）。

**本 skill 可整体搬移**（例如暂存到 `~/dev/skill/pws-stacking`）而**无需改动任何代码**：
`vendor/` 是自包含的，`pws_run.py` / `precheck.py` 只依赖同目录的 `vendor/`。
搬离仓库后 `extract.py` 无法再从路径推导上游，此时用 `--src` 指定，或依赖
`scripts/VENDOR.json` 里记录的来源（自动探测顺序：向上找仓库根 → `VENDOR.json` → 已知候选路径）。

> `VENDOR.json` 的 `src` 是**构建者的本机绝对路径**，在他人机器上必然失效——它只用于
> 本机做漂移核对。搬移或分发后该字段应视为历史记录；`extract.py` 的候选路径与 `--src`
> 才是可移植的定位方式。文件哈希部分与路径无关，始终有效。

**为什么不调 `python main.py --cli`**：`main.py` 在没有 `--cli` 时会 `from app import main`
并创建 QApplication——GUI 与引擎耦合在同一入口。本 skill 只导入引擎本体，从根上排除拉起界面的
可能，并在运行前后断言 Qt 未被加载。

---

## 七、许可与署名

**双重许可，互不混淆**：本 Skill 的封装层（`SKILL.md`、本仓库脚本）按
[`LICENSE.md`](LICENSE.md) 的专有许可发布；随包的**上游引擎源码保持 MIT 不变**
（`scripts/vendor/LICENSE`，`Copyright (c) 2026 D.Cikey`），`python "$TASK_SKILL_DIR/scripts/extract.py" --check` 可验证其逐位一致。

引擎与方法：**D.Cikey**。软件 MIT（`scripts/vendor/LICENSE`），方法论文 CC BY 4.0。
第三方组件声明见 `scripts/vendor/THIRD_PARTY_LICENSES.md`。

向用户交付成果时，请一并保留署名：

> 本项目的方法基于 **D.Cikey** 提出的**分区权重叠加法（Partition-Weighted
> Stacking，PWS）**，原文见 <https://github.com/DDCikey/Partition-Weighted-Stacking/tree/main/docs>；
> PDF 版可在微信公众号「小丁的星空」下载。
