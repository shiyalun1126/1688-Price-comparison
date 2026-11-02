// 逐行追踪括号深度，找出未闭合位置
const fs = require('fs');
const code = fs.readFileSync('scripts/_bm_generated.js', 'utf8');
const lines = code.split('\n');

let braces = 0, parens = 0;

function scan(line) {
  let inStr = null, inComment = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    const prev = line[i - 1];
    const next = line[i + 1];

    if (inStr) {
      if (c === inStr && prev !== '\\') inStr = null;
      continue;
    }
    if (inComment) continue;
    if (c === '/' && next === '/' && !inStr) { inComment = true; continue; }
    if (c === "'" || c === '"' || c === '`') { inStr = c; continue; }

    if (c === '{') braces++;
    if (c === '}') braces--;
    if (c === '(') parens++;
    if (c === ')') parens--;
  }
}

console.log('行号 | 花括号 | 圆括号 | 内容');
lines.forEach((l, i) => {
  scan(l);
  const n = i + 1;
  // 只打印关键行
  if (n <= 10 || n >= 60 || (braces !== 0 && n > 40)) {
    console.log(String(n).padStart(3) + ' | ' + String(braces).padStart(3) + ' | ' + String(parens).padStart(3) + ' | ' + l.slice(0, 90));
  }
});

console.log('\n最终: 花括号=' + braces + ' 圆括号=' + parens);