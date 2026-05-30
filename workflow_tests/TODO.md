# AI客服工作流测试待办

工作流：`7639623828015988742`

## 全局规则

- 所有请求通过环境变量 `COZE_WORKLOAD_API_TOKEN` 鉴权，不把 token 写入文件。
- 2026-05-21 已发现并修复 PowerShell 请求体编码问题；旧 SF1-SF4 测试结果作废，详见 `workflow_tests/reports/ENCODING_INCIDENT.md`。
- 每个子流程至少 20 条用例。
- 每个子流程都要覆盖空历史、短历史、多轮历史三类输入。
- 预约和门店流程也测试，但报告中单独标记“能力未完成/不应误承诺”。
- 目标是尽可能少转人工：只有投诉、退款、严重术后异常、明确人工、强烈议价等才应转人工。

## 子流程进度

- [x] SF1_new_customer_reply：新客首响（UTF-8 重跑完成，需纳入最终汇总）
- [x] SF2_profile_collect：画像收集（UTF-8 重跑完成，需纳入最终汇总）
- [x] SF3_project_consult：项目咨询（UTF-8 重跑完成，需纳入最终汇总）
- [x] SF4_face_consult：面诊咨询（UTF-8 重跑完成，需纳入最终汇总）
- [x] SF5_competitor_response：竞品应对（UTF-8 测试完成，需纳入最终汇总）
- [x] SF6_store_match：门店匹配（UTF-8 测试完成，需纳入最终汇总）
- [x] SF7_price_consult：价格咨询（UTF-8 测试完成，需纳入最终汇总）
- [x] SF8_campaign_push：活动推送（UTF-8 测试完成，需纳入最终汇总）
- [x] SF9_appointment：邀约到店（UTF-8 测试完成，需纳入最终汇总）
- [x] SF10_trust_build：信任建立（UTF-8 测试完成，需纳入最终汇总）
- [x] SF11_emotion_companion：情感陪伴（UTF-8 测试完成，需纳入最终汇总）
- [x] SF12_after_sales：售后服务（UTF-8 测试完成，需纳入最终汇总）
- [x] DIRECT_REPLY：直接标准回复（UTF-8 测试完成，需纳入最终汇总）
- [x] HUMAN_HANDOFF：转人工边界（UTF-8 测试完成，需纳入最终汇总）

## 输出文件约定

- 用例：`workflow_tests/*_cases.json`
- 原始结果：`workflow_tests/results/*_results.json`
- 人工分析报告：`workflow_tests/reports/*_report.md`
