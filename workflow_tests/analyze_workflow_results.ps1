param(
  [Parameter(Mandatory=$true)]
  [string]$ResultsPath,

  [Parameter(Mandatory=$true)]
  [string]$ReportPath,

  [string]$FlowName = "unknown"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ResultsPath)) {
  throw "Results file not found: $ResultsPath"
}

function Join-OutputText($output) {
  if (-not $output) { return "" }
  $parts = New-Object System.Collections.Generic.List[string]
  foreach ($item in @($output)) {
    if ($item -and $item.content) {
      $parts.Add([string]$item.content)
    }
  }
  return ($parts -join "`n")
}

function Count-Questions($text) {
  if (-not $text) { return 0 }
  return ([regex]::Matches($text, "[?？]")).Count
}

function Has-Any($text, [string[]]$patterns) {
  if (-not $text) { return $false }
  foreach ($p in $patterns) {
    if ($text -match $p) { return $true }
  }
  return $false
}

$results = Get-Content -LiteralPath $ResultsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$caseRows = New-Object System.Collections.Generic.List[object]

$summary = [ordered]@{
  total = 0
  api_error = 0
  empty_output = 0
  fallback_reply = 0
  likely_handoff = 0
  likely_appointment = 0
  likely_store = 0
  likely_price = 0
  likely_over_recommend = 0
  too_many_questions = 0
  too_long_single_message = 0
  pass = 0
}

foreach ($r in @($results)) {
  $summary.total += 1

  $issues = New-Object System.Collections.Generic.List[string]
  $output = @($r.actual.output)
  $text = Join-OutputText $output

  if ($r.error) {
    $summary.api_error += 1
    $issues.Add("api_error")
  }

  if (-not $output -or $output.Count -eq 0 -or [string]::IsNullOrWhiteSpace($text)) {
    $summary.empty_output += 1
    $issues.Add("empty_output")
  }

  if ($text -match "抱歉，未能获取到回复内容|未能获取到回复|获取回复失败") {
    $summary.fallback_reply += 1
    $issues.Add("fallback_reply")
  }

  if (Has-Any $text @("转人工", "人工.*跟进", "安排.*顾问", "顾问.*接上", "人工客服")) {
    $summary.likely_handoff += 1
    $issues.Add("likely_handoff")
  }

  if (Has-Any $text @("预约", "到店", "什么时候方便", "周几.*来", "帮你登记", "保留.*名额")) {
    $summary.likely_appointment += 1
    $issues.Add("likely_appointment_push")
  }

  if (Has-Any $text @("门店", "地址", "位置", "你在哪个城市", "附近门店", "路线")) {
    $summary.likely_store += 1
    $issues.Add("likely_store_match")
  }

  if (Has-Any $text @("价格", "多少钱", "费用", "报价", "优惠价", "活动价", "体验价")) {
    $summary.likely_price += 1
    $issues.Add("likely_price_or_campaign")
  }

  if (Has-Any $text @("推荐.*水光", "推荐.*光子", "推荐.*皮秒", "推荐.*热玛吉", "推荐.*超声炮", "水杨酸", "小气泡", "项目.*适合你")) {
    $summary.likely_over_recommend += 1
    $issues.Add("likely_over_recommend")
  }

  if ((Count-Questions $text) -gt 1) {
    $summary.too_many_questions += 1
    $issues.Add("too_many_questions")
  }

  foreach ($item in $output) {
    if ($item.content -and ([string]$item.content).Length -gt 90) {
      $summary.too_long_single_message += 1
      $issues.Add("too_long_single_message")
      break
    }
  }

  $pass = ($issues.Count -eq 0)
  if ($pass) { $summary.pass += 1 }

  $caseRows.Add([pscustomobject][ordered]@{
    case_id = $r.case_id
    content = $r.content
    scene = $r.actual.scene
    intent = $r.actual.intent
    output_count = $output.Count
    issues = @($issues)
    pass = $pass
    reply = $text
  })
}

$passRate = if ($summary.total -gt 0) { [math]::Round(($summary.pass / $summary.total) * 100, 2) } else { 0 }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# $FlowName 测试结果分析")
$lines.Add("")
$lines.Add("## 汇总")
$lines.Add("")
$lines.Add("| 指标 | 数量 |")
$lines.Add("|---|---:|")
$lines.Add("| 总用例 | $($summary.total) |")
$lines.Add("| 自动通过 | $($summary.pass) |")
$lines.Add("| 自动通过率 | $passRate% |")
$lines.Add("| API错误 | $($summary.api_error) |")
$lines.Add("| 空回复 | $($summary.empty_output) |")
$lines.Add("| 兜底失败话术 | $($summary.fallback_reply) |")
$lines.Add("| 疑似转人工 | $($summary.likely_handoff) |")
$lines.Add("| 疑似主动预约 | $($summary.likely_appointment) |")
$lines.Add("| 疑似门店匹配 | $($summary.likely_store) |")
$lines.Add("| 疑似价格/活动 | $($summary.likely_price) |")
$lines.Add("| 疑似过早推荐项目 | $($summary.likely_over_recommend) |")
$lines.Add("| 问题超过1个 | $($summary.too_many_questions) |")
$lines.Add("| 单条过长 | $($summary.too_long_single_message) |")
$lines.Add("")
$lines.Add("## 明细")
$lines.Add("")
$lines.Add("| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |")
$lines.Add("|---|---|---|---|---|---|---|")

foreach ($row in $caseRows) {
  $reply = ($row.reply -replace "`r", " " -replace "`n", " ")
  if ($reply.Length -gt 90) {
    $reply = $reply.Substring(0, 90) + "..."
  }
  $issueText = if ($row.issues.Count -gt 0) { ($row.issues -join ", ") } else { "" }
  $content = ([string]$row.content) -replace "\|", "\\|"
  $replyEscaped = $reply -replace "\|", "\\|"
  $lines.Add("| $($row.case_id) | $content | $($row.scene) | $($row.intent) | $($row.pass) | $issueText | $replyEscaped |")
}

$lines.Add("")
$lines.Add("## 说明")
$lines.Add("")
$lines.Add("- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。")
$lines.Add("- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。")
$lines.Add("- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。")

$reportDir = Split-Path -Parent $ReportPath
if ($reportDir -and -not (Test-Path -LiteralPath $reportDir)) {
  New-Item -ItemType Directory -Path $reportDir | Out-Null
}

$lines | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "Saved report to $ReportPath"


