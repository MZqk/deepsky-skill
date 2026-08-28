# 马赛克验收

仅在查看最终预览、决定是否调整 feather，或解释 `review_required` 时读取本文件。

## 机器门禁

`result.json` 的下列项必须全部通过：

- Siril 返回 0 且未超时；
- sequence、plate-solved、registered 以及 Siril 日志中的实际 stacked 数量均等于输入 panel 数；
- 线性 FITS 可读，JPEG 有合法文件头；
- 对不同指向的 panel，输出画布面积大于单幅 panel；
- 输入、脚本、日志和交付物都有 SHA-256。

任一失败都不能用视觉观感覆盖。

## 视觉门禁

在适合观察接缝的缩放和 100% 星点缩放下都查看预览：

1. `target_complete`
   - 用户要求的天体主要结构均在画布内。
   - 不能只凭 `OBJECT`、文件名或一个中心坐标判定。
2. `alignment_no_duplicate_stars`
   - 四条重叠边中的星点为单一中心；无双星、弧形拖影或局部错层。
3. `seams_and_background`
   - 重叠区没有明显矩形边、亮度阶跃或颜色突变。
   - 星云本身的真实渐变不应被误判为接缝并强行抹平。
4. `no_internal_black_gaps`
   - 外轮廓的无覆盖黑角可以保留供后续裁切；panel 网格内部不能有黑洞或漏块。
5. `source_structure_preserved`
   - 细丝、恒星和窄带颜色来自源 panel；无插值振铃、重复纹理或显示拉伸导致的高光大片截断。

## 有界重试

- 只有接缝轻微且配准正确：在新 run 中试一个相邻 feather 值（常见 32、64、128）。
- 背景阶跃仍明显：保留 `-overlap_norm`，检查 panel 是否在拼接前接受了不一致的背景处理；不要无限加大 feather。
- 双星或错层：检查 WCS、SIP、像素尺寸、焦距和 `scale`，重新解算；feather 无法修复配准。
- 内部黑洞或 panel 数不足：直接 `review_required`，检查输入覆盖和解算日志。
- 目标超出画布：确认同时存在 `-framing=max` 与 `-maximize`，以及 scale 没有被误当裁切。

每次重试必须使用新目录。最多一个参数化重试；仍不满足时保留失败证据并停止。
