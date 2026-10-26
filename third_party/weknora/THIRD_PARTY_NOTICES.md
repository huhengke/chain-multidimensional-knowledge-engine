# 第三方通知

## WeKnora

来源：https://github.com/Tencent/WeKnora
锁定提交：97796c637aa53713edc846bd0a39470d687f0fdf
版权：Copyright (C) 2026 Tencent. All rights reserved.

上游 LICENSE 声明 WeKnora 为 MIT，所列第三方组件各自保留其许可证。完整原文（包括上游 opencc / Apache-2.0 通知）原样保存在 derived/LICENSE，没有删改通知。

LICENSE SHA-256：
99f1251d7662dda0b55ba9309a754a73223b5dd56f6417cf0eebcc2fd9e480f9

本项目抽取的原样文件列在 source-manifest.json；其他源码是宿主适配或有注明出处的局部抽取，记录在 patches/。本次编译的派生 Go 模块不包含外部 require，未链接上游 Vue、登录/租户、独立 Agent 或上游全部数据库/文档解析依赖。构建使用 Go 标准库，随附 GO_LICENSE.txt；完整上游快照仍保留其自身 LICENSE 和依赖清单。

## 派生范围

- 上游 chunker（原样）及其自测：父子块、标题层级、递归与受保护区域分割。
- knowledgebase_search_fusion.go（原样）：词法/向量候选的 RRF 融合。
- metric 下五个上游算法（原样）：Recall、MRR、NDCG、Precision、MAP；页面目前使用前三项。
- retrieval_config.go、extract_graph.go 和两个提示模板（原样）。
- 本工程添加 stdio 协议、最小类型、stderr 日志适配、模型 HTTP 调用、摘要/概念结果转换。
- linked_images.go 为 image_resolver.go 中链接图片解包表达式及函数的局部抽取，不含远程图片下载。

## 维护与分发

分发时保留 derived/LICENSE、此通知和 GO_LICENSE.txt；同时保留本项目其他 Python/Node/原生依赖的许可证记录。这里记录已核对的源码出处与编译范围，不把它描述为整个产品的法务或供应链发布审查已经完成。

升级需重新锁定真实提交、生成清单和差异、运行回归并记录兼容性，不得只改提交号。知识模型服务的商用、数据处理条款由所选提供方决定，不因 WeKnora 开源而自动获得服务授权。
