# Photoshop 天文后期处理工作流程

> **扩展阅读，不是权威来源。**
> 本文件只描述 Photoshop 的**菜单路径、图层习惯与软件特有事项**。
> 操作内容（工具、步骤、参数依据、蒙版策略）以 `scripts/software_guidance.py` 为唯一真相源；
> 目的 / 起始策略 / 调整原则 / 阶段验收 / 回退条件以 `scripts/generate_advice.py` 为唯一真相源；
> 阶段定义见 `references/pipeline_stages.md`。**修改本文件不会改变生成结果。**
>
> Photoshop 在本技能中是**下游二次加工阶段**，不承担校准、注册、叠加、背景建模、
> 测光校色与窄带通道合成。

## 简介

Photoshop 是通用图像编辑器。它的优势在图层化、可逆的收尾精修；
天文数据的测量类操作不在这里做。

## 阶段与操作的对应关系

| 阶段 | 报告中的操作 | Photoshop 入口 |
|---|---|---|
| linear | 校准、注册、叠加、裁切、背景建模、测光校色、窄带映射、线性降噪、星形诊断 | **不在 Photoshop 处理**，回主轨 |
| nonlinear | 受控非线性拉伸（仅收尾微调） | 多层 Curves 调整图层；High Pass + 柔光做局部对比 |
| nonlinear | 亮核与高光保护 | 亮度蒙版 + Curves / Camera Raw Highlights |
| nonlinear | 星点处理 | 外部星点层 + Minimum（Preserve Roundness）+ 图层不透明度 |
| finishing | 色彩精修 | Vibrance / Selective Color / Color Balance 调整图层 |
| export | 母版保存与最终导出 | 分层 16-bit PSD/TIFF；Convert to Profile；受蒙版保护的输出锐化 |

每个操作的具体步骤、参数选择依据与验收条件不在本文件，见文首说明的来源。

## Photoshop 特有的注意事项

### 不要用像素级修补改天区

仿制图章、修复画笔、内容识别填充、生成式填充会删除或伪造真实星云、IFN、暗尘埃
与微弱星系结构。背景与梯度问题回主轨处理，这里只做已确认的轻微低频残差。

### 调整图层优先于破坏性调整

用调整图层 + 蒙版，而不是「图像 → 调整」直接改像素。这样每一步都可逆、可对比、
可随时关闭查看原始状态。需要应用滤镜时，先转换为 Smart Object。

### 色彩空间：Convert 而不是 Assign

导入 16-bit TIFF 时保持上游嵌入的配置文件。交付时用 **Convert to Profile** 转换副本，
不要用 Assign Profile 强行指定——后者会让颜色看起来变了，但数据没有真正转换。

### 星点层应来自外部

准确的星点层由主轨（StarNet / StarXTerminator）生成。在 Photoshop 里自行构建星点蒙版
容易把星云亮结和星团成员误判为星点。

### 亮度蒙版的构建入口

- 通道面板载入 RGB 亮度选区 → 选择 → 存储选区 → 修改 → 羽化
- 或使用第三方插件：Lumenzia / Raya Pro / TK Luminosity Masks

### Astronomy Tools Action Set

安装：窗口 → 动作 → 载入动作（`.atn` 文件）。

与报告操作相关的动作：**Make Stars Smaller**、**Create Star Mask**、
**Enhance DSO and Reduce Stars**。这些都是**星点处理**的入口，
不要用它们替代主轨的背景与色彩处理。

## 图层组织建议

自下而上建议：原始图像（保持不动，作为对照）→ 各项调整图层 → 星点层 → 输出锐化层。
保留一个未被修改的底图，便于随时开关对比。

## 性能与内存

编辑 → 首选项 → 性能：

- 内存使用与历史记录状态按本机内存和图像尺寸调整，不必照搬固定值；
- 缓存拼贴大小按图像尺寸调整；
- 大图优先用 Smart Object 承载滤镜，避免多份完整副本。

## 输出与保存

- **母版**：保留分层 16-bit PSD/TIFF，不要对母版做输出锐化或缩放。
- **交付副本**：Convert to Profile → 缩放 → 受蒙版保护的输出锐化 → 嵌入配置文件导出。
- **打印**：图像 → 模式 → CMYK，并用色域警告检查超出色域的颜色。
- 导出后检查是否出现色带、暗部或高光裁切。

## 快捷键

```
- Ctrl+J: 复制图层
- Ctrl+Shift+N: 新建图层
- Ctrl+Alt+Shift+N: 新建空白图层
- Ctrl+[: 向下移动图层
- Ctrl+]: 向上移动图层
- Ctrl+G: 图层编组
- Ctrl+Alt+G: 创建剪贴蒙版
- Ctrl+点击图层缩略图: 载入图层透明度选区
- Q: 快速蒙版模式
- X: 切换前景/背景色
- D: 恢复默认颜色（黑/白）
```

## 与主轨的衔接

- 从 Siril 或 PixInsight 导出 **16-bit TIFF**，并记录其色彩空间。
- 报告默认给出 Siril 与 PixInsight **两条并列主轨**，任选其一执行；
  Photoshop 只在这条主轨的 `export` 之后介入。
- 交接的位深、色彩空间、已完成项与不可逆项，见报告的「交接给 Photoshop 的契约」一节。

## 学习资源

- Photoshop 天文处理教程：https://www.youtube.com/c/AstrophotographyTV
- Astronomy Tools 官方教程
- PixInsight 论坛 Photoshop 板块
- Cloudy Nights 论坛 Photoshop 专区
