# 测试编码事故记录

时间：2026-05-21

## 结论

之前 SF1-SF4 的工作流测试请求存在入参编码问题：PowerShell 调用 Coze 工作流 API 时，中文 JSON 正文被转换成 `????????`，导致 Coze Trace 中的 `content` 和 `conversation_history` 变成乱码。

这属于测试脚本请求编码问题，不是 Coze 模型主动生成乱码，也不是工作流节点本身把中文改坏。

## 影响范围

以下结果和报告均需要作废并重跑：

- `workflow_tests/results/sf1_new_customer_reply_results.json`
- `workflow_tests/results/sf2_profile_collect_results.json`
- `workflow_tests/results/sf3_project_consult_results.json`
- `workflow_tests/results/sf4_face_consult_results.json`
- `workflow_tests/reports/sf1_new_customer_reply_report.md`
- `workflow_tests/reports/sf1_new_customer_reply_manual_report.md`
- `workflow_tests/reports/sf2_profile_collect_report.md`
- `workflow_tests/reports/sf2_profile_collect_manual_report.md`
- `workflow_tests/reports/sf3_project_consult_report.md`
- `workflow_tests/reports/sf3_project_consult_manual_report.md`
- `workflow_tests/reports/sf4_face_consult_report.md`
- `workflow_tests/reports/sf4_face_consult_manual_report.md`

## 修复方式

`workflow_tests/run_workflow_tests.ps1` 已改为：

- 将 JSON body 显式转换为 UTF-8 bytes。
- 请求头使用 `application/json; charset=utf-8`。

关键逻辑：

```powershell
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
Invoke-RestMethod `
  -ContentType "application/json; charset=utf-8" `
  -Body $bodyBytes
```

## 验证

新增 smoke case：

- `workflow_tests/smoke_utf8_case.json`
- 输出文件：`workflow_tests/results/smoke_utf8_result.json`

测试输入：`你好，我想了解祛斑`

工作流返回了正常中文项目咨询回复，说明修复后的请求已能被工作流按中文内容处理。

## 后续处理

从 SF1 开始重新执行测试。旧测试只保留作事故追溯，不纳入最终优化报告。

## 清理记录

2026-05-21 已按用户要求删除旧乱码测试的结果文件和报告文件。当前仅保留本事故记录、测试用例、以及 `_utf8_` 后缀的新测试结果/报告。
