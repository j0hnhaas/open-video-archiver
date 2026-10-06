param(
    [switch]$SkipInstall,
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "This build script must run on Windows."
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Require-Command {
    param([Parameter(Mandatory=$true)][string]$Name)

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Required command '$Name' was not found in PATH."
    }
    return $cmd
}

function Run-Python {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)

    & python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: python $($Args -join ' ')"
    }
}

Write-Host ""
Write-Host "Open Video Archiver - Windows Portable Build" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

$Python = Require-Command "python"
$Ffmpeg = Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue
if (-not $Ffmpeg) { $Ffmpeg = Get-Command "ffmpeg" -ErrorAction SilentlyContinue }
$Ffprobe = Get-Command "ffprobe.exe" -ErrorAction SilentlyContinue
if (-not $Ffprobe) { $Ffprobe = Get-Command "ffprobe" -ErrorAction SilentlyContinue }
$Deno = Get-Command "deno.exe" -ErrorAction SilentlyContinue
if (-not $Deno) { $Deno = Get-Command "deno" -ErrorAction SilentlyContinue }

if (-not $Ffmpeg) {
    throw "FFmpeg was not found. Install it first, e.g. winget install Gyan.FFmpeg"
}
if (-not $Ffprobe) {
    throw "FFprobe was not found. It normally ships with FFmpeg."
}
if (-not $Deno) {
    throw "Deno was not found. Install it first, e.g. winget install DenoLand.Deno"
}

if (-not $SkipInstall) {
    Write-Host "[1/7] Installing/updating build dependencies..." -ForegroundColor Cyan
    Run-Python -Args @("-m", "pip", "install", "-e", ".[gui,build]")
} else {
    Write-Host "[1/7] Build dependency installation skipped." -ForegroundColor DarkGray
}

$Version = (& python -c "from ova_constants import APP_VERSION; print(APP_VERSION)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) {
    throw "Could not determine Open Video Archiver version."
}

$Architecture = (& python -c "import platform; print(platform.machine())").Trim().ToLowerInvariant()
switch -Regex ($Architecture) {
    "^(amd64|x86_64)$" { $ArchitectureLabel = "x64"; break }
    "^arm64$"          { $ArchitectureLabel = "arm64"; break }
    default            { $ArchitectureLabel = $Architecture }
}

$PackageName = "Open-Video-Archiver-$Version-Windows-$ArchitectureLabel-Portable"
$BuildRoot = Join-Path $Root ".build"
$WorkPath = Join-Path $BuildRoot "pyinstaller"
$TempDist = Join-Path $BuildRoot "dist"
$IconPath = Join-Path $BuildRoot "open-video-archiver.ico"
$VersionFile = Join-Path $BuildRoot "windows-version.txt"
$FinalDistRoot = Join-Path $Root "dist"
$PortableDir = Join-Path $FinalDistRoot $PackageName
$ZipPath = Join-Path $FinalDistRoot "$PackageName.zip"
$ZipHashPath = "$ZipPath.sha256"

Write-Host "[2/7] Preparing Windows resources..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
Run-Python -Args @(
    "tools\make_windows_resources.py",
    "--icon", $IconPath,
    "--version-file", $VersionFile
)

Write-Host "[3/7] Building OpenVideoArchiver.exe with PyInstaller..." -ForegroundColor Cyan
if (Test-Path $WorkPath) { Remove-Item -Recurse -Force $WorkPath }
if (Test-Path $TempDist) { Remove-Item -Recurse -Force $TempDist }

$env:OVA_BUILD_ROOT = $Root
$env:OVA_ICON_PATH = $IconPath
$env:OVA_VERSION_FILE = $VersionFile

Run-Python -Args @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $TempDist,
    "--workpath", $WorkPath,
    "packaging\OpenVideoArchiver.spec"
)

$BuiltDir = Join-Path $TempDist "OpenVideoArchiver"
$BuiltExe = Join-Path $BuiltDir "OpenVideoArchiver.exe"
if (-not (Test-Path $BuiltExe)) {
    throw "PyInstaller completed but OpenVideoArchiver.exe was not found at $BuiltExe"
}

Write-Host "[4/7] Creating portable folder..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $FinalDistRoot | Out-Null
if (Test-Path $PortableDir) { Remove-Item -Recurse -Force $PortableDir }
Move-Item -Path $BuiltDir -Destination $PortableDir

$ToolsRoot = Join-Path $PortableDir "tools"
$FfmpegTarget = Join-Path $ToolsRoot "ffmpeg"
$DenoTarget = Join-Path $ToolsRoot "deno"
New-Item -ItemType Directory -Force -Path $FfmpegTarget, $DenoTarget | Out-Null

$FfmpegSource = [System.IO.Path]::GetFullPath($Ffmpeg.Source)
$FfprobeSource = [System.IO.Path]::GetFullPath($Ffprobe.Source)
$FfmpegDir = Split-Path -Parent $FfmpegSource
$FfprobeDir = Split-Path -Parent $FfprobeSource

# Copy only the FFmpeg executables Open Video Archiver actually uses.
# Do not copy ffplay.exe or unrelated tools from the FFmpeg distribution.
Copy-Item -Path $FfmpegSource -Destination (Join-Path $FfmpegTarget "ffmpeg.exe") -Force
Copy-Item -Path $FfprobeSource -Destination (Join-Path $FfmpegTarget "ffprobe.exe") -Force

# Shared FFmpeg builds may depend on adjacent DLLs. Copy DLLs only, from the
# directories that contain the selected ffmpeg/ffprobe executables.
$FfmpegRuntimeDirs = @($FfmpegDir, $FfprobeDir) | Select-Object -Unique
foreach ($RuntimeDir in $FfmpegRuntimeDirs) {
    Get-ChildItem -Path $RuntimeDir -Filter "*.dll" -File -ErrorAction SilentlyContinue |
        Copy-Item -Destination $FfmpegTarget -Force
}

Copy-Item -Path ([System.IO.Path]::GetFullPath($Deno.Source)) -Destination (Join-Path $DenoTarget "deno.exe") -Force

Copy-Item -Path (Join-Path $Root "LICENSE") -Destination (Join-Path $PortableDir "LICENSE") -Force
Copy-Item -Path (Join-Path $Root "SOFTWARE.md") -Destination (Join-Path $PortableDir "SOFTWARE.md") -Force
Copy-Item -Path (Join-Path $Root "PORTABLE_README.txt") -Destination (Join-Path $PortableDir "README.txt") -Force

$PythonVersion = (& python --version 2>&1 | Select-Object -First 1)
$PyInstallerVersion = (& python -m PyInstaller --version 2>&1 | Select-Object -First 1)
$FfmpegVersion = (& $Ffmpeg.Source -version 2>&1 | Select-Object -First 1)
$DenoVersion = (& $Deno.Source --version 2>&1 | Select-Object -First 1)

@"
Open Video Archiver Windows Portable Build
==========================
Open Video Archiver: $Version
Architecture: $ArchitectureLabel
Built: $(Get-Date -Format "yyyy-MM-ddTHH:mm:ssK")
Python: $PythonVersion
PyInstaller: $PyInstallerVersion
FFmpeg: $FfmpegVersion
Deno: $DenoVersion
Source: https://github.com/j0hnhaas/open-video-archiver
"@ | Set-Content -Path (Join-Path $PortableDir "BUILD-INFO.txt") -Encoding UTF8

Write-Host "[5/7] Checking portable payload..." -ForegroundColor Cyan
foreach ($required in @(
    (Join-Path $PortableDir "OpenVideoArchiver.exe"),
    (Join-Path $PortableDir "tools\ffmpeg\ffmpeg.exe"),
    (Join-Path $PortableDir "tools\ffmpeg\ffprobe.exe"),
    (Join-Path $PortableDir "tools\deno\deno.exe"),
    (Join-Path $PortableDir "README.txt"),
    (Join-Path $PortableDir "LICENSE")
)) {
    if (-not (Test-Path $required)) {
        throw "Portable payload is incomplete: missing $required"
    }
}

if (-not $NoZip) {
    Write-Host "[6/7] Creating portable ZIP..." -ForegroundColor Cyan
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
    if (Test-Path $ZipHashPath) { Remove-Item -Force $ZipHashPath }

    Compress-Archive -Path $PortableDir -DestinationPath $ZipPath -CompressionLevel Optimal

    Write-Host "[7/7] Creating SHA-256 for ZIP..." -ForegroundColor Cyan
    $Hash = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $([System.IO.Path]::GetFileName($ZipPath))" |
        Set-Content -Path $ZipHashPath -Encoding ASCII
} else {
    Write-Host "[6/7] ZIP creation skipped." -ForegroundColor DarkGray
    Write-Host "[7/7] ZIP checksum skipped." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "BUILD COMPLETE" -ForegroundColor Green
Write-Host "Portable folder:" -ForegroundColor White
Write-Host "  $PortableDir" -ForegroundColor Yellow
if (-not $NoZip) {
    Write-Host "Portable ZIP:" -ForegroundColor White
    Write-Host "  $ZipPath" -ForegroundColor Yellow
    Write-Host "ZIP SHA-256:" -ForegroundColor White
    Write-Host "  $ZipHashPath" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Run the portable application with:" -ForegroundColor White
Write-Host "  $PortableDir\OpenVideoArchiver.exe" -ForegroundColor Cyan
Write-Host ""
