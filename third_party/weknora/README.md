# WeKnora 内嵌源码与运行时

2026-10-13 已完成真实源码锁定、抽取、编译和平台默认接入。

- 上游：[Tencent/WeKnora](https://github.com/Tencent/WeKnora/tree/97796c637aa53713edc846bd0a39470d687f0fdf)。
- 锁定提交：97796c637aa53713edc846bd0a39470d687f0fdf，状态见 UPSTREAM_COMMIT。
- upstream/：Git 受管稀疏快照，用于溯源；运行不依赖 Git 或上游 HTTP 服务。
- derived/：实际编译的派生模块；source-manifest.json 记录 34 个原样上游文件。
- bin/cognition-runtime.exe：本工程受管 stdio 进程，仅供知识脑调用，无独立产品入口。
- build-manifest.json：编译器、二进制及 Go 源文件哈希；build-weknora.ps1 每次构建检查原样源码没有漂移。

实际复用上游父子分块、标题/递归切分、RRF 融合、评测算法、摘要和概念提取提示模板。文档解码、授权过滤、模型凭据、向量缓存、智慧树、自生长和证据生命周期由本工程承载。真实 embedding/rerank/chat 经用户配置接口执行，缺失时显式失败。精确向量扫描尚未替换为大规模 ANN；摘要/概念候选不等于上游完整 Wiki/关系图谱产品。

同一嵌入空间的近邻驱动知识脑留痕、星团和智慧树 generation。WeKnora 不是另一个独立知识库或答案系统；Vue、账号/租户、通用会话与独立 Agent 产品层没有引入。结构化和时空脑算法保持各自体系，跨脑只传公共锚点，目标脑独立检索和验证。

部署及当前能力边界见 [平台说明](../../docs/production-platform.md)，首次使用见 [验收手册](../../docs/首次使用与生产验收测试手册.md)。核心单元测试仍保留 BuiltinKnowledgeRuntime；生产管理入口默认是 weknora-embedded-v1，禁止静默回退。

许可证见 [第三方通知](THIRD_PARTY_NOTICES.md)；适配记录见 [改动记录](patches/97796c637aa53713edc846bd0a39470d687f0fdf/CHANGE_LOG.md)。
