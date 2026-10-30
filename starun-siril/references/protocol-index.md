# Protocol Index

拉伸协议默认使用 [主体优先多节入口](stages/stage-07-stretch.md#subject-priority-stretch-v27)，参考 [五类形态起始链](stages/stage-07-stretch.md#morphology-starting-chains)，复用 [ROI 保护](stages/stage-07-stretch.md#roi-protected-stretch-v22)与显式 v4 因子提案。独立单链比较只在显式选择时执行；不增加协议或自动优化器。


用于按用户请求定位单个协议正文，不规定阶段顺序。正文文件是唯一 primary，锚点只用于定位，SHA-256 绑定整个文件。tone.adjust 与 color.finish（全局及局部饱和度）分别唯一维护在 shared-protocols；其他协议由所属阶段维护。完整处理按 [阶段顺序](stage-sequence.md) 编排，任务模式见 [入口](../SKILL.md#定向模式)。新绑定不要求 Stage 10 primary 或 Stage 8 supporting；历史绑定按 [兼容说明](session-contract.md#文档指导版本) 原样复核。

| 协议 | 何时读取 |
|---|---|
| [input.inspect](stages/stage-01-input-inspect.md#input-inspect) | 新 session 实际执行前建立可读输入证据；已有同图且已接受检查可复用 |
| [geometry.crop-near-black](stages/stage-02-geometry.md#geometry-crop-near-black) | 可见近黑边、堆栈边或构图边界需要保守裁切 |
| [background.subtract](stages/stage-03-background.md#background-subtract) | 完整处理的线性图必须完成控制区与采样评估；条件合格时实际试跑一至四阶多项式或 RBF，独立验证后采用或保留父源 |
| [astrometry.solve](stages/stage-04-astrometry-color.md#astrometry-solve) | 保留有效 WCS，或按在线/离线边界解析缺失的 WCS |
| [color.calibrate](stages/stage-04-astrometry-color.md#color-calibrate) | 已确认线性通道和响应/滤镜证据；先完成路线、实际父源、适用区域和显示参数准备，统一路径并静态校验后执行 |
| [restoration.denoise](stages/stage-05-linear-restoration.md#restoration-denoise) | 线性背景噪声影响后续拉伸 |
| [restoration.deconvolve](stages/stage-05-linear-restoration.md#restoration-deconvolve) | 已接受线性父源需要恢复细节，并有足够可靠星点支持 PSF |
| [stars.separate](stages/stage-06-stars-separation.md#stars-separate) | 按目标类型、用户目标和运行能力评估线性分星 |
| [stretch](stages/stage-07-stretch.md#stretch) | 线性科学父源需要转为非线性显示图 |
| [structure.local-contrast](stages/stage-08-starless-enhance.md#structure-local-contrast) | 已审查的非线性 starless 主体需要局部亮度对比增强 |
| [color.map](stages/stage-08-starless-enhance.md#color-map) | 已接受非线性 starless 父源具有可靠窄带角色，需要 palette 映射 |
| [tone.adjust](shared-protocols/tone-adjust.md#tone-adjust) | 已接受非线性主体或最终图需要背景、中间调或亮核整形 |
| [stars.recompose](stages/stage-09-stars-recompose.md#stars-recompose) | 已接受 starless 分支需要按冻结传递链合星 |
| [restoration.denoise-nonlinear](shared-protocols/denoise-nonlinear.md#restoration-denoise-nonlinear) | 非线性父源需要保守原生降噪 |
| [color.finish](shared-protocols/color-finish.md#color-finish) | Stage 8 可信非线性 RGB FITS 无星主体需要局部饱和度，或 Stage 10 最终三通道父源需要轻量显示调色 |
| [delivery.render](stages/stage-10-delivery-finishing.md#delivery-render) | 已接受最终非线性父源需要生成 JPEG 交付候选 |


双窄带独立提取/拉伸使用 [stretch 的严格 HOO 变体](stages/stage-07-stretch.md)，仍绑定 Stage 7 为唯一 primary；不新增 public protocol，不借用 color.map 宣称物理解混。


Stage 7 按需附文：[HDR](stage7-hdr.md)、[HOO](stage7-hoo.md)、[诊断示例](stage7-diagnostics.md)、[历史与兼容](stage7-legacy.md)。普通拉伸只读当前主文档；变体与诊断材料按实际使用绑定 supporting，历史材料不授权新执行。

## Multiscale local contrast v21

structure.local-contrast 的小波与 CLAHE 为同一协议的变体，详见 [Stage 8 多尺度导引](stages/stage-08-starless-enhance.md)。不新增协议或流程编排器。
