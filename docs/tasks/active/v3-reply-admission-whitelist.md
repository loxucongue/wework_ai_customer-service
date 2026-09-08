# v3-reply-admission-whitelist

- status: active
- owner: root
- base_branch: main
- base_sha: `b53e52bf77d092709e48b03245b80ae09e0bdcc5`
- production_verified_at: `2026-09-08T17:47:12+08:00`
- production_releases: `control/reply/worker=ai-paths-unified-20260908-173813-b21228ae@b21228aeacce813cb54ad019ba54ea18e0fbc61f`

## 目标

把 V3 Reply 运行时质量门收敛为明确白名单：结构、素材/图片、收款卡、门店和预约完成态；保留客户明确退订后的停止营销保护。

## 非目标

- 不调整门店匹配算法、素材召回算法或模型供应商。
- 不调用真实模型，不发送客户消息，不执行生产写接口。
- 不把普通销售表达、问题数量、卡点、情绪或暂停状态重新实现成关键词规则。

## Change contract

- type: V3 Reply 运行时合同收敛
- scope: Reply admission、策略一致性、收款结构提示、确定性测试和稳定合同文档
- risk: 取消旧门禁后模型错误表达会直接送达；支付、门店、素材和预约完成态必须保持硬校验
- validation: 专项白名单测试、全仓确定性回归、clean main 发布后健康/路由/日志核验
- rollback: 回滚 control/reply/worker 到发布前统一 release

## 涉及模块与文件所有权

- `ai_paths/app/graph/nodes/reply_admission.py`
- `ai_paths/app/graph/nodes/reply_validation.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/app/graph/nodes/reply_generation.py`
- `ai_paths/app/graph/nodes/reply_contract.py`
- `ai_paths/app/prompts/reply_synthesizer.py`
- Reply 质量门相关测试与合同文档

## 不可破坏合同

- 客户明确退订必须停止营销并记录可审计事实。
- 收款卡不得对已付/声称已付客户重复发送；活动和价格必须在更早对话交付；每人 10 元，只允许 10/20/30/40 元，同轮最多一张。
- 素材、图片和门店结构只能来自本轮合法候选或权威事实。
- 无权威预约事实不得宣称已留位、已约好/预约成功或已排客/排客成功。

## 已确认事实与证据

- 生产基线为 clean `main@b21228ae`，三个后端角色使用统一 release。
- 旧质量门仍会拦截问题数量、ask 无问号、选店后未追问、暂停营销、活动卡点、营业时间、档期和登记完成措辞。

## 已完成

- 已移除上述非白名单运行时拦截和专用安全恢复。
- 已按新口径收敛收款卡和预约完成态校验。
- 已新增白名单专项回归。

## 待办

- 提交并快进主分支。
- 发布三个后端角色并完成 L4 只读核验。
- 更新生产状态、历史索引并关闭任务。

## 测试结果

- 白名单及相关合同：128 passed。
- 全仓：563 passed，1 个第三方弃用 warning。

## 发布与回滚

- 待发布。

## 待沉淀的长期结论

- V3 Reply admission 必须维持显式白名单；新增运行时拦截必须先更新合同和正反例测试。
