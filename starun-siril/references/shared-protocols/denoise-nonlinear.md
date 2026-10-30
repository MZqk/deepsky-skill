# 共用协议 · 非线性整体与色度降噪

<a id="restoration-denoise-nonlinear"></a>
## restoration.denoise-nonlinear


在最终调色之前评估；只处理已审查接受的非线性科学主父源。每个候选独立运行，不将 linear-only 降噪用于非线性图。

<a id="ssf-knowledge-and-skeleton"></a>
<a id="restoration-denoise-nonlinear-ssf-knowledge-and-skeleton"></a>
### 来源与 SSF 示例

本阶段文件为 primary；冻结 `denoise` 手册定义原生 NL-Bayes 调制，Python 只执行与验证。

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-nonlinear-parent.fit"
denoise -nocosmetic -mod=0.20
stat main
save "/abs/session/artifacts/100-denoise-nonlinear" -chksum
savejpg "/abs/session/previews/100-denoise-nonlinear" 95
close
```

参数仅允许 `0 < mod <= 0.35`，禁用 SOS、DA3D、VST 和 cosmetic correction。检查背景、细丝、亮核、星点轮廓与颜色连续性；无明确可见收益则 reject 并保留已接受父源，指标不能接受视觉门。


<a id="pre-color-chroma-denoise-chroma_bilateral_v1"></a>
## 增色前色度降噪（chroma_bilateral_v1）

新会话冻结 `chroma_denoise=chroma_bilateral_v1`。Stage 8 在无星主体完成主要亮度整形后、color.finish 前评估；有可信红绿斑、蓝紫斑等证据才试跑。无收益、结构退化或无法判断时保留父源，不创建 identity run。只接受已审查的非线性 RGB FITS 无星父源；mono、unknown 和含星父源不适用。保持原星层与拉伸链，修订从同一可信父源开始，不累积降噪。

使用原生 RGB 联合双边滤波 epf，同时利用亮度差和颜色差限制跨边界混合，不逐 RGB 通道独立滤波。设父源 A、滤波结果 D，计算 `deltaC=(D-A)-Y(D-A)*(1,1,1)`，其中 `Y=0.2126R+0.7152G+0.0722B` 为现有加权显示亮度，最终为 `A+t*deltaC`。此约束不证明物理光度或感知明度保持。等亮度异色边界也须保护；微弱真实颜色边缘仍由实际审查判断，不按颜色名称删除、不以去绿或降饱和代替降噪。

t 为 RGB 共用权重，不超过提议强度及各通道距离 `[1e-6,1-1e-6]` 边界的可用修正比例。父源任一通道位于开区间之外时，整个像素保持原样。只限制修正权重，不裁剪或归一化科学像素。

### 显式提案与原生单位

请求使用 `starun-siril.chroma-denoise-request.v1`：source、regions、goal、parameter_reason、strength、diameter、sigma_spatial、sigma_range 和 color_probe。全部数值显式填写；strength/range sigma 为 (0,1]，spatial sigma 为 (0,32]，diameter 为 3–19 的奇数。ROI 覆盖实际色斑、颜色交界与弱结构，至少一个 faint/core 主体区；有真实天空时加入，不能伪造纯天空。更大核与更强参数不自动代表更好。

Siril 1.4.4 浮点 RGB 的实际范围参数为 `(si/50)^2`，因此生成 `si=50*sqrt(sigma_range)`、`ss=sigma_spatial`、`mod=1`。该版本换算已用原生像素与独立计算核对；冻结 epf 手册的强度单位描述与实现有差异，不能照抄，也不修改离线手册包。[Siril 1.4.4 epf.c](https://gitlab.com/free-astro/siril/-/blob/1.4.4/src/filters/epf.c) 提供实现依据；未验证运行时版本拒绝执行。

```json
{"schema":"starun-siril.chroma-denoise-request.v1","source":"artifacts/085-tone.fit","regions":"reports/087-regions.json","goal":"Reduce observed red-green and blue-purple blotches before color gain","parameter_reason":"One conservative proposal based on actual blotch scale and color boundaries","strength":0.4,"diameter":9,"sigma_spatial":2,"sigma_range":0.04,"color_probe":{"D":0.42,"saturation_protect_start":0.65,"saturation_protect_end":0.9}}
```

示例只说明格式，参数须根据当前素材选择。color_probe 为拟用 GHS D 与 s0/s1。两侧复用现有 color.finish 骨架，使用相同 D、阈值及 `mask_mode=none`、`vivid_preservation_v1` 保护规则；饱和度蒙版分别从对应父源计算。这些产物仅为诊断，不作为已接受调色或科学父源。

```bash
"$STARUN_PYTHON" -B /abs/skill/scripts/deep_sky_siril_chroma.py solve --session /abs/session --request reports/087-request.json --run-id 087-chroma-trial
"$STARUN_PYTHON" -B /abs/skill/scripts/deep_sky_siril_chroma.py generate --session /abs/session --parameters reports/087-chroma-trial/parameters.json --run-id 088-chroma
```

solve 在 reports 中保留一份原生降噪试跑、两份受保护 GHS 试跑，以及原分辨率增色前后 ROI 面板、蒙版、脚本、日志和配置。generate 只输出正式降噪 SSF/provenance 与完整 expect 清单；随后使用正常 run/review 入口。本文件为唯一 primary，声明 `# chroma-denoise:` 绑定 variant 与参数报告。helper 生成并逐字验证完整原生骨架；额外 epf、split、pm、rgbcomp、icc_assign 权限只适用于该显式变体。Python 生成脚本与诊断，全部科学图层由 Siril 写入。

### 证据、采用与回放

receipt.chroma_denoise 绑定配方、图层及父源／权重／候选直接预览。processing_checks.chroma_denoise 核对几何、有限 float32 像素、加权亮度与中间算术（1e-6 容差）、保护像素严格不变、零新增端点、已有 WCS/曝光及正式／试跑一致性。不可变报告绑定请求、父源、ROI、已接受谱系、运行时、实现及全部诊断文件和配置。reports 内科学试跑产物不能成为科学父源。

复用 denoise_assessment：分别观察增色前及相同参数增色后的色噪收益，逐区审查颜色边界、淡红／蓝色结构和细丝保留。必须打开原分辨率面板与权重图；色度 MAD 下降不单独证明降噪。仅在收益 observed、全部结构 retained 且 structure/color 门通过时采用；不确定或退化则保留父源。合星后仍复核主体颜色、星色和光晕。

Stage 10 原整体 NL-Bayes 变体保持原命令与参数边界；已采用仅色度处理后，不自动重复整体降噪。历史会话按原 primary、策略及实现指纹回放/finalize，不能改写既有证据或升级会话；新增执行须新建 session。
