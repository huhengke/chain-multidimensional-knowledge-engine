# WeKnora 受管运行时与原生知识工作台设计

**状态：宿主侧已实现并完成本地验收；上游 WeKnora 源码锁定与 Go 派生 bridge 仍被 `PENDING_LOCK` 阻塞**  
**日期：2026-10-12**  
**适用工程：`链式多维时空知识库引擎`**

## 1. 目标与边界

### 当前实现状态（2026-10-12）

已实现：知识脑默认控制面接入；`KnowledgeImportJob` 状态机；受管文本/文件/URL 草稿 API；父/子块、对象哈希、索引版本、同空间知识星团投影、跨脑关联候选、混合检索分数轨迹、评测、例外复核和原生 HTML/CSS/JS 工作台。普通低风险文本自动发布，高风险材料进入 `pending_review`；未配置的 PDF/Office/图片/音视频解析器明确返回不可用。

仍未实现为真实上游能力：WeKnora 上游源码尚未获得可验证锁定，因此未创建/编译 Go 派生 bridge，也没有把 `weknora-embedded-v1` 标为可用。`ManagedWeKnoraRuntime` 现已实现协议边界与结构化错误，调用时返回 `RUNTIME_UNAVAILABLE`，绝不回退到 builtin。URL 抓取器同样尚未配置，获批 URL 只生成草稿并在启动时如实失败。

本增量一次性覆盖规划书中 WeKnora 的六项能力：文档解析、父子分块、混合检索、重排、Wiki/图谱候选、检索评测与可观测性；同时提供三脑项目原生的“知识导入与证据管理”工作台。

WeKnora 只作为受管、可替换的内部知识运行时，且只影响知识认知脑。多模态时空记忆脑与结构化数据推理脑保持既有导入、索引、检索和自动生长实现，本增量不把 WeKnora 接入其运行路径，也不改写其数据结构。

三脑系统继续唯一拥有：原文证据与版本真相源、权限、策略门禁、审计、智慧树拓扑发布、Agent/会话和最终回答。运行时索引、向量和缓存均为可重建产物，不能覆盖这些真相源。普通低风险知识的留痕、聚类、分枝和发布由策略自动执行；页面用于观察、拆分、合并、暂停、重建和纠偏，而不是要求用户逐条审批。

允许参考上游 Vue 工作台的任务流、信息架构和交互节奏；不复制或嵌入其 Vue 源码、组件、样式、静态资源、路由、会话、登录/租户/RBAC 或 Agent 产品层。工作台以本工程的 API、权限和审计实现。

## 2. 当前事实与启动门槛

已存在的 `BuiltinKnowledgeRuntime` 真实实现了确定性文本/表格解析、标题切分、词项候选排序和概念共现图，但其元数据明确为 `runtime=builtin`、`upstream_status=PENDING_LOCK`。`KnowledgeBrain` 已有五层管道、六件套证据契约，以及按 `knowledge.<domain>` 树写向量留痕和自动星团/分枝的实现；该向量目前来自内建运行时，尚未切换至 WeKnora。它也尚未被 `CognitionService` 默认注册，当前控制面没有知识导入 API 或前端页面。

上游固定为 `https://github.com/Tencent/WeKnora`。本次锁定状态定义为 `LOCK_BLOCKED`：本机 Git 的 Schannel 信任链失败，改用 OpenSSL 后仍无法连接 GitHub。不得关闭 TLS 校验、不得填写未经核实的提交号、不得把内建运行时表述为 WeKnora 集成。只有在取得真实 40 位提交、完成许可证和依赖通知审查后，状态才能变为 `LOCKED`，受管源码才能进入 `third_party/weknora/upstream/`。

## 3. 组件与责任

```
原生 KnowledgeWorkbench
  -> 知识导入 API / 任务与证据查询 API
  -> KnowledgeBrain（知识认知脑的唯一运行边界）
       ├─ 知识智慧树：中通内链 / 中通周存 / DNA 外链 / generation
       ├─ 内嵌 ManagedWeknoraRuntime -- stdio JSON-RPC --> weknora-bridge（Go 派生模块）
       ├─ 统一证据契约 / KnowledgeStore / 知识域向量留痕 / 自动星团
       └─ 跨脑关联边（时间 / 实体 / 事件 / 稳定概念）
```

`KnowledgeWorkbench` 仅负责用户交互和呈现，不自行解析、建索引或授予权限。`KnowledgeBrain` 是 WeKnora 的唯一宿主和唯一对外边界：它负责将运行时输出规范化为六件套，按自动策略写入知识域留痕、星团和智慧树。`ManagedWeknoraRuntime` 是 `KnowledgeBrain` 内部的受管组件，只管理已锁定的本地子进程、协议版本、超时、输入输出校验和追踪；`weknora-bridge` 仅暴露白名单能力，不对外启动上游完整 Web 服务，也不读写三脑的原文真相库。

这里的“嵌入智慧树”分为两个层次。**逻辑/执行层**：解析、父子分块、混合检索、重排与评测均在 `KnowledgeBrain` 内部由 WeKnora 派生运行时执行，不能作为独立服务被 Agent 或其他脑直连。**数据/拓扑层**：其索引版本、嵌入空间、父子块引用、近邻轨迹和重排轨迹由 `KnowledgeBrain` 投影到知识树的中通周存，并通过 DNA 外链回到原文证据。为性能而保留的物理向量/关键词索引可以是可重建的受管存储，但它从属于对应知识树的版本和生命周期，不能在智慧树外独立发布、授权或回答。

知识星团的成员、向量空间、阈值和生长代仅属于知识脑的 `knowledge.<domain>` 树。跨脑联想由独立的 `CrossBrainLink` 表达：它只使用已规范化的时间、实体、事件、稳定概念或业务维度作为关联锚点，记录关联依据、算法/规则版本、自动状态和人工覆盖。不同脑的原始向量坐标不得因名称相同而直接比较或混合；沿关联边访问目标脑时，目标脑仍按自身权限、版本和查询契约独立检索。

`BuiltinKnowledgeRuntime` 保留为名称明确的开发/回归档位。选择 `weknora-embedded-v1` 后，桥接进程不可用、协议不兼容、模型不可用或索引损坏必须返回结构化错误；禁止悄悄回退至 `builtin`。

## 4. 受管源码与桥接协议

### 4.1 源码布局

锁定后目录固定为：

```
third_party/weknora/
  UPSTREAM_COMMIT
  THIRD_PARTY_NOTICES.md
  upstream/                 # 只读、检出到 locked_commit
  derived/                  # 由补丁从 upstream 派生的受管工作树
  patches/0001-*.patch       # 每个本地修改可审计、可重放
  bridge/                    # 位于派生 Go 模块内的 cmd/cognition-runtime
```

`UPSTREAM_COMMIT` 必须记录仓库 URL、完整提交哈希、获取时间、许可证 SHA-256 和派生补丁清单哈希。`THIRD_PARTY_NOTICES.md` 必须列出 MIT 许可证文本、版权声明和所有被编译/分发的第三方通知。升级只能新建锁定记录和补丁，不原地改写既有锁定。

### 4.2 JSON-RPC 边界

Python 与 Go 通过受管子进程的标准输入/输出交换单行 JSON；每条请求和响应均带 `protocol_version="weknora-bridge/v1"`、`request_id`、`trace_id`、`runtime_version` 与 `operation`。允许操作只有：

| operation | 输入 | 输出 | 失败代码 |
| --- | --- | --- | --- |
| `health` | 无敏感正文 | 运行时、提交、能力清单 | `RUNTIME_UNAVAILABLE` |
| `parse` | 受管文件标识、MIME、解析配置 | 块、页码、标题路径、模态声明 | `PARSE_FAILED` |
| `segment` | 规范块、父子分块配置 | 父块/子块及稳定定位 | `SEGMENT_FAILED` |
| `index` | 已校验知识单元与索引命名空间 | 可重建索引版本、嵌入空间标识和单元向量引用 | `INDEX_FAILED` |
| `neighbors` | 单元 ID、索引/嵌入空间、相似度阈值和候选数 | 同空间近邻单元、距离/相似度、索引版本 | `NEIGHBOR_FAILED` |
| `retrieve` | 查询、过滤条件、候选数 | 词法/语义/RRF 候选与分数构成 | `RETRIEVE_FAILED` |
| `rerank` | 查询和候选 ID/文本摘要 | 重新排序及模型版本 | `RERANK_FAILED` |
| `graph` | 已发布单元 | Wiki/实体/关系候选与证据 | `GRAPH_FAILED` |
| `evaluate` | 固定评测集和索引版本 | Recall@K、MRR、失败样例 | `EVALUATION_FAILED` |

桥接层只能接受受管存储根目录内的相对对象标识，拒绝绝对路径、父目录跳转和未授权网络 URL。每次调用的超时、子进程退出码、索引版本、模型版本和脱敏错误摘要写入治理审计。

## 5. 契约与存储演进

现有六件套保持向后兼容，新增字段均为可选字段并同步更新 Pydantic 模型、JSON Schema 和字段一致性测试：

| 模型 | 新增字段 | 语义 |
| --- | --- | --- |
| `CanonicalDocument` | `content_hash`, `source_object_ref`, `ingest_job_id`, `document_status` | 固定原文身份、任务与发布状态 |
| `KnowledgeUnit` | `parent_unit_id`, `chunk_level`, `chunk_order`, `index_version` | 父子块、稳定排序与索引归属 |
| `KnowledgeEvidence` | `source_object_ref`, `runtime_version`, `index_version` | 原文展开与运行时可追溯 |
| `KnowledgeRoute` | `retrieval_version`, `lexical_score`, `semantic_score`, `fusion_score`, `rerank_score` | 候选与选中依据 |
| `KnowledgeReasoningResult` | `runtime_version`, `review_status` | 推理来源和是否已审核 |

`KnowledgeUnit` 额外增加可选 `embedding_space_id`；`KnowledgeRoute` 额外增加可选 `cluster_id`、`cluster_generation` 和 `cross_brain_link_ids`。这些字段只说明检索与联想路径，不把星团或关联边伪装成结论证据。

新增独立的 `KnowledgeImportJob`、`KnowledgeIndexVersion`、`KnowledgeClusterProjection`、`CrossBrainLink`、`GraphProposal`、`EvaluationRun` Pydantic 模型与 `knowledge-workbench.schema.json`。这些状态不挤入六件套，避免把任务控制信息伪装成证据。`KnowledgeClusterProjection` 至少记录 `tree_id`、`cluster_id`、`embedding_space_id`、成员单元/证据引用、次数、中心/离散度、阈值事件、算法/规则版本、状态和 generation；`CrossBrainLink` 至少记录源/目标脑、源/目标引用、关联锚点、依据、自动或人工覆盖状态、版本和可撤销原因。

原文保存为内容哈希寻址的对象；文档版本不可变。删除、回滚或权限收回会使对应索引版本失效，并从检索候选中排除，保留审计记录。索引重建只生成新 `index_version`，经校验后切换活动版本；旧版本可回滚，不改写证据 ID。

## 6. 导入任务、自动生长与门禁

导入状态机固定为：`draft -> validating -> queued -> parsing -> segmenting -> indexing -> clustering -> proposing_graph -> ready_to_publish -> published | failed | cancelled`。只有策略命中的任务会从任一步转入 `pending_review`；审核通过后回到最近安全检查点并继续自动执行。

普通低风险资料在解析、切分、索引、星团和图谱候选均成功后自动进入 `published`，不要求人工逐条确认。`failed` 保留步骤、错误代码、可安全显示的原因和重试次数；`cancelled` 不发布任何单元；`pending_review` 的内容可以预览但不能成为知识结论；`published` 才对外提供正式检索和跨脑链式联想。高风险领域、冲突裁决、不可逆发布、权限越界和人工标记的材料强制进入 `pending_review`。

`clustering` 是自动后台步骤：桥接层以 `embedding_space_id` 和 `index_version` 返回同空间近邻，知识脑自动写入留痕、合并或创建知识星团，并由现有生长引擎创建新的不可变知识树 generation。聚类至少同时受最小独立来源数、近重复去重、紧密度、离散度、概念/上下文一致性和策略阈值约束；同一篇长文的相似分块不得虚增出现次数。冲突材料可归入同一主题星团，但必须保留冲突状态，不得自动合并为同一结论。

星团自动生长不等于修改另两个脑。已发布知识星团可自动形成 `CrossBrainLink` 候选；只有公共锚点匹配且策略允许时才激活关联边。关联边不会写入多模态或结构化脑的内部索引，也不会跳过其权限检查；在访问时仅将锚点作为目标脑的查询约束。

导入输入包括本地文件、粘贴文本和已审批的受管 URL 抓取。文件类型覆盖 PDF、Word、PowerPoint、Excel、CSV、TXT、Markdown、HTML、图片、音频和视频；未配置 OCR、ASR、视觉或模型时返回对应模态的 `unavailable`，不生成伪造文本。URL 抓取使用允许域名、私有地址拒绝、重定向上限、内容大小上限和下载审计；未获批准的 URL 只保留草稿，不能启动抓取。

## 7. 原生知识工作台

工作台由 FastAPI 静态资源提供的原生 HTML/CSS/JavaScript 实现，避免在当前纯 Python 工程引入独立前端构建链。它是可用的操作界面，不是产品介绍页。

1. **知识库与来源列表**：显示来源、版本、权限范围、导入任务、发布状态和索引版本；支持筛选与状态查询。
2. **导入向导**：选择文件/文本/获批 URL，显示类型与大小预检，确认来源和权限范围，提交草稿后创建任务。
3. **任务详情**：按状态机显示解析、切分、索引、星团、图谱候选的进度；失败时显示可操作的重试或取消，不隐藏错误。
4. **证据预览**：展示原文定位、父子块层级、解析块、版本、引用链和权限；禁止直接编辑已发布原文。
5. **星团与链式联想**：展示知识星团的成员、独立来源数、阈值事件、紧密度、离散度、索引/嵌入空间、树 generation，以及到其他脑的时间/实体/事件/稳定概念关联依据；可按权限拆分、合并、暂停、重建或覆盖关联，但不要求对普通自动生长逐条确认。
6. **审核与例外处理**：只展示门禁触发理由、冲突并列材料、Wiki/图谱候选；只有有审核权限的主体可批准、拒绝、回滚或处理策略例外。
7. **检索与评测**：显示词法/语义/RRF/重排的候选轨迹、所用索引与模型版本、星团命中和完整检索回退；评测运行展示指标和失败样例，不能用指标代替证据。

界面不提供 WeKnora 的通用聊天、登录、租户管理或独立 Agent。认证主体由三脑宿主注入；工作台只消费其 `Principal` 和允许的操作范围。

## 8. HTTP API

API 前缀为 `/api/v1/knowledge`。每个写操作要求经过宿主认证后的 `Principal`；未通过时返回 `AUTHZ_DENIED`，不创建草稿或任务。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `GET` | `/runtime` | 运行时身份、锁定状态、能力与健康信息 |
| `POST` | `/imports/files` | 上传受管文件并创建 `draft` 导入任务 |
| `POST` | `/imports/text` | 保存粘贴文本并创建 `draft` 导入任务 |
| `POST` | `/imports/urls` | 仅对获批 URL 创建抓取草稿 |
| `POST` | `/imports/{job_id}/start` | 校验后启动状态机 |
| `GET` | `/imports/{job_id}` | 读取任务、步骤和安全错误信息 |
| `POST` | `/imports/{job_id}/retry` | 从最近安全检查点重试失败任务 |
| `POST` | `/imports/{job_id}/cancel` | 取消未发布任务 |
| `GET` | `/documents` | 查询文档与发布状态 |
| `GET` | `/documents/{document_id}` | 文档、版本、证据和父子块摘要 |
| `POST` | `/documents/{document_id}/review` | 仅对进入 `pending_review` 的例外执行 `approve`、`reject` 或 `rollback` |
| `GET` | `/clusters` | 查询知识域自动星团、状态、generation 与成员摘要 |
| `GET` | `/clusters/{cluster_id}` | 展开成员、阈值事件、版本、证据和链式关联 |
| `POST` | `/clusters/{cluster_id}/corrections` | 记录 `split`、`merge`、`pause`、`rebuild` 或关联覆盖；生成新 generation，不覆写历史 |
| `POST` | `/search` | 返回候选、重排、证据与版本轨迹 |
| `POST` | `/evaluations` | 针对固定索引版本启动评测 |
| `GET` | `/evaluations/{run_id}` | 读取不可变评测结果 |

跨脑关联查询位于宿主拓扑 API：`GET /api/v1/topology/cross-brain-links`。知识工作台只读其允许范围内的关联。所有响应包含 `request_id`、`trace_id`、`runtime_profile` 和相关版本；API 不接受客户端提供的已发布状态、证据 ID、索引版本或跨脑关联状态作为可信事实。

## 9. 控制面接入

`CognitionService` 默认构造并注册 `KnowledgeBrain`，健康检查把知识脑报告为 `connected`。控制面只调用 `KnowledgeBrain.query()` 并继续通过现有证据聚合、冲突消解、三阶段输出和审计；它不直接调用 Go 桥接进程或运行时存储。多模态时空记忆脑与结构化数据推理脑的注册、实现和运行配置不因本增量改变。

查询允许先命中自动星团作为“快速记得”线索：知识星团命中后，系统以成员引用过滤时间范围和权限，再使用 WeKnora 完整检索核对。命中率不足、星团暂停、索引空间不一致、版本不匹配或跨脑边不允许时，必须回退到知识脑完整检索。沿 `CrossBrainLink` 联想时，控制面把公共锚点转成目标脑的受控查询参数；目标脑必须自行返回结果和证据，关联边不能替代其检索或事实判断。

知识运行时配置以 `RuntimeProfile` 表达：`builtin-dev-v1` 与 `weknora-embedded-v1` 为独立档位。配置档冻结后，单个请求不得切换运行时。`weknora-embedded-v1` 的健康失败返回 `ENGINE_UNAVAILABLE` 或桥接错误，绝不回退。

## 10. 验收与测试

实现必须新增并通过以下测试：

1. 锁定记录、许可证、通知和补丁清单完整；不存在伪造提交号或未审查源码发布。
2. 每个桥接操作都有协议成功、超时、无效 JSON、版本不兼容和子进程退出测试。
3. PDF/Office/HTML/表格/图片/音视频分别验证解析结果或显式 `unavailable`，并能展开至受管原文定位。
4. 父子块稳定 ID、块顺序、索引重建和回滚不改变已发布证据 ID。
5. 混合检索返回词法、语义、融合与重排轨迹；权限收回/文档回滚后不再召回。
6. 普通低风险资料无需人工确认即可完成自动星团、拓扑 generation 与发布；高风险/冲突/不可逆策略例外进入 `pending_review`，冲突材料保持并列。
7. 固定评测集对指定索引版本产生可复现 Recall@K、MRR 和失败样例。
8. 星团测试覆盖嵌入空间隔离、近重复去重、最小独立来源、自动生长、新 generation、暂停/拆分/合并/重建、撤销后的失效和完整检索回退。
9. 跨脑测试覆盖时间/实体/事件/稳定概念关联边、关联依据/版本/人工覆盖、目标脑独立权限与证据返回，以及禁止跨脑混合原始向量空间。
10. 工作台 API 覆盖导入、状态转换、失败重试、例外审核、自动发布、星团纠偏、撤销和越权拒绝；浏览器验收覆盖桌面与移动视口、文字对比度、无重叠和真实交互。
11. `python -m pytest` 全量通过；Go 派生模块执行其受限包的 `go test`；前端无独立构建链，因此以浏览器 API 契约和交互验收代替虚假的前端编译通过声明。

## 11. 实施顺序与完成条件

实现按不可破坏的依赖顺序执行：先锁定/合规和桥接契约，再扩展模型与存储状态，再实现 Go 派生运行时与 Python 适配器，随后接入控制面，最后实现 API、原生工作台、评测和端到端验收。任何前一层失败都显式停止后续发布，不启用替代运行时。

本增量完成的判据是：真实锁定的 WeKnora 派生运行时只处理知识认知脑的受管资料；普通低风险知识可自动完成导入、星团、知识树生长和发布，用户能在原生工作台查看与事后纠偏；知识星团可经可审计关联边触发其他脑的独立检索；已发布资料能通过三脑控制面返回带版本和定位的证据；六项能力均有可复现测试；不存在第二套会话、权限、审计或事实库。

## 12. 导入与访问流程对照及效率评估

本节以 WeKnora 当前公开文档描述的能力作为参考基线，不复制其实现。主对照只展示“上游参考”和“本工程目标融合路径”；`BuiltinKnowledgeRuntime` 仅是尚未锁定上游源码前的开发/回归基线，不是目标架构的一栏。任何效率结论必须注明比较对象、资料集、硬件、模型、索引/星团 generation 和运行时提交。

### 12.1 导入流程

| 阶段 | WeKnora 上游参考流程 | 本工程目标融合流程：知识脑 + 智慧树 |
| --- | --- | --- |
| 接入 | 文件/网页/手写知识创建记录，保存原文件并异步入队 | 原生工作台预检后保存受管对象，创建不可伪造的 `KnowledgeImportJob` |
| 排队与状态 | `pending`、处理中、完成/失败/取消，前端显示进度和失败原因 | `draft -> validating -> queued -> parsing -> segmenting -> indexing -> clustering -> proposing_graph -> ready_to_publish -> published`；每步有开始/结束/错误码，普通低风险任务自动继续 |
| 解析 | 依据文件类型选择 docreader/解析引擎；OCR、ASR、表格和网页按能力配置 | `weknora-bridge.parse()` 调用锁定派生运行时；模型/解析器缺失返回 `unavailable`，不会伪造内容 |
| 切分与索引 | 分块、向量化、索引，后处理摘要/问题/图谱/Wiki/多模态 | 派生运行时产生父/子块、词法/语义索引、索引版本和嵌入空间标识；统一证据契约后写入知识树 |
| 自动星团与生长 | 上游索引为知识库检索服务 | 同空间近邻自动进入知识向量留痕；满足来源去重、紧密度、离散度与策略阈值后自动形成知识星团、周存投影和新智慧树 generation |
| 跨脑链式联想 | 上游不拥有本工程的三脑拓扑 | 已发布知识星团自动按时间、实体、事件、稳定概念形成可审计关联边；目标脑保持原实现，只接收受控查询约束 |
| 门禁、删除与重建 | 索引完成后可检索；上游按自身知识库权限控制 | 低风险任务自动发布；高风险、冲突、不可逆和权限例外才进入 `pending_review`。撤销/权限收回使索引、星团成员和关联边同步失效；重建产生新索引和树 generation，可回滚 |

### 12.2 访问流程

| 阶段 | WeKnora 上游参考流程 | 本工程目标融合流程：知识脑 + 智慧树 |
| --- | --- | --- |
| 请求入口 | 知识库检索/问答 API 选择知识库和模式 | 统一认知请求冻结 profile，路由到已注册 `KnowledgeBrain`；工作台检索仅走同一受控 API |
| 星团快速线索 | 上游不提供本工程的智慧树星团语义 | 先命中知识星团并按成员引用与时间/权限求交；只作为“快速记得”线索，不能替代完整检索 |
| 召回与重排 | 向量与关键词召回，经 RRF 融合；可配置 rerank 和父/子上下文 | 桥接层返回 lexical/semantic/fusion/rerank 分数、索引版本和候选 ID；仅在已授权、已发布候选上展开上下文，禁止绕过受管索引直读上游存储 |
| 跨脑联想 | 上游不拥有本工程的三脑拓扑 | 命中 `CrossBrainLink` 时，把公共锚点编译为目标脑查询约束；目标脑自行检索、鉴权并返回独立证据，不混合原始向量空间 |
| 证据与统一回答 | 知识库访问控制和引用返回；上游产品可生成回答 | 运行时候选先经过版本、权限、本体与治理核验，再转为六件套和 `KnowledgeRoute`；控制面继续唯一生成三脑回答、引用和审计 |

### 12.3 必采指标与可比口径

导入侧记录：文件类型、字节数、页数/时长、任务排队时间、解析/切分/索引/星团/图谱各阶段 P50/P95、吞吐量、块数、父子块比例、重试数、失败代码、策略例外等待时间、自动发布成功率、星团生成/重建耗时、近重复剔除数、激活星团数、generation 次数和每个索引版本的资源占用。

访问侧记录：端到端 P50/P95、控制面路由耗时、星团命中率/耗时/回退率、桥接往返耗时、词法召回耗时、语义召回耗时、融合耗时、重排耗时、权限/版本过滤数量、父块展开耗时、跨脑关联命中率/目标脑验证耗时、返回证据数、空结果/重排拒绝率、Recall@K、MRR、NDCG、引用可展开成功率。

每次评估运行固定以下输入：语料清单与内容哈希、查询集与相关证据标注、运行时提交、桥接协议版本、解析/切分/检索/重排配置、模型版本、索引版本、星团规则/算法版本与 generation、跨脑关联规则版本、设备与并发数。评测结果只与完全相同口径的运行作比较；不同运行时、模型、索引、星团 generation 或关联规则不得合并成单一“知识脑效率”数字。
