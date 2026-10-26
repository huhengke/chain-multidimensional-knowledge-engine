"""M1 记忆星团 / 特征共振索引（StarCluster）。

星团是**时间无关的聚合身份**：仅以
``向量空间/模型版本 + 归一化空间位置 + 向量值 + 出现次数``识别同类特征。
日期通过成员引用（``member_trace_ids`` 及外部 OccurrenceRef）另行保存。

本模块提供真实的增量聚类：向量落在原型 ``ε`` 邻域内即并入，
并更新中心与离散度（Welford 式在线更新，全程 Decimal，无浮点）。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

from topology.src.trace import TraceRecord

_TOLERANCE_RE = re.compile(r"^(?:eps=)?([0-9]*\.?[0-9]+)$")


def to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"非定点数：{value!r}") from exc


def _to_vec(values) -> list[Decimal]:
    return [to_decimal(v) for v in values]


def _vec_sub(a: list[Decimal], b: list[Decimal]) -> list[Decimal]:
    return [x - y for x, y in zip(a, b)]


def _vec_scale(a: list[Decimal], k: Decimal) -> list[Decimal]:
    return [x * k for x in a]


def _vec_add(a: list[Decimal], b: list[Decimal]) -> list[Decimal]:
    return [x + y for x, y in zip(a, b)]


def _norm2(a: list[Decimal]) -> Decimal:
    total = Decimal(0)
    for x in a:
        total += x * x
    return total


def euclidean(a: list[Decimal], b: list[Decimal]) -> Decimal:
    if len(a) != len(b):
        raise ValueError("向量维度不一致")
    return _norm2(_vec_sub(a, b)).sqrt()


def fmt(value: Decimal) -> str:
    """规范化定点字符串（去掉尾随零，永不输出科学计数法）。"""
    return format(value.normalize(), "f")


def parse_tolerance(tolerance: str) -> Decimal:
    match = _TOLERANCE_RE.match(tolerance or "")
    if not match:
        raise ValueError(f"tolerance 必须形如 'eps=0.05'：{tolerance!r}")
    return Decimal(match.group(1))


class StarCluster(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    cluster_id: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    prototype_vector: list[str]
    tolerance: str
    occurrence_count: int = Field(ge=0)
    centroid: list[str]
    dispersion: str
    sub_clusters: list[str] = Field(default_factory=list)
    algo_version: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    growth_events: list[str] = Field(default_factory=list)
    member_trace_ids: list[str] = Field(default_factory=list)

    @field_validator("tolerance")
    @classmethod
    def _check_tolerance(cls, value: str) -> str:
        parse_tolerance(value)
        return value

    @field_validator("prototype_vector", "centroid")
    @classmethod
    def _check_fixed_point_vector(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("向量不得为空")
        for item in value:
            to_decimal(item)
        return value

    @field_validator("dispersion")
    @classmethod
    def _check_dispersion(cls, value: str) -> str:
        to_decimal(value)
        return value

    def eps(self) -> Decimal:
        return parse_tolerance(self.tolerance)


class StarClusterIndex:
    """按 ``tree_id`` 组织的增量聚类索引（时间无关）。"""

    def __init__(
        self,
        tree_id: str,
        tolerance: str = "eps=0.05",
        algo_version: str = "v1",
        rule_version: str = "v1",
    ) -> None:
        if not tree_id:
            raise ValueError("tree_id 不得为空")
        self.tree_id = tree_id
        self.tolerance = tolerance
        self.algo_version = algo_version
        self.rule_version = rule_version
        parse_tolerance(tolerance)
        self._clusters: dict[str, StarCluster] = {}
        self._ss: dict[str, Decimal] = {}  # cluster_id -> 成员到中心的平方距离和
        self._counter = 0

    # ---- 查询 ------------------------------------------------------------
    @property
    def clusters(self) -> dict[str, StarCluster]:
        return dict(self._clusters)

    def get(self, cluster_id: str) -> StarCluster:
        return self._clusters[cluster_id]

    def __len__(self) -> int:
        return len(self._clusters)

    # ---- 分配 ------------------------------------------------------------
    def assign(self, trace: TraceRecord) -> StarCluster:
        """把一条留痕并入最近的原型邻域，或建立新星团。

        返回被并入/新建的星团。**不**读取任何日期信息。
        """
        vector = _to_vec(trace.vector)
        best: StarCluster | None = None
        best_dist: Decimal | None = None
        for cluster in self._clusters.values():
            proto = _to_vec(cluster.prototype_vector)
            if len(proto) != len(vector):
                continue
            dist = euclidean(proto, vector)
            if dist <= cluster.eps() and (best_dist is None or dist < best_dist):
                best = cluster
                best_dist = dist
        if best is None:
            return self._create(trace, vector)
        return self._incorporate(best, trace, vector)

    def _new_id(self) -> str:
        self._counter += 1
        return f"sc-{self.tree_id.split('.')[-1]}-{self._counter}"

    def _create(self, trace: TraceRecord, vector: list[Decimal]) -> StarCluster:
        cluster = StarCluster(
            cluster_id=self._new_id(),
            tree_id=self.tree_id,
            prototype_vector=[fmt(x) for x in vector],
            tolerance=self.tolerance,
            occurrence_count=1,
            centroid=[fmt(x) for x in vector],
            dispersion="0",
            algo_version=self.algo_version,
            rule_version=self.rule_version,
            member_trace_ids=[trace.trace_id],
        )
        self._clusters[cluster.cluster_id] = cluster
        self._ss[cluster.cluster_id] = Decimal(0)
        return cluster

    def _incorporate(
        self, cluster: StarCluster, trace: TraceRecord, vector: list[Decimal]
    ) -> StarCluster:
        old_n = Decimal(cluster.occurrence_count)
        new_n = old_n + 1
        centroid = _to_vec(cluster.centroid)
        new_centroid = _vec_scale(_vec_add(_vec_scale(centroid, old_n), vector), Decimal(1) / new_n)
        delta = _vec_sub(new_centroid, centroid)
        # 平移中心后成员平方距离和更新：ss_new = ss_old + n_old*|delta|^2 + |v - c_new|^2
        ss = self._ss[cluster.cluster_id]
        ss_new = ss + old_n * _norm2(delta) + _norm2(_vec_sub(vector, new_centroid))
        dispersion = (ss_new / new_n).sqrt()

        cluster.occurrence_count = int(new_n)
        cluster.centroid = [fmt(x) for x in new_centroid]
        cluster.dispersion = fmt(dispersion)
        cluster.member_trace_ids = [*cluster.member_trace_ids, trace.trace_id]
        self._ss[cluster.cluster_id] = ss_new
        return cluster