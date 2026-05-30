# sf2_profile_collect 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 14 |
| 自动通过率 | 58.33% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 4 |
| 疑似门店匹配 | 0 |
| 疑似价格/活动 | 0 |
| 疑似过早推荐项目 | 1 |
| 问题超过1个 | 3 |
| 单条过长 | 2 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF2_001 | 我主要想改善脸上的斑 | S2_profile_collecting | project_inquiry | False | too_many_questions | 针对脸上的斑，我们有光子嫩肤、皮秒/调Q激光、化学焕肤等方向可以了解。 你的斑主要是什么类型呢？比如晒后、炎症后或雀斑样？ |
| SF2_002 | 毛孔粗大，还有点出油 | S2_profile_collecting | project_inquiry | False | likely_over_recommend | 针对你毛孔粗大和出油的情况，方向上可以先了解水杨酸焕肤和清洁管理类项目。 你之前有尝试过类似的皮肤管理项目吗？ |
| SF2_003 | 我平时经常熬夜，脸色很黄 | S1_icebreaking | project_inquiry | True |  | 针对熬夜导致脸色黄的情况，我们有相应的项目方向可以沟通。 比如补水修复、光子亮肤、抗氧化护理和基础皮肤管理这些方向。 具体方案需结合皮肤检测或面诊评估，建议后续做个皮肤检测。 |
| SF2_004 | 我是敏感肌，之前刷酸会刺痛 | S2_profile_collecting | project_inquiry | True |  | 刷酸后刺痛可能是皮肤屏障受刺激，敏感肌做焕肤类项目需谨慎，建议优先修复稳定。 针对你的情况，可了解补水修复管理或屏障修复类项目，侧重温和修复方向。 是否方便描述下皮肤泛红/脱皮的具... |
| SF2_005 | 我之前做过水光，但是维持不久 | S7_dealed_active | after_sales | True |  | 我理解你会在意水光效果的维持情况，效果表现通常和项目类型、个人恢复周期有关。你这次做的水光具体是什么时候操作的呢？我帮你让顾问一起看看记录和后续怎么跟进。 |
| SF2_006 | 预算不要太高，我先看看 | S2_profile_collecting | emotion_chat | True |  | 懂你的顾虑～我们有很多性价比高的项目可以慢慢选，你先看看喜欢哪种风格，不用急着决定哈。 |
| SF2_007 | 我怕疼，也怕恢复期太久 | S1_icebreaking | trust_issue | False | too_long_single_message | 您担心这个很正常，医美项目的安全性和恢复期确实需要重点关注。我们这边的设备和操作流程都经过严格规范，您可以先看一下这份资料，更清楚我们的安全保障。 https://lf26-app... |
| SF2_008 | 想自然一点，不要太明显 | S2_profile_collecting | 暂无 | False | likely_appointment_push | 你所在的城市或方便到店的区域是哪里呀？ |
| SF2_009 | 我在厦门 | S2_profile_collecting | 暂无 | False | too_many_questions | 那你现在皮肤有什么困扰吗？比如干燥、暗沉这些情况？ |
| SF2_010 | 25岁，混油皮 | S2_profile_collecting | 暂无 | False | likely_appointment_push | 方便问下你所在的城市或方便到店的区域吗？ |
| SF2_011 | 脸颊泛红，偶尔会痒 | S2_profile_collecting | 暂无 | False | likely_appointment_push | 方便问下你现在所在的城市或者方便到店的区域是哪里吗？ |
| SF2_012 | 主要是黑眼圈重，经常熬夜 | S1_icebreaking | project_inquiry | True |  | 方向上可以先看眼周护理、补水修复、抗氧化这类项目 你方便说说黑眼圈是青黑色还是偏茶色吗 |
| SF2_013 | 我想先改善痘印，痘痘已经少了 | S2_profile_collecting | project_inquiry | True |  | 方向上可以先看光子嫩肤、红蓝光/修复类管理等项目，帮助改善痘印。 你的痘印是红色为主还是褐色为主呢？ |
| SF2_014 | 脸有点松，法令纹也明显 | S2_profile_collecting | project_inquiry | True |  | 脸松和法令纹明显可以考虑抗衰项目，方向上先看射频紧致或深层提升类项目。 可以优先了解热玛吉（偏皮肤紧致）或超声炮（偏深层提升轮廓）这类方向。 你更在意皮肤紧致感还是中下面部轮廓清晰... |
| SF2_015 | 我刚生完孩子，还在哺乳期 | S0_wait_icebreak | human_request | True |  | 好的，了解你的情况。关于哺乳期医美项目，建议咨询专业同事进一步确认。 |
| SF2_016 | 我未成年，可以做吗 | S0_wait_icebreak | human_request | True |  | 未成年人进行医美项目需符合相关规定，具体可请专业同事为你详细说明，我会帮你转接。 |
| SF2_017 | 没有做过医美，护肤也比较简单 | S1_icebreaking | greeting | False | too_many_questions | 了解啦，你之前没接触过医美，护肤也比较简单～ 那你这次是想先了解哪个方向的改善呢？比如皮肤、抗衰这些？ |
| SF2_018 | 之前被推销怕了，不想被一直催 | S1_icebreaking | emotion_chat | True |  | 懂你这种担心被催的心情，我们这边不会一直推销的，你可以放心慢慢了解~ |
| SF2_019 | 恢复期最好短一点，我工作没法请假 | S3_deep_consult | project_inquiry | True |  | 热玛吉、超声炮等轻光电类项目通常恢复期相对短，具体因人而异。 建议结合你的皮肤状态和面诊评估，才能确定最适合的项目哦。 |
| SF2_020 | 我比较急，最好这周能看到变化 | S3_deep_consult | project_inquiry | False | likely_appointment_push | 你的需求比较急，建议先到店做皮肤检测和面诊，明确问题后推荐快速方案。 你这周方便到店吗？能更快帮你评估肤质。 |
| SF2_021 | 我在广州，想改善暗沉和毛孔 | S2_profile_collecting | project_inquiry | True |  | 有的，针对暗沉和毛孔问题，方向上可以先了解光子嫩肤和水光类项目。 光子适合综合改善肤质、暗沉和毛孔；水光类则侧重补水亮肤，具体可根据你的皮肤状态再推荐。 |
| SF2_022 | 就想先淡斑，别的暂时不考虑 | S2_profile_collecting | project_inquiry | True |  | 我们有针对淡斑的项目，比如皮秒/超皮秒和光子嫩肤方向，适合改善雀斑、晒斑等色素问题。 你的斑是点状的雀斑还是片状的色斑？可以先描述下。 |
| SF2_023 | 不太清楚是什么斑，晒太阳后更明显 | S1_icebreaking | project_inquiry | True |  | 方向上可以先了解光子嫩肤、皮秒/调Q激光这类改善色沉的项目，具体需结合你的皮肤状态和面诊检测确认哦。 |
| SF2_024 | 我最担心反黑，之前听别人说过 | S1_icebreaking | trust_issue | False | too_long_single_message | 您担心反黑很正常，医美项目的安全性确实需要重点关注。我们的仪器设备都有正规的授权和检测报告，这也是保障安全的一部分，您可以看看相关资料。 https://lf9-appstore-... |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
