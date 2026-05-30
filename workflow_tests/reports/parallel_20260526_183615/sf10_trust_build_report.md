# sf10_trust_build 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 4 |
| 自动通过率 | 16.67% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 4 |
| 疑似门店匹配 | 0 |
| 疑似价格/活动 | 2 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 0 |
| 单条过长 | 20 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF10_001 | 你们公司正规吗 | S1_icebreaking | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实建议先看资质、产品来源和后续服务。我先把机构品牌和资质相关资料发您，您可以更直观了解。 https://lf26-appstore-sign.ocea... |
| SF10_002 | 你们靠谱吗，不会是骗人的吧 | S1_icebreaking | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实建议先看资质、产品来源和后续服务。我们这边可以先把相关资料发您看一下，您对比的时候也更清楚。 https://lf3-appstore-sign.oc... |
| SF10_003 | 有营业执照或者医疗资质吗 | S1_icebreaking | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实需要确认资质。我们这边可以先把相关资料发您看一下，您对比的时候也更清楚。 https://lf26-appstore-sign.oceanclouda... |
| SF10_004 | 产品是真的吗，会不会是假货 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目确实要确认产品来源和授权信息。我们的产品都有正规的溯源流程，您可以重点看一下这些信息。 https://lf26-appstore-sign.oceanc... |
| SF10_005 | 你们设备是正规的吗 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心设备是否正规是对的，超声炮这类项目确实需要关注设备的型号、来源和授权情况，这会直接影响体验和效果哦。我们这边可以先把相关设备的正规性资料发您看一下，您参考时也更清楚。 htt... |
| SF10_006 | 医生或者技师专业吗 | S1_icebreaking | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目确实需要关注医生的专业背景和经验。我们这边有张医生和李技师的详细资质，张医生有10年美容外科经验，擅长眼部和面部年轻化项目；李技师有8年皮肤美容经验，专注... |
| SF10_007 | 做这个安全吗，会不会做坏 | S3_deep_consult | trust_issue | True |  | 您担心这个很正常，医美项目安全和效果确实需要多方面确认。安全性方面，我们会严格把控产品来源、操作流程和医生面诊评估，您后续可以先到院面诊，我们会根据您的皮肤状态给出详细方案。 |
| SF10_008 | 会不会没效果 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心效果是很正常的，医美项目的效果确实会受个人皮肤基础、项目方案和术后护理等因素影响。我们有不少真实的祛斑案例可以参考，您如果想了解具体效果，我可以发给您看看，不过每个人的情况不... |
| SF10_009 | 我怕做完不自然，翻车怎么办 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目确实需要结合操作流程和术后护理来保障效果和安全。我们做完后会有1天、7天、30天的护理跟进，您可以看看对应的护理说明。 https://lf3-appst... |
| SF10_010 | 到店会不会被强推销 | S4_appointment_negotiating | trust_issue | False | likely_appointment_push, too_long_single_message | 您担心到店会被强推销的顾虑很正常，我们的服务流程会先做咨询沟通和需求确认，再根据您的实际需求推荐合适的方案，不会有强制推销的情况。 https://lf9-appstore-sig... |
| SF10_011 | 会不会到店乱收费，有隐形消费吗 | S3_deep_consult | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 您担心到店乱收费和隐形消费是很正常的，这类问题确实需要先确认清楚。我们这边有医美项目价格透明的说明资料，您可以看看这个，上面明确标注了各项目的价格，没有额外隐形消费。 https:... |
| SF10_012 | 做完有人管吗，售后有没有保障 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心做完没人管是很正常的，医美项目的售后保障确实很重要。我们有完善的售后流程，包括问题反馈、客服受理和跟踪回访，您可以看看这个售后保障流程图。 https://lf3-appst... |
| SF10_013 | 如果没效果能退吗 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心术后效果和退款问题很正常，医美项目的售后保障确实很重要。我们有完善的售后流程来处理这类情况，您可以先了解下具体的处理方式。 https://lf9-appstore-sign... |
| SF10_014 | 你是真人还是AI啊 | S1_icebreaking | trust_issue | True |  | 您的问题我们会记录反馈，但目前主要处理信任建立相关的咨询，您可以先说说需要了解的具体信任问题，我会尽力帮您解答。 |
| SF10_015 | 你能不能让真人顾问跟我说 | S1_icebreaking | human_request | True |  | 好的，我会帮你联系真人顾问，稍后会有同事和你沟通。 |
| SF10_016 | 我之前在别家被坑过，所以不太相信 | S3_deep_consult | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实需要多确认资质和服务流程。我们的医师都有严格的资质审核流程，技术也经过专业培训，您可以先看看我们的医师资料。 https://lf26-appstor... |
| SF10_017 | 朋友说医美都很坑，我有点怕 | S1_icebreaking | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实建议先看资质、产品来源和后续服务。我们这边可以先把相关资料发您看一下，您对比的时候也更清楚。 https://lf3-appstore-sign.oc... |
| SF10_018 | 你们有没有真实案例可以看 | S2_profile_collecting | image_inquiry | False | likely_appointment_push, too_long_single_message | 我们有淡斑项目的真实案例供参考，但具体案例需要结合你的面部肤质和需求来匹配。建议你可以先补充一下目前的皮肤状态（比如是否有暗沉、色沉等情况），或者预约一次免费的线上面诊，我会帮你分... |
| SF10_019 | 你们是不是说百分百安全 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目安全性需要结合个人皮肤状态、操作流程和后续护理来看。我们不会说‘百分百安全’，建议您可以先做个面诊，医生会根据您的具体情况评估方案的适配性和安全性，这样也... |
| SF10_020 | 如果出了问题你们负责吗 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目的售后保障确实很重要。我们有完善的售后保障流程，包括问题反馈、客服受理、方案制定和跟踪回访，会提供7×24小时服务保障您的权益。 https://lf6-... |
| SF10_021 | 我想看你们的消毒流程 | S3_deep_consult | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目确实需要关注消毒和无菌操作流程。 https://lf6-appstore-sign.oceancloudapi.com/ocean-cloud-tos/... |
| SF10_022 | 你们价格透明吗，我怕去了又加钱 | S3_deep_consult | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_long_single_message | 您担心到店后有加价的心情我很理解，价格透明确实是医美消费中需要重点关注的。我们这边所有项目都是明码标价，没有隐藏消费的，您可以重点看一下价格透明说明图里的内容，这样对比起来也更清楚... |
| SF10_023 | 你们别骗我，我要投诉 | S3_deep_consult | complaint_refund | True |  | 好的，我会帮你转接相关同事处理，麻烦你稍等一下。 |
| SF10_024 | 我现在就是不放心，你先发点资料我看看 | S2_profile_collecting | trust_issue | False | too_long_single_message | 您谨慎一点是对的，医美项目确实建议先看资质、产品来源和后续服务。我们这边可以先把相关资料发您看一下，您对比的时候也更清楚。 https://lf3-appstore-sign.oc... |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
