# Registers two Windows scheduled tasks for the current user:
#   MorningBrief-Generate  (config.generate_time, default 07:30) - builds the report
#   MorningBrief-Open      (config.open_time,     default 08:00) - opens it in the browser
# Run:  powershell -ExecutionPolicy Bypass -File install_schedule.ps1      (add -Remove to uninstall)
param([switch]$Remove)

$Root = $PSScriptRoot
$names = 'MorningBrief-Generate', 'MorningBrief-Open'
foreach ($n in $names) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
if ($Remove) { 'Removed.'; return }

$cfg = Get-Content "$Root\config.json" -Raw | ConvertFrom-Json
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

function Make($name, $script, $time, $desc) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\$script`"" -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -Daily -At $time
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description $desc | Out-Null
    "Registered $name at $time"
}
Make 'MorningBrief-Generate' 'run_daily.ps1'   $cfg.generate_time 'Builds the daily finance Morning Brief HTML report.'
Make 'MorningBrief-Open'     'open_report.ps1' $cfg.open_time     'Opens the daily Morning Brief in the browser.'
