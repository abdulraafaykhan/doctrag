param([switch]$Build)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
$localNpm = Join-Path (Get-Location) 'data\tools\node_modules\npm\bin\npm-cli.js'
if (-not $npmCommand -and -not (Test-Path -LiteralPath $localNpm)) { throw 'Install Node 20+ with npm.' }
function Invoke-Frontend([string[]]$Arguments) {
    if ($npmCommand) { & $npmCommand.Source @Arguments } else { & node $localNpm @Arguments }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend command failed.' }
}
if ($Build) { Invoke-Frontend -Arguments @('--prefix', 'frontend', 'run', 'build'); exit }
& node scripts/dev.mjs $python $(if ($npmCommand) { $npmCommand.Source } else { $localNpm })
