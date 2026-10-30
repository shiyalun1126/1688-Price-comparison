# 1688 比价智能体（以款找货源）

服装场景的「以款找货源 + 比价」智能体。录入目标款（图片/文字描述），在 1688 全网召回候选店铺，做同款/等效替代分层，逐家询价，最后给出比价结论、采购建议与议价话术。

## 定位

- **小批量买家**：同款谁便宜，横向比价。
- **大宗进货方**：拿有条件报价，判断谈判空间，给出议价建议与话术。

## 核心流程

```
录入目标款（图/文字）
   → 书签脚本从 1688 商品页一键导入候选商品（标题/主图/价格/店铺）
   → 双通道匹配（多模态视觉比对为主 + 属性核验兜底）
   → 分层：完全同款（已核验）/ 等效替代（未核验，需声明）/ 已排除
   → 半自动询价（智能体出话术 → 人发送 → 智能体解析报价）
   → 比价 + 采购建议 + 议价话术
```

## 功能特性

- **视觉同款匹配**：调用 deepseek-flash 多模态模型比对目标款与候选商品图，针对服装拍摄差异（模特/挂拍/平铺、角度、水印）做过判定调优；「看不到细节」不武断判否，只有明确矛盾才排除
- **分层可比**：完全同款（exact）才进硬比价，等效替代（equivalent）标注参考，已排除/未核验不参与
- **增量匹配**：已判定商品复用结果，只算新商品；视觉比对 8 线程并发
- **半自动询价**：LLM 生成带属性核验问题的询价话术，人发送后贴回卖家回复，自动解析结构化报价（价格/MOQ/含税/阶梯价），LLM 失败自动降级正则
- **比价结论**：标价与报价归一合并（报价优先），给出综述、采购建议、议价话术（LLM 生成，规则兜底）
- **ReAct 智能体**：DeepSeek function calling 驱动，5 个业务工具自主规划（看目标款 → 匹配 → 比价 → 出话术 → 汇总），推理轨迹前端可见
- **书签导入**：在 1688 商品页点书签即抓取入库，无需手动录入
- **离线降级**：无 DEEPSEEK_API_KEY 时话术/报价解析/议价全部走规则兜底，链路可跑通

## 技术栈

- Python 3.11+ / FastAPI + uvicorn
- LLM：DeepSeek（deepseek-chat 文本 / deepseek-flash 视觉，OpenAI 兼容协议，可插拔）
- 数据：SQLite（标准库 sqlite3，无 ORM），可平滑迁移 PostgreSQL
- 前端：FastAPI 返回的轻量 HTML 看板（单页 4 Tab：首页/导入/教程/智能体）

## 目录结构

```
app/
  models/        数据模型（Pydantic）+ 状态枚举
  store/         数据存取（SQLite，含 schema 迁移）
  matcher/       匹配引擎：vision.py 视觉比对 / matcher.py 双通道+分层 / image.py pHash
  inquire/       询价话术生成 + 报价解析（LLM 优先，正则兜底）
  comparison/    比价 + 建议 + 议价话术
  llm/           LLM 客户端（DeepSeek 可插拔）
  agent/         ReAct 智能体（react.py 循环 / tools.py 工具集 / service.py 组装）
  api/           FastAPI 路由（REST + /api/agent/run）
  web/           看板模板 + 书签/调试页
scripts/         工具与测试脚本（种子数据、pHash 校准、视觉实测、书签 JS、E2E 冒烟）
data/            SQLite 库、上传图、测试图
```

## 快速开始

```bash
pip install -e ".[dev]"
cp .env.example .env   # 填入 DEEPSEEK_API_KEY（不配则走规则兜底）
python -m app
```

打开 http://127.0.0.1:8000

- 首页：目标款管理、候选商品、匹配、询价、比价
- 书签页：`/bookmarklet`（拿「一键导入」书签，去 1688 商品页点击即入库）
- 智能体 Tab：输入目标（如「帮我比一下卫衣哪家便宜」），看自主推理轨迹

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/targets` | 目标款列表 / 创建 |
| POST | `/api/targets/from-url` | 书签从 URL 创建目标款 |
| POST | `/api/import-product` | 书签导入候选商品（自动建店铺） |
| POST | `/api/targets/{id}/match` | 视觉匹配（默认增量，`?incremental=false` 强制全量） |
| GET/POST | `/api/targets/{id}/inquiries`、`/api/inquiries` | 询价任务 |
| POST | `/api/inquiries/{id}/parse-reply` | 解析卖家回复 → 报价 |
| GET | `/api/targets/{id}/quotes` | 报价列表 |
| POST | `/api/targets/{id}/comparison` | 生成比价结论 |
| POST | `/api/agent/run` | ReAct 智能体执行任务 |

## 当前进度

全链路已通并在真实数据上验证：目标款 → 书签导入 → 视觉匹配 → 分层 → 询价 → 比价 → 智能体。

当前库内数据：4 个目标款、5 家店铺、39 个候选商品、多条报价与比价结论（详见 `data/app.db`，库与上传图不入 git）。

后续方向：

- 1688 搜索 API 直连召回（替代纯书签导入）
- 报价可比性六维归一（MOQ/含税/运费/阶梯价）的 UI 呈现
- PostgreSQL 迁移与多用户
- 匹配判定的人工纠正反馈回路
