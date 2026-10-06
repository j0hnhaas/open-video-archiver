$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Open Video Archiver (OVA) - user installation"
Write-Host "Copyright (c) 2026 John G. Haas"
Write-Host ""

python -m pip install --user -U -e .
if ($LASTEXITCODE -ne 0) {
    throw "Python package installation failed."
}

$ScriptsDir = python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
if (-not $ScriptsDir) {
    throw "Could not determine the Python user Scripts directory."
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Parts = @()
if ($UserPath) {
    $Parts = $UserPath -split ';' | Where-Object { $_ }
}

if ($Parts -notcontains $ScriptsDir) {
    $NewPath = if ($UserPath) { $UserPath.TrimEnd(';') + ";" + $ScriptsDir } else { $ScriptsDir }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    Write-Host "Added to user PATH: $ScriptsDir"
}

if (($env:Path -split ';') -notcontains $ScriptsDir) {
    $env:Path = $env:Path.TrimEnd(';') + ";" + $ScriptsDir
}

Write-Host ""
Write-Host "Installation complete."
Write-Host "Command: ova"
Write-Host ""

ova --version
