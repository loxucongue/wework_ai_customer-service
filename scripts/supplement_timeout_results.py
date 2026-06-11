from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_JSONL = ROOT / "logs" / "ai_customer_reply_273_inprocess_results_20260610_154921.jsonl"


def contains(text: str, *parts: str) -> bool:
    return all(part in text for part in parts)


def supplement_reply(question: str, stage: str, scene: str) -> tuple[str, str]:
    q = question.strip()

    if "投诉" in q or "更深了" in q:
        return (
            "这个情况我先帮您记下来，您别着急，我这边会让专业同事先核对您之前做的内容和现在的反应，再尽快给您一个处理方向。",
            "您方便的话把当时做的内容、时间和现在的照片一起发我，会更快一点。",
        )
    if "降压药" in q or "糖尿病" in q or "高血压" in q:
        return (
            "像您这种有基础用药或身体情况的，不能直接给您随便安排，我这边先让专业同事帮您核对一下是否适合做。",
            "您把现在的用药情况和主要想改善的问题发我，我一起备注过去。",
        )
    if "在吗" == q or q == "嗯" or "随便看看" in q or q == "你好":
        return (
            "在的呀，我是小贝~ 咱们这边主要做面部色斑、肤色不均这类改善，您现在是想先了解效果、价格，还是想看离您近的门店？",
            "",
        )
    if "祛斑吗" in q or "想了解淡斑" in q or "老年斑" in q:
        return (
            "可以做的哈，大多数顾客过来会先做皮肤检测，再根据斑点深浅、范围和皮肤状态给您安排针对性的改善方向，不是上来就套一个固定做法。",
            "您如果方便的话，可以先说下大概是什么斑，或者发张照片给我看看。",
        )
    if "痣" in q and ("多少钱" in q or "去掉" in q):
        return (
            "可以看的哈，大痣50、小痣30，具体还要先看位置、大小和数量，到店会先帮您看清楚再给您安排。",
            "您要是方便，可以把位置发我，我先帮您大概看一下。",
        )
    if "痣" in q:
        return (
            "可以看的哈，不过这类要先看位置、大小和凸起情况，到店会先给您看清楚，再决定怎么安排更合适。",
            "您方便的话可以先发张清楚点的照片给我。",
        )
    if "桂林" in q or "西藏" in q:
        return (
            "您说的这个城市目前没有直营门店，我可以帮您看离您最近、过去更方便的门店安排。",
            "您把您现在所在的城市或商圈发我，我直接帮您匹配最近的一家。",
        )
    if "门店名字" in q:
        return (
            "我们这边给您发的是导航地址，您按地址过去就可以找到，不用担心找不到地方哈。",
            "您如果要过去，我可以直接把最近门店的导航地址和到店路线发您。",
        )
    if "价格" in q or "多少钱" in q or "最低" in q or "底价" in q or "58元" in q or "199" in q or "268" in q or "380" in q:
        if "58" in q:
            return (
                "您看到的58一般是活动引流信息，具体还是要以您当前参加的活动页面和到店核对为准，避免看错不同城市或不同批次活动。",
                "您把您看到的活动页面发我，我帮您对一下现在对应的是哪档活动价。",
            )
        if "380" in q:
            return (
                "同样是做这个方向，不同活动包装和门店批次会有差别，您这边参加的活动价会更划算一些，所以价格看起来会不一样。",
                "您把您看到的页面发我，我帮您按您这个活动口径对清楚。",
            )
        if "199" in q:
            return (
                "是的，您参加活动的话就是199的价格，属于一次的活动体验费用，到店检测、全脸护理和清理斑点都按这个活动口径来，不存在到店再乱加收费的情况。",
                "您要是想保留这个活动价，我可以继续帮您安排最近方便的门店和时间。",
            )
        if "268" in q:
            return (
                "是的，您说的这个活动口径就是268，到店做检测、全脸护理和清理斑点全程都按活动价来，不会做到一半再给您乱加项目。",
                "您要是方便，我可以继续帮您安排门店和时间，您到店先看效果再决定也可以。",
            )
        if "最低" in q or "底价" in q:
            return (
                "这边活动就是按当前参加的活动口径来给您安排，您主要是想控制预算的话，我建议您先按活动价了解，别被别的高价口径吓住了。",
                "您更在意价格还是效果，我可以按您关心的点直接给您说清楚。",
            )
        return (
            "价格这块要看您参加的是哪档活动口径，一般活动价会比日常价划算很多，到店前我们都会先给您对清楚，不会含糊报价。",
            "您把您看到的活动价或者主要想做的方向告诉我，我直接按那个口径跟您说。",
        )
    if "做完付款" in q or "付全款" in q or "尾款" in q or "有效果再付钱" in q:
        return (
            "到店后老师会先给您做检测，再根据您的情况把内容和价格跟您对清楚，您觉得合适再安排，满意后再补尾款就可以了。",
            "您如果担心付款规则，我也可以把定金和尾款怎么走跟您说清楚。",
        )
    if "优惠" in q or "活动" in q or "名额" in q or "结束" in q:
        return (
            "现在是有活动的，活动名额和时间都是按当前批次走，所以您现在问我，我会按这会儿还能参加的口径给您说清楚。",
            "您要是想保留活动价，我可以继续帮您看最近方便的时间。",
        )
    if "照片" in q or "效果图" in q or "对比" in q or "案例" in q:
        if "假的" in q:
            return (
                "发您的都是参加活动顾客做完恢复后的参考效果，不是随便找图来糊弄您的，不过每个人斑点情况不同，出来的变化也不会一模一样。",
                "您如果想看更接近您情况的，我可以按您的问题方向帮您找同类参考。",
            )
        if "几次" in q or "一次" in q:
            return (
                "我发您的这类图，都是参加活动后的真实参考效果，主要给您看改善方向，不会拿特别夸张的说法来忽悠您。",
                "您如果方便，可以把您更在意的点说一下，我帮您找更接近的参考。",
            )
        return (
            "可以给您看同类改善参考，主要是让您先有个效果预期，不过每个人的斑点深浅和皮肤状态不同，最后变化还是会有差别。",
            "您更想看斑点改善、肤色改善，还是做完恢复后的参考，我可以按这个方向给您找。",
        )
    if "方法" in q or "激光" in q:
        if "皱纹" in q:
            return (
                "没有斑点也可以做别的改善哈，像您更在意皱纹的话，到店会先给您检测皮肤状态，再看更适合做紧致提升还是纹路改善方向。",
                "您如果方便，我可以先按您在意的部位帮您梳理下方向。",
            )
        return (
            "我们不是上来就套一个固定方式，而是先看斑点深浅、范围和皮肤状态，再定更适合您走温和一点还是集中改善一点的方向。",
            "您如果方便，可以说下您主要是老年斑、晒斑，还是颜色不均这类问题。",
        )
    if "一次做好" in q or "多少次" in q or "完全去掉" in q or "再长" in q:
        return (
            "大部分顾客过来做一次变化就已经很明显了，不过每个人的斑点情况不一样，次数和节奏也会不一样，到店会先检测后再给您定得更准。",
            "您如果方便，可以先把大概是什么类型的斑和出现多久了跟我说一下。",
        )
    if "多久才能好" in q or "恢复期" in q or "洗脸吗" in q:
        return (
            "这类做完后的恢复一般会根据个人肤质有差别，通常会给您把护理重点交代清楚，按要求护理就行，不需要太紧张。",
            "您如果是已经做过了，可以把现在的情况和时间发我，我帮您判断下该怎么护理。",
        )
    if "安全" in q or "做坏" in q or "越做越差" in q:
        return (
            "您放心，我们这边不会随便给您乱安排，都会先检测皮肤情况，再按适合的方向来做，主要就是为了把风险降下来。",
            "您更担心安全还是效果，我可以先把您最在意的点说清楚。",
        )
    if "你是门店的人吗" in q:
        return (
            "我是这个活动的负责人，主要帮您安排门店、老师档期和活动口径，您有什么想问的都可以直接问我。",
            "您现在更想先了解价格、效果，还是门店位置？",
        )
    if "专业" in q or "设备更好" in q or "别家" in q:
        return (
            "对比别家的时候，不能只看一句话或一个价格，主要还是看包含内容、做的范围、后续安排和收费是不是说得清楚。",
            "您把对方说的价格或页面发我，我帮您对一下差别在哪。",
        )
    if "骗子" in q or "资质" in q or "医疗资质" in q or "正规吗" in q:
        return (
            "这个您放心，我们门店该有的资质都是有的，到店都可以看得到，也不是今天开明天关的小店了。",
            "您如果主要担心收费或者地址真实性，我也可以一起跟您说清楚。",
        )
    if "隐形消费" in q or "乱收费" in q or "推销" in q or "强迫消费" in q:
        return (
            "您放心，活动价和到店内容都会先跟您对清楚，我们消费是公开透明的，不会把您哄过去再乱加一堆收费。",
            "您如果担心这一点，到店前我也可以先把活动口径跟您再确认一遍。",
        )
    if "做完了出了问题找谁" in q:
        return (
            "做完之后有任何情况都可以直接联系我这边，我会帮您对接专业同事跟进，不会让您找不到人。",
            "您如果现在是担心术后这一块，我也可以先把后续跟进方式跟您说清楚。",
        )
    if "我想去看看" in q or "我想预约" in q:
        return (
            "可以呀，您把所在城市或者大概位置发我，我先帮您安排最近方便的门店和时间。",
            "您如果已经有想去的时间，也可以一起告诉我，我直接往下给您排。",
        )
    if "还要再考虑" in q:
        return (
            "没关系，您先考虑清楚也可以，我这边先把活动和门店信息给您留着，等您方便了再来安排就行。",
            "您如果主要卡在价格、效果还是距离上，我可以按那个点再给您讲清楚。",
        )
    if "改成下周" in q or "临时有事" in q or "没去成" in q:
        return (
            "可以的，我这边先按您新的时间帮您备注，您不用担心白跑，等您方便的时候再重新安排就行。",
            "您大概想改到哪天，我可以继续帮您接着排。",
        )
    if "身份证" in q:
        return (
            "一般按门店正常到店流程来就行，您要是方便带上当然更好，不过最重要的还是按约好的时间过去。",
            "您如果担心到店流程，我也可以顺手把注意事项一起发您。",
        )
    if "包接送" in q or "车费" in q:
        return (
            "目前没有包接送和车费报销这类安排哈，不过您要是不会看路线，我可以帮您把导航地址和过去方式发清楚。",
            "您把您现在大概在哪个位置告诉我，我帮您看怎么过去更方便。",
        )
    if "其他项目" in q:
        return (
            "可以的，像您说的这种情况也可以一起看看，到店会先把面部情况和您想改善的点都看清楚，再给您安排更合适的方向。",
            "您如果方便，可以把还想一起看的部位也先告诉我。",
        )
    if "老客" in q or "我朋友也想做" in q or "我老伴" in q:
        return (
            "可以呀，您身边人如果也想了解，我这边可以一起帮忙看看活动和门店安排，不过还是要先看各自的情况来定。",
            "您可以让她把主要想改善的问题发我，我先帮她做个初步参考。",
        )
    if "天气凉了" in q or "好久不见" in q:
        return (
            "最近天气是凉一点了，您这边最近皮肤状态怎么样呀？要是还想继续做改善，我也可以接着帮您看看。",
            "",
        )
    if "之前咨询过" in q or "好久没联系" in q:
        return (
            "还在的呀，之前您咨询过的内容这边还能继续接着聊，您这次主要是想接着看效果、价格，还是门店安排？",
            "",
        )
    if "我去过" in q and "没效果" in q:
        return (
            "您先别着急，我想先帮您核对一下，看看您之前去的是不是我们现在这家门店，以及当时具体做的是哪一类内容。",
            "您把大概时间、门店位置和做的内容发我，我这边先帮您查清楚。",
        )
    return (
        "您这个问题我先帮您接住，具体会结合您现在的情况给您说清楚，不会随便糊弄您。",
        "您可以把最在意的那个点直接告诉我，我按那个点跟您说。",
    )


def md_escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def main() -> None:
    rows: list[dict[str, Any]] = []
    with SOURCE_JSONL.open(encoding="utf-8") as handle:
        for line in handle:
            rows.append(json.loads(line))

    for row in rows:
        if row.get("error") == "TimeoutError":
            reply_1, reply_2 = supplement_reply(
                str(row.get("question", "")),
                str(row.get("customer_stage", "")),
                str(row.get("scene_type", "")),
            )
            row["reply_1"] = reply_1
            row["reply_2"] = reply_2
            row["all_text_replies"] = [text for text in [reply_1, reply_2] if text]
            row["judgement"] = "补全：按业务规则补写"
            row["error"] = ""
            row["response"] = {"supplemented": True}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ROOT / "logs" / f"ai_customer_reply_273_inprocess_results_supplemented_{timestamp}.jsonl"
    md_path = ROOT / "docs" / f"ai_customer_reply_273_inprocess_report_supplemented_{timestamp}.md"
    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    lines = [
        "# AI 客服 273 条全量回复报告（超时项按业务规则补写）",
        "",
        f"- 基础结果：`{SOURCE_JSONL.name}`",
        "- 说明：仅补写原本 `TimeoutError` 的 90 条，其余结果保持不变",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| 客户阶段 | 场景类型 | 用户问题 | AI实际回复（第1条） | AI引导回复（第2条） | 日志id | 评判 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(row.get("customer_stage", "")),
                    md_escape(row.get("scene_type", "")),
                    md_escape(row.get("question", "")),
                    md_escape(row.get("reply_1", "")),
                    md_escape(row.get("reply_2", "")),
                    md_escape(row.get("log_id", "")),
                    md_escape(row.get("judgement", "")),
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8-sig", newline="\n")
    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
