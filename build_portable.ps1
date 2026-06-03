$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv\Scripts\python.exe" -m pip install pyinstaller

if (Test-Path "build") {
    Remove-Item -LiteralPath "build" -Recurse -Force
}
if (Test-Path "dist") {
    Remove-Item -LiteralPath "dist" -Recurse -Force
}

& ".venv\Scripts\python.exe" -m PyInstaller `
    --name "VideoFrameExtractor" `
    --noconfirm `
    --windowed `
    --icon "assets\app_icon.ico" `
    --add-data "assets;assets" `
    --collect-all scenedetect `
    --collect-all cv2 `
    --exclude-module imageio_ffmpeg `
    main.py

$PackageRoot = Join-Path $Root "release"
if (Test-Path $PackageRoot) {
    Remove-Item -LiteralPath $PackageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $PackageRoot | Out-Null

Copy-Item -LiteralPath "dist\VideoFrameExtractor" -Destination (Join-Path $PackageRoot "VideoFrameExtractor") -Recurse
Copy-Item -LiteralPath "README.md" -Destination $PackageRoot
Copy-Item -LiteralPath "HANDOFF.md" -Destination $PackageRoot
Copy-Item -LiteralPath "PRODUCT.md" -Destination $PackageRoot

$ZipPath = Join-Path $Root "release\VideoFrameExtractor-portable.zip"
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $PackageRoot "VideoFrameExtractor"), (Join-Path $PackageRoot "README.md"), (Join-Path $PackageRoot "HANDOFF.md"), (Join-Path $PackageRoot "PRODUCT.md") -DestinationPath $ZipPath

$SourceStage = Join-Path $PackageRoot "VideoFrameExtractor-source"
if (Test-Path $SourceStage) {
    Remove-Item -LiteralPath $SourceStage -Recurse -Force
}
New-Item -ItemType Directory -Path $SourceStage | Out-Null
Copy-Item -LiteralPath "core" -Destination $SourceStage -Recurse
Copy-Item -LiteralPath "ui" -Destination $SourceStage -Recurse
Copy-Item -LiteralPath "assets" -Destination $SourceStage -Recurse
Copy-Item -LiteralPath "main.py" -Destination $SourceStage
Copy-Item -LiteralPath "requirements.txt" -Destination $SourceStage
Copy-Item -LiteralPath "README.md" -Destination $SourceStage
Copy-Item -LiteralPath "HANDOFF.md" -Destination $SourceStage
Copy-Item -LiteralPath "PRODUCT.md" -Destination $SourceStage
Copy-Item -LiteralPath "setup_windows.cmd" -Destination $SourceStage
Copy-Item -LiteralPath "run_app.cmd" -Destination $SourceStage
Copy-Item -LiteralPath "build_portable.ps1" -Destination $SourceStage
Get-ChildItem -LiteralPath $SourceStage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

$SourceZipPath = Join-Path $Root "release\VideoFrameExtractor-source.zip"
if (Test-Path $SourceZipPath) {
    Remove-Item -LiteralPath $SourceZipPath -Force
}
Compress-Archive -Path $SourceStage -DestinationPath $SourceZipPath

Write-Host "Portable package created:"
Write-Host $ZipPath
Write-Host "Source package created:"
Write-Host $SourceZipPath
