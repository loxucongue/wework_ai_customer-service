# SF7_price_consult 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 3 |
| 自动通过率 | 12.5% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 8 |
| 疑似门店匹配 | 2 |
| 疑似价格/活动 | 20 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 2 |
| 单条过长 | 2 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF7_001 | 水光多少钱 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign, too_many_questions | 水光的价格会根据具体项目品类、产品、剂量等因素不同哦，我先帮你确认下具体项目方向？你是想改善干燥暗沉，对吗？ |
| SF7_002 | 水光靓颜冻干粉套组是什么价格呢 | S3_deep_consult | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 这个套组的具体价格需要按系统配置参考，但因为项目要结合你的皮肤情况和系统报价，建议你到店后让顾问详细确认~ |
| SF7_003 | 祛斑大概什么价格 | S1_icebreaking | price_inquiry | False | likely_price_or_campaign, too_long_single_message | 我直接按系统价给你说哈，祛斑价格要看具体项目类型、部位和皮肤情况哦，现在系统里没有明确的价格参考呢。你要是有具体想了解的改善方向或预算偏好，可以和我说下，我看看有没有合适的方案推荐... |
| SF7_004 | 光子嫩肤单次多少钱 | S1_icebreaking | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 光子嫩肤单次的具体价格需要按系统配置和你想改善的问题（比如毛孔暗沉的具体情况）来确认哈，我先按系统当前的单次价给你参考下~你要是想了解更详细的，到店可以让顾问帮你具体看方案。 |
| SF7_005 | 皮秒价格多少 | S1_icebreaking | price_inquiry | False | likely_price_or_campaign | 我先按当前系统价给你参考哈，具体是否符合新客价要再确认，皮秒价格和项目部位、皮肤情况等有关，你可以先说说你的具体需求，我帮你看看更合适的方案~ |
| SF7_006 | 热玛吉多少钱一次 | S3_deep_consult | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 热玛吉的单次价格需要按系统配置和项目大类确认，我先按系统价给你参考，具体是新客价还是老客价要到店再核实哦。如果你想了解更详细的项目方向或预算相关的，可以和我说下你的具体情况~ |
| SF7_007 | 超声炮贵不贵 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign | 超声炮的价格会根据项目部位、剂量等有所不同，我先按系统价给你参考~具体是否符合新客价/老客价需要再确认，如果你预算方面有考虑，也可以和我说说你的偏好哦。 |
| SF7_008 | 有没有新客体验价 | S1_icebreaking | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 新客体验价具体要看系统配置和项目详情哦，你这次是首次了解水光项目，具体价格我先帮你确认下系统信息，或者你可以到店后让顾问详细给你介绍项目包含内容和价格~ 你主要是想改善什么皮肤问题... |
| SF7_009 | 我之前做过水光，还能用新客价吗 | S7_dealed_active | price_inquiry | False | likely_price_or_campaign | 新客价是该品类首次体验用的，你之前做过水光项目，这次就不能再按新客价走啦。具体价格要按系统配置确认，你可以说说这次想改善的具体问题吗？ |
| SF7_010 | 我做过淡斑，现在想问眼周项目，有新客价吗 | S7_dealed_active | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 眼周项目属于新的项目方向，你之前做过淡斑，这次可以按新客价参考。具体价格需要以系统配置为准，到店后顾问会详细给你介绍，你主要想改善眼周的什么问题呢？ |
| SF7_011 | 老客复购水光多少钱 | S7_dealed_active | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 老客复购水光的具体价格需要按系统配置确认，你之前做过水光，属于该品类老客。具体还是要到店让顾问帮你核对，比如单次用量、产品类型等。你想了解的是单次水光还是有其他项目需求呢？ |
| SF7_012 | 有没有活动价 | S3_deep_consult | campaign_inquiry | False | likely_store_match, too_many_questions | 您好呀，您是想了解光子嫩肤相关的活动，对吗？ 我帮您查了一下，目前我们有一个针对新朋友的体验活动，具体包含的项目和适用门店，我需要再跟您确认一下是否符合条件。 您方便告诉我您之前是... |
| SF7_013 | 现在五一活动淡斑多少钱 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign | 五一活动淡斑的价格需要按系统配置确认，目前没有查到具体活动价。你可以确认下淡斑的具体项目类型，或者告诉我预算范围，我帮你看看是否有合适的方案。 |
| SF7_014 | 套餐多少钱，买三次有优惠吗 | S3_deep_consult | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 目前系统里没有具体的套餐和三次优惠信息呢，水光的单次价格和卡项优惠建议到店让顾问根据你的情况介绍哦~ 你可以说说预算或偏好，我帮你看看更适合的方式~ |
| SF7_015 | 最低多少钱，直接给我底价 | S3_deep_consult | bargain | True |  | 您好，我是您的专属客服，很高兴为您服务。请问有什么可以帮您？ |
| SF7_016 | 能不能再便宜点 | S3_deep_consult | bargain | True |  | 您好，我是您的专属客服，很高兴为您服务。请问有什么可以帮您的？ |
| SF7_017 | 别家水光199，你们能做到这个价吗 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign | 你多对比一下是对的，医美项目确实不能只看一个报价。主要要看产品、剂量、部位、操作人员和后续护理是不是一样。 要不你先确认下对方报价里水光是哪个类型的产品，具体剂量、操作部位是怎么安... |
| SF7_018 | 这个价格包含皮肤检测吗 | S3_deep_consult | price_inquiry | False | likely_appointment_push, likely_price_or_campaign | 皮肤检测是否包含在价格内，我这边系统没查到具体信息呢。建议你到店后让顾问帮你核对下，或者可以告诉我你的具体需求，我帮你看看有没有更合适的方案~ |
| SF7_019 | 只做半脸是不是便宜点 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign, too_long_single_message | 热玛吉的价格会根据项目部位、剂量等配置不同，我先按系统当前价给你参考，具体是否有半脸优惠需要进一步确认。如果你想了解具体方案或预算情况，可以说说你的改善需求或预算偏好，我帮你匹配更... |
| SF7_020 | 学生有没有优惠 | S1_icebreaking | price_inquiry | True |  | 你可以先告诉我具体想了解哪个项目哦，学生身份如果是首次体验某个项目的话，新客价可以按系统价参考的~ |
| SF7_021 | 我未成年，价格会便宜吗 | S0_wait_icebreak | price_inquiry | False | likely_price_or_campaign | 价格需要根据具体项目来定哦，我先按系统价给你参考哈。你可以先告诉我想了解哪个具体项目，或者改善什么问题，这样我能更准确回复~ |
| SF7_022 | 这个多少钱 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign | 你是想问皮秒淡斑还是光子淡斑的价格呢？我先帮你查询对应的系统报价哈，具体价格需要按项目和配置确认~ |
| SF7_023 | 刚刚那个PDRN三文鱼冻干修护套组价格呢 | S3_deep_consult | price_inquiry | False | likely_store_match, likely_price_or_campaign | 我直接按系统价给你说哈，不过目前系统里没有明确的价格信息，具体价格需要按项目大类和门店配置确认。你可以先说说你的预算偏好，或者我帮你看看有没有适配的体验方案~ |
| SF7_024 | 先告诉我大概预算范围就行 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign | 改善暗沉和毛孔可能涉及光子或水光项目，价格需要按具体项目确认哦。你想了解光子还是水光项目的价格呢？ |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
