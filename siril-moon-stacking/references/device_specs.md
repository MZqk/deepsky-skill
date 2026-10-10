# 智能望远镜设备规格表

当视频 / SER 采集源没有写入 `FOCALLEN` / `APERTURE` / `XPIXSZ` 时，本技能用这张表提供**先验**，
让 `--drizzle auto` 的判定与 postprocess 的光学推断有可用输入，而不是退回 3.73µm / 80mm / 400mm
这类硬编码默认值。

> **先验不是实测。** 表里的数值来自厂商公布规格。任何由本表写入 FITS 头的值都会带溯源标记
> （`DEVICE` / `DEVSRC` / `DEVCONF` / `OPTPRIOR` / `OPTISRC`），下游报告会显示成
> `FITS Header (FOCALLEN) [device_table: seestar-s50]`，绝不会伪装成实测值。
> 独立于本表的实测手段是**月轮几何反推**（见 SKILL.md Step 4），它优先于本表。

## 收录范围

**仅智能望远镜**——固定光路、规格由厂商确定的整机。不收行星相机机身、不收「望远镜 + 减焦 + 相机」
这类套装、不收单反长焦组合：这些的组合空间是无穷的，查表不可靠，只能靠几何反推。

## 来源等级

| 等级 | 含义 |
| :--- | :--- |
| `official` | 厂商规格页公布 |
| `official+chip` | 厂商公布镜体规格，像元尺寸取自传感器 datasheet |
| `official+third_party` | 厂商公布焦距/口径，焦比与像元尺寸由第三方佐证 |
| `dealer+third_party` | 停产机型，来自区域经销商页面与拆解 |
| `disputed` | 厂商与第三方对传感器型号说法冲突 |

`sensor_status` 为 `confirmed` / `disputed` / `unpublished`。**只有 `confirmed` 的传感器才会被
当作识别信号**——存疑或未公布的机型不会被传感器反推命中。

## 规格表（长焦/天文主镜头）

| 设备 ID | 厂商 / 型号 | 焦距 | 口径 | 焦比 | 传感器 | 像元 | 来源等级 |
| :--- | :--- | ---: | ---: | ---: | :--- | ---: | :--- |
| `seestar-s50` | ZWO Seestar S50 | 250 mm | 50 mm | f/5 | Sony IMX462 | 2.9 µm | official |
| `seestar-s50-pro` | ZWO Seestar S50 Pro | 260 mm | 50 mm | f/5.2 | 官方未公布 | 2.9 µm | official |
| `seestar-s30` | ZWO Seestar S30 | 150 mm | 30 mm | f/5 | Sony IMX662 | 2.9 µm | official |
| `seestar-s30-pro` | ZWO Seestar S30 Pro | 160 mm | 30 mm | f/5.3 | Sony IMX585 | 2.9 µm | official |
| `dwarf-2` | DWARFLAB DWARF 2 | 100 mm | 24 mm | f/4.2 | Sony IMX415 | 1.45 µm | official+third_party |
| `dwarf-3` | DWARFLAB DWARF 3 | 150 mm | 35 mm | f/4.3 | Sony IMX678 | 2.0 µm | official |
| `dwarf-mini` | DWARFLAB DWARF mini | 150 mm | 30 mm | f/5 | Sony IMX662 | 2.9 µm | official |
| `dwarf-draco` | DWARFLAB DRACO | 340 mm | 90 mm | f/3.8 | OmniVision OV50Q40 | 2.394 µm | official |
| `vaonis-stellina` | Vaonis Stellina | 400 mm | 80 mm | f/5 | Sony IMX178 | 2.4 µm | official+chip |
| `vaonis-vespera` | Vaonis Vespera（停产） | 200 mm | 50 mm | f/4 | Sony IMX462 | 2.9 µm | dealer+third_party |
| `vaonis-vespera-pro` | Vaonis Vespera Pro | 250 mm | 50 mm | f/5 | Sony IMX676 | 2.0 µm | official |
| `vaonis-vespera-ii` | Vaonis Vespera II | 250 mm | 50 mm | f/5 | Sony IMX585 | 2.9 µm | official |
| `vaonis-vespera-pro-2` | Vaonis Vespera Pro 2 | 245 mm | 50 mm | f/4.9 | Sony IMX676 | 2.0 µm | official |
| `unistellar-evscope-2` | Unistellar eVscope 2 | 450 mm | 114 mm | f/4 | 存疑（见下） | 2.9 µm | disputed |
| `unistellar-equinox-2` | Unistellar eQuinox 2 | 450 mm | 114 mm | f/4 | 存疑（见下） | 2.9 µm | disputed |
| `unistellar-odyssey` | Unistellar Odyssey | 320 mm | 85 mm | f/3.9 | 官方未公布 | 1.45 µm | official |
| `unistellar-odyssey-pro` | Unistellar Odyssey Pro | 320 mm | 85 mm | f/3.9 | 官方未公布 | 1.45 µm | official |
| `celestron-origin` | Celestron Origin | 335 mm | 152 mm | f/2.2 | Sony IMX178 | 2.4 µm | official |
| `celestron-origin-mk2` | Celestron Origin Mark II | 335 mm | 152 mm | f/2.2 | Sony IMX678 | 2.0 µm | official |

查表：`python scripts/moon_stack.py devices`（JSON 全表）或 `... devices --id seestar-s50`（单项）。
用 `--device <id>` 可强制指定；`--device none` 可完全关闭。

## 存疑与未公布（务必按此理解）

1. **Unistellar eVscope 2 / eQuinox 2 传感器冲突**：厂商页面写作 Sony IMX224，但第三方拆解认定实为
   **Sony IMX347**（1/1.8″，原生 4.1MP，2.9µm），厂商宣传的 7.7MP 疑为插值放大。
   本表按 2.9µm 处理并标注 `disputed`，且**该机型不参与传感器反推**。
2. **ZWO Seestar S50 Pro 传感器未公布**：厂商只给 1/1.2″、8.3MP、2.9µm，未给型号。
   第三方分析指向 OmniVision OS08B10，但无厂商确认。
3. **Unistellar Odyssey / Odyssey Pro 传感器未公布**：厂商只给 85mm、320mm、f/3.9、1.45µm。
   Odyssey 与 Odyssey Pro 光学完全相同，Pro 仅多一个 Nikon 目镜。
4. **焦比与焦距/口径的舍入**：厂商公布的 f 值偶有舍入。例如 Odyssey 官方 f/3.9，而 320/85 = 3.76，
   差 3.5%。本表保留厂商原值；**实际参与计算的是焦距与口径**，焦比仅作展示。
5. **DRACO 双像元**：OV50Q40 原生 1.197µm，2×2 合并输出 2.394µm。写入 `XPIXSZ` 的是**合并后的
   2.394µm**（与实际输出帧一致）；原生值仅存于表中 `pixel_size_native_um` 供查阅。

## 未收录

| 机型 | 原因 |
| :--- | :--- |
| Vaonis Hestia | **无内置传感器**，通过手机摄像头成像，像元尺度取决于手机型号，不是固定的设备属性 |

## 识别信号与优先级

`scripts/device_specs.py` 的 `identify_device()` 按下列顺序匹配：

1. `--device <id>` 命令行覆盖（置信度 `user`，无条件采纳）
2. FITS 头 `TELESCOP` / `INSTRUME`（置信度 `high`）
3. 采集侧车 `.txt` 的 `SENSOR` / `TELESCOP` / `INSTRUME`（置信度 `medium`）
4. 文件名（置信度 `medium`）
5. 传感器型号回退（置信度 `low`，见下）

**型号特异性**：取匹配到的最长 token，因此 `Seestar S30 Pro` 命中 `seestar-s30-pro` 而不会退化成
`seestar-s30`；`Vespera Pro 2` / `Vespera Pro` / `Vespera II` / `Vespera` 同理。
**仅命中品牌**（例如头里只写 `DWARFLAB`）时只记录品牌，**不注入任何先验**。

**传感器回退的歧义规则**：

- 若共用该传感器的所有机型**规格一致**，采纳整组先验。
  例：IMX662 → Seestar S30 与 DWARF mini 都是 150mm / 30mm / f/5 / 2.9µm。
- 若规格不一致但**像元尺寸一致**，只回退像元尺寸并标记 `ambiguous`。
  例：IMX585 → S30 Pro(160/30) 与 Vespera II(250/50) 焦距口径冲突，但都是 2.9µm。
- 若品牌线索能把候选收窄到唯一机型，则采纳整组先验。
  例：IMX585 + 品牌 `Seestar` → 只剩 S30 Pro。
- 否则拒绝。例：IMX178 → Stellina 与 Origin 冲突（但像元同为 2.4µm，故仅回退像元）。

## 溯源标记

由本表写入 FITS 头的关键字（全部 ≤8 字符，符合 FITS 规范）：

| 关键字 | 含义 |
| :--- | :--- |
| `DEVICE` | 匹配到的设备 ID，如 `seestar-s50` |
| `DEVSRC` | 匹配信号：`cli` / `header` / `sidecar` / `filename` / `sensor` |
| `DEVCONF` | 置信度：`user` / `high` / `medium` / `low` |
| `OPTPRIOR` | **实际由本表填入**的光学关键字列表，逗号分隔，如 `APERTURE,FOCALLEN,XPIXSZ,YPIXSZ` |
| `OPTISRC` | `device_table`（整机先验）或 `sensor_pixel_only`（仅像元回退） |

`OPTPRIOR` 是精确到键的：若侧车已给出 `FOCALLEN=160`，则该键由侧车胜出、**不会**出现在
`OPTPRIOR` 中，其来源标注也保持为普通 FITS 头来源。**侧车与实测永远优先于本表。**

## 各输入路径的行为

| 输入 | 行为 |
| :--- | :--- |
| 视频（AVI/MP4/MOV） | 识别 → 把先验写入生成的 FITS 帧 + 溯源标记 |
| SER | 同上（从 SER 头的 instrument / telescope 字段识别） |
| FITS | **只识别不注入**。FITS 帧是用户原文件的软链接，技能红线禁止改写用户原始目录；识别结果写入 `import_receipt.json` 的 `device` 字段并打日志 |

FITS 输入路径下若确实需要这些先验，请用 `--focal` / `--pixel-size` / `--aperture` 显式传入，
或让 `--drizzle auto` 走月轮 ESF 实测判据（无需任何光学元数据）。
