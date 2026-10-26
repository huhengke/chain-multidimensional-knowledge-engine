#!/usr/bin/env python3
"""Provision WeKnora Wiki/Graph with the platform default chat model and
reprocess the active knowledge bases end to end.

The run is resumable: provisioning, reparse submission, native chunk remapping
and coverage validation each record their state in a JSON report.  Re-running
the same command continues where the previous run stopped.

Safety rules enforced here:
  * only the platform's default (or Codex) chat model may synthesise Wiki;
  * only knowledge bases created by this deployment are touched;
  * a native reparse invalidates native chunk ids, so the chunk -> canonical
    unit mapping is rebuilt before validation;
  * every active knowledge id must appear in at least one Wiki page
    ``source_refs`` entry, and the link graph must load without truncation.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from platform_server.store import Store
from platform_server.weknora_index import (
    WIKI_CONFIG,
    SidecarUnavailable,
    WeKnoraIndex,
    _chat_model_config,
    sidecar_for,
)

STORE_ROOT = ROOT / '.runtime/standalone/platform'
DEFAULT_REPORT = ROOT / '.runtime/wiki_rebuild_report.json'
TERMINAL_STATUSES = {'completed', 'failed', 'cancelled', 'deleting'}
FAILED_STATUSES = {'failed', 'cancelled', 'deleting'}
GRAPH_LIMIT = 2000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def log(message: str) -> None:
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {message}', flush=True)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(prefix='.wiki-report-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def item_updated_at(item: dict | None) -> str:
    return str((item or {}).get('updated_at') or '')


class WikiRebuild:
    def __init__(self, store: Store, owner: str, report_path: Path):
        self.store = store
        self.owner = owner
        self.report_path = report_path
        self.sidecar = sidecar_for(store)
        self.index = WeKnoraIndex(store, owner, sidecar=self.sidecar)
        self.report = load_json(report_path) or self._new_report()
        self.trees: dict[str, dict] = {}
        self.embedding_id = ''
        self.wiki_model_id = ''
        self.model_row: dict = {}

    def _new_report(self) -> dict:
        return {
            'schema': 1,
            'owner': self.owner,
            'created_at': now_iso(),
            'updated_at': now_iso(),
            'active_revision': self.index.active_revision(),
            'model': {},
            'trees': {},
            'last_action': '',
            'summary': {},
        }

    def save(self) -> None:
        self.report['updated_at'] = now_iso()
        self.report['active_revision'] = self.index.active_revision()
        write_json(self.report_path, self.report)

    # ---- guards and discovery -------------------------------------------
    def guard_model(self) -> None:
        config = _chat_model_config(self.store)
        if not config:
            raise SystemExit('没有启用的知识脑默认对话模型；拒绝生成 Wiki。')
        rows = [row for row in self.store.list('model', 'system')
                if row.get('id') == config['id']]
        row = rows[0] if rows else {}
        identity = ' '.join([config.get('base_url', ''), config.get('model', ''),
                             row.get('deployment', '')]).lower()
        if row.get('deployment') != 'codex' or 'deepseek' in identity:
            raise SystemExit(
                f"默认知识脑对话模型不是 Codex（deployment={row.get('deployment')!r}, "
                f"model={config.get('model')!r}）；请在平台模型配置中把 Codex 设为默认。")
        self.model_row = row
        self.report['model'] = {
            'id': config['id'],
            'deployment': row.get('deployment'),
            'model': config['model'],
            'base_url': config['base_url'],
        }
        self.save()
        log(f"Wiki 合成模型：{row.get('deployment')} / {config['model']} / {config['base_url']}")

    def _active_parts(self) -> dict[str, dict]:
        """Read the platform's authoritative active part list per tree."""
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT document_id,trees,kb_ids,parts FROM weknora_documents "
                "WHERE owner=? AND status='ready' ORDER BY document_id",
                (self.owner,)).fetchall()]
        result: dict[str, dict] = {}
        for row in rows:
            try:
                parts = json.loads(row.get('parts') or '[]')
            except ValueError:
                parts = []
            for part in parts:
                tree = part.get('tree') or (row['trees'].split(',')[0] if row['trees'] else '')
                knowledge_id = part.get('knowledge_id')
                if not tree or not knowledge_id:
                    continue
                bucket = result.setdefault(tree, {'knowledge': {}, 'documents': defaultdict(list)})
                bucket['knowledge'][knowledge_id] = {
                    'document_id': row['document_id'],
                    'kb_id': part.get('kb_id'),
                }
                bucket['documents'][row['document_id']].append(knowledge_id)
        return result

    def discover_trees(self, requested: list[str]) -> dict[str, dict]:
        mapping = self.index.tree_knowledge_bases()
        active = self._active_parts()
        available = sorted(set(mapping) & set(active))
        if requested:
            missing = [tree for tree in requested if tree not in available]
            if missing:
                raise SystemExit(f'未找到可用的活动知识树：{", ".join(missing)}；可选：{", ".join(available)}')
            selected = requested
        else:
            selected = available
        if not selected:
            raise SystemExit('没有找到可处理的活动知识树（weknora_documents 为空或索引未同步）。')
        self.trees = {}
        for tree in selected:
            kb = mapping[tree]
            if not str(kb.get('name') or '').startswith('three-brain::'):
                raise SystemExit(f'拒绝处理非本部署知识库：{kb.get("name")}')
            self.trees[tree] = {
                'kb_id': kb['id'],
                'kb_name': kb['name'],
                'knowledge': active[tree]['knowledge'],
                'documents': dict(active[tree]['documents']),
            }
        prior_trees = self.report.get('trees') or {}
        for tree in self.trees:
            prior_trees.setdefault(tree, {})
        self.report['trees'] = prior_trees
        for tree, info in self.trees.items():
            state = self.report['trees'][tree]
            state.update({'knowledge_base_id': info['kb_id'], 'knowledge_base_name': info['kb_name']})
            state.setdefault('phases', {})
            state.setdefault('knowledge', {})
            state.setdefault('validation', None)
        self.save()
        return self.trees

    # ---- provisioning ----------------------------------------------------
    def provision(self) -> None:
        self.embedding_id, _rerank_id = self.sidecar.ensure_models()
        self.wiki_model_id = self.sidecar.ensure_chat_model() or ''
        if not self.wiki_model_id:
            raise SystemExit('WeKnora 未返回 Wiki 合成模型 id；拒绝继续。')
        for tree, info in self.trees.items():
            state = self.report['trees'][tree]
            current = self.sidecar.knowledge_base(info['kb_id'])
            if not current:
                raise SystemExit(f'WeKnora 知识库不存在：{tree} / {info["kb_id"]}')
            if not state.get('original'):
                state['original'] = {
                    'indexing_strategy': current.get('indexing_strategy') or {},
                    'wiki_config': current.get('wiki_config') or {},
                    'chunking_config': current.get('chunking_config') or {},
                }
            kb_id = self.sidecar.ensure_knowledge_base(
                info['kb_name'], self.embedding_id, self.wiki_model_id)
            if kb_id != info['kb_id']:
                raise SystemExit(f'知识库名称解析不一致：{info["kb_name"]} -> {kb_id} != {info["kb_id"]}')
            live = self.sidecar.knowledge_base(kb_id) or {}
            strategy = live.get('indexing_strategy') or {}
            wiki_config = live.get('wiki_config') or {}
            if strategy.get('wiki_enabled') is not True:
                raise SystemExit(f'{tree} 未启用 Wiki')
            if wiki_config.get('synthesis_model_id') != self.wiki_model_id:
                raise SystemExit(f'{tree} 的 Wiki 合成模型不是平台默认模型')
            for key, value in WIKI_CONFIG.items():
                if wiki_config.get(key) != value:
                    raise SystemExit(f'{tree} 的 Wiki 配置 {key} 未生效')
            state['phases']['provisioned_at'] = now_iso()
            state['knowledge_base_config'] = {
                'indexing_strategy': strategy,
                'wiki_config': wiki_config,
                'chunking_config': live.get('chunking_config') or {},
            }
            log(f'{tree} 已启用 Wiki（synthesis={self.wiki_model_id[:8]}…）')
        self.save()

    # ---- reparse submission ---------------------------------------------
    def live_knowledge(self, tree: str) -> dict[str, dict]:
        info = self.trees[tree]
        live = {item['id']: item for item in self.sidecar.list_knowledge(info['kb_id'])}
        missing = sorted(set(info['knowledge']) - set(live))
        if missing:
            raise SystemExit(
                f'{tree} 有 {len(missing)} 个平台活动知识在 WeKnora 中不存在；'
                f'请先运行原生索引同步（示例 id：{missing[:3]}）。')
        deleting = sorted(kid for kid in info['knowledge']
                          if live.get(kid, {}).get('parse_status') == 'deleting')
        if deleting:
            raise SystemExit(f'{tree} 仍有删除中的知识，拒绝重处理：{deleting[:3]}')
        return live

    def _previous_missing(self, tree: str) -> set[str]:
        validation = (self.report['trees'].get(tree) or {}).get('validation') or {}
        return set(validation.get('missing_knowledge') or [])

    def select_reparse(self, tree: str, live: dict[str, dict], force: bool) -> list[str]:
        state = self.report['trees'][tree]
        knowledge = state.setdefault('knowledge', {})
        previous_missing = self._previous_missing(tree)
        first_submission = not state.get('phases', {}).get('submitted_at')
        selected = []
        for kid in self.trees[tree]['knowledge']:
            item = live.get(kid) or {}
            status = str(item.get('parse_status') or '')
            entry = knowledge.setdefault(kid, {})
            attempts = int(entry.get('attempts') or 0)
            if force or first_submission:
                selected.append(kid)
            elif status in FAILED_STATUSES:
                selected.append(kid)
            elif status == 'completed' and kid in previous_missing and attempts < 3:
                selected.append(kid)
        return selected

    def start(self, force: bool = False, wait: bool = False, timeout: int = 21600,
              poll: int = 10) -> int:
        self.report['last_action'] = 'start'
        self.guard_model()
        self.discover_trees(self.requested_trees)
        self.provision()
        submitted_any = False
        for tree in self.trees:
            info = self.trees[tree]
            state = self.report['trees'][tree]
            live = self.live_knowledge(tree)
            for kid, item in live.items():
                if kid not in info['knowledge']:
                    continue
                entry = state['knowledge'].setdefault(kid, {})
                entry.update({
                    'title': item.get('title') or '',
                    'parse_status': item.get('parse_status'),
                    'updated_at': item_updated_at(item),
                })
            selected = self.select_reparse(tree, live, force)
            if not selected:
                log(f'{tree}：没有需要重新解析的知识（已完成 {len(live)} 项）')
                continue
            submitted_at = now_iso()
            for kid in selected:
                entry = state['knowledge'].setdefault(kid, {})
                entry['pre_status'] = (live.get(kid) or {}).get('parse_status')
                entry['pre_updated_at'] = item_updated_at(live.get(kid))
                entry['attempts'] = int(entry.get('attempts') or 0) + 1
                entry['last_submitted_at'] = submitted_at
            task_ids = self.sidecar.reparse_knowledges(info['kb_id'], selected)
            if not task_ids:
                raise SystemExit(f'{tree} 的重解析任务未返回 task_id')
            state['phases']['submitted_at'] = submitted_at
            state.setdefault('reparse_tasks', []).extend(task_ids)
            state['last_reparse_ids'] = selected
            submitted_any = True
            log(f'{tree}：已提交 {len(selected)} 个知识重解析（task={",".join(task_ids)}）')
            self.save()
        self.save()
        if not submitted_any and not wait:
            log('无需提交；使用 validate 检查现有 Wiki，或使用 --force 强制全部重处理。')
        if wait:
            return self.wait_and_validate(timeout=timeout, poll=poll)
        if submitted_any:
            log('重处理已提交。完成后续处理并校验：python scripts/rebuild_wiki.py wait --wait')
        return 0

    # ---- waiting ---------------------------------------------------------
    def _status_counts(self, statuses: dict[str, dict]) -> Counter:
        return Counter(str(item.get('parse_status') or 'missing') for item in statuses.values())

    def wait_for_terminal(self, timeout: int, poll: int) -> dict[str, dict]:
        deadline = time.monotonic() + timeout
        last_report = 0.0
        last_remap = time.monotonic()
        while True:
            terminal = True
            observed_all = True
            statuses_by_tree: dict[str, dict[str, dict]] = {}
            for tree, info in self.trees.items():
                state = self.report['trees'][tree]
                statuses = self.sidecar.knowledge_statuses(info['kb_id'], list(info['knowledge']))
                statuses_by_tree[tree] = statuses
                for kid in info['knowledge']:
                    item = statuses.get(kid) or {}
                    entry = state['knowledge'].setdefault(kid, {})
                    status = str(item.get('parse_status') or 'missing')
                    entry['parse_status'] = status
                    entry['updated_at'] = item_updated_at(item)
                    entry['error'] = item.get('error_message') or ''
                    if status not in TERMINAL_STATUSES:
                        terminal = False
                    if entry.get('pre_updated_at') is not None:
                        changed = (status != entry.get('pre_status')
                                   or item_updated_at(item) != entry.get('pre_updated_at'))
                        if not changed and status == 'completed':
                            observed_all = False
                try:
                    stats = self.sidecar.wiki_stats(info['kb_id'])
                except SidecarUnavailable as exc:
                    stats = {'error': str(exc), 'pending_tasks': -1, 'is_active': True}
                state['stats'] = stats
                if int(stats.get('pending_tasks') or 0) != 0 or stats.get('is_active'):
                    terminal = False
                self.save()
            # Native chunk ids change as soon as a reparse rewrites chunks,
            # long before Wiki synthesis finishes.  Refresh the mapping while
            # waiting so retrieval keeps working during the Wiki run.
            if time.monotonic() - last_remap >= 300:
                try:
                    self.remap()
                except Exception as exc:  # a transient sidecar/workspace error must not abort waiting
                    log(f'原生分块映射刷新失败（将在下一轮重试）：{type(exc).__name__}: {exc}')
                last_remap = time.monotonic()
            elapsed = time.monotonic() - (deadline - timeout)
            if time.monotonic() - last_report >= 30:
                for tree, info in self.trees.items():
                    state = self.report['trees'][tree]
                    counts = self._status_counts(statuses_by_tree[tree])
                    stats = state.get('stats') or {}
                    activity = state['knowledge'].get(next(iter(info['knowledge']), ''), {})
                    log(f'{tree}：{dict(counts)} · Wiki pending={stats.get("pending_tasks")} '
                        f'active={stats.get("is_active")} pages={stats.get("total_pages")} '
                        f'({elapsed:.0f}s)')
                last_report = time.monotonic()
            if terminal and observed_all:
                log('所有活动知识重处理完成，Wiki 队列已空闲。')
                return statuses_by_tree
            if time.monotonic() >= deadline:
                raise TimeoutError(f'等待重处理超时（{timeout}s）；报告已保存，可重新运行 wait 继续。')
            time.sleep(max(2, poll))

    def failed_knowledge(self) -> dict[str, list[str]]:
        failures: dict[str, list[str]] = {}
        for tree, info in self.trees.items():
            state = self.report['trees'][tree]
            failed = [kid for kid in info['knowledge']
                      if (state['knowledge'].get(kid) or {}).get('parse_status') in FAILED_STATUSES]
            if failed:
                failures[tree] = failed
        return failures

    # ---- remap -----------------------------------------------------------
    def remap(self) -> dict:
        from platform_server.ingestion import Ingestion
        completed_documents = set()
        for tree, info in self.trees.items():
            state = self.report['trees'][tree]
            for document_id, knowledge_ids in info['documents'].items():
                # `finalizing` has completed chunks/embeddings and is only
                # waiting for Wiki synthesis, so the native mapping can be
                # refreshed now instead of waiting hours for the wiki queue.
                if all((state['knowledge'].get(kid) or {}).get('parse_status') in {'completed', 'finalizing'}
                       for kid in knowledge_ids):
                    completed_documents.add(document_id)
        if not completed_documents:
            return {'remapped': 0, 'documents': 0, 'failed': [], 'skipped': 'no-completed-documents'}
        ingestion = Ingestion(self.store)
        try:
            knowledge_store = ingestion.workspace(self.owner).knowledge.brain.store
            result = {}
            for attempt in range(3):
                result = self.index.remap_documents(
                    knowledge_store, document_ids=sorted(completed_documents))
                if not result.get('failed'):
                    break
                log(f'原生分块映射失败 {len(result["failed"])} 份，第 {attempt + 1}/3 次重试…')
                time.sleep(10)
        finally:
            ingestion.close()
        self.report['remap'] = {'at': now_iso(), **result}
        self.save()
        log(f'原生分块映射刷新：{result.get("remapped")} 份成功，'
            f'{len(result.get("failed") or [])} 份失败')
        return result

    # ---- validation ------------------------------------------------------
    def _fetch_pages(self, kb_id: str) -> tuple[list[dict], int]:
        pages: list[dict] = []
        total = 0
        for page in range(1, 1001):
            response = self.sidecar.wiki_pages(kb_id, page=page, page_size=100)
            batch = response.get('pages') or []
            total = int(response.get('total') or 0)
            pages.extend(batch)
            if not batch or len(pages) >= total:
                break
        return pages, total

    def validate(self) -> dict:
        overall_ok = True
        summary: dict[str, dict] = {}
        for tree, info in self.trees.items():
            state = self.report['trees'][tree]
            live = self.live_knowledge(tree)
            active_ids = set(info['knowledge'])
            pages, total = self._fetch_pages(info['kb_id'])
            stats = self.sidecar.wiki_stats(info['kb_id'])
            covered, unknown = set(), set()
            empty_content, short_content = [], []
            for page in pages:
                for ref in page.get('source_refs') or []:
                    knowledge_id = str(ref).split('|', 1)[0]
                    covered.add(knowledge_id)
                    if knowledge_id not in active_ids:
                        unknown.add(knowledge_id)
                content = str(page.get('content') or '').strip()
                if page.get('page_type') != 'index':
                    if not content:
                        empty_content.append(page.get('slug'))
                    elif len(content) < 80:
                        short_content.append(page.get('slug'))
            missing = sorted(active_ids - covered)
            graph = self.sidecar.wiki_graph(info['kb_id'], mode='overview', limit=GRAPH_LIMIT)
            nodes = graph.get('nodes') or []
            edges = graph.get('edges') or []
            meta = graph.get('meta') or {}
            non_index_pages = sum(1 for page in pages if page.get('page_type') != 'index')
            block_counts = Counter(str(item.get('parse_status') or 'missing')
                                   for item in live.values())
            checks = {
                'stats_idle': int(stats.get('pending_tasks') or 0) == 0 and not stats.get('is_active'),
                'pages_present': total > 0 and len(pages) == total,
                'every_knowledge_covered': not missing,
                'no_unknown_source_refs': not unknown,
                'no_empty_page_content': not empty_content,
                'summary_pages_present': int((stats.get('pages_by_type') or {}).get('summary') or 0) > 0,
                'graph_nodes_present': bool(nodes),
                'graph_edges_present': bool(edges),
                'graph_not_truncated': bool(meta) and not meta.get('truncated')
                and int(meta.get('returned') or 0) == int(meta.get('total') or 0)
                and int(meta.get('total') or 0) == non_index_pages,
            }
            ok = all(checks.values())
            overall_ok = overall_ok and ok
            validation = {
                'at': now_iso(),
                'ok': ok,
                'checks': checks,
                'active_knowledge': len(active_ids),
                'knowledge_statuses': dict(block_counts),
                'pages': total,
                'pages_by_type': stats.get('pages_by_type') or {},
                'published_pages': sum(1 for page in pages if page.get('status') == 'published'),
                'covered_knowledge': len(covered & active_ids),
                'missing_knowledge': missing,
                'unknown_source_refs': sorted(unknown),
                'empty_content_pages': empty_content[:20],
                'short_content_pages': short_content[:20],
                'graph': {
                    'nodes': len(nodes),
                    'edges': len(edges),
                    'total': meta.get('total'),
                    'returned': meta.get('returned'),
                    'truncated': meta.get('truncated'),
                },
                'stats': {
                    'total_pages': stats.get('total_pages'),
                    'total_links': stats.get('total_links'),
                    'pending_tasks': stats.get('pending_tasks'),
                    'pending_issues': stats.get('pending_issues'),
                    'is_active': stats.get('is_active'),
                },
            }
            state['validation'] = validation
            summary[tree] = {
                'ok': ok,
                'knowledge': len(active_ids),
                'covered': len(covered & active_ids),
                'missing': len(missing),
                'pages': total,
                'nodes': len(nodes),
                'edges': len(edges),
            }
            self.save()
            log(f'{tree} 校验：{"通过" if ok else "未通过"} · 知识覆盖 '
                f'{len(covered & active_ids)}/{len(active_ids)} · 页面 {total} · '
                f'图节点 {len(nodes)} / 边 {len(edges)}')
            for name, passed in checks.items():
                if not passed:
                    log(f'  ✗ {name}')
            if missing:
                log(f'  ✗ 未覆盖知识：{", ".join(value[:8] for value in missing[:8])}')
            if unknown:
                log(f'  ✗ 未知来源引用：{len(unknown)}')
        self.report['summary'] = {
            'ok': overall_ok,
            'validated_at': now_iso(),
            'trees': summary,
            'failed_knowledge': self.failed_knowledge(),
            'deepseek_model_used': False,
            'model': self.report.get('model'),
        }
        self.report['last_action'] = 'validate'
        self.save()
        return self.report['summary']

    def wait_and_validate(self, timeout: int, poll: int) -> int:
        timed_out = False
        try:
            self.wait_for_terminal(timeout=timeout, poll=poll)
        except TimeoutError as exc:
            log(str(exc))
            timed_out = True
        failures = self.failed_knowledge()
        if failures:
            log(f'存在重处理失败：{ {tree: len(ids) for tree, ids in failures.items()} }')
        remap = self.remap()
        if remap.get('failed'):
            log('原生分块映射仍有失败项；校验将报告问题。')
        summary = self.validate()
        ok = summary['ok'] and not failures and not remap.get('failed') and not timed_out
        if ok:
            log('Wiki 与关系图谱校验通过。')
            return 0
        log('Wiki 校验未完全通过；请查看报告并重新运行 start --force 或仅重试缺失项。')
        return 3 if timed_out else 1

    # ---- status / rollback ----------------------------------------------
    def status(self) -> int:
        self.discover_trees(self.requested_trees)
        for tree, info in self.trees.items():
            state = self.report['trees'][tree]
            live = {item['id']: item for item in self.sidecar.list_knowledge(info['kb_id'])}
            counts = Counter(str(item.get('parse_status') or 'missing')
                             for kid, item in live.items() if kid in info['knowledge'])
            try:
                stats = self.sidecar.wiki_stats(info['kb_id'])
            except SidecarUnavailable as exc:
                stats = {'error': str(exc)}
            validation = state.get('validation') or {}
            log(f'{tree}：{info["kb_id"]} · knowledge={dict(counts)} · '
                f'wiki_pages={stats.get("total_pages")} pending={stats.get("pending_tasks")} '
                f'active={stats.get("is_active")} · validation={validation.get("ok", "未校验")}')
        return 0

    def rollback(self) -> int:
        self.discover_trees(self.requested_trees)
        for tree, info in self.trees.items():
            state = self.report['trees'][tree]
            original = state.get('original') or {}
            if not original:
                log(f'{tree}：报告没有原始配置快照，跳过。')
                continue
            current = self.sidecar.knowledge_base(info['kb_id']) or {}
            strategy = dict(original.get('indexing_strategy') or current.get('indexing_strategy') or {})
            strategy['wiki_enabled'] = bool(
                (original.get('indexing_strategy') or {}).get('wiki_enabled'))
            body = {
                'name': info['kb_name'],
                'description': current.get('description') or '知识数据推理脑原生检索索引（可重建）',
                'config': {
                    'chunking_config': original.get('chunking_config')
                    or current.get('chunking_config') or {},
                    'wiki_config': original.get('wiki_config') or {},
                    'indexing_strategy': strategy,
                },
            }
            self.sidecar._request('PUT', f'/api/v1/knowledge-bases/{info["kb_id"]}', body)
            state['phases']['rolled_back_at'] = now_iso()
            log(f'{tree}：已恢复原始 Wiki 配置（页面数据保留，重新启用后可复用）。')
        self.report['last_action'] = 'rollback'
        self.save()
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Resumable WeKnora Wiki reprocessing and validation')
    parser.add_argument('action', choices=['start', 'wait', 'validate', 'status', 'rollback'])
    parser.add_argument('--owner', default='admin')
    parser.add_argument('--store-root', type=Path, default=STORE_ROOT)
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    parser.add_argument('--tree', action='append', default=[], help='knowledge.medical / ...')
    parser.add_argument('--all', action='store_true', help='all active trees (default)')
    parser.add_argument('--force', action='store_true', help='reparse even completed knowledge')
    parser.add_argument('--wait', action='store_true', help='wait for completion after start')
    parser.add_argument('--timeout', type=int, default=21600)
    parser.add_argument('--poll', type=int, default=10)
    return parser


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = build_parser().parse_args()
    store = Store(args.store_root)
    runner = WikiRebuild(store, args.owner, args.report)
    runner.requested_trees = [] if args.all else args.tree
    try:
        if args.action == 'start':
            return runner.start(force=args.force, wait=args.wait,
                                timeout=args.timeout, poll=args.poll)
        if args.action == 'wait':
            runner.discover_trees(runner.requested_trees)
            return runner.wait_and_validate(timeout=args.timeout, poll=args.poll)
        if args.action == 'validate':
            runner.discover_trees(runner.requested_trees)
            summary = runner.validate()
            return 0 if summary['ok'] else 1
        if args.action == 'status':
            return runner.status()
        if args.action == 'rollback':
            return runner.rollback()
    except SystemExit:
        raise
    except (SidecarUnavailable, TimeoutError, RuntimeError, ValueError) as exc:
        log(f'失败：{exc}')
        log(traceback.format_exc(limit=3))
        return 4
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
