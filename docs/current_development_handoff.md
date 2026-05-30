# 当前开发交接说明

更新时间：2026-05-30

## 用途

这份文档用于新对话窗口快速恢复项目上下文。每次继续开发前，先阅读：

- `AGENTS.md`
- `docs/current_development_handoff.md`
- `docs/langgraph_migration_tasks.md`
- `docs/agentic_prompting_and_model_policy.md`
- `docs/langgraph_coze_tool_contracts.md`

## 当前目标

把企业微信医美客服工作流从 Coze 节点编排迁移到本地 Python FastAPI + LangGraph 服务。

Coze 暂时保留为工具层，用于：

- 统一知识库检索
- 项目价格数据库 CRUD
- 项目价格知识库同步
- 部分外部业务 API 过渡

## 当前系统状态

- 本地后端位于 `ai_paths/`。
- 前端测试项目位于 `projects/`。
- FastAPI `/chat` 已兼容前端现有请求形状。
- Next.js `/api/chat` 已可代理到本地 AI Paths 服务。
- LangGraph 已有最小可运行闭环：输入归一化、上下文读取、可选图片理解、多意图规划、工具调用、回复合成、画像/事件提取、trace 日志。
- trace 日志写入 `logs/runs/{request_id}.json`。
- 本地 memory 写入 `logs/memory/{customer_id}.json`。
- Coze OAuth 和统一知识库 wrapper 已存在。
- Platform Agent API wrapper 已接入安全 fallback，但本地未必配置真实 token。

## 当前回复质量重点

客服对外统一自称 `小贝`。旧文档和部分代码里可能还残留 `小美`，后续需要统一清理。

回复原则：

- 先解决客户当前问题。
- 不主动暴露 AI、工具、系统流程。
- 不说 `系统查询到`、`知识库显示`、`工具返回`。
- 不直接说 `转人工`，优先说 `我让专业同事/护理老师协助确认`。
- 有已知事实时先直接使用，不要反复让客户补充。
- 每轮最多追问一个必要问题，售后风险收集除外。
- 不编造营业执照、资质图片、项目价格、疗效承诺。

## 最近压测结论

最新边界压测文件：

- `ai_paths/logs/regression/pressure_edge_cases_1780106068.json`

结果：

- `total: 12`
- `passed: 12`
- `failed: []`

已重点修复：

- 中文关键词乱码导致路由/判断不稳定。
- “营业执照”误触发门店查询或直接承诺发送图片。
- 客户只说脸上斑多少钱时，不再乱拿具体品项价格。
- 竞品比价中必须保留核心价格信息。
- 已知轻度售后情况时，先给护理建议，不再空泛追问。

## 当前 Git/GitHub 状态

GitHub 远程：

```text
git@github.com:loxucongue/wework_ai_customer-service.git
```

本地将以当前源码作为首次基线提交。注意不要提交：

- `.env`
- API token
- 私钥
- `logs/`
- `ai_paths/logs/`
- `projects/assets/` 下的私钥文件
- `node_modules/`
- `.next/`
- 构建压缩包和部署包

## 建议的后续开发顺序

1. 建立 GitHub 首次基线，保证可回滚。
2. 统一 persona：把 `小美` 清理为 `小贝`，同步更新文档与代码。
3. 固化压测集，把最近失败过的场景变成稳定回归测试。
4. 优化全局回复合成：
   - 当前问题优先回答。
   - 多意图最多覆盖 3 个核心业务点。
   - 价格、资质、售后、图片事实不能被模型合成丢失。
5. 接入或验证真实 Platform Agent token 后，再推进预约、门店、订单上下文。
6. 每轮优化后运行语法检查和压测，并更新本交接文档。

## 新窗口恢复流程

新窗口开始时，直接让助手执行：

```text
请先阅读 AGENTS.md 和 docs/current_development_handoff.md，然后检查 git status，继续按交接文档推进下一步。
```

如果之前有未提交改动，先查看 `git status` 和文件 diff，再继续开发。
