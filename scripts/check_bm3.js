// 正确方式：把整个 <script> 内容在 Node 里执行，取得真实的书签代码
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('app/web/index.html', 'utf8');

// 提取最后一个 <script> 块（主逻辑）
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const mainScript = scripts[scripts.length - 1];
console.log('主脚本长度:', mainScript.length);

// 构造最小 DOM 环境
const store = {};
function makeEl(id) {
  return {
    id, innerHTML: '', value: '', href: '', textContent: '',
    files: [], checked: false,
    classList: { add(){}, remove(){}, contains(){ return false; } },
    style: {},
    onchange: null,
    setAttribute(){}, getAttribute(){ return null; },
  };
}
const sandbox = {
  console,
  location: { origin: 'http://127.0.0.1:8010', href: 'http://127.0.0.1:8010/' },
  document: {
    getElementById(id) { return store[id] || (store[id] = makeEl(id)); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener(){},
    body: { innerText: '' },
    title: 'test',
  },
  fetch: async () => ({ json: async () => ({}) }),
  alert: (m) => console.log('[alert]', m),
  confirm: () => true,
  prompt: () => '',
  navigator: { clipboard: {} },
  encodeURIComponent,
  setTimeout, clearTimeout,
  URL,
};

vm.createContext(sandbox);
try {
  vm.runInContext(mainScript, sandbox);
  console.log('✅ 脚本执行成功');
} catch (e) {
  console.log('❌ 脚本执行失败:', e.message);
  process.exit(1);
}

// 调用 generateBookmarklet
try {
  const code = sandbox.generateBookmarklet(false);
  console.log('\n书签代码长度:', code.length);
  console.log('前100字符:', code.slice(0, 100));

  // 找出含 u00A5 的行
  code.split('\n').forEach((l, i) => {
    if (l.includes('u00A5')) console.log(`第${i+1}行:`, JSON.stringify(l));
  });

  // 验证语法
  try {
    new Function(code);
    console.log('\n✅ 书签代码语法合法');
  } catch (e) {
    console.log('\n❌ 书签语法错误:', e.message);
    fs.writeFileSync('scripts/_bm_generated.js', code, 'utf8');
    console.log('已保存到 scripts/_bm_generated.js');
  }
} catch (e) {
  console.log('调用失败:', e.message);
}