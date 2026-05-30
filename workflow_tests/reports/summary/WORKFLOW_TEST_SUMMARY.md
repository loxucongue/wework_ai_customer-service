# AI客服工作流测试总报告

测试对象：`Message_Reply` 工作流  
工作流 ID：`7639623828015988742`  
测试范围：`SF1-SF12`、`DIRECT_REPLY`、`HUMAN_HANDOFF`  
测试时间：2026-05-21 至 2026-05-22  

## 结论

当前工作流已经具备基础对话能力，项目咨询、图片咨询、信任建立、售后护理等普通场景可以生成可用回复。但距离可上线还有明显差距，核心问题不是接口不可用，而是高风险分支、外部数据依赖、兜底逻辑和知识库边界没有闭环。

目前最需要先修的是：

- `HUMAN_HANDOFF` 没有真正接住人工分支，投诉、退款、明确要人工、未成年、孕期、强议价等大量落入欢迎语兜底。
- `SF6` 门店匹配、`SF8` 活动推送存在大量编造信息和模板占位符输出。
- `SF7` 价格咨询没有有效报价结果时暴露“系统价/系统配置”等内部词。
- `SF9` 预约流程在没有预约接口结果时仍有直接确认、取消、定金承诺等风险。
- 主路由对当前消息的强约束还不够，部分信任、竞品、图片、短确认和高风险输入被历史上下文带偏。

在这些问题修复前，不建议开放全自动对客回复；可以先用于内部灰度和人工旁路审核。

## 测试结果数据汇总

本轮汇总只纳入 UTF-8 修复后的有效测试结果。早期乱码测试结果已废弃，仅保留编码事故追溯。

### 总体数据

| 指标 | 数值 |
|---|---:|
| 测试流程数量 | 14 |
| 总测试用例数 | 336 |
| API/执行异常 | 1 |
| 空回复 | 1 |
| 图片消息输出流程 | SF3、SF5、SF9、SF10、SF11 |
| 主要风险集中流程 | HUMAN_HANDOFF、SF6、SF7、SF8、SF9、DIRECT_REPLY |

说明：

- API 层整体稳定，绝大多数问题发生在路由、分支承接、外部数据依赖和回复生成层。
- `SF8_campaign_push` 出现 1 条执行/空回复异常，后续回归需要重点复测强风险活动咨询。
- “欢迎语兜底”主要指输出类似“您好，我是专属客服，有什么可以帮您”的泛用回复，通常代表路由或子流程承接失败。

### 各流程结果数据

| 流程 | 用例数 | API异常 | 空回复 | 图片用例 | 欢迎语兜底 | 实际 intent 分布 |
|---|---:|---:|---:|---:|---:|---|
| SF1 新客首响 | 24 | 0 | 0 | 0 | 1 | project_inquiry:10；greeting:9；emotion_chat:3；campaign_inquiry:1；pre_visit_question:1 |
| SF2 画像收集 | 24 | 0 | 0 | 0 | 1 | project_inquiry:12；emotion_chat:5；trust_issue:3；human_request:1；appointment_intent:1；after_sales:1；store_inquiry:1 |
| SF3 项目咨询 | 24 | 0 | 0 | 1 | 0 | project_inquiry:23；trust_issue:1 |
| SF4 面诊图片咨询 | 24 | 0 | 0 | 0 | 0 | image_inquiry:13；project_inquiry:8；emotion_chat:3 |
| SF5 竞品应对 | 24 | 0 | 0 | 4 | 2 | competitor_compare:9；trust_issue:6；project_inquiry:3；price_inquiry:2；bargain:2；emotion_chat:1；campaign_inquiry:1 |
| SF6 门店匹配 | 24 | 0 | 0 | 0 | 0 | store_inquiry:19；appointment_intent:2；project_inquiry:2；appointment_change:1 |
| SF7 价格咨询 | 24 | 0 | 0 | 0 | 2 | price_inquiry:20；bargain:2；competitor_compare:1；campaign_inquiry:1 |
| SF8 活动推送 | 24 | 1 | 1 | 0 | 4 | campaign_inquiry:17；price_inquiry:5；human_request:1；空 intent:1 |
| SF9 邀约到店 | 24 | 0 | 0 | 1 | 3 | appointment_intent:12；appointment_cancel:3；pre_visit_question:2；appointment_confirm:2；appointment_change:2；trust_issue:1；price_inquiry:1；store_inquiry:1 |
| SF10 信任建立 | 24 | 0 | 0 | 19 | 2 | trust_issue:21；complaint_refund:1；image_inquiry:1；human_request:1 |
| SF11 情感陪伴 | 24 | 0 | 0 | 2 | 2 | emotion_chat:13；price_inquiry:2；trust_issue:2；complaint_refund:2；project_inquiry:2；appointment_cancel:2；after_sales:1 |
| SF12 售后服务 | 24 | 0 | 0 | 0 | 3 | after_sales:17；project_inquiry:3；complaint_refund:3；emotion_chat:1 |
| DIRECT_REPLY | 24 | 0 | 0 | 0 | 6 | emotion_chat:10；appointment_cancel:4；pre_visit_question:4；store_inquiry:2；appointment_change:1；silence_return:1；appointment_confirm:1；price_inquiry:1 |
| HUMAN_HANDOFF | 24 | 0 | 0 | 0 | 15 | human_request:6；complaint_refund:5；after_sales:4；trust_issue:3；bargain:3；emotion_chat:1；project_inquiry:1；appointment_intent:1 |

### 数据解读

1. `HUMAN_HANDOFF` 的欢迎语兜底最高，24 条里 15 条出现泛用欢迎语，是当前最大 P0。
2. `DIRECT_REPLY` 的欢迎语兜底也偏高，说明短确认和到店前标准问题没有被专门承接。
3. `SF6` 虽然没有欢迎语兜底，但主要问题是编造门店事实，不能只看空回复或 API 成功率。
4. `SF8` 同时存在执行异常、空回复、欢迎语兜底和活动编造，属于外部配置型高风险流程。
5. `SF10` 图片输出最多，说明图库链路基本可用，但需要控制每轮图片数量和资料匹配度。
6. `SF4` 只有 13/24 被识别为 `image_inquiry`，说明图片/看图意图识别还不够硬。
7. `SF5` 只有 9/24 被识别为 `competitor_compare`，竞品强识别规则需要加强。

## 流程表现分层

### 相对可用，需局部优化

| 流程 | 结论 |
|---|---|
| SF1 新客首响 | 基础打招呼稳定，但第二轮容易过早推荐项目。 |
| SF3 项目咨询 | 能回答多数项目问题，但知识库覆盖不足，偶尔暴露内部词。 |
| SF4 面诊图片咨询 | 安全边界整体可控，但图片输入使用不稳定，回复偏长。 |
| SF10 信任建立 | 整体最好之一，能发送图片资料，但承诺感偏强、图片过多。 |
| SF11 情感陪伴 | 轻情绪能承接，但投诉/催到店不满仍会兜底失败。 |
| SF12 售后服务 | 常规护理和严重异常边界较好，但退款投诉、订单记录、产品使用仍不足。 |

### 暂不建议上线，需要优先修复

| 流程 | 主要问题 |
|---|---|
| SF2 画像收集 | 高风险人群没有稳定转人工，画像补充容易被业务流程抢走。 |
| SF5 竞品应对 | 竞品意图识别不稳，强压价边界失败，存在内部词和无依据承诺。 |
| SF6 门店匹配 | 大量编造门店、地址、营业时间、停车、地图链接。 |
| SF7 价格咨询 | 没有实际价格输出，内部词暴露，强议价/未成年边界失败。 |
| SF8 活动推送 | 大量编造活动、有效期、门店、礼包，出现模板占位符。 |
| SF9 邀约到店 | 预约接口未闭环，存在直接确认、取消、定金承诺和地址编造。 |
| DIRECT_REPLY | 到店前问题大量欢迎语兜底，短确认上下文理解不稳。 |
| HUMAN_HANDOFF | 最严重，强制转人工场景大量失败。 |

## P0 问题

### 1. 转人工闭环失败

命中明确人工、投诉、退款、维权、曝光、强烈不满、强议价、孕期/哺乳期/未成年、慢病、严重过敏、自伤风险、病例/异常伤口图片时，系统必须进入 `HUMAN_HANDOFF` 或专业人工承接。

当前测试中，大量输入被输出成“您好，我是专属客服，有什么可以帮您”。这是不可上线问题。

### 2. 门店/预约/活动/价格数据被模型编造

门店名称、地址、营业时间、停车、地铁、地图链接、门店数量、活动名称、活动权益、活动有效期、适用门店、价格金额、定金规则、预约成功/取消成功，必须来自外部数据或配置结果。

当前 `SF6`、`SF8`、`SF9` 风险最高，`SF7` 也存在客户侧内部表达问题。

### 3. 模板占位符直接输出

测试中出现过：

- `[请根据store_info填充...]`
- `[活动结束日期]`
- `[适用门店]`
- `XX店`
- `XX市XX区XX路XX号`

这类内容必须在所有子流程中硬禁。

### 4. 医疗/合规高风险优先级不够

孕期、哺乳期、未成年、慢病、严重过敏、视力异常、呼吸异常、异常伤口、病例报告、自伤风险，都必须覆盖在主路由最前置规则中，并且下游节点不能覆盖这个结果。

## P1 问题

### 1. 当前消息优先级仍需加强

当前消息是“你们公司正规吗”时，不应被历史项目推荐带偏。当前消息是“收到/可以/谢谢”时，应结合上一轮助手动作，而不是从历史里抽项目/价格意图。

### 2. 子流程边界不够稳定

- 新客第二轮过早进入项目推荐。
- 画像补充被门店/预约抢走。
- 看图意图被项目咨询抢走。
- 竞品比价和强砍价边界不清。
- 情绪场景被价格/项目节点抢走后缺少情绪承接。

### 3. 回复风格和拆分需要统一

部分流程单条消息过长，或使用“好哒、呀、～、emoji”等偏轻松表达。企微医美客服建议稳定、简短、专业，每轮 1-3 条，每条只讲一个意思。

## P2 问题

### 1. 知识库覆盖不足

项目知识库需要补齐光子嫩肤原理、PDRN、水光、小气泡、淡斑风险、恢复期、常见对比等。售后知识库需要补齐洗脸、化妆、运动、饮酒、防晒、修复产品等。

### 2. 资料匹配度需要提高

信任建立中图片资料要和客户顾虑一一匹配。客户怕不自然，不应优先发术后护理图；客户问正规性，优先发资质；问产品真假，优先发产品溯源。

### 3. 测试集可以继续扩展

当前每个流程已覆盖至少 20 条以上，但后续需要按真实客服话术继续扩展长历史、多轮纠错、同一客户跨阶段、带图片、带外部接口结果的回归集。

## 建议上线策略

### 不建议直接上线全自动

当前不适合直接面向真实客户全自动回复，主要原因是转人工和外部数据依赖没有闭环。

### 可灰度上线的前提

至少完成以下修复后，可以考虑小流量灰度：

1. `HUMAN_HANDOFF` 独立节点可用，强制人工场景 100% 命中。
2. 门店、活动、价格、预约没有数据时不编造。
3. 模板占位符、内部词被全局禁止。
4. 高风险人群和严重售后异常能稳定拦截。
5. 使用回归用例重新跑一轮，P0 用例全部通过。

## 相关报告

- `workflow_tests/reports/sf1_new_customer_reply_utf8_manual_report.md`
- `workflow_tests/reports/sf2_profile_collect_utf8_manual_report.md`
- `workflow_tests/reports/sf3_project_consult_utf8_manual_report.md`
- `workflow_tests/reports/sf4_face_consult_utf8_manual_report.md`
- `workflow_tests/reports/sf5_competitor_response_utf8_manual_report.md`
- `workflow_tests/reports/sf6_store_match_utf8_manual_report.md`
- `workflow_tests/reports/sf7_price_consult_utf8_manual_report.md`
- `workflow_tests/reports/sf8_campaign_push_utf8_manual_report.md`
- `workflow_tests/reports/sf9_appointment_utf8_manual_report.md`
- `workflow_tests/reports/sf10_trust_build_utf8_manual_report.md`
- `workflow_tests/reports/sf11_emotion_companion_utf8_manual_report.md`
- `workflow_tests/reports/sf12_after_sales_utf8_manual_report.md`
- `workflow_tests/reports/direct_reply_utf8_manual_report.md`
- `workflow_tests/reports/human_handoff_utf8_manual_report.md`
