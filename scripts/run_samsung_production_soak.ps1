[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Error "virtual environment Python not found: $Python"
    exit 3
}

$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $RepoRoot 'src'
$ExitCode = 1

Push-Location $RepoRoot
try {
    # Task Scheduler is only the launcher; the runner, lock, persistence and logs are portable Python.
    & $Python -m smartwatch_clank.soak_runner
    $ExitCode = $LASTEXITCODE
    if ($null -eq $ExitCode) { $ExitCode = 1 }
}
catch {
    Write-Error ($_ | Out-String).Trim()
    $ExitCode = 1
}
finally {
    Pop-Location
    $env:PYTHONPATH = $PreviousPythonPath
}

exit $ExitCode
