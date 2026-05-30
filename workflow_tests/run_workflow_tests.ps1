param(
  [Parameter(Mandatory=$true)]
  [string]$CasesPath,

  [Parameter(Mandatory=$true)]
  [string]$OutPath,

  [string]$WorkflowId = "7639623828015988742",
  [string]$ApiBaseUrl = $env:COZE_API_BASE_URL,
  [int]$DelayMs = 700
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not $ApiBaseUrl) {
  $ApiBaseUrl = "https://api.coze.cn"
}

if (-not $env:COZE_WORKLOAD_API_TOKEN) {
  throw "Missing environment variable COZE_WORKLOAD_API_TOKEN. Set it before running tests."
}

if (-not (Test-Path -LiteralPath $CasesPath)) {
  throw "Cases file not found: $CasesPath"
}

$cases = Get-Content -LiteralPath $CasesPath -Raw -Encoding UTF8 | ConvertFrom-Json
$results = New-Object System.Collections.Generic.List[object]

$outDir = Split-Path -Parent $OutPath
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
  New-Item -ItemType Directory -Path $outDir | Out-Null
}

$completedCaseIds = @{}
if (Test-Path -LiteralPath $OutPath) {
  try {
    $existing = Get-Content -LiteralPath $OutPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in @($existing)) {
      if ($item.case_id) {
        $completedCaseIds[[string]$item.case_id] = $true
        $results.Add($item)
      }
    }
    Write-Host ("Loaded {0} existing results from {1}" -f $results.Count, $OutPath)
  } catch {
    Write-Host "Existing result file could not be parsed; starting a fresh run."
    $results.Clear()
    $completedCaseIds = @{}
  }
}

$headers = @{
  Authorization = "Bearer $($env:COZE_WORKLOAD_API_TOKEN)"
}

$index = 0
foreach ($case in $cases) {
  $index += 1

  if ($completedCaseIds.ContainsKey([string]$case.case_id)) {
    Write-Host ("[{0}/{1}] {2} skipped" -f $index, $cases.Count, $case.case_id)
    continue
  }

  $body = @{
    workflow_id = $WorkflowId
    parameters = @{
      content = [string]$case.content
      customer_id = [string]$case.customer_id
      corp_id = [string]$case.corp_id
      conversation_history = @($case.conversation_history)
      file_image = [string]$case.file_image
    }
  } | ConvertTo-Json -Depth 12

  $startedAt = Get-Date -Format "o"
  $raw = $null
  $parsedData = $null
  $errorMessage = ""

  try {
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $raw = Invoke-RestMethod `
      -Method Post `
      -Uri "$ApiBaseUrl/v1/workflow/run" `
      -Headers $headers `
      -ContentType "application/json; charset=utf-8" `
      -Body $bodyBytes `
      -TimeoutSec 600
    if ($raw.code -eq 0 -and $raw.data) {
      try {
        $parsedData = $raw.data | ConvertFrom-Json
      } catch {
        $errorMessage = "Workflow data JSON parse failed: $($_.Exception.Message)"
      }
    } elseif ($raw.code -ne 0) {
      $errorMessage = "Workflow returned non-zero code: $($raw.code) $($raw.msg)"
    }
  } catch {
    $errorMessage = $_.Exception.Message
  }

  $output = @()
  $intent = $null
  $scene = $null

  if ($parsedData) {
    if ($parsedData.output) {
      $output = @($parsedData.output)
    }
    if ($parsedData.intent) {
      $intent = $parsedData.intent
    }
    if ($parsedData.scene) {
      $scene = $parsedData.scene
    }
  }

  $result = [ordered]@{
    case_id = $case.case_id
    target_flow = $case.target_flow
    content = $case.content
    expected = $case.expected
    actual = [ordered]@{
      code = if ($raw) { $raw.code } else { $null }
      msg = if ($raw) { $raw.msg } else { $null }
      execute_id = if ($raw) { $raw.execute_id } else { $null }
      scene = $scene
      intent = $intent
      output = $output
    }
    error = $errorMessage
    started_at = $startedAt
    finished_at = Get-Date -Format "o"
  }

  $results.Add([pscustomobject]$result)
  $results | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutPath -Encoding UTF8
  Write-Host ("[{0}/{1}] {2} done" -f $index, $cases.Count, $case.case_id)
  Start-Sleep -Milliseconds $DelayMs
}

$results | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutPath -Encoding UTF8
Write-Host "Saved results to $OutPath"



