[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $PythonArchive,
    [Parameter(Mandatory = $true)] [string] $PythonUrl,
    [Parameter(Mandatory = $true)] [string] $GetPipScript,
    [Parameter(Mandatory = $true)] [string] $GetPipUrl
)

$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $PSScriptRoot 'manifest\artifacts.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Update-Artifact([object] $Artifact, [string] $Path, [string] $Url) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Cannot find $Path" }
    $Artifact.url = $Url
    $Artifact.size = (Get-Item -LiteralPath $Path).Length
    $Artifact.sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

Update-Artifact $manifest.python $PythonArchive $PythonUrl
Update-Artifact $manifest.pip_bootstrap $GetPipScript $GetPipUrl
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host "Updated $manifestPath"
