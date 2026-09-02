# Quality and Review Protocol

实际打开协议要求的每个图像或报告。文件名、摘要、统计数字和退出码不能替代视觉检查。

## Review receipt

每个采用的 run 写入 `reviews/<run-id>.json`，遵循 `review.schema.json`：

- 原样复制 run ID、receipt SHA-256、协议 ID；
- 列出每个实际打开材料的 session 相对路径和 SHA-256；
- 对 `structure`、`background`、`color`、`stars`、`geometry` 写
  `pass|fail|not_applicable|uncertain`；
- `verdict=accept` 要求所有适用门均为 `pass`，不得包含 `fail|uncertain`；
- 说明具体观察，不写无法从材料证明的推断。

## 通用门

- `structure`：真实细节保留，无抹除、伪结构、振铃、泄漏或塑料感。
- `background`：渐变改善，无坑洞、误减、条纹或色块。
- `color`：过渡连续，无通道裁剪或无来源的真实性声明。
- `stars`：星核、星径、星色和光晕自然，无重复星或明显残留。
- `geometry`：构图、裁边、尺寸和 WCS 变化符合协议预期。

候选没有明确优于父源时写 `reject` 并保留父源；证据无法可靠判断时写 `uncertain` 并安全停止。

## Unknown 输入

direct 与 linked-autostretch 都只是可读性显示，不解析线性状态。只判断结构与星点能否可靠辨认；
诊断拉伸中的偏色和渐变可标为 `not_applicable`。通道来源也没有证据时保持 channel unknown，不得从
色调推断 broadband、dualband 或 narrowband。审查完成后停止，不进入 Stage 2 或最终审查；获得可靠状态
证据后创建新 session。

## 最终审查

最终 review 必须实际打开最终 JPEG 和最终父源预览。父源预览必须来自生成当前父源的已验证 run；
父源为原输入时使用已验证的 `input.inspect` 预览，不得临时伪造审查材料。默认输出必须含星；仅用户显式选择
`standalone-starless` 时允许无星。接受前五项完整性门必须全部通过；任何 uncertain 都使用
`review_required`，不要伪装为成功。`delivery.render` 的 receipt 至少列这两份不同图像，五项门不得
写 `not_applicable`。limitations 不豁免最终五门；明显强偏色背景、通道裁剪、梯度、结构伪影或星点
异常必须 fail。unknown session 不进入本节的最终审查。
