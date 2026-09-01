from app.services.outreach.first_day import classify_conversation_refresh_error


def test_conversation_refresh_account_not_found_is_not_reported_as_timeout() -> None:
    code, warning = classify_conversation_refresh_error(
        'RuntimeError: outreach_system_http_404: {"code":40401,"msg":"account not found for corp_id and wechat"}'
    )

    assert code == "conversation_account_not_found"
    assert "客服账号" in warning
    assert "超时" not in warning


def test_conversation_refresh_timeout_is_reported_as_timeout() -> None:
    code, warning = classify_conversation_refresh_error("ReadTimeout: ")

    assert code == "conversation_refresh_timeout"
    assert "超时" in warning


def test_conversation_refresh_other_error_stays_generic() -> None:
    code, warning = classify_conversation_refresh_error("RuntimeError: outreach_system_http_500")

    assert code == "conversation_refresh_failed"
    assert "查询失败" in warning
    assert "超时" not in warning
