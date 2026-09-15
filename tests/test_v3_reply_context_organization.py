from ai_paths.app.prompts.reply_context_organization import organize_reply_context
from ai_paths.app.prompts.reply_synthesizer import _select_diverse_argument_candidates


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


def test_candidate_selection_caps_and_diversifies_arguments():
    def candidate(identifier, checkpoint, action):
        return {'id': identifier, 'checkpoint_code': checkpoint, 'action_code': action}
    value = {'sequence_candidates': [{'id':'s1'}, {'id':'s2'}], 'candidates': [
        candidate('a','price','explain'), candidate('duplicate','price','explain'),
        candidate('b','price','proof'), candidate('c','trust','proof'), candidate('d','distance','switch'),
    ]}
    selected = _select_diverse_argument_candidates(value)
    assert [item['id'] for item in selected['candidates']] == ['a','b','c']
    assert [item['id'] for item in selected['sequence_candidates']] == ['s1']
