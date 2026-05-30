# sf7_price_consult 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 1 |
| 自动通过率 | 4.17% |
| API错误 | 0 |
| 空回复 | 19 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 0 |
| 疑似门店匹配 | 1 |
| 疑似价格/活动 | 3 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 1 |
| 单条过长 | 0 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF7_001 | 水光多少钱 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_002 | 水光靓颜冻干粉套组是什么价格呢 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_003 | 祛斑大概什么价格 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_004 | 光子嫩肤单次多少钱 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_005 | 皮秒价格多少 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_006 | 热玛吉多少钱一次 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_007 | 超声炮贵不贵 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_008 | 有没有新客体验价 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_009 | 我之前做过水光，还能用新客价吗 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_010 | 我做过淡斑，现在想问眼周项目，有新客价吗 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_011 | 老客复购水光多少钱 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_012 | 有没有活动价 | S3_deep_consult | campaign_inquiry | False | likely_price_or_campaign | 你好，我先帮你看看光子嫩肤当前有没有活动价。 目前我这边没有直接查到关于光子嫩肤的有效活动配置。 如果你想了解光子嫩肤的具体价格，或者想看看其他皮肤项目有没有活动，我可以帮你再确认... |
| SF7_013 | 现在五一活动淡斑多少钱 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_014 | 套餐多少钱，买三次有优惠吗 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_015 | 最低多少钱，直接给我底价 | S3_deep_consult | bargain | True |  | 关于水光项目的底价，我需要请同事帮你核实，稍后给你回复~ |
| SF7_016 | 能不能再便宜点 | S3_deep_consult | bargain | False | likely_price_or_campaign | 价格相关的具体优惠信息需要进一步确认，您可以联系相关同事或按流程咨询，有需要随时和我说 |
| SF7_017 | 别家水光199，你们能做到这个价吗 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_018 | 这个价格包含皮肤检测吗 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_019 | 只做半脸是不是便宜点 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_020 | 学生有没有优惠 | S1_icebreaking | campaign_inquiry | False | too_many_questions | 我先帮你看看学生相关的优惠活动。 目前关于学生群体的专属活动，需要看具体的项目和当前的有效配置。你主要想了解哪个项目方向呢？比如皮肤管理、脱毛、或者光电类项目？ |
| SF7_021 | 我未成年，价格会便宜吗 | S1_icebreaking | price_inquiry | False | likely_store_match, likely_price_or_campaign | 关于未成年客户的价格政策，建议您联系对应同事或门店进一步确认哦。 |
| SF7_022 | 这个多少钱 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_023 | 刚刚那个PDRN三文鱼冻干修护套组价格呢 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF7_024 | 先告诉我大概预算范围就行 | S2_profile_collecting | price_inquiry | False | empty_output |  |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
