# Install only the pinned Anima controls into an existing Windows ComfyUI.
# Run with ComfyUI closed. No administrator rights or new Python are required.
[CmdletBinding()]
param(
    [string]$ComfyRoot,
    [string]$Python,
    [string]$ModelsRoot,
    [string]$CharacterSource,
    [switch]$DependenciesOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$packageRoot = Split-Path -Parent $PSCommandPath
$pins = Get-Content -LiteralPath (Join-Path $packageRoot 'revisions.json') -Raw | ConvertFrom-Json
$logPath = Join-Path $packageRoot ('install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
Start-Transcript -LiteralPath $logPath | Out-Null

function Run-Python([string]$Code, [string[]]$Arguments = @()) {
    # Send code over stdin: Windows PowerShell 5.1 removes quotes from -c code.
    $Code | & $Python - @Arguments
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI Python failed (exit $LASTEXITCODE). Log: $logPath" }
}
function Test-Python([string]$Code) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $Code | & $Python - 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $previousPreference }
}
function Find-ComfyRoot {
    $candidates = @((Get-Location).Path, (Join-Path $HOME 'Documents\ComfyUI'))
    foreach ($app in @('ComfyUI', 'Comfy Desktop')) {
        $configPath = Join-Path $env:APPDATA "$app\config.json"
        if (Test-Path -LiteralPath $configPath) {
            $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            foreach ($key in @('basePath', 'installPath')) {
                if ($config.$key) { $candidates += [string]$config.$key }
            }
        }
    }
    $candidates = @($candidates | ForEach-Object { $_; Join-Path $_ 'ComfyUI' })
    $found = @($candidates | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_ 'custom_nodes')) -and
        (Test-Path -LiteralPath (Join-Path $_ '.venv\Scripts\python.exe'))
    } | Select-Object -Unique)
    if ($found.Count -eq 1) { return $found[0] }
    return Select-Folder 'Select your ComfyUI installation folder (or the folder containing custom_nodes and .venv)'
}
function Install-Packages([string[]]$Packages) {
    if ($Uv) {
        # uv accepts file URIs without splitting paths containing spaces.
        $uvPackages = @($Packages)
        for ($index = 0; $index -lt $uvPackages.Count - 1; $index++) {
            if ($uvPackages[$index] -eq '-r') {
                $uvPackages[$index + 1] = ([System.Uri](Resolve-Path -LiteralPath $uvPackages[$index + 1]).Path).AbsoluteUri
            }
        }
        $constraintUri = ([System.Uri]$constraints).AbsoluteUri
        Run-Checked $Uv (@('pip', 'install', '--python', $Python, '--constraint', $constraintUri) + $uvPackages)
    } else {
        Run-Checked $Python (@('-m', 'pip', 'install', '--constraint', $constraints) + $Packages)
    }
}

function Select-Folder([string]$Description) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $Description
    if ($dialog.ShowDialog() -ne 'OK') { throw 'Folder selection cancelled.' }
    return $dialog.SelectedPath
}
function Run-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable (exit $LASTEXITCODE)" }
}
function Test-Command([string]$Executable, [string[]]$Arguments) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $previousPreference }
}
function Download-File([string]$Url, [string]$Destination, [string]$Sha256 = '') {
    if ((Test-Path -LiteralPath $Destination) -and $Sha256 -and
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) { return }
    if ((Test-Path -LiteralPath $Destination) -and -not $Sha256) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    $partial = $Destination + '.part'
    Run-Checked 'curl.exe' @('--fail', '--location', '--retry', '3', '--output', $partial, $Url)
    if ($Sha256 -and (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Sha256) {
        throw "Downloaded file hash mismatch: $Destination"
    }
    if (Test-Path -LiteralPath $Destination) {
        throw "Existing file differs: $Destination. Move it aside manually; nothing was overwritten."
    }
    Move-Item -LiteralPath $partial -Destination $Destination
}
function Install-Repository([string]$Name, [string]$Url, [string]$Revision) {
    $destination = Join-Path (Join-Path $ComfyRoot 'custom_nodes') $Name
    if (-not (Test-Path -LiteralPath $destination)) {
        Run-Checked 'git' @('clone', '-c', 'core.autocrlf=false', '--filter=blob:none', '--no-checkout', $Url, $destination)
        Run-Checked 'git' @('-C', $destination, 'fetch', '--depth', '1', 'origin', $Revision)
        Run-Checked 'git' @('-C', $destination, 'checkout', '--detach', $Revision)
    }
    $head = & git -C $destination rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $head.Trim() -ne $Revision) {
        throw "Existing $Name has another version. It was not changed."
    }
    return $destination
}
function Apply-Patch([string]$Directory, [string]$PatchName, [string]$Source, [string]$Expected, [string]$Previous, [string]$Applied) {
    $patch = Join-Path $packageRoot $PatchName
    $sourcePath = Join-Path $Directory $Source
    if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash -eq $Previous) {
        # Replace only the exact previous patch; preserve all unknown local edits.
        $backup = $sourcePath + '.before-controls-update'
        if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $sourcePath -Destination $backup }
        Run-Checked 'git' @('-C', $Directory, '-c', 'core.autocrlf=false', 'restore', '--source=HEAD', '--worktree', '--', $Source)
        Write-Host "Removed previous patch from $Source; backup: $backup"
    }
    if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash -eq $Applied) {
        Run-Checked 'git' @('-C', $Directory, 'apply', '--reverse', '--check', $patch)
        return
    }
    if ((Get-FileHash -LiteralPath (Join-Path $Directory $Source) -Algorithm SHA256).Hash -ne $Expected) {
        throw "Cannot patch a different source version: $Source"
    }
    Run-Checked 'git' @('-C', $Directory, 'apply', '--check', $patch)
    Run-Checked 'git' @('-C', $Directory, 'apply', $patch)
}

try {
if (-not $ComfyRoot) { $ComfyRoot = Find-ComfyRoot }
$ComfyRoot = (Resolve-Path -LiteralPath $ComfyRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $ComfyRoot 'custom_nodes')) -and
    (Test-Path -LiteralPath (Join-Path $ComfyRoot 'ComfyUI\custom_nodes'))) {
    $ComfyRoot = Join-Path $ComfyRoot 'ComfyUI'
}
if (-not (Test-Path -LiteralPath (Join-Path $ComfyRoot 'custom_nodes'))) {
    throw 'Wrong ComfyUI folder: custom_nodes is missing.'
}
if (-not $Python) {
    $Python = Join-Path $ComfyRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "ComfyUI Python not found: $Python. Select the ComfyUI data folder containing .venv; no global Python is used."
    }
}
if (-not $ModelsRoot) { $ModelsRoot = Join-Path $ComfyRoot 'models' }
if (Get-Process -Name 'ComfyUI', 'Comfy Desktop' -ErrorAction SilentlyContinue) {
    throw 'Close ComfyUI Desktop before running the installer.'
}
Write-Host "ComfyUI folder: $ComfyRoot"
Write-Host "Python: $Python"
$Uv = $null
$uvCommand = Get-Command uv.exe -ErrorAction SilentlyContinue
if ($uvCommand) { $Uv = $uvCommand.Source }
if (-not $Uv) {
    foreach ($candidate in @(
        (Join-Path $ComfyRoot '.venv\Scripts\uv.exe'),
        (Join-Path (Split-Path -Parent $ComfyRoot) 'standalone-env\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\ComfyUI\resources\uv\win\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Comfy Desktop\resources\uv\win\uv.exe'),
        (Join-Path $HOME '.local\bin\uv.exe')
    )) {
        if (Test-Path -LiteralPath $candidate) { $Uv = $candidate; break }
    }
}
if (-not $Uv -and -not (Test-Command $Python @('-m', 'pip', '--version'))) {
    Run-Checked $Python @('-m', 'ensurepip')
}
$constraints = Join-Path $packageRoot 'installed-gpu-constraints.txt'
Run-Python 'import importlib.metadata,pathlib,sys; names=["torch","torchvision","torchaudio","numpy"]; rows=[]
for n in names:
 try: rows.append(n+"=="+importlib.metadata.version(n))
 except importlib.metadata.PackageNotFoundError: pass
pathlib.Path(sys.argv[1]).write_text("\n".join(rows))' @($constraints)
if (-not (Test-Python 'import pynvml')) {
    Install-Packages @('nvidia-ml-py==13.580.82')
}
Run-Python 'import sys,torch,pynvml; print("Environment:", sys.executable); print("Torch:",torch.__version__); assert torch.cuda.is_available(), "ComfyUI environment has no working CUDA. Its torch/driver was not changed."; print("GPU:",torch.cuda.get_device_name())'
if (-not (Test-Python 'from transformers import SiglipVisionModel, Siglip2VisionModel; import sentencepiece')) {
    Install-Packages @('transformers==4.57.1', 'sentencepiece==0.2.1')
}
$aux = Join-Path $ComfyRoot 'custom_nodes\comfyui_controlnet_aux'
if (Test-Path -LiteralPath (Join-Path $aux 'requirements.txt')) {
    Install-Packages @('-r', (Join-Path $aux 'requirements.txt'))
}
if ($DependenciesOnly) {
    Run-Python 'import pynvml; from transformers import SiglipVisionModel, Siglip2VisionModel; import sentencepiece; print("Python dependencies verified.")'
    Write-Host 'Done. Restart ComfyUI. If a node still fails, send its error from the ComfyUI log.'
    exit 0
}
Get-Command git, curl.exe -ErrorAction Stop | Out-Null

Write-Host '1/3 STYLE: nodes, compatibility patch, weights and SigLIP2'
$style = Install-Repository 'ComfyUI-Anima_IP-Adapter' 'https://github.com/LuciferTC9527/ComfyUI-Anima_IP-Adapter' $pins.style.revision
Apply-Patch $style 'style-compat.patch' 'nodes.py' $pins.style.patch_source_sha256 $pins.style.previous_patch_sha256 $pins.style.patched_sha256
Download-File "https://huggingface.co/LuciferTC/Anima-IP-Adapter/resolve/$($pins.style_weights.revision)/ip_adapter-Character_Reference-10.safetensors" (Join-Path $ModelsRoot 'ipadapter\ip_adapter-Character_Reference-10.safetensors') 'dc9612cc28b55f00ba39f6169bfe4871130be028a55682ab54dd51360acb8232'
$siglip = Join-Path $ModelsRoot 'siglip2\siglip2-base-patch16-512'
foreach ($name in @('config.json', 'model.safetensors', 'preprocessor_config.json', 'special_tokens_map.json', 'tokenizer.json', 'tokenizer.model', 'tokenizer_config.json')) {
    $sha = if ($name -eq 'model.safetensors') { 'fe0e601c625e69eed8e73500d39e9b6164403fe03db8048e87913c3cefbbb3fe' } else { '' }
    Download-File "https://huggingface.co/google/siglip2-base-patch16-512/resolve/$($pins.siglip.revision)/$name" (Join-Path $siglip $name) $sha
}

Write-Host '2/3 DEPTH: ControlNet, preprocessor and weights'
$conflicts = Get-ChildItem -LiteralPath (Join-Path $ComfyRoot 'custom_nodes') -Directory | Where-Object { $_.Name -match 'advanced.?controlnet' -and $_.Name -ne 'ComfyUI-Advanced-ControlNet' }
if ($conflicts) { throw 'Another Advanced ControlNet folder exists. Disable it before installing this fork.' }
$depth = Install-Repository 'ComfyUI-Advanced-ControlNet' 'https://github.com/PineCookie/ComfyUI-Advanced-ControlNet' $pins.depth.revision
Apply-Patch $depth 'depth-dtype.patch' 'adv_control\control_anima_vace.py' $pins.depth.patch_source_sha256 $pins.depth.previous_patch_sha256 $pins.depth.patched_sha256
$aux = Join-Path $ComfyRoot 'custom_nodes\comfyui_controlnet_aux'
if (-not (Test-Path -LiteralPath $aux)) {
    $aux = Install-Repository 'comfyui_controlnet_aux' 'https://github.com/Fannovel16/comfyui_controlnet_aux' $pins.preprocessors.revision
    Install-Packages @('-r', (Join-Path $aux 'requirements.txt'))
}
Download-File "https://huggingface.co/TaihoC/Anima-ControlNet-VACE-Depth/resolve/$($pins.depth_weights.revision)/anima-vace-depth.safetensors" (Join-Path $ModelsRoot 'controlnet\anima-vace-depth.safetensors') '29762e7037bd74b70abaa832c79d10159d5e7e0612045961318e304eb50e802d'
Download-File 'https://huggingface.co/depth-anything/Depth-Anything-V2-Small/resolve/03876f8651c73a60fe4c2c48294e09fcb6838fcf/depth_anything_v2_vits.pth' (Join-Path $aux 'ckpts\depth-anything\Depth-Anything-V2-Small\depth_anything_v2_vits.pth') '715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378'

Write-Host '3/3 CHARACTER: InContext nodes and LoRA'
$character = Join-Path $ComfyRoot 'custom_nodes\comfyui-anima-incontext'
if ($CharacterSource) {
    New-Item -ItemType Directory -Force -Path $character | Out-Null
    foreach ($name in @('__init__.py','incontext.py','nodes.py','style_adapter.py','style_nodes.py')) {
        $sourceFile = Join-Path (Join-Path $CharacterSource 'comfyui-anima-incontext') $name
        $targetFile = Join-Path $character $name
        if (Test-Path -LiteralPath $targetFile) {
            if ((Get-FileHash -LiteralPath $sourceFile).Hash -ne (Get-FileHash -LiteralPath $targetFile).Hash) { throw "Existing Character code differs: $name. Nothing overwritten." }
        } else { Copy-Item -LiteralPath $sourceFile -Destination $targetFile }
    }
    $weight = Join-Path $CharacterSource 'anima-incontext-character.safetensors'
    if ((Get-FileHash -LiteralPath $weight -Algorithm SHA256).Hash -ne '77eeb2c4263551f4760cc7af46196c6811ff623b7706f97a8f97c31ce5f0fe56') { throw 'Character weight hash mismatch.' }
    New-Item -ItemType Directory -Force -Path (Join-Path $ModelsRoot 'loras') | Out-Null
    $targetWeight = Join-Path $ModelsRoot 'loras\anima-incontext-character.safetensors'
    if (Test-Path -LiteralPath $targetWeight) {
        if ((Get-FileHash -LiteralPath $targetWeight).Hash -ne (Get-FileHash -LiteralPath $weight).Hash) { throw 'Existing Character weight differs; nothing overwritten.' }
    } else { Copy-Item -LiteralPath $weight -Destination $targetWeight }
} else {
    try {
        $characterDownload = Join-Path $packageRoot 'character-download'
        foreach ($name in @('__init__.py','incontext.py','nodes.py','style_adapter.py','style_nodes.py')) {
            Download-File "https://huggingface.co/darask0/Anima-InContext-Character/resolve/$($pins.character.revision)/comfyui-anima-incontext/$name" (Join-Path $characterDownload $name)
        }
        Download-File "https://huggingface.co/darask0/Anima-InContext-Character/resolve/$($pins.character.revision)/anima-incontext-character.safetensors" (Join-Path $ModelsRoot 'loras\anima-incontext-character.safetensors') '77eeb2c4263551f4760cc7af46196c6811ff623b7706f97a8f97c31ce5f0fe56'
        New-Item -ItemType Directory -Force -Path $character | Out-Null
        foreach ($name in @('__init__.py','incontext.py','nodes.py','style_adapter.py','style_nodes.py')) {
            $targetFile = Join-Path $character $name
            $sourceFile = Join-Path $characterDownload $name
            if (Test-Path -LiteralPath $targetFile) {
                if ((Get-FileHash -LiteralPath $targetFile).Hash -ne (Get-FileHash -LiteralPath $sourceFile).Hash) { throw "Existing Character code differs: $name" }
            } else { Copy-Item -LiteralPath $sourceFile -Destination $targetFile }
        }
    } catch {
        Write-Warning 'Character download unavailable (the source currently returns 401). Use an existing authorized local copy with -CharacterSource. Style/Depth installation is retained.'
        Write-Host 'Restart ComfyUI with --gpu-only. Character is NOT ready yet.'
        exit 2
    }
}
Write-Host 'Installed. Restart ComfyUI with --gpu-only, then ask Jessica for generation_health.'
} catch {
    Write-Host "INSTALLATION STOPPED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log saved to: $logPath"
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
