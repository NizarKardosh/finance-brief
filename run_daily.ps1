# Generates today's Morning Brief: subagent news + market data -> reports\<date>.html
param([string]$Date = (Get-Date -Format 'yyyy-MM-dd'))

$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force "$Root\logs", "$Root\data\$Date", "$Root\reports" | Out-Null
$Log = "$Root\logs\$Date.log"
function Log($msg) { "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg | Tee-Object -FilePath $Log -Append }

$Config = Get-Content "$Root\config.json" -Raw | ConvertFrom-Json
$Claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
if (-not (Test-Path $Claude)) { $Claude = (Get-Command claude -ErrorAction SilentlyContinue).Source }
$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) { $Python = 'C:\Python313\python.exe' }

Log "=== Morning Brief $Date ==="
# Tokens copied from a wrapped terminal line often contain a line break; strip any whitespace.
if ($env:CLAUDE_CODE_OAUTH_TOKEN) { $env:CLAUDE_CODE_OAUTH_TOKEN = $env:CLAUDE_CODE_OAUTH_TOKEN -replace '\s', '' }
Remove-Item "$Root\data\$Date\status.json" -ErrorAction SilentlyContinue

# Re-runs on the same day: subagents can't overwrite existing files, so move the previous run aside.
$DataDir = "$Root\data\$Date"
$Prev = "$DataDir\previous-run"
$outputs = 'news_google.json', 'news_yahoo.json', 'news_bloomberg.json', 'pulse.json', 'briefing.json'
if ($outputs | Where-Object { Test-Path "$DataDir\$_" }) {
    Remove-Item $Prev -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $Prev | Out-Null
    foreach ($f in $outputs) { if (Test-Path "$DataDir\$f") { Move-Item "$DataDir\$f" $Prev } }
    Log 'Moved previous run outputs to data\<date>\previous-run'
}

# 1) Orchestrator + subagents (20 min timeout)
$prompt = (Get-Content "$Root\prompts\orchestrator.md" -Raw).Replace('{{DATE}}', $Date)
$tools = 'Agent,Task,WebFetch,WebSearch,Read,Glob,Grep,Write,Edit,Bash(python fetch_markets.py:*)'
$job = Start-Job -ScriptBlock {
    param($Root, $Claude, $prompt, $tools)
    Set-Location $Root
    $env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = '1080000'   # let background subagents finish (18 min) before -p exits
    $prompt | & $Claude -p --output-format text --max-turns 80 --settings "$Root\agent_permissions.json" --allowedTools $tools 2>&1
    "EXIT:$LASTEXITCODE"
} -ArgumentList $Root, $Claude, $prompt, $tools

if (Wait-Job $job -Timeout 1200) {
    $out = @(Receive-Job $job)
    $out | ForEach-Object { Log "claude> $_" }
    if ($out -match 'Failed to authenticate|not logged in|Invalid API key') {
        Log 'AUTH ERROR: the claude CLI is not logged in. Run "claude auth login" (or "claude setup-token").'
        '{"auth_error": true}' | Set-Content "$Root\data\$Date\status.json"
    }
} else {
    Log 'Orchestrator timed out after 20 min; stopping it.'
    Stop-Job $job
}
Remove-Job $job -Force

# If this run produced no briefing but an earlier run today did, restore the earlier one.
if (-not (Test-Path "$DataDir\briefing.json") -and (Test-Path "$Prev\briefing.json")) {
    Log 'No new briefing - restoring the previous run'
    foreach ($f in $outputs) { if ((Test-Path "$Prev\$f") -and -not (Test-Path "$DataDir\$f")) { Copy-Item "$Prev\$f" $DataDir } }
}

# 2) Make sure market data exists even if the agent step failed
if (-not (Test-Path "$Root\data\$Date\markets.json")) {
    Log 'markets.json missing - fetching directly'
    & $Python "$Root\fetch_markets.py" $Date 2>&1 | ForEach-Object { Log "py> $_" }
}

# 3) Render
& $Python "$Root\render.py" $Date 2>&1 | ForEach-Object { Log "py> $_" }

# 4) If we're past the open time (e.g. the PC was off at the scheduled time), open now
$openAt = [datetime]::ParseExact($Config.open_time, 'HH:mm', $null)
if ((Get-Date) -ge $openAt -and $Date -eq (Get-Date -Format 'yyyy-MM-dd')) {
    & "$Root\open_report.ps1" -Date $Date
}
Log 'Done.'
