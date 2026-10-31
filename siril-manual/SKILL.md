---
name: siril-manual
description: >-
  Query and read the pinned, integrity-verified Siril 1.4.4 offline manual
  (commit 1550a31d325276124fe961368477c90d49df804b) with bilingual zh-CN/en
  search, exact command syntax lookup, and reStructuredText include resolution.
  Use when the user asks about Siril 1.4.4 commands, parameters, GUI/CLI
  options, PixelMath functions, SPCC/PCC details, or astrophotography theory
  documented in the official Siril manual without network access. Do not use
  for executing FITS/XISF pixel processing pipelines (route pixel execution to
  starun-siril).
license: NOASSERTION
compatibility: >-
  Python 3.10+ standard library only; completely offline, zero network or
  subprocess execution at query time.
metadata:
  slug: siril-manual
  version: "0.1.0"
  displayName: Siril 1.4.4 Offline Manual
  summary: Integrity-verified offline query and section reader for official Siril 1.4.4 documentation.
  tags: "astronomy, astrophotography, siril, documentation, manual, offline-reference"
  homepage: https://github.com/MZqk/deepsky-skill
---

# Siril 1.4.4 离线手册技能（`siril-manual`）

本技能提供固定到官方 Siril `1.4.4`（Git 提交 `1550a31d325276124fe961368477c90d49df804b`）的纯文本离线手册组件（位于 `assets/siril-manual/`）及无副作用完整性校验检索器 `scripts/query_siril_manual.py`。

## 职责边界

- **适用场景**：离线解答 Siril `1.4.4` 命令语法、参数含义、PixelMath 函数表、SPCC/PCC/GHS/反卷积原理、GUI 与无头脚本机制等纯知识与文档查询问题。
- **不适用场景**：实际对 FITS/XISF 主文件执行深空后处理流水线（请路由至 `$starun-siril`）。本技能只提供官方文档检索，不授予任何自动脚本执行权限。

## 快速使用

详细 CLI 参数、输出结构与退出码见 [离线手册查询参考](references/manual-query.md)。

1. **首次或验收时校验组件完整性**：
   ```bash
   python3 -B scripts/query_siril_manual.py --verify-bundle
   ```
2. **中英文关键词检索定位**：
   ```bash
   python3 -B scripts/query_siril_manual.py "背景提取" --top 5
   python3 -B scripts/query_siril_manual.py "SPCC" --format text
   ```
3. **精确命令查询与章节原文读取**：
   ```bash
   python3 -B scripts/query_siril_manual.py --command autostretch --format text
   python3 -B scripts/query_siril_manual.py --read doc/Commands.rst --format text
   python3 -B scripts/query_siril_manual.py --read section:<id> --format text
   ```

## 安全与许可约束

- 每次查询均通过 `O_NOFOLLOW` 与 SHA-256 树摘要校验 `assets/siril-manual/` 完整闭包，不访问网络、不启动子进程、不在技能目录写入缓存。
- `assets/siril-manual/` 保留上游 `GPL-3.0-only` 与 MuniPack 派生段落 GNU FDL 声明（整体组件许可状态 `NOASSERTION`，详见 `THIRD_PARTY_NOTICES.md`）。
