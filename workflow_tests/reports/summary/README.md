# 工作流测试汇总文档索引

本目录用于汇总 `Message_Reply` 工作流测试结论和优化计划。

## 文档

1. `WORKFLOW_TEST_SUMMARY.md`  
   全流程测试总报告，包含整体结论、流程分层、P0/P1/P2 问题和上线建议。

2. `IMMEDIATE_FIX_PLAN.md`  
   立即修复清单，只放现在马上应该改的内容，重点是转人工闭环、外部数据幻觉、内部词/占位符、高风险拦截。

3. `FUTURE_OPTIMIZATION_ROADMAP.md`  
   后续优化路线图，包含报价系统、活动系统、门店库、预约接口、订单接口、知识库扩充、自动化回归和灰度上线策略。

## 建议阅读顺序

1. 先看 `WORKFLOW_TEST_SUMMARY.md` 确认整体问题。
2. 再按 `IMMEDIATE_FIX_PLAN.md` 修 P0/P1。
3. 修完后跑回归测试。
4. 最后按 `FUTURE_OPTIMIZATION_ROADMAP.md` 做配置系统、知识库和接口建设。

## 当前判断

当前工作流不建议直接全自动上线。建议先修复 `HUMAN_HANDOFF`、`SF6`、`SF7`、`SF8`、`SF9`、`DIRECT_REPLY` 的 P0 问题，再进入小流量灰度。
