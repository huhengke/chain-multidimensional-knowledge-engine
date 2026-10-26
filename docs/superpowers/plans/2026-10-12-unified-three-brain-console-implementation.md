# 三脑统一交互演示后台实施计划

**依据**：`2026-10-12-unified-three-brain-console-design.md`（已获确认）  
**目标**：在不伪造生产接入、不改变 WeKnora 仅属于知识脑边界的前提下，交付可登录、可点击验证三脑和真实 RTSP/摄像头入口的本地控制台。

## 约束

- 保持默认 `CognitionService()` 的生产保守构造不变；只有 `/console` 使用显式本地 demo runtime。
- 所有三脑通过现有 `Brain`/`BrainQuery`/`BrainResult` 契约接入控制面；不得复制其算法或混合向量空间。
- RTSP 凭据只存服务端会话内存，任何 response、日志、审计和异常均输出脱敏来源。
- 摄像头只由显式 `/start` 动作打开；自动化测试只用假适配器。

## 实施步骤

1. **测试基线与依赖声明**
   - 将 OpenCV、PyAV、NumPy 声明为运行依赖；保持当前已安装环境可运行。
   - 新建控制台测试模块，覆盖登录、越权、夹具、三脑统一查询、流会话与凭据脱敏。

2. **受控实时流模块**
   - 新建 `brains/memory/src/streaming.py`：`StreamSession`、`StreamSegment`、状态枚举、`VideoStreamAdapter` 协议、OpenCV 摄像头适配器、PyAV RTSP 适配器和可注入假适配器。
   - `StreamSessionService` 管理有界队列、采样工作线程、重连上限、JPEG 预览、瞬态凭据和停止清理；采样片段只经 `MemoryBrain.ingest()` 写入。
   - 扩展 `RawMediaStore` 支持受管 JPEG 字节，并确保所有 raw-media 回放仍由既有权限门禁保护。

3. **本地三脑验收运行时**
   - 新建 `control-plane/src/console.py`：内存会话、固定本地角色、可选夹具和可手工写入的本地结构化引擎客户端，以及 `ConsoleService`。
   - 以三个真实脑构造独立 demo `CognitionService`；默认运行态为空，用户可自行写入知识材料、时空记忆事件和结构化聚合指标；夹具仅在管理员主动点击后幂等加载。
   - 提供可操作及只读投影：三脑状态、记忆单元/星团/代际、结构化数据集与手工写入值、控制面审计、治理门禁和跨脑候选。

4. **API 与治理接线**
   - 在 `api/__init__.py` 注册 `/console` 页面与 `/api/v1/console/*`。
   - Cookie 会话必须服务端验证；所有写操作经角色 scope 和既有 `Governance` 判定。
   - 实现登录/登出、概览、可选夹具加载/清空当前内存运行态、统一查询、文本与本地文件知识导入、记忆导入、结构化指标写入/查询、拓扑/治理视图、流设备枚举、流创建/启动/停止/预览/标注。
   - 原有 `/knowledge/workbench` 与 API 契约保持不变。

5. **统一后台 UI**
   - 新增登录页和单页控制台原生 HTML/CSS/JS：概览、统一认知台、知识脑、记忆脑、结构化脑、拓扑治理；输入框默认空白，夹具收于可选折叠区。
   - 所有状态、错误与来源标签可见；未经权限的操作由 API 返回显式拒绝。
   - 知识脑工作台以链接方式复用，避免两套不一致的导入实现。

6. **验证与交付**
   - 执行完整 `pytest`、`compileall`、依赖检查和 demo HTTP 端到端路径。
   - 浏览器验证桌面/移动布局、操作链路、文字对比度和控制台错误。
   - 在用户主动开启时进行一次本机 `Honor Camera` 的短时实际采集；若设备拒绝，报告真实错误而不以假帧替代。
   - RTSP 适配器使用假源自动验证；真实 RTSP 需用户后续在控制台填写可访问地址。
