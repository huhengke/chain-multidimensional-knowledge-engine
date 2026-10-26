"""两套 Agent（Python 与 Harness）语义槽位逐条对齐的回归测试。

Harness 是 Node 侧实现，这里用同一份问题表跑两边，比较数据集路由
（点名/范围口径/同环比）、条件、基座候选与分组/明细判定，以及无锚点文本
的澄清判定（unresolved）与澄清文案是否同串；
任何一侧单独改动语义都会在这里红掉。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from agent.python_agent.skills import semantic_slots
from agent.python_agent.skills.semantic_slots import (
    CLARIFY_ANSWER,
    extract_slots,
)

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "agent" / "harness"

ENGINE_SNAPSHOT = {
    "油品销售月报": "oil_monthly_sales",
    "油品销售": "oil_monthly_sales",
    "油品销售日报": "oil_sales_daily",
    "油品销售计划": "oil_sales_plan",
    "非油品销售明细": "non_oil_sales",
    "非油品": "non_oil_sales",
    # 向导注册的数据集靠别名被业务方点名，别名与中文名同等有效
    "线上商城": "non_oil_sales",
}
DAY_GRAINED = {
    "non_oil_sales": "非油品销售明细",
    "oil_sales_daily": "油品销售日报",
}

QUESTIONS = [
    "2024年总销售金额是多少？",
    "2024年各月销量如何？",
    "2026年4月第三周油品销售金额是多少？",
    "2026年4月第一周老城站销量",
    "2026年4月1日油品销售金额是多少？",
    "上周各分公司的油品销售量",
    "2024年老城站的销售记录有哪些？",
    "2024年海口分公司的油品销售金额是多少？",
    "三亚分公司92号汽油销售额",
    "2025年澄迈片区非油品销售金额",
    "2025年移动支付销售金额是多少？",
    "2025年现金支付方式的销售金额",
    "2024年1月海口分公司用移动支付的销售记录有哪些？",
    "查询2025年92号汽油销售数量",
    "2026年8月油品日均销量是多少？",
    "8月欠进度怎么样？",
    "2026年8月海口分公司油品欠进度",
    "各分公司本月日均销量对比",
    "本月完成率如何？",
    "2026年9月油品销售日报的日均销量是多少？",
    "2026年4月13日到4月19日油品销售日报的日均销量",
    "2026年9月非油品销售明细的日均销量是多少？",
    "去年油品销售金额是多少？",
    "今年油品日均销量是多少？",
    "去年8月海口分公司的销售金额是多少？",
    "2025年非油品老城站的销售记录有哪些？",
    "2025年线上商城的销售金额是多少？",
    "柴油、汽油、合计的销售金额是多少？",
    "2024年销售金额同比增长多少？",
    "2025年油品销售计划是多少？",
    "2026年8月油品销售计划和实际完成情况？",
    # 产品语义只下传基座原语：油号/品类都不再是自己拼的层级条件
    "2024年93号汽油的销售金额是多少？",
    "2026年8月0号柴油的销售量？",
    "2024年汽油的销售金额是多少？",
    # 无锚点文本：两套都必须判成 unresolved，交给编排层澄清而不是查全量
    "哈哈哈",
    "今天天气怎么样",
    "asdfghjkl",
]

_HARNESS_SCRIPT = """
import { extractSlots } from './src/semantic.js';
const labels = %s;
const dayGrained = %s;
const questions = JSON.parse(process.env.PARITY_QUESTIONS);
const pick = (obj) => obj && Object.keys(obj).length ? obj : null;
console.log(JSON.stringify(questions.map((q) => {
  const r = extractSlots(q, labels, dayGrained) || {};
  return {
    question: q,
    intent: r.intent ?? null,
    actual_dataset: r.actual_dataset ?? null,
    dataset: r.dataset ?? null,
    datasets: r.datasets?.length ? r.datasets : null,
    dataset_scope: r.dataset_scope ?? null,
    comparisons: r.comparisons?.length ? r.comparisons : null,
    conditions: pick(r.conditions),
    base_filters: pick(r.base_filters),
    group_by: r.group_by ?? null,
    detail: r.detail ?? null,
    derived_metrics: r.derived_metrics ?? [],
    period_defaulted: r.period_defaulted ?? false,
    actual_dataset: r.actual_dataset ?? null,
    unresolved: Boolean(r.unresolved),
  };
})));
"""


@contextmanager
def registry_snapshot():
    saved = dict(semantic_slots._LABELS_CACHE)
    semantic_slots._LABELS_CACHE.update(
        at=time.monotonic(),
        map=dict(ENGINE_SNAPSHOT),
        by_id=semantic_slots._ids_by_label(ENGINE_SNAPSHOT),
        day_grained=dict(DAY_GRAINED),
    )
    try:
        yield
    finally:
        semantic_slots._LABELS_CACHE.clear()
        semantic_slots._LABELS_CACHE.update(saved)


def harness_slots(questions: list[str]) -> dict[str, dict]:
    if not shutil.which("node"):
        raise unittest.SkipTest("需要 node 才能对比 Harness 语义")
    script = _HARNESS_SCRIPT % (
        json.dumps(ENGINE_SNAPSHOT, ensure_ascii=False),
        json.dumps(DAY_GRAINED, ensure_ascii=False),
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(HARNESS),
        env={**os.environ, "PARITY_QUESTIONS": json.dumps(questions, ensure_ascii=False)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError("Harness 语义脚本执行失败：%s" % proc.stderr[:400])
    return {item["question"]: item for item in json.loads(proc.stdout)}


class AgentParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = harness_slots(QUESTIONS)

    def python_slot(self, question: str) -> dict:
        result = extract_slots(question)
        return {
            "question": question,
            "intent": result.get("intent"),
            "actual_dataset": result.get("actual_dataset") or None,
            "dataset": result.get("dataset") or None,
            "datasets": list(result.get("datasets") or []) or None,
            "dataset_scope": result.get("dataset_scope") or None,
            "comparisons": list(result.get("comparisons") or []) or None,
            "conditions": result.get("conditions") or None,
            "base_filters": result.get("base_filters") or None,
            "group_by": result.get("group_by"),
            "detail": bool(result.get("detail")),
            "derived_metrics": list(result.get("derived_metrics") or []),
            "period_defaulted": bool(result.get("period_defaulted")),
            "unresolved": bool(result.get("unresolved")),
        }

    def test_slots_match_between_agents(self):
        with registry_snapshot():
            for question in QUESTIONS:
                with self.subTest(question=question):
                    self.assertEqual(
                        self.python_slot(question), self.harness[question]
                    )

    def test_clarify_answer_is_one_and_the_same_string(self):
        """澄清文案按字节比对：两套 Agent 的答案必须逐字一致。"""
        if not shutil.which("node"):
            self.skipTest("需要 node 才能读取 Harness 文案")
        proc = subprocess.run(
            ["node", "--input-type=module", "-e",
             "import { CLARIFY_ANSWER } from './src/semantic.js';"
             "process.stdout.write(CLARIFY_ANSWER);"],
            cwd=str(HARNESS), capture_output=True, text=True,
            encoding="utf-8", timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertEqual(CLARIFY_ANSWER, proc.stdout)


if __name__ == "__main__":
    unittest.main()
