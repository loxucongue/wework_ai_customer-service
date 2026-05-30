# sf5_competitor_response 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 5 |
| 自动通过率 | 20.83% |
| API错误 | 0 |
| 空回复 | 3 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 0 |
| 疑似门店匹配 | 1 |
| 疑似价格/活动 | 7 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 0 |
| 单条过长 | 13 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF5_001 | 别家给我报的水光比你们便宜很多 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign | 你多对比一下是对的，医美项目确实不能只看一个报价。主要要看产品、剂量、部位、操作人员和后续护理是不是一样。你可以补充一下对方报价里包含的项目、产品、剂量、部位和售后信息吗？ |
| SF5_002 | 我看另一家光子嫩肤才299，你们为什么贵 | S3_deep_consult | bargain | True |  | 这个问题我需要咨询同事确认，稍后给你回复~ |
| SF5_003 | 别家说你们产品不正规，是真的吗 | S3_deep_consult | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实建议先看产品来源和资质。我们的产品包装和来源都有正规的溯源信息，您可以重点关注一下。 https://lf6-appstore-sign.oceanc... |
| SF5_004 | 别家顾问说你们家技术不行 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目确实需要关注操作人员的经验和专业度。我们的医生团队都经过严格资质审核，像张医生有10年美容外科经验，擅长眼部整形和面部年轻化；李技师有8年皮肤美容经验，在... |
| SF5_005 | 我发你个截图，这个水光才199，你们差这么多？ | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF5_006 | 另一家现在做活动买一送一，你们有吗 | S3_deep_consult | campaign_inquiry | False | likely_price_or_campaign | 我先按当前能确认的活动信息跟你说。关于淡斑项目，目前我这边没有直接匹配到“买一送一”这类活动。 不同机构的优惠形式不太一样。如果你想了解我们淡斑项目当前具体的活动价、体验方案或者礼... |
| SF5_007 | 别家说皮秒肯定比光子好，你们怎么说 | S3_deep_consult | competitor_compare | True |  | 多对比一下是对的。不同皮肤基础和问题类型差异很大，有些项目可能一次有变化，但不能按一次就完全解决来承诺。斑、暗沉这类问题通常更看方案和周期。 你可以把斑点的具体情况（比如类型、持续... |
| SF5_008 | 我朋友在别家做淡斑没效果，你们有什么区别 | S3_deep_consult | competitor_compare | False | too_long_single_message | 多对比一下是对的。医美效果和个人皮肤基础、方案设计、操作细节以及术后护理都关系很大，像淡斑这类项目通常需要根据斑点类型和严重程度制定周期方案。我们建议您可以先详细说说斑点的具体情况... |
| SF5_009 | 你们和另一家机构比哪个好 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign, too_long_single_message | 多对比一下是对的。医美项目更适合把内容拆开看：项目是否一样、产品和剂量是否清楚、操作人员是否明确、费用是否透明、术后有没有跟进。如果你有对方的具体方案或报价，也可以发给我帮你按维度... |
| SF5_010 | 别家把资质证书发我了，你们有吗 | S2_profile_collecting | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实建议先看资质、产品来源和后续服务。我们这边也有相关资质资料，您可以先看一下，对比的时候也更清楚。 https://lf6-appstore-sign.... |
| SF5_011 | 他们说用的是进口设备，你们这边呢 | S2_profile_collecting | project_inquiry | True |  | 我们这边抗衰项目涵盖射频（热玛吉）、超声（超声炮）、激光（Fotona）等类型，设备均经过专业评估匹配项目需求。 您目前皮肤主要是松弛、细纹、凹陷中的哪类困扰呢？ |
| SF5_012 | 别家说他们售后包修包退，你们售后怎么保障 | S3_deep_consult | trust_issue | False | too_long_single_message | 您对比售后保障是对的，医美项目的后续服务确实很关键。我们的售后保障流程会明确问题反馈、客服受理等环节，您可以参考下。 https://lf26-appstore-sign.ocea... |
| SF5_013 | 我就是想多对比几家，不想被你们催 | S2_profile_collecting | emotion_chat | False | likely_price_or_campaign | 懂你，对比的时候确实希望能按自己节奏来～我们这边不会催单的，你慢慢选就好。你现在最在意的是效果、价格还是医生团队呀？ |
| SF5_014 | 你们要是比他们便宜我就来，不然算了 | S3_deep_consult | bargain | True |  | 好的，我会帮你反馈这个情况，后续有进展会尽快和你沟通。 |
| SF5_015 | 别家一直说你们坑人，我有点不放心 | S1_icebreaking | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实需要仔细查看资质和服务流程。我们这边有机构的资质资料可以先给您看，您对比下来也能更安心。 https://lf6-appstore-sign.ocea... |
| SF5_016 | 是不是同行都说你们不好啊 | S1_icebreaking | trust_issue | False | likely_store_match, too_long_single_message | 您担心同行评价是对的，医美机构的正规性确实可以通过门店环境、资质和服务流程来判断。我们这边可以先让您看看门店环境资料，您也能更直观了解我们的规范。 https://lf3-apps... |
| SF5_017 | 同样是皮秒，为什么他们只要几百 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF5_018 | 别家说一次就能把斑淡很多，你们能做到吗 | S1_icebreaking | competitor_compare | False | too_long_single_message | 多对比是对的，医美项目确实需要理性判断~医美效果不适合做绝对承诺，靠谱的方式是先看基础情况、方案匹配和后续护理。能改善到什么程度，要面诊后更准确。你最想改善的具体是哪些斑点问题呢？... |
| SF5_019 | 我在别家做过水光维持不久，换你们会不会好点 | S3_deep_consult | project_inquiry | True |  | 水光维持时间受个人代谢、生活习惯等影响，没法固定时长哦。 具体效果要结合你的皮肤状态来判断，建议面诊让顾问详细看看呢。 |
| SF5_020 | 一家推荐皮秒，一家推荐光子，我到底听谁的 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign, too_long_single_message | 多对比一下是对的~不同机构给出的方案思路可能不一样，不一定是谁对谁错。 关键要看你的核心诉求（比如斑的具体情况、皮肤基础）、项目的作用层次（比如是表层淡斑还是深层改善），还有方案里... |
| SF5_021 | 我把别家的报价单发你，你帮我看看合理吗 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign, too_long_single_message | 多对比一下是对的。我可以帮你按维度看，不直接评价人家好坏。重点看项目名、产品、剂量、部位、次数、操作人员、售后和是否还有额外费用。你可以把截图里没写清楚的部分补充一下，比如产品规格... |
| SF5_022 | 别家说套餐包干，你们为什么只报单次 | S3_deep_consult | price_inquiry | False | empty_output |  |
| SF5_023 | 我想比较一下你们和另一家的安全保障 | S3_deep_consult | competitor_compare | False | too_long_single_message | 多对比一下是对的。安全保障可以从项目内容是否一致、产品来源是否透明、操作人员资质是否明确、术后有没有跟进服务这些维度来看哦。如果方便的话，你可以把对方的安全保障相关细节发过来，我们... |
| SF5_024 | 别家说不用面诊可以直接做，更方便 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign, too_long_single_message | 多对比是对的呢。其实面诊不是为了拖流程，是为了确认皮肤状态、适合的项目、风险和费用哦。像光电、注射这类项目，不建议完全不评估就做，毕竟每个人的基础和需求不太一样。您可以先过来了解下... |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
