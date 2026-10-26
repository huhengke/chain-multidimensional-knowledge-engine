"""M2 证据聚合（evidence）。

聚合各脑返回的证据引用，按请求分配稳定、可追溯的 ``evidence_id``（``ev-N``），
并按 (source, ref) 去重。
"""

from __future__ import annotations

from brains.protocol import BrainName, EvidenceRef

_CANONICAL_ORDER: tuple[BrainName, ...] = (
    BrainName.memory,
    BrainName.knowledge,
    BrainName.structured,
)


class EvidenceAggregator:
    def aggregate(self, per_brain: dict[BrainName, list[EvidenceRef]]) -> list[EvidenceRef]:
        """把各脑证据引用归并为带 ``ev-N`` 编号的有序列表。"""
        seen: set[tuple[str, str]] = set()
        result: list[EvidenceRef] = []
        counter = 0
        ordered = [b for b in _CANONICAL_ORDER if b in per_brain]
        ordered += [b for b in per_brain if b not in _CANONICAL_ORDER]
        for brain in ordered:
            for ref in per_brain[brain]:
                key = (ref.source.value, ref.ref)
                if key in seen:
                    continue
                seen.add(key)
                counter += 1
                result.append(
                    EvidenceRef(
                        evidence_id=f"ev-{counter}",
                        source=ref.source,
                        ref=ref.ref,
                        detail=ref.detail,
                    )
                )
        return result