# M5 结构化数据推理脑

本目录已有 `StructuredBrain` 适配器、`client` 和 `mapping`，将统一 `BrainQuery` 受控翻译为既有引擎请求，再保真映射为 `BrainResult`。无命中返回 `no_match: true` 与空 `claims`，引擎不可达返回 `ENGINE_UNAVAILABLE`，绝不伪造数值。

WeKnora 不进入本脑，也不改写其数据集、精确计算、CNTS 或运行配置档。跨脑关联只把经过审计的公共锚点编译为本脑查询约束；本脑仍独立鉴权、计算并返回自身证据。控制面未注册本脑时返回显式 `BRAIN_NOT_CONNECTED`。
