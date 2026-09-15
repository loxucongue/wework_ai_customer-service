import pytest

from ai_paths.scripts.v3_reply_effectiveness_dataset import redact_text, split_cases, stratified_split_cases


def test_effectiveness_ablation_resolves_and_records_exact_baseline(monkeypatch):
    from ai_paths.scripts import evaluate_v3_reply_effectiveness as evaluator

    full_sha = "a" * 40
    calls = []

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, **_kwargs):
        calls.append(tuple(command))
        if command[1] == "rev-parse":
            return Result(full_sha + "\n")
        if command[1] == "show":
            return Result("PARALLEL_REPLY_SYSTEM_PROMPT = 'baseline'\n")
        raise AssertionError(command)

    monkeypatch.setattr(evaluator.subprocess, "run", fake_run)
    assert evaluator._resolve_baseline("origin/main") == (full_sha, "baseline")
    assert calls == [
        ("git", "rev-parse", "--verify", "origin/main^{commit}"),
        ("git", "show", f"{full_sha}:{evaluator.PROMPT_PATH}"),
    ]


def test_effectiveness_ablation_requires_explicit_baseline_ref():
    with pytest.raises(SystemExit):
        from ai_paths.scripts.evaluate_v3_reply_effectiveness import parser

        parser().parse_args([])


def test_redaction_preserves_price_but_removes_known_ids_and_signed_urls():
    text = redact_text('客户secret-contact 13812345678 活动268元 https://host/a?token=secret', {'secret-contact':'customer-demo'})
    assert '268元' in text and 'customer-demo' in text
    assert '13812345678' not in text and 'token=' not in text and 'host' not in text


def test_split_is_frozen_and_contact_disjoint():
    rows = [{'case_id': f'c{i}', 'group_id': f'g{i//2}', 'customer_context': f'example {i}'} for i in range(120)]
    result = split_cases(rows)
    assert result == split_cases(list(reversed(rows)))
    assert len(result['development']) == 50 and len(result['holdout']) == 30
    assert not {r['group_id'] for r in result['development']} & {r['group_id'] for r in result['holdout']}


def test_near_duplicate_dialogues_cannot_cross_split():
    rows = [{'case_id': f'c{i}', 'group_id': f'g{i}', 'customer_context': f'短句{i}'} for i in range(10)]
    rows[0]['customer_context'] = '需要明确了解之前活动具体包含哪些项目和如何预约以及相关的费用'
    rows[1]['customer_context'] = rows[0]['customer_context'] + '呢'
    result = split_cases(rows, development_count=4, holdout_count=4)
    sets = [{r['case_id'] for r in result[key]} for key in ('development','holdout')]
    assert not ('c0' in sets[0] and 'c1' in sets[1] or 'c1' in sets[0] and 'c0' in sets[1])


def test_insufficient_samples_refused_not_relabelled_synthetic():
    with pytest.raises(ValueError, match='insufficient'):
        split_cases([{'case_id':'a','group_id':'one','customer_context':'你好'}])


def test_stratified_split_meets_quotas_without_contact_overlap():
    rows = []
    annotations = []
    for category in ("one","two"):
        for index in range(12):
            case_id=f"{category}-{index}"
            rows.append({"case_id":case_id,"group_id":f"{category}-g{index}","customer_context":case_id})
            annotations.append({"case_id":case_id,"category":category,"observed_quality":"good" if index==0 else "mixed"})
    result=stratified_split_cases(rows,annotations,development_quotas={"one":4,"two":4},
                                  holdout_quotas={"one":3,"two":3})
    assert len(result["development"])==8 and len(result["holdout"])==6
    assert not {row["group_id"] for row in result["development"]} & {row["group_id"] for row in result["holdout"]}
    assert {row["review_brief"]["category"] for row in result["development"]}=={"one","two"}
