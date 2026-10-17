# Exercise the installer helpers with an isolated, unseeded Python environment.
# No ComfyUI, GPU, existing environment or model files are modified.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
$Python = (Resolve-Path -LiteralPath $Python).Path
$testRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $Python))
$packageRoot = $PSScriptRoot
$logPath = Join-Path $testRoot 'helper-test.log'
$constraints = Join-Path $testRoot 'constraints with spaces.txt'
[System.IO.File]::WriteAllText($constraints, '')
$Uv = (Get-Command uv.exe -ErrorAction Stop).Source
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'install-anima-controls.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Installer syntax errors' }
foreach ($name in @('Run-Checked','Test-Command','Run-Python','Test-Python','Install-Packages','Find-ComfyRoot')) {
    $definition = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    Invoke-Expression $definition.Extent.Text
}
$value = Run-Python 'import sys; print("quotes survive", sys.argv[1])' @('argument with spaces')
if ($value -ne 'quotes survive argument with spaces') { throw 'Python quoting/argument regression' }
if (-not (Test-Python 'import sys')) { throw 'Import success was rejected' }
if (Test-Python 'import nonexistent_anima_installer_fixture') { throw 'Missing import was accepted' }
$failed = $false
try { Run-Python 'raise RuntimeError("expected failure")' } catch { $failed = $true }
if (-not $failed) { throw 'Python failure was swallowed' }
Install-Packages @('nvidia-ml-py==13.580.82')
if (-not (Test-Python 'import pynvml')) { throw 'NVML not importable after installation' }
Run-Python 'import importlib.metadata,sys; from pathlib import Path; p=Path(sys.argv[1]); p.write_text(importlib.metadata.version("nvidia-ml-py"))' @($constraints)
if ((Get-Content -LiteralPath $constraints -Raw) -ne '13.580.82') { throw 'Wrong target environment or broken Python code' }
[System.IO.File]::WriteAllText($constraints, 'nvidia-ml-py==13.580.82')
$requirements = Join-Path $testRoot 'requirements with spaces.txt'
[System.IO.File]::WriteAllText($requirements, 'nvidia-ml-py==13.580.82')
Install-Packages @('-r', $requirements)
[System.IO.File]::WriteAllText($requirements, 'nvidia-ml-py==12.560.30')
$conflictRejected = $false
try { Install-Packages @('-r', $requirements) } catch { $conflictRejected = $true }
if (-not $conflictRejected) { throw 'Version constraint was ignored' }
Run-Python 'import importlib.metadata; assert importlib.metadata.version("nvidia-ml-py") == "13.580.82"'
Run-Python 'import pynvml,sys; from pathlib import Path; assert Path(pynvml.__file__).is_relative_to(Path(sys.prefix)); print("Installed into:",sys.prefix)'
$oldAppData = $env:APPDATA
# Only APPDATA is redirected; HOME is never overwritten.
try {
    $env:APPDATA = Join-Path $testRoot 'fake appdata'
    $configDir = Join-Path $env:APPDATA 'ComfyUI'
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $testRoot 'custom_nodes') | Out-Null
    @{basePath=$testRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $configDir 'config.json') -Encoding UTF8
    if ((Find-ComfyRoot) -ne $testRoot) { throw 'Desktop environment autodetection failed' }
    $managedRoot = Join-Path $testRoot 'managed installation'
    $managedComfy = Join-Path $managedRoot 'ComfyUI'
    New-Item -ItemType Directory -Force -Path (Join-Path $managedComfy 'custom_nodes'), (Join-Path $managedComfy '.venv\Scripts') | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $managedComfy '.venv\Scripts\python.exe') | Out-Null
    @{installPath=$managedRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $configDir 'config.json') -Encoding UTF8
    if ((Find-ComfyRoot) -ne $managedComfy) { throw 'Managed Desktop nested environment detection failed' }
} finally { $env:APPDATA = $oldAppData }
Write-Host "PASS: PowerShell $($PSVersionTable.PSVersion), stdin quoting, imports, errors, uv without pip, NVML, environment isolation and Desktop detection."
