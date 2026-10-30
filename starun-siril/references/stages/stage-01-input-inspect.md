# Stage 1 · 输入准备与证据冻结

完整处理先 init 冻结已校准、配准并叠加的只读 master，再执行 input.inspect。有效输入状态或通道来源未知时只完成检查与审查，随后停止依赖处理；获得可靠来源证据后通过 confirm-input 在同会话确认，更旧的只读执行策略仍需创建新 session。诊断预览不作为科学父源。

本阶段集中维护协议正文、参数、SSF 骨架及专属审查。provenance 按协议到阶段映射，以首份阶段文件为唯一 primary、其余为 supporting，绑定完整文件 SHA-256。通用质量门见 [quality](../quality.md)。

## 形态保护

按 [目标形态参考](../target-morphology-review.md) 选择观察区域，不由预览反推输入状态或通道来源。

- **深空发射星云 (如 M8, M42, NGC6888)**：
  - 极暗线性数据中，Hα 强发射可能在 autostretch 预览中呈现全红或微红高光，切勿误判为严重白平衡损坏；
- **高动态范围星系与亮核天体 (如 M31, M42)**：
  - 记录亮核与微弱外缘的可辨性，供后续按 stretch 协议选择参数；预览饱和不能证明科学像素已截断，也不触发额外 HDR/rolloff 操作。
- **密集星团与星野 (如 M45, NGC 6530)**：
  - 记录恒星形态、重复星和锐化伪影等可见问题；成因不明时不推断机内处理历史。

---

## 故障定位

结合 [排查参考](../troubleshooting.md) 区分观察与已确认原因。

| 故障现象 | 潜在原因 | 排查与处置对策 |
|---|---|---|
| **Direct 预览一片全黑** | 当前显示下信号不可辨，尚不能确定图像域 | 用 autostretch 辅助观察；状态仍以冻结证据为准，无证据时保持 `unknown`。 |
| **Autostretch 严重发白/死白** | 当前显示传递不适合该数据，或存在异常像素 | 对照 direct 与科学测量，记录可读性限制；不能据此把输入设为 `nonlinear`。 |
| **状态无法确信为线性** | 缺乏 FITS 头或拍摄参数记录，且无法从受支持智能望远镜堆栈特征确证 | 保持 `input_state = unknown`，停止依赖处理；取得明确未拉伸状态的真实来源证据后，用 `confirm-input` 在同会话确认。堆栈完成本身不证明线性。 |
| **输入缺少 WCS 天体坐标** | 来源或保留情况待确认 | 记录限制，按 Stage 4 的来源保留与在线 Gaia 默认路径处理；明确 offline 时才要求本地星表。 |

<a id="input-inspect"></a>
## input.inspect


<a id="input-inspect-目的"></a>
### 目的

建立可读输入证据，不解析未知线性状态。完整处理与定向执行的新 session 先运行并审查一次；已有 session 可复用绑定同一输入且已接受的检查记录。纯知识查询与仅生成 session 绑定脚本不执行本协议，模式边界见 [入口](../../SKILL.md#定向模式)。

<a id="input-inspect-输入证据摘要"></a>
### 输入证据摘要

在现有 review notes 中记录与本任务有关的可读元数据及来源：实际尺寸/通道、曝光、滤镜、增益/温度、
WCS、校准与堆栈历史。区分文件内声明、用户记录和本次测量；缺失或冲突明确记为 unknown，不从
文件名补齐，也不把 Header 中单帧的曝光或滤镜声明当作整个堆栈的完整历史。只追问会改变协议
选择的缺项；不要求用户补齐与当前任务无关的采集清单。证据不替代 session 冻结的状态与通道来源。

<a id="palette-route-selection"></a>
### 配色路线判定

先按滤镜、采集记录、真实提取/组合来源和当前通道角色判断可用路线，再记录用户选择；可用不等于已获选择。配色目标与 `natural/vivid/balanced/artistic` 的鲜艳程度独立。默认宽带自然 RGB，窄带映射默认不开启；未显式选择时保留输入配色，不把窄带当作宽带自然 RGB。

| 数据与来源条件 | 用户选择与后续路线 |
|---|---|
| 宽带 RGB | 默认经适用校色的自然 RGB；Stage 4 按证据选择方法，Stage 8 跳过 `color.map`。未完成测光校准时披露限制。 |
| 真实 SII、Ha、OIII 三谱线角色 | 用户显式选择 SHO 或 modified-SHO 后，在 Stage 8 映射；单个三通道 master 必须已具有可核验的通道组合来源。 |
| 原生 Ha/OIII 双窄带 OSC RGB | 用户显式选择 HOO 时，`init --dualband auto` 开启现有 Stage 7 估计分支；默认 `--dualband off` 保留原 RGB 路线。 |
| 已有可信 Ha/OIII 通道组合 | 用户显式选择 HOO 后，沿用 Stage 8 真实通道映射入口；不能从混合 OSC RGB 直接宣称纯谱线。 |
| 已是所选目标映射，或谱线角色证据不足 | 已映射时不重复；证据不足时跳过映射并记录缺项，其他协议仍按各自前置条件评估。 |

沿用 `channel_mode/channel_map` 表示实际输入类型和角色，不把期望输出颜色写入 `channel_map`，不为 OSC 估计填写纯谱线角色。滤镜名称、文件名、色调或预览均不能补齐 SII 来源。只有 Ha/OIII 时不能标为 SHO；本轮不提供双窄带哈勃风格显示或合成 SII。

在现有 input.inspect `review.notes` 中简要记录目标路线、用户显式选择或默认保留、来源证据、当前是否已映射；后续相关 SSF 在 provenance 的 `rationale.applicability/parameter_choices` 复述实际依据。沿用 2000 字符 notes 上限、材料绑定和实际审查，不新增字段或自由文本解析器。

若用户已选择原生 OSC HOO，在 init 时显式传 `--dualband auto`；它仅允许条件合格的候选评估，仍须审查。冻结为 `off` 后改选此分支须另建会话，`confirm-input` 不修改该开关；新建同源会话也不继承旧会话的开启状态。现有会话保持冻结值。

<a id="input-inspect-ai-morphology"></a>
### AI 形态观察与后续评估

执行 input.inspect 的完整处理与定向任务使用当前 Agent 的视觉能力；不引入天体库、别名数据集、新视觉模型或外部识别 API。实际查看 direct/autostretch 预览及必要局部，在现有 `review.notes` 中记录以下内容：

| 记录项 | 观察与限制 |
|---|---|
| 画面主体 | 恒星主导、可见弥散结构、紧致主体或复合视场；只描述实际可见形态。天体名称仅可作为候选，不写成已确认目录身份。 |
| 亮核与微弱外缘 | 记录层次、可辨结构及两者在当前显示下是否兼容；预览饱和不证明科学裁剪。 |
| 恒星与星晕 | 记录密集区、亮星、宽光晕及可能与目标混淆的紧致亮结；内嵌星团与周边非恒星结构共同保护。 |
| 背景可分性 | 说明哪里可能是天空、哪里可能含微弱目标及不可分范围；暗角、低亮度或自动蒙版不证明纯天空。 |
| 限制与评估方向 | 区分可辨观察、疑似判断和无法判断；说明后续要测量或审查什么，不填写未经标定的置信百分比或固定参数配方。 |

只填写实际打开材料后的观察，并沿用 `inspected_materials` 的 SHA-256 与 run receipt 绑定；必要局部沿用现有材料与区域测量方式，不新增形态报告。`notes` 保持 2000 字符上限，优先记录会影响处理选择的内容。`review-check` 仍输出空 verdict、gates 和 notes，供 Agent 实际审查后填写；验证器不解析自由文本，也不判断 AI 识别是否正确。

保持冻结的 `target_name/target_type` 不变。与用户声明不一致时记录冲突及影响，不静默覆盖；不能由视觉补齐线性、滤镜、通道角色、谱线来源、纯天空或 PSF。形态无法判断本身不新增失败门；必要图像审查无法完成时按原规则待审。输入状态或通道模式仍为 unknown 时只完成检查与审查，不解除依赖处理门禁。纯知识查询与仅生成绑定脚本保持原模式边界。

初始观察只提供检查方向。裁切、降噪或换父源后，重新核对 ROI 坐标、来源哈希和实际像素域，不能直接沿用旧亮度统计。后续衔接见 [形态观察与科学选参](../target-morphology-review.md#ai-morphology-and-scientific-measurement)。

<a id="input-inspect-ssf-知识关系"></a>
### SSF 知识关系

输入路径、状态声明和通道证据来自 immutable session 与用户/采集来源；本协议对应的阶段文件是 SSF 的 primary protocol
reference，提供双预览角色和参数化骨架；冻结 Siril 1.4.4 手册提供 `savejpg/autostretch/stat` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定
流水线。逐字采用下方完整骨架可记录 manual lookup `not_needed`；改变命令语义或选项时必须查询原文。

<a id="input-inspect-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/master.fit"
stat main
savejpg "/abs/session/previews/010-input-direct" 95
close
load "/abs/master.fit"
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/010-input-autostretch" 95
close
```

把两个 JPEG 都作为 `--expect`，并保留 `*-input-direct.jpg` 与
`*-input-autostretch.jpg` 的角色后缀，finalizer 会据此选择处理前参考图。不要把 autostretch
预览当作新的科学父源或交付父源；known 路由后续科学父源仍是原 master。`input_state` 或 `channel_mode` 任一为 `unknown` 时，完成本协议的审查后
停止依赖处理；获得可靠来源证据后按 `confirm-input` 正式确认，在同会话继续。`channel_map=null` 不等于 `channel_mode=unknown`。

<a id="input-inspect-审查"></a>
### 审查

known linear 通常使用 autostretch 观察结构，known nonlinear 通常使用 direct。unknown 只选择更能可靠
辨认结构与星点的显示；`background` 与 `color` 可记 `not_applicable`。通道来源无可靠证据时保持 channel
unknown，不从画面颜色推断。两者都不可读时停止。

## XISF input metrics

Full-image input metrics read XISF Image 0, matching the existing Siril import, even when crop masks or
rejection images follow it. The report records the image index, ID and actual image count. Supported
scientific layouts are Gray/RGB, planar/normal, little/big endian, UInt8/16/32 and Float32/64, attached
or embedded, uncompressed or single-stream zlib with optional byte shuffling. Floating bounds must
be explicit, finite and increasing. Measurement units follow those bounds or the unsigned integer
full range; pixels are never clipped, repaired or normalized by their observed maximum.
Unsupported or damaged data remain uncertain with a report warning, without a JPEG fallback.
Input inspection only reports clipping; metrics do not establish linear identity or an accept verdict.
XISF ROI metrics remain unsupported under the existing FITS-only region contract.
