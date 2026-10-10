# Siril 天文后期处理工作流程

> **扩展阅读，不是权威来源。**
> 本文件只描述 Siril 的**菜单路径、进程名、文件命名与脚本自动化**等软件特有知识。
> 操作内容（工具、步骤、参数依据、蒙版策略）以 `scripts/software_guidance.py` 为唯一真相源；
> 目的 / 起始策略 / 调整原则 / 阶段验收 / 回退条件以 `scripts/generate_advice.py` 为唯一真相源；
> 阶段定义见 `references/pipeline_stages.md`。**修改本文件不会改变生成结果。**

## 简介

Siril 是免费开源的天文图像处理软件，覆盖校准、注册、叠加与基础后期，脚本化能力强，
适合作为免费主轨。

## 阶段与操作的对应关系

| 阶段 | 报告中的操作 | Siril 入口 |
|---|---|---|
| linear | 校准、选帧与叠加 | 「转换/序列」→「预处理」→「注册」→「序列图」→「叠加」 |
| linear | 裁切无效边缘 | 「图像处理」→「裁切」 |
| linear | 背景与梯度处理 | 「图像处理」→「背景提取」/ RBF 背景提取 |
| linear | 宽带色彩校准 | 「图像处理」→「天体解算」→ Photometric Color Calibration |
| linear | 窄带通道映射 | 「图像处理」→ 通道提取 / Pixel Math / RGB 合成 |
| linear | 线性阶段降噪 | 「滤镜」→ 小波变换（Wavelets） |
| linear | 星点形态诊断 | 序列图（FWHM/圆度）、注册结果、中心与四角对比 |
| nonlinear | 受控非线性拉伸 | 「图像处理」→ GHS / Asinh / 直方图变换 |
| nonlinear | 亮核与高光保护 | GHS 保护参数、范围蒙版、Pixel Math 融合 |
| nonlinear | 星点处理 | StarNet（需单独安装）、星点蒙版、形态学处理 |
| finishing | 色彩精修 | 「色彩」→ 色彩饱和度 / 色彩平衡 |
| export | 母版保存与最终导出 | 「保存」为 32-bit FITS；「导出」16-bit TIFF |

每个操作的具体步骤、参数选择依据与验收条件不在本文件，见文首说明的来源。

## Siril 特有的注意事项

### CFA / Bayer 处理顺序

Siril 的预处理要求**在校准前保持 CFA 数据未去马赛克**，校准完成之后才按正确的
Bayer 排列去马赛克。顺序颠倒会破坏校准精度，这一点与部分其他软件的默认流程不同。

### 序列图（Plot）

叠加之前用序列图评估每一帧的 FWHM、圆度、背景与注册质量。
它是**帧间相对质量排序**工具，不能当作绝对视宁度测量。

### 拒绝算法不是固定参数

拒绝算法由有效帧数和异常值分布决定，不要固定套用同一组 sigma。
叠加后必须查看 rejection map，确认被拒绝的是卫星轨迹、热像素等异常，
而不是真实星核或目标结构。

### 已知限制

- 色彩校准依赖星表求解结果；求解失败时应先修正焦距、像元尺寸与中心坐标，
  而不是用白平衡掩盖。
- 去星依赖外部 StarNet；未安装时只能做形态学处理。

## 命令行与脚本自动化

Siril 支持 `.ssf` 脚本，可把预处理到叠加整条链路批量化：

```bash
requires 2.0.0
load Darks
convert Darks dark
load Flats
convert Flats flat
load Bias
convert Bias bias
load Lights
convert Lights light
preprocess light -opt -debayer
register pp_light
stack r_pp_light -nonorm -out=stacked
```

## 输出文件命名规则

- `pp_light_*.fits`：预处理（已校准）后的亮场
- `r_pp_light_*.fits`：注册后的图像
- `stacked.fits`：叠加结果
- `result_*`：各类处理的中间结果

## 与 PixInsight / Photoshop 的衔接

- 在 Siril 完成预处理与后期后，保存 **32-bit FITS** 母版；需要二次加工时导出 **16-bit TIFF**。
- 报告默认给出 Siril 与 PixInsight **两条并列主轨**，任选其一执行，不要串联叠加。
- 交给 Photoshop 的位深、色彩空间与不可逆项要求，见报告的「交接给 Photoshop 的契约」一节。
