# 统一 Agent 耗时与“只答一半”整改（2026-10-22）

## 一、问题与取证结论

现场问题：问“我朋友久咳不止，怕冷，是什么原因引起的，应该怎么办，有相关方剂可以参考吗？”，45.6 秒返回一段被改写的、拒绝给方剂与剂量的回答。

运行记录（`.runtime/maintenance/agent-run-45s.txt`）分解：

| 阶段 | 耗时 | 占比 |
| --- | --- | --- |
| 输入编译 | 0 ms | — |
| 星团快速检索 | 938 ms | 2% |
| 知识脑检索 | 2,984 ms | 6.5% |
| 知识脑模型调用 | 27,672 ms（首字 20,094 ms） | 61% |
| 编排模型合并 | 13,953 ms（首字 8,125 ms） | 31% |

结论：时间几乎全部在两次串联的模型往返上，知识检索只占 6.5%；“只答一半”不是检索失败，而是提示词与编排策略主动砍掉了答案。

## 二、本轮改动

1. 单脑逐字透传，取消编排改写（`platform_server/unified_agent.py` `_merge`）
   只有一个脑产出可用结果时没有可综合的内容。此前仍会调用编排模型，多花约 14 秒，并把该脑结论改写成“合并结果说明/不冒充其他脑”一类元话术，同时把答案截断到 5000 字。现在直接返回该脑原文，`merge_run.status = "template"`，运行步骤写明“未做二次改写”。

2. 生成期把已产出文本回传前端（`_invoke_model` → `execute` → 前端轮询）
   `_invoke_model` 本来就在解析 SSE 增量，现在把累计文本交给 `emit`。只命中一个脑时（该情况下的最终答案就是这段文本），每 0.8 秒把 `live_answer` 写入运行记录；页面沿用已有的 running 轮询（间隔由 1.2 秒收紧到 0.7 秒）渲染“正在生成”气泡。写入前会重读运行记录状态，绝不覆盖用户暂停后写入的终态。多脑运行仍只在合并完成后一次性给出。

3. 回答改为两栏，通识栏允许到剂量与用法（`platform_server/agent_runs.py`）
   `SYSTEM_PROMPT` 固定要求：第一栏「资料证据」只依据授权证据并挂 evidence_id、文件名与页码；第二栏「通识补充（非本项目证据）」按通用大模型方式正面回答原因、治法、方剂、参考剂量与用法、调理与就医建议，不挂 evidence_id，不得伪称出自本项目资料。涉及医疗时结尾只保留一行就医提示。
   同时把知识脑“零证据即返回‘没有找到可引用证据’”的短路改为：仍调用模型，只是把证据上下文换成 `status: no_evidence`，并把该次结果标记 `answer_basis = "general"`；记忆脑保持原来的 `no_hit`。`medical_reference_note` 不再追加“医疗资料使用边界”段落，只展开可追溯出处清单，且没有出处时不再输出占位文字。

4. 检索期的语料自身词形扩展（`brains/knowledge/src/brain.py` `_corpus_expansion`）
   口语词与书面语料不匹配（“久咳不止”对“久嗽”）时，原查询在 native FTS 上召回不足。机制不建静态别名表：先确认问题的双字词在语料词表里全都查不到，再用问题的单字取种子单元，统计这些种子单元里被两个以上单元共同支持的双字词，取前 6 个拼进 native 查询文本。命中语料原有词形时函数直接返回空，主路径与改动前逐字一致；扩展结果记入 `last_retrieval_stats.corpus_expansion`。

## 三、验证

- 回归：`brains/knowledge/tests`、`test_unified_agent.py`、`test_agent_runs.py`、`test_platform.py`、`test_publication_states.py` 全绿；新增单脑透传、零证据通识作答、语料扩展三个用例。
- 新增 `test_truncated_sse_stream_retries_once_without_streaming`：mock 只在流式帧里给 `delta.role`，断言请求序列是 `[True, None]`（先流式、再非流式重试一次）、该脑 `status=="succeeded"`、`provider_attempts==2`、`streaming is False`，且答案仍含“通识补充”并逐字写入最终消息。
- 契约变化写进了 `platform_server/tests/test_agent_runs.py` 的医疗用例：断言两栏提示词与出处清单，并断言旧的“不得直接开方、推荐剂量”禁令不再出现在提示词里。
- 前端 `tsc --noEmit` 通过，已产出新包并替换 `frontend/dist`（`index-DopO0JNI.js`）。注意 `vite build` 直接写 `frontend/dist` 会在 rendering chunks 阶段无输出退出，构建到临时目录再拷回 `dist` 可用；本次进一步确认 `npm run build` / `npx vite build` 即使输出到临时目录也会以 exit 127 退出（无错误输出），改用 `node node_modules/vite/bin/vite.js build --outDir <临时目录>` 可正常构建（约 11 秒）。

## 四、重启后的实测（2026-10-22 11:05）

同一个医书问题、同一套 45 篇已发布语料，进程内走 `UnifiedAgentRuns.execute`（与 `POST /api/v1/platform/unified/sessions/{id}/messages` 同一代码路径），运行记录 `conversation-dfed904180744a0aaa86ef7c161272f6`：

| 指标 | 实测 |
| --- | --- |
| 总耗时 | 112.91 s |
| 路由 | 只命中知识脑（`route_plan.selected=["knowledge"]`，命中能力词“应该、怎么办”） |
| 知识脑检索 | 8,297 ms |
| 知识脑模型往返 | 104,437 ms，首字 48,875 ms |
| `provider_attempts` | 1（SSE 一次成功，未触发非流式重试） |
| 合并 | `merge_run.status="template"`，0 ms，`model=null`，逐字透传 |
| 最终答案 | 2,815 字，两栏表格 + 8 条可追溯出处 |
| `live_answer` | 57.48 s 出现第 1 字，此后每 0.8–1.2 s 增长，112.73 s 累计 2,174 字 |

与整改前 45.6 s 的单段拒答对比：耗时长在生成量上（两栏约 2,200–2,700 字 ≈ 2,200 补全 token），不是新增的串联调用；生成从 57 s 起就能在前端逐步看到，不必等满 113 s。

## 五、为跑通实测额外发现的两个缺陷

1. `platform_server/app.py` `save_model` 对 `purpose=="chat"` 强制 `data["timeout"]=90`
   模型页上填的超时被丢弃，`contracts.py` 里 `timeout: Field(default=90, ge=5, le=300)` 的边界形同虚设。已删除该覆盖；`unified_agent._invoke_model` 与 `agent_runs` 改为按部署配置取 `min(max(timeout,5),300)`。实测这条双栏答案需要 104 s，旧的硬编码 90 s 必然把答案掐在半路（第二次实测就是在 129.86 s 处 `MODEL_TIMEOUT` 失败；同一提示流式实测 153.8 s、非流式 93.5 s）。
2. SSE 空流导致整次回答 502
   网关会返回 200 的事件流但只有一帧 `delta.role` 就结束（探针实测 0 字出现在 6.5 / 15.5 / 25.9 s 三次，同批有一次 153.8 s 正常吐完），`reasoning_effort:"low"` 只会更快空流，不是推理空闲。`_invoke_model` 现在拆出 `_provider_exchange`，流式拿不到内容时自动重试一次非流式，遥测记 `provider_attempts`。同样的重试在单脑会话路径 `platform_server/agent_runs.py` 仍未实现，该路径空流会以 `MODEL_RESPONSE_INVALID` 失败——已确认但本轮未改。
3. 失败分支把模型耗时记到检索头上
   `_semantic_brain` 的四个失败分支原本共用同一组表达式，模型 129 s 超时会写出 `retrieval_duration_ms≈129,000`（真实检索 5.7 s）。改为 `failure_timings()`，检索与提供方往返分别归属。

另外发现启动方式的一个坑：`platform_server/launcher.py` 每 0.2 s 轮询 `healthz`、单次超时 1 s，平台仍在启动时这些探针会被主动断开并留在监听队列里；Windows 完成端口模型下首个 `accept()` 随即以 `OSError: [WinError 64]` 失败，asyncio 判定致命并关闭监听套接字——进程存活但端口再无人应答，启动器报 `platform did not become ready`。手工以 20 s 超时单探针启动同一命令，平台 4 秒即就绪，确认不是本机负载或本轮源码改动导致启动变慢。绕过脚本 `.runtime/maintenance/start-platform-patient.py` 已可用；启动器探针本身尚未修改。

## 六、知识补强“大文档必失败”的取证结论

先前假设是文档体积超过 ~104k 字就会即时 `MODEL_CONNECTION_FAILED`。按 61 篇文档的字量与作业里持久化的 `graph_error` 组合后，假设不成立：

| 字量 | 文档 | 补强结果 |
| --- | --- | --- |
| 265,557 | 一见能医 | succeeded |
| 215,202 | 竹亭医案（上册） | succeeded |
| 129,070 | 王乐亭指要 | succeeded |
| 104,436 | 中医基础理论笔记图解 | partial，`MODEL_CONNECTION_FAILED` |
| 56,322 | 医方絜度 | partial，连接超时或未响应 |
| 50,323 | 疡科指南医案 | pending_review，连接超时或未响应 |
| 394,311 | 玄隐文本底稿 | partial，连接超时或未响应 |

失败横跨 50k–394k 字，成功也横跨 50k–265k 字，与体积无关。决定性实验：复刻补强缓存摘要，找出每篇文档**第一个从未被缓存过的批次**再真实调用一次。

| 文档 | 已缓存批次 | 首个未缓存批次 | 本次结果 |
| --- | --- | --- | --- |
| 太平经.txt（349,843 字，220 批） | 0 | 1 | 40.6 s 成功，12 个概念 |
| 玄隐文本底稿.txt（394,311 字，249 批） | 0 | 1 | 44.2 s 成功，12 个概念 |
| 中医基础理论笔记图解（104,436 字，66 批） | 0–48 | 49 | 7.3 s 成功，11 个概念 |

当时失败的那一批现在全部成功，且 7–45 s 远低于桥内 `http.Client{Timeout:90s}`。结论是：补强失败是网关侧瞬态（429 与传输层拒绝）叠加 `concept_graph` 逐批串行、无重试——`EmbeddedRuntime.concept_graph` 的注释写着“Stop on provider failure”，任一批次一失败整篇就中止。同时 `cognition-runtime` 把任何传输错误一律映射成 `MODEL_CONNECTION_FAILED`，而 `ingestion.py` 又把它和 `RUNTIME_TIMEOUT` 归并为“对话模型连接超时或未响应”，真实原因在两层归并中被抹掉。

一本 35 万字的书光概念提取就是 220 次模型往返，按 40 s/批约需 2.5 小时，再加知识摘要的分段与归并轮次；25 篇待补强在网关单并发下不可能在一个晚上跑完。已完成的批次留在 `knowledge_cache` 里（上表 49/66 就是证据），补强按篇可续跑，缺的是逐批退避重试与超时/错误码归因，不是重灌数据。

## 七、查询档位（2026-10-22 12:30）

按用户拍板新增每次查询可选的 `answer_mode`，默认 `standard`，标准路径逐字不变：

| 档位 | 检索 | 生成提示 | 输出预算 | 合并 | 实测总耗时 |
| --- | --- | --- | --- | --- | --- |
| `standard` 深度分析 | 同一套 `ingest.search` | 现有两栏 `SYSTEM_PROMPT` | 模型自身超时，不设补全上限 | 单脑透传；多脑编排改写 | 112.91 s |
| `fast` 快速回答 | 同一套检索，取前 8 条、每条 700 字 | 探针版紧凑提示：3–5 条要点 + `[n]` 引用 + 一段“通识补充” | `temperature=0`、`max_tokens=2000` | 单脑透传；多脑直接并列，不再调编排模型 | 23.5 s（检索 3.2 + 生成 20.3） |

实现位置：

1. `platform_server/contracts.py`：`UnifiedMessageInput.answer_mode: Literal["standard","fast"]="standard"`，并在 `_semantic_brain` 的检索请求中排除，检索输入与标准档完全一致。
2. `platform_server/agent_runs.py`：`fast_system_prompt`（医学问题才附加方剂/剂量许可）、`fast_evidence_prompt`（`[n] 文件名 | locator | 正文` 紧凑渲染）、`fast_reference_note`（出处清单由代码追加，就医提示固定在最后一行）；`evidence_context` 增加 `text_limit`，标准档默认 1400 不变。
3. `platform_server/unified_agent.py`：`_invoke_model/_provider_exchange` 增加档位、`temperature`、`max_tokens`；`_merge` 在 fast 档不再调用编排模型；运行记录与助手消息写入 `answer_mode`，审计事件同步记录。
4. 前端 `UnifiedAgent.tsx`：查询框上方“查询档位：快速回答 / 深度分析”，请求带 `answer_mode`；助手气泡显示“快速”标签，运行面板显示档位。新包 `frontend/dist/assets/index-B6BFeh53.js`，旧包保留为 `frontend/dist-before-answer-mode-20261022`。

实测（2026-10-22 12:26，同一医书问题、同一 45 篇语料、Codex `gpt-5.6-sol`）：检索 3.2 s，20 条 claim 选 8 条证据，提示词 3,994 字；首字 9.78 s，生成 20.34 s，补全 1,072 token；正文 660 字，加出处清单共 1,306 字，`provider_attempts=1`。原文以 4 条证据要点 + 通识补充给出久咳原因、止嗽散组成与现代检查/处理方向，8 条出处均带文件名、页码、锚点和 evidence_id，末行为就医提示。该次为进程内调用同一 `_invoke_model` fast 路径（平台账号无默认密码，未代登录写运行记录），结果存于 `.runtime/maintenance/answer-mode-fast-live.json`。

新增测试：`test_fast_prompt_and_reference_note_stay_compact_and_put_disclaimer_last`（700 字截断、紧凑提示、出处清单、提示只出现一次且在最后）、`test_fast_answer_mode_uses_compact_request_and_keeps_code_owned_sources`（`temperature=0`、`max_tokens=2000`、无“本次授权证据”JSON、提示词不是两栏、来源清单与末尾提示齐全）。

