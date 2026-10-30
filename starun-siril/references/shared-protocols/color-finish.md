# 共用协议 · 鲜艳曲线、轻量调色与局部饱和度

## 逐区域颜色目标与保护排除

新会话冻结 `color_region_goals=explicit_targets_v1`。存在不同颜色需求时，使用 `--region-goals reports/color-goals.json` 替代 `--target-gain`。后者仍是全部 faint ROI 共用目标的显式入口。Agent 根据实际材料指定意图和目标，不按饱和度自动分类；每次仍只有一个显式 D 和一份候选。数值目标只作诊断，不自动决定采用。

```json
{
  "schema": "starun-siril.color-region-goals.v1",
  "regions": {"path": "reports/color-regions.json", "sha256": "<actual sha256>", "size": 123},
  "goals": [
    {"region": "blue-shell", "intent": "enhance", "target_gain": 0.08},
    {"region": "red-filament", "intent": "preserve"},
    {"region": "sky", "intent": "preserve"}
  ]
}
```

文件必须逐一覆盖全部绑定 ROI。只有 faint ROI 可声明 enhance，并提供有限正数的相对 target_gain；preserve 不带增长目标。遗漏、重复、未知区域、来源或几何漂移、增强区被完全覆盖，均在原生执行前拒绝。全部 preserve 时跳过试跑，不生成 identity run。保护矩形包含其内全部像素，不是自动识别的结构轮廓。

复用现有原始单色权重 M0：将保护矩形并集内置零得到 B，最终权重为 `min(B, Gaussian(B))`，沿用现有 sigma。在现有 SSF 的蒙版 PixelMath 后、保存 mask-base 及平滑前，插入原生 `fill 0 x y width height`；坐标从左上起算，必须与绑定的保护 ROI 完全一致。fill 仅允许在此区域 GHS 变体的当前单色蒙版上执行，不能用于主体、RGB 层、旧 satu/rmgreen 或局部亮度路径。其他像素继续使用现有选择器、亮度和高饱和度保护；平滑在保护区外形成过渡，重叠时保护优先，不增加羽化参数或逐区 D。首次无亮度蒙版比较也保留保护排除。

增长统计使用增强矩形扣除保护矩形并集后的固定测量域，父源和候选使用完全相同的像素。颜色选择器适用性及 faint 亮度参考只检查此测量域；背景和核心参考沿用原适用边界。报告仍保留全部原 ROI 的完整统计和面板，另增加绑定的 region_goals 与逐区 target_results，记录意图、有效像素数、目标、实际增益及达标状态。区域模式的全局 target_gain/achieved_gain 为 null；任一可测目标未达为 false，全部达标为 true，其余为 null。近中性相对增益保持不可测，同时显示绝对饱和度变化；不为追数值提高 D 或撤掉保护。

所有原 ROI 继续进入 color_assessment，附上绑定的 intent 和逐区 assessment/observation。保护像素不变也仍须审查边界、结构和颜色连续性。采用必须有实际收益、实际目标达到、全部区域可接受且既有端点、元数据和材料门通过；数值目标未达本身不否决实际收益。目标文件、派生矩形、试跑、正式 SSF、receipt 和审查材料须一致绑定；保持同一父源与 ROI，以 previous/observation 和新 run ID 修订。首次增加亮度蒙版时目标文件也须相同；改变目标或保护选择须另存不可变目标文件并说明实际观察，不能改写旧证据。

修改前完整策略、helper/实现、文档和配置指纹保存在 `references/color-region-goals-history.json`。旧会话原样回放／finalize，不迁移报告字段或像素。正式／试跑容差保持 1e-6；科学图层全部由 Siril 写入，不增加依赖、不升级版本、不发布。


## 新保色参数

新非零 GHS 增色提案须根据实际 ROI 显式填写 `--saturation-protect-start S0 --saturation-protect-end S1`，满足 `0<=S0<S1<=1`。从父源 HSV 饱和度计算 `Wsat=1-smoothstep(clamp((S-S0)/(S1-S0),0,1))`，乘入 M0 后使用 `min(M0,Gaussian_2(M0))`。`--mask-mode none` 只取消亮度蒙版，仍保留饱和度保护。SSF 声明及不可变试跑／回执绑定两个阈值与 `mask_policy=vivid_preservation_v1`；零目标不要求阈值。首次增加亮度蒙版时固定 D 和两个阈值，后续修订可按实际观察显式更改。

检查父源／蒙版／候选、目标有效覆盖率和权重分位数，以及每个 ROI 的颜色、亮度与端点。弱色增强可能显露色噪，实际审查不通过时保留可信父源；没有新保色标记的旧会话仍使用原乘积蒙版和颜色配方。

<a id="inverse-saturation-candidate"></a>
## 可选逆向饱和度候选

新会话冻结 `color_curve_variants=inverse_saturation_v1`。默认继续使用原正向 `ght -sat`；可显式提出 `--curve invght --strength D --sp SP --hp HP`。非零目标必须同时提供有限数值 D、SP、HP，满足 `0<D<=10`、`0<=SP<=HP<=1`；拒绝布尔值和非有限数值。B、LP 固定为 0，仅处理全部通道的饱和度，固定 `-clipmode=rgbblend`，不开放独立 RGB 或任意原生命令参数。正向提案不接受 SP/HP，零目标仍不产生试跑。

例如 `--curve invght --strength 1.2 --sp 0.5 --hp 0.7` 只是显式提案格式，不是默认档位。原生骨架生成 `invght -D=1.2 -B=0 -LP=0 -SP=0.5 -HP=0.7 -sat -clipmode=rgbblend`，提案、声明、试跑、正式回执共同绑定 `curve="invght"`、`SP`、`HP` 及 D；完整解析值必须一致，不能以数字显示舍入掩盖参数变化。

每次仍只生成一份候选。与正向比较时沿用相同父源和 ROI，以新 run ID、previous 和实际 observation 提出另一份提案；采用一份有实际显示收益、颜色层次与保护区域均可接受的输出。首次添加亮度蒙版时保持曲线种类、D、SP、HP 和两个饱和度保护阈值一致，再独立评估蒙版影响。SP/HP 控制曲线形状，不能替代现有 `saturation_protect_start/end`；最小值蒙版、保护矩形和主体空间权重继续适用，零权重像素严格不变。

以主体鲜艳度和最终显示效果评估收益，高饱和度统计继续只作诊断。Siril 1.4.4 的部分精确等通道色样在正、逆变换中均可能偏色；回归与报告保留色相诊断和实际材料，沿用逐 ROI 审查，不增加自动色相硬门，也不能把技术通过写成视觉通过。旧策略不授权逆向候选，旧配方、helper、命令权限与证据指纹保持原样回放／finalize。

正文与完整 SSF 只维护在本文件；Stage 8/10 仅说明执行位置。新 provenance 绑定本文件完整 SHA-256 为唯一 `primary`，无需绑定 Stage 10 primary 或 Stage 8 supporting。其他确实用于决策的材料可按原规则记录为 supporting；旧会话保持原冻结路径、角色和指纹，见 [兼容说明](../session-contract.md#文档指导版本)。

<a id="color-finish"></a>
## color.finish


<a id="color-finish-适用条件"></a>
### 适用条件

只用于已审查接受的 nonlinear 三通道科学父源的轻量显示调色。它不增加光度真实性；无明确改善时跳过。

`rmgreen` 是 SCNR 类绿色抑制，只在宽带校色或窄带映射的依据与结果已经审查、且实际像素仍有
可分离的残余绿色偏色时考虑。它不能代替 `color.calibrate`，也不能修补错误去拜耳、未知通道来源
或失败的校色；这些情况按 [排查参考](../troubleshooting.md) 验证，证据不足时跳过绿色抑制。
双窄带/窄带中的 OIII 青绿色及 SHO 绿色可能来自真实信号或选定 palette，不能仅因画面偏绿就抑制。

<a id="color-finish-参数"></a>
### 参数

- identity：跳过，不创建 run；
- saturation：`satu AMOUNT 1 6`，`0<AMOUNT<=0.20`；
- rmgreen：`rmgreen 3 AMOUNT`，`0<AMOUNT<=0.20`。

默认只选一个操作，不把 saturation 与 rmgreen 叠加到同一协议运行。

<a id="color-finish-ssf-知识关系"></a>
### SSF 知识关系

是否调色及单一操作参数来自当前 nonlinear 父源、用户目标和实际像素证据；本共用文档 `references/shared-protocols/color-finish.md` 是 SSF 唯一的 primary
协议正文，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `satu/rmgreen` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定
流水线。逐字采用本节已展开变体可记录 manual lookup `not_needed`；其他选项必须查询原文。

<a id="color-finish-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
icc_assign sRGB
stat main
savejpg "/abs/session/previews/100-color-finish-parent" 95
satu 0.10 1 6
stat main
save "/abs/session/artifacts/100-color-finish" -chksum
savejpg "/abs/session/previews/100-color-finish-candidate" 95
close
```

<a id="color-finish-审查与回退"></a>
### 审查与回退

一次仅加载声明父源，执行一次 saturation 或 rmgreen，保存一次科学主产物；变换之后不得切换父源或继续变换。
分别声明科学 FITS 与两个 JPEG 的 --expect，并指定科学 --primary-output。receipt 的 color_finish 记录实际命令及参数。

必须实际打开同一 run 的直接父源和候选 JPEG，并将两份哈希绑定进 review；用固定背景、目标和星区测量核对科学裁剪、颜色过渡、背景中性、星色和色噪。
过饱和、绿色结构误删或改善不明确时 reject，保留父源；不能用全图颜色均值证明增色改善。
完整后期任务即使跳过，也在最终 delivery review notes 中明确记录原因。

非线性预览前必须使用原生 `icc_assign sRGB` 标记显示约定；只赋予 profile，不转换科学像素。
父源和候选使用同一 TRC，保存的 FITS 保留该标记，避免重开后重复 Gamma。已有其他明确色域证据时停止并核对，不能强行改标。

<a id="color-finish-local-saturation-on-a-trusted-starless-branch"></a>
### 可信无星分支的局部饱和度

在 Stage 8 可选 tone.adjust 之后、stars.recompose 之前评估。父源必须是已审查接受的 nonlinear RGB FITS primary，且属于可信、已接受的分星分支。目标颜色无明确收益或没有可审查的有序蒙版时跳过；分位分布重叠先按下方鲜艳曲线的中位亮度回退评估，不直接视为无法增色。该变体不识别天体、不恢复物理颜色，也不修改星层。采用后通常跳过 Stage 10 同目的增饱和，仍评估最终颜色与星点。

#### 来源、参数与算子

`# local-saturation:` 必须是恰好一个严格 JSON 对象，六个字段全部为有限数值：`amount`、`low_start`、`low_end`、`high_start`、`high_end`、`sigma`。拒绝重复键或声明、未知字段、布尔值及非有限值。`amount` 为 (0, 0.20]，`sigma` 为 0.5–16 像素；满足 `0 <= low_start < low_end <= high_start < high_end <= 1`。边界依据当前父源的背景、目标和高光证据选择，没有固定默认边界；示例仅展示语法。

L 是加权显示域亮度，V 是 RGB 最大通道值，可保护单通道高光。smoothstep 控制暗部淡入与高光淡出；Gaussian 平滑后与原始蒙版取较小值，保留显式排除的像素。关闭原生 satu 的全局背景阈值，仅由显式蒙版选择贡献，并对所有 RGB 通道使用同一权重。`-nosum` 防止曝光关键字累加，不缩放像素；禁止 `-rescale`、裁剪或归一化最终混合。

<a id="color-finish-local-ssf-skeleton"></a>
#### 局部饱和度 SSF 示例

```ssf
requires 1.4.4 1.5.0
set32bits
# local-saturation: {"amount":0.08,"low_start":0.03,"low_end":0.08,"high_start":0.65,"high_end":0.90,"sigma":2}
load "/abs/session/artifacts/085-tone.fit"
icc_assign sRGB
stat main
savejpg "/abs/session/previews/088-local-saturation-parent" 95
split "/abs/session/artifacts/088-r" "/abs/session/artifacts/088-g" "/abs/session/artifacts/088-b"
pm "0.2126 * $artifacts/088-r$ + 0.7152 * $artifacts/088-g$ + 0.0722 * $artifacts/088-b$" -nosum
save "/abs/session/artifacts/088-luminance" -chksum
pm "max($artifacts/088-r$, max($artifacts/088-g$, $artifacts/088-b$))" -nosum
save "/abs/session/artifacts/088-peak" -chksum
pm "(min(1, max(0, ($artifacts/088-luminance$ - 0.03) / 0.05))^2 * (3 - 2 * min(1, max(0, ($artifacts/088-luminance$ - 0.03) / 0.05)))) * (1 - min(1, max(0, ($artifacts/088-peak$ - 0.65) / 0.25))^2 * (3 - 2 * min(1, max(0, ($artifacts/088-peak$ - 0.65) / 0.25))))" -nosum
save "/abs/session/artifacts/088-mask-base" -chksum
gauss 2
save "/abs/session/artifacts/088-mask-smoothed" -chksum
pm "min($artifacts/088-mask-smoothed$, $artifacts/088-mask-base$)" -nosum
save "/abs/session/artifacts/088-mask" -chksum
icc_assign sRGB
savejpg "/abs/session/previews/088-local-saturation-mask" 95
rgbcomp "/abs/session/artifacts/088-mask.fit" "/abs/session/artifacts/088-mask.fit" "/abs/session/artifacts/088-mask.fit" -out=/abs/session/artifacts/088-mask-rgb -nosum
load "/abs/session/artifacts/085-tone.fit"
satu 0.08 0 6
save "/abs/session/artifacts/088-saturated" -chksum
pm "$artifacts/085-tone$ + $artifacts/088-mask-rgb$ * ($artifacts/088-saturated$ - $artifacts/085-tone$)" -nosum
icc_assign sRGB
stat main
save "/abs/session/artifacts/088-local-saturation" -chksum
savejpg "/abs/session/previews/088-local-saturation-candidate" 95
close
```

通过 `--expect` 声明全部十一份科学产物（三个 split 通道、亮度、最大通道、原始/平滑/最终/RGB 蒙版、增饱和中间图、最终候选）和三份 JPEG；最终混合 FITS 为 `--primary-output`。所有写入路径不同，只使用精确父源和本 run 图层。仅允许一次 saturation，不允许 rmgreen 或额外传递。完整展开骨架可记录 manual lookup `not_needed`，变体仍按手册查询合同处理。

#### 验证、审查与回退

冻结 `local_saturation=luminance_peak_mask_v1`。receipt 保留 `color_finish` 命令表，并绑定 `local_saturation` 参数、来源 SHA、图层角色及父源/蒙版/候选预览。`processing_checks.local_saturation` 核验科学几何、WCS、曝光保留、蒙版范围、推导与混合误差（1e-6 容差）、排除像素变化及各通道成对继承/新增端点计数。端点跨界是风险证据，不证明信息截断，也不构成新接受阈值；测量不接受视觉门。

review 必须绑定不可变 run receipt 和三份直接 JPEG。检查蒙版覆盖、固定天空/目标/亮核区域、颜色连续性与噪声；收益不明确 reject，证据缺失 uncertain。交付父源预览只能是混合候选预览，不能是调整前图或蒙版。合星后重新检查目标颜色、星色与光晕。失败保留父源和诊断产物；历史策略仅回放/finalize，执行新变体须新建 session。


<a id="vivid-color-curve"></a>
## 默认鲜艳风格的蒙版 GHS 饱和度曲线

新会话 `--style vivid` 默认评估主体颜色改善并保留原色相。可信、已接受的 nonlinear RGB FITS 无星主体优先在 Stage 8 的 tone.adjust 之后评估；没有可信无星图时，在 Stage 10 非线性降噪、亮度调整之后评估含星父源并检查星色、光晕。已采用增色时先说明当前剩余目标；目标已完成就跳过，不能仅因谱系出现过增色而拒绝新的、有依据的剩余目标。natural/balanced/artistic 仍由显式目标启用，不改写 session 风格。mono 跳过 RGB 曲线；unknown 保持原输入门。

<a id="unmasked-first-color"></a>
### 先评估无掩膜，再决定保护

本节沿用的“无掩膜”名称仅指 `mask-mode none` 不使用亮度蒙版；新保色策略始终应用饱和度保护，首次候选也须检查实际保护蒙版。

新会话冻结 `color_evaluation=unmasked_first_v1`。Agent 选择当前可信 ROI，显式提出剩余目标、原生 GHS 强度 D 和参数理由；helper 每次只运行一个实际候选，不自动搜索强度、补默认目标或填写接受结论。使用 `ght -sat -clipmode=rgbblend`，保持 `B=0, LP=0, SP=0, HP=1`，不叠加 satu/rmgreen、亮度调整、独立 RGB 通道或 palette 替换。D 使用原生合法范围 `0 < D <= 10`，这不是推荐强度范围；保留足以往返原值的数值精度。亮度整形仍属于 tone.adjust。

先查看当前科学父源与已接受的上游结果，建立现有 `starun-siril.regions.v1`：reference 绑定当前 SHA、宽高，预览左上、零基、半开矩形。至少有一个 faint 主体保护区；可信背景、亮部及含星图的代表星区按实际观察加入。无可信天空时明确记录不可判断，不能从四角、低亮度或全图统计伪造背景；无掩膜候选不要求天空与主体的亮度分布可分离。

```bash
"$STARUN_PYTHON" /abs/skill/scripts/deep_sky_siril_color.py \
  --session /abs/session --source /abs/session/artifacts/current.fit \
  --regions /abs/session/reports/color-regions.json --run-id 088-color-probe \
  --target-gain 0.08 --strength 0.42 --mask-mode none \
  --saturation-protect-start 0.65 --saturation-protect-end 0.90 \
  --goal "Improve the remaining shell color contrast" \
  --parameter-reason "One explicit proposal based on the current parent and ROI comparison"
```

示例数值必须按实际材料替换，不作为默认起点。`target_gain` 是 faint ROI 的 HSV 显示域饱和度中位数相对增益，`0.08` 表示增加 8%，不是增加 8 个百分点或 D。数值未达时披露差距，由实际剩余视觉目标与保护审查决定是否采用，不能伪称数值达标。目标为零时仍验证来源与 ROI，保存 skipped 报告，不生成科学候选、蒙版或 identity run；此时不要求强度和执行理由。执行时缺少目标、D、剩余目标或参数理由直接拒绝。

无掩膜试跑在 `reports/RUN_ID/trial/` 保留原生科学候选和同显示约定的父源／候选 JPEG，另保存原分辨率 ROI 对照 PNG、SSF、日志、配置及来源／ROI／运行时指纹。这些科学文件仅作诊断，不能成为通用科学父源。PNG 只作显示裁剪，不改写科学 FITS。区域报告记录饱和度、色相、亮度、逐通道中位数、负值、色度 MAD 与端点；色度 MAD 不是已分离的噪声测量，统计量不证明真实颜色层次。近中性区域的相对增益标为不可测，报告绝对变化；高饱和度作为风险提示，不以单个中位数替代实际审查。

试跑配置在启动前和 Siril 成功退出后按类型核对六个声明值：core.force_16bit=false、core.check_updates=false、core.fits_save_icc=true，以及 gui.icc_autoassignment=0、gui.use_scripts_repository=false、gui.use_spcc_repository=false。允许正常序列化产生的顺序、空白、等价布尔写法及额外项变化；缺键、重复、非法类型或值变化拒绝，DEFAULT 不能补齐必需键。退出后解析检查与哈希使用同一份实际配置，随后冻结完整文件；正式执行与回放仍拒绝任何后续配置变化。当前策略与接口不变，失败目录保留且不补写成功报告；原会话用新 run ID 重试。精确旧 helper、完整实现集合与受影响文档指纹仅供既有证据回放，不改写旧报告或 review，也不接受未知或混搭绑定。

Agent 根据实际对照决定：主体收益明确且其余区域可接受，可采用无掩膜结果；主体有收益而背景、亮核或星色受损时，再提出亮度蒙版；无收益、不可接受的损伤或无法判断时保留可信父源。试跑增加新的黑白端点时不能采用，但保留诊断供有依据的修订。

### 有观察依据的蒙版或强度修订

修订使用新 run ID，显式给 `--previous /abs/session/reports/088-color-probe/color-parameters.json` 与 `--observation "Actual comparison shows unwanted background color"`。父源与 ROI 绑定必须相同；另一候选已存在却没有观察绑定时拒绝。第一次加入亮度蒙版必须保持前次 D、s0 和 s1，隔离亮度蒙版效果；更改这些参数作为单独的有观察依据的修订，不执行固定网格。

`--mask-mode luminance` 才推导现有亮度／高光蒙版。先尝试天空 p99 到 faint p25；重叠时按已有 benefit_first 策略回退到有序中位数，保留高光淡出、sigma=2 平滑后与原始蒙版取 min 的配方。蒙版要求可信 background、faint，每区至少 64 像素，天空区不与其他区重叠。不交换无序端点或填造间隔。仍不可分离时只记录蒙版路线 unavailable，保留先前真实无掩膜材料供审查，不能把它解释为增色无收益。

新策略的两种模式均使用 `C = A + M × (E_D(A) − A)`，其中 `M=min(M0,Gaussian_2(M0))`。无亮度蒙版时 `M0=Wsat`；亮度模式将亮度、高光及可选颜色权重共同乘入 M0。所有候选从同一可信父源 A 生成，不累积未接受候选。修订绑定前次报告与实际显示材料。青色选择器仍要求亮度模式；无亮度蒙版候选只按饱和度保护选择贡献，不能把它称为青色专属增强。仅旧策略的无掩膜配方保留 `U = E_D(A)`。

### 正式执行与审查

helper 的 `script_text(..., report=...)` 可生成现有单协议 SSF 骨架；正式运行仍由 `run` 执行，Agent 绑定本文件为唯一 primary 并编写现有 provenance。手写 SSF 也必须使用同一 `# color-curve:`：无亮度蒙版字段为 `D, mask_mode=none, mask_policy=vivid_preservation_v1, saturation_protect_start, saturation_protect_end, parameters`；亮度蒙版除新增三个保护字段外为 `D, mask_mode=luminance, low_start, low_end, high_start, high_end, sigma, parameters`，青色模式另加 `selector=cyan`。parameters 指向不可变的本次报告，声明、原生命令、科学父源、ROI、分支与报告必须一致。

新保色策略的两种模式均生成十一份科学图层和父源／蒙版／候选三个 JPEG，原生 GHS 执行一次，再用 PixelMath 和 `-nosum` 混合；禁止截断或重新归一化。实际 `--expect` 列举全部输出，科学候选为 primary。正式像素与保留的原生试跑按现有 `1e-6` 容差核对；几何、有限值、WCS、曝光元数据、蒙版闭合与原端点门继续有效。旧策略无掩膜配方保留一份科学 FITS 与父源／候选两个 JPEG。含空格的 `-out=...` 整个参数须加双引号。

新候选 receipt 标记 `color_evaluation=unmasked_first_v1`；无掩膜记录 color_curve，蒙版继续记录 local_saturation，processing_checks.color_curve 保存实际区域诊断。review 的 `color_assessment` 绑定参数诊断，填写目标是否达到、实际新增收益、逐 ROI 可接受性、观察与采用理由。接受要求目标达到、收益 observed、全部绑定区域 acceptable，并通过既有质量门；数值有增益不能抵消主体颜色层次、背景、亮部或星色退化，uncertain 不采用。父源／候选统一显示、原分辨率 ROI 面板、参数／ROI／试跑 SSF／日志／配置、必要蒙版及前次比较必须实际打开并绑定；仍遵守 32 份审查材料上限，超限须缩小有依据的材料范围，不能静默丢弃。

Stage 9 从已接受的 A′ 比较原 F 基线与显式星点曲线 Fs，重放同一对线性 full/starless；合星后重新检查颜色、星色及光晕。上文普通 satu/rmgreen、旧 local-saturation、红色局部亮度与既有 palette 选择保持原适用边界。

### 历史兼容

无新标签的旧鲜艳曲线保留 `masked_saturation_ghs_v1`、原 D 网格、默认目标、亮度蒙版与旧审查字段，仅按已存在证据回放／finalize，不升级报告、review 或结果。修改前完整策略、56 份参考文档与实现／helper 指纹已登记。新执行不回落到旧选参默认值；旧会话需继续处理时按 session 合同从可信输入新建会话。

<a id="display-cyan-selector"></a>
### 可选的青色显示选区

颜色 helper 保留 `--selector all|cyan`，cyan 仅用于可信、已接受的 nonlinear RGB starless 分支。新执行先完成无掩膜比较，再以 `--mask-mode luminance --previous ... --observation ...`、相同显式 D、目标和参数理由评估青色选择器。例如 `--selector cyan --target-gain 0.15` 是专属 faint ROI 的相对增饱和目标，不是增加 15 个百分点或直接把 G/B 乘以 1.15。natural 等风格保持原冻结值，选择器不因 vivid 自动启用。

在当前父源上建立专属 regions.v1：全部 faint ROI 都是实际待增强青色结构，其他颜色区域使用独立诊断区域；不得把红壳加入青色最低增益目标后再忽略其失败。报告记录每区 selector_pixels；任一目标不含选区时跳过。仍报告完整目标 ROI 的饱和度中位数相对增益，不另换自动评分或只挑改善像素；正式采用由实际区域审查决定。

固定选择权重为 `S=smoothstep(clamp((G+B-2R)/max(G+B+2R,1e-6),0,1))`，smoothstep(t)=t²(3−2t)。它只表示显示颜色主导程度；不提取纯 OIII，不证明谱线比。原始蒙版 M0 为既有亮度/高光蒙版乘 S，最终为 `min(M0, Gaussian_2(M0))`，固定 sigma=2。选区外和暗部/高光排除像素不变；其余背景仍实际测量，不能宣称零底噪风险。

正式 `# color-curve:` 在原字段之外仅增加 `selector:"cyan"`，必须与参数报告和试跑一致。不开放阈值、羽化宽度或独立通道增益。新执行使用同一显式单候选与实际审查合同；历史网格与筛选仅用于旧证据回放。达到饱和度目标不保证亮度绝对不变，仍审查色相、亮度、色噪与颜色边界。采用后 Stage 10 不重复同目的增色。


## 增色前色度降噪

新会话在无星主体完成主要亮度整形后、color.finish 前评估[仅色度降噪](denoise-nonlinear.md#pre-color-chroma-denoise-chroma_bilateral_v1)。有可信红绿斑、蓝紫斑等证据才试跑；无收益、结构退化或无法判断时保留父源。此变体区别于整体 NL-Bayes，原星层及拉伸链保持冻结，合星后仍须复核实际颜色。

### Stage 8 主体空间蒙版 GHS 变体

新 `subject_mask=reviewed_polygons_v1` 会话可在已接受 nonlinear RGB starless 父源上显式增加 `--subject-mask reports/079-mask/subject-mask.json --high-start 0.65 --high-end 0.90`。数值仅为格式示例，须根据当前父源选择。轮廓准备、文件合同与审查见 [Stage 8 主体空间蒙版](../stages/stage-08-starless-enhance.md#subject-spatial-mask)。首版仅接入当前 GHS 路线，不扩展旧 satu、red local-tone、含星 Stage 10 或 cyan 选择器。

空间变体使用 `--mask-mode none --selector all`；none 仅表示没有亮度选区。首次可直接评估空间候选，不要求先生成全图候选，也不推导天空／主体亮度分离。仍显式声明 D、s0/s1、剩余目标和理由，并保留 target-gain／region-goals、previous／observation 与完整 ROI 统计。禁止与 luminance 模式或颜色选择器叠加。没有 subject-mask 时不能单独声明 high-start/high-end，原接口与像素配方保持不变。

`# color-curve:` 在现有 none 字段上新增 `subject_mask={path,sha256,size}`、`high_start`、`high_end`。高光淡出取父源 max(R,G,B)，满足 `0<=high_start<high_end<=1`；P0=高光淡出×Wsat，preserve 矩形继续置零，P=min(P0,Gaussian_2(P0))。最终 M=Mspace×P，再用 `C=A+M×(E_D(A)−A)` 混合。空间权重不参加 Gaussian，不改变原 GHS 算子，不强制低亮度门。零有效覆盖记录 skipped，无 identity run。正式运行与回放核对同一蒙版报告、来源和全部辅助证据；空间资产不可作为科学父源。review 实际查看空间/叠加并排图、最终权重和原有全部 ROI，不能只在被选中像素上重定义收益。
