# sf6_store_match 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 0 |
| 自动通过率 | 0% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 2 |
| 疑似门店匹配 | 24 |
| 疑似价格/活动 | 0 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 0 |
| 单条过长 | 0 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF6_001 | 你们门店在哪里 | S1_icebreaking | store_inquiry | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF6_002 | 附近有门店吗 | S3_deep_consult | store_inquiry | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF6_003 | 我在厦门，有近一点的吗 | S3_deep_consult | store_inquiry | False | likely_store_match | 有的，厦门这边有几家门店，我先发你门店信息，你看哪家更方便。 |
| SF6_004 | 厦门思明区附近有没有门店 | S4_appointment_negotiating | store_inquiry | False | likely_store_match | 有的，我看到厦门思明区这边有门店，我先发你门店信息，你看哪家更方便。你更方便工作日还是周末过来？ |
| SF6_005 | 广州天河有店吗 | S3_deep_consult | store_inquiry | False | likely_store_match | 有的，广州天河区这边有门店，我先发你门店信息，你看哪家更方便。 |
| SF6_006 | 深圳南山店具体地址发我 | S3_deep_consult | store_inquiry | False | likely_store_match | 好的，深圳南山店的具体地址我发你看下。你更方便工作日还是周末过来？ |
| SF6_007 | 你们营业到几点 | S1_icebreaking | store_inquiry | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF6_008 | 周末营业吗 | S3_deep_consult | store_inquiry | False | likely_store_match | 厦门这边周末是营业的，我先发你几家门店信息，你看哪家更方便。 |
| SF6_009 | 有停车场吗 | S3_deep_consult | store_inquiry | False | likely_store_match | 厦门这边有几家门店，我先发你门店信息，你可以看下哪家更方便。 |
| SF6_010 | 坐地铁怎么过去 | S4_appointment_negotiating | store_inquiry | False | likely_store_match | 好的，广州天河这边有门店，我先发你门店信息，你可以看下哪家方便。 |
| SF6_011 | 把导航发我 | S4_appointment_negotiating | store_inquiry | False | likely_store_match | 好的，厦门这边有几家门店，我先发你门店信息，你看哪家更方便。具体时段还要再确认。 |
| SF6_012 | 我在湖里区，哪家近 | S3_deep_consult | store_inquiry | False | likely_store_match | 好的，你在湖里区，我先按厦门这边的门店信息帮你匹配，稍后发你参考。 |
| SF6_013 | 我现在在上海，你们那边有店吗 | S3_deep_consult | store_inquiry | False | likely_store_match | 有的，上海这边有好几家门店，我先发你几家门店信息，你看哪家更方便。 |
| SF6_014 | 我可以换到离公司近的店吗 | S4_appointment_negotiating | appointment_change | False | likely_appointment_push, likely_store_match | 好的，想换到离公司更近的门店，我理解。 我看到您当前有一条待安排到店时间的记录，但还没有明确预约时间。 您方便说一下公司的大概位置吗？我先帮您看看附近有哪些门店。 |
| SF6_015 | 我已经约了思明店，地址再发我一下 | S5_appointed_waiting | store_inquiry | False | likely_appointment_push, likely_store_match | 好的，已经预约了思明店，我先把门店信息发给你，方便你确认。 |
| SF6_016 | 门店是在商场里面吗，几楼 | S3_deep_consult | store_inquiry | False | likely_store_match | 厦门思明店的具体位置信息我发你看下，你可以先了解下。 |
| SF6_017 | 附近有公交站吗 | S1_icebreaking | store_inquiry | False | likely_store_match | 好的，我看到深圳南山这边有门店，我先发你门店信息，具体交通情况你可以参考一下。 |
| SF6_018 | 离我最近的门店可以今天去吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？具体时段还需要根据你方便的时间来确认。 |
| SF6_019 | 厦门店现在人多吗，要不要排队 | S3_deep_consult | store_inquiry | False | likely_store_match | 厦门这边有几家门店，我先发你门店信息，你可以看下哪家方便。具体时段和排队情况，我这边再帮你确认。 |
| SF6_020 | 哪家店医生比较好 | S3_deep_consult | store_inquiry | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF6_021 | 你们厦门几家店 | S3_deep_consult | store_inquiry | False | likely_store_match | 你好，厦门这边有几家门店，我先发你门店信息，你看哪家更方便。 |
| SF6_022 | 我不在本地，可以先线上咨询再决定门店吗 | S3_deep_consult | project_inquiry | False | likely_store_match | 可以的，你可以先线上咨询项目方向后再决定门店。 你关注的水光项目适合干燥缺水、换季干敏等问题，能提升肤质稳定度和光泽感。 需要帮你对接线上顾问吗？ |
| SF6_023 | 门店可以做皮肤检测吗 | S2_profile_collecting | store_inquiry | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？这样我可以先发你看哪家可以做皮肤检测更方便。 |
| SF6_024 | 我在厦门，周六到店可以吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 好的，厦门这边有几家门店，我先发你门店信息，你看哪家更方便。具体周六的时段还需要再确认一下。 |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
