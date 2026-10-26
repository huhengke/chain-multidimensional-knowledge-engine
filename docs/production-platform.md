# 三脑独立管理平台：部署与交付说明

更新时间：2026-10-19，关联主规划 V3.4。当前为独立单机实现，面向生产需求开发，尚未完成真实现场与完整运维验收。逐项操作见 [首次使用与生产验收测试手册](首次使用与生产验收测试手册.md)，新增模式与代码限制见[技术模式与实现现状](技术模式与实现现状.md)（[HTML](技术模式与实现现状.html)）。

## 1. 与原项目的关系已经修正

此前交付只迁了管理前端，通过接口调用原工程进程，未满足“独立项目”的要求。本次把结构化引擎、Agent、CNTS 源码迁入本项目，并改为本项目的身份、模型、配置、数据目录及启动流程。保留来源清单是为了追溯代码，不代表运行依赖。

    浏览器 → 本项目管理平台 :8079
                ├→ 本项目结构化引擎 :18000 → 本项目事实库 / 候选 CNTS Catalog
                ├→ 本项目 Node Agent :18001 → 本项目 MCP 工具 → 本项目结构化引擎
                ├→ 知识脑 / 智慧树
                │      ├→ WeKnora Lite 边车 :18090（HTTP；SQLite FTS5 + sqlite-vec + RRF）
                │      │      → 已注册的本地向量 / 重排模型（知识模型端点）
                │      └→ 规范知识单元、证据、发布与权限门禁（平台 SQLite）
                └→ 时空记忆推理脑 → 文件 / 摄像头 / RTSP 适配器

- services/structured/engine：数据源、导入注册、字段画像、基座特征、规则、精确聚合、运行配置、身份等实现。
- services/agent/src：会话、附件、Skill、MCP 工具、执行记录；新增本项目模型工具循环，不依赖旧 Harness 服务。保留的旧 Harness 适配源文件不是运行入口。
- services/cnts：Rust crates、Python 适配、Schema、CLI 与 Windows CPython 3.11 扩展。原项目业务库、历史账号、业务 Catalog、密钥均未复制。
- 新安装的可写入口是本项目空的 legacy_sqlite 数据库。CNTS 仍走“候选构建→验收→人工激活”，不把旧数据集当成本项目预置数据。

services/source-migration.json 记录首次迁入文件的原始哈希；后续宿主适配并不与原始哈希相等。独立运行不需要再次执行源码迁入脚本。

## 2. WeKnora 接入的准确范围

上游锁定提交：97796c637aa53713edc846bd0a39470d687f0fdf。34 个原样文件的哈希、许可证、派生构建信息都在 third_party/weknora/。

| 环节 | 当前实现 | 仍需区分的边界 |
|---|---|---|
| 文档解析 | 宿主 PDF/DOCX/文本提取；扫描页使用本地 RapidOCR，繁体竖排排序、逐页缓存与页锚点传给内部桥 | OCR 是机器识别，需抽查疑难字；未复制上游全部 docreader 服务 |
| 父子分块 | 实际 WeKnora heading、recursive、parent-child 算法 | 长表格、扫描件需真实资料验收 |
| 向量、索引、近邻 | 导入时平台生成规范知识单元，按账号投影到 WeKnora Lite：边车内 FTS5 全文索引 + sqlite-vec 向量近邻；查询由边车统一计算一次查询向量。原生索引就绪时生产检索默认走该路径 | sqlite-vec 当前是单机向量近邻，不是可横向扩展的 ANN；百万级、多机与混合负载仍需专项验收 |
| 混合召回与重排 | WeKnora hybrid-search 在边车内完成 FTS5 与向量召回及 RRF 融合；平台把 chunk 命中按字符区间映射回规范单元，再执行发布/权限过滤。重排默认 `adaptive`：强词项覆盖时跳过，头部不够明确时至多重排 5 个候选；另有 `off`、`always` | 重排调用 `.runtime/standalone/models` 的本地 bge-reranker HTTP 端点，约 1.2–1.4 秒/5 个长单元（CPU），不再是每次启动 bridge 进程；`always` 不适合作为默认交互模式 |
| Wiki 与图谱 | WeKnora 原生 Wiki 子系统：Codex 按文档生成摘要、实体/概念/综述页，页内保留 `[[双链]]`、分类路径、来源 `source_refs` 与 chunk 引用；平台转发页面、页面详情、关系图谱与统计，知识森林新增“知识 Wiki 与图谱”页签 | Codex 网关每用户只允许 1 路并发，后台 Wiki 合成通过 `WEKNORA_MODEL_MAX_CONCURRENCY=1` 串行，单批超时为 6 小时，知识库按单文档批次推进；Lite 的 SQLite 缺少 `similarity()`，语义去重退化为精确标题去重；全量覆盖校验见 [验证记录](standalone-verification.md) |
| 自生长 | 真实向量进入原知识脑留痕、星团、generation 与智慧树投影；完整拓扑保存可读快照，星团和跨脑边分页读取 | 页面可观察；星团拆分、合并、冻结的完整操作台尚未完成 |
| 评测与观测 | 页面指定问题和标准文档，调用 WeKnora Recall、MRR、NDCG，记录耗时/版本；另有本地对照基准（20 条原文短语 + 10 条整句，比较 native/legacy 与 rerank 开关） | 尚未完成真实标注集、并发基准与告警接入 |

WeKnora 仅影响知识脑，不替换另两脑的存储算法；三脑不共享原始向量。普通低风险知识继续自动生长，人工复核只用于风险与纠偏。缺少模型、维度变化、运行时失败都明确报错，不回退到哈希向量或测试结果。

## 3. 安装、启动、停止

当前电脑已构建前端和 WeKnora 二进制。首次部署到另一台机器时，在本项目根目录执行：

    python -m venv .venv
    ./.venv/Scripts/python.exe -m pip install -r requirements-platform.txt
    # 需要外部数据库时安装相应驱动；此文件包含四类支持的驱动
    ./.venv/Scripts/python.exe -m pip install -r requirements-datasources.txt
    Push-Location frontend
    npm.cmd ci --no-audit --no-fund
    Pop-Location
    Push-Location services/agent
    npm.cmd ci --no-audit --no-fund
    Pop-Location
    ./scripts/build-platform.ps1
    ./scripts/build-weknora.ps1
    # 构建轻量边车；使用本项目便携 Go / MinGW，不修改系统 PATH
    bash .runtime/weknora/build-lite.sh
    ./scripts/start-platform.ps1 -PythonExecutable './.venv/Scripts/python.exe' -NodeExecutable node

要求 Python 3.11（当前 CNTS 扩展是 Windows CPython 3.11）、Node 20+、Go 1.26+。其他 Python/系统平台需从 services/cnts 重建原生扩展。Go 与 MinGW 只在构建时需要；可直接使用 `.runtime/toolchains` 下的便携版本，源码已在本项目，不需部署 WeKnora Vue 或另起上游服务。轻量边车首次启动前必须存在 `.runtime/weknora/weknora-lite.exe`，否则启动脚本会明确失败。

本机经验证的命令：

    ./scripts/start-platform.ps1 -PythonExecutable '.runtime/platform-venv/Scripts/python.exe' -NodeExecutable 'D:/Program Files/nodejs/node.exe'

上述 Python 只是本机已安装依赖的解释器，不会启动 Hermes 或原结构化项目；正式部署推荐自己的 .venv。系统 Node 24.13 曾在打包时退出，已用上列 Node 24 构建通过。

访问 <http://127.0.0.1:8079/login>，创建自己的管理员。无默认密码、无演示资料。启动脚本依次拉起本项目四个服务，任一侧车或端口身份不符都会明确失败：

| 服务 | 地址 | 说明 |
|---|---|---|
| 管理平台 | 127.0.0.1:8079 | 浏览器入口与 API |
| 结构化引擎 | 127.0.0.1:18000 | 事实库与候选 CNTS Catalog |
| Node Agent | 127.0.0.1:18001 | MCP 工具与会话执行 |
| WeKnora Lite | 127.0.0.1:18090 | 知识索引与混合检索边车 |

边车配置在 `.runtime/weknora/weknora.env`：`EDITION=lite`、SQLite、本地文件存储，`NEO4J_ENABLE=false`、`ENABLE_GRAPH_RAG=false`、并发池 3。健康检查为 `GET http://127.0.0.1:18090/health`。日志在 `.runtime/standalone/weknora.stdout.log` / `weknora.stderr.log`，进程号写入 `.runtime/standalone/weknora.pid`。修改后端或边车配置后需重启：

    # 先在页面停止所有采集，等待正在进行的导入结束
    ./scripts/stop-platform.ps1
    # 然后执行上述 start-platform 命令

停止脚本核验安装路径，只停止本项目进程（含 18090 边车），保留所有数据。无需启动原工程的 8000/8001，也不会替你停止原工程。

## 4. 数据、权限与维护

- .runtime/standalone/installation.json：本安装的身份与内部服务密钥；请保管，不在聊天中发送。
- .runtime/standalone/structured/：事实数据库、声明、规则配置。数据库来源密码加密保存，不回显。
- .runtime/standalone/agent/：本项目会话、附件与 Skill；实际路径以 services/agent/src/config.js 为准。
- .runtime/standalone/platform/：元数据、SHA-256 原件、凭据加密密钥、知识向量和模型结果缓存。
- 知识、时空来源/资产/会话按账号隔离；结构化数据集沿用本安装共享角色权限，并非已实现租户级、逐行隔离。
- 管理员可创建、停用账号、重设密码和角色；停用/改密使旧令牌失效。只读账号不可写。最后一个管理员不能被停用或降权。
- 模型统一在“模型配置→编排与三脑模型”维护。统一编排控制面只使用 chat 模型合并已核验结果；结构化、知识、时空分别绑定自己的模型用途。填写地址与凭据后可读取可用模型 ID；“检查连接与模型”核对列表和已选 ID，“验证推理”会执行对话请求，再通过导入/检索与 Agent 核对真实效果。密钥加密存储，客户端不接收内部服务密钥。

### 知识脑大规模日圈检索路径

知识数据推理脑允许“一天一圈、一花一世界”持续增长。主干日圈保存存入/真实观察时间；语义星团身份不含日期，成员引用另行关联日圈。内容时间由元数据、标题、章节和正文自动抽取并形成独立内容时间索引，不确定项保留候选；古籍纪年与复杂叙事并未全部覆盖。导入日期不代表阅读日期、案例日期或生效日期。

“去年发生了什么”应定位内容时间，“最近导入了什么”定位存入时间，“昨天听说去年发生的事”需要真实观察与内容时间共同匹配。当前分别读取内存倒排，按集合大小排序求交；`parallel-intersection` 是策略名，不证明线程并行。存入/观察候选当前可并集作为进入系统一侧；严格“看过/听过”需要真实行为证据，不能仅凭导入日作答。纯时间问题的无主题词枚举仍需专项验证。

检索按边界与原生索引分层。显式时间范围或跨脑强制单元边界仍走平台规范词项/时间索引，得到有界候选；这类请求当前不进入 WeKnora，因为原生投影还没有时间过滤字段。其余问题在 WeKnora 索引 `ready`、修订号等于活动修订且边车健康时，调用边车 `hybrid-search`：边车内 FTS5 与 sqlite-vec 各召回候选后做 RRF 融合，平台把 chunk 的字符区间映射回规范知识单元，并在任何重排之前完成发布、权限与证据过滤。原生索引未就绪、修订号过期、边车不健康或没有合格命中时，回退到平台规范单元上的词项/查询向量路径；响应里的 `retrieval.native_status`、`native_fallback` 会说明原因。

重排默认 `adaptive`：查询词项被头部候选充分覆盖时跳过重排；头部不够明确时才把至多 5 个候选交给本地 bge-reranker。可显式改为 `off` 或 `always`。原生路径中平台进程不再调用 embedding，查询向量由边车用它已注册的本地向量模型统一计算一次；只有回退路径才使用平台查询向量缓存。因此 `retrieval.mode=native` 且 `embedding_skipped=true` 表示“平台侧跳过”，不表示没有向量召回。首次同步会在租户尚未显式配置 RRF 权重时写入向量 0.5 / 关键词 0.5；这套中医古籍语料含 OCR 空格和异体字，纯向量权重会压过精确词项。人工显式配置后平台不再覆盖。

索引按账号知识库增量同步：发布、撤回、重分类与重处理会调度一次 sidecar 同步；同步把规范单元按不超过 18 万字符打包为可重建的 WeKnora manual knowledge，成功拿到 chunk 后用字符区间建立 chunk→unit 映射。控制字符或 NUL 组成的 PDF 提取残渣不进入原生投影；OCR 改变正文后规范文档 ID 和向量内容都会变化，必须按新文档重新同步并重新复核，不能沿用旧发布记录。SQLite Lite 的 Wiki 页面来源引用查询使用文本 LIKE，不依赖 PostgreSQL 的 JSONB 包含运算符；`FindSimilarPages` 的 `similarity()` 仍仅适用于 PostgreSQL，Lite 仅能做精确标题/别名去重。同步失败不清空活动修订，查询继续回退旧路径；不要手工修改 `weknora_index_state` 或映射表来“修好”索引，应重跑同步。

平台在恢复与导入后预热可重建查询投影，快照可先显示。原生检索查询不再扫描平台向量矩阵，热态只读内存映射与边车索引；回退路径的投影保存单元 ID、词项、树/日圈/星团矩阵和时间倒排，冷启动第一次查询可能承担投影构建。时间边界仍逐日展开，宽时间候选未统一分页，纯时间枚举仍需专项验证。不能因原生索引就绪就声称所有检索路径都已达到常数时间。

优先增量维护签名/投影、压缩长时间区间、限制宽候选和隔离导入/查询。WeKnora 原生 FTS5 + sqlite-vec 已替代生产默认的 Python 词项全量路由；首次同步把默认 RRF 调整为向量 0.5 / 关键词 0.5，并在平台侧优先同短语覆盖的规范单元。本机 30 条原文短语/整句基准中，原生路径原文单元 Recall@5/10 与文档 Recall@5/10 均为 1.0，关闭重排 P50 约 0.33–0.42 秒，自适应 P50 约 0.36–0.44 秒，少数头部不明确查询触发本地重排约 1.9 秒。该样本偏原文短语，不能代表开放式语义问题的全量质量；sqlite-vec 仍是单机近邻，不是分布式 ANN，并发与真实规模 P95 仍缺。热态证据检索目标约 1 秒；完整回答还包含独立模型等待与生成。

模型配置页的最近推理统计将模型往返、检索前置、首 token 和流式生成分别记录。启用“流式输出与生成计时”且服务返回 OpenAI 兼容 SSE 时，生成耗时表示首 token 到最后一个 token；非流式服务不会伪造生成耗时，只显示模型往返时间。

知识 Agent 默认超时为 90 秒，显式模型配置以保存值为准。`provider_roundtrip_ms` 包含网络/服务等待/生成；`ttft_ms` 包含网络、排队与预填充；`generation_duration_ms` 是客户端观察的流式接收区间，不能称为纯服务端计算时间。搜索 API 的 `retrieval`、`temporal_plan` 可用于检查候选与模型调用，工作区最近指标不是按 run 的完整并发监控，一部分索引前置开销未纳入该计时。

- 数据中心保留“数据接入”和“导入任务”两个入口；“数据接入”页统一提供知识、时空、结构化三个页签。结构化页签复用完整工作区，承载数据库配置、表发现、数据集注册、智慧树结构查看、浏览、导入和重算；知识/时空页签分别承载文件/文本、摄像头/RTSP。`/datasets` 仍作为结构化工作区深链接，侧栏不再单独显示“结构化数据集”或“结构化数据接入”。来源登记后可直接选用，原来源不需要重新登记。
- “导入任务”合并原任务页与知识媒体资产页：同一资料一行，保留任务记录、尝试次数、进度、重试、原件、解析正文、分类纠偏和发布管理；结构化任务单独页签读取原引擎进度。采样资料没有独立任务时仍可查看。原 `/sources`、`/assets`、`/import`、`/datasources` 路由跳转到对应新入口；`/records` 转到数据集，浏览数据从数据集操作进入。
- 已有知识索引时禁止直接换 embedding 模型或禁用它，避免混写向量空间；在线双索引迁移尚未实现。知识 embedding / rerank 使用 `scripts/setup-knowledge-local.ps1` 安装的本地模型（`BAAI/bge-small-zh-v1.5`、`BAAI/bge-reranker-base`），由平台 `/api/internal/knowledge-model/v1` 提供，边车按这两个准确名称注册；改模型名或维度后必须全量重建索引。
- 本轮仍以 SQLite 为事实源与索引存储：平台元数据、结构化引擎事实库、WeKnora 边车各自使用 SQLite。后续替换数据库不改变规范单元、证据和权限 API，但需要单独迁移与验收，不能把“换库”当成检索提速的默认手段。
- 单机单 worker；后台任务持久化，但没有分布式队列租约。重启恢复排队任务，中断任务显示可重试，采集不会自行重连设备。
- 主导航只显示统一 Agent；三个专脑保留独立路由，通过统一 Agent 的“专脑管理与诊断”或运行步骤进入。统一会话持久化星团、三脑状态、模型部署、generation、trace、证据清单和合一状态；某脑失败不应取消其他脑。
- 知识智慧树使用持久化可读快照和独立拓扑画布，重启后先显示已确认的文档/知识单元或最近完整 generation；Agent 检索工作区后台恢复期间页面明确提示，完成后自动消失。真实节点/边、星团和跨脑关联均由服务端分页，浏览器只接收向量维度与空间标识，不接收原始向量。
- 三脑均保留树形架构原理图与来源关系图；前者展示基座、向上日圈主干、周存、DNA 外链和实际分枝，后者展示文件/数据集/媒体到结果的关系。不能用流程图替代树形，不能把字段声明数当成实际分枝数。
- 知识主领域按文档标题、章节和全文依据自动分类，知识单元继承主树，领域内主题形成概念路径。`document-subject-v2` 是可解释的本地主题规则，支持常见中医/繁体表达；低支持度或多领域接近时保留“待归类”，不把零散的“保养、温度、成本”直接作为整份资料的领域。资产页可查看依据，管理员/操作员可填写原因纠偏或恢复自动分类。纠偏重用解析与向量缓存，旧拓扑快照存档，原件与引用 ID 保留，发布权限独立校验。
- 日志在 .runtime/standalone/*.log。当前是操作审计，未实现不可篡改外部审计存储。

资料发布与处理状态独立显示：资产与任务的 `processing_status` 表示解析/索引完成度，`publication_status` 表示已发布、待复核或已拒绝。旧 `status` 保留供已有流程兼容。批准发布不会清除 OCR 缺页和摘要错误。发布面板按状态提供去复核、发布管理（撤回）、重新复核，并展示真实审核记录。审核记录、资产、任务结果/说明和拓扑在同一事务保存；重启依据真实记录校正旧发布字段，分类未变化也同步。提交失败丢弃未提交工作区，随后由持久化输入恢复。普通低风险资料仍自动发布。

备份应先停止本项目全部服务，再将 `.runtime/standalone`（平台、结构化、Agent、本地模型与密钥）和 `.runtime/weknora`（边车二进制、env、`data/` 中的索引与原件副本）复制到受控备份位置，不能只复制运行中的 SQLite 主文件。WeKnora 索引可由规范单元重建，但边车 env 中的密钥与模型注册信息不应丢失；恢复后若索引修订不一致，重跑同步即可。默认仅监听回环地址；不要直接把 18000/18001/18090 暴露到公网。备份恢复演练、NTFS ACL、密钥外置、监控告警与 HTTPS 发布仍需完成。

## 5. 可复现验证

    python -m pytest -q --tb=short
    python scripts/verify-migration.py
    Push-Location services/agent
    node --test tests/*.test.js
    Pop-Location
    ./scripts/build-weknora.ps1
    bash .runtime/weknora/build-lite.sh
    ./scripts/build-platform.ps1

运行期最少核对：

    Invoke-RestMethod http://127.0.0.1:18090/health
    Invoke-RestMethod http://127.0.0.1:8079/healthz

登录后打开“运行总览”，接口 `/api/v1/platform/overview` 应返回 `weknora.status=ready` 且 `revision` 等于 `active_revision`；不一致或 `unavailable` 时先查边车日志，再重跑索引同步。

根目录测试覆盖三脑、拓扑、治理、证据、平台权限与恢复，并通过真实编译的 WeKnora 进程校验模型协议；协议提供方是隔离测试服务，不能作为真实语义效果证明。迁移回归使用临时目录，不写两边的业务库。结果见 [本轮验证记录](standalone-verification.md)。

## 6. 尚未交付的完整生产能力

这不是要求用户替开发补代码。以下开发项需继续完成：统一 Agent 的长任务计划、人工工具审批与分布式调度；星团拆分/合并操作台；网页采集与完整视频语义管道；CNTS 候选构建网页工作流；可横向扩展的向量 ANN、批处理与模型空间迁移；资料删除和权限撤销传播；组织租户、SSO/MFA、细粒度权限；配置回滚、对象存储、告警、备份恢复自动化及不可篡改审计。

完整 Wiki 与关系图谱已从“待接入”改为产品内实现：发布到知识库的规范单元重处理后，WeKnora 自动生成 Wiki 页面并建立双链图谱；平台只做转发与权限过滤，不另建一套 Wiki 存储。前端入口在“知识智慧树”的“知识 Wiki 与图谱”页签，页面含统计、类型/状态筛选、全文搜索、分页、页面详情和可缩放关系图。生成是长任务，由 Codex 串行合成；可用 `scripts/rebuild_wiki.py` 的 `start/wait/validate/status/rollback` 分批、断点续跑并校验每份资料是否被页面 `source_refs` 覆盖。验收结果见 [独立验证记录](standalone-verification.md)。

源代码独立迁移、WeKnora 真正锁定/编译、真实 embedding/rerank 调用、WeKnora Lite 边车与原生 FTS5 + sqlite-vec + RRF 检索属于既有实现，不再列作“待接入”。性能目标、业务正确性与外部设备效果需要真实输入验收，不能用自动化协议测试替代。

已有成功医书不需重新上传；索引恢复/分类纠偏优先复用持久化原件、知识单元、模型缓存、ID 与发布审核。失败文件单独重试；换模型空间需要重算向量并验收，当前禁止在线直接改绑已有 embedding 模型。

## 7. 用户需配合的信息

第一轮运行最少需要：创建管理员；配置知识 embedding、rerank、chat，以及结构化 chat；提供一份真实业务表、几份真实知识资料、少量标准问题；需要测试时空脑时提供媒体文件和可达 RTSP 或本机摄像头。模型/数据库/RTSP 密码只在页面录入。

后续生产部署还需要：目标主机规格/网络、数据规模与增长量、并发及 P95 目标、角色和共享范围、备份保留/RPO/RTO。是否迁移旧业务数据需另行明确；当前默认是独立新项目空库，未复制旧库。
