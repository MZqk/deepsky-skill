# Siril 1.4.4 离线手册查询

本技能完整携带固定到 Siril `1.4.4`、提交 `1550a31d325276124fe961368477c90d49df804b` 的官方英文纯文本手册语料（位于 `assets/siril-manual/`），用于离线确认功能、命令语法、参数与算法说明。

## 查询模式

1. **验证离线语料闭包**：
   ```bash
   python3 -B scripts/query_siril_manual.py --verify-bundle
   ```

2. **关键词与中英文别名搜索**（先定位 `id` 或 `path`，再读取原文）：
   ```bash
   python3 -B scripts/query_siril_manual.py "背景提取" --top 5
   python3 -B scripts/query_siril_manual.py "SPCC" --format text
   ```

3. **精确命令查询**：
   ```bash
   python3 -B scripts/query_siril_manual.py --command autostretch
   python3 -B scripts/query_siril_manual.py --command rl --format text
   ```

4. **章节与整页读取（自动内联安全 `include` 与 `csv-table :file:` 依赖）**：
   ```bash
   python3 -B scripts/query_siril_manual.py --read section:<id>
   python3 -B scripts/query_siril_manual.py --read doc/Commands.rst
   ```

5. **原子落盘 JSON 查询结果（可选 `--output`）**：
   - `--output` 仅接受绝对路径且要求 `--format json`，父目录必须为真实既有目录，只新建文件、绝不覆盖或跟随符号链接。

## 边界说明

- 默认输出 `starun-siril.manual-query.v1` 兼容 JSON；`--format text` 适合人工与上下文阅读。
- 普通搜索无匹配仍退出 `0` 并返回 `result.status=no_match`；精确命令或页面不存在返回退出码 `3`。
- 查询器不访问网络、不启动子进程、不自更新，也不写入 `assets/siril-manual/`。
- `upstream_reverified_now=false` 表示仅校验本地冻结来源，未实时连接上游。

## 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 验证、读取、精确查询或普通搜索成功（含普通搜索无匹配） |
| `1` | 语料闭包、索引、许可证或文件完整性校验失败 |
| `2` | 参数组合、`--top` 或输出路径无效 |
| `3` | 精确命令、页面或 section 不存在 |
