# 项目长期记忆

## 设计系统（2026-10-30 重设计，用户选定「买手工作台」方向）
- 前端两个页面：app/web/index.html（看板 `/`）+ app/web/bookmarklet.html（`/bookmarklet`）。
- Token：暖纸底 oklch(97.8% .009 85) + 暖墨 + 赭金单 accent（oklch 48-56% / .15-.17 / 42）；
  衬线标题栈（Noto Serif SC→Songti SC→SimSun）+ 系统正文 + 等宽数字；
  智能体控制台是唯一的深色面板（暖墨 oklch 23%），轨迹用等宽字体。
- 语义色：金=完全同款、赭=等效替代、灰=排除/未核验、绿=已报价成功。
- 布局：左侧固定导航 + 顶栏（目标款切换器）+ 五步流程条 + 左主区四分区 + 右 sticky 智能体控制台。
- 约束：保留全部 JS 函数名/DOM id/API 调用；renderMarkdown 的行内样式依赖
  --accent/--border/--muted 三个 CSS 变量名，改样式时不得删除这三个变量。

## 运行环境
- 依赖（fastapi/uvicorn/playwright）在 D:\miniconda3\python.exe（3.14）；py -3.12、bash 默认 python 均缺。
- 启动：`D:/miniconda3/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`；
  8000 常被用户服务占用（再起会 bind 失败 exit 3），直接复用；main.py 每请求读 html，改文件即生效。
- 环境有 HTTP 代理，服务异常时会出现 502；页面 api() 已做非 JSON 兜底。

## 用户偏好
- 看板榜单要展示真实商品名称和编号，不接受匿名编号。
- 持续通过截图验证结果；反馈具体（如"页面太乱"）。
- 项目定位「全平台全品类」（不止服装），测试数据（airpods 等）保留不删。
