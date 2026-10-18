# Physical metadata and astrometry priors

## Capture metadata

The normalized metadata object includes:

- `exposure_seconds`
- `exposure_kind` (`unknown`, `integration`, or explicitly confirmed `subexposure`)
- `subexposure_seconds` when independently established
- `gain`
- `sensor_temperature_c`
- `filter` and `filter_profile`
- `telescope`
- `camera`
- `object`
- `ra` / `dec`
- `date_obs`
- binning, pixel size, focal length, and aperture when available

Treat metadata as evidence, not ground truth. FITS writers use inconsistent
keywords and units.

Filter classification and channel analysis share the stdlib-only
`scripts/filter_metadata.py` (also shipped identically with `starun-siril`).
`FILTER`, `FILTERID`, and `FILTNAME` are checked in that order, skipping blanks.
The filter profile retains the raw value, Header key, and mapping source.
Whitespace, separators, and Unicode emission-line names are normalized before
matching complete tokens. Generic `dual/duo` names alone do not establish H-alpha/OIII;
explicit combinations retain their actual lines. Bare `LP` is recognized only
with a supported Seestar model in the Header and its documented filter mapping;
other devices and conflicting wide-camera metadata remain unknown.

Pixel geometry remains separate: a three-channel dual-band master is RGB, and a
one-channel mixed-band master is mono. Only a single documented line can name a
mono channel `ha`, `oiii`, or `sii`. Filenames do not establish spectral roles.
Neither filter classification nor channel count establishes an unstretched input.

## Physical interpretation rules

- Warm sensor temperature is direct evidence of elevated dark current.
- Numeric gain is camera-dependent. High gain often reduces highlight headroom;
  it does not universally increase read noise.
- Long subexposures require stronger star-core and highlight protection.
- Short subexposures increase sensitivity to read-noise accumulation.
- Dual-band metadata activates emission color handling, moderate SCNR, and
  H-alpha/OIII preservation.
- Do not claim true HOO/SHO synthesis from one OSC image unless a documented
  channel-separation model and filter response are available.

User overrides take precedence over physical priors. Record both in the result.

Only an explicit subexposure duration drives short/long-subexposure hints.
Unqualified `EXPTIME` remains ambiguous. Recognized stacked DWARF headers and
`NCOMBINE > 1` describe integration; a filename-derived duration is only a hint.
TIFF intermediates retain source metadata and their domain in `.meta.json`
sidecars, and Agent sessions carry the same source metadata across steps.

## Plate solving and object association

Astrometry.net `solve-field` establishes the image WCS: center coordinates,
pixel scale, orientation, and field size. It does not by itself identify every
object in the field.

Supply a catalog JSON to associate objects:

```json
[
  {
    "name": "NGC6888",
    "type": "emission_nebula",
    "ra_deg": 303.027,
    "dec_deg": 38.354
  }
]
```

The projector returns `pixel` and `normalized_center` for objects inside the
frame. Agent actions may then use:

```json
{
  "operation": "run_step",
  "step": "local_enhance",
  "object_name": "NGC6888",
  "object_radius": 0.12,
  "local_strength": 0.2
}
```

Do not describe the field inventory as complete unless the supplied catalog is
known to be complete to the required magnitude and object classes.

## 叠加母版的时间与温度字段

- `EXPTIME` 在叠加母版中可能是累计积分，不能未经核实当作单帧曝光。
- DWARF 的 `DET-TEMP` 可提供传感器温度。
- Hα 主导图须检查每通道近饱和占比，不能只看平均亮度裁切；`stretch_min_p99=0.5` 与
  饱和度/强风格叠加可能压平红通道亮部。
- 线性中间阶段保留线性样本，不运行成片背景锁定；线性审查不采用显示域暗部亮度或绝对
  平滑度阈值。


## Explicit DWARF selected filters

Model evidence comes from INSTRUME, TELESCOP, TELESCOPE, CREATOR and ORIGIN. Complete DWARF mini,
II/2 and 3/III names (including normalized DWARFLAB prefixes and Roman numerals) add device_model
only when unique. No filter selection is inferred from a device, filename or pixel distribution.
Explicit line names and supported external filters precede device-specific mappings.

Mini/3 Duo-Band aliases map to dual_band with manufacturer main H-alpha/OIII passbands. Mini Astro,
II VIS and 3 Astro/VIS (optional Filter suffix) map to broadband with no named lines. II IR Pass,
Astro and generic Duo-Band retain generic classification; II generic Duo-Band does not establish
H-alpha/OIII. Bare DWARF LP stays unknown. Unknown/wide CAMERA/LENS/LENSNAME and conflicting known
models suppress telephoto mappings; absent lens fields allow a unique-model, selected-filter match.

Successful profiles preserve matched_by=device_filter_mapping and the official reference URL:
[mini manual](https://help.dwarflab.com/en/docs/DWARF-mini-Smart-Telescope-User-Manual),
[II/3 comparison](https://www.dwarflab.com/gb/pages/astronomy-with-dwarf-3-smart-telescope),
[3 manual](https://help.dwarflab.com/en/docs/DWARF-3-Smart-Telescope-User-Manual-Part1-App-Interface-Introduction?product=dwarf-3).
Main passbands can support existing emission-color/ratio recommendations; they do not mean pixels
contain only these lines, identify mono channel roles, establish linearity or provide calibration
response curves. Three-channel mixed-band images remain RGB; one-channel mixtures remain mono.
