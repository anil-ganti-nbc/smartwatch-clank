[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'Smartwatch Clank - Samsung Production Soak'
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -eq $Task) {
    Write-Output "Not installed: $TaskName"
    exit 0
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Remove scheduled task only')) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task: $TaskName"
    Write-Output 'Database, observations, discoveries, candidates, configuration, and logs were retained.'
}

