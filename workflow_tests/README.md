# 工作流测试说明

## 运行前准备

在 PowerShell 中设置环境变量：

```powershell
$env:COZE_WORKLOAD_API_TOKEN="你的 Coze PAT"
$env:COZE_API_BASE_URL="https://api.coze.cn"
```

不要把 token 写入脚本或测试结果。

## 执行 SF1 测试

```powershell
.\workflow_tests\run_workflow_tests.ps1 `
  -CasesPath .\workflow_tests\sf1_new_customer_reply_cases.json `
  -OutPath .\workflow_tests\results\sf1_new_customer_reply_results.json
```

## 当前判定重点

SF1 新客首响不是项目推荐节点。它的核心质量要求：

- 能自然承接首次打招呼或低意图咨询。
- 不要直接报价、预约、问门店。
- 不要上来强推具体项目或产品。
- 可以简单说明服务范围，但要短。
- 最多问一个关键问题，引导客户说出关注方向。
- 普通新客不要转人工。

