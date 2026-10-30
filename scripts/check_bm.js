// 从 index.html 提取 generateBookmarklet，模拟生成书签并验证语法
const fs = require('fs');

const html = fs.readFileSync('app/web/index.html', 'utf8');

// 提取函数体
const start = html.indexOf('function generateBookmarklet');
if (start < 0) { console.log('未找到函数'); process.exit(1); }

// 找到匹配的结束大括号
let depth = 0, i = html.indexOf('{', start), end = -1;
for (let j = i; j < html.length; j++) {
  if (html[j] === '{') depth++;
  else if (html[j] === '}') { depth--; if (depth === 0) { end = j + 1; break; } }
}
const fnSrc = html.slice(start, end);
fs.writeFileSync('scripts/_bm_fn.js', fnSrc + '\nmodule.exports = generateBookmarklet;\n');

// 模拟 location.origin
global.location = { origin: 'http://127.0.0.1:8010' };
const generateBookmarklet = require('./_bm_fn.js');

const code = generateBookmarklet(false);
console.log('生成代码长度:', code.length);

// 验证：去掉 javascript: 前缀后能否被解析为合法 JS
try {
  new Function(code);
  console.log('✅ 语法合法');
} catch (e) {
  console.log('❌ 语法错误:', e.message);
  // 打印出错附近的代码
  console.log('--- 代码片段 ---');
  console.log(code.slice(0, 500));
}

const encoded = 'javascript:' + encodeURIComponent(code);
console.log('书签 URL 长度:', encoded.length);
if (encoded.length > 60000) console.log('⚠️ 书签过长，可能被浏览器截断');

// 检查关键部分
console.log('\n检查要点:');
console.log('  含 getCurrentTarget:', code.includes('getCurrentTarget'));
console.log('  含 CURRENT_API:', code.includes('CURRENT_API'));
console.log('  没有硬编码 target id:', !/target_style_id:'[0-9a-f]{20,}'/.test(code));