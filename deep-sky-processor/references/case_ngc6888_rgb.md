# NGC 6888 RGB/LP 实战案例

输入示例：SeeStar RGB Float32 XISF 集成主灯，LP 滤镜，2160×3840。

## 诊断结论

- 目标类型：发射星云，NGC 6888 Wolf-Rayet 风泡壳层。
- 原始中位数约 `0.0006`，属于极暗线性数据。
- 背景噪声约 `0.00028`，不需要强降噪。
- 全局梯度不显著，应跳过 DBE，避免误减天鹅座真实 Hα 背景。
- 若灰度梯度不显著但 RGB 通道低频梯度方向或幅度差异明显，标记为
  `review_chromatic`：暂停自动 DBE，通过背景模型差分判断真实 Hα 与色偏。
- 红色主导是预期发射信号，不是普通白平衡错误。
- 星场密集，目标在全画面中占比小，但宽场构图本身属于有效信号。
- 默认保留完整画幅，只自动裁掉配准产生的无效黑边；主体定位仅用于增强蒙版。

## 推荐命令

```bash
python3 scripts/pipeline.py NGC6888.xisf NGC6888.jpg \
  --strength emission \
  --auto-crop-edges \
  --local-strength 0.85 \
  --external-starless starless_NGC6888.fit \
  --external-detail-strength 1.4 \
  --keep-all
```

该文件没有无效黑边，因此自动裁边结果为：

```text
crop = (0, 0, 2160, 3840)
```

增强蒙版仍会优先使用外部无星线性图定位主体，约为
`center=(1079,1937)`、`object_bbox=(947,1788,265,299)`，但不会据此裁图。
只有用户明确要求主体特写时才使用 `--auto-crop-target`。

## 质量门槛

- 宽场成片背景中位数建议 `0.08-0.15`，保留天鹅座弥散 Hα 层次。
- 发射星云星点面积建议 `<3%`。
- `uniform_5x5_dark_patch_ratio` 应接近 0，避免塑料涂抹。
- 低噪声长曝光数据若壳层在最终降噪后明显变软，应取消 `final_denoise`。
- 外部无星图有暗环时，不直接作为底图重组；使用 `external_detail` 仅提取正向多尺度结构，禁止负残差进入成片。
- 去星失败并保留原始星点时，使用带星保护的 `masked_ghs`，局部增强必须叠加星点反向蒙版。
- 极暗数据的 `masked_ghs` 必须设置 `shadow_pctl=0.0`，先做稳健动态范围
  映射，并将目标背景托至约 `0.10-0.15`；禁止直接在接近零的原始范围上
  使用过大的 GHS `b`。
- 品红修正不得覆盖 OIII 青色候选区域；若 OIII 原始信噪比不足，不人为制造青色。
- RGB/LP 数据只能保留已记录色彩，不能仿造参考图中缺失的 SHO/HOO 通道。

## 有参考成片时

先使用不含 `star_remove`、`star_reduce`、`style` 的安全基础链路：

```bash
python3 scripts/pipeline.py NGC6888.xisf NGC6888_base.jpg \
  --strength adaptive \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --color-mode emission \
  --stretch-method emission \
  --steps color,stretch,final_color \
  --override-params \
  '{"dbe_method":"skip","shadow_pctl":0.0,"highlight_pctl":99.95,"stretch_gamma":0.38,"saturation":1.15}'
```

确认星点完整、背景未贴黑后，再用 `scripts/reference_grade.py` 做全局参考定调。
如果参考图曝光更深，只允许接近其亮度、综合色调和饱和度，不得补画缺失云气。

本案例的极暗线性数据应在安全拉伸后运行 StarNet2，并独立导出无星层和星点层：

```bash
python3 scripts/enhance_starless.py \
  starless_NGC6888.tif stars_NGC6888.tif NGC6888_starless_finish \
  --target-type emission_nebula \
  --target-name NGC6888
```

禁止把线性数据直接压成 16-bit 后去星；这会量化背景并在拉伸后形成红色块状
伪影。重点审查外壳连续性、内部细丝真实性、Hα/OIII 平衡、StarNet 暗坑或
亮环，以及 HIGH 档是否仍自然。任何硬门禁失败都必须记录，不能强行宣布成功。

Python 3.14 环境必须保留当前脚本中的显式 NumPy 输出缓冲区。部分 NumPy
构建会复用 borrowed-reference 数组操作数，导致综合色调层被高频细节层静默
覆盖；回归测试必须检查处理后无星层中位数达到请求值。

## C50 实测参数（本图专用，不是通用预设）

C50 母版实测采用：

| 版本 | `stretch_gamma` | `target_bg` | `stretch_min_p99` |
|---|---|---|---|
| 自然版 | 0.58 | 0.045 | 0.24 |
| 增强版 | 0.50 | 0.055 | 0.30 |

两版均关闭末端强调色和风格。**这些是本图参数，不得当作通用预设套用其他目标。**


## Manual Siril background extraction, only with confirmed sky evidence

The observed case above still skips DBE when no significant separable gradient is established.
Its faint Cygnus emission is signal, including potentially dark corners; a small bright shell does
not make the rest of the frame empty sky. Do not enable automatic full-frame RBF/polynomial sample
grids based on the target name, apparent redness or a preference for a flatter background.

If a separate additive gradient is confirmed, manually protect the inspected shell: enlarge its
bounding-box dimensions to 1.5 times around the same center, round boundaries outward and clip to
the frame. This is a starting total extent; enlarge or add protection for actual faint emission,
dust and star halos beyond it. Use source-bound core/faint/stars rectangles and manual fit_samples
in the existing Siril contracts, then clear prior samples, install only the declared coordinates
and use -existing. A sample center outside a nebula is insufficient if its full 25x25 box crosses it.

Three to five samples per verified corner are a candidate layout only. Confirm the entire box avoids
faint/bright stars, halos and emission. Keep middle-side clean-sky samples and independent held-out
sky controls; corner-only samples cannot replace the existing middle-third coverage gate. Missing
trustworthy sky or controls means skip subtraction, without shrinking the exclusion or assigning
nebular outer emission a background label. Grid tolerance is a brightness-deviation factor, not a
pixel size, and the existing sample bridge remains fixed at 25x25 pixels.

Use Order 1 for a supported near-plane additive gradient and Order 2 only with independent evidence
of curvature. This manual field guide excludes high-order fits and RBF. General quadratic surfaces
are not necessarily centrally symmetric or immune to subtracting broad emission. Optical vignetting
is multiplicative: check upstream flats instead of treating subsky subtraction as its exact repair.
Actual fixed-transfer parent/candidate review must preserve the shell and faint external emission;
scientific improvement and geometric exclusions alone cannot establish signal preservation.

Reference: [Siril 1.4.4 background extraction](https://siril.readthedocs.io/en/stable/processing/background.html).
