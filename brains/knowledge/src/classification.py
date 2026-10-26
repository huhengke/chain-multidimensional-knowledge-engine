"""Versioned document subject classification, separate from evidence/publication risk.

Scores express rule support, not calibrated probabilities. Ambiguous incidental
words never establish a document's subject. Original text/locators stay unchanged.
"""
from __future__ import annotations

import re
import hashlib
import unicodedata
from collections import Counter

CLASSIFICATION_VERSION = "document-subject-v2"
DOMAIN_NAMES = {"medical": "医疗", "equipment": "设备", "finance": "财务",
                "legal": "法律", "quality": "质量", "safety": "安全",
                "humanities": "人文典籍", "general": "待归类"}

# Normalize only the classification view, never the evidence text.
_TRADITIONAL = str.maketrans(dict(zip(
    "醫藥經脈臟腑針灸傷寒雜病論診斷處劑療養氣陰陽學說證體虛實熱溫壓護設備維檢潤裝財務預潤費結稅責違訟權賠質驗險應規條約術傳統辭軍戰卦師書", 
    "医药经脉脏腑针灸伤寒杂病论诊断处剂疗养气阴阳学说证体虚实热温压护设备维检润装财务预润费结税责违讼权赔质验险应规条约术传统辞军战卦师书")))
_TRADITIONAL.update(str.maketrans({"訴": "诉", "償": "偿", "鏡": "镜", "鵲": "鹊", "內": "内",
    "勞": "劳", "動": "动", "機": "机", "軸": "轴", "產": "产", "國": "国", "廠": "厂",
    "調": "调", "攝": "摄", "臨": "临", "牀": "床", "補": "补", "腎": "肾"}))

ANCHORS = {
    "medical": ("医疗", "中医", "医药", "药典", "医镜", "医经", "内经", "扁鹊", "伤寒", "本草", "方剂", "针灸", "经络", "经脉", "脏腑", "病机", "辨证", "诊断", "临床", "患者", "处方", "用药", "剂量", "禁忌", "养生", "补肾", "治病", "疾病"),
    "equipment": ("设备", "机械", "机组", "泵站", "电机", "轴承", "检修", "润滑", "装配", "转速", "零部件"),
    "finance": ("财务", "会计", "预算", "利润", "结算", "折旧", "税率", "报销", "现金流", "资产负债", "采购成本"),
    "legal": ("法律", "法规", "法条", "违约", "诉讼", "判决", "裁决", "赔偿", "甲方", "乙方", "劳动合同", "合同法", "民法"),
    "quality": ("质量管理", "质量控制", "产品质量", "质检", "抽样", "出厂", "检验报告", "不合格品"),
    "safety": ("生产安全", "安全生产", "事故", "隐患", "防护", "操作规程", "应急预案", "风险控制", "消防", "应急处置"),
    "humanities": ("道教", "道藏", "天师", "真人", "修道", "儒家", "佛教", "易学", "易林", "周易", "卦辞", "爻辞", "占筮", "星占", "兵书", "兵法", "兵垣", "军政", "战阵"),
}

TOPICS = {
    "medical": {
        "中医基础": ("阴阳", "五行", "精气", "元气", "脏腑", "气血", "津液"),
        "辨证诊断": ("辨证", "诊断", "脉象", "舌苔", "病机", "证候"),
        "本草方药": ("本草", "方剂", "处方", "用药", "药物", "剂量", "药典", "药材"),
        "经络针灸": ("经络", "经脉", "针灸", "穴位", "腧穴", "督脉"),
        "养生防治": ("养生", "保养生命", "防病", "延年", "调摄", "保精"),
    },
    "equipment": {"运行参数": ("温度", "压力", "转速"), "维护检修": ("维护", "检修", "保养", "润滑")},
    "finance": {"成本费用": ("成本", "费用", "报销"), "财务核算": ("会计", "结算", "折旧", "利润", "税率")},
    "humanities": {"道教思想": ("道教", "天师", "真人", "修道"), "易学术数": ("周易", "易林", "卦", "爻", "占筮", "星占"), "兵学": ("兵法", "兵书", "军政", "战阵", "兵垣")},
}


def normalized(text: str) -> str:
    # OCR frequently separates every Chinese character with spaces/newlines.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "").translate(_TRADITIONAL)).lower()


def term_present(text: str, term: str, *, normalized_text: bool = False) -> bool:
    text, term = text if normalized_text else normalized(text), normalized(term)
    if term == "成本":
        text = re.sub(r"形成(?:本原|本源|本体)|构成本(?:身|体)", "", text)
    if term in {"保养", "维护", "温度", "压力"}:
        # These remain useful topics inside an equipment document, but don't
        # provide domain evidence on their own (see ANCHORS).
        return term in text
    return term in text


def classify_document(title: str, text: str, *, override: dict | None = None) -> dict:
    body = normalized(text)
    # Explicit Markdown headings contribute context even when the filename is generic.
    headings = " ".join(re.findall(r"(?m)^#{1,6}\s+(.{1,100})$", text or "")[:30])
    title_text = normalized(title + " " + headings)
    candidates = []
    for domain, anchors in ANCHORS.items():
        title_hits = [word for word in anchors if normalized(word) in title_text]
        counts = {word: body.count(normalized(word)) for word in anchors if normalized(word) in body}
        # Repeated weak prose cannot outvote several distinctive subject terms.
        score = len(title_hits) * 12 + sum(min(5, count) for count in counts.values())
        eligible = bool(title_hits) or len(counts) >= 2 or max(counts.values(), default=0) >= 3
        if score:
            candidates.append({"domain": domain, "label": DOMAIN_NAMES[domain], "score": score,
                               "title_terms": title_hits, "body_terms": list(counts), "eligible": eligible})
    candidates.sort(key=lambda item: (-bool(item["title_terms"]), -item["score"], item["domain"]))
    eligible = [item for item in candidates if item["eligible"]]
    first = eligible[0] if eligible else None
    second = eligible[1] if len(eligible) > 1 else None
    clear_title = first and first["title_terms"] and not (second and second["title_terms"])
    clear_body = first and (not second or first["score"] >= second["score"] * 1.6)
    confident = bool(first and (clear_title or clear_body))
    domain = first["domain"] if confident else "general"
    result = {"version": CLASSIFICATION_VERSION, "domain": domain, "label": DOMAIN_NAMES[domain],
              "status": "automatic" if confident else "needs_classification", "method": "document-context-rules",
              "confidence": "0.9" if clear_title else "0.75" if confident else "0.3",
              "basis": (["标题/章节主题：" + "、".join(first["title_terms"])] if clear_title else []) +
                       (["全文领域依据：" + "、".join(first["body_terms"][:12])] if first else []) +
                       ([] if confident else ["领域依据不足或多领域接近，保留待归类"]),
              "candidates": candidates, "related_domains": [i["domain"] for i in eligible if i["domain"] != domain]}
    if override:
        requested = override.get("domain")
        if requested not in DOMAIN_NAMES:
            raise ValueError("不支持的知识主领域")
        result.update(domain=requested, label=DOMAIN_NAMES[requested], status="manual",
                      override=dict(override), automatic_domain=domain,
                      related_domains=[i['domain'] for i in eligible if i['domain'] != requested])
    return result


def topic_path(domain: str, text: str, headings: list[str], title: str) -> list[str]:
    body = normalized(text)
    topics = TOPICS.get(domain, {})
    counts = Counter({label: sum(normalized(term) in body for term in terms) for label, terms in topics.items()})
    if counts and counts.most_common(1)[0][1]:
        return [counts.most_common(1)[0][0]]
    # A meaningful chapter is a better fallback than an incidental foreign-domain word.
    chapter = next((h.strip() for h in reversed(headings) if h.strip() and normalized(h) != normalized(title)), "")
    if chapter:
        return [chapter[:80]]
    return [re.sub(r"\.(pdf|txt|md|docx|csv|json)$", "", title or "未命名资料", flags=re.I)[:80]]


def aggregate_subject_identity(path: list[str]) -> str:
    # Display labels may contain publication years or scanned book identifiers.
    # Dates belong to occurrence edges, not the aggregation identity.
    semantic = re.sub(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{8,}|(?<!\d)(?:19|20)\d{2}(?!\d)', '', '/'.join(path))
    digest = hashlib.sha256(normalized(semantic).encode('utf-8')).hexdigest()
    # Group the opaque digest so numeric runs cannot resemble a date either.
    return '.'.join(digest[i:i+4] for i in range(0, len(digest), 4))
