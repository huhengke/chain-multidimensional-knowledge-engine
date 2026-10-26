# 链式多维时空知识库引擎

本项目的产品名称为“链式多维时空知识库引擎”。“三脑”仅表示内部的三类独立认知执行器：结构化数据推理脑、知识数据推理脑和时空记忆推理脑，不再作为项目对外名称。

## 当前独立管理后台入口（2026-10-19 文档基线）

统一管理平台：<http://127.0.0.1:8079>。首次访问创建本项目管理员，不沿用原项目账号。前端位于 `frontend/`，服务入口为 `platform_server.app:app`；不加载演示数据，不调用 `ConsoleService`。

结构化引擎、Node Agent、CNTS Rust/Python 实现已迁入 `services/`，本项目启动自己的进程、空库与配置；运行不访问原项目目录、8000/8001 服务或旧 Catalog。来源迁移清单见 `services/source-migration.json`。共享数据源、三脑导入、模型配置、账号、系统参数与审计均由本项目承载。

WeKnora 已锁定至 `97796c637aa53713edc846bd0a39470d687f0fdf` 并抽取、编译为知识脑内部运行时；导入使用真实向量模型，语义检索使用融合与模型重排，文字/时间小候选快速路径可跳过查询模型。缺少所需模型会明确失败。未引入上游 Vue、账号或独立 Agent 产品层。范围与限制见 `third_party/weknora/README.md`。本交付仍需真实业务、模型、设备与运维验收，**不能等同于完整生产验收完成**。

首次使用请按 [首次使用与生产验收测试手册](docs/首次使用与生产验收测试手册.md) 从创建管理员开始。部署、运行目录和未完成事项见 [管理平台部署与使用说明](docs/production-platform.md)。旧 `/console` 仅供开发诊断，与此入口独立。

新增模式、代码依据与限制集中在[技术模式与实现现状 Markdown](docs/技术模式与实现现状.md) / [HTML](docs/技术模式与实现现状.html)。主规划书已同步至 V3.4；本次归档、原文档快照与验证范围见[同步记录](docs/archive/2026-10-19/归档与同步记录.md)。本轮没有重启服务或重新上传医书。

知识脑现在区分存入/真实观察时间与内容时间，自动生成内容时间索引并按候选求交。检索先走文字/时间边界，必要时才生成查询向量，重复语义问题使用有限 LRU；这些索引支持复用既有医书。当前仍有索引签名遍历、宽时间区间与同步导入开销，尚未实现 BM25/FTS5、ANN 或异步批量向量化，不把“约 1 秒”写成已达到的生产 SLA。

三脑均提供智慧树原理图与来源关系图；模型按脑和用途隔离。数据中心一个“数据接入”菜单内含结构化、知识、时空页签，另有统一导入任务；处理完成度与发布状态分别呈现。

后台主导航只提供一个“统一 Agent 工作台”。一次请求固定保留“输入/基座 → 记忆星团快速线索 → 三脑按需独立核验 → 证据合一”的 3+1 轨迹；星团不是第四个脑，也不能替代目标脑证据。结构化、知识、时空三个专脑工作台仍是独立页面和服务边界，只从统一 Agent 的“专脑管理与诊断”抽屉或运行步骤进入。知识拓扑采用独立画布，显示 WeKnora 内嵌运行层、中通内链/周存、向量留痕、知识星团、自动分枝、generation 和跨脑公共锚点，不复用结构化脑拓扑组件。

知识脑可使用本地中文向量、重排和 PDF OCR：执行 `scripts/setup-knowledge-local.ps1 -PythonExecutable <Python路径>` 后重启平台。依赖安装在本工程 `.runtime/platform-venv`，模型与固定文件校验清单位于 `.runtime/standalone/models`；启动器仅对平台服务选用这个环境。下载受限时可显式传入 `-HuggingFaceEndpoint https://hf-mirror.com`。安装步骤不会覆盖已启用的外部模型配置。WeKnora 桥通过受保护的内部接口调用真实 BGE 向量与重排模型；OCR 在本机逐页执行，支持繁体竖排、页码保留与重试复用。页面“模型配置”可分别验证对话、向量和重排推理，“导入任务”显示 OCR 和索引进度。

本工程已包含统一智慧树拓扑、统一认知控制面、三脑实现、跨脑编排、治理与契约测试的源码和测试。当前运行时状态应区分为：

- 多模态时空记忆脑与结构化数据推理脑沿用现有实现；本次 WeKnora 规划不改写它们的导入、索引、检索或数据结构。
- 管理平台知识脑通过 `EmbeddedRuntime` 调用真实 WeKnora 派生代码；定点向量、索引、近邻留痕和星团仍投影到本工程知识智慧树。
- 开发用 `BuiltinKnowledgeRuntime` 和独立控制面演示入口保留供契约测试，绝不是管理平台 WeKnora 失败后的回退。
- 独立控制面 API 的脑注册情况与管理平台部署不同，不能用开发控制面的 `BRAIN_NOT_CONNECTED` 推断平台服务状态。

- 语言：Python 3.11+
- 依赖：pydantic v2、FastAPI、uvicorn、httpx、jsonschema、OpenCV、PyAV、NumPy、pytest
- 管理平台以已认证服务端会话建立权限上下文；旧控制面适配器的主体请求头只用于开发集成，不能直接暴露为生产认证。

## 目录结构

```
链式多维时空知识库引擎/
├─ conftest.py                # 测试会话路径准备（工程根 + control-plane/src 加入 sys.path）
├─ pytest.ini / pyproject.toml
├─ docs/contracts/            # 7 份 JSON Schema（机器可校验）
├─ topology/                  # M1 统一智慧树认知拓扑
│  ├─ src/{ruler,ring,payload,node,trace,star_cluster,growth}.py
│  └─ tests/
├─ control-plane/             # M2 统一认知控制面
│  ├─ src/{api,router,profile,evidence,merge,audit}/
│  ├─ src/service.py
│  ├─ static/knowledge-workbench.{html,css,js}
│  ├─ static/console.{html,css,js}
│  └─ static/console-responsive.css
│  └─ tests/
├─ brains/                    # 脑边界协议 + M3/M4/M5 实现与测试
├─ orchestration/             # M6 三阶段编排与冲突处理
├─ governance/                # M7 权限、门禁、审计与版本治理
├─ third_party/weknora/       # M4 已锁定源码、派生桥、来源/构建清单与通知
└─ contracts-tests/           # M8 契约一致性 / 契约自检测试
```

## 安装

```powershell
python -m pip install -r requirements.txt
# 或
python -m pip install "pydantic>=2" fastapi uvicorn httpx jsonschema "opencv-python-headless>=4.10" "av>=12" "numpy>=1.26" pytest
```

## 运行测试

在工程根目录执行：

```powershell
python -m pytest
```

覆盖智慧树、三脑、编排、治理与契约的单元/契约测试，以及三项硬不变式：
- 留痕层 `aggregate_key` **不含日期**（正则断言）
- generation **不可变 / 可回滚**
- 配置档**冻结**与**禁止回退**

## 启动服务

`control-plane` 目录名含连字符，不能作为包名，故其 `src` 直接挂到 `sys.path`，
顶层模块为 `api` / `service` / `profile` / `router` / `evidence` / `merge` / `audit`。

在工程根目录执行：

```powershell
python -m uvicorn --app-dir control-plane/src api:app --host 127.0.0.1 --port 8077
# 或
python control-plane/src/service.py
```

对外接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/cognition/query` | 统一认知请求 |
| GET | `/api/v1/cognition/healthz` | 健康检查 |
| GET | `/api/v1/cognition/profile` | 查看活动配置档 |
| GET | `/knowledge/workbench` | 原生知识导入、检索与星团工作台 |
| GET | `/api/v1/knowledge/runtime` | 查看知识运行时与锁定状态 |
| POST | `/api/v1/knowledge/imports/text` | 创建受管文本导入草稿 |
| POST | `/api/v1/knowledge/search` | 返回检索、重排、证据与星团轨迹 |
| GET | `/api/v1/topology/cross-brain-links` | 读取知识脑生成的跨脑关联候选 |

## 本地三脑控制台

本节为历史开发诊断入口，不是上述独立管理后台的使用流程或账号说明。

启动服务后访问 [http://127.0.0.1:8077/console](http://127.0.0.1:8077/console)。这是明确标注为 `local_demo` 的交互验收入口，不是生产账户、租户或认证方案。

| 角色 | 本地演示口令 | 能力 |
|---|---|---|
| `admin` | `brain-demo-2026` | 加载夹具、三脑查询、导入、流会话、复核和治理查看 |
| `reviewer` | `review-demo-2026` | 查询、查看、处理具有既有发布权限的复核路径 |
| `observer` | `observe-demo-2026` | 查询、证据与审计只读查看 |

控制台默认是空的本地验收运行态：问题、知识正文、记忆事件和结构化指标均由用户自行输入，随后可查看处理状态、证据、generation 和审计。控制台提供总览、统一认知台、知识脑、时空记忆脑、结构化推理脑和拓扑治理六个视图。知识脑支持粘贴文本和选择本地 UTF-8 文本文件；结构化脑先写入本地测试指标、再按既有 `StructuredBrain` 契约查询，未写入时明确返回 `no_match`，绝不返回占位数值。可选夹具收在总览折叠区，且管理员主动加载的样例一律标为 `demo_fixture`；手工记忆导入标为 `manual_import`；真实摄像头或 RTSP 采样片段标为 `live_stream`。清空运行态会清除当前服务进程内存中的夹具和手工输入，页面会要求确认。

时空记忆脑有两个入口：离线受管文件/文本事件，或本机摄像头/RTSP。创建流会话和枚举设备不会打开硬件；只有页面的“启动采集”才会连接来源。RTSP URL 仅存于当前服务进程内存，响应、审计和错误只出现脱敏地址；停止或登出会关闭适配器并清除凭据。当前没有视觉、OCR、ASR、姿态或声学模型时，真实 JPEG 采样帧仍会进入受管记忆路径，但对应语义特征明确显示为 `unavailable`，不会伪造识别结果。

示例：

```powershell
curl -s http://127.0.0.1:8077/api/v1/cognition/healthz

curl -s -X POST http://127.0.0.1:8077/api/v1/cognition/query `
  -H "content-type: application/json" `
  -d '{"request_id":"r-1","trace_id":"t-1","raw_query":"上周华东区汽油销量环比如何",
       "time_scope":{"start":"2026-10-01","end":"2026-10-11","granularity":"day","confidence":"exact"}}'
```

## 三条硬不变式落点

1. **不留日期**：`topology/src/trace.py::assert_no_date_in_aggregate_key`
   （`TraceRecord` 校验器 + 测试正则断言）；日期仅经 `OccurrenceRef` 表达。
2. **禁止回退**：`control-plane/src/profile/__init__.py::ProfileRegistry.freeze`
   （引擎不可用或档位未激活时抛 `ProfileUnavailableError`，绝不回退其它档位）。
3. **无命中不报 0**：`control-plane/src/merge/__init__.py::Merger.merge`
   （无命中返回 `no_match: true, claims: []`，绝不合成数值 0）。
