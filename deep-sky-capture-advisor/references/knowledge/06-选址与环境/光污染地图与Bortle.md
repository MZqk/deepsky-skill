---
type: "Planning Reference"
title: "光污染地图与Bortle"
description: "光污染地图提供区域夜光或模型估计，Bortle 等级描述现场视觉条件；两者可用于拍摄点初筛，但不能替代现场天空背景、地平线灯光、透明度和安全检查。"
category: "06-选址与环境"
tags: ["选址", "入门", "光污染", "Bortle"]
difficulty: "新手"
audience: "刚入门、准备第一次出摊或寻找拍摄点的深空爱好者"
status: stable
created: "2025-07-10"
updated: "2026-08-27"
stale_after: "2027-01-30"
generated:
  by: process:knowledge-optimization
  at: "2026-08-01T19:00:00+08:00"
review:
  state: needs-human-review
  owner: knowledge-base-maintainer
applies_to:
  系统: ["使用地图和现场观测评估深空拍摄天空背景的规划场景"]
  条件: ["地图数据日期、分辨率、天气、局地灯光和实际天空测量共同解释"]
  不适用: ["把地图颜色或 Bortle 等级当作精确实时测量", "据此保证成像结果"]
sources:
  - id: raw-research
    resource: "/raw/素材调研报告.md"
    title: "深空天文拍摄知识库素材调研报告"
    evidence_level: internal-ledger
    rights: unknown
    usage: metadata-only
    accessed_at: "2026-08-27"
  - id: src-24170615
    resource: "https://lightpollutionmap.info/"
    title: "Light Pollution Map 官网"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-132241b7
    resource: "https://astrophotographylens.com/blogs/astro/bortle-scale"
    title: "Bortle Scale 详解（Astrophotography Lens）"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-30da6572
    resource: "https://baike.baidu.com/item/%E6%B3%A2%E7%89%B9%E5%B0%94%E6%9A%97%E7%A9%BA%E5%88%86%E7%B1%BB%E6%B3%95/3811822"
    title: "波特尔暗空分类法（百度百科）"
    evidence_level: secondary
    rights: unknown
    usage: link-only
  - id: src-e93231aa
    resource: "https://astrocompare.com/narrowband-filters-light-pollution-guide.html"
    title: "城市窄带滤镜应对光污染指南（AstroCompare）"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"

---
# 光污染地图与Bortle

**摘要**：光污染地图适合比较区域趋势，Bortle 等级适合描述现场视觉条件，SQM 则是特定方向和时刻的亮度测量。三者不是可无损互换的单一刻度；规划时还要结合目标光谱、月亮、透明度、局部灯光、地平线遮挡和现场安全。[^src-24170615]

## 背景 / 适用场景
适用场景：选择拍摄地点、判断本地是否值得架设设备、规划长途追星行程。前置条件：了解自己的经纬度或目标地点。

## 核心知识点
- Bortle 暗空分类法使用 1~9 级描述肉眼可见现象和天空环境，数字越小通常代表越暗；它是观察描述，不是由一个地图像素或 SQM 数值自动换算出的精确等级。[^src-132241b7]
- SQM 常以 mag/arcsec² 表示某一方向、时刻和仪器条件下的天空亮度。可用经验范围辅助比较，但不应把 Bortle 与 SQM 写成固定一一对应表。
- 光污染地图使用卫星观测或模型估计区域夜光，适合找候选方向和比较相对趋势。地图图层、年份、分辨率与显示算法会变化，使用前应阅读站点说明。[^src-24170615]
- 窄带或双窄带滤镜可提高部分发射线目标在亮背景下的对比，但效果取决于目标光谱、滤镜带宽、相机响应、月亮与当地光源；“Bortle 6~8 也能拍”不是画质或曝光保证。[^src-e93231aa]
- 星系和反射星云含有大量连续谱信号，窄带不能替代宽带数据。是否转移到更暗地点，应同时评估目标高度、天空背景、交通与安全，而不是设定统一的 Bortle 4 门槛。

## 权威问答口径

- 本页可承担的回答范围：使用地图和现场观测评估深空拍摄天空背景的规划场景；成立条件：地图数据日期、分辨率、天气、局地灯光和实际天空测量共同解释。
- 遇到以下情况必须拒绝确定结论或转入专项页/实测：把地图颜色或 Bortle 等级当作精确实时测量；据此保证成像结果。
- 具体数值、兼容性、软件行为或安全结论只使用正文就近绑定的来源，并同时受 `stale_after` 与人工 `verified.scope` 限制。


## 注意事项
- 地图数据为卫星反演的平均背景亮度，局部树荫、地面灯、地平线附近辉光会让实际体验差于地图数值，务必到现场用 SQM 或手机测光复核。
- 同一地点的现场表现会随月亮、透明度、气溶胶、积雪和局部照明变化；地图历史图层本身不会随当晚条件实时变化。
- 不要把'地图显示暗'等同于'适合拍'：还需同时看视宁度、云量、灰霾与wind。
- 窄带滤镜会显著改变色彩平衡，后期需做窄带配色（SHO / HOO 等）与连续谱校准，新手需预留学习成本。

## 示例
- 出摊前：用地图找两个候选点，记录图层和数据年份；再比较目标方向的地平线灯光、遮挡、月亮、天气、合法停车与撤离条件。到场后用测试帧背景和现场观察决定是否继续。
- 长途追星：地图只做初筛。不要因为颜色更暗就默认山地、保护区或偏远道路可进入、可停车或更安全；出发前核对开放规则、天气和通信。
- 滤镜选择：先确认目标是发射线还是连续谱，再查滤镜透过曲线与相机响应；用短测试序列比较背景、星色、晕圈和目标信号，避免仅凭 Bortle 数字购买。

## 相关资源
- [Light Pollution Map（在线光污染地图，Bortle/SQM）](https://lightpollutionmap.info/)
- [Light Pollution Map App（含银河可见性）](https://lightpollutionmap.app/)
- [天文通（微信小程序，含光污染/波尔特等级）](https://twtapp.com/)
- [Bortle 暗空分类法（百度百科）](https://baike.baidu.com/item/%E6%B3%A2%E7%89%B9%E5%B0%94%E6%9A%97%E7%A9%BA%E5%88%86%E7%B1%BB%E6%B3%95/3811822)

## 信息来源
- [Light Pollution Map 官网](https://lightpollutionmap.info/)
- [Bortle Scale 详解（Astrophotography Lens）](https://astrophotographylens.com/blogs/astro/bortle-scale)
- [波特尔暗空分类法（百度百科）](https://baike.baidu.com/item/%E6%B3%A2%E7%89%B9%E5%B0%94%E6%9A%97%E7%A9%BA%E5%88%86%E7%B1%BB%E6%B3%95/3811822)
- [城市窄带滤镜应对光污染指南（AstroCompare）](https://astrocompare.com/narrowband-filters-light-pollution-guide.html)


## 关联知识

- [视宁度-透明度-云量](/06-选址与环境/视宁度-透明度-云量.md)
- [月相-季节窗口-远程台](/06-选址与环境/月相-季节窗口-远程台.md)

## 维护记录
| 日期 | 修改人 | 变更说明 |
|------|--------|----------|
| 2026-08-01 | `process:knowledge-optimization` | 区分地图、Bortle 与 SQM，移除固定换算和 Bortle 阈值，并把地图与滤镜主张就近关联到来源。 |
| 2025-07-10 | 知识库助手 | 基于互联网调研初稿 |
