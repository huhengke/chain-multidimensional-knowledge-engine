# M4 知识认知脑

本目录已有 `KnowledgeBrain`、五层导入管道（`parse/segment/annotate/gate/publish`）、六件套统一证据契约、专业推理、知识向量留痕与自动分枝/星团，以及 `KnowledgeWorkbenchService` 的导入任务、父子块、索引版本、检索轨迹、评测和跨脑关联候选实现。默认运行时是进程内 `BuiltinKnowledgeRuntime`，用于开发与回归，不得称为 WeKnora 集成。

目标 WeKnora 形态只作为 `KnowledgeBrain` 内部、可替换的 `KnowledgeRuntime`：解析、切分、索引、近邻、混合检索与重排结果先经统一证据契约，再投影到本脑知识智慧树。稳定概念/时间/领域写入中通内链；索引版本、嵌入空间和近邻/重排轨迹写入中通周存；原文块、页码、图表、引用关系和向量候选保留在 DNA 外链。物理索引可重建，但从属于知识树版本与发布生命周期，不能独立回答或授权。

同一嵌入空间内的近邻可自动触发知识留痕、知识星团与新 generation；普通低风险材料自动生长，页面用于查看和事后拆分、合并、暂停、重建或纠偏。跨脑只通过时间、实体、事件或稳定概念锚点形成可审计关联边，目标脑仍独立检索、鉴权并返回证据；多模态时空记忆脑和结构化数据推理脑不因该运行时而修改。

`ManagedWeKnoraRuntime` 已定义受管 stdio JSON-RPC 协议、超时和响应校验；在取得真实提交号并完成许可证审查前，上游锁定记录保持 `PENDING_LOCK`，该运行时明确返回 `RUNTIME_UNAVAILABLE`，绝不降级到 builtin。控制面默认注册本脑；其他未注册脑仍返回显式 `BRAIN_NOT_CONNECTED`。
