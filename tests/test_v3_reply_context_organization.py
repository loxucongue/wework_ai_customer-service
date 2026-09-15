from ai_paths.scripts.reply_context_organization import organize_rendered_reply_context, organize_reply_context


def test_context_groups_each_source_block_exactly_once():
    blocks = [
        "【当前时间】\nnow", "【完整聊天】\nchat", "【当前结构事实与不能越过的边界】\nstate",
        "【已交付内容】\ndelivered", "【本轮相关权威事实】\nfacts", "【销售主线机会与本轮动作边界（不是每轮流程任务）】\nnext",
        "【跟进序列与话术素材（取其意思，不模仿句式）】\nargument", "【输出引用与结构边界】\nrefs",
        "请只返回 json。",
    ]
    rendered = organize_reply_context(blocks)
    assert [rendered.count(word) for word in ('now','chat','state','delivered','facts','next','argument','refs')] == [1] * 8
    assert rendered.index('【当前对话】') < rendered.index('【已知客户情况】') < rendered.index('【已交付内容】')
    assert rendered.index('【已交付内容】') < rendered.index('【相关事实与执行边界】') < rendered.index('【候选论据与销售方向】')


def test_persisted_context_keeps_blank_paragraphs_inside_source_section():
    source = "【完整聊天】\n客户第一句\n\n客户第二句\n\n【本轮相关权威事实】\n事实一\n\n事实二\n\n请只返回 json。"
    rendered = organize_rendered_reply_context(source)
    for value in ("客户第一句", "客户第二句", "事实一", "事实二", "请只返回 json"):
        assert rendered.count(value) == 1


def test_persisted_context_uses_same_three_angle_budget():
    scripts = "\n".join(f"话术ID={i}｜卡点=c{i}｜动作=a{i}\n  参考表达：value{i}" for i in range(5))
    rendered = organize_rendered_reply_context(f"【完整聊天】\nchat\n\n【跟进序列与话术素材（取其意思，不模仿句式）】\n{scripts}")
    assert rendered.count("话术ID=") == 3
    assert "value0" in rendered and "value2" in rendered and "value4" not in rendered
