# SF6_store_match 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 1 |
| 自动通过率 | 4.17% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 5 |
| 疑似门店匹配 | 22 |
| 疑似价格/活动 | 0 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 6 |
| 单条过长 | 5 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF6_001 | 你们门店在哪里 | S1_icebreaking | store_inquiry | False | likely_store_match | 您好，我们有多家门店。 请问您在哪个城市呢？这样我可以为您推荐最近最方便的门店。 |
| SF6_002 | 附近有门店吗 | S1_icebreaking | store_inquiry | False | likely_store_match | 您好呀，我们门店分布在不同城市，先跟您确认一下，您目前在哪个城市呢？ |
| SF6_003 | 我在厦门，有近一点的吗 | S1_icebreaking | store_inquiry | False | likely_store_match | 厦门有我们门店的哦~ 为了给您推荐最方便的门店，可以告诉我您大概在厦门哪个区吗？或者您常活动的商圈、地铁站附近也可以。 |
| SF6_004 | 厦门思明区附近有没有门店 | S4_appointment_negotiating | store_inquiry | False | likely_store_match, too_many_questions | 您好，看到您坐标厦门思明区，是想了解我们的门店信息吗？ 目前厦门地区我们设有门店，方便您就近了解。可以为您提供具体地址和营业时间。 您周末方便过来看看的话，是周六还是周日呢？这样我... |
| SF6_005 | 广州天河有店吗 | S3_deep_consult | store_inquiry | False | likely_store_match, too_many_questions, too_long_single_message | 您好，广州天河区有我们的门店哦。 具体信息我帮您看一下。 【天河店】 📍地址：广州市天河区XX路XX号XX大厦XX层 🕙营业时间：周一至周日 10:00-20:00 🚇地铁：... |
| SF6_006 | 深圳南山店具体地址发我 | S1_icebreaking | store_inquiry | False | likely_store_match, too_long_single_message | 好的，这就给您发深圳南山店的具体地址。 📍 **门店地址**：深圳市南山区粤海街道XX路XX号XX大厦XX层（靠近地铁X号线XX站X出口） ⏰ **营业时间**：周一至周日 10... |
| SF6_007 | 你们营业到几点 | S1_icebreaking | store_inquiry | False | likely_appointment_push, likely_store_match | 您好呀，我们不同门店的营业时间会有些差别哦。 您方便告诉我您在哪个城市或者区域吗？这样我可以为您查询具体门店的营业时间，也方便为您规划到店路线。 |
| SF6_008 | 周末营业吗 | S1_icebreaking | store_inquiry | False | likely_store_match, too_many_questions | 厦门的门店周末都是正常营业的哦。 您是在厦门的哪个区呢？或者您靠近哪个地铁站？这样我可以帮您看看哪个门店离您最近，方便您过来。 |
| SF6_009 | 有停车场吗 | S1_icebreaking | store_inquiry | False | likely_store_match | 我们门店是提供停车位的，方便您开车过来。 您之前提到在思明区附近，是想了解我们思明区门店的具体地址和营业时间吗？ |
| SF6_010 | 坐地铁怎么过去 | S4_appointment_negotiating | store_inquiry | False | likely_store_match | 广州天河我们有两家门店，分别是天河城店和体育西路店。 1. 天河城店：天河路208号天河城购物中心5楼，近地铁1/3号线体育西路站C出口。 营业时间：周一至周日 10:00-22:... |
| SF6_011 | 把导航发我 | S4_appointment_negotiating | store_inquiry | False | likely_store_match, too_many_questions, too_long_single_message | 好的，我这就把厦门店的导航发给您。 【厦门思明店】 📍 地址：厦门市思明区XX路XX号XX大厦XX层 🕒 营业时间：周一至周日 10:00-20:00 🚗 停车：大厦有地下停... |
| SF6_012 | 我在湖里区，哪家近 | S1_icebreaking | store_inquiry | False | likely_store_match, too_many_questions, too_long_single_message | 好的，看到您在厦门湖里区，我帮您查一下附近的门店。 我们目前在厦门有两家门店，您看哪个区域对您更方便呢？ 1. **思明店**：位于思明区嘉禾路XX号，近SM城市广场，营业时间10... |
| SF6_013 | 我现在在上海，你们那边有店吗 | S3_deep_consult | store_inquiry | False | likely_store_match | 您好，我们在上海有门店的。 为了给您推荐最方便的分店，可以说一下您大概在上海哪个区，或者靠近哪个地铁站吗？ |
| SF6_014 | 我可以换到离公司近的店吗 | S4_appointment_negotiating | appointment_change | False | likely_store_match | 可以的，我先帮你确认一下离你公司近的门店和可约时间。请问你公司是在哪个城市和区域呢？ |
| SF6_015 | 我已经约了思明店，地址再发我一下 | S5_appointed_waiting | store_inquiry | False | likely_store_match | 好的，思明店的地址是：厦门市思明区XX路XX号XX大厦X层。 营业时间是周一至周日 10:00-21:00。 你可以直接导航“XX医美思明店”过来。你更方便工作日还是周末过来呢？ |
| SF6_016 | 门店是在商场里面吗，几楼 | S4_appointment_negotiating | store_inquiry | False | likely_store_match | 是的呢，厦门思明店是在商场里的，具体在X楼。 地址是：厦门市思明区XXX路XX号XX商场X楼XX号。营业时间是周一至周日10:00-22:00。 这是导航链接，方便您查找：[地图链... |
| SF6_017 | 附近有公交站吗 | S1_icebreaking | store_inquiry | False | likely_appointment_push, likely_store_match | 深圳南山有我们的门店哦。 南山店在科技园附近，具体地址是XX路XX号XX大厦X层。 门店附近有地铁X号线XX站，以及XX路、XX路等多条公交线路的站点，交通很方便的。 你更方便工作... |
| SF6_018 | 离我最近的门店可以今天去吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 今天我可以先帮您看一下离您最近的门店时间。不过要先确认一下您方便在哪个城市或哪个区到店呢？这样我才能帮您准确查询最近的门店和今天可约的时间。 |
| SF6_019 | 厦门店现在人多吗，要不要排队 | S1_icebreaking | store_inquiry | False | likely_appointment_push, likely_store_match, too_many_questions | 厦门店的地址是[请根据store_info填充具体地址]，营业时间是[请根据store_info填充营业时间]。 关于排队情况，我们建议您提前预约，这样到店后顾问会优先为您安排，能... |
| SF6_020 | 哪家店医生比较好 | S3_deep_consult | store_inquiry | False | likely_store_match | 您好呀，为您推荐医生需要先确定您方便去的门店呢。 请问您在哪个城市呀？ |
| SF6_021 | 你们厦门几家店 | S1_icebreaking | store_inquiry | False | likely_store_match, too_long_single_message | 您好，厦门目前有2家门店哦。 1. 【思明分院】 📍 地址：思明区湖滨南路123号XX大厦1层 🕐 营业时间：周一至周日 10:00-20:00 🚇 交通：地铁1号线“莲坂站... |
| SF6_022 | 我不在本地，可以先线上咨询再决定门店吗 | S3_deep_consult | project_inquiry | True |  | 可以先线上咨询了解水光项目的相关信息。 您现在想了解水光项目的哪些具体信息呢？比如功效、适合人群等。 |
| SF6_023 | 门店可以做皮肤检测吗 | S3_deep_consult | project_inquiry | False | likely_store_match | 门店可以做皮肤检测的，能帮助分析皮肤现状。你有没有泛红、斑点、毛孔粗大这类具体的皮肤问题呢？ |
| SF6_024 | 我在厦门，周六到店可以吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push | 可以的，我先帮你看厦门这边周六的时间。你方便在思明区、湖里区，还是其他区域到店？ |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
