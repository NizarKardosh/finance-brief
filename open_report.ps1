# Opens today's Morning Brief in the default browser (once per day).
# Falls back to the most recent report if today's isn't ready.
param([string]$Date = (Get-Date -Format 'yyyy-MM-dd'), [switch]$Force)

$Root = $PSScriptRoot
$flag = "$Root\data\$Date\.opened"
if ((Test-Path $flag) -and -not $Force) { return }

$report = "$Root\reports\$Date.html"
if (-not (Test-Path $report)) {
    $latest = Get-ChildItem "$Root\reports\*.html" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { return }
    $report = $latest.FullName
} else {
    New-Item -ItemType Directory -Force "$Root\data\$Date" | Out-Null
    Set-Content -Path $flag -Value (Get-Date -Format o)
}
Start-Process $report
