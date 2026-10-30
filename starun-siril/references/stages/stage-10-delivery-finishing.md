# Stage 10 · 最终亮度、轻量调色与交付

使用已审查接受的非线性科学父源，默认最终含星。依次评估可选 restoration.denoise-nonlinear、tone.adjust、color.finish，再独立运行 delivery.render；各项独立 SSF、run 与 review。完整处理必须评估 tone.adjust 与 color.finish，无收益时跳过且不创建 identity run，在最终 delivery review notes 分别记录采用或跳过的理由、实际参数和证据。采用 Stage 8 局部增色后，通常跳过同目的的全图增饱和，仍评估最终颜色。正式交付与 final-selection 规则见 [delivery](../delivery.md)。

最终非线性降噪和亮度整形后、delivery.render 前评估 color.finish；目标为零、已完成、无可信材料或无收益时跳过。Stage 8 已完成的同目的增色不重复；候选与审查按[共用增色合同](../shared-protocols/color-finish.md#unmasked-first-color)执行。

保持 Stage 1 所选且已经接受的配色，最后的亮度和饱和度调整不重选 SHO/HOO、不默认去绿。交付分别披露目标配色、Stage 4 实际校准或中性化状态、星层来源及限制；窄带星点不能标为宽带真彩，SHO/modified-SHO 不能标为自然 RGB。未显式选择窄带映射或已有目标配色时说明保留原因。

本文件维护 delivery.render；非线性降噪、亮度及调色按下方共用正文读取，provenance 绑定各协议唯一 primary 的完整 SHA-256。通用质量门见 [quality](../quality.md)。

<a id="restoration-denoise-nonlinear"></a>
## restoration.denoise-nonlinear

完整定义见[共用非线性降噪协议](../shared-protocols/denoise-nonlinear.md)。Stage 10 保留整体 NL-Bayes；Stage 8 使用独立的增色前色度变体。

<a id="toneadjust"></a>
<a id="tone-adjust"></a>
<a id="适用条件与位置"></a>
<a id="tone-adjust-适用条件与位置"></a>
<a id="参数与选择依据"></a>
<a id="tone-adjust-参数与选择依据"></a>
<a id="ssf-知识关系与骨架"></a>
<a id="tone-adjust-ssf-知识关系与骨架"></a>
<a id="审查与回退"></a>
<a id="tone-adjust-审查与回退"></a>
## 亮度调整（tone.adjust）

在最终非线性降噪后、调色前，按[剩余亮度目标](../shared-protocols/tone-adjust.md#remaining-goal-tone)复核 Stage 7/8 的结果与最终含星画面；已定调且无新问题时跳过，不因进入 Stage 10 而重复调整。完整合同见 [tone.adjust 共用正文](../shared-protocols/tone-adjust.md#tone-adjust)。

<a id="colorfinish"></a>
<a id="color-finish"></a>
<a id="color-finish-适用条件"></a>
<a id="参数"></a>
<a id="color-finish-参数"></a>
<a id="color-finish-ssf-知识关系"></a>
<a id="color-finish-参数化-ssf-骨架"></a>
<a id="审查与回退-1"></a>
<a id="color-finish-审查与回退"></a>
## 轻量调色（color.finish）

在最终亮度整形后、交付前评估；目标为零或已完成时跳过，保留已接受配色。完整合同见 [color.finish 共用正文](../shared-protocols/color-finish.md#color-finish)。

<a id="delivery-render"></a>
## delivery.render


<a id="适用条件-1"></a>
<a id="delivery-render-适用条件"></a>
### 适用条件

当前父源必须是按默认阶段顺序或合法定向模式形成、已经审查接受的 nonlinear 图。默认星点政策要求
含星。unknown session 和 `input.inspect` 的 direct/autostretch 诊断预览不得进入本协议。

<a id="ssf-知识关系-1"></a>
<a id="delivery-render-ssf-知识关系"></a>
### SSF 知识关系

父源资格、星点要求和 JPEG 参数来自 selected lineage、用户选择与实际像素证据；本协议对应的阶段文件是 SSF 的 primary
协议正文，提供交付边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `save/savejpg/load/stat` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定
流水线。逐字采用下方完整骨架可记录 manual lookup `not_needed`；改变命令或选项时必须查询原文。

<a id="参数化-ssf-骨架-1"></a>
<a id="delivery-render-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
fmul 1
icc_assign sRGB
save "/abs/session/artifacts/110-final-candidate.fit" -chksum
close
load "/abs/session/artifacts/110-final-candidate.fit"
stat main
savejpg "/abs/session/artifacts/110-final-candidate" 95
close
load "/abs/session/artifacts/110-final-candidate.jpg"
stat main
close
```

调用 run 时用 --source 绑定当前 nonlinear 科学父源，分别用 --expect 声明科学 FITS 和 JPEG，
并用 --primary-output /abs/session/artifacts/110-final-candidate.fit 选择 FITS。JPEG 仍由 final-selection 的
candidate_image 选择，不能作为科学 primary。新会话冻结 delivery=paired_fits_jpeg_v2。

使用 fmul 1 进行原生 float32 恒等升精度，随后仅保存一次带校验和的科学 FITS，关闭并重开它后导出唯一 JPEG，再重开 JPEG；禁止额外换源、拉伸、
裁剪、调色或像素截断。FITS 必须为 32 位浮点、mono/RGB、有限像素并与 JPEG 尺寸一致。
FITS 父源的保存前后规范化像素在两个 float32 ULP 内一致，保留端点、范围外值和已有 WCS；
TIFF/XISF 只由原生 Siril 转存，不使用 Python 改写科学像素。缺少验证能力时失败，不退化为仅 JPEG。

SSF 不写 outputs/。finalizer 从选中的 JPEG 所属 delivery_export 获取配对 FITS，正式复制为
outputs/final.jpg 和 outputs/final.fit；后者是最终 nonlinear 处理母版，不是原始 master 或线性母版。

<a id="delivery-render-最终审查"></a>
### 最终审查

实际打开最终候选与最终父源预览，五项质量门全部通过才 accept。父源预览必须是生成当前父源的成功
run 中已验证的显示图；已知 nonlinear 原输入作为父源时使用成功 `input.inspect` 的 direct 预览。unknown
不得进入本协议。任何 uncertain 都使用 `review_required`。
最终 review receipt 必须列出三份不同的 session 图像：科学 FITS、对应最终 JPEG 和最终父源预览；`delivery.render` 的
五项门全部写 `pass`。当前交付直方图门按下方规则核验全图 JPEG；全图中位数不是背景测量。强青/绿色背景、明显梯度、死黑/过曝直方图违规、通道裁剪、伪结构或星点异常必须 fail，并以
`review_required` 结束。

已验证 nonlinear、采用 sRGB 显示约定的父源使用 `icc_assign sRGB` 后再导出；assignment 不改变像素，不能使用 convert 重复变换。
执行器禁用加载时的 ICC 自动猜测；最终 JPEG 必须与同 TRC 的父源候选预览一致，科学 FITS 与 JPEG 分别验证。


`set32bits` 指定处理后输出精度，不能保证未变换的整数输入提升；唯一一次 `fmul 1` 是经审计的原生恒等提升，像素按两个 float32 ULP 验证。CD 与 CDELT/PC 的等价 WCS 序列化只有保持变换时才接受。

<a id="delivery-histogram-review-v2"></a>
### 交付直方图与告警审查

`delivery=paired_fits_jpeg_v2` 记录 `delivery_histogram_gate_v2`：全图 JPEG 亮度 p99-p01 小于 15 时记录 `global_contrast_span_low` 告警，不单独拒绝；中位数超出 15–100、纯白比例超过 5% 仍阻断交付审查。这些是工程显示检查，不能独立测量天空或主体显著性。

有告警时，实际检查并绑定 run receipt、配对 FITS、直接 JPEG 和已验证父源预览。五项门全部通过，notes 说明亮核/外缘细节、伴体、天空、颜色与星点观察；主体不可辨的平坦图不能因技术运行成功就接受。骨架与验证器从成功 receipt 解析合格父源预览，不按文件名猜测；调整类父源使用候选预览，排除调整前图与蒙版。

### 历史兼容

旧 `paired_fits_jpeg_v1` 保留原对比度硬门，仅按冻结记录复核，不改写历史 receipt。共用协议的历史 Stage 10/Stage 8 路径与角色同样保留；当前规则和操作顺序不迁移到旧记录。

v22 从绑定证据报告实际拉伸选择、背景预算和 legacy/回退状态。最终降噪、调色与交付检查仍独立执行，合成测试不证明真实画面收益。

v23 最终复核按实际采用的形态链及比较材料披露收益与代价；检查合星后亮核、微弱外缘、背景与星色，不能把两链参数报告代替成片像素审查。

<a id="ssf-knowledge-and-skeleton"></a>

<a id="restoration-denoise-nonlinear-ssf-knowledge-and-skeleton"></a>

<a id="来源与-ssf-示例"></a>

来源与 SSF 示例见[共用非线性降噪正文](../shared-protocols/denoise-nonlinear.md#restoration-denoise-nonlinear-ssf-knowledge-and-skeleton)。
