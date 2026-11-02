// 从页面实际渲染结果中提取书签 code 并验证
const fs = require('fs');

const html = fs.readFileSync('app/web/index.html', 'utf8');

// generateBookmarklet 体：从 "return `(function(){" 开始到 "})();`;" 结束
const startMark = 'return `(function(){';
const endMark = '})();`;';
const s = html.indexOf(startMark);
const e = html.indexOf(endMark, s);
if (s < 0 || e < 0) { console.log('未定位到模板'); process.exit(1); }

let body = html.slice(s + 'return `'.length, e + '})();'.length);
console.log('模板体长度:', body.length);

// 替换模板中的 ${...} 占位符（模拟 location.origin）
body = body.replace(/\$\{location\.origin\}/g, 'http://127.0.0.1:8010');
body = body.replace(/\$\{createNew\}/g, 'false');

// 检查是否还有未替换的占位符
const left = body.match(/\$\{[^}]+\}/g);
if (left) console.log('⚠️ 未替换占位符:', left);

try {
  new Function(body);
  console.log('✅ 语法合法');
} catch (err) {
  console.log('❌ 语法错误:', err.message);
  // 逐行尝试定位
  const lines = body.split('\n');
  for (let i = 0; i < lines.length; i++) {
    try { new Function(lines.slice(0, i + 1).join('\n') + '\n});'); }
    catch (x) {}
  }
}

fs.writeFileSync('scripts/_bm_body.js', body, 'utf8');
console.log('已写入 scripts/_bm_body.js 供检查');

// 关键检查
console.log('\n检查:');
console.log('  含 getCurrentTarget:', body.includes('getCurrentTarget'));
console.log('  含 CURRENT_API:', body.includes('CURRENT_API'));
const encoded = 'javascript:' + encodeURIComponent(body);
console.log('  编码后长度:', encoded.length);