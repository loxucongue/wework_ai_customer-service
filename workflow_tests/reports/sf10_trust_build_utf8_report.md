# SF10_trust_build 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 3 |
| 自动通过率 | 12.5% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 1 |
| 疑似主动预约 | 15 |
| 疑似门店匹配 | 0 |
| 疑似价格/活动 | 8 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 5 |
| 单条过长 | 19 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF10_001 | 你们公司正规吗 | S0_wait_icebreak | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你有这个顾虑很正常，医美机构的资质确实需要仔细确认。我先发你一张医疗机构执业许可证样本看下。 https://lf26-appstore-sign.oceancloudapi.co... |
| SF10_002 | 你们靠谱吗，不会是骗人的吧 | S1_icebreaking | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你有这个顾虑很正常，医美确实要先确认机构资质和服务流程。我先发你一张资质资料看下。 https://lf9-appstore-sign.oceancloudapi.com/ocea... |
| SF10_003 | 有营业执照或者医疗资质吗 | S3_deep_consult | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你有这个顾虑很正常，我先发你我们的营业执照资料看下。 https://lf26-appstore-sign.oceancloudapi.com/ocean-cloud-tos/Fi... |
| SF10_004 | 产品是真的吗，会不会是假货 | S3_deep_consult | trust_issue | False | likely_appointment_push, too_many_questions, too_long_single_message | 你担心产品真假是很正常的，医美项目确实需要确认产品来源。我先发你产品防伪溯源的流程资料看一下。 https://lf9-appstore-sign.oceancloudapi.co... |
| SF10_005 | 你们设备是正规的吗 | S3_deep_consult | trust_issue | False | likely_appointment_push, too_long_single_message | 你有这个顾虑很正常，医美设备正规确实需要确认。我先发你一张我们机构设备的授权及检测报告图片，能看到上面有认证印章。 https://lf3-appstore-sign.oceanc... |
| SF10_006 | 医生或者技师专业吗 | S3_deep_consult | trust_issue | False | likely_appointment_push, too_many_questions, too_long_single_message | 你担心医生或技师的专业性是很正常的，我先发你我们技师和医师的资质资料看下。 https://lf6-appstore-sign.oceancloudapi.com/ocean-cl... |
| SF10_007 | 做这个安全吗，会不会做坏 | S3_deep_consult | trust_issue | False | likely_appointment_push, too_long_single_message | 你有这个顾虑很正常，医美确实要重视安全流程。我先发你我们的消毒无菌操作流程图看看。 https://lf3-appstore-sign.oceancloudapi.com/ocea... |
| SF10_008 | 会不会没效果 | S1_icebreaking | trust_issue | False | likely_appointment_push | 担心效果是很正常的，祛斑效果会受皮肤基础、方案匹配度和术后护理影响，我们会提前沟通清楚。 到店后会先面诊评估，确认你的皮肤基础和适合的方案，效果预期也会明确说明再安排。 你可以和我... |
| SF10_009 | 我怕做完不自然，翻车怎么办 | S1_icebreaking | trust_issue | False | too_long_single_message | 你担心效果不自然是很正常的，我先发你一张术后护理跟进说明图。 https://lf3-appstore-sign.oceancloudapi.com/ocean-cloud-tos... |
| SF10_010 | 到店会不会被强推销 | S4_appointment_negotiating | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你有这个顾虑很正常，我们不会强制推销的。 https://lf6-appstore-sign.oceancloudapi.com/ocean-cloud-tos/FileBizTy... |
| SF10_011 | 会不会到店乱收费，有隐形消费吗 | S3_deep_consult | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你担心到店后收费不透明是很正常的，我先发你一张我们的价格透明说明图。 https://lf26-appstore-sign.oceancloudapi.com/ocean-clou... |
| SF10_012 | 做完有人管吗，售后有没有保障 | S3_deep_consult | trust_issue | False | likely_appointment_push, too_many_questions, too_long_single_message | 你担心做完后没人管和售后保障，我先发你术后护理跟进说明图，里面标注了术后1天、7天、30天的护理内容。 https://lf26-appstore-sign.oceanclouda... |
| SF10_013 | 如果没效果能退吗 | S3_deep_consult | trust_issue | False | too_many_questions, too_long_single_message | 你担心术后效果和售后跟进是很合理的，我发你售后保障流程说明图看下。 https://lf6-appstore-sign.oceancloudapi.com/ocean-cloud-... |
| SF10_014 | 你是真人还是AI啊 | S1_icebreaking | trust_issue | False | likely_handoff, likely_price_or_campaign | 这边会先帮你整理需求和基础问题。涉及方案、价格确认或复杂情况，可以安排顾问和你详细沟通。 |
| SF10_015 | 你能不能让真人顾问跟我说 | S0_wait_icebreak | human_request | True |  | 您好，我是您的专属客服。有什么可以帮您的吗？ |
| SF10_016 | 我之前在别家被坑过，所以不太相信 | S3_deep_consult | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你之前有过不好的经历，谨慎完全能理解。我先发你一张价格透明说明图，能看到所有项目明码标价。 https://lf3-appstore-sign.oceancloudapi.com/... |
| SF10_017 | 朋友说医美都很坑，我有点怕 | S1_icebreaking | trust_issue | False | likely_appointment_push, too_long_single_message | 你有这个担心很正常，医美项目确实需要谨慎选择。我先发你一张我们机构的执业许可证，能看到公司的正规性。 https://lf3-appstore-sign.oceancloudapi... |
| SF10_018 | 你们有没有真实案例可以看 | S3_deep_consult | image_inquiry | True |  | 我们有一些经过隐私处理的淡斑类真实案例可以参考哦，不过为了帮你更精准地匹配适合的方案，可能需要先了解下你的具体淡斑诉求和皮肤状态呢。 |
| SF10_019 | 你们是不是说百分百安全 | S3_deep_consult | trust_issue | False | too_long_single_message | 医美安全性确实需要重视，我们会严格遵循院感规范来保障。我先发你一张消毒/无菌操作流程图。 https://lf6-appstore-sign.oceancloudapi.com/o... |
| SF10_020 | 如果出了问题你们负责吗 | S3_deep_consult | trust_issue | False | too_long_single_message | 你担心的售后问题我们有明确流程，我先给你看一下售后保障流程图。 https://lf3-appstore-sign.oceancloudapi.com/ocean-cloud-to... |
| SF10_021 | 我想看你们的消毒流程 | S3_deep_consult | trust_issue | False | likely_appointment_push, too_long_single_message | 你担心感染是很正常的，我们的消毒流程可以通过图片让你更清楚。 https://lf9-appstore-sign.oceancloudapi.com/ocean-cloud-tos... |
| SF10_022 | 你们价格透明吗，我怕去了又加钱 | S4_appointment_negotiating | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 你担心到店乱收费的顾虑很正常，我们所有项目都有透明的价格说明。 https://lf3-appstore-sign.oceancloudapi.com/ocean-cloud-to... |
| SF10_023 | 你们别骗我，我要投诉 | S3_deep_consult | complaint_refund | True |  | 您好，我是您的专属客服。有什么可以帮您的吗？ |
| SF10_024 | 我现在就是不放心，你先发点资料我看看 | S3_deep_consult | trust_issue | False | too_many_questions, too_long_single_message | 你有这个顾虑很正常，我先发你资质资料和安全流程资料看看。 https://lf6-appstore-sign.oceancloudapi.com/ocean-cloud-tos/F... |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
