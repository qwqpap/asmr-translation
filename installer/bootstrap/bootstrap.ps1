[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $PlanPath,
    [Parameter(Mandatory = $true)] [string] $StateRoot,
    [string] $MirrorBase = "",
    [ValidateSet("cpu", "cuda")] [string] $Accelerator = "cpu",
    [switch] $InstallFfmpeg,
    [switch] $InstallModel
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Event([string] $Event, [hashtable] $Data = @{}) {
    $payload = [ordered]@{ protocol = 1; event = $Event }
    foreach ($entry in $Data.GetEnumerator()) { $payload[$entry.Key] = $entry.Value }
    [Console]::Out.WriteLine(($payload | ConvertTo-Json -Compress -Depth 8))
    [Console]::Out.Flush()
}

function Fail([string] $Message) {
    $safe = [regex]::Replace($Message, 'https?://[^\s]+', '<redacted-url>')
    Write-Event "error" @{ message = $safe }
    exit 2
}

function Is-Placeholder([string] $Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $true }
    return $Value -match "^(REPLACE_|GENERATED_|TODO|0{64})"
}

function Get-ArtifactUrl([string] $Url) {
    if ([string]::IsNullOrWhiteSpace($MirrorBase)) { return $Url }
    $relative = [Uri]::new($Url).AbsolutePath.TrimStart('/')
    return ($MirrorBase.TrimEnd('/') + '/' + $relative)
}

function Get-Sha256([string] $Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Download-Artifact([string] $Name, [object] $Artifact, [string] $CacheRoot) {
    if ($null -eq $Artifact) { throw "$Name is not configured" }
    $sha = [string]$Artifact.sha256
    if (Is-Placeholder $sha) { throw "$Name is missing a release SHA-256" }
    if ($sha -notmatch '^[0-9a-fA-F]{64}$') { throw "$Name has an invalid SHA-256" }
    $url = Get-ArtifactUrl ([string]$Artifact.url)
    $nameOnDisk = [IO.Path]::GetFileName(([Uri]$url).AbsolutePath)
    if ([string]::IsNullOrWhiteSpace($nameOnDisk)) { throw "$Name URL has no filename" }
    $target = Join-Path $CacheRoot $nameOnDisk
    $part = "$target.part"
    if (Test-Path -LiteralPath $target) {
        if ((Get-Sha256 $target) -eq $sha.ToLowerInvariant()) { return $target }
        Remove-Item -LiteralPath $target -Force
    }
    Write-Event "download_start" @{ name = $Name; size = [int64]$Artifact.size }
    $client = [Net.Http.HttpClient]::new()
    try {
        $client.Timeout = [TimeSpan]::FromMinutes(30)
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $url)
        $existing = if (Test-Path -LiteralPath $part) { (Get-Item -LiteralPath $part).Length } else { 0 }
        if ([int64]$Artifact.size -gt 0 -and $existing -gt [int64]$Artifact.size) {
            Remove-Item -LiteralPath $part -Force
            $existing = 0
        }
        if ($existing -gt 0) {
            $request.Headers.Range = [Net.Http.Headers.RangeHeaderValue]::new($existing, $null)
        }
        $response = $client.SendAsync($request, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        if ($existing -gt 0 -and $response.StatusCode.value__ -eq 416) {
            if ((Get-Sha256 $part) -eq $sha.ToLowerInvariant()) {
                Move-Item -LiteralPath $part -Destination $target -Force
                Write-Event "verify" @{ name = $Name; sha256 = $sha.ToLowerInvariant() }
                return $target
            }
            Remove-Item -LiteralPath $part -Force
            $existing = 0
            $request.Dispose()
            $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $url)
            $response = $client.SendAsync($request, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        }
        if ($existing -gt 0 -and $response.StatusCode.value__ -eq 200) {
            Remove-Item -LiteralPath $part -Force
            $existing = 0
        }
        if ($existing -gt 0 -and $response.StatusCode.value__ -ne 206) {
            throw "$Name server does not support Range resume (HTTP $($response.StatusCode.value__))"
        }
        $response.EnsureSuccessStatusCode()
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $mode = if ($existing -gt 0) { [IO.FileMode]::Append } else { [IO.FileMode]::Create }
        $file = [IO.File]::Open($part, $mode, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $buffer = New-Object byte[] 1048576
            $read = 0L
            while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $file.Write($buffer, 0, $count)
                $read += $count
                Write-Event "download_progress" @{ name = $Name; received = ($existing + $read); total = [int64]$Artifact.size }
            }
        } finally { $file.Dispose(); $stream.Dispose() }
        $request.Dispose()
    } finally { $client.Dispose() }
    if ((Get-Sha256 $part) -ne $sha.ToLowerInvariant()) {
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        throw "$Name SHA-256 verification failed"
    }
    Move-Item -LiteralPath $part -Destination $target -Force
    Write-Event "verify" @{ name = $Name; sha256 = $sha.ToLowerInvariant() }
    return $target
}

function Use-LocalArtifact([string] $Name, [object] $Artifact, [string] $PlanRoot) {
    if ($null -eq $Artifact -or [string]::IsNullOrWhiteSpace([string]$Artifact.path)) {
        throw "$Name local path is not configured"
    }
    $sha = [string]$Artifact.sha256
    if ((Is-Placeholder $sha) -or $sha -notmatch '^[0-9a-fA-F]{64}$') {
        throw "$Name has an invalid SHA-256"
    }
    $path = [IO.Path]::GetFullPath((Join-Path $PlanRoot ([string]$Artifact.path)))
    $stageRoot = [IO.Path]::GetFullPath((Join-Path $PlanRoot '..'))
    if (-not $stageRoot.EndsWith([IO.Path]::DirectorySeparatorChar)) {
        $stageRoot += [IO.Path]::DirectorySeparatorChar
    }
    if (-not $path.StartsWith($stageRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name local path escapes the installer staging directory"
    }
    if (-not (Test-Path -LiteralPath $path)) { throw "Cannot find ${Name}: $path" }
    if ((Get-Sha256 $path) -ne $sha.ToLowerInvariant()) { throw "$Name SHA-256 verification failed" }
    return $path
}

function Safe-ExtractZip([string] $Archive, [string] $Destination) {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = [IO.Path]::GetFullPath((Join-Path $Destination ''))
    if (-not $root.EndsWith([IO.Path]::DirectorySeparatorChar)) { $root += [IO.Path]::DirectorySeparatorChar }
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $candidate = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
            if (-not $candidate.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Archive contains a path traversal entry: $($entry.FullName)"
            }
            if ([string]::IsNullOrEmpty($entry.Name)) { [void][IO.Directory]::CreateDirectory($candidate); continue }
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($candidate))
            $entry.ExtractToFile($candidate, $true)
        }
    } finally { $zip.Dispose() }
}

function Install-OptionalArchive([string] $Name, [object] $Artifact, [string] $CacheRoot,
    [string] $StateRoot) {
    if ($null -eq $Artifact -or [string]::IsNullOrWhiteSpace([string]$Artifact.target)) {
        throw "$Name target is not configured"
    }
    $target = [IO.Path]::GetFullPath((Join-Path $StateRoot ([string]$Artifact.target)))
    $statePrefix = [IO.Path]::GetFullPath((Join-Path $StateRoot ''))
    if (-not $statePrefix.EndsWith([IO.Path]::DirectorySeparatorChar)) {
        $statePrefix += [IO.Path]::DirectorySeparatorChar
    }
    if (-not $target.StartsWith($statePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name target escapes the state directory"
    }
    $archive = Download-Artifact $Name $Artifact $CacheRoot
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    [void][IO.Directory]::CreateDirectory($target)
    if ([bool]$Artifact.archive) {
        Safe-ExtractZip $archive $target
    } else {
        [void](Copy-Item -LiteralPath $archive -Destination (Join-Path $target ([IO.Path]::GetFileName($archive))))
    }
    return $target
}

try {
    $plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $state = [IO.Path]::GetFullPath($StateRoot)
    $cache = Join-Path $state 'downloads'
    $runtime = Join-Path $state 'runtime\python-3.12-embed-amd64'
    [void][IO.Directory]::CreateDirectory($cache)
    [void][IO.Directory]::CreateDirectory($state)

    $pythonZip = Download-Artifact 'python' $plan.python $cache
    if (-not (Test-Path -LiteralPath (Join-Path $runtime 'python.exe'))) {
        $temporary = "$runtime.tmp.$PID"
        Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
        [void][IO.Directory]::CreateDirectory($temporary)
        Safe-ExtractZip $pythonZip $temporary
        $pth = Get-ChildItem -LiteralPath $temporary -Filter '*._pth' -File | Select-Object -First 1
        if ($null -eq $pth) { throw 'Python Embeddable has no _pth file' }
        Add-Content -LiteralPath $pth.FullName -Value "`nLib\site-packages`nimport site`n" -Encoding ASCII
        Remove-Item -LiteralPath $runtime -Recurse -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $temporary -Destination $runtime
    }

    $pip = Download-Artifact 'pip_bootstrap' $plan.pip_bootstrap $cache
    $python = Join-Path $runtime 'python.exe'
    Write-Event 'install' @{ name = 'pip' }
    & $python $pip --no-warn-script-location --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed with exit code $LASTEXITCODE" }

    $lock = if ($Accelerator -eq 'cuda') { $plan.wheels.cuda_lock } else { $plan.wheels.cpu_lock }
    if (($null -eq $lock) -or (Is-Placeholder ([string]$lock.path))) { throw 'ASR dependency lock is not configured' }
    $lockPath = Join-Path (Split-Path -Parent $PlanPath) ('..\' + [string]$lock.path)
    $lockPath = [IO.Path]::GetFullPath($lockPath)
    $stageRoot = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $PlanPath) '..'))
    if (-not $stageRoot.EndsWith([IO.Path]::DirectorySeparatorChar)) {
        $stageRoot += [IO.Path]::DirectorySeparatorChar
    }
    if (-not $lockPath.StartsWith($stageRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Dependency lock escapes the installer staging directory'
    }
    if (-not (Test-Path -LiteralPath $lockPath)) { throw "Cannot find dependency lock: $lockPath" }
    Write-Event 'install' @{ name = 'asmr_lrc dependencies'; accelerator = $Accelerator }
    & $python -m pip install --disable-pip-version-check --require-hashes -r $lockPath
    if ($LASTEXITCODE -ne 0) { throw "ASR dependency install failed with exit code $LASTEXITCODE" }

    $wheel = Use-LocalArtifact 'project_wheel' $plan.wheels.project (Split-Path -Parent $PlanPath)
    & $python -m pip install --disable-pip-version-check --no-deps --force-reinstall $wheel
    if ($LASTEXITCODE -ne 0) { throw "Project wheel install failed with exit code $LASTEXITCODE" }

    $ffmpegPath = ''
    $modelPath = ''
    if ($InstallFfmpeg) {
        if ($null -eq $plan.ffmpeg) { throw 'This release has no FFmpeg artifact; choose ffmpeg.exe in Settings' }
        $ffmpegRoot = Install-OptionalArchive 'ffmpeg' $plan.ffmpeg $cache $state
        $ffmpeg = Get-ChildItem -LiteralPath $ffmpegRoot -Filter 'ffmpeg.exe' -File -Recurse |
            Select-Object -First 1
        if ($null -eq $ffmpeg) { throw 'FFmpeg artifact contains no ffmpeg.exe' }
        $ffmpegPath = $ffmpeg.FullName
    }
    if ($InstallModel) {
        if ($null -eq $plan.whisper_model) { throw 'This release has no Whisper model artifact; configure a model directory manually' }
        $modelPath = Install-OptionalArchive 'whisper_model' $plan.whisper_model $cache $state
    }

    Write-Event 'complete' @{ python = $python; runtime = $runtime; accelerator = $Accelerator;
        ffmpeg = $ffmpegPath; model = $modelPath }
    exit 0
} catch {
    Fail $_.Exception.Message
}
