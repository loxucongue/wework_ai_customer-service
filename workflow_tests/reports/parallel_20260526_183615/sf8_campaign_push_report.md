# sf8_campaign_push 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 11 |
| 自动通过率 | 45.83% |
| API错误 | 0 |
| 空回复 | 5 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 3 |
| 疑似门店匹配 | 3 |
| 疑似价格/活动 | 4 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 3 |
| 单条过长 | 0 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF8_001 | 你们现在有什么活动 | S1_icebreaking | campaign_inquiry | True |  | 您好，我先帮您看一下当前有哪些可以确认的活动信息。 目前需要根据您想了解的具体项目来匹配活动，比如面部抗衰、皮肤管理、眼部或轮廓等方向，您对哪个方面更感兴趣呢？ 您告诉我一个方向，... |
| SF8_002 | 水光最近有活动吗 | S3_deep_consult | campaign_inquiry | True |  | 你好呀，我来帮你看看水光项目近期的活动情况。 根据目前能确认的信息，暂时没有查询到水光项目当前有效的活动配置。 活动需要看具体的项目和当前的有效期，如果你想了解水光或者其他项目，我... |
| SF8_003 | 淡斑活动价多少 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF8_004 | 五一活动现在还有吗 | S1_icebreaking | campaign_inquiry | True |  | 我先按当前能确认的活动信息跟你说。 目前没有直接确认的五一活动信息。活动需要看具体项目和当前有效的配置，如果你想了解某个项目（比如皮肤、抗衰或者某个具体项目）的近期活动，我可以帮你... |
| SF8_005 | 618会有活动吗 | S1_icebreaking | campaign_inquiry | True |  | 关于618的活动，我先按当前能确认的活动信息跟你说。 目前我这边还没有查询到具体的618活动配置。 如果你想了解哪个具体项目的活动，可以告诉我，我帮你再看看。 |
| SF8_006 | 新客有什么福利 | S1_icebreaking | campaign_inquiry | False | likely_appointment_push, likely_price_or_campaign | 你好，新客的福利通常和具体想了解的项目相关，比如首次体验价、到店礼包这些。 目前我这边没有直接匹配到可以确认的新客活动信息。你可以告诉我大概想了解哪个方向的项目吗？比如皮肤管理、抗... |
| SF8_007 | 我是老客，有没有复购活动 | S3_deep_consult | campaign_inquiry | True |  | 感谢你一直以来的支持！关于老客复购活动，我先按当前能确认的活动信息跟你说。 目前查询到的活动信息里，暂时没有专门针对老客复购水光项目的活动。 如果你想了解其他项目的活动，或者需要我... |
| SF8_008 | 我之前做过水光，还能参加水光新客活动吗 | S3_deep_consult | campaign_inquiry | False | too_many_questions | 你好，我之前做过水光，还能参加水光新客活动吗？ 根据你之前做过水光的情况，这类新客活动通常是限首次体验的。 我可以帮你看看当前是否有针对老客户的复购权益或水光相关的其他活动，你需要... |
| SF8_009 | 我做过淡斑，现在想问眼周活动，算新客吗 | S1_icebreaking | campaign_inquiry | True |  | 你好，我先帮你看看眼周项目当前有没有适合的活动。 关于新客资格，是这样的：不同项目大类通常可以分开算。你之前做过淡斑，现在咨询眼周项目，是有机会享受眼周项目的新客活动的。具体要看活... |
| SF8_010 | 学生有没有优惠 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF8_011 | 到店有什么礼品吗 | S4_appointment_negotiating | campaign_inquiry | False | likely_appointment_push, likely_store_match | 关于到店礼品，我这边需要先帮您查询一下当前有效的活动配置。 目前我这边还没有直接能确认的礼品信息。如果您周末方便到店，可以提前和门店同事确认一下，他们可能会有最新的到店礼遇安排。 ... |
| SF8_012 | 有没有买一送一 | S3_deep_consult | campaign_inquiry | False | likely_price_or_campaign | 你好呀，关于水光项目，我先按当前能确认的活动信息跟你说。 目前查询到的活动里，没有关于水光项目的“买一送一”活动。 如果你想了解水光当前是否有其他优惠或体验价，我可以帮你进一步确认... |
| SF8_013 | 套餐活动多少钱 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF8_014 | 朋友介绍有没有优惠 | S1_icebreaking | campaign_inquiry | True |  | 感谢您朋友推荐，我先帮您看看当前有哪些活动可以参与。 目前活动需要根据具体项目来匹配，您朋友有提到对哪个项目比较感兴趣吗？比如皮肤、抗衰或者眼部等项目，我可以帮您看看对应的优惠。 |
| SF8_015 | 这个活动适用哪家门店 | S3_deep_consult | campaign_inquiry | False | likely_store_match | 我先按当前能确认的活动信息跟你说。 关于水光项目，目前没有查到明确标注适用门店的活动配置。 你想了解哪个城市或区域的门店呢？我可以帮你确认一下那边的具体情况。 |
| SF8_016 | 活动什么时候结束 | S3_deep_consult | campaign_inquiry | True |  | 关于水光项目的活动结束时间，我需要根据当前有效的活动配置来确认。 目前我这边缺少可以直接确认的水光活动具体信息。 如果你想了解水光或者其它项目，我可以帮你再具体看看当前有没有合适的... |
| SF8_017 | 活动海报发我看看 | S1_icebreaking | campaign_inquiry | False | too_many_questions | 好的，我这边先帮你看看当前有没有可以确认的活动海报。 目前我这边没有直接能确认的活动海报信息。活动海报通常需要结合具体的项目和您所在的城市来确认。 您是想了解哪个项目方向的活动呢？... |
| SF8_018 | 我今天预约能锁这个活动吗 | S4_appointment_negotiating | campaign_inquiry | False | likely_appointment_push, likely_store_match, likely_price_or_campaign | 好的，我先按当前能确认的活动信息跟你说。 关于水光项目，目前有一个春季焕肤活动，活动价是999元，包含基础水光注射和一次术后修护面膜。 这个活动适用于所有门店，有效期到本月底。需要... |
| SF8_019 | 淡斑新客体验价多少 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF8_020 | 水光活动最低价是多少 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF8_021 | 别家五一活动298，你们有类似的吗 | S3_deep_consult | campaign_inquiry | False | likely_price_or_campaign, too_many_questions | 我先按当前能确认的活动信息跟你说。 光子项目目前的活动信息需要再确认一下。不同品牌、仪器和医生操作的活动价格会有差别，直接和别家比价格可能不太准确。 你想了解的光子具体是侧重改善哪... |
| SF8_022 | 预算低一点，有没有什么福利 | S2_profile_collecting | campaign_inquiry | True |  | 你好，我理解你想在预算内找到合适的福利。 目前我这边没有直接匹配到针对改善毛孔项目的特定福利或活动信息。 如果你想了解某个具体项目方向（比如毛孔清洁、光子嫩肤等）的当前活动，我可以... |
| SF8_023 | 我未成年，可以参加新客活动吗 | S0_wait_icebreak | campaign_inquiry | True |  | 关于未成年能否参加新客活动，需要由同事进一步确认后回复你，你可以稍等一下。 |
| SF8_024 | 我还在哺乳期，能不能先买活动券 | S3_deep_consult | campaign_inquiry | True |  | 关于哺乳期购买活动券的相关问题，我需要和同事确认后给你回复。 |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
