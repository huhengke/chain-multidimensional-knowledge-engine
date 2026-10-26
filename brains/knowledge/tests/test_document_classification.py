import pytest

from brains.knowledge import KnowledgeBrain, KnowledgeInput, KnowledgeSource
from brains.knowledge.src.classification import classify_document, normalized, term_present
from brains.knowledge.src.classification import aggregate_subject_identity
from topology.src.trace import contains_date


@pytest.mark.parametrize('title,text,domain', [
    ('中医基础理论笔记', '阐释宇宙万物形成本原。阴阳温度举例。保养生命，延年益寿。', 'medical'),
    ('中醫基礎理論', '醫藥、經脈、臟腑與針灸。', 'medical'),
    ('扁鵲鏡經', '內經與經脈。', 'medical'),
    ('機械設備維護', '電機軸承的檢修與潤滑。', 'equipment'),
    ('勞動合同', '違約訴訟與賠償。', 'legal'),
    ('药典凡例', '温度与压力要求，产品质量控制，法律法规。', 'medical'),
    ('设备维护手册', '机械设备的电机、轴承、润滑保养及运行温度。', 'equipment'),
    ('财务成本核算', '会计预算、折旧与费用结算。', 'finance'),
    ('劳动合同', '甲方乙方签约，违约赔偿责任。', 'legal'),
    ('消防应急预案', '消防防护、隐患处置与风险控制。', 'safety'),
    ('质量管理规范', '质检、抽样和不合格品处理。', 'quality'),
    ('易林注', '周易卦辞与占筮。偶有治病养生的说法。', 'humanities'),
    ('未命名资料', '阐释宇宙万物形成本原。保养生命。温度10℃。费用若干。', 'general'),
    ('领域综合资料', '中医 经络 针灸 设备 电机 轴承', 'general'),
])
def test_document_subject(title, text, domain):
    assert classify_document(title, text)['domain'] == domain


def test_false_substring_and_traditional_are_only_normalized_for_matching():
    assert not term_present('万物形成本原', '成本')
    assert term_present('采购成本下降', '成本')
    assert normalized('醫 療\n經 脈') == '医疗经脉'


def raw(override=None):
    return KnowledgeInput(raw_id='medical', source=KnowledgeSource(source_id='s1', kind='file'),
        title='中医基础理论笔记.pdf', content='# 精气学说\n宇宙万物形成本原。\n\n# 阴阳\n温度举例，保养生命。',
        version='original-v1', permission_scope=['owner'], metadata={'classification_override': override})


def test_document_owns_one_tree_and_reclassification_preserves_evidence():
    before = KnowledgeBrain()
    doc = before.ingest(raw())
    assert set(before.store.unit_tree.values()) == {'knowledge.medical'}
    assert all(u.concept_path[0] == '医疗' for u in before.store.document_units(doc.document_id))
    after = KnowledgeBrain()
    new = after.ingest(raw({'domain': 'humanities', 'reason': '验证纠偏'}))
    assert doc.document_id == new.document_id
    assert set(before.store.units) == set(after.store.units)
    assert set(before.store.evidence) == set(after.store.evidence)
    assert before.store.unit_text == after.store.unit_text
    assert set(after.store.unit_tree.values()) == {'knowledge.humanities'}
    assert after.store.publications[new.document_id].status == 'pending_review'
    assert any('医疗' in g.reason for g in after.store.gates.values())


def test_cross_domain_clue_does_not_split_medical_document():
    brain = KnowledgeBrain()
    doc = brain.ingest(raw().model_copy(update={'content': '# 医疗法规\n医疗患者的法律与赔偿责任。\n\n# 养生\n保养生命。'}))
    assert set(brain.store.unit_tree.values()) == {'knowledge.medical'}
    assert any('legal' in a.related_domains for a in brain.store.unit_annotation.values())
    assert brain.store.document_classification[doc.document_id]['domain'] == 'medical'


def test_book_numbers_and_dates_are_not_aggregation_identity():
    a = ['人文典籍', '古籍通释-1984-11427627']
    b = ['人文典籍', '古籍通释-2025-13246602']
    assert aggregate_subject_identity(a) == aggregate_subject_identity(b)
    brain = KnowledgeBrain()
    brain.ingest(raw().model_copy(update={'title': '古籍通释-1984-11427627.pdf', 'content': '真人修道与道教思想。'}))
    assert brain.store.traces
    assert all(not contains_date(t.aggregate_key) for t in brain.store.traces.values())
