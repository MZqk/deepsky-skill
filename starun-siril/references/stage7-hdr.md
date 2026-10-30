# Stage 7 · 可选 HDR 合同

新会话的[伪影重点区审查](stage7-artifact-focus.md)覆盖 gentle、faint 和最终混合图，尤其检查已登记的核边界与蒙版过渡；两支共享追加清单，不重置科学 ROI 或额度。

默认按[分步前缀反馈](stage7-stepwise.md)先完成 gentle、再完成 faint，Agent 分别声明分支结束并按 gentle 实际域选蒙版；初始化不填写未来因子。仅显式 `--batch` 才按下文整链 HDR 方式，最终融合验收与合星合同一致。

只在实际选择本变体时读取；与[当前 Stage 7 主文档](stages/stage-07-stretch.md)共同使用，SSF 以主文档为唯一 primary，本文件为 supporting。 使用 `protocol_reference_bindings('stretch', variant='hdr')` 获取完整绑定；手写 SSF 同样需要该附文的 supporting 路径与 SHA-256。

<a id="masked-dual-hdr"></a>
<a id="stretch-masked-dual-hdr"></a>
HDR 内部温和或强分支可只有一步，但两支均须完成并通过最终融合验收；不能将其中一支独立采用为单步主体。

### 双传递蒙版 HDR

#### 适用条件与来源

新会话冻结 `stretch_hdr=morphology_selected_v1`。Agent 根据实际父源形态、动态范围、固定 ROI 和处理目标选择双分支蒙版 HDR，可以首次提案即使用，无需先证明单链冲突或试跑普通链。它覆盖默认链对应的五类形态，不按天体名称自动触发；M42、M31 仅为亮核与弱外缘的典型案例。优先已接受的线性 starless 父源，也支持 mono 和含星线性父源；含星蒙版须额外检查星晕。两支都从精确同一已接受线性源开始。HDR 只使用显式 MTF/Asinh/GHS，GHS 五参数全部给出；不引入 autostretch、独立通道操作、自动选参或额外协议。

| 当前主要形态 | 温和分支的保护重点 | 强拉伸分支的显示目标 |
|---|---|---|
| emission_nebula | 明亮云气、嵌入核心及其连续过渡 | 弥漫发射、弱细丝和外缘 |
| high_dynamic_range | 亮核内部层次、颜色及核周过渡 | 弱包层与外延云气 |
| galaxy_halo | 星系核球、盘面梯度和尘埃带 | 外盘、旋臂与外晕 |
| dust_reflection | 明亮反射区、亮星周围与平滑尘埃过渡 | 弱反射光、暗带及尘埃延伸 |
| star_cluster | 密集中心的星点分离、星色及光晕 | 稀疏弱星与外围成员；优先含星路线 |

所选形态、实际预览与全部 ROI 声明仍须合格；可见亮核继续声明 core 保护，无亮核时保留真实 core_absence。分支因子和蒙版取自当前科学像素及目标，不采用按天体名称固定的强度。原输入已饱和丢失的层次不能靠双分支恢复。

显式使用 `init-request --batch --mode exception`，在 goals.v3 的 `hdr` 冻结 gentle/faint 两条原生命令链及 mask；`operators` 保留显式提案与区域保护声明，不先执行其普通 solve。将返回的 strategy.json 绑定为 SSF 的 `# stretch-strategy:`，另绑定下方最终保护请求，再按已有原生骨架执行 `run --protocol stretch`。完整 HDR 首次原生运行占一次科学试跑，两支、蒙版和同源 full 重放不另计；修订共享三次总额度。

首次 revision=null；后续仍从同一线性源创建新计划，revision.parameters 可绑定上一 HDR 的真实运行回执，evidence 引用该回执的原生日志、科学产物或预览。程序验证来源、manifest、脚本与分支绑定，并记录分支及蒙版变化；不要求单链失败或保护冲突。已有历史会话按原冻结规则回放/finalize，不迁移证据。

相同 shadowsclip 下，autostretch targetbg=0.25 比 0.08 更亮/更强，不是温和设置；二者都不能证明亮核裁剪或保护。分支参数依据当前 ROI，并解释两支各自的结构目标；以下只展示合法语法。

#### 参数与算子

严格声明有限 JSON 数值 `mask_low`、`mask_high`、`sigma`，满足 `0 <= mask_low < mask_high <= 1` 和 `0.5 <= sigma <= 16` 像素。在**温和分支输出域**计算 `t = min(1, max(0, (L-mask_low)/(mask_high-mask_low)))`，再令 `M = gauss(t^2*(3-2*t), sigma)`。背景/IFN 应取强拉伸分支权重，亮核取温和分支权重；原始亮度图不是合格蒙版。宽过渡、模糊和权重不能保证无光晕、亮度反转或裁剪，须检查固定 ROI。

Siril 1.4.4 PixelMath 要求通道数一致。RGB 从原生温和 RGB split 计算既有加权亮度，保存 mono 蒙版，再用 `rgbcomp` 复制到三通道；不能直接混合 RGB 与 mono 蒙版。绝对路径含空格时引用**整个** `-out=...` token，只引用等号后的路径会生成带字面引号的文件名。只有蒙版允许 min/max，最终凸组合不裁剪或重标度；HDR 的 PixelMath 与 RGB 合成都用 `-nosum`，避免重复累加曝光时间。

#### SSF 示例

完整 RGB 骨架写九份科学 FITS（温和图、其三个 mono 通道、亮度、强拉伸图、mono/RGB 蒙版和主产物）及四份直接 JPEG（温和/强拉伸/蒙版/候选）。全部十三份列为 `--expect`，`070-hdr.fit` 为 `--primary-output`。

```ssf
requires 1.4.4 1.5.0
set32bits
# hdr-stretch: {"mask_low": 0.15, "mask_high": 0.75, "sigma": 2}
load "/abs/session/artifacts/060-starless.fit"
mtf 0 0.2 1
icc_assign sRGB
save "/abs/session/artifacts/070-gentle" -chksum
savejpg "/abs/session/previews/070-gentle" 95
split "/abs/session/artifacts/070-gentle-r" "/abs/session/artifacts/070-gentle-g" "/abs/session/artifacts/070-gentle-b"
pm "0.2126 * $artifacts/070-gentle-r$ + 0.7152 * $artifacts/070-gentle-g$ + 0.0722 * $artifacts/070-gentle-b$" -nosum
save "/abs/session/artifacts/070-luminance" -chksum
close
load "/abs/session/artifacts/060-starless.fit"
mtf 0 0.06 1
icc_assign sRGB
save "/abs/session/artifacts/070-faint" -chksum
savejpg "/abs/session/previews/070-faint" 95
close
pm "min(1, max(0, ($artifacts/070-luminance$ - 0.15) / 0.6))^2 * (3 - 2 * min(1, max(0, ($artifacts/070-luminance$ - 0.15) / 0.6)))" -nosum
gauss 2
icc_assign sRGB
save "/abs/session/artifacts/070-mask" -chksum
savejpg "/abs/session/previews/070-mask" 95
rgbcomp "/abs/session/artifacts/070-mask.fit" "/abs/session/artifacts/070-mask.fit" "/abs/session/artifacts/070-mask.fit" "-out=/abs/session/artifacts/070-mask-rgb" -nosum
pm "$artifacts/070-gentle$ * $artifacts/070-mask-rgb$ + $artifacts/070-faint$ * (1 - $artifacts/070-mask-rgb$)" -nosum
icc_assign sRGB
save "/abs/session/artifacts/070-hdr" -chksum
stat main
savejpg "/abs/session/previews/070-hdr" 95
close
```

mono 变体直接将温和图作为亮度，不生成 split/RGB-copy 中间层；声明全部四份 FITS 和四份 JPEG，两次 load 仍绑定同一线性父源。

```ssf
requires 1.4.4 1.5.0
set32bits
# hdr-stretch: {"mask_low": 0.15, "mask_high": 0.75, "sigma": 2}
load "/abs/current-linear-parent.fit"
mtf 0 0.2 1
icc_assign sRGB
save "/abs/session/artifacts/070-gentle" -chksum
savejpg "/abs/session/previews/070-gentle" 95
close
load "/abs/current-linear-parent.fit"
mtf 0 0.06 1
icc_assign sRGB
save "/abs/session/artifacts/070-faint" -chksum
savejpg "/abs/session/previews/070-faint" 95
close
pm "min(1, max(0, ($artifacts/070-gentle$ - 0.15) / 0.6))^2 * (3 - 2 * min(1, max(0, ($artifacts/070-gentle$ - 0.15) / 0.6)))" -nosum
gauss 2
icc_assign sRGB
save "/abs/session/artifacts/070-mask" -chksum
savejpg "/abs/session/previews/070-mask" 95
pm "$artifacts/070-gentle$ * $artifacts/070-mask$ + $artifacts/070-faint$ * (1 - $artifacts/070-mask$)" -nosum
icc_assign sRGB
save "/abs/session/artifacts/070-hdr" -chksum
stat main
savejpg "/abs/session/previews/070-hdr" 95
close
```

#### 验证、审查与回退

CLI 逐项验证原生操作，允许等价数字格式；扩大命令白名单不授权任意 PixelMath。receipt 的 `hdr_transfer` 记录两条有序链、蒙版声明/产物角色和四份预览路径；空间变换使用 `transfer_chain=[]`，不伪装成平坦链。只读 `processing_checks.hdr` 核验有限像素、几何、蒙版范围、等通道复制和凸组合误差，数值容差保持 1e-6。输出沿用 receipt 指纹；完整骨架按既有 manual lookup 规则，未展开链或歧义查询冻结原文。

实际打开四份直接预览并绑定 review，保留科学 ROI 测量与视觉门。检查亮核纹理、微弱结构、噪声、过渡环、星晕与颜色；强拉伸分支须提高主体/背景可分性，温和分支保留核层次，全图更亮本身不是收益。最终混合同时核对收益与代价，两支都保留科学端点；按既有规则 reject 或 uncertain。两支同时裁掉的结构和源中丢失的核细节不能靠混合恢复。

接受的 HDR 成为 nonlinear primary；Stage 8/tone.adjust 只修改 A_prime。Stage 9 在 full 和原始 starless 上重放两链，使用**同一冻结蒙版**及 RGB 副本；禁止分别派生蒙版，也不把 HDR 解释成最后一支链。

### HDR 最终混合保护

HDR 维持既有显式双链及共享蒙版，不自动选择或排序。增加一条 `# stretch-protection: {"path":"reports/hdr-protection.json","sha256":"<request-sha256>"}`。绑定的 JSON 包含 `schema=starun-siril.stretch-protection.v2`、source、full_source（绑定或 null）、regions、profile、targets、core_absence 和最终 constraints；来源、区域及 full_source 使用 path/SHA-256 绑定。该请求保护最终混合图，不代表某条 HDR 分支的曲线。

分星路线在主体最终预览之后追加：load 精确 full_source，重放冻结 gentle 链并另存 FITS；重新 load full_source，重放 faint 链并另存；PixelMath 用同一份原冻结蒙版混合这两图，保存 full mixed FITS，分配 sRGB 并保存 JPEG。显式声明新增四份产物。解析器要求精确的同蒙版重放；最终两份混合图接受端点与区域复测，不因被保护的 faint 分支单独溢出而拒绝。审查另绑定保护请求、ROI 与 full 对照。Stage 9 继续原双传递的有符号加法残差，Stage 8 按冻结策略检查方法资格：新会话 HDR 后仅允许小波评估，CLAHE 仍禁止；历史会话保持原 HDR 互斥。


## Stage 8 的 HDR 谱系互斥

已采用的双链蒙版 HDR 会保留在当前科学谱系中，后续 tone.adjust、调色或降噪不能解除 CLAHE 互斥。需要 CLAHE 时从另一个已接受的普通拉伸分支重新评估；新会话 local_contrast_hdr=wavelet_only_v1 可在 HDR 后评估小波，仍须独立审查新增收益，不自动换分支或叠加 CLAHE。规则见 [Stage 8 纹理门](stages/stage-08-starless-enhance.md#local-contrast-texture-and-hf-gates)。

