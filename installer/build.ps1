# Builds the installer: dist\installer\LeeLooPatternSetup-<VERSION>.exe  (run: powershell -File installer\build.ps1)
# Needs: the Python of .venv-cam (or Python 3.14) with the packages of requirements.txt + PyInstaller
# (installer\requirements-build.txt), Inno Setup 6.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv-cam\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe" }
$version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim()
New-Item -ItemType Directory -Force (Join-Path $root "build") | Out-Null
$info = Join-Path $root "build\build_info.json"
[IO.File]::WriteAllText($info, (@{ built = (Get-Date -Format "yyyy-MM-dd") } | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))      # UTF-8 without BOM, works in PowerShell 5 and 7
$env:LEELOO_BUILD_INFO = $info
& $py -m PyInstaller (Join-Path $PSScriptRoot "leeloopattern.spec") --noconfirm --distpath (Join-Path $root "build\dist") --workpath (Join-Path $root "build\work")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
& $iscc "/DAppVersion=$version" (Join-Path $PSScriptRoot "leeloopattern.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Get-ChildItem (Join-Path $root "dist\installer") | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB) } }
