# V3 沉默唤醒按跟进序列生成任务

- status: ready_for_release
- owner: Codex
- branch: `codex/v3-silence-follow-sequence`
- base_sha: `6060eb8cb46ee0e791fb37096caa9a3b3060a711`
- production_baseline: `ai-paths-unified-20260907-165401-9b8ba028` / `9b8ba02839b0597a708476e848d025f7f75ceeb6`
- implementation_commit: `ea2864b1`

## 目标

- 取消沉默唤醒固定两步、固定 15～20 分钟、固定场景组合和每日 2 计划/4 任务限制。
- 有明确卡点时选择一条外部已发布跟进序列，并按完整节点数 1:1 物化任务。
- 兼容平台合法 `actNNN` 动作编码，禁止静默丢弃节点。
- 夜间非活跃客户顺延到日间；夜间仍活跃客户把完整计划压缩到客户最后消息后的 40 分钟内。
- 每个任务执行前根据最新上下文生成话术，并继续执行 AI 接待、客户回复、退订、人工、订单终态等安全门禁。

## 非目标

- 不改变 V3 公共回复接口、第三方 SOP、B 单延时 Shadow 和交易动作权限。
- 不启用新的自动付款、预约或平台写接口。
- 不以代码关键词判断销售心理、卡点或序列。

## 独占范围

- `ai_paths/app/services/outreach/`
- `ai_paths/app/services/follow_knowledge_client.py`
- `ai_paths/app/services/v3_semantic_router_service.py`（仅平台动作编码兼容）
- `ai_paths/app/services/outreach_prompts.py`（仅节点话术采用合同）
- `ai_paths/app/routers/outreach_admin.py`、`ai_paths/app/workers/supervisor.py`（仅主动唤醒配置与健康状态）
- `ai_paths/app/runtime_services.py`
- 与主动唤醒有关的配置、确定性测试、合同和运行说明

## 不可破坏合同

- 客户状态严格按 `corp_id + wechat + external_userid/customer_id` 隔离。
- 计划和每次发送前均须明确确认 AI 接待状态；人工、未知或上游失败时不发送。
- 客户回复、明确退订、人工接管、已预约/已支付或订单未知时停止剩余触达。
- 同一沉默周期只创建一个有效计划，保持指纹幂等。
- DeepSeek 负责卡点、序列选择和节点文案；代码只负责目录完整性、调度、事实、安全与幂等。

## 计划

1. 修复 Follow Knowledge 对新动作编码和节点完整性的兼容。
2. 将外部跟进序列接入沉默唤醒计划上下文与 worker 依赖。
3. 重构计划合同为“一个序列节点对应一个任务”，保留无卡点主线 fallback。
4. 实现夜间顺延与 40 分钟压缩，补充日志可观测字段。
5. 完成确定性回归、DeepSeek 隔离效果验证和发布检查。

## 风险与回滚

- 最长 11 节点在 40 分钟内触达可能造成较高打扰；保留主动唤醒总开关和逐次安全门禁。
- 外部目录不完整时不得创建残缺序列计划；回退无卡点主线或跳过并记录原因。
- 运行异常时关闭主动唤醒开关；发布异常时恢复上一 clean main release。

## 测试证据

- 本地全仓确定性回归：407 passed；本任务专项：37 passed；Ruff 目标文件通过。
- 第三方只读目录：92/92 条序列可用、0 条无效；522/522 条话术可用；544 个节点、单序列 3～11 节点、28 种动作码。
- 渐进候选覆盖：544/544 节点有候选，0 个空节点；137 个节点有同卡点同动作候选，其余节点由同卡点或语义候选补足。
- DeepSeek 隔离：卡点/无卡点/退订 6/6；28 种动作逐类话术生成 28/28 采用、0 拒绝、0 错误，P50/P95 约 1.68/2.80 秒；所有模型轨迹均为 `deepseek-chat`。
- 夜间调度：92/92 条序列保留完整节点并落在客户最后消息后 40 分钟内，0 失败。
- 所有验证只读取测试知识目录；不使用真实客户身份，不创建生产计划，不写生产数据库，不发送客户消息。

## 发布记录

- 2026-09-07 发布前现场：control/reply/worker 均 active，生产为 clean `9b8ba028`，MySQL；第三方测试知识目录 92 条序列、522 条话术可读。
- 统一 `/opt/ai-paths/.env` 缺少 Follow Knowledge 配置；历史 V3 环境备份保留同一份有效只读凭证和测试域名。部署时先备份当前环境，再恢复这组配置并核对指纹，不在文档或 Git 写入 token。
