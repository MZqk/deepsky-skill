# Siril Safety Protocol

## 脚本

- 每个 `.ssf` 前两条有效命令固定为 `requires 1.4.4 1.5.0` 与 `set32bits`。
- 每个脚本只实现一个声明协议；命令集合以 `command-policy.json` 为准。
- 每个交给 `run` 或 `--validate-only` 的 Agent SSF 都必须有同目录同 stem provenance sidecar；CLI 在启动
  Siril 前核对其脚本哈希、primary protocol reference、policy 哈希和 manual evidence。CLI 自行生成的
  `runtime/decode-validation/*.ssf` 由 run receipt 约束，不冒充 Agent 协议脚本。
- 输入 `load` 只能读取原 master、成功 run 产物或当前脚本在 session 内生成的临时文件。
- `save`、`savejpg`、`savetif32`、`rgbcomp -out` 只能写 session 内路径。
- 禁止 shell、任意 Python、GUI、Stage1、序列校准/配准/堆栈和任意外部增强器。
- `pyscript` 只允许背景样点适配器并受协议 ID 约束；StarNet 使用 Siril 原生 `starnet` 命令。

命令拼写、可脚本化标记和参数顺序以版本锁定的离线 Siril 1.4.4 手册为准。完整 protocol 骨架可在 `init`
冻结 Bundle 验证后直接实例化；骨架未展开、语法歧义、需要确认参数或运行结果矛盾时按
[手册查询协议](manual-query.md) 读取并保留 command/read envelope，不得从浮动网页或 1.5 文档反向猜测
1.4 命令，也不得把 Bundle 验证伪写成命令查询。
手册知识与执行授权必须分开：只有 `command-policy.json` 为当前协议列出的命令才能进入 `.ssf`。
CLI 还会把实际命令映射到 session 冻结的 1.4.4 command index，核对 scriptable 并记录
`command_knowledge`；这项机器校验不生成脚本，也不算 manual lookup evidence。

## 执行

CLI 使用 session 专属 initfile、固定工作目录、关闭 stdin、固定 C locale 和显式 timeout。每次运行记录
当前 Siril 与适配器指纹；退出码为零不代表产物或日志有效。未知 `error/exception/traceback` 使 run 失败，
即使输出可重开；失败 run 的产物只保留为诊断证据，不进入后续 lineage。

JPEG EXIF 缺失和成功后的 BrokenPipe 不是按文本全局忽略：只有分别绑定实际 JPEG 读取、完整通道统计、
显示解码，或绑定成功结束、零退出码和全部产物验证时才降级为结构化 warning。原始合并日志始终保留，
replay/finalize 会重新核对其 fingerprint 和分类。

启动 Siril 时设置 `PIP_NO_INDEX=1`，禁止 Siril 的 Python 环境初始化旁路下载依赖。probe 只报告已安装
能力；Skill 不下载、解包或安装工具。

SirilPy 不得由 Skill 自动安装。`background.subtract` 实际执行时由内置适配器导入并校验
`sirilpy==1.0.25`、确认连接、核对当前加载源并验证注入后的样点坐标；任一步不满足即关闭该次运行。

不得覆盖已有脚本、receipt、候选或最终图。修订候选使用新编号。原输入哈希漂移时立即停止。

## 科学产物解码

科学容器模式在 `init --container-validation siril|strict` 冻结。`init` 本身仍只做输入类型、普通文件和
fingerprint 冻结，避免把有限的标准库解析器误当成输入格式的完整兼容性判据。

- 默认 `siril` 模式直接使用冻结的 Siril 1.4.4 独立离线重开，解析 load/stat 日志中的格式、尺寸、通道
  和有限统计，并核对验证前后 fingerprint 未变化。
- `strict` 模式在上述重开前增加完整容器预检。FITS 预检遍历全部 HDU，核对 80/2880 字节结构、
  `END`、数据区长度、padding 和精确 EOF；接受
  普通 IMAGE HDU 及标准压缩图像表，拒绝随机分组和任一后续 HDU 截断。
- `strict` 模式的 XISF 预检仅接受一个二维 Gray/RGB Image 的 monolithic `XISF0100` 单元。它验证 UTF-8/XML、
  attachment/embedded 数据块、受支持 sample format、`zlib+sh` 解压长度和声明 checksum；DTD、entity、
  外部 `url/path` 位置和越界块均失败关闭。
- 两种模式都不可用 Python 图像包的 header 或数组读取替代 Siril 重开。重开脚本只能执行
  `requires 1.4.4 1.5.0`、`set32bits`、`load`、`stat main`、`close`，不得生成新图像。
- 重开脚本写入 `runtime/decode-validation/`，initfile 写入 `runtime/siril-configs/`，完整 stdout/stderr
  写入 `logs/`；单文件 timeout 上限为 300 秒，并将路径和 fingerprint 固化到 run receipt。

严格容器预检或 Siril 重开失败都使用稳定的 `artifact_invalid` 分类记录 failed run；退出码为零、只有
header 可读或仅有正确扩展名都不能成为成功证据。

## 网络

默认 offline。只有非 offline session 的 `color.calibrate` 明确使用 `pcc/spcc -catalog=gaia` 时允许
远程 Gaia；`localgaia`、neutral 与其他协议都以 `--offline` 启动。offline session 使用远程 Gaia 在
静态验证阶段拒绝。自动下载永远禁止；工具缺失时报告缺失能力和影响，由用户在 Skill 外处理安装。

## 失败

可选协议失败时保留父源并记录限制。必需的 Siril、stretch 或 delivery 失败时停止。可选工具缺失时跳过
对应能力；必需工具缺失时返回 `review_required` 或结构化失败。
