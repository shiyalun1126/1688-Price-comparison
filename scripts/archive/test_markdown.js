// 从 index.html 提取 renderMarkdown 并测试
const fs = require('fs');
const html = fs.readFileSync('app/web/index.html', 'utf8');

// 提取 renderMarkdown 和 escapeHtml
function extractFn(name) {
  const start = html.indexOf('function ' + name);
  if (start < 0) throw new Error('找不到 ' + name);
  let depth = 0, i = html.indexOf('{', start), end = -1;
  for (let j = i; j < html.length; j++) {
    if (html[j] === '{') depth++;
    else if (html[j] === '}') { depth--; if (depth === 0) { end = j + 1; break; } }
  }
  return html.slice(start, end);
}

const code = extractFn('escapeHtml') + '\n' + extractFn('renderMarkdown') + '\nmodule.exports={renderMarkdown};';
fs.writeFileSync('scripts/_md_test.js', code, 'utf8');
const { renderMarkdown } = require('./_md_test.js');

const cases = [
  ['链接', '看这个 [点此搜索](https://s.1688.com/x?kw=abc) 吧'],
  ['粗体', '这是**重点内容**和普通文字'],
  ['表格', '| 优先级 | 关键词 |\n|---|---|\n| 1 | 蓝白格棉衬衫 |\n| 2 | 格纹长袖衬衫男 |'],
  ['标题', '### 结论\n内容在这里'],
  ['裸链接', '访问 https://s.1688.com/abc 看看'],
  ['行内代码', '调用 `plan_search` 工具'],
  ['XSS 防护', '<script>alert(1)</script> 和 <img src=x onerror=alert(1)>'],
  ['javascript 链接', '[恶意](javascript:alert(1))'],
];

for (const [name, input] of cases) {
  const out = renderMarkdown(input);
  console.log(`\n=== ${name} ===`);
  console.log('输入:', input.replace(/\n/g, '\\n'));
  console.log('输出:', out.replace(/\n/g, '\\n'));

  // 安全检查
  if (/<script/i.test(out)) console.log('  ⚠️ 安全警告：含 script 标签');
  if (/javascript:/i.test(out) && /<a /.test(out)) console.log('  ⚠️ 安全警告：含 javascript: 链接');
  if (/onerror=/i.test(out) && /<img/i.test(out)) console.log('  ⚠️ 安全警告：含 onerror');
}