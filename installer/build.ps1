[CmdletBinding()]
param(
    [string] $Configuration = 'Release',
    [string] $Version = '0.3.0',
    [string] $Wix = 'wix',
    [switch] $SkipCmake
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$native = Join-Path $repo 'native'
$build = Join-Path $native 'build-installer'
$stage = Join-Path $PSScriptRoot '.stage'
$dist = Join-Path $repo 'dist'

function Require-Command([string] $Name) {
    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Cannot find $Name. Install WiX Toolset v4 and add wix to PATH."
    }
}

function Assert-Hash([string] $Path) {
    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($text -match 'REPLACE_WITH|GENERATED_BY_BUILD|TODO|0{64}') {
        throw "Installer manifest or dependency lock still contains placeholder hashes: $Path"
    }
}

Require-Command $Wix
Assert-Hash (Join-Path $PSScriptRoot 'deps\requirements-cpu.lock')
Assert-Hash (Join-Path $PSScriptRoot 'deps\requirements-cuda.lock')
$manifestTemplate = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest\artifacts.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$manifestTemplate.python.sha256 -match 'REPLACE_' -or
    [string]$manifestTemplate.pip_bootstrap.sha256 -match 'REPLACE_') {
    throw 'Python Embeddable and get-pip.py must have official SHA-256 values before release'
}

if (-not $SkipCmake) {
    cmake -S $native -B $build -G 'Visual Studio 17 2022' -A x64
    cmake --build $build --config $Configuration
}

$exe = Join-Path $build "$Configuration\asmr-translation.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Cannot find Release GUI: $exe" }
$wheel = Get-ChildItem -LiteralPath (Join-Path $repo 'dist') -Filter 'asmr_lrc-*.whl' -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $wheel) { throw 'Build the project wheel first, for example: python -m build --wheel' }
if ($wheel.Name -ne "asmr_lrc-$Version-py3-none-any.whl") {
    throw "Wheel version mismatch: expected asmr_lrc-$Version-py3-none-any.whl, got $($wheel.Name)"
}
$wheelName = "asmr_lrc-$Version-py3-none-any.whl"

Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage, (Join-Path $stage 'wheel'), (Join-Path $stage 'bootstrap'), (Join-Path $stage 'manifest'), (Join-Path $stage 'deps') | Out-Null
Copy-Item -LiteralPath $exe -Destination (Join-Path $stage 'asmr-translation.exe')
Copy-Item -LiteralPath $wheel.FullName -Destination (Join-Path $stage "wheel\$wheelName")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'bootstrap\bootstrap.ps1') -Destination (Join-Path $stage 'bootstrap\bootstrap.ps1')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'deps\requirements-cpu.lock'), (Join-Path $PSScriptRoot 'deps\requirements-cuda.lock') -Destination (Join-Path $stage 'deps')
$manifestTemplate.wheels.project.size = $wheel.Length
$manifestTemplate.wheels.project.name = $wheelName
$manifestTemplate.wheels.project.path = "../wheel/$wheelName"
$manifestTemplate.wheels.project.sha256 = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
foreach ($name in @('cpu_lock', 'cuda_lock')) {
    $lockFile = Join-Path $stage "deps\requirements-$($name -replace '_lock','').lock"
    $manifestTemplate.wheels.$name.size = (Get-Item -LiteralPath $lockFile).Length
    $manifestTemplate.wheels.$name.sha256 = (Get-FileHash -LiteralPath $lockFile -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifestPath = Join-Path $stage 'manifest\artifacts.json'
$manifestTemplate | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Assert-Hash $manifestPath
Copy-Item -LiteralPath (Join-Path $repo 'README.md'), (Join-Path $repo 'COPYING'), (Join-Path $repo 'THIRD_PARTY_NOTICES.md') -Destination $stage

New-Item -ItemType Directory -Path $dist -Force | Out-Null
$msi = Join-Path $dist "asmr-translation-$Version-x64.msi"
& $Wix build (Join-Path $PSScriptRoot 'asmr-translation.wxs') `
    -arch x64 -d "ProductVersion=$Version" -d "ProjectWheelName=$wheelName" `
    -bindpath "stage=$stage" -o $msi
if ($LASTEXITCODE -ne 0) { throw "WiX build failed with exit code $LASTEXITCODE" }
Get-FileHash -LiteralPath $msi -Algorithm SHA256 | Format-List
Write-Host "Generated $msi"
