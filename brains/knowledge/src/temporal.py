"""知识脑内容时间抽取与时间维度星团投影。

该模块只保存可重建的时间索引和来源定位，不拥有正文、向量或权限数据。
存入/观察时间和内容时间分开建索引，避免把“资料何时进入系统”误当成
“资料描述的事件何时发生”。
"""

from __future__ import annotations

import calendar
import hashlib
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field


_DATE_RE = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})(?:\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*(?:日|号)?)?")
_FULL_DATE_RE = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*(?:日|号)")
_ISO_DATE_RE = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)")
_MONTH_RE = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月")
_YEAR_RANGE_RE = re.compile(r"(?<!\d)(?P<start>19\d{2}|20\d{2})\s*(?:年)?\s*(?:至|到|[-—~～])\s*(?P<end>19\d{2}|20\d{2})\s*年?")
_RELATIVE_RE = re.compile(r"(?P<term>去年|前年|今年|明年|上个月|上月|本月|下个月|下月|上季度|本季度|下季度)")


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _as_date(value: str | datetime | date | None, fallback: date) -> date:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        match = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
        if match:
            year = int(match.group(1))
            month = int(match.group(2) or 1)
            day = int(match.group(3) or 1)
            return date(year, month, day)
    return fallback


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _year_range(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), _month_end(year, month)


def _quarter_range(year: int, quarter: int) -> tuple[date, date]:
    month = (quarter - 1) * 3 + 1
    return date(year, month, 1), _month_end(year, month + 2)


class ContentTimeAnchor(BaseModel):
    """从知识内容中抽取的时间锚点。"""

    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    start: str = Field(min_length=10)
    end: str = Field(min_length=10)
    granularity: str = "unknown"  # day | month | year | range | unknown
    time_type: str = "occurrence"  # occurrence | effective | published | revised | validity
    raw_expression: str = ""
    source_locator: dict[str, Any] = Field(default_factory=dict)
    extraction_method: str = "body"
    confidence: str = "0"
    status: str = "exact"  # exact | range | candidate | undetermined
    reference_date: str | None = None


class KnowledgeTemporalCluster(BaseModel):
    """按内容时间分区的可重建星团投影。"""

    model_config = ConfigDict(extra="forbid")

    temporal_cluster_id: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    start: str = Field(min_length=10)
    end: str = Field(min_length=10)
    granularity: str = "year"
    member_unit_ids: list[str] = Field(default_factory=list)
    anchor_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    status: str = "active"
    generation: int = 1
    created_at: str = ""


def _metadata_candidates(raw: Any) -> Iterable[tuple[str, str, str, str, str]]:
    """返回 (value, time_type, method, status, confidence) 候选。"""
    metadata = getattr(raw, "metadata", {}) or {}
    explicit = (
        (getattr(raw, "effective_at", None) or metadata.get("effective_at"), "effective"),
        (getattr(raw, "published_at", None) or metadata.get("published_at"), "published"),
        (getattr(raw, "revised_at", None) or metadata.get("revised_at"), "revised"),
    )
    for value, time_type in explicit:
        if value:
            yield str(value), time_type, "metadata", "exact", "1"
    for key in ("content_time", "content_time_start", "event_time", "occurrence_at", "valid_from"):
        value = metadata.get(key)
        if value:
            yield str(value), "occurrence" if key != "valid_from" else "validity", "metadata", "exact", "1"


def _parse_absolute(expression: str) -> tuple[date, date, str] | None:
    text = expression.strip()
    range_match = _YEAR_RANGE_RE.search(text)
    if range_match:
        start_year = int(range_match.group("start"))
        end_year = int(range_match.group("end"))
        if start_year <= end_year:
            return date(start_year, 1, 1), date(end_year, 12, 31), "range"
    full = _FULL_DATE_RE.search(text) or _ISO_DATE_RE.search(text)
    if full:
        year = int(full.group("year"))
        month = int(full.group("month"))
        day = int(full.group("day"))
        try:
            value = date(year, month, day)
        except ValueError:
            return None
        return value, value, "day"
    month_match = _MONTH_RE.search(text)
    if month_match:
        year, month = int(month_match.group("year")), int(month_match.group("month"))
        if 1 <= month <= 12:
            return (*_month_range(year, month), "month")
    match = _DATE_RE.search(text)
    if not match:
        return None
    year = int(match.group("year"))
    month_text = match.group("month")
    day_text = match.group("day")
    if month_text is None:
        return (*_year_range(year), "year")
    month = int(month_text)
    if not 1 <= month <= 12:
        return None
    if day_text is None:
        return (*_month_range(year, month), "month")
    day = int(day_text)
    try:
        value = date(year, month, day)
    except ValueError:
        return None
    return value, value, "day"


def _parse_relative(expression: str, reference: date) -> tuple[date, date, str] | None:
    match = _RELATIVE_RE.search(expression)
    if not match:
        return None
    term = match.group("term")
    if term in {"去年", "前年", "今年", "明年"}:
        offset = {"前年": -2, "去年": -1, "今年": 0, "明年": 1}[term]
        return (*_year_range(reference.year + offset), "year")
    if term in {"上月", "上个月", "本月", "下月", "下个月"}:
        offset = {"上月": -1, "上个月": -1, "本月": 0, "下月": 1, "下个月": 1}[term]
        month_index = reference.year * 12 + reference.month - 1 + offset
        year, month = divmod(month_index, 12)
        return (*_month_range(year, month + 1), "month")
    quarter = (reference.month - 1) // 3 + 1
    offset = {"上季度": -1, "本季度": 0, "下季度": 1}[term]
    quarter_index = reference.year * 4 + quarter - 1 + offset
    year, quarter_index = divmod(quarter_index, 4)
    return (*_quarter_range(year, quarter_index + 1), "range")


def _absolute_expressions(text: str) -> list[str]:
    """按最长表达式优先返回不重叠的绝对时间片段。"""
    matches: list[tuple[int, int, str]] = []
    for pattern in (_FULL_DATE_RE, _ISO_DATE_RE, _YEAR_RANGE_RE, _MONTH_RE, _DATE_RE):
        for match in pattern.finditer(text or ""):
            matches.append((match.start(), match.end(), match.group(0)))
    result: list[str] = []
    occupied: list[tuple[int, int]] = []
    for start, end, expression in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < right and end > left for left, right in occupied):
            continue
        occupied.append((start, end))
        result.append(expression)
    return result


def extract_content_time(
    *,
    raw: Any,
    unit_id: str,
    text: str,
    title: str | None,
    heading_path: list[str] | None,
    locator: dict[str, Any] | None,
    ingested_at: str,
) -> list[ContentTimeAnchor]:
    """自动抽取一个知识单元的内容时间，不抛出不确定表达式。"""
    reference = _as_date(
        getattr(raw, "published_at", None)
        or getattr(raw, "effective_at", None)
        or getattr(raw, "retrieved_at", None)
        or ingested_at,
        date.today(),
    )
    source_locator = dict(locator or {})
    context_parts = [str(title or ""), *[str(item) for item in (heading_path or [])], text or ""]
    context = "\n".join(part for part in context_parts if part)
    anchors: list[ContentTimeAnchor] = []
    seen: set[tuple[str, str, str, str]] = set()

    def append(value: tuple[date, date, str] | None, expression: str, method: str, time_type: str, confidence: str, status: str = "exact"):
        if value is None:
            return
        start, end, granularity = value
        key = (start.isoformat(), end.isoformat(), time_type, method)
        if key in seen:
            return
        seen.add(key)
        anchor_id = "kta-" + _digest(unit_id, *key, expression)[:16]
        anchors.append(ContentTimeAnchor(
            anchor_id=anchor_id,
            unit_id=unit_id,
            start=start.isoformat(),
            end=end.isoformat(),
            granularity=granularity,
            time_type=time_type,
            raw_expression=expression,
            source_locator=source_locator,
            extraction_method=method,
            confidence=confidence,
            status=status,
            reference_date=reference.isoformat() if method == "relative" else None,
        ))

    for value, time_type, method, status, confidence in _metadata_candidates(raw):
        parsed = _parse_absolute(value) or _parse_relative(value, reference)
        append(parsed, value, method, time_type, confidence, status)

    # 标题和章节时间比正文更可能是作用域约束，但仍保留正文定位。
    heading_text = "\n".join([str(title or ""), *[str(item) for item in (heading_path or [])]])
    for expression in _absolute_expressions(heading_text):
        append(_parse_absolute(expression), expression, "title_or_heading", "occurrence", "0.95")
    for match in _RELATIVE_RE.finditer("\n".join([str(title or ""), *[str(item) for item in (heading_path or [])]])):
        expression = match.group(0)
        append(_parse_relative(expression, reference), expression, "relative", "occurrence", "0.9")

    for expression in _absolute_expressions(text or ""):
        append(_parse_absolute(expression), expression, "body", "occurrence", "0.9")
    for match in _RELATIVE_RE.finditer(text or ""):
        expression = match.group(0)
        append(_parse_relative(expression, reference), expression, "relative", "occurrence", "0.8")

    # 只保留能定位到具体时间的锚点；模糊表达式仍由正文语义检索处理，
    # 不制造一个看似精确的时间。
    return anchors


@dataclass(frozen=True)
class TemporalQueryPlan:
    content_start: str | None = None
    content_end: str | None = None
    ingest_start: str | None = None
    ingest_end: str | None = None
    observed_start: str | None = None
    observed_end: str | None = None
    mode: str = "none"  # none | content | ingest | observed | dual
    source: str = "explicit"

    @property
    def has_content(self) -> bool:
        return bool(self.content_start or self.content_end)

    @property
    def has_ingest(self) -> bool:
        return bool(self.ingest_start or self.ingest_end)

    @property
    def has_observed(self) -> bool:
        return bool(self.observed_start or self.observed_end)


def infer_query_plan(query: str, *, reference: date | None = None) -> TemporalQueryPlan:
    """从自然语言识别内容时间和“最近/昨天”存入时间。"""
    query = query or ""
    reference = reference or datetime.now(timezone.utc).date()
    content: tuple[date, date, str] | None = None
    ingest: tuple[date, date, str] | None = None
    observed: tuple[date, date, str] | None = None

    # “听、看、读、浏览、查看”表达用户行为。行为时间优先走观察倒排，
    # 同时保留存入时间作为自然语言查询的兼容降级入口：老资料可能没有
    # observed_at，但仍应能按导入时间返回可追溯结果。
    activity_query = any(term in query for term in ("听", "看", "读", "浏览", "查看", "阅读", "关联"))

    # 查询中的绝对时间默认属于内容时间；“最近/昨天/前天”属于存入时间。
    absolute = _parse_absolute(query)
    if absolute:
        content = absolute
    relative_matches = list(_RELATIVE_RE.finditer(query))
    for match in relative_matches:
        term = match.group("term")
        parsed = _parse_relative(term, reference)
        if parsed is None:
            continue
        if term in {"去年", "前年", "今年", "明年", "上季度", "本季度", "下季度"}:
            content = parsed
        elif term in {"上月", "上个月", "本月", "下月", "下个月"}:
            content = parsed

    if "昨天" in query:
        day = reference - timedelta(days=1)
        ingest = (day, day, "day")
        if activity_query:
            observed = ingest
    elif "前天" in query:
        day = reference - timedelta(days=2)
        ingest = (day, day, "day")
        if activity_query:
            observed = ingest
    elif any(term in query for term in ("最近", "近来", "近期", "刚刚看", "刚看过", "最近看")):
        ingest = (reference - timedelta(days=30), reference, "range")
        if activity_query:
            observed = ingest

    if content and (ingest or observed):
        mode = "dual"
    elif content:
        mode = "content"
    elif observed:
        mode = "observed"
    elif ingest:
        mode = "ingest"
    else:
        mode = "none"
    return TemporalQueryPlan(
        content_start=content[0].isoformat() if content else None,
        content_end=content[1].isoformat() if content else None,
        ingest_start=ingest[0].isoformat() if ingest else None,
        ingest_end=ingest[1].isoformat() if ingest else None,
        observed_start=observed[0].isoformat() if observed else None,
        observed_end=observed[1].isoformat() if observed else None,
        mode=mode,
        source="inferred",
    )


class KnowledgeTemporalIndex:
    """内容时间和存入/观察时间的倒排索引。"""

    def __init__(self) -> None:
        self.anchors_by_unit: dict[str, list[ContentTimeAnchor]] = {}
        self.content_units_by_day: dict[str, set[str]] = defaultdict(set)
        self.ingest_units_by_day: dict[str, set[str]] = defaultdict(set)
        self.observed_units_by_day: dict[str, set[str]] = defaultdict(set)
        self.content_days: list[str] = []
        self.ingest_days: list[str] = []
        self.observed_days: list[str] = []
        self.clusters: dict[str, KnowledgeTemporalCluster] = {}
        self.cluster_members_by_bucket: dict[str, set[str]] = defaultdict(set)
        self.unit_cluster_ids: dict[str, set[str]] = defaultdict(set)
        self.last_build_stats: dict[str, int] = {}

    def clear(self) -> None:
        self.__init__()

    @staticmethod
    def _days(start: str, end: str) -> Iterable[str]:
        left, right = date.fromisoformat(start), date.fromisoformat(end)
        while left <= right:
            yield left.isoformat()
            left += timedelta(days=1)

    @staticmethod
    def _bucket(anchor: ContentTimeAnchor) -> str:
        if anchor.granularity == "day":
            return anchor.start
        if anchor.granularity == "month":
            return anchor.start[:7]
        return anchor.start[:4]

    def add_unit(
        self,
        *,
        unit_id: str,
        tree_id: str,
        document_id: str,
        anchors: list[ContentTimeAnchor],
        ingested_at: str | None,
        observed_at: str | None,
        created_at: str,
        generation: int = 1,
    ) -> None:
        self.anchors_by_unit[unit_id] = list(anchors)
        for anchor in anchors:
            for day in self._days(anchor.start, anchor.end):
                self.content_units_by_day[day].add(unit_id)
            bucket = self._bucket(anchor)
            cluster_id = "ktc-" + _digest(tree_id, bucket)[:16]
            cluster = self.clusters.get(cluster_id)
            if cluster is None:
                cluster = KnowledgeTemporalCluster(
                    temporal_cluster_id=cluster_id,
                    tree_id=tree_id,
                    bucket=bucket,
                    start=anchor.start,
                    end=anchor.end,
                    granularity=anchor.granularity,
                    created_at=created_at,
                    generation=generation,
                )
                self.clusters[cluster_id] = cluster
            else:
                cluster.start = min(cluster.start, anchor.start)
                cluster.end = max(cluster.end, anchor.end)
            if unit_id not in cluster.member_unit_ids:
                cluster.member_unit_ids.append(unit_id)
            if anchor.anchor_id not in cluster.anchor_ids:
                cluster.anchor_ids.append(anchor.anchor_id)
            if document_id not in cluster.document_ids:
                cluster.document_ids.append(document_id)
            self.cluster_members_by_bucket[bucket].add(unit_id)
            self.unit_cluster_ids[unit_id].add(cluster_id)

        for value, target in ((ingested_at, self.ingest_units_by_day), (observed_at, self.observed_units_by_day)):
            if value:
                day = _as_date(value, date.today()).isoformat()
                target[day].add(unit_id)

    def rebuild(
        self,
        *,
        units: Iterable[Any],
        unit_tree: dict[str, str],
        anchors_by_unit: dict[str, list[ContentTimeAnchor]],
        ingested_at_by_unit: dict[str, str],
        observed_at_by_unit: dict[str, str],
        created_at: str,
        generation_by_tree: dict[str, int] | None = None,
    ) -> dict[str, int]:
        self.clear()
        for unit in units:
            unit_id = unit.unit_id
            self.add_unit(
                unit_id=unit_id,
                tree_id=unit_tree.get(unit_id, "knowledge.general"),
                document_id=unit.document_id,
                anchors=anchors_by_unit.get(unit_id, []),
                ingested_at=ingested_at_by_unit.get(unit_id),
                observed_at=observed_at_by_unit.get(unit_id),
                created_at=created_at,
                generation=(generation_by_tree or {}).get(unit_tree.get(unit_id, "knowledge.general"), 1),
            )
        self.last_build_stats = {
            "content_anchors": sum(len(items) for items in self.anchors_by_unit.values()),
            "content_days": len(self.content_units_by_day),
            "ingest_days": len(self.ingest_units_by_day),
            "observed_days": len(self.observed_units_by_day),
            "temporal_clusters": len(self.clusters),
            "temporal_members": sum(len(item.member_unit_ids) for item in self.clusters.values()),
        }
        # Day keys are kept sorted so a year/month query visits only the
        # relevant buckets instead of iterating every historical day.
        self.content_days = sorted(self.content_units_by_day)
        self.ingest_days = sorted(self.ingest_units_by_day)
        self.observed_days = sorted(self.observed_units_by_day)
        return dict(self.last_build_stats)

    @staticmethod
    def _select_days(
        mapping: dict[str, set[str]],
        sorted_days: list[str],
        start: str | None,
        end: str | None,
    ) -> set[str]:
        if not (start or end):
            return set()
        left, right = start or "0001-01-01", end or "9999-12-31"
        start_index = bisect_left(sorted_days, left)
        end_index = bisect_right(sorted_days, right)
        return {
            unit_id
            for day in sorted_days[start_index:end_index]
            for unit_id in mapping[day]
        }

    def content_candidates(self, start: str | None, end: str | None) -> set[str]:
        return self._select_days(self.content_units_by_day, self.content_days, start, end)

    def ingest_candidates(self, start: str | None, end: str | None) -> set[str]:
        return self._select_days(self.ingest_units_by_day, self.ingest_days, start, end)

    def observed_candidates(self, start: str | None, end: str | None) -> set[str]:
        return self._select_days(self.observed_units_by_day, self.observed_days, start, end)

    def clusters_for_units(self, unit_ids: set[str]) -> list[str]:
        result = {cluster_id for unit_id in unit_ids for cluster_id in self.unit_cluster_ids.get(unit_id, set())}
        return sorted(result)

    def view(self) -> dict[str, Any]:
        return {
            "stats": dict(self.last_build_stats),
            "clusters": [cluster.model_dump(mode="json") for cluster in self.clusters.values()],
        }


__all__ = [
    "ContentTimeAnchor",
    "KnowledgeTemporalCluster",
    "KnowledgeTemporalIndex",
    "TemporalQueryPlan",
    "extract_content_time",
    "infer_query_plan",
]
