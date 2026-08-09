[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$TaskName = 'Smartwatch Clank - Samsung Production Soak'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Database = Join-Path $RepoRoot 'var\smartwatch-clank.sqlite3'
$Lock = "$Database.lock"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -eq $Task) {
    Write-Output 'Task installed: no'
} else {
    $Info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Output 'Task installed: yes'
    Write-Output "Task enabled: $($Task.State -ne 'Disabled')"
    Write-Output "Task state: $($Task.State)"
    Write-Output "Last scheduled run: $($Info.LastRunTime)"
    Write-Output "Last Task Scheduler result: $($Info.LastTaskResult)"
    Write-Output "Next scheduled run: $($Info.NextRunTime)"
}

Write-Output "Production database: $Database"
Write-Output "Production database exists: $(Test-Path -LiteralPath $Database)"
if (Test-Path -LiteralPath $Lock) {
    Write-Output "Current lock: $(Get-Content -LiteralPath $Lock -Raw)"
} else {
    Write-Output 'Current lock: none'
}

if (Test-Path -LiteralPath $Python -PathType Leaf) {
    $PreviousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $RepoRoot 'src'
    Push-Location $RepoRoot
    try {
        Write-Output 'Smartwatch Clank health:'
        & $Python -m smartwatch_clank.cli health
        Write-Output 'Smartwatch Clank soak summary (30 days):'
        & $Python -m smartwatch_clank.cli soak report --days 30
    } finally {
        Pop-Location
        $env:PYTHONPATH = $PreviousPythonPath
    }
} else {
    Write-Output "Smartwatch Clank health unavailable: virtual environment Python not found at $Python"
}
