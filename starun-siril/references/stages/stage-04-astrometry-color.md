# Stage 4 · 天体测量与色彩校准

## 换父源后的保护区绑定

新 `vivid_preservation_v1` 会话的 PCC/SPCC `# color-calibration:` 必须包含 `protection_regions`，指向 reports 中绑定当前父源的 `regions.v1`；只有 neutral 可用 `background_regions` 代替，PCC/SPCC 缺少 `protection_regions` 时在执行前静态拒绝。解算、背景处理或裁剪后重新绑定 SHA 与尺寸，不直接继承旧 SHA。为可辨红、蓝云气分别声明 faint/core 区域，有可信天空时另加背景区。

所有实际校色路线均记录前后 RGB／亮度、背景扣除后的颜色比例及逐通道端点。中性化使用已验证增益；PCC/SPCC 可将 Siril 实际 `channel_gains` 记录为三个正值，缺失时保留未知。增益修正后的差值仅供诊断，不自动判定信号损失，也不能据此接受退化。背景不可靠时颜色比例不可测。审查须在 `color_protection_assessment` 说明连续性、颜色、弱外缘及红／蓝区域适用性。

仅使用已接受的线性父源。已确认三通道 dualband-osc 默认先评估单位增益 native neutral，不需要 WCS 或星表；用户明确选择 SPCC/PCC 时才先评估 astrometry.solve 并核对其响应/滤镜证据。宽带按实际证据评估 SPCC、兼容 PCC、neutral 或跳过，响应不足不自动放行 PCC。方法独立，不叠加失败校正；回退均从同一已接受校色前父源开始。默认在线，显式 offline 只使用冻结本地星表；不接 Astrometry.net 或下载星表。

完整处理主动补齐可取得的证据并争取小收益，不以“可能不明显”提前结束本阶段。先读当前 Header、可信采集记录与已安装响应名单；证据不足时查询对应型号、实际滤镜与光路的厂家资料，保存实际 URL、读取日期、相关结论与仍缺项目。精确传感器/滤镜/LPF 响应是 SPCC 要求；PCC 使用有来源的标准 RGB 与 IR 阻挡兼容证据，不要求完整 SPCC 响应，也不能只凭 broadband/IRCUT 标签猜测兼容。缺 WCS 但坐标、采样与星表条件具备时，先运行并审查现有 astrometry.solve，不把可解算的缺项当作跳过理由。

路线证据齐全时实际运行一个候选；测光不可用时继续评估 native neutral。可信 fit/validation 天空存在且实测系数不是恒等时，即使偏移小于单像素噪声或全幅看似中性，也运行一次保守 neutral；空间/区域汇总偏移与单像素噪声不是同一统计量。微小但同源、可复核的独立天空或星色改善即可支持采用，不要求肉眼显著，也不另设最小收益阈值；仍检查颜色、主体、星点与科学完整性。无实际改善、完整恒等、天空不足或出现实质退化才保留父源，并记录已尝试的取证与实测依据。双窄带测光的显式用户选择要求不变；不得为了执行阶段编造响应、白参考或颜色真实性。

## 配色路线与校色状态

承接 [Stage 1 配色路线](stage-01-input-inspect.md#palette-route-selection)，按实际输入类型决定校色条件，不按期望显示颜色改变来源声明。宽带的自然 RGB 目标优先评估具备真实传感器/滤镜/光路响应的 SPCC；标准 RGB 与 IR 阻挡兼容证据充分时可用 PCC；否则评估适用的背景中性化或跳过。仅完成 neutral 不等于完成测光校准，审查与交付必须分别说明目标和实际校色状态。

路线按下列顺序逐级下行，每下行一级都必须写入 `route_history`：

| 级 | 方法 | 进入条件 | 禁止条件 | `route_history.status` |
|---|---|---|---|---|
| 1 | SPCC | 传感器/滤镜/LPF 响应与白参考均可溯源，且父源已有完整有限 WCS | 响应库无对应条目；或首次拟合被判 `degenerate` 后不得靠改响应名重试 | `adopted` / `degraded` / `failed` |
| 2 | PCC | 有来源的标准 RGB + IR 阻挡兼容证据（`source_bound_v1` 下须绑定 reports 文件） | 滤镜类别为窄带/双窄带/亮度；无任何来源证据 | `adopted` / `degraded` |
| 3 | 显示性回退 `pcc_fallback` | 用户显式选择，`source=user` 且 `fallback_reason` 非空 | 无用户证据；不得声明测光或真彩星色 | `adopted`（`claim=display_color_balance`） |
| 4 | native neutral | 测光路线不可解、无证据，或用户不选择测光 | 无可信天空或 fit/validation 分区不成立 | `adopted` / `failed` |
| 5 | skip / `preserve` | 以上均不成立 | 不得写成"无梯度""已中性"或"无需校色" | `skipped` / `unavailable` |

**跑前无法做星表相关性预检**：本阶段命令白名单只含 `platesolve`、`spcc`、`pcc`，不允许 `findstar`，也不允许下载或安装星表，因此跑前拿不到星表颜色。判据分两段——跑前只做数据侧风险评估（饱和像素比例、采样 arcsec/px 与星场拥挤度、天空可信度，据此决定是否值得试测光及 `-limitmag` 取值），跑后由回执的 `fit_quality` 判定拟合是否退化。

窄带保留本阶段既有 SPCC/PCC 来源与显式选择条件，原生 dualband-osc 默认评估单位增益 neutral；`dualband=off` 只关闭 Stage 7 HOO 估计，不改变输入类型或校色资格。SHO/HOO 显示映射留在所选分支，校色成功也不证明最终哈勃配色、宽带真彩星色或未采集的 SII 信息。

<a id="scientific-container-inputs"></a>
## 科学容器与来源绑定

Astrometry、PCC/SPCC 和新策略 native neutral 直接读取已验证 FITS/XISF；公开 ROI 测量仍只接受 FITS。XISF 固定 Image 0，与 Siril 导入一致，后续 Image 即使是 integration masks 也不改选择。几何来自实际 Image 属性，旧 NAXIS 不覆盖它；元数据读取有类型的 `FITSKeyword`，不解析 PixInsight 私有 WCS 属性。重复冲突关键字、非有限 WCS/初始信息、损坏 XML、危险外部引用或来源漂移阻断处理；缺完整 WCS 时须先原生解算才能测光校色。

科学输出为 FITS。像素证据绑定原文件哈希、Image 0、声明单位与 FITS 底部起算方向。Astrometry 保持 1e-7 绝对恒等容差并核对已有 WCS；测光校色检查有限 RGB、裁剪计数及 WCS 保留。这些不能证明天空对应或测光保真。

浮点 XISF 必须显式 `bounds="0:1"`；Siril 1.4.4 对其他 bounds 导入不归一化，因此执行前拒绝，不改变单位或放宽恒等检查。无符号整数使用类型满量程；布局与数据载荷沿用安全 XISF reader，不回退到 JPEG 或显示像素。线性、滤镜/响应、星表/网络、审查及同父源回退边界不变。

本阶段维护专属协议正文、参数、SSF 与审查。按 [协议索引](../protocol-index.md) 绑定本文件为唯一 primary 和完整 SHA-256；实际参与决策的其他材料按原规则记录为 supporting。通用质量门见 [quality](../quality.md)。

<a id="astrometry-solve"></a>
## astrometry.solve


<a id="applicability-and-evidence"></a>
<a id="astrometry-solve-applicability-and-evidence"></a>
### 适用条件、来源与声明

测光校色前只处理线性 FITS/XISF。已有完整有限 WCS 时用仅 load/save 的脚本保留，并记录 `preserved_existing_solution`；否则默认原生在线 Gaia 解算。本地 Gaia 可选，显式 `--offline` 只能用它；缺本地星表或坐标/采样证据时记录 skipped，不能宣称成功。

子帧 WCS 只有在几何和 master 像素坐标对应已验证后才能同步，见 [源头保留协议](../astrometry-source-preservation.md)。坐标只是初始信息，不是 WCS 解；只有提示时优先原生 Gaia。

坐标及采样来自有限文件元数据 `RA`、`DEC`、`FOCALLEN`、`XPIXSZ`。用户覆盖须声明 `# astrometry-evidence: {"source":"user","evidence":"exact user evidence","ra":98.3,"dec":4.7,"focal":160,"pixelsize":2.9}`，匹配 `platesolve -catalog=gaia -noflip -nocrop 98.3,4.7 -focal=160 -pixelsize=2.9`；provenance 记录同一真实依据，不从文件名推断。

<a id="ssf-knowledge-and-skeleton"></a>
<a id="astrometry-solve-ssf-knowledge-and-skeleton"></a>
### 参数与 SSF 示例

本阶段文件为 primary。变体查询冻结 `platesolve/set` 手册并保留 evidence；在线形式只查询当前星场，不下载完整星表或安装工具。

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
platesolve -catalog=gaia -noflip -nocrop
save "/abs/session/artifacts/040-solved" -chksum
stat main
savejpg "/abs/session/previews/040-solved" 95
close
```

本地解算在 load 前设置 `set "core.catalogue_gaia_astro=/abs/gaia_astrometric.dat"`，使用 `-catalog=localgaia` 并绑定 probe 冻结的精确文件；远程解算不能设置本地目录。catalog 始终逐脚本显式选择，不依赖 session 在线默认。保留已有解时省略 set 和 platesolve。

科学产物声明为 primary。成功要求完整有限 WCS、尺寸不变、像素方向和值不变，已有 WCS 系数保留。保留实际 Siril 解算/匹配结果并审查天空对应；往返闭合不证明匹配正确。需要时使用 `sync_fits_header.py inspect/verify` 核对 round-trip（< 1e-4 px）及 footprint。服务失败可记录理由后评估 neutral；证据格式、哈希或基础设施错误停止，不实现 Astrometry.net API。

<a id="receipt-status-is-an-outcome-not-a-request"></a>
<a id="astrometry-solve-receipt-status-is-an-outcome-not-a-request"></a>
### 验证与 receipt 状态

静态验证在 Siril 前，只记录 `solve_requested_local_gaia` 或 `solve_requested_remote_gaia`。只有输出写入后重测完整有限 WCS、几何和恒等像素，receipt 才记录 `solved_local_gaia`、`solved_remote_gaia` 或 `preserved_existing_solution`。失败保留 requested 状态，不能把请求解释或报告为已成功解算，缺 primary 时也不宣称解算完成。

<a id="failure-ladder-for-a-remote-solve"></a>
<a id="astrometry-solve-failure-ladder-for-a-remote-solve"></a>
### 远程解算失败与回退

第三方星表服务失败可能暂时发生；按实际日志分类并在 review notes 记录。每 session 只用一次下列处理，不重跑已接受候选。

1. `transient`：超时、HTTP 5xx 或服务不可达/无响应。短暂间隔后以新 run ID 重试同一解算一次，来源、证据和参数保持相同；这是有限服务重试，不是参数搜索。
2. `persistent_request`：HTTP 4xx 或端点响应损坏。不重试同一请求；检查冻结 probe 的 `tools.local_gaia_astro.compatible`，可用时切换上方本地骨架并绑定精确文件，否则记录不可用。
3. `no_match`：目录已响应但未匹配星点，属于图像/初始信息证据，不是基础设施。禁止盲重试；评估真实用户/文件坐标的 `platesolve ... RA,DEC -focal= -pixelsize=` 形式，再核对天空对应。

穷尽适用处理后才回到颜色路径，记录真实原因以区分 skipped 和 failed；二者都不能写成成功，依赖 WCS 的 SPCC/PCC 保持未执行。

<a id="color-calibrate"></a>
## color.calibrate


<a id="color-calibrate-适用条件"></a>
### 适用条件

只用于已确认通道来源的 linear 三通道父源。SPCC 可用于宽带或有实际通带/组合历史证据的窄带 RGB；PCC 与 neutral 分别遵循下方方法边界。PCC/SPCC 必须有完整有限 WCS。双窄带默认先评估 native neutral，无需 WCS 或星表；SPCC/PCC 仅按用户明确选择及其证据独立评估。宽带仍按各自证据选择方法，不叠加校正。由 Agent
根据证据生成独立 SSF 并审查，Python 不编排流程。默认在线，Siril 不带 `--offline` 启动；`pcc/spcc` 仍必须显式声明
`-catalog=gaia|localgaia`。`init --offline` 的 session 只使用已配置并由 probe 冻结的本地 Gaia，缺目录时不执行测光校色。

<a id="stage4-preflight"></a>
### 校色 SSF 生成前的准备

坐标保持不变的已接受谱系可以复用已检查的 ROI 布局，但要写入绑定新父源哈希的合同并实测新父源；不必重新识别未变几何，也不能照搬旧数值。先选择一条主路线并记录回退触发条件，触发后再准备备选系数、区域、原生试跑和 SSF。决策摘要引用证据，不重复完整来源表或未经验证的物理解释。

进入 Stage 4 时先按下方方法边界确定首选路线、证据缺口与适用回退条件。Stage 3 已接受或保留的线性父源确定后，若测光路线需要解算，先独立执行并审查 `astrometry.solve`；生成首份校色 SSF 前再冻结实际直接父源的绝对路径、SHA-256、尺寸、方向及 WCS。解算前的文件哈希不能代替解算后父源，即使科学像素恒等。每种备用方法仍从同一已接受校色前父源开始，不叠加已校正、失败或拒绝的候选。

native neutral 在编写 SSF 前完成以下准备：

1. 从实际校色前父源独立调用[现有采样器](#color-calibrate-自动背景中性化)，默认提议 36 个 16×16 天空区，实际逐框审查后确认 fit/validation 分区。两组各至少三个有效 RGB 天空区，每区至少 64 个有限像素，所有背景 ID 恰好分配一次；完整矩形互不重叠并避开目标。不能把 Stage 3 的拟合样点、六区尺寸要求或跳过结论直接当作本阶段合同；背景候选未执行、无收益或被拒绝时，仍从保留的父源执行这项独立评估。
2. 同时确认 `core/faint/stars` 保护诊断区。M31 示例应覆盖核心、微弱外盘和代表星区；其他对象按实际结构选择，缺少可辨保护区时记录限制，不虚构区域。诊断区不参与天空拟合，validation 不参与系数估计；同图增加角色不证明天空可信。
3. 根据实际父源诊断选择显式 MTF 参数，记录可看清微弱结构的依据、原始方向及固定裁切位置。优先复用与当前父源绑定且已审查的显示参数；现有预览不足以辨认外缘时先补足显示诊断依据，不猜默认值。下方 `mtf 0 0.15 1` 只是语法示例，不是默认值或外盘可见性保证。父源与候选必须使用同一参数、同一位置的全幅和局部对照，不能分别 autostretch。
4. 冻结区域合同、分区、来源和曲线后再测量系数、编写 SSF。同父源的后续候选复用这些基线；确需调整时保留旧合同与曲线，记录原因并建立新评估基线，不能根据已看过的候选补选有利 validation。父源变化后重新核验来源与测量，只有几何、方向及像素坐标对应已确认时才能复用区域坐标并重绑新哈希。

PCC/SPCC 按其设备、星表及白参考证据准备，保留现有单次 `load` 骨架和预览合同；不要把 neutral 的分区、九产物清单或双预览尾部强加给测光路线。本节不扩展 PCC/SPCC 的双预览能力。

<a id="stage4-paths-and-invocations"></a>
### 一次派生路径与调用参数

先确定 session 根路径与 run ID，再用同一份路径清单填入现有 SSF 骨架、同 stem provenance、来源声明、全部 `--expect` 和审查调用。下面是 native neutral 的只打印命令示例，用已核验的 `STARUN_PYTHON` 执行；先替换三个绝对路径。它不生成 SSF、不执行 Siril，也不构成新 CLI。

```python
from pathlib import Path
import shlex
import sys

session = Path("/abs/session").resolve()
source = Path("/abs/current-pre-calibration-parent.fit").resolve()
cli = Path("/abs/starun-siril/scripts/deep_sky_siril.py").resolve()
run_id = "046-neutral"
regions_run_id = "046-neutral"  # Keep the reviewed region contract on retries.
paths = {
    "script": session / "scripts" / f"{run_id}.ssf",
    "provenance": session / "scripts" / f"{run_id}.provenance.json",
    "regions": session / "reports" / regions_run_id / "regions.json",
    "sampling": session / "reports" / regions_run_id / "sampling.json",
    "raw": [session / "artifacts" / f"{run_id}-raw-{c}.fit" for c in "rgb"],
    "corrected": [session / "artifacts" / f"{run_id}-corrected-{c}.fit" for c in "rgb"],
    "primary": session / "artifacts" / f"{run_id}.fit",
    "parent_preview": session / "previews" / f"{run_id}-parent.jpg",
    "candidate_preview": session / "previews" / f"{run_id}-candidate.jpg",
    "review": session / "reviews" / f"{run_id}.json",
}
expected = [*paths["raw"], *paths["corrected"], paths["primary"],
            paths["parent_preview"], paths["candidate_preview"]]
run_args = [sys.executable, "-B", str(cli), "run", "--session", str(session),
            "--protocol", "color.calibrate", "--script", str(paths["script"]),
            "--source", str(source), "--primary-output", str(paths["primary"])]
for output in expected:
    run_args += ["--expect", str(output)]
review_args = [sys.executable, "-B", str(cli), "review-check",
               "--session", str(session), "--run-id", run_id]
print(shlex.join(run_args + ["--validate-only"]))
print(shlex.join(run_args))
print(shlex.join(review_args))
print(shlex.join(review_args + ["--review", str(paths["review"])]))
```

`paths["regions"].relative_to(session).as_posix()` 用于声明的 `background_regions`；`source` 同时用于父源 `load` 和 `--source`。SSF 路径用双引号，shell 命令使用上例的参数列表与 `shlex.join`，不手拼含空格路径。`split` 与 `savejpg` 的 stem 用对应路径的 `.with_suffix("")`，`--expect` 始终使用实际 `.fit/.jpg` 文件；PixelMath 使用 `$` 包围的 session-relative、无扩展名图像引用，例如 `$artifacts/046-neutral-raw-r$`，不是 shell 变量或绝对路径。provenance、区域与采样报告不是这份 SSF 写出的文件，不计入九项 `--expect`。

静态校验和实际执行复用同一 `run_args`，只切换 `--validate-only`，不手工重写第二条命令。静态通过后先核对报告，再实际执行；静态报告不是 run receipt。native neutral 科学分支必须先完成七份 FITS，再按现有骨架保持 `close → load parent → mtf → savejpg → close → load candidate → mtf → savejpg → close` 的尾部，不追加 `stat` 或额外变换。PCC/SPCC 沿用各自骨架，不能复制该尾部。

<a id="stage4-preflight-record"></a>
### 准备记录与重跑证据

在现有 `reports/` 保存 `stage-04-preflight.md`，于首份校色 SSF 编写前记录已确定项目；静态校验后补记实际结果，运行与审查后逐次追加处置。它是 Agent 工作记录，不新增 schema、机器解析或执行器强制门；不回填旧 session。记录以下证据及实际时间，完整日志以路径和指纹引用：

- 路线依据、缺失证据、解算/回退条件；实际父源路径、SHA-256、几何、方向及适用 WCS。
- 区域合同与采样报告路径和哈希、逐区审查结论、fit/validation ID、主体与星区保护及无法判断的限制；不足时记录具体原因并保留父源。
- 显示参数的父源诊断依据、全幅/局部实际审查位置；区分已选择参数与实际看过的材料，不把数值记录当作视觉审查。
- 路径清单、SSF/provenance 指纹、实际读取的 primary/supporting 文档与手册查询 evidence、静态调用/报告/结果；来源、哈希、解码或路径错误沿用阻断规则，不归类为无收益。
- 每次尝试的 run ID、修改原因、实际调用及结果、receipt/原始日志、科学产物/预览/测量、review 路径与最终处置。静态拒绝也保留原因和原报告，失败 run 的产物只作诊断。

实际重跑使用新编号和新候选路径，保留旧脚本、provenance 与证据；同父源重试的 `regions_run_id` 继续指向已冻结合同。修改 SSF 后重新计算并绑定 provenance 的脚本哈希及实际引用，不复制旧运行或审查指纹。按上例第三条命令从真实 receipt 取得 review 骨架，实际打开材料、填写审查后才运行第四条校验命令；两条均保留 `--session` 与 `--run-id`。最终仅采用实际接受的候选；微小可复核收益允许采用，无可信天空、实测无收益或审查不通过时保留父源。

<a id="color-calibrate-方法"></a>
### 方法

- `neutral`：仅背景中性化；有可靠白参考才增加参考白平衡，不增加光度真实性声明。
- `pcc`：必须有有效 WCS、显式 `pcc -catalog=gaia|localgaia` 及下方标准 RGB 兼容性证据。光害滤镜、缺少 IR 阻挡或非标准通带不能仅凭 broadband 标签获得测光声明。
- `spcc`：显式指定 catalog、全部实际响应和白参考。probe 通过离线原生 `spcc_list` 读取 Siril 已有响应库；
  相机到传感器的对应关系须有用户、采集或厂家来源，不能模糊匹配。mono 宽带必须填写 sensor 与 R/G/B
  filters；OSC 宽带必须填写 sensor、filter 与 LPF。相关响应缺失时按已确认通道和滤镜证据评估兼容的 PCC、背景中性化或跳过，不沿用历史设置。
  白参考默认明确填写 `Average Spiral Galaxy`，来源标为 `policy_default`；其他白参考需用户或采集来源。
  `FILTER=mixed` 不能确定三片滤镜。响应库缺失不自动放行 PCC，不安装或下载外挂星表。

<a id="color-calibrate-双窄带适用性"></a>
### 双窄带适用性

`dualband-osc` 不等于普通宽带 RGB；有效 WCS 也不足以证明下方 PCC 骨架适用。默认先评估单位增益背景中性化；非恒等且天空可信时实际试跑，不能仅凭“已接近中性”跳过；天空不可信、系数恒等或实测无改善时记录理由并保留父源。仅在用户明确选择测光路径时核对实际滤镜
通带、通道提取/组合历史和目标，再说明所选方法为何适配；SPCC 证据不足或拟合被拒绝时，
保留父源，或在用户明确选择时按下方 PCC 合同评估显示性颜色平衡，不把显示偏色当作宽带校色依据。

已确认 linear `dualband-osc` 或非标准宽带 RGB、且有完整 WCS 时，PCC 仅在用户明确选择后作为显示实验。使用原始已接受校色前父源，声明真实 `fallback_reason` 及 `equipment.pcc_fallback`，其中 `source="user"`、`evidence` 非空；`policy_default` 不能授权。`channel_mode` 不变，receipt 使用 `claim="display_color_balance"`，不宣称恢复宽带星色或物理校准线比。unknown 通道及其他窄带组合不获得此回退。

实际检查拟合、背景、星云颜色/结构和裁剪；imprecise-solution 是拟合质量告警，不自动失败或 reject，须保留并审查真实结果。颜色恶化 reject 并保留校色前父源。

使用 SPCC 窄带模式时，按冻结手册查询并显式指定 `-narrowband`、每通道中心波长与带宽
（`-rwl/-rbw/-gwl/-gbw/-bwl/-bbw`）及有来源的白参考，不沿用上次配置。窄带模式忽略普通滤镜
选项；波长/带宽须对应实际通道，而非从 RGB 颜色猜测。相同来源的 HOO G/B 通道使用相同通带
参数；参数或角色缺失时跳过 SPCC。Gaia XP 的低光谱分辨率可能在强谱线附近影响窄通带的合成通量；检查恒星拟合残差和响应模型限制，不因原生支持某带宽就声明精确线比，也不设置新的固定带宽禁令。

Siril 窄带 SPCC 也支持原生 OSC RGB，不必先提取 HOO。确认去拜耳/堆栈历史、实际传感器及滤镜通带；未重映射 OSC 用 `equipment.channel_layout` 记录 `{"value":"native_osc","source":"acquisition","evidence":"actual RGB processing history"}`，用户明确声明可用 `source="user"`。保持 `channel_map` 未设置，名义 SPCC 通带不能把混合 Bayer 平面变成独立发射线观测；已有可信通道图时沿用其真实组合历史。不从 `FILTER=LP`、设备名、色调或文件名推断。

SPCC 校正与 HOO/SHO 显示映射是不同目的；校色不补回未采集的宽带星色，也不保证得到 Hubble
palette。映射仍按 [color.map](../stages/stage-08-starless-enhance.md#color-map) 的图像域与来源要求执行。

<a id="color-calibrate-ssf-知识关系"></a>
### SSF 知识关系

适用性、校准方法和设备参数来自 WCS、用户/采集来源与当前父源证据；本协议对应的阶段文件是 SSF 的 primary protocol
reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供命令语法语义；`command-policy.json` 独立决定
执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定流水线。下方 local/online PCC
完整变体可记录 manual lookup `not_needed`；neutral、SPCC 或未展开选项必须先查询原文并保留 evidence。

<a id="color-calibrate-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
# color-calibration: {"method":"pcc","source_sha256":"CURRENT_SOURCE_SHA256","equipment":{"pcc_compatibility":{"value":"standard_rgb","source":"acquisition","evidence":"Actual RGB passbands and IR-blocking record","reference":{"path":"reports/pcc-evidence.txt","sha256":"ACTUAL_SHA256","size":123}}}}
set "core.catalogue_gaia_photo=/abs/local-gaia"
load "/abs/current-parent.fit"
pcc -catalog=localgaia
stat main
save "/abs/session/artifacts/040-color" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/040-color" 95
close
```

online Gaia 删除 `set` 并使用 `-catalog=gaia`；`-catalog=` 在任何 session 都强制显式声明，不因在线默认而省略。SPCC 用官方
1.4.4 `spcc` 选项并显式指定 catalog，所有字符串严格引用；
不得添加协议未说明的 profile 字段。

<a id="color-calibrate-spcc-与回退来源记录"></a>
### SPCC 与回退来源记录

Agent 为需要设备/回退证据的脚本写一行 `# color-calibration: JSON`；它通过 SSF 哈希绑定，无需新增 sidecar。
字段为 `method`、`source_sha256`，以及适用的 `equipment`、`white_reference`、`background_regions`、
`protection_regions`、`route_history`、`fallback_reason`。声明方法必须与实际命令一致，hash 必须绑定当前校色前父源。PCC 也必须提供该行：标准宽带使用 `equipment.pcc_compatibility`，显示性试验使用 `equipment.pcc_fallback` 与真实 `fallback_reason`，两种声明不能混用。

`route_history` 是可选的阶梯记录，按实际评估顺序列出每一级：`method` ∈ `spcc|pcc|neutral|preserve`，`status` ∈ `adopted|degraded|failed|skipped|unavailable`，`reason` 非空且不超过 2000 字符，最多五级，最后一项必须与实际声明的方法一致。测光拟合被判为退化是合法且必须记录的降级理由；"看起来偏色"或"已接近中性"不能作为跳级依据。缺省该字段表示按旧行为只记录最终方法。

标准宽带兼容性声明示例（必须替换成当前设备的实际证据）：

```json
{"value":"standard_rgb","source":"acquisition","evidence":"Confirmed ordinary RGB passbands and appropriate IR blocking in the acquisition record","reference":{"path":"reports/pcc-evidence.txt","sha256":"ACTUAL_SHA256","size":123}}
```

将该对象放在 `equipment.pcc_compatibility`。`source` 仅接受 user/acquisition/manufacturer；厂家 evidence 必须为实际查询的 HTTPS 来源。它声明普通 RGB 响应适配，不能由滤镜分类或像素颜色推断；与实际滤镜冲突时拒绝。receipt 绑定 `compatibility_evidence`，显示性试验绑定原有 `fallback_evidence`。
新 `evidence_admission=source_bound_v1` 要求 acquisition/manufacturer 的 `reference` 绑定 reports 中非空来源文件的路径、SHA-256 和大小；厂家文件记录实际查询日期、URL 与相关结论。user 的 evidence 保留用户明确确认原文。Agent 检查来源实际支持 RGB 响应与 IR 阻挡；文件哈希、FILTER=IRCUT 和 BAYERPAT 本身不证明兼容。

SPCC 的 `equipment` 按命令参数名记录来源对象，例如 `monosensor/rfilter/gfilter/bfilter`。
每个对象含 `source` 与 `evidence`；`source` 可为 `user`、`acquisition`、`manufacturer` 或 `fits_header`。
厂家映射的 evidence 必须为实际查询过的 HTTPS 来源；FITS 来源另附 `key`，对应头值须与所选响应一致。
`white_reference` 使用同样结构，策略默认值例外允许如下记录：

```json
{"source":"policy_default","evidence":"Use the explicit Average Spiral Galaxy reference"}
```

先用 probe 中 `spcc_profiles.names` 核对精确名称。mono/OSC 二者仅选一个。窄带另提供六个正数通带参数及
`equipment.passbands` 的 user/acquisition 来源。SPCC 参数的完整命令语法必须查询冻结手册并保留 evidence。
厂家通带也可用实际查询的 HTTPS 来源，`filter` 须匹配科学父源滤镜别名，`parameters` 对象包含与 SSF 完全一致的六个波长/带宽值；Siril 文档允许厂家 FWHM 作为带宽。evidence 记录精确滤镜型号和配置，不替换成近似名称产品。复用现有 evidence 对象，不能从 RGB 算术、分位数或合成 SII 填波长；滤镜分类仍不足。

<a id="自动背景中性化"></a>
<a id="color-calibrate-自动背景中性化"></a>
### 原生背景中性化：来源与样区

只用于已确认线性宽带或双窄带 OSC RGB，无需 WCS 或星表。双窄带默认先评估此路径；宽带按其方法证据独立选择。测光不可用或拒绝时也须回到同一已接受校色前父源，不使用拒绝的 SPCC/PCC 输出；不改变通道元数据，不宣称校准发射线比例。

现有只读采样器可直接从科学 FITS/XISF 提议哈希绑定的 `starun-siril.regions.v1`：

```bash
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/siril_auto_samples.py \
  --source "/abs/current-pre-calibration-parent.fit" \
  --output "/abs/session/reports/046-neutral/regions.json" \
  --regions --samples 36 --region-size 16 --target-type emission_nebula \
  --report "/abs/session/reports/046-neutral/sampling.json"
```

使用真实目标类型；`emission_nebula` 只展示本天区。RGB 采样保护任一通道信号，包括仅红通道 Ha 发射。候选区为不同、不重叠方框，完整分辨率检查有限、未截断 RGB 和逐通道中位数异常。`--regions` 拒绝显示预览，接受 12..64 个提议样区和 8..64 像素边长，至少六个背景区存活，不放松排除蒙版。矩形沿用预览左上起算、零基、半开坐标；辅助读取使用 canonical FITS 底部起算像素，XISF 固定 Image 0 与声明单位。

实际对照父源逐框审查，自动蒙版不能证明没有微弱云气、尘埃或星晕；目标满幅或天空不可信时跳过并说明理由，不以全图、目标像素或 gray-world 替代天空。仅系数恒等时不创建 identity run；非恒等候选先试跑，用独立 validation 与配对显示检查微小改善和代价，不以偏移低于单像素噪声作为跳过或拒绝依据。

全幅偏红、存在紧凑星点或不能证明绝对无发射，不单独构成天空不足；沿用 Stage 3 的[区域资格判别](stage-03-background.md#background-control-eligibility)，同时保持本阶段较小矩形的完整像素、目标排除与拟合／验证独立性。实际污染的提议区应排除；若剩余不足，可依据来源局部人工重选一次并保存原提议及理由，不按候选结果移动验证区，也不放宽采样器阈值。仍不足时记录本阶段实际样区和具体阻塞，不能只引用 Stage 3 的 75×75 控制区失败。

Agent 用普通 Python 调用只读 `deep_sky_siril_processing.neutral_coefficients(source, regions_path,
white_reference=None, background_partition=confirmed_partition, policy="scientific_native_flow_v2")` 得到各通道背景中位数 `b`、共同背景 `t=max(b)` 与增益 `g`。
双窄带 OSC 的 `white_reference` 必须缺省或 null，增益精确为 `[1,1,1]`；仅做加性偏移 `C' = C - b[c] + t`。保留本阶段背景扣除后的 RGB 信号和原始通道增益关系，不执行星点白平衡、全图通道均衡、重标度或模拟谱线。不宣称物理 Hα/OIII 比或最终非线性图的线强度比；拟合背景汇总对齐不代表每个独立天空 ROI 都精确相等。

可靠分区确定后必须实际计算系数，并在 `stage-04-preflight.md` 保存区域来源、分区、各通道 `b`、共同 `t`、增益及加性偏移 `t-b[c]`。只有恒等系数才因无操作跳过；非恒等时运行一份保守候选，双窄带保持单位增益，宽带沿用下方有来源白参考规则，以固定 validation 色差、目标扣背景后信号、科学端点及同曲线像素决定采用或保留父源。Stage 3 的空间梯度无收益不等于本阶段通道偏移恒等；不得仅以保留双窄带原配色、背景已近中性或偏移小于单像素噪声省略试跑。无可靠白参考时不增加增益，现有公式闭合、来源及安全审查不变。

无白参考时 `g=1`；仅宽带有参考时，只接受有 user/acquisition 证据的 `stars` 区域，至少九个未饱和像素且
信号高于背景噪声，再以 `g[c]=min(signal)/signal[c]` 计算相对白平衡。不得把背景中性化称为测光校色。

neutral 声明含 `background_regions` 的 session-relative 报告路径；有白参考时 `white_reference` 另含
`region` 指向合同中的 stars 区域。Agent 编写一份 SSF：先 load 父源、立即且恰好一次 fmul 1，然后 split 为三个不同原始层；分别以
`pm "($artifacts/raw-channel$ - b) * g + t"` 计算并 save 三个不同校正层；随后
`rgbcomp corrected-r corrected-g corrected-b "-out=/abs/primary.fit" -nosum`，关闭科学分支后用相同显式 MTF 分别生成父源与候选预览。
公式中的三个数字须使用 Python `.17g` 格式；不使用 rescale、截断或额外变换，科学输出先于预览拉伸。
七个科学路径必须不同，全部声明 `--expect`，并指定重组结果为 `--primary-output`。

只读验证会重算系数并逐像素检查公式闭合、有限值、几何与已有 WCS，记录校正前后及成对新增的科学端点越界。
区域合同在执行期间变化即失败；白参考不可靠时移除白参考后重新评估背景中性化，不猜增益。
现有 SSF 声明/receipt 记录真实 `fallback_reason`、区域合同和测量系数。对照父源/候选、固定天空/目标/星区、通道裁剪和背景连续性；明确改善且全部适用门通过才接受，拒绝保留校色前父源。自动测量不替代视觉采用。

<a id="neutral-held-out-evidence-policy"></a>
<a id="color-calibrate-neutral-held-out-evidence-policy"></a>
### 独立验证分区与科学验收

当前冻结 `neutral=scientific_native_flow_v2`，`regions.v1` 不变；既有 `# color-calibration:` 声明增加 `background_partition`：

```json
{"fit":["bg-001","bg-002","bg-003"],"validation":["bg-004","bg-005","bg-006"]}
```

使用实际 ID，示例不能直接照抄。fit 与 validation 各至少三个有效 RGB 天空区，每区至少 64 个有限像素，覆盖所有背景 ID 且各一次；完整天空矩形互不重叠并避开 `core/faint/stars`。采样器 `--report` 只提议几何最远点分区（三分之一，至少三个），须实际确认来源。可在同一合同增加固定主体与星区；天空不足跳过，不能把目标改标为背景凑数。

只有 fit 区估计偏移及白参考的背景/噪声基准，validation 不参与拟合。不可变 receipt 保存 `processing_checks.color_calibration.neutral_validation`，绑定来源/候选/区域指纹、分区与系数。收益测量独立天空 ROI 的 RGB 中位数范围、成对改善、median/p90、改善比例及空间通道跨度，反映采样天空中性，不证明物理目标颜色或校准统计显著性；纯加性与有来源白参考方法分别标记。

安全证据逐通道记录新增、保留与恢复的暗部/高光端点、8×8 预览坐标网格及超界幅度。科学浮点跨越 0/1 不证明硬截断；core/faint/stars 减去最近固定 validation 天空，报告增益修正后的信号差。缺区域、近零或非正颜色分母保持不可用，不能只凭原 RGB 比变化推断目标受损。

数值证据保持 `uncertain`，实际审查后才 accept。绑定 neutral 报告替代通用候选绝对暗部计数建议，不能仅因继承端点拒绝。有限像素、公式、几何、WCS、来源完整性仍必需，不新增未经标定的收益/端点门限。review 材料含 run receipt 和区域合同，accept 还须绑定同一显示传递的父源/候选预览；附加 metrics/recovery 只复制 receipt 证据，不能替代它。

<a id="native-neutral-ssf"></a>
<a id="color-calibrate-native-neutral-ssf"></a>
### 原生 neutral SSF 示例

使用当前校色前线性 RGB 父源。NGC 6888 等满幅发射天区优先人工确认 fit/validation 天空及 `core/faint/stars` 保护；四角、低亮度或自动蒙版不证明纯天空，可信天空不足跳过。公开 ROI 只接受 FITS，科学容器 ROI 仅供此内部 neutral 路径；XISF 固定 Image 0、声明单位与 canonical FITS 底部起算，浮点 bounds 必须 `0:1`，输出 float32 FITS。

完整模板假设实测 `b=[0.02,0.04,0.03]`、`t=0.04`、单位增益及真实六区域分区。每个系数以 `.17g` 替换，绑定真实来源 SHA-256、路径与 ID，不当作默认值。`mtf 0 0.15 1` 只展示显示传递，按真实父源选择并同参数作用于两份预览。全部七份 FITS 和两份 JPEG 列为 `--expect`，重组 FITS 为 `--primary-output`。

```ssf
requires 1.4.4 1.5.0
set32bits
# color-calibration: {"method":"neutral","source_sha256":"SOURCE_SHA256","background_regions":"reports/040-neutral/regions.json","background_partition":{"fit":["bg-001","bg-002","bg-003"],"validation":["bg-004","bg-005","bg-006"]},"white_reference":null}
load "/abs/current-parent.xisf"
fmul 1
split "/abs/session/artifacts/040-raw-r" "/abs/session/artifacts/040-raw-g" "/abs/session/artifacts/040-raw-b"
close
pm "($artifacts/040-raw-r$ - 0.02) * 1 + 0.04"
save "/abs/session/artifacts/040-corrected-r.fit" -chksum
close
pm "($artifacts/040-raw-g$ - 0.04) * 1 + 0.04"
save "/abs/session/artifacts/040-corrected-g.fit" -chksum
close
pm "($artifacts/040-raw-b$ - 0.03) * 1 + 0.04"
save "/abs/session/artifacts/040-corrected-b.fit" -chksum
close
rgbcomp "/abs/session/artifacts/040-corrected-r.fit" "/abs/session/artifacts/040-corrected-g.fit" "/abs/session/artifacts/040-corrected-b.fit" "-out=/abs/session/artifacts/040-neutral.fit" -nosum
close
load "/abs/current-parent.xisf"
mtf 0 0.15 1
savejpg "/abs/session/previews/040-neutral-parent" 95
close
load "/abs/session/artifacts/040-neutral.fit"
mtf 0 0.15 1
savejpg "/abs/session/previews/040-neutral-candidate" 95
close
```

仅授权该已验证恒等提升，`set32bits` 本身不提升整数输入。禁止其他通道归一化、重标度、截断或混合测光；公式闭合容差保持 `1e-6`，有限像素、几何、WCS、源选择、单位与方向均绑定。采用前检查独立天空改善、成对端点与局部背景扣除后的目标信号。[官方手册](https://siril.readthedocs.io/en/stable/processing/color-calibration/manual.html) 分别说明中位背景中性化与参考白平衡。

<a id="color-calibrate-审查与回退"></a>
### 审查与回退

检查背景中性、星色多样性、颜色连续性和通道裁剪。PCC/SPCC 失败、色彩断层或偏色恶化时 reject；
不得把显示调色描述成光度校准。PCC 的 `imprecise solution` 是拟合质量警告，不是执行失败，也不自动
要求 reject。日志保留恒星数量、系数、deviation、背景参考和原生完成信息，receipt 将该提示记录为
`pcc_imprecise_solution` warning。实际对照父源与候选，检查背景、星色、目标颜色连续性和科学截断；
全部适用质量门通过时可 accept，并在 review notes 与最终报告披露警告及实际判断依据，不能宣称高精度
光度真实性。颜色恶化、结构损失或截断不通过时 reject；材料不足时 uncertain，均保留校色前父源。
退出码、星数、有限像素或原生成功提示不能单独替代图像审查；真实错误仍按执行门禁处理。

<a id="large-angular-galaxies-and-pcc-fit-warnings"></a>
#### 大角径星系与 PCC 拟合告警

大目标、弥漫表面亮度及独立天空不足可能影响孔径测光与背景估计，不能凭天体名称或标称视场确认原因。Siril 的 deviation 是逐星校色系数的平均绝对偏差；梯度、滤镜兼容性和弥散背景都可能影响它，见 [官方 PCC 说明](https://siril.readthedocs.io/en/stable/processing/color-calibration/pcc.html)。

任务 2 M31 的日志最终使用 1550 颗恒星，排除 1999 颗：1860 颗在图像外、111 颗内半径太小、24 颗像素越界、4 颗通量比不通过；日志建议检查梯度。这些不证明 M31 盘面导致可用星数不足。保留告警、系数与 deviation 为实际观测，不将案例变成普遍物理解释或自动接受授权。

对照父源/候选，检查独立天空偏色与梯度、星色和星晕、核球至盘面颜色连续性及科学通道裁剪。独立天空不足时披露限制，不将微弱外盘当天空强制中性化。适用门通过后才可保留告警接受，并说明观察与精度限制；颜色恶化或裁剪失败 reject，证据不足 uncertain。核心看起来自然不能证明测光准确；线性、WCS、通带及同父源回退约束不变。

每次备用方法使用同一已接受的校色前父源，不能引用 SPCC/PCC 的失败或拒绝产物，也不叠加到已校色候选。
设备响应不足、真实求解失败或审查不通过时，重新检查备用方法的通道、滤镜和用户证据；条件不足则背景中性化或跳过。路径、来源、哈希、协议或运行时错误直接停止。
最终 receipt 的 `color_calibration` 记录实际方法、声明类型、目录模式和回退原因；仅把接受的候选纳入正式 lineage。

<a id="color-calibrate-fit-quality"></a>
### 拟合质量证据（fit_quality）

`pcc`/`spcc` 运行结束后，执行器从本次运行绑定的日志中解析拟合行、白平衡系数、星数与 imprecise 告警，写入 `receipt.processing_checks.color_calibration.fit_quality`：

| 字段 | 含义 |
|---|---|
| `fits[]` | 每条 `Image R/G = a + b * Catalog R/G (sigma: s)` 的 `ratio`、`intercept`、`slope`、`sigma`，附原始日志行号 |
| `white_balance` | `K0/K1/K2`，日志缺失时为 `null` |
| `stars` | `used` / `excluded`，日志未给出时分别为 `null` |
| `checks.slope_below_scatter` | 是否所有拟合都满足 `abs(slope) <= 0.05 * sigma` |
| `checks.gains_match_grey_world` | `K` 是否等于 `(B/G)/(R/G)` 归一化比值；仅作佐证 |
| `imprecise_warning` / `warnings` | Siril 的 imprecise 告警及其代码 |
| `verdict` | `acceptable` / `degraded` / `degenerate` |

判定：所有拟合斜率都落在散度之内即 `degenerate`（目录项对拟合没有解释力，典型表现是斜率≈0 而 `K` 恰为整图灰世界比值）；出现 imprecise 告警或任一 `sigma >= 0.20` 为 `degraded`；其余为 `acceptable`。这些阈值是工程启发式，不是物理置信度，只用于诊断与验收，不进入执行门。

`degenerate` 的测光候选一律不得 accept：保留校色前父源，按阶梯下行并把该级记入 `route_history`。`degraded` 允许在 review notes 书面说明后接受。`imprecise solution` 仍只是 warning——它进入 `log_diagnostics.findings` 与 `color_calibration.warnings`，不使 run 失败。日志既无拟合行也无该告警时不写入 `fit_quality`，因此旧回执与回放不受影响。


<a id="dwarf-metadata-is-not-calibration-compatibility"></a>
### DWARF 滤镜来源与独立校色证据

mini/3 明确 Duo-Band 记录仅提供厂家声明的 Ha/OIII 主要通带，不是传感器响应曲线、像素独占内容或 RGB 谱线角色，仍不兼容 standard_rgb PCC。Astro/VIS 的 broadband 分类也须独立有来源 PCC 兼容性；SPCC 须显式响应、通带参数与通道历史。DWARF II 通用 Duo-Band 不补设备专属 Ha/OIII。选择滤镜的来源见 [mini 手册](https://help.dwarflab.com/en/docs/DWARF-mini-Smart-Telescope-User-Manual)、[DWARF II/3 对比](https://www.dwarflab.com/gb/pages/astronomy-with-dwarf-3-smart-telescope) 和 [DWARF 3 手册](https://help.dwarflab.com/en/docs/DWARF-3-Smart-Telescope-User-Manual-Part1-App-Interface-Introduction?product=dwarf-3)；设备身份不证明实际滤镜选择。

### 历史兼容

旧 `heldout_benefit_safety_v1` 保留原计算与报告字段，仅回放/finalize，不补填或重写。v9/v10/v11 的确认与同图恢复保持原冻结证据；新 guidance 继续执行科学来源和 PCC 来源检查，不授予旧 session 新执行权限。完整版本历史见 [session 兼容说明](../session-contract.md#文档指导版本)。


双窄带 v20 的后续谱线估计沿用本阶段已接受的原生 RGB 父源及已记录的增益/背景处理。估计路线不补做宽带 PCC、不要求虚构响应曲线，也不将中性化解释成纯 Ha/OIII 提取；具体入口见 [Stage 7](../stage7-hoo.md#dualband-hoo-stretch)。
