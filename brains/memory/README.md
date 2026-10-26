# M3 多模态时空记忆脑

本目录已有 `MemoryBrain`、`ingest/extract/normalize/trace+growth/recall` 五层管道、自动记忆星团与测试。敏感身份、声纹和精确位置经策略门禁，不进入默认自动星团。

WeKnora 只属于知识认知脑的内部运行时。本脑的素材接入、特征提取、向量留痕、智慧树生长与检索保持原实现；跨脑联想最多接收受控的时间、实体、事件或稳定概念查询约束，不读取 WeKnora 索引或混合原始向量空间。控制面未注册本脑时返回显式 `BRAIN_NOT_CONNECTED`。

## 受控视频入口

`src/streaming.py` 提供离线导入之外的第二个入口：本机摄像头（OpenCV）与 RTSP（PyAV）通过 `StreamSessionService` 生成受管 `StreamSegment`，再统一调用 `MemoryBrain.ingest()`。视频适配器不能直接写存储。

- 摄像头只在显式 `start` 时打开；设备枚举不会读取帧。
- RTSP 用户名、口令和完整 URL 只在服务端会话内存，停止、登出或连续失败后清除；对外只返回脱敏来源。
- 会话状态为 `draft -> connecting -> running -> degraded | failed | stopped`。停止会关闭活动适配器并短暂等待工作线程退出。
- 受控片段列表上限为 48；过期采样帧的原始字节会按保留策略回收，记忆单元和留痕仍保留可审计引用。
- 当前未配置视觉、OCR、ASR、姿态与声学模型；采样帧可形成来源/时间元数据记忆，但视觉语义特征必须为 `unavailable`。
