# Protocol Index

每次只读取并执行一个协议。完整后期任务按 [默认阶段顺序](stage-sequence.md) 评估；输入证据与用户目标
决定某个协议执行或跳过以及参数选择，但不得倒置图像域顺序。定向模式仍可只执行一个满足前置条件的协议。

## SSF 知识层级

每份 SSF 都由 Agent 在当前任务中参数化生成，不由 Python、Recipe 或固定流水线生成：

1. 当前 session、用户目标、采集来源和实际像素证据决定协议适用性与参数；未知信息保持 `unknown`。
2. `stage-sequence.md` 规定完整任务的默认宏观顺序；实际用于排序时可作为 supporting reference。
3. 被选中的 protocol reference 是 SSF 的 primary reference，规定科学边界、参数范围、参数化骨架、
   预期产物和审查要求。
4. 固定到 Siril 1.4.4 的离线手册是命令语法与语义来源；只有骨架未展开、语法/参数有歧义、结果与预期
   矛盾或用户询问功能时才按需查询，不逐命令机械重复查询。
5. `command-policy.json` 是独立执行授权边界；手册收录或标为 scriptable 不等于允许执行。

交给 `run` 或 `run --validate-only` 的每个 `scripts/<run-id>.ssf` 都必须有同 stem 的
`scripts/<run-id>.provenance.json`。sidecar 遵循 [SSF provenance Schema](ssf-provenance.schema.json)，绑定
脚本、唯一 primary protocol reference、可选 supporting references、policy、实际 manual command/read 查询
envelope 及参数理由。只做 Bundle 验证或逐字实例化完整骨架时不得伪造 manual query evidence。
`run` 仍会把 SSF 中每个命令映射到 session 冻结的 1.4.4 command index，核对 scriptable 与 policy 并写入
receipt 的 `command_knowledge`；这是机器知识闭包校验，不等同于 Agent 执行过 `--command`/`--read`。

| Stage | 协议 | 何时读取 |
|---|---|---|
| 1 | [input.inspect](protocols/input-inspect.md) | 所有执行模式的第一步；建立可读输入预览 |
| 2 | [geometry.crop-near-black](protocols/geometry-crop-near-black.md) | 可见近黑边、堆栈边或构图边界需要保守裁切 |
| 3 | [background.subtract](protocols/background-subtract.md) | 线性图存在可分离的大尺度渐变 |
| 4 | [color.calibrate](protocols/color-calibrate.md) | 线性 broadband/dualband 具备所选校准方法需要的证据 |
| 5a | [restoration.deconvolve](protocols/restoration-deconvolve.md) | 线性图有足够星点生成可靠 PSF |
| 5b | [restoration.denoise](protocols/restoration-denoise.md) | 反卷积之后仍有影响后续拉伸的线性背景噪声 |
| 6 | [stars.separate](protocols/stars-separate.md) | 明确需要 starless 分支且 StarNet2 可用 |
| 7 | [stretch](protocols/stretch.md) | 线性父源需要变为非线性显示图 |
| 8 | [color.map](protocols/color-map.md) | 已接受 starless 非线性父源且有可靠窄带来源角色 |
| 9 | [stars.recompose](protocols/stars-recompose.md) | 已接受 starless 分支，需要匹配传递合星 |
| 10a | [color.finish](protocols/color-finish.md) | 最终非线性三通道父源需要轻量显示调色 |
| 10b | [delivery.render](protocols/delivery-render.md) | 已选定并接受最终非线性父源，需要最终 JPEG |

## 组合规则

- 完整处理默认采用 Stage 1 → 10；同一阶段的多个协议也分别运行、分别审查。
- 线性输入必须先完成需要在线性域执行的协议，再执行 `stretch`；Stage 5 同时适用时先
  `restoration.deconvolve`，再 `restoration.denoise`。
- nonlinear 输入不得执行只接受 linear 的协议。
- unknown 输入只执行 Stage 1 的 `input.inspect`，之后停止。获得可靠状态证据后创建新 session；诊断预览
  不是科学父源或交付父源。
- `stars.recompose` 只接受同一 StarNet 分支和 stretch 记录的匹配 MTF。
- 任意可选协议失败时保留父源；`stretch` 或 `delivery.render` 失败时停止。
- 不执行没有可见证据或用户目标支持的协议。
