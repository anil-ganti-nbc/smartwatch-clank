[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Smartwatch Clank - Samsung Production Soak'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot 'run_samsung_production_soak.ps1'

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Scheduled runner not found: $Runner"
}

$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Runner`""
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $RepoRoot

# Twelve daily triggers create a permanent two-hour cadence without an artificial end date.
$Triggers = foreach ($Hour in 0,2,4,6,8,10,12,14,16,18,20,22) {
    New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($Hour).AddMinutes(30))
}

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 90) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask -Action $Action -Trigger $Triggers -Settings $Settings -Principal $Principal `
    -Description 'Runs the existing Smartwatch Clank Samsung production soak every two hours. Discord remains disabled.'

if ($PSCmdlet.ShouldProcess($TaskName, 'Create or update current-user scheduled task')) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
    Write-Output "Installed: $TaskName"
    Write-Output "Schedule: 00:30, 02:30, 04:30, 06:30, 08:30, 10:30, 12:30, 14:30, 16:30, 18:30, 20:30, 22:30 local time"
    if ($RunNow) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Output 'Manual task trigger requested.'
    }
}

