# SF5_competitor_response 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 7 |
| 自动通过率 | 29.17% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 4 |
| 疑似门店匹配 | 3 |
| 疑似价格/活动 | 9 |
| 疑似过早推荐项目 | 1 |
| 问题超过1个 | 3 |
| 单条过长 | 13 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF5_001 | 别家给我报的水光比你们便宜很多 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign, too_long_single_message | 多对比一下是对的，医美项目确实不能只看一个报价。主要要看产品、剂量、部位、操作人员和后续护理是不是一样。你方便说下对方报价里具体包含的项目内容、产品品牌/类型、剂量和后续服务吗？这... |
| SF5_002 | 我看另一家光子嫩肤才299，你们为什么贵 | S3_deep_consult | bargain | True |  | 您好，很高兴为您服务。请问有什么可以帮您的吗？ |
| SF5_003 | 别家说你们产品不正规，是真的吗 | S3_deep_consult | trust_issue | False | likely_appointment_push, too_long_single_message | 你担心产品正规性很正常，我先发你产品防伪溯源的流程资料看下。 https://lf6-appstore-sign.oceancloudapi.com/ocean-cloud-tos... |
| SF5_004 | 别家顾问说你们家技术不行 | S3_deep_consult | trust_issue | True |  | 你有这个顾虑很正常。我们会先面诊评估你的情况，确认适合的技术方案后再安排操作，不会让你不清楚情况就进行。 评估时会详细说明技术细节和你的疑问，你随时问我就好，不着急决定的。 |
| SF5_005 | 我发你个截图，这个水光才199，你们差这么多？ | S3_deep_consult | price_inquiry | False | likely_price_or_campaign, too_long_single_message | 这个价格差异可能是因为项目具体细节不同哦（比如水光的剂量、部位或产品类型）。我先按系统配置给你说哈~ 你提到的199是针对哪种具体水光项目呢？可以再详细说下吗，这样能更准确匹配报价... |
| SF5_006 | 另一家现在做活动买一送一，你们有吗 | S3_deep_consult | campaign_inquiry | False | likely_store_match | 您好，感谢关注我们的活动。我们这边确实有面向不同阶段客户的专属权益，比如新客礼包、老客复购优惠等。 您刚才提到想淡斑，我这边可以帮您看看是否有针对这个项目的相关活动。不过具体的活动... |
| SF5_007 | 别家说皮秒肯定比光子好，你们怎么说 | S3_deep_consult | competitor_compare | False | likely_price_or_campaign, too_long_single_message | 多对比一下是对的~医美项目其实可以从几个维度来理性判断：比如项目具体内容是不是完全一致（像皮秒和光子虽然都针对祛斑，但具体模式、能量设置可能不同）、产品/设备来源是否明确、操作人员... |
| SF5_008 | 我朋友在别家做淡斑没效果，你们有什么区别 | S3_deep_consult | competitor_compare | False | too_many_questions, too_long_single_message | 多对比一下是对的，担心效果没达到也是很正常的。每家机构的方案和操作细节不同，效果也会受个人皮肤基础、斑点类型和治疗周期影响。淡斑这类项目通常需要根据斑点类型（比如晒斑、黄褐斑等）和... |
| SF5_009 | 你们和另一家机构比哪个好 | S1_icebreaking | competitor_compare | False | likely_price_or_campaign | 多对比一下是对的。医美项目更适合把内容拆开看：项目是否一样、产品和剂量是否清楚、操作人员是否明确、费用是否透明、术后有没有跟进。 |
| SF5_010 | 别家把资质证书发我了，你们有吗 | S3_deep_consult | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你有顾虑很正常，正规机构的资质确实是重要参考。我先发你我们的医疗机构执业许可证样本看下。 https://lf6-appstore-sign.oceancloudapi.com/o... |
| SF5_011 | 他们说用的是进口设备，你们这边呢 | S3_deep_consult | project_inquiry | True |  | 我们这边有进口设备的抗衰项目方向，比如射频、超声类等。 你更关注皮肤紧致、轮廓提升还是其他抗衰需求？ |
| SF5_012 | 别家说他们售后包修包退，你们售后怎么保障 | S3_deep_consult | trust_issue | False | too_many_questions, too_long_single_message | 你有这个顾虑很正常，我们也重视售后保障透明度。我先发你售后保障流程图看看。 https://lf6-appstore-sign.oceancloudapi.com/ocean-cl... |
| SF5_013 | 我就是想多对比几家，不想被你们催 | S8_silent_return | emotion_chat | True |  | 理解你的想法，多对比确实是应该的，我们肯定不会催你哒~ 你可以慢慢看，不用着急决定的。 |
| SF5_014 | 你们要是比他们便宜我就来，不然算了 | S3_deep_consult | bargain | True |  | 您好，我是您的专属客服。请问有什么可以帮您？ |
| SF5_015 | 别家一直说你们坑人，我有点不放心 | S1_icebreaking | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你有这个顾虑很正常，医美机构选择确实需要谨慎。我先发你我们机构的执业许可证样本，你可以看看资质是否齐全。 https://lf9-appstore-sign.oceanclouda... |
| SF5_016 | 是不是同行都说你们不好啊 | S1_icebreaking | trust_issue | True |  | 你有这个顾虑很正常，我们一直以合规透明为标准，所有服务流程都会提前和你确认清楚，不会有隐瞒。 如果你想了解具体的服务流程或资质，我可以详细说给你听，你也可以放心对比。 |
| SF5_017 | 同样是皮秒，为什么他们只要几百 | S3_deep_consult | price_inquiry | False | likely_store_match, likely_price_or_campaign | 皮秒项目的价格会因具体项目类型、产品、疗程或门店活动等不同有差异哦。我这边暂时没查到具体价格细节，你可以和我说说更想了解哪个具体方案，或者你的预算范围，我帮你看看该怎么更匹配~ |
| SF5_018 | 别家说一次就能把斑淡很多，你们能做到吗 | S1_icebreaking | competitor_compare | False | too_long_single_message | 多对比一下是很正常的谨慎态度。医美效果确实会受个人皮肤基础、斑点类型和方案设计影响，没法简单说一次就能完全消除。像您这样的斑比较明显，我们这边会先做专业的皮肤检测，根据具体情况定制... |
| SF5_019 | 我在别家做过水光维持不久，换你们会不会好点 | S7_dealed_active | project_inquiry | False | too_many_questions | 水光的维持时间受项目类型、个人代谢等多种因素影响，不能承诺固定时长。 具体方案需结合你的皮肤状态，建议面诊评估后，再安排适合的水光项目方案。 你可以发张当前皮肤状态的照片参考下吗？... |
| SF5_020 | 一家推荐皮秒，一家推荐光子，我到底听谁的 | S3_deep_consult | project_inquiry | True |  | 光子嫩肤适合整体提亮肤色和轻中度色沉，皮秒更针对明确斑点的淡化 具体选择需结合斑型和皮肤状态，建议先做皮肤检测，面诊后再确定方案 |
| SF5_021 | 我把别家的报价单发你，你帮我看看合理吗 | S3_deep_consult | competitor_compare | False | likely_store_match, likely_price_or_campaign, too_long_single_message | 多对比一下是对的，帮助你更清楚价格是否合理。不过报价是否合理，要结合项目名、产品、剂量、部位、次数、操作人员、售后和是否有额外费用这些细节来看，我帮你按维度核对没问题~你可以把截图... |
| SF5_022 | 别家说套餐包干，你们为什么只报单次 | S3_deep_consult | competitor_compare | False | likely_appointment_push, likely_over_recommend, too_long_single_message | 多对比一下是对的，做淡斑项目确实要仔细看看方案细节呢。不同机构的套餐设计可能侧重点不同，比如有的机构会将后续补打或护理作为套餐内容，而我们报单次更侧重根据你的皮肤基础精准调配剂量和... |
| SF5_023 | 我想比较一下你们和另一家的安全保障 | S3_deep_consult | competitor_compare | False | too_long_single_message | 多对比是对的，安全保障确实是医美项目里很重要的一点。不过医美项目一般不太适合说完全没风险，正规的沟通应该把适合人群、可能的风险和术后护理都说清楚。我们这边做项目时会先给你做详细的皮... |
| SF5_024 | 别家说不用面诊可以直接做，更方便 | S4_appointment_negotiating | competitor_compare | False | likely_price_or_campaign, too_long_single_message | 多对比一下是对的，你谨慎一点挺好的。其实面诊不是为了拖流程，是为了确认皮肤状态、适合项目、风险和费用。像咱们做的这类项目，尤其是光电、注射类的，确实不建议完全不评估就直接操作，这样... |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
