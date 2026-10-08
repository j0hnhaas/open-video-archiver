param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ToolsRoot = Join-Path $Root ".build-tools"

$FfmpegVersion = "9.0.2"
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip"
$FfmpegSha256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"

$DenoVersion = "2.9.7"
$DenoUrl = "https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-pc-windows-msvc.zip"
$DenoSha256 = "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238"

function Get-VerifiedArchive {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [Parameter(Mandatory=$true)][string]$ExpectedSha256,
        [Parameter(Mandatory=$true)][string]$Destination
    )

    Invoke-WebRequest -Uri $Url -OutFile $Destination
    $Actual = (Get-FileHash -Path $Destination -Algorithm SHA256).Hash.ToLowerInvariant()

    if ($Actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "SHA-256 mismatch for $Url. Expected $ExpectedSha256, got $Actual."
    }
}

if (Test-Path $ToolsRoot) {
    Remove-Item -Recurse -Force $ToolsRoot
}
New-Item -ItemType Directory -Force -Path $ToolsRoot | Out-Null

$FfmpegZip = Join-Path $ToolsRoot "ffmpeg-$FfmpegVersion.zip"
$FfmpegExtract = Join-Path $ToolsRoot "ffmpeg-extracted"
$FfmpegBin = Join-Path $ToolsRoot "ffmpeg-bin"

Write-Host "Downloading FFmpeg $FfmpegVersion..."
Get-VerifiedArchive -Url $FfmpegUrl -ExpectedSha256 $FfmpegSha256 -Destination $FfmpegZip
Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtract -Force

$FfmpegExe = Get-ChildItem -Path $FfmpegExtract -Recurse -File -Filter "ffmpeg.exe" | Select-Object -First 1
$FfprobeExe = Get-ChildItem -Path $FfmpegExtract -Recurse -File -Filter "ffprobe.exe" | Select-Object -First 1
if (-not $FfmpegExe -or -not $FfprobeExe) {
    throw "FFmpeg archive did not contain ffmpeg.exe and ffprobe.exe."
}

New-Item -ItemType Directory -Force -Path $FfmpegBin | Out-Null
Copy-Item $FfmpegExe.FullName (Join-Path $FfmpegBin "ffmpeg.exe") -Force
Copy-Item $FfprobeExe.FullName (Join-Path $FfmpegBin "ffprobe.exe") -Force

$DenoZip = Join-Path $ToolsRoot "deno-$DenoVersion.zip"
$DenoBin = Join-Path $ToolsRoot "deno-bin"

Write-Host "Downloading Deno $DenoVersion..."
Get-VerifiedArchive -Url $DenoUrl -ExpectedSha256 $DenoSha256 -Destination $DenoZip
New-Item -ItemType Directory -Force -Path $DenoBin | Out-Null
Expand-Archive -Path $DenoZip -DestinationPath $DenoBin -Force

$DenoExe = Join-Path $DenoBin "deno.exe"
if (-not (Test-Path $DenoExe)) {
    throw "Deno archive did not contain deno.exe."
}

$env:PATH = "$FfmpegBin;$DenoBin;$env:PATH"

if ($env:GITHUB_PATH) {
    Add-Content -Path $env:GITHUB_PATH -Value $FfmpegBin
    Add-Content -Path $env:GITHUB_PATH -Value $DenoBin
}

Write-Host "Prepared verified release tools:"
& (Join-Path $FfmpegBin "ffmpeg.exe") -version | Select-Object -First 1
& $DenoExe --version | Select-Object -First 1
