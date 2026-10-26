# Wiki 生成与 partial 补强整改记录（2026-10-21）

本轮只处理边车 Wiki 链路与知识脑 partial 资产的补强，未修改产品规划书、结构化脑与时空脑的配置和业务数据。整改清单的 #10～#12 已改代码并有自动化回归；#13 的补强在本记录时点仍在运行，不能记为已完成。

## 一、已修复：批次全部失败却报 success

现象：引用（citation）分批调用全部被模型拒绝时，批次仍把该文档记为已完成，页面上看起来"生成了 Wiki"，实际没有任何引用关系。

改动：

- [wiki_ingest_cite.go:299](../third_party/weknora/upstream/internal/application/service/wiki_ingest_cite.go#L299) 与同文件 309 行开始累计 `failedBatches`，`classifyChunkCitations` 额外返回 `batchCount, failedBatches`。
- [wiki_ingest_batch.go:1539](../third_party/weknora/upstream/internal/application/service/wiki_ingest_batch.go#L1539)：`batchCount>0 && failedBatches>0 && 引用与新 slug 均为空` 时构造 `citation pass lost every batch: N/M failed, cited_slugs=0`，并对该 span 记 `CITATION_ALL_FAILED`。
- [wiki_ingest_batch.go:1559](../third_party/weknora/upstream/internal/application/service/wiki_ingest_batch.go#L1559)：该错误原样返回给批次，走既有的重排队路径，不再落成功状态。
- 引用分批预算从 12,000 提到 24,000 rune（[wiki_ingest_cite.go:29](../third_party/weknora/upstream/internal/application/service/wiki_ingest_cite.go#L29)），批内并发 4（同文件 33 行），减少单文档的调用次数——在下面的单并发槽位网关下，调用次数直接决定耗时。

## 二、已修复：Windows 断连不算可重试错误

`isTransientLLMError` 原来只认 `connection reset by peer` / `EOF`。Windows 丢弃失效 keep-alive 时给出的是 `wsarecv: An existing connection was forcibly closed by the remote host.`，两者都不匹配，于是被当成永久错误，批次直接放弃而不是退避重试。

- 判定串补充见 [wiki_ingest.go:3048-3056](../third_party/weknora/upstream/internal/application/service/wiki_ingest.go#L3048)：`forcibly closed`、`wsarecv`、`use of closed network connection` 等。
- 超时值本轮没有改：`WEKNORA_LLM_CHAT_TIMEOUT_SECONDS` 仍是 300 秒兜底，且只在调用方没有 deadline 时生效（`transport.go:20`、`openaicompletions/client.go:72`）。Wiki 批次自带 60 分钟任务超时，收紧这个值会砍掉合法的 32k token 页面重写；`ResponseHeaderTimeout` 对非流式调用同样不安全，所以只修判定、不动预算。

## 三、已修复：同步把 busy 当成功

本机运行目录中的 `finish_ocr_batch2.py:194` 的 `sync_sidecar` 现在对 `status='busy'` 轮询等待，超时后在 217 行显式 `raise SystemExit('sidecar sync never left busy; refusing to assume it synced')`；`error` 只在少于 2 次时重试。以前 busy 会被当作同步完成继续往下跑。

## 四、真正的阻塞点：网关只给一个在途槽位

- 429 响应体是 `{"error":{"code":"gateway_concurrency_limit",...,"type":"rate_limit_error"}}`，限制的是同一账号的**在途请求数**，不是当日额度；每次被拒约等 30 秒才返回。
- 同一时刻边车日志里 `model=gpt-5.6-sol, purpose=wiki_page_modify` 调用成功（prompt 17,900 / completion 2,750 token），说明槽位被边车自身的 Wiki 生成占着。此时再跑平台侧摘要/概念补强，两边互相抢，谁都做不完；边车批次还会把自身 10 次重试耗光。
- 处理方式：串行。本机运行目录中的 `await_wiki_convergence.py` 连续两次采样到 `task_pending_ops` 归零且各知识库 `pending_tasks=0 / is_active=false` 之后，才启动补强循环，最后统一校验。
- 默认对话模型确认仍是 Codex，没有使用 deepseek。

## 五、附带查明的队列唤醒缺陷

Lite 模式的触发器不在持久层：`task_pending_ops` 的行是持久的，但唤醒它的 per-KB `wiki:ingest` 触发器只活在进程内。锁冲突时批次会立刻退出并交给 asynq 重试，重试到 10/10 时报 `Task failed (exhausted retries) ... err=concurrent wiki task active`，之后这批持久行**没有任何东西会再来唤醒**——每批只补一个后继触发器，`housekeeping` 只用这张表判断"文档是否真的卡住"，不会重建触发器。

- 实测：某长批次持锁期间，多个触发器耗尽重试；随后队列连续 3 次采样（9 分钟）保持 13 行、`claimed_at` 全空、`is_active=false`、页数不再增长。这 13 行都是本批已发布《中医古籍珍稀抄本精选》各册的 ingest 操作。
- 官方恢复路径是进程重启时 [recover_pending_wiki_tasks.go:120](../third_party/weknora/upstream/internal/container/recover_pending_wiki_tasks.go#L120) 按持久行重建触发器。本轮用 `scripts/stop-platform.ps1` + `scripts/start-platform.ps1` 重启服务，日志出现 `[WikiRecovery] recreated 1 trigger(s) from durable pending queues`，平台端口全部 ready。
- 该缺陷本身尚未修（需要改触发器耗尽路径并重编译边车）。

## 六、partial 资产补强：换了一条不重导的路

- 先用官方 `Ingestion.retry` 试跑一份：数据完全安全（文档指纹、knowledge_id、分块映射、Wiki 页数前后一致，边车 `reused=39, indexed=0`），但每份要先花约 19 分钟重建候选工作区，而且第一次图谱批次就被 429 打断。
- 改成本机运行目录中的 `enrich_partial_assets.py`：一次装载工作区，按资产调用 `enrich_knowledge`，靠 `EmbeddedRuntime._invoke` 的载荷摘要缓存做到每批成功即落盘、可断点续跑；每份写完前后对比 WeKnora 身份并在漂移时告警，限流即中断本趟并冷却，最后统一 `synchronize` + `scripts/rebuild_wiki.py validate`。
- 范围核实：25 个 `partial` 知识资产里，15 个已发布（需要补强），10 个仍在 `pending_review`、没有进入 WeKnora，不在本轮范围。

## 七、`validate` 的 `stats_idle` 不是数据损坏

`stats_idle = pending_tasks==0 and not is_active`（`scripts/rebuild_wiki.py:482`）。本轮 `rc=1` 只由 `knowledge.medical` 的 `pending_tasks` 触发，页面存在、每条知识被覆盖、无未知 source_ref、无空内容、图谱不截断等检查全部通过，即"还在生成"而非"生成坏了"。观察到的收敛：队列 47 → 13，Wiki 页数 1451 → 1469。

## 八、记入但本轮没有解决

1. 15 篇已发布 partial 资产的摘要/概念补强在本记录时点仍未完成，结果以链路的 `enrich-partial-report.json` 和最终 `validate` 为准。
2. 10 篇 `pending_review` 的 partial 资产需要人工发布复核，不属于技术失败。
3. 溯源缺口：`third_party/weknora/build-manifest.json` 只登记派生模块和构建摘要；本机运行边车二进制不随源码仓库上传。`upstream/` 的完整源码与锁定提交用于溯源，编译产物需在本机重新生成。
4. 登录后的页面点击验收仍未做（无用户凭据）。

## 九、验证

- 定向 Go 回归全部通过（`go test -count=1 -run 'TestIsTransientLLMError|TestSplitChunksIntoCitationBatches|TestClassifyChunkCitations' -v ./internal/application/service/`）：`TestSplitChunksIntoCitationBatches_RespectsBudgetAndOrder`、`TestClassifyChunkCitations_CountsFailedBatches`（含 every batch fails / one batch survives 两个子例）、`TestIsTransientLLMError_HTTPStatuses`、`_TransportSubstrings`、`_AbortsWhenParentCtxDone`、`_NilError`、`_RateLimit403` 均 PASS，日志 `.runtime/maintenance/go-wiki-regression.out`。
- 运行中的边车确实包含上述守卫：生产 Go 源码最后修改 21:43～21:44，二进制 21:54 构建，22:00 之后只改过 `_test.go`。
- 四个服务重启后健康检查全部就绪；`[WikiRecovery]` 确认重建了持久队列的唤醒触发器。

用户下一步：平台可用 `http://127.0.0.1:8079/login` 正常登录；补强与 Wiki 生成仍在后台运行，跑完后看 `.runtime/maintenance/wiki-then-enrich.out`、`enrich-partial-report.json` 的最终 `validate rc`，再决定是否需要人工发布复核。
