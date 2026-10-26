"""M2 意图路由（router）。

按问题意图裁剪 ``targets``（三脑）。默认三脑全选；命中意图关键词则裁剪。
规则实现为真实可解释逻辑（关键词命中 + 候选集合约束）。
"""

from __future__ import annotations

from brains.protocol import BrainName

ALL_TARGETS: tuple[BrainName, ...] = (
    BrainName.memory,
    BrainName.knowledge,
    BrainName.structured,
)

# 意图关键词（真实规则；命中即视为该脑相关）
INTENT_KEYWORDS: dict[BrainName, tuple[str, ...]] = {
    BrainName.structured: (
        "销量", "环比", "同比", "销售额", "指标", "数量", "汇总", "合计", "统计",
        "多少", "增长率", "占比", "金额", "均值", "总量", "排行", "排名", "分布",
    ),
    BrainName.memory: (
        "记得", "照片", "视频", "语音", "录音", "上次", "曾经", "我拍", "回忆",
        "影像", "拍摄", "那天", "当面", "当时", "见到", "听到",
    ),
    BrainName.knowledge: (
        "规则", "定义", "依据", "条款", "标准", "为什么", "知识", "文档", "规范",
        "政策", "法律", "解释", "案例", "条件", "例外", "如何判定", "原理", "办法",
    ),
}


class Router:
    def __init__(self, all_targets: tuple[BrainName, ...] = ALL_TARGETS) -> None:
        self.all_targets = all_targets

    def _normalize(self, requested) -> list[BrainName]:
        if not requested:
            return list(self.all_targets)
        result: list[BrainName] = []
        for item in requested:
            try:
                brain = item if isinstance(item, BrainName) else BrainName(str(item))
            except ValueError as exc:
                raise ValueError(f"未知 target：{item!r}，应为 {[b.value for b in self.all_targets]}") from exc
            if brain not in result:
                result.append(brain)
        return result

    def route(self, raw_query: str, requested=None) -> list[BrainName]:
        """返回目标脑集合（保持规范顺序）。

        - ``requested`` 缺省：三脑全选，再由意图关键词裁剪（无明确意图则保留全集）；
        - ``requested`` 显式给定：视为调用方意图，按规范顺序返回，不再按关键词裁剪。
        """
        if requested:
            return self._normalize(requested)
        query = raw_query or ""
        selected = [
            brain
            for brain in self.all_targets
            if any(kw in query for kw in INTENT_KEYWORDS[brain])
        ]
        if not selected:
            # 无明确意图：保留候选全集，交由各脑与合并阶段自行判定命中
            return list(self.all_targets)
        return selected