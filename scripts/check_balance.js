// 检查生成的书签代码括号平衡
const fs = require('fs');
const code = fs.readFileSync('scripts/_bm_generated.js', 'utf8');

let braces = 0, parens = 0, brackets = 0;
let inStr = null, inComment = false;

for (let i = 0; i < code.length; i++) {
  const c = code[i];
  const prev = code[i - 1];
  const next = code[i + 1];

  if (inStr) {
    if (c === inStr && prev !== '\\') inStr = null;
    continue;
  }
  if (inComment) {
    if (c === '\n') inComment = false;
    continue;
  }
  if (c === '/' && next === '/') { inComment = true; continue; }
  if (c === "'" || c === '"' || c === '`') { inStr = c; continue; }

  if (c === '{') braces++;
  if (c === '}') braces--;
  if (c === '(') parens++;
  if (c === ')') parens--;
  if (c === '[') brackets++;
  if (c === ']') brackets--;
}

console.log('花括号平衡:', braces, '(应为0)');
console.log('圆括号平衡:', parens, '(应为0)');
console.log('方括号平衡:', brackets, '(应为0)');
console.log('未闭合字符串:', inStr);

const lines = code.split('\n');
console.log('\n总行数:', lines.length);
console.log('\n=== 最后 25 行 ===');
lines.slice(-25).forEach((l, i) => {
  console.log((lines.length - 25 + i + 1) + ': ' + l);
});