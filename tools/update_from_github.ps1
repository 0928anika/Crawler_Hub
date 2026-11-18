# Crawler Hub 업데이트: GitHub 공개 저장소의 브랜치 ZIP을 받아 이 폴더에 덮어씁니다.
# - git·로그인 불필요(공개 저장소). 이 PC에만 있는 파일은 지우지 않습니다.
# - 실행 중인 업데이트 실행 파일(Crawler_Hub_Update_Windows.bat)은 직접 덮어쓰지 않고 .new로 저장합니다.
param(
    [string]$Target = (Split-Path -Parent $PSScriptRoot),
    [string]$Repo = '0928anika/Crawler_Hub',
    [string]$Branch = 'Oberon'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Target = (Resolve-Path -LiteralPath $Target).Path
$work = Join-Path $env:TEMP ('crawler_hub_update_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $zip = Join-Path $work 'hub.zip'
    $url = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 120
    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work 'x') -Force
    $root = Get-ChildItem -LiteralPath (Join-Path $work 'x') -Directory | Select-Object -First 1
    if (-not $root -or -not (Test-Path (Join-Path $root.FullName 'app\bootstrap.py'))) { throw '받은 ZIP에 Crawler Hub 파일이 없습니다.' }
    $changed = 0
    Get-ChildItem -LiteralPath $root.FullName -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($root.FullName.Length + 1)
        if ($rel -like '.git*') { return }
        $dest = Join-Path $Target $rel
        if ((Test-Path -LiteralPath $dest) -and ((Get-FileHash -LiteralPath $dest).Hash -eq (Get-FileHash -LiteralPath $_.FullName).Hash)) { return }
        if ($rel -eq 'Crawler_Hub_Update_Windows.bat') { $dest = $dest + '.new' }
        New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        $changed++
    }
    Write-Output ("바뀐 파일 " + $changed + "개 반영 (브랜치: " + $Branch + ")")
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
