/* Offline document QA. No browser, network, model calls or runtime mutations. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const app = path.dirname(__dirname);
const archive = path.join(app, 'docs/archive/2026-10-19');
const moduleRoot = process.argv[2];
if (!moduleRoot) throw new Error('Pass the installed workspace Node modules directory.');
const sharp = require(path.join(moduleRoot, 'sharp'));
const xml = require(path.join(moduleRoot, 'xml-js'));
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const main = '链式多维时空知识库引擎-产品与技术规划书';
const tech = 'docs/技术模式与实现现状';
const docs = [main + '.html', main + '.md', '链式多维时空知识库引擎-全量实施计划与接口契约.md',
  'README.md', 'docs/production-platform.md',
  'docs/首次使用与生产验收测试手册.md',
  'docs/superpowers/specs/2026-10-18-knowledge-fast-retrieval-design.md',
  tech + '.md', tech + '.html'];
const errors = [];
const links = [];
for (const relative of docs) {
  const value = read(relative);
  if (value.includes('\ufffd') || /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/u.test(value)) errors.push(`${relative}: invalid Unicode`);
  const clean = relative.endsWith('.md') ? value.replace(/```[\s\S]*?```/g, '') : value;
  const references = relative.endsWith('.md')
    ? [...clean.matchAll(/!?\[[^\]]+\]\(([^)]+)\)/g)].map(m => m[1])
    : [...clean.matchAll(/\b(?:href|src)="([^"]+)"/g)].map(m => m[1]);
  for (const item of references) {
    let href = item.replace(/^<|>$/g, '');
    if (/^(?:https?:|mailto:)/i.test(href)) continue;
    if (href.startsWith('#')) {
      if (relative.endsWith('.html') && !value.includes(`id="${href.slice(1)}"`)) errors.push(`${relative}: missing anchor ${href}`);
      continue;
    }
    href = decodeURIComponent(href.split('#')[0]);
    const target = path.resolve(path.dirname(path.join(root, relative)), href);
    const exists = fs.existsSync(target);
    links.push({ document: relative, href, exists });
    if (!exists) errors.push(`${relative}: missing link ${href}`);
  }
}

const mainMd = read(main + '.md'), mainHtml = read(main + '.html');
const fragments = ['topology-views', 'knowledge-modes', 'product-entry', 'implementation-baseline'];
const normalize = value => value.replace(/<!--.*?-->/gs, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/\s+/g, '');
const fragmentResults = [];
for (const name of fragments) {
  const start = `<!-- sync:${name}:start -->`, end = `<!-- sync:${name}:end -->`;
  const mdFragment = mainMd.split(start)[1]?.split(end)[0];
  const htmlFragment = mainHtml.split(start)[1]?.split(end)[0];
  if (!mdFragment || !htmlFragment) { errors.push(`Missing fragment ${name}`); continue; }
  const htmlText = normalize(htmlFragment.replace(/<[^>]*>/g, ''));
  // Compare every Markdown heading, paragraph, list item and table cell with rendered text.
  const mdText = mdFragment.replace(/!\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
  const expected = mdText.split('\n').map(line => line.trim()).filter(line => line && !/^\|?[\s:|-]+\|$/.test(line))
    .flatMap(line => line.startsWith('|') ? line.replace(/^\||\|$/g, '').split('|') : [line.replace(/^#{1,6}\s+|^-\s+|^\d+\.\s+/g, '')])
    .map(value => normalize(value.replace(/\*\*|`/g, ''))).filter(Boolean);
  const absent = expected.filter(value => !htmlText.includes(value));
  fragmentResults.push({ name, checkedTextItems: expected.length, missingTextItems: absent.length });
  for (const value of absent) errors.push(`HTML parity ${name}: ${value.slice(0, 70)}`);
}
const techText = normalize(read(tech + '.html').replace(/<[^>]*>/g, ''));
const techExpected = read(tech + '.md').replace(/!\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  .split('\n').map(line => line.trim()).filter(line => line && !/^\|?[\s:|-]+\|$/.test(line))
  .flatMap(line => line.startsWith('|') ? line.replace(/^\||\|$/g, '').split('|') : [line.replace(/^#{1,6}\s+|^-\s+|^\d+\.\s+/g, '')])
  .map(value => normalize(value.replace(/\*\*|`/g, ''))).filter(Boolean);
const techAbsent = techExpected.filter(value => !techText.includes(value));
for (const value of techAbsent) errors.push(`Technical HTML parity: ${value.slice(0, 70)}`);
const originalMainHtml = fs.readFileSync(path.join(archive, 'before-update', '三脑认知与知识引擎-产品与技术规划书.html'), 'utf8');
const figurePattern = /class="diagram(?:\s[^"\n]*)?"/g;
const originalFigures = [...originalMainHtml.matchAll(figurePattern)].length;
const currentFigures = [...mainHtml.matchAll(figurePattern)].length;
if (originalFigures !== currentFigures) errors.push('Existing architecture figure count changed');
if (!mainMd.includes('V3.4') || !mainHtml.includes('V3.4')) errors.push('Main version mismatch');
if (mainHtml.includes('V3.3') || mainMd.includes('V3.3')) errors.push('Stale main version');
for (const relative of [main + '.html', tech + '.html']) {
  const source = read(relative).replace(/<!--.*?-->/gs, '').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, '');
  const voids = new Set(['meta', 'link', 'img', 'br', 'hr', 'input', 'source', 'area', 'base', 'col', 'embed', 'param', 'track', 'wbr']);
  const stack = [];
  for (const tag of source.matchAll(/<(\/?)([a-z][\w:-]*)\b[^>]*>/gi)) {
    const name = tag[2].toLowerCase();
    if (voids.has(name) || tag[0].endsWith('/>')) continue;
    if (!tag[1]) stack.push(name);
    else if (stack.pop() !== name) errors.push(`${relative}: tag nesting near ${tag.index}, ${tag[0]}`);
  }
  if (stack.length) errors.push(`${relative}: unclosed tags ${stack.join(',')}`);
}

async function mainCheck() {
  const diagrams = [];
  const previewDir = path.join(archive, 'previews');
  fs.mkdirSync(previewDir, {recursive: true});
  for (const name of ['three-brain-tree', 'knowledge-dual-time', 'knowledge-adaptive-retrieval']) {
    const relative = `docs/diagrams/${name}.svg`;
    const bytes = fs.readFileSync(path.join(root, relative));
    const parsed = xml.xml2js(bytes.toString('utf8'), {compact: false});
    if (!parsed.elements?.some(element => element.name === 'svg')) errors.push(`Not SVG: ${name}`);
    const destination = path.join(previewDir, name + '.png');
    await sharp(bytes, {density: 144}).png().toFile(destination);
    const info = await sharp(destination).metadata();
    diagrams.push({ file: relative, sha256: sha256(bytes), preview: path.relative(archive, destination).split(path.sep).join('/'), width: info.width, height: info.height, bytes: fs.statSync(destination).size });
  }
  const snapshots = JSON.parse(fs.readFileSync(path.join(archive, 'before-update-manifest.json'), 'utf8'));
  for (const row of snapshots) if (sha256(fs.readFileSync(path.join(archive, row.snapshot))) !== row.sha256) errors.push(`Snapshot changed: ${row.snapshot}`);
  const report = { as_of: '2026-10-19', scope: 'documentation-only', checks: {
    documents: docs.length, relativeFileLinks: links.length, fragments: fragmentResults,
    technicalHtmlTextItems: techExpected.length, technicalHtmlMissingItems: techAbsent.length,
    existingArchitectureFigures: {before: originalFigures, after: currentFigures},
    snapshotFilesVerified: snapshots.length, svgXmlAndLocalRaster: diagrams.length,
    htmlBrowserVisualCheck: 'not-run: browser policy rejected local file protocol; no server/proxy/browser workaround attempted',
    runtimeTests: 'not-run: no runtime change or production benchmark in this documentation task'
  }, errors, diagrams };
  fs.writeFileSync(path.join(archive, 'verification.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
  const artifacts = [...docs, ...diagrams.map(row => row.file),
    'scripts/archive-project-docs.cjs', 'scripts/sync-project-docs.cjs',
    'scripts/verify-project-docs.cjs'];
  fs.writeFileSync(path.join(archive, 'updated-documents-manifest.json'), JSON.stringify(artifacts.map(relative => {
    const bytes = fs.readFileSync(path.join(root, relative));
    return {file: relative, bytes: bytes.length, sha256: sha256(bytes)};
  }), null, 2) + '\n', 'utf8');
  process.stdout.write(JSON.stringify(report, null, 2));
  if (errors.length) process.exitCode = 1;
}
mainCheck().catch(error => { console.error(error); process.exitCode = 1; });
