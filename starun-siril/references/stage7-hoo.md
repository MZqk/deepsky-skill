# Stage 7 · 可选 HOO 合同

HOO 保留专用整链流程；未指定执行模式时自动选择该流程，显式 --stepwise 仍拒绝。Ha/OIII 主体链各至少两步；内部 RGB 星点／显示参照链可单步，但不得独立作为 Stage 7 主体采用。

新会话的正式 RGB 结果接入[伪影重点区记录](stage7-artifact-focus.md)，用已有原生材料检查补丁、光晕、纹理与色彩过渡；提取、映射和 RGB 星色处理方式不变。

只在实际选择本变体时读取；与[当前 Stage 7 主文档](stages/stage-07-stretch.md)共同使用，SSF 以主文档为唯一 primary，本文件为 supporting。 使用 `protocol_reference_bindings('stretch', variant='hoo')` 获取完整绑定；手写 SSF 同样需要该附文的 supporting 路径与 SHA-256。

<a id="dualband-hoo-stretch"></a>
## 双窄带 HOO 主体与原 RGB 星色

新会话默认 `dualband=off`，未选择窄带映射时保留原 RGB 路线。用户显式选择 HOO 并在 init 传 `--dualband auto` 后，才在可信 Ha/OIII 滤镜、有效 `channel_mode=dualband-osc` 和已接受的原始线性 RGB `stars.separate` 主输出上评估本变体。已经映射的 `channel_map`、宽带、非线性、未知滤镜或无可信无星分支保持原 RGB 路线；无设备来源的 LP 字样、文件名或色调不足以启用。不要求未知的响应矩阵；这条路线明确只提供显示估计。

本变体属于 `stretch` 一个协议。科学文件由 Siril 写入：先 `fmul 1` 后原生 `split`，保留 H=R、O=(G+B)/2 的符号及单位，不预先裁零、按峰值归一化、减 R 串扰或构造 SII。等权是显示估计，不是 75%/25% 光子比例。保留原输入用于科学测量；已去拜耳 RGB 不能调用要求 CFA 的 `extract_HaOIII`。

Agent 确认当前父源上的天空、Ha/OIII 微弱结构和核心/星点 ROI，沿用 regions.v1；不同谱线可以引用不同的 faint 条目。选择三组已有 MTF/Asinh/GHS 算子和区域约束；参数层不选择方法、不增加链长或放宽约束。三条链必须显式可回放，不用 autostretch。RGB 链保护原星点显示，Ha/OIII 链分别分配主体动态范围，不按天体名称写死目标亮度。

新执行使用 `starun-siril.dualband-request.v4`，包含 schema、source、regions、operators、oiii_denoise、manual_lookup，以及 profile、targets、core_absence。source/regions 沿用已有路径及 SHA-256 绑定；operators 为恰好 rgb/ha/oiii 三组带显式 factors 的算子列表，manual_lookup 沿用真实查询证据格式。Header 无法识别实际滤镜时，可另绑定 filter_evidence，恰好为 `{"source":"user","lines":["H-alpha","OIII"],"evidence":"Actual explicit user declaration and its context"}`；source 允许 user/acquisition，必须有真实用户或采集来源。它不改写 session.filter_profile，不从未知 LP、传感器型号或文件名补猜。

oiii_denoise 关闭时为 `{"enabled": false, "reason": "Actual noise assessment and structure protection evidence"}`。开启时必须另有 `proposal`，绑定 [Stage 5 共用提案](stages/stage-05-linear-restoration.md#linear-denoise-agent-proposal) 的路径、SHA/size；显式 modulation/cosmetic 来自这份提案，不能静默回落到 0.35。Ha 不追加降噪，不得仅因 OIII 弱就默认执行。

先调用 `deep_sky_siril_dualband.py prepare --session /abs/session --source artifacts/060-starless.fit --run-id 074-oiii-input --display-mtf 0 0.15 1` 冻结真实原生 OIII=(G+B)/2 输入、父源、分星证据及显示预览；显示值按实际父源选择，无法从 header 识别滤镜时传入实际 `--filter-evidence` JSON。Agent 查看后给实际 OIII 选择 regions.v1，再用共用 analyze 测量；提案 source 绑定返回的 OIII，额外 `derived_context` 绑定返回的 prepared。HOO v4 的 source/regions 仍绑定原 RGB 分星父源，两套 ROI 不混用。

试跑与正式生成使用同一显式命令和原生配方；保留未降噪 OIII 的同曲线对照，以及线性域同 MTF 预览、原分辨率保护区域和有符号残差。HOO review 必须独立填写 denoise_assessment，拉伸通过不能代替纹理保留；噪声收益不成立、结构损失或无法判断时保留父源，可用新 run ID 关闭额外降噪后重新提案。主体/RGB 局部预览在此变体中保留两个代表区域，降噪面板覆盖全部保护 ROI，以遵守 32 份材料上限。

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_siril_dualband.py solve \
  --session /abs/session --request /abs/session/reports/075-request.json \
  --run-id 075-hoo --timeout 1800
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_siril_dualband.py generate \
  --session /abs/session --parameters /abs/session/reports/075-hoo/dualband-parameters.json
```

solve 复用单曲线原生求参核心，默认 v4 双窄带请求每步只评估一次因子提案。单色派生输入只用于诊断，不伪装成已接受父源。请求、RGB 科学父源、同源 full、分星 receipt/review、ROI、运行时、派生图和所有试跑脚本/日志均绑定。结果不是 solved 时不生成正式 SSF；诊断证据不进入科学谱系。失败或外壳损失时，以新 run ID 评估关闭额外 OIII 降噪的候选，不覆盖已有证据。

generate 创建 SSF 与 provenance，Agent 审查后使用普通 `run --protocol stretch`，显式传入返回的 primary_output 和全部 expected 路径。SSF 的 `# dualband-stretch: {"parameters":"/abs/session/reports/075-hoo/dualband-parameters.json"}` 选择严格变体；其完整原生操作顺序、参数和路径必须与报告一致，不能与 HDR/adaptive-stretch 声明混合。普通 stretch 不因命令白名单增加而获得 denoise/fmul/crop 权限。

Ha/OIII 分别拉伸后，以显示配方 `R=H, G=0.8*O+0.2*H, B=O` 合成。主输出是 HOO 无星图；保存原 RGB 无星显示基线、线性估计图、采用的 OIII 降噪图、拉伸图及相同 OIII 曲线下未额外降噪的对照。直接 JPEG 使用相同 sRGB 显示条件；固定天空、faint、core、stars 区域各生成 RGB/HOO 局部预览，避免各自 autostretch 比较。

receipt.dualband_transfer 绑定报告、ROI、三条链、估计语义、图层和预览；transfer_chain 继续表示原 RGB 星点链。processing_checks.dualband 重测提取、混合公式、最终区域约束、负值/端点和当前域统计。实际打开所有绑定单色对照、RGB 基线、HOO 图及局部，检查外壳连续性、细丝、亮核、背景色斑和端点；五项门都通过才能 accept。允许可复核的微小收益；仅放大噪声、结构损失或证据不足保留 RGB 路线。指标和退出码不自动接受候选。

Stage 8 不再次 color.map 或自动去绿，保留已接受 palette，再评估 tone.adjust 与 vivid。Stage 9 只回放 RGB 链求星层，见对应 [合星说明](stages/stage-09-stars-recompose.md#dualband-rgb-stars)。

