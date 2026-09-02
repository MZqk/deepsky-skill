# Siril 1.4.4 离线手册查询

本 Skill 完整携带固定到 Siril `1.4.4`、提交
`1550a31d325276124fe961368477c90d49df804b` 的官方英文手册组件。它用于确认功能、命令语法和参数；
协议文件负责科学决策，`command-policy.json` 负责执行授权。手册中存在或标为 scriptable 的命令不因此
自动获得执行权限。

## 按需使用

执行任务由 `init` 自动完成一次完整 Bundle 验证，并把结果冻结到 session；不要在 `init` 前手动重复
`--verify-bundle`。纯知识查询等没有 session 的任务首次使用时才显式执行一次。解包验收、发布检查或
怀疑组件损坏时也可显式验证：

```bash
python3 -B scripts/query_siril_manual.py --verify-bundle
```

protocol reference 中已经完整展开、且只替换证据支持的路径和参数占位符的命令，在 session 已冻结 Bundle
验证后可直接实例化；这时 provenance 写 `manual_lookup.status=not_needed` 和具体原因，不为满足形式要求
重复查询。以下情况必须查询原文：

- protocol 只命名但没有完整展开的变体或选项，例如 `color.calibrate` 的 neutral/SPCC、
  `color.map` 的 modified-SHO PixelMath、`stretch` 的 asinh 分支；
- 命令拼写、参数顺序、引用、单位、flag 组合、scriptable 状态或 1.4.4 行为存在歧义；
- Siril 实际日志与 protocol 骨架的预期矛盾；
- 用户询问 Siril 功能、命令或参数。

查询不熟悉的功能或参数时，先搜索定位，再用结果的 `id`、`path` 或精确命令读取上下文：

```bash
python3 -B scripts/query_siril_manual.py "背景提取" --top 5
python3 -B scripts/query_siril_manual.py "SPCC" --format text
python3 -B scripts/query_siril_manual.py --read section:<id>
python3 -B scripts/query_siril_manual.py --read doc/Commands.rst
```

生成或审查包含不确定命令的 `.ssf` 时做精确查询：

```bash
python3 -B scripts/query_siril_manual.py --command autostretch
```

结果区分官方 `documentation.scriptable` 与本包的 `execution_policy.state`。只有 policy 为 `allowed` 且
当前协议列在 `allowed_protocols` 中时才可进入脚本；`manual_only` 只能用于解释，`non_scriptable` 不得
放入 `.ssf`。

## Provenance evidence

若 SSF 实际依赖 `--command` 或 `--read`，让查询器直接原子创建完整 JSON envelope：

```bash
python3 -B /abs/starun-siril/scripts/query_siril_manual.py \
  --command autostretch \
  --output /abs/session/reports/manual-evidence/090-autostretch.command.json

python3 -B /abs/starun-siril/scripts/query_siril_manual.py \
  --read section:<id> \
  --output /abs/session/reports/manual-evidence/090-stretch-section.json
```

`--output` 只接受绝对路径和 JSON 格式，父目录必须是真实的既有目录；它原子新建文件，拒绝已有目标、
symlink、非真实父目录或覆盖。成功时仍把与文件逐字相同的 JSON envelope 输出到 stdout。修订查询使用新
文件名，不删除或改写旧 evidence。

在同 stem SSF provenance 的 `manual_lookup.evidence` 中记录输出文件的 session 相对路径和 SHA-256。
普通 search 只用于定位，`--verify-bundle` 只证明本地组件闭包；即使为它们传 `--output`，也不能作为
provenance 的 command/read evidence，更不得在报告中写成“已查询命令原文”。没有实际 command/read 时使用
`status=not_needed`，evidence 必须为空。`--format text` 适合临时阅读，但不能与 `--output` 组合，也不能
代替 provenance 所需的完整 JSON envelope。

provenance 的 primary reference 必须是所声明协议的 `references/protocols/<protocol-id-with-dashes>.md`；
冻结手册文件本身不得作为 reference 条目，手册使用只通过上述 query envelope 记录。这样既能证明 SSF 的
来源，也不会把手册变成自动 Recipe 或要求每条命令重复检索。

## 边界

- 默认输出 `starun-siril.manual-query.v1` JSON；`--format text` 只改变展示。
- 普通搜索无匹配仍退出 `0` 并返回 `result.status=no_match`；精确命令或页面不存在则失败。
- 搜索摘要只用于定位。凡因搜索命中而做参数或功能结论，应再执行 `--read` 或 `--command`。
- 每次查询都会验证冻结组件的完整闭包和读取证据；`--verify-bundle` 用于显式报告完整验收结果。
- 查询器不访问网络、不启动子进程、不自更新，也不写入手册组件。
- `upstream_reverified_now=false` 表示只验证本地冻结来源，并未实时访问上游。

## 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 验证、读取、精确查询或普通搜索成功；包括普通搜索无匹配 |
| `1` | Bundle、索引、许可证、policy 或完整性失败 |
| `2` | 参数组合、`--top` 或读取路径无效 |
| `3` | 精确命令、页面或 section 不存在 |

手册验证成功不证明 FITS/XISF 可解码、Siril 已执行、像素质量合格或视觉验收完成。
