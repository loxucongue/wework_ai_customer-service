# SF8_campaign_push 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 4 |
| 自动通过率 | 16.67% |
| API错误 | 1 |
| 空回复 | 1 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 5 |
| 疑似门店匹配 | 15 |
| 疑似价格/活动 | 8 |
| 疑似过早推荐项目 | 1 |
| 问题超过1个 | 3 |
| 单条过长 | 1 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF8_001 | 你们现在有什么活动 | S1_icebreaking | campaign_inquiry | False | likely_store_match, likely_price_or_campaign | 您好，我这边帮您看看当前有哪些合适的活动。 根据您的信息，目前有一个新客专享的体验活动，具体包含的项目和价格需要根据您选择的门店来确认，活动有效期也请以门店最新信息为准。 您想了解... |
| SF8_002 | 水光最近有活动吗 | S3_deep_consult | campaign_inquiry | False | likely_store_match | 亲，您好呀！最近确实有水光针相关的活动呢。 目前我们有一个【春日焕肤水光礼遇】活动，主要是针对改善干燥和提亮肤色的。这个活动需要到我们指定的几家门店参与，有效期到本月底。具体的适用... |
| SF8_003 | 淡斑活动价多少 | S3_deep_consult | price_inquiry | False | likely_store_match, likely_price_or_campaign | 目前系统暂未查到淡斑的具体活动价，价格会根据项目细节、门店活动等有所不同哦。你可以先确认下具体想了解的淡斑项目类型，比如是激光淡斑还是其他方式，我再帮你核对更准确的信息~ |
| SF8_004 | 五一活动现在还有吗 | S1_icebreaking | campaign_inquiry | False | likely_appointment_push, likely_store_match | 您好，我来帮您查询一下五一期间的活动安排。 根据当前信息，我们五一期间有推出【新客专享体验礼包】，适用于首次到店的新客朋友，包含了几个热门项目的体验机会。不过具体的适用门店和有效期... |
| SF8_005 | 618会有活动吗 | S8_silent_return | campaign_inquiry | False | likely_store_match, too_many_questions | 亲，关于618的活动，我这边帮您查一下哦。 目前我们有几个活动在进行，比如新客专享的体验礼包，还有针对一些特定项目的优惠。具体的适用条件、参与门店和有效期，我需要根据您的情况来帮您... |
| SF8_006 | 新客有什么福利 | S1_icebreaking | campaign_inquiry | False | likely_store_match, too_many_questions | 您好呀，欢迎第一次咨询我们~ 新客的专属福利我帮您看看。 根据系统信息，目前有新客专享的体验活动。具体的活动内容、适用门店和有效期，我需要为您确认一下最新详情。 您方便告诉我您对哪... |
| SF8_007 | 我是老客，有没有复购活动 | S7_dealed_active | campaign_inquiry | False | likely_store_match | 您好呀，欢迎回来！老客复购我们一直有专属的福利呢。 目前针对老客复购，有一个“老朋友专享回馈”活动，适用于之前消费过的项目进行再次治疗或升级。具体门店和有效期需要根据您所在的城市和... |
| SF8_008 | 我之前做过水光，还能参加水光新客活动吗 | S7_dealed_active | campaign_inquiry | True |  | 您好呀，新客活动通常是为首次体验某个项目准备的哦。您之前做过水光，可能就不符合水光新客活动的条件了呢。 不过别担心，我们还有其他适合老客的活动。我帮您查一下当前针对水光项目的其他优... |
| SF8_009 | 我做过淡斑，现在想问眼周活动，算新客吗 | S7_dealed_active | campaign_inquiry | True |  | 您之前在我们这做过淡斑项目，已经是老顾客啦~ 关于眼周的活动，我这边看到目前有针对老顾客的复购专属权益。 具体的适用项目、优惠内容和有效期，我帮您详细查一下，确认是否符合您的需求，... |
| SF8_010 | 学生有没有优惠 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign | 目前系统里没有查到皮肤管理相关的优惠信息哦，你可以具体说下想了解哪个项目，我帮你确认下适用的价格~ |
| SF8_011 | 到店有什么礼品吗 | S4_appointment_negotiating | campaign_inquiry | False | likely_appointment_push, likely_store_match | 您好，关于到店礼品，我们这边需要根据您到访的具体门店和您的会员身份来确认。 目前我们系统里没有查询到您所问门店的通用到店礼信息。不过，您可以告诉我您计划去哪个门店，以及您是新客还是... |
| SF8_012 | 有没有买一送一 | S3_deep_consult | campaign_inquiry | False | likely_store_match | 您好呀，我帮您查一下我们近期的活动。 根据您之前咨询水光项目的情况，我这边看到目前有一个相关的优惠活动。 具体的活动内容、适用门店和有效期，我需要为您查询确认一下。您方便告诉我您所... |
| SF8_013 | 套餐活动多少钱 | S3_deep_consult | price_inquiry | False | likely_store_match, likely_price_or_campaign | 我直接按系统价给你说哈，光子嫩肤的价格需要按具体项目和门店活动确认，目前未查到明确的套餐活动价。你可以先提供下预算偏好，我帮你看看是否匹配合适的方案~ |
| SF8_014 | 朋友介绍有没有优惠 | S1_icebreaking | campaign_inquiry | True |  | 朋友推荐来的呀，欢迎欢迎！我们最近确实有针对新朋友的专属礼遇。 不过具体的优惠活动，需要根据您想了解的项目和您所在的城市来匹配。方便告诉我您想咨询哪个项目，以及您在哪个城市吗？我帮... |
| SF8_015 | 这个活动适用哪家门店 | S3_deep_consult | campaign_inquiry | False | likely_store_match | 好的，我帮您查一下这个水光活动的适用门店。 根据现有信息，这个活动目前支持XX店和YY店参与。活动有效期到[请插入campaign_info中的有效期]，新老客都可以参加。 您方便... |
| SF8_016 | 活动什么时候结束 | S3_deep_consult | campaign_inquiry | False | likely_store_match | 亲~ 不同活动的有效期不一样的，我帮你具体看看。 以我们最近的水光活动为例，是到[活动结束日期]截止的，适用于[适用门店]门店。 具体的适用条件和名额，我需要帮你查一下系统确认最准... |
| SF8_017 | 活动海报发我看看 | S1_icebreaking | campaign_inquiry | False | likely_store_match | 好的，这就把活动海报发您看看。 这是我们目前主推的【新客专享礼包】，包含一次皮肤检测和基础清洁体验，所有门店都适用，到本月底结束。 您看下海报内容，如果感兴趣的话，我帮您看看是否符... |
| SF8_018 | 我今天预约能锁这个活动吗 | S3_deep_consult | campaign_inquiry | False | likely_appointment_push | 您好，关于活动预约锁定，需要根据您具体想参与的活动和预约时间来看呢。 目前我们这边显示有水光针的体验活动，具体适用条件和有效期需要为您查询确认一下。 您方便告诉我一下您想预约的大概... |
| SF8_019 | 淡斑新客体验价多少 | S1_icebreaking | price_inquiry | False | likely_price_or_campaign | 淡斑项目的新客体验价需要按系统配置确认，我先给你参考目前系统里的基础价哦~你可以说说更关注的改善问题（比如淡斑的面积、频次）或预算范围，我帮你看看更贴合的方案~ |
| SF8_020 | 水光活动最低价是多少 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign, too_many_questions, too_long_single_message | 我目前没法给你具体的水光活动最低价金额哦。价格需要结合项目具体类型、系统配置来确认，你可以先告诉我具体想了解水光哪个项目方向吗？或者你的预算大概在什么范围？我可以帮你参考更合适的方... |
| SF8_021 | 别家五一活动298，你们有类似的吗 | S3_deep_consult | campaign_inquiry | False | likely_appointment_push, likely_store_match, likely_price_or_campaign | 您好呀，我这边马上帮您看看咱们五一期间的活动哦。 目前我们有一个【新客光子嫩肤体验活动】，价格和您说的比较接近。这个活动主要面向首次到店的新客，在XX门店和YY门店都可以用，有效期... |
| SF8_022 | 预算低一点，有没有什么福利 | S3_deep_consult | campaign_inquiry | False | likely_appointment_push, likely_store_match, likely_price_or_campaign, likely_over_recommend | 您好，理解您想找一些预算友好的福利。我这边帮您看看当前有哪些合适的活动。 根据您之前提到的想改善毛孔，目前有一个【新客毛孔体验礼包】可能比较适合。这个礼包包含一次毛孔清洁体验和一次... |
| SF8_023 | 我未成年，可以参加新客活动吗 | S0_wait_icebreak | human_request | True |  | 您好，我是您的专属客服，很高兴为您服务。有什么可以帮您的吗？ |
| SF8_024 | 我还在哺乳期，能不能先买活动券 |  |  | False | api_error, empty_output |  |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
