/* Mechanical HTML rendering: Markdown is authoritative for V3.4 additions. */
const fs = require('node:fs');
const path = require('node:path');
const app = path.resolve(__dirname, '..');
const root = app;
const mainBase = path.join(root, '链式多维时空知识库引擎-产品与技术规划书');
const techBase = path.join(app, 'docs', '技术模式与实现现状');
const escape = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function inline(value) {
  const expression = /(!?\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*)/g;
  let output = '', previous = 0;
  for (const match of value.matchAll(expression)) {
    output += escape(value.slice(previous, match.index));
    if (match[4] !== undefined) output += `<code>${escape(match[4])}</code>`;
    else if (match[5] !== undefined) output += `<strong>${inline(match[5])}</strong>`;
    else {
      let href = match[3].replace(/^<|>$/g, '');
      if (/^(?:javascript|data):/i.test(href)) throw new Error('Unsafe document link');
      if (href.endsWith('技术模式与实现现状.md')) href = href.slice(0, -3) + '.html';
      output += match[0].startsWith('!')
        ? `<figure class="document-figure"><img src="${escape(href)}" alt="${escape(match[2])}"><figcaption>${escape(match[2])}</figcaption></figure>`
        : `<a href="${escape(href)}">${escape(match[2])}</a>`;
    }
    previous = match.index + match[0].length;
  }
  return output + escape(value.slice(previous));
}

function render(markdown) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const output = [];
  for (let i = 0; i < lines.length;) {
    const line = lines[i].trim();
    if (!line || /^<!--.*-->$/.test(line)) { i++; continue; }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) { output.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); i++; continue; }
    if (line.startsWith('```')) {
      const code = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) code.push(lines[i++]);
      i++; output.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`); continue;
    }
    if (/^\|/.test(line) && /^\|?[\s:|-]+\|$/.test((lines[i + 1] || '').trim())) {
      const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
      const head = cells(lines[i]); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(`<tr>${cells(lines[i++]).map(cell => `<td>${inline(cell)}</td>`).join('')}</tr>`);
      }
      output.push(`<div class="table-scroll"><table><thead><tr>${head.map(cell => `<th>${inline(cell)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`);
      continue;
    }
    if (/^(?:- |\d+\. )/.test(line)) {
      const ordered = /^\d/.test(line), tag = ordered ? 'ol' : 'ul', items = [];
      const pattern = ordered ? /^\d+\.\s+/ : /^-\s+/;
      while (i < lines.length && pattern.test(lines[i].trim())) items.push(`<li>${inline(lines[i++].trim().replace(pattern, ''))}</li>`);
      output.push(`<${tag}>${items.join('')}</${tag}>`); continue;
    }
    if (/^!\[/.test(line)) { output.push(inline(line)); i++; continue; }
    if (/^---+$/.test(line)) { output.push('<hr>'); i++; continue; }
    const paragraph = [line]; i++;
    while (i < lines.length && lines[i].trim() && !/^(?:#|<!--|\||!\[|```|- |\d+\. )/.test(lines[i].trim())) paragraph.push(lines[i++].trim());
    output.push(`<p>${inline(paragraph.join(' '))}</p>`);
  }
  return output.join('\n');
}

const style = `
/* sync:document-style:start */
.document-figure { margin:24px 0; padding:16px; border:1px solid #cfdbe6; background:#f8fbff; border-radius:6px; }
.document-figure img { display:block; width:100%; height:auto; background:#fff; }
.document-figure figcaption { margin-top:10px; color:#475569; font-size:13px; }
.table-scroll { max-width:100%; overflow-x:auto; margin:18px 0; }
.table-scroll table { width:100%; min-width:660px; margin:0; }
.section code { color:#243b53; background:#eef3f8; padding:2px 4px; border-radius:3px; overflow-wrap:anywhere; }
.section a { color:#155d9b; overflow-wrap:anywhere; }
@media(max-width:700px) { .document-figure { padding:6px; margin:16px 0; } .table-scroll { margin:12px -8px; } }
/* sync:document-style:end */`;

const mainMarkdown = fs.readFileSync(mainBase + '.md', 'utf8');
let mainHtml = fs.readFileSync(mainBase + '.html', 'utf8');
mainHtml = mainHtml.replaceAll('V3.3', 'V3.4');
mainHtml = mainHtml.replace('状态：自动生长与治理边界基线', '状态：架构与实现对照 · 2026-10-19');
mainHtml = mainHtml.replace('查询必须先给出明确时间条件，系统将其分别映射到每棵符合权限与能力条件的树。',
  '有时间限定的问题先编译为明确范围，再映射到符合权限与能力条件的树；无时间的概念问题可跨日检索授权内容，不要求用户人为补日期。');
mainHtml = mainHtml.replace(/<div class="status-grid">.*?<\/div><\/div>\s*(?=<\/section>)/s,
  '<div class="status-grid"><div class="status"><span class="tag live">独立工程基线</span><strong>结构化数据推理脑与统一平台</strong><p>引擎、Node Agent 与 CNTS 实现已迁入本项目，本安装独立持有数据、配置与进程。统一后台已有接入、模型、数据集、Agent、图表与拓扑入口。</p></div><div class="status"><span class="tag candidate">候选配置须验收</span><strong>CNTS 二进制执行器</strong><p>保留不可变 generation、Catalog 与人工激活/回切；源码迁入不等于候选配置已在本安装启用。</p></div><div class="status"><span class="tag plan">源码已实现，生产待验收</span><strong>知识脑与时空记忆脑</strong><p>已有知识 OCR、内嵌 WeKnora、Agent、双时间索引与检索优化；时空脑有文件与摄像头/RTSP 入口。并发、恢复与完整生产后台仍需验收。</p></div></div>');
mainHtml = mainHtml.replace('同尺独立主干；每个日圈为发布、生效、修订或采集时间下的三维概念与证据向量集合；无可靠时间则显式标记未定时间层',
  '存入/真实观察时间形成进入系统的日圈；案例、发布、生效与修订时间另建有来源的内容时间锚点；未知内容日期不能用导入日代替');
mainHtml = mainHtml.replace('<strong>可靠时间主干：</strong>使用发布、生效、修订、采集等有来源的时间字段。没有可靠时间的材料进入“未定时间层”，不能伪造时序。',
  '<strong>双时间而非混合日期：</strong>存入/真实观察时间用于中通日圈；发布、生效、修订与案例时间另建内容锚点。未知内容时间不由导入日补造。');
const runtimeParagraph = mainMarkdown.split('\n').find(line => line.startsWith('知识认知脑已锁定并维护 WeKnora'));
mainHtml = mainHtml.replace(/<h3>5\.4 WeKnora[^<]*<\/h3>\s*<p>.*?<\/p>/s,
  `<h3>5.4 WeKnora 源码内嵌知识运行时（已内嵌的受管实现）</h3>\n<p>${inline(runtimeParagraph)}</p>`);
mainHtml = mainHtml.replace('<td>直接内嵌源码模块</td><td>文档解析、切分与父子块、混合检索、重排、自动 Wiki/图谱、检索评测与观测</td>',
  '<td>宿主提取与内嵌源码协作</td><td>宿主解析/OCR；WeKnora 父子块、语义融合/重排、Wiki/图谱候选与评测；文字快速路径可跳过模型重排</td>');
mainHtml = mainHtml.replace('5.5 内部契约、来源治理与实现前置条件', '5.5 内部契约、来源治理与升级约束');
mainHtml = mainHtml.replace('由于上游采用 Go 的内部源码组织，实施时应以锁定提交的受管源码快照/派生模块方式维护，而不是把不稳定内部包作为普通依赖；上游提交号、抽取目录、改动记录和升级差异须可追溯。',
  '按锁定提交维护 Go 受管快照与派生模块；提交、抽取目录、文件哈希、改动记录与构建来源可追溯，升级不得静默漂移。');
mainHtml = mainHtml.replace('实施前固定上游版本，保留原许可证、版权声明及第三方依赖通知，并在 <code>THIRD_PARTY_NOTICES.md</code> 中记录来源、提交号、所用模块和本项目修改内容；未完成审查不得发布包含该源码的交付物。',
  '现有来源与第三方通知随模块保留；升级时重新核对许可证、依赖和改动，交付仍需对应环境审查与验收。');
if (!mainHtml.includes('以下保留原分期建设路线')) mainHtml = mainHtml.replace('<h2>第九部分：实施路线与验收标准</h2>',
  '<h2>第九部分：实施路线与验收标准</h2><p>以下保留原分期建设路线，用于说明依赖关系；不代表所有条目仍未开发，也不代表整期已通过生产验收。当前实现差异以第十一部分和技术现状文档为准。</p>');

function synchronizeFragment(name, section) {
  const begin = `<!-- sync:${name}:start -->`, end = `<!-- sync:${name}:end -->`;
  const first = mainMarkdown.indexOf(begin), last = mainMarkdown.indexOf(end);
  if (first < 0 || last < first) throw new Error(`Missing Markdown fragment ${name}`);
  const html = `${begin}\n${render(mainMarkdown.slice(first + begin.length, last))}\n${end}`;
  if (mainHtml.includes(begin)) {
    const htmlFirst = mainHtml.indexOf(begin), htmlLast = mainHtml.indexOf(end);
    if (htmlLast < htmlFirst) throw new Error(`Broken HTML fragment ${name}`);
    mainHtml = mainHtml.slice(0, htmlFirst) + html + mainHtml.slice(htmlLast + end.length);
  } else if (section) {
    const at = mainHtml.indexOf(`id="${section}"`), close = mainHtml.indexOf('</section>', at);
    if (close < 0) throw new Error(`Missing section ${section}`);
    mainHtml = mainHtml.slice(0, close) + html + '\n' + mainHtml.slice(close);
  } else {
    mainHtml = mainHtml.replace(/(?=\s*<footer class="footer">)/,
      `\n<section class="section" id="part11">\n${html}\n</section>\n`);
  }
  return { name, markdownCharacters: last - first - begin.length, htmlCharacters: html.length };
}
// Part 5 contains nested sections inside the existing architecture figure.
// Insert relative to the NEXT outer part, not the first nested closing tag.
function beforeNextPart(name, nextPart) {
  if (mainHtml.includes(`<!-- sync:${name}:start -->`)) return synchronizeFragment(name);
  const nextAt = mainHtml.indexOf(`<section class="section" id="${nextPart}">`);
  const close = mainHtml.lastIndexOf('</section>', nextAt);
  const marker = `<!-- sync:${name}:start -->\n<!-- sync:${name}:end -->\n`;
  mainHtml = mainHtml.slice(0, close) + marker + mainHtml.slice(close);
  return synchronizeFragment(name);
}
const fragments = [beforeNextPart('topology-views', 'part4'), beforeNextPart('knowledge-modes', 'part6'),
  beforeNextPart('product-entry', 'part8'), synchronizeFragment('implementation-baseline')];
if (!mainHtml.includes('href="#part11"')) mainHtml = mainHtml.replace('<li><a href="#part10">工程约束与风险边界</a></li>',
  '<li><a href="#part10">工程约束与风险边界</a></li><li><a href="#part11">V3.4 实现对照与后续工作</a></li>');
if (!mainHtml.includes('sync:document-style:start')) mainHtml = mainHtml.replace('</style>', `${style}\n</style>`);
if (!mainHtml.includes('class="revision-note"')) {
  const intro = mainMarkdown.split('\n').find(line => line.startsWith('本版新增统一 Agent'));
  mainHtml = mainHtml.replace('</header>', `</header>\n<aside class="section revision-note">${inline(intro)}</aside>`);
}
fs.writeFileSync(mainBase + '.html', mainHtml, 'utf8');

const techMarkdown = fs.readFileSync(techBase + '.md', 'utf8');
const standaloneStyle = `body{margin:0;background:#f3f6fa;color:#172b42;font:16px/1.8 "Microsoft YaHei","Segoe UI",sans-serif}main{max-width:1140px;margin:32px auto;padding:38px 44px;background:#fff;border:1px solid #d6e0ea;border-radius:8px}h1{font-size:30px;line-height:1.4}h2{font-size:24px;margin:40px 0 16px;border-top:1px solid #dbe4ec;padding-top:24px}h3{font-size:20px}a{color:#155d9b}table{border-collapse:collapse}th,td{border:1px solid #d5dfeb;text-align:left;vertical-align:top;padding:12px 14px}th{background:#eaf2fa;color:#163b59}pre{background:#f3f6fa;padding:18px;overflow:auto}code{overflow-wrap:anywhere}li{margin:7px 0}header small{color:#475569}@media(max-width:700px){main{margin:0;padding:22px 16px;border:0;border-radius:0}h1{font-size:25px}h2{font-size:21px}}`;
const techHtml = `<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>链式多维时空知识库引擎：技术模式与实现现状 V3.4</title><style>${standaloneStyle}\n${style}</style></head><body><main class="section"><header><small>V3.4 · 2026-10-19 · 源码对照与实现边界</small></header>\n${render(techMarkdown)}\n<footer><p>由同名 Markdown 生成。<a href="技术模式与实现现状.md">编辑源文档</a> · <a href="../链式多维时空知识库引擎-产品与技术规划书.html">主规划书</a></p></footer></main></body></html>\n`;
fs.writeFileSync(techBase + '.html', techHtml, 'utf8');
process.stdout.write(JSON.stringify({ version: 'V3.4', fragments, generated: [mainBase + '.html', techBase + '.html'] }, null, 2));
