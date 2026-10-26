"""Page-preserving PDF extraction with durable, owner-scoped OCR checkpoints."""
from __future__ import annotations

import hashlib
import io
import json
import os
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from pypdf import PdfReader

from platform_server.text_safety import sanitize_unicode_text

OCR_FILES = (
    'ch_PP-OCRv4_det_mobile.onnx',
    'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
    'ch_PP-OCRv5_rec_server.onnx',
)
PARSER_VERSION = 'pdf-pages-v2-rapidocr381-v5server-2.4x-reading-order-nul-guard'
_engines = threading.local()


def usable_text_layer(text: str) -> tuple[str, bool]:
    """Strip control characters and decide whether a page has a real text layer.

    Scanned PDFs often expose a tiny amount of junk text plus thousands of NUL
    code points.  Counting only ``strip()`` length treats that layer as valid
    and skips OCR, persisting an index built from NULs.  A usable layer must
    contain at least 24 non-space characters and be predominantly printable.
    """
    # Keep isolated surrogates so the later sanitize pass can replace them
    # with U+FFFD and report the count instead of silently dropping data.
    cleaned = ''.join(ch for ch in text if ch in '\n\r\t'
                      or ch.isprintable() or 0xD800 <= ord(ch) <= 0xDFFF)
    compact = ''.join(cleaned.split())
    raw_compact = ''.join(text.split())
    if len(compact) < 24:
        return cleaned, False
    if raw_compact and len(compact) / len(raw_compact) < 0.5:
        return cleaned, False
    return cleaned, True


def ocr_engine(model_root: Path, *, prepare=False):
    from rapidocr import RapidOCR, OCRVersion, ModelType
    model_root.mkdir(parents=True, exist_ok=True)
    if not prepare and not all((model_root / name).is_file() for name in OCR_FILES):
        raise ValueError('本地 OCR 模型未就绪，请运行 scripts/setup-knowledge-local.ps1；原件已保留')
    return RapidOCR(params={
        'Global.model_root_dir': str(model_root),
        'Global.log_level': 'warning',
        'Rec.ocr_version': OCRVersion.PPOCRV5,
        'Rec.model_type': ModelType.SERVER,
        'EngineConfig.onnxruntime.intra_op_num_threads': 2,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
    })


def reading_order(lines):
    """Vertical Chinese columns read right-to-left, with fragments top-to-bottom."""
    if not lines:
        return [], 'empty'
    def bounds(line):
        box = line['box']
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        return min(xs), min(ys), max(xs), max(ys)
    tall = [line for line in lines if (bounds(line)[3] - bounds(line)[1]) > 2 * (bounds(line)[2] - bounds(line)[0])]
    if len(tall) < max(2, len(lines) * .45):
        return lines, 'horizontal'
    widths = [bounds(line)[2] - bounds(line)[0] for line in tall]
    tolerance = max(4, statistics.median(widths) * .65)
    columns = []
    for line in sorted(lines, key=lambda x: -(bounds(x)[0] + bounds(x)[2]) / 2):
        x = (bounds(line)[0] + bounds(line)[2]) / 2
        if not columns or abs(columns[-1][0] - x) > tolerance:
            columns.append((x, [line]))
        else:
            columns[-1][1].append(line)
    return [line for _, col in columns for line in sorted(col, key=lambda x: bounds(x)[1])], 'vertical-rtl'


def recognize_page(image, model_root):
    import numpy as np
    key = str(model_root)
    if getattr(_engines, 'key', None) != key:
        _engines.engine, _engines.key = ocr_engine(model_root), key
    result = _engines.engine(image)
    lines = [{'text': text, 'confidence': round(float(score), 5), 'box': box.tolist()}
             for box, text, score in zip(result.boxes if result.boxes is not None else [], result.txts or [], result.scores or [])]
    lines, order = reading_order(lines)
    text = '\n'.join(line['text'] for line in lines)
    weight = sum(len(line['text']) for line in lines)
    confidence = sum(line['confidence'] * len(line['text']) for line in lines) / weight if weight else None
    # A non-white page with no recognized text is reported, never silently dropped.
    ink_fraction = float(np.mean(np.min(image[:, :, :3], axis=2) < 180))
    return {'text': text, 'method': 'ocr', 'reading_order': order, 'lines': lines,
            'mean_confidence': round(confidence, 4) if confidence is not None else None,
            'unresolved': not text.strip() and ink_fraction > .005,
            'low_confidence_lines': sum(line['confidence'] < .8 for line in lines)}


def extract_pdf(store, owner, digest, data, progress=None):
    import numpy as np
    import pypdfium2 as pdfium
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError('PDF 已加密，请先提供可读取的文件；原件已保留')
    total = len(reader.pages)
    if total > 500:
        raise ValueError('单次 PDF 导入最多 500 页，请拆分文档')
    model_root = store.root.parent / 'models' / 'ocr'
    model_hash = hashlib.sha256()
    for name in OCR_FILES:
        file = model_root / name
        if file.exists():
            with file.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    model_hash.update(block)
    version = PARSER_VERSION + ':' + model_hash.hexdigest()[:20]
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS pdf_page_cache(owner TEXT, digest TEXT, version TEXT, page INTEGER, data TEXT, PRIMARY KEY(owner,digest,version,page))')
        cached = {r['page']: json.loads(r['data']) for r in db.execute('SELECT page,data FROM pdf_page_cache WHERE owner=? AND digest=? AND version=?', (owner, digest, version))}
    pages = {}
    unicode_replacements = sum(int(value.get('unicode_replacements', 0) or 0) for value in cached.values())
    def complete(page, value, *, cache=True):
        nonlocal unicode_replacements
        value = dict(value)
        value['text'], replaced_count = sanitize_unicode_text(value.get('text', ''))
        unicode_replacements += replaced_count
        if replaced_count:
            value['unicode_replacements'] = replaced_count
        pages[page] = value
        if cache:
            with store.connect() as db:
                db.execute('INSERT OR REPLACE INTO pdf_page_cache VALUES(?,?,?,?,?)', (owner, digest, version, page, json.dumps(value, ensure_ascii=False)))
        if progress:
            progress({'stage': 'pdf_extract', 'completed': len(pages), 'total': total,
                      'message': f'PDF 解析 / OCR：已处理 {len(pages)} / {total} 页（保留原页码）'})
    document = pdfium.PdfDocument(data)
    pending = {}
    try:
        workers = min(4, max(1, (os.cpu_count() or 4) // 4))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='knowledge-ocr') as pool:
            def drain():
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    page = pending.pop(future)
                    complete(page, future.result())
            for i, source in enumerate(reader.pages):
                page_number = i + 1
                if page_number in cached:
                    complete(page_number, cached[page_number], cache=False)
                    continue
                text = source.extract_text() or ''
                clean, usable = usable_text_layer(text)
                if usable:
                    complete(page_number, {'text': clean, 'method': 'text_layer', 'reading_order': 'native', 'unresolved': False})
                    continue
                # PDFium objects only live on this thread; workers receive copied pixels.
                page = document[i]
                bitmap = page.render(scale=min(2.4, 2400 / max(page.get_size())))
                image = np.asarray(bitmap.to_pil().convert('RGB')).copy()
                bitmap.close()
                page.close()
                pending[pool.submit(recognize_page, image, model_root)] = page_number
                if len(pending) >= workers * 2:
                    drain()
            while pending:
                drain()
    finally:
        document.close()
    text, final_replacements = sanitize_unicode_text('\f'.join(pages[i]['text'] for i in range(1, total + 1)))
    unicode_replacements += final_replacements
    if not text.strip():
        raise ValueError('PDF 逐页解析和 OCR 后仍无可识别文字；请检查扫描清晰度，原件已保留')
    summary = [{'page': i, 'characters': len(pages[i]['text']), **{k: v for k, v in pages[i].items() if k not in {'text', 'lines'}}} for i in range(1, total + 1)]
    return text, {'parser_version': version, 'page_count': total,
                  'ocr_pages': sum(p['method'] == 'ocr' for p in pages.values()),
                  'unicode_replacements': unicode_replacements,
                  'unresolved_pages': sorted(i for i, p in pages.items() if p.get('unresolved')),
                  'pages': summary}
