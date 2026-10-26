# 97796c637aa53713edc846bd0a39470d687f0fdf 派生记录

作者：本工程维护 / Codex；日期：2026-10-13。

| 编号 | 来源 / 派生路径 | 原因与影响 |
|---|---|---|
| P01 | source-manifest.json 所列 34 文件 → derived/ 同路径 | 原样复用 chunker、融合、评测、类型、提示模板与 LICENSE；无内联修改 |
| P02 | derived/go.mod | 只编译实际抽取范围；保留上游模块名以兼容 internal 导入；不引入上游整站依赖 |
| P03 | derived/internal/types/host.go | 只保留融合/评测所需载体，避免引入租户、数据库和 API 模型 |
| P04 | derived/internal/application/service/host.go | 导出受管调用入口，内部执行原样 RRF |
| P05 | derived/internal/application/service/metric/sets.go | 评测集合辅助定义，避免导入无关依赖 |
| P06 | derived/internal/logger/adapter.go | 宿主日志写 stderr，stdout 专供协议，不污染 JSON 响应 |
| P07 | 上游 internal/infrastructure/docparser/image_resolver.go → derived/internal/infrastructure/docparser/linked_images.go | 局部抽取链接图片解包；不引入联网图片抓取；沿用上游 MIT 通知 |
| P08 | derived/cmd/cognition-runtime/main.go | 新增版本化 stdio 协议、实际模型调用、校验与失败语义、固定小数向量、原文锚点、Wiki/概念候选与指标输出 |
| P09 | platform_server/knowledge_runtime.py | 宿主绑定账号/模型空间、加密凭据、持久缓存、向量维度保护；不改变其他两脑向量空间 |
| P10 | brains/knowledge/src/runtime_adapter.py、pipeline.py、brain.py | UTF-8 桥接、父子块上下文、授权候选接 WeKnora 融合/重排；统一证据与拓扑仍归本工程 |

完整上游 parent_child_offsets_test.go 依赖未抽取的文档服务类型，未复制进最小模块；其余复制的 chunker 自测执行，另外以 Python 调真实编译进程覆盖多页父子关系、原文锚点、模型协议与恢复缓存。

本次没有新增外部 Go 模块依赖；标准库许可证随 GO_LICENSE.txt 保留。宿主 Python / Node 依赖见项目 requirements 与 package-lock；不能用本表代替整个产品依赖审查。

实际边界：PDF/DOCX 解码沿用宿主；词法/向量候选预筛由宿主产生；当前向量精确扫描；Wiki 为资料摘要，图谱为概念至原文锚点候选。完整图谱关系治理、Wiki 编辑、ANN 与分布式运维不是本补丁完成项。

源码与二进制逐项摘要见 ../../build-manifest.json，原样上游文件摘要见 ../../source-manifest.json。
