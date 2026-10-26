"""Explicit installation command; model downloading never occurs during a query."""
import argparse
import hashlib
import json

from platform_server.installation import configure
from platform_server.local_models import EMBEDDING, RERANK, PREFIX, file_digest, local_service_token
from platform_server.pdf_extraction import ocr_engine
from platform_server.store import Store


def prepare(port):
    from fastembed import TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    installation = configure()
    root = installation / 'models'
    root.mkdir(parents=True, exist_ok=True)
    token = local_service_token(root, create=True)
    store = Store(installation / 'platform')
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    for purpose, model, cls in [('embedding', EMBEDDING, TextEmbedding), ('rerank', RERANK, TextCrossEncoder)]:
        active = next((m for m in store.list('model', 'system') if m['brain']=='knowledge' and m['purpose']==purpose and m['enabled']), None)
        if active and not active.get('managed_local'):
            print(f'{purpose}: keeping the configured provider')
            continue
        if purpose == 'embedding' and not active and any(a['brain']=='knowledge' and a['status'] in {'ready','partial','pending_review','rejected'} for a in store.list('asset')):
            raise ValueError('已有知识索引但没有活动向量配置，请先受控迁移；安装命令不会直接替换向量空间')
        if model not in manifest:
            runtime = cls(model, cache_dir=str(root / 'fastembed'), threads=4)
            folder = runtime.model._model_dir.resolve()
            files = {str(file.relative_to(folder)).replace('\\', '/'): file_digest(file)
                     for file in sorted(folder.rglob('*')) if file.is_file() and '.cache' not in file.parts}
            revision = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
            manifest[model] = {'path': str(folder.relative_to(root.resolve())), 'files': files, 'revision': revision}
            # Atomic replacement prevents a partial manifest on interruption.
            staged = manifest_path.with_suffix('.pending')
            staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            staged.replace(manifest_path)
        if active:
            if active.get('provider_revision') != manifest[model]['revision']:
                raise ValueError('已配置模型版本与本地文件不一致，请先进行受控索引迁移')
            if not active.get('secret_ref') or store.reveal(active['secret_ref']) != token:
                active['secret_ref'] = store.secret(token)
                store.put('model', 'system', active, action='model.rotate_local_credential')
            continue
        store.put('model', 'system', {
            'id': 'model-local-knowledge-' + purpose,
            'name': '本地中文向量 BGE-small-zh' if purpose == 'embedding' else '本地中文重排 BGE-reranker',
            'brain': 'knowledge', 'purpose': purpose, 'model': model,
            'base_url': f'http://127.0.0.1:{port}' + PREFIX,
            'secret_ref': store.secret(token),
            'enabled': True, 'supports_images': False, 'timeout': 90,
            'managed_local': True, 'provider_revision': manifest[model]['revision'],
        }, action='model.install_local')
        print(f'{purpose}: installed {model}')
    ocr_engine(root / 'ocr', prepare=True)
    print('OCR models ready. Restart the platform service, then retry failed imports.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8079)
    prepare(parser.parse_args().port)
