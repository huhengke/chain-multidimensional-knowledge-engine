/* Documentation-only archive: no runtime databases, credentials or source trees. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');

const app = path.resolve(__dirname, '..');
const root = app;
const archive = path.join(app, 'docs/archive/2026-10-19');
const digest = data => crypto.createHash('sha256').update(data).digest('hex');
const documents = [
  '链式多维时空知识库引擎-产品与技术规划书.html',
  '链式多维时空知识库引擎-产品与技术规划书.md',
  '链式多维时空知识库引擎-全量实施计划与接口契约.md',
  'README.md',
  'docs/production-platform.md',
  'docs/首次使用与生产验收测试手册.md',
  'docs/superpowers/specs/2026-10-18-knowledge-fast-retrieval-design.md',
];

function snapshot() {
  const target = path.join(archive, 'before-update');
  const manifest = path.join(archive, 'before-update-manifest.json');
  if (fs.existsSync(manifest)) {
    const rows = JSON.parse(fs.readFileSync(manifest, 'utf8'));
    for (const row of rows) {
      if (digest(fs.readFileSync(path.join(archive, row.snapshot))) !== row.sha256)
        throw new Error(`Snapshot hash mismatch: ${row.snapshot}`);
    }
    console.log(JSON.stringify({stage: 'existing-snapshot-verified', files: rows.length}));
    return;
  }
  const rows = documents.map(relative => {
    const source = path.join(root, relative);
    const destination = path.join(target, relative);
    const bytes = fs.readFileSync(source);
    fs.mkdirSync(path.dirname(destination), {recursive: true});
    fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
    const sha256 = digest(bytes);
    if (digest(fs.readFileSync(destination)) !== sha256) throw new Error(`Copy failed: ${relative}`);
    return {source, snapshot: path.relative(archive, destination).split(path.sep).join('/'), bytes: bytes.length, sha256};
  });
  fs.writeFileSync(manifest, JSON.stringify(rows, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({stage: 'snapshot-created', files: rows.length, bytes: rows.reduce((n, r) => n + r.bytes, 0)}));
}

function filesIn(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
    const absolute = path.join(directory, entry.name);
    return entry.isDirectory() ? filesIn(absolute) : entry.name.endsWith('.jsonl') ? [absolute] : [];
  });
}

async function sessionMetadata(file) {
  const input = fs.createReadStream(file);
  const lines = readline.createInterface({input, crlfDelay: Infinity});
  try {
    for await (const first of lines) {
      const record = JSON.parse(first);
      return record.type === 'session_meta' ? record.payload : null;
    }
    return null;
  } catch { return null; } finally { lines.close(); input.destroy(); }
}

const topicRules = [
  ['architecture-and-WeKnora', /WeKnora|weknora|智慧树|三脑|3\+1|中通|DNA/],
  ['retrieval-and-performance', /向量|检索|性能|耗时|共振|embedding|重排|全量扫描/],
  ['dual-time', /双时间|内容时间|时间维度星团|昨天.*去年/],
  ['platform-and-intake', /数据接入|数据中心|模型配置|后台|导入|OCR|上传/],
  ['verification-and-docs', /验收|测试|验证|文档|规划书|归档/],
];
function safeSnippet(value, max = 650) {
  return value.replace(/(?:sk-|Bearer\s+)[A-Za-z0-9_.-]{8,}/gi, '[REDACTED]')
    .replace(/((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+/gi, '$1[REDACTED]')
    .replace(/https?:\/\/[^\s<>"']+/g, '[URL]')
    .replace(/<environment_context>[\s\S]*?<\/environment_context>/g, '')
    .replace(/<in-app-browser-context[\s\S]*?<\/in-app-browser-context>/g, '')
    .trim().slice(0, max);
}
async function sourceAudit() {
  const codexRoot = process.env.CODEX_HOME || '';
  const files = [...filesIn(path.join(codexRoot, 'sessions')), ...filesIn(path.join(codexRoot, 'archived_sessions'))];
  const rows = [];
  const excerpts = [];
  const failures = [];
  for (const file of files) {
    const meta = await sessionMetadata(file);
    const cwd = meta?.cwd || '';
    if (!cwd.includes('多模态时空智能引擎-产品规划书')) continue;
    const tags = new Set();
    let users = 0, assistants = 0, compacted = 0, first = '', last = '';
    const recent = [];
    try {
      const lines = readline.createInterface({input: fs.createReadStream(file), crlfDelay: Infinity});
      for await (const line of lines) {
        let record;
        try { record = JSON.parse(line); } catch { continue; }
        if (record.type === 'compacted') compacted++;
        const payload = record.payload || {};
        if (record.type !== 'response_item' || payload.type !== 'message' || !['user', 'assistant'].includes(payload.role)) continue;
        const message = (payload.content || []).map(part => part.text || '').join('\n');
        if (!message.trim()) continue;
        if (payload.role === 'user') users++; else assistants++;
        first ||= record.timestamp || '';
        last = record.timestamp || last;
        for (const [tag, pattern] of topicRules) if (pattern.test(message)) tags.add(tag);
        if (payload.role === 'user' || payload.channel === 'final') {
          const snippet = safeSnippet(message);
          if (snippet) recent.push({role: payload.role, timestamp: record.timestamp, snippet});
          if (recent.length > 8) recent.shift();
        }
      }
      const bytes = fs.statSync(file).size;
      rows.push({thread_id: meta.id || null, source_path: file, cwd, first_message_at: first, last_message_at: last,
        user_messages: users, assistant_messages: assistants, compactions: compacted, bytes, topics: [...tags],
        coverage: 'Readable local message records indexed; topic summary is synthesized separately; compacted/deleted/cloud-only text is not reconstructed.'});
      excerpts.push({thread_id: meta.id, messages: recent});
    } catch (error) { failures.push({source_path: file, reason: error.code || 'READ_FAILED'}); }
  }
  fs.mkdirSync(archive, {recursive: true});
  const report = {as_of: '2026-10-19', scope: root, inspected_session_files: files.length,
    selected_sessions: rows.length, sources: rows, unreadable: failures,
    exclusions: ['Unrelated projects', 'Tool outputs and secrets', 'Deleted or unavailable cloud-only conversations', 'Original documents, databases, model weights and media are not copied']};
  fs.writeFileSync(path.join(archive, 'conversation-sources.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({summary: {inspected_session_files: files.length, selected_sessions: rows.length, unreadable: failures.length}, sources: rows, recent_excerpts: excerpts}, null, 2));
}

if (process.argv[2] === 'snapshot') snapshot();
else if (process.argv[2] === 'sources') sourceAudit().catch(error => { console.error(error); process.exitCode = 1; });
else throw new Error('Usage: node scripts/archive-project-docs.cjs snapshot|sources');
