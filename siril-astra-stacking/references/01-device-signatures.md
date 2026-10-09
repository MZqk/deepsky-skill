# 01 · 设备识别

> **定位**：设备识别是整条流水线的地基——判错一次，后续校准、筛帧、链路选择全部错配，而错配是静默的。
> **加载时机**：设备识别失败（退出码 2）、需要新增设备、签名表需要扩展时。
> **对应代码**：`scripts/device_signatures.py`

## 1.1 只用元数据，绝不用几何

允许作为证据的关键字：

| 关键字 | 说明 |
|---|---|
| `TELESCOP` | 设备名，但常带序列号后缀 |
| `INSTRUME` | 可能是设备名，**也可能是传感器型号** |
| `CREATOR` / `PRODUCER` | 拍摄软件 |
| `FILTER` / `IMAGETYP` / `XPIXSZ` | 辅助判据 |

**禁止**使用 `NAXIS1`/`NAXIS2`、位深、宽高比、像素数。理由：这些量在 Seestar S50（1080×1920）、S30 Pro（IMX585）、传统 OSC 之间存在重叠，无法唯一反推设备；而一旦猜错，`flats_available`、`dark_embedded` 等 profile 字段会套用到错误分支，产出看起来正常但已损坏的母版。拒绝则只需用户改一行配置。

## 1.2 真实头部（实测抓取，非文档）

### ZWO Seestar S30 Pro — M42 实测

```
TELESCOP = 'S30 Pro_10d57b1d'   ← 含设备序列号，不是通用型号名
INSTRUME = 'imx585  '           ← 传感器型号，不是设备名
FILTER   = 'IRCUT   '           ← 3 个尾空格
BAYERPAT = 'GRBG    '           ← 4 个尾空格
OBJECT   = 'M 42    '           ← 尾空格
EXPTIME  = 60.
FOCALLEN = 162.539022546324
XPIXSZ   = 2.90000009536743     ← IMX585
GAIN     = 80
CCD-TEMP = 9.9375
CREATOR  = <缺失>               ← S30 Pro 上根本没有这个关键字
EQUINOX  = 9.87654321E+107      ← Siril 自己写出的垃圾值
```

三个关键事实：

1. **`CREATOR` 不存在。** 早期设计假设查 `CREATOR='ZWO Seestar S50'`，在 S30 Pro 上会直接判失败。
2. **`INSTRUME` 是传感器型号。** S50 写 `'Seestar S50'`，S30 Pro 写 `'imx585  '`。
3. **`TELESCOP` 带序列号。** 正则必须匹配前缀而非全等。

### ZWO Seestar S50 — 社区抓取（对照）

```
CREATOR  = 'ZWO Seestar S50'   TELESCOP = 'Seestar S50'
INSTRUME = 'Seestar S50'       BAYERPAT = 'GRBG '
CCD-TEMP = 9.9375  GAIN = 80  FILTER = 'IRCUT '
STACKCNT = 1346  TOTALEXP = 13460.
XPIXSZ = 2.9  FOCALLEN = 250  APERTURE = 5.
```

`STACKCNT` 表示该帧本应是 live stacking 的第几帧，`TOTALEXP` 是累计曝光秒数。live stacking 已做逐帧质量筛选并加权，混有被判废的帧——这正是智能分支需要先筛后拒的原因。

### 逐帧 sub 与内部 master 的差异

| | 逐帧保存的 sub | 设备内部 master |
|---|---|---|
| NAXIS | 2（**未去马赛克 CFA**） | 3（已去马赛克 RGB） |
| 暗场 | 已内嵌 | 已应用 |
| 用途 | 本工具的目标 | 设备自出图 |

## 1.3 签名表结构

```python
{
  "id": "zwo_seestar_s30pro",
  "class": "smart",              # smart | traditional
  "description": "ZWO Seestar S30 Pro (IMX585)",
  "match": {                     # 全部满足才命中
      "TELESCOP": r"^S30\s*Pro",
  },
  "prefer": {                    # 收窄用，不阻断命中
      "INSTRUME": r"^imx585",
  },
  "any_of": [                    # 每组至少一项满足
      {"CREATOR": r"(?i)seestar|zwo"},
      {"PRODUCER": r"(?i)zwo"},
  ],
  "profile": { ... },            # 驱动全部下游参数
}
```

匹配顺序：`--device-profile` 覆盖 → `SIGNATURES` 顺序遍历 → **拒绝**。

`prefer` 只用于同前缀设备的区分，不参与阻断——否则固件改名就会导致识别失败。

## 1.4 profile 字段

| 字段 | 含义 | 影响 |
|---|---|---|
| `flats_available` | 设备是否产平场 | 智能望远镜为 false → 跳过 flat，报告记 `smart_scope_no_flat` |
| `dark_embedded` | 暗场是否已内嵌 | true → 不应用 master dark |
| `cfa_sensor` | 传感器型号 | 报告与诊断 |
| `mount` | altaz / equatorial | AltAz 有持续场旋转 |
| `field_rotation_per_frame` | 是否逐帧旋转 | AltAz 为 true |
| `mosaic_capable` | 是否支持拼接 | Seestar 支持 mosaic 模式 |
| `live_stacking` | 设备是否内部叠加 | true → 需先筛帧 |
| `min_pairs` | `register -minpairs` | 4（Siril 默认 8 对小视场不足） |

**未知 profile 字段一律报错，不填默认值。**

## 1.5 扩展新设备

在 `scripts/device_signatures.py` 的 `SIGNATURES` 列表**末尾**追加（顺序敏感，具体的在前）：

```python
{
    "id": "my_scope",
    "class": "traditional",
    "description": "My 80mm refractor on an ASI camera",
    "match": {"TELESCOP": r"^my80"},
    "prefer": {"INSTRUME": r"(?i)asi533"},
    "profile": {
        "flats_available": True,
        "dark_embedded": False,
        "cfa_sensor": "IMX533",
        "mount": "equatorial",
        "field_rotation_per_frame": False,
        "mosaic_capable": False,
        "live_stacking": False,
        "min_pairs": 6,
        "notes": "equatorial; field rotation only on meridian flips",
    },
},
```

调试步骤：

1. 用 `python3 scripts/fits_probe.py`（或直接 `read_fits_header`）打印真实头部。
2. 确认尾空格已被剥离——正则匹配的是剥离后的值。
3. 识别失败时报告的 `device.evidence` 会列出全部线索，据此写正则。
4. 跑 `python3 scripts/selftest.py` 确认没有破坏既有匹配。

## 1.6 不修改代码的覆盖方式

`--device-profile <json>` 接受 device id 到 profile 字段的映射：

```json
{
  "zwo_seestar_s50": { "min_pairs": 6, "flats_available": false }
}
```

**注意**：覆盖只能修改已匹配到设备的 profile 字段，**不能**用来让一个未列入签名表的设备通过识别——那仍然是拒绝。真正的扩展必须改 `SIGNATURES`。