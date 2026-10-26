"""Regression contracts for mixed PDFs, OCR checkpoints and original page anchors."""
import hashlib
import io
import json
import sys
import time
import types

import pytest
from pypdf import PdfWriter
from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

from platform_server.store import Store
from platform_server.pdf_extraction import extract_pdf, reading_order
from platform_server.text_safety import sanitize_unicode_text


def mixed_pdf():
    writer = PdfWriter()
    for i in range(3):
        page = writer.add_blank_page(width=200 + i, height=300)
        if i == 0:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 10 Tf 10 280 Td (Native text must remain on original page one.) Tj ET')
            page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_mixed_pdf_keeps_pages_and_reuses_owner_scoped_checkpoints(tmp_path, monkeypatch):
    data = mixed_pdf()
    digest = hashlib.sha256(data).hexdigest()
    store = Store(tmp_path / 'platform')
    calls = []
    def recognize(image, root):
        calls.append(image.shape[1])
        return {'text': f'OCR width {image.shape[1]}', 'method': 'ocr', 'unresolved': False}
    monkeypatch.setattr('platform_server.pdf_extraction.recognize_page', recognize)
    progress = []
    text, meta = extract_pdf(store, 'alice', digest, data, progress.append)
    assert text.split('\f')[0].startswith('Native text')
    assert len(text.split('\f')) == meta['page_count'] == 3
    assert meta['ocr_pages'] == 2 and len(calls) == 2
    assert [p['page'] for p in meta['pages']] == [1, 2, 3]
    assert progress[-1]['completed'] == progress[-1]['total'] == 3
    assert extract_pdf(store, 'alice', digest, data)[0] == text
    assert len(calls) == 2
    extract_pdf(store, 'bob', digest, data)
    assert len(calls) == 4


def test_interrupted_ocr_keeps_completed_pages_and_reports_empty_scan(tmp_path, monkeypatch):
    data = mixed_pdf()
    digest = hashlib.sha256(data).hexdigest()
    store = Store(tmp_path / 'platform')
    def failed(image, root):
        raise ValueError('OCR worker unavailable')
    monkeypatch.setattr('platform_server.pdf_extraction.recognize_page', failed)
    with pytest.raises(ValueError, match='worker unavailable'):
        extract_pdf(store, 'alice', digest, data)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM pdf_page_cache').fetchone()[0] == 1
    monkeypatch.setattr('platform_server.pdf_extraction.recognize_page', lambda *_: {'text': '', 'method': 'ocr', 'unresolved': True})
    text, meta = extract_pdf(store, 'alice', digest, data)
    assert meta['unresolved_pages'] == [2, 3]
    assert text.count('\f') == 2  # Empty scans must not shift later citations.


def test_pdf_surrogate_text_is_replaced_before_utf8_cache(tmp_path, monkeypatch):
    class Page:
        def extract_text(self):
            return '古籍正文中的异常字体映射：\udcfe；后续文字仍然需要进入知识索引。'

    class Reader:
        is_encrypted = False
        pages = [Page()]

    class PdfDocument:
        def __init__(self, _data):
            pass

        def close(self):
            pass

    monkeypatch.setattr('platform_server.pdf_extraction.PdfReader', lambda _stream: Reader())
    monkeypatch.setitem(sys.modules, 'pypdfium2', types.SimpleNamespace(PdfDocument=PdfDocument))
    store = Store(tmp_path / 'platform')

    text, meta = extract_pdf(store, 'alice', 'a' * 64, b'fake-pdf')

    assert '\udcfe' not in text
    assert '�' in text
    assert meta['unicode_replacements'] == 1
    with store.connect() as db:
        cached = json.loads(db.execute('SELECT data FROM pdf_page_cache').fetchone()['data'])
    assert '\udcfe' not in cached['text']
    assert cached['unicode_replacements'] == 1


def test_unicode_safety_preserves_valid_surrogate_pairs():
    text, replacements = sanitize_unicode_text('\ud83d\ude00')
    assert text == '😀'
    assert replacements == 0


def test_vertical_columns_keep_right_to_left_and_fragment_order():
    def line(text, x, y, height):
        return {'text': text, 'box': [[x,y],[x+10,y],[x+10,y+height],[x,y+height]]}
    lines = [line('left', 10, 0, 100), line('right-bottom', 101, 60, 40), line('right-top', 100, 0, 40)]
    ordered, orientation = reading_order(lines)
    assert orientation == 'vertical-rtl'
    assert [x['text'] for x in ordered] == ['right-top', 'right-bottom', 'left']


def test_usable_text_layer_rejects_nul_rich_scanned_pages():
    from platform_server.pdf_extraction import usable_text_layer
    cleaned, usable = usable_text_layer('\x00' * 5000 + '短序')
    assert usable is False
    assert '\x00' not in cleaned
    assert cleaned.endswith('短序')


def test_usable_text_layer_accepts_real_chinese_text():
    from platform_server.pdf_extraction import usable_text_layer
    cleaned, usable = usable_text_layer('少阳之为病，口苦，咽干，目眩也。\n\n本条论述少阳病的提纲证候。')
    assert usable is True
    assert cleaned.startswith('少阳之为病')


def test_usable_text_layer_rejects_sparse_cover_text():
    from platform_server.pdf_extraction import usable_text_layer
    cleaned, usable = usable_text_layer('封面 书名')
    assert usable is False
    assert cleaned == '封面 书名'
