# 链式多维时空知识库引擎 · GitHub 上传说明

这个目录是“链式多维时空知识库引擎”的源码上传副本，目录本身可以直接作为 GitHub 仓库根目录。“三脑”只保留为内部架构术语，指三个独立认知执行器。

## 已包含

- 三脑源码：`brains/`、`topology/`、`orchestration/`、`governance/`
- 独立管理平台：`platform_server/`、`frontend/`
- 结构化引擎、CNTS、Node Agent：`services/`
- WeKnora 的锁定溯源、派生桥、补丁、许可证和构建清单：`third_party/weknora/`
- 测试、JSON Schema、部署脚本和生产验收文档
- 产品与技术规划书、全量实施计划和架构图

## 未上传

`.gitignore` 会排除本机运行态和生成物，包括：

- `.runtime/`、SQLite/数据库、知识快照、模型文件和向量索引
- 医书、销售 Excel、用户上传原件和 OCR/解析缓存
- API 密钥、密钥文件、日志和本地会话
- `node_modules/`、Python 缓存、Rust/Go 构建目录、前端 `dist-*` 历史备份
- WeKnora 编译二进制 `third_party/weknora/bin/`

因此 GitHub 仓库是可复现的源码仓库，不包含当前机器上的业务数据和运行状态。首次运行需要按 `README.md` 和 `docs/production-platform.md` 安装依赖，并重新生成前端和 WeKnora 本地构建产物。

## 上传前检查

在此目录执行：

```powershell
git init
git add .
git status --short
git diff --cached --stat
```

确认暂存区没有数据库、模型、原始资料、密钥或日志后，再绑定 GitHub 远程仓库并提交。

项目级许可证为 Apache License 2.0，见根目录 `LICENSE`；根目录 `NOTICE` 和 `third_party/weknora/` 中保留了 WeKnora 及其他第三方许可证、版权与来源通知。

## 发布前检查

请确认仓库历史中没有曾经提交过密钥、数据库或业务原件。Git 忽略规则只阻止后续加入，不会从历史中删除已经提交的敏感文件。
