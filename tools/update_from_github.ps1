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
    # 저장소에서 지워지거나 이름이 바뀐 파일: 남아 있으면 같은 브랜드가 두 번 보이므로 백업 폴더로 옮김(삭제 안 함)
    $moved = 0
    $list = Join-Path $root.FullName 'tools\removed_files.txt'
    if (Test-Path -LiteralPath $list) {
        $backup = Join-Path $Target ('_removed_backup\' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
        Get-Content -LiteralPath $list -Encoding UTF8 | ForEach-Object {
            $rel = $_.Trim()
            if (-not $rel -or $rel.StartsWith('#') -or $rel.Contains('..')) { return }
            $rel = $rel.Replace('/', '\')
            $src = Join-Path $Target $rel
            if ((Test-Path -LiteralPath $src) -and -not (Test-Path -LiteralPath (Join-Path $root.FullName $rel))) {
                $dest = Join-Path $backup $rel
                New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
                Move-Item -LiteralPath $src -Destination $dest -Force
                $moved++
            }
        }
    }
    Write-Output ("바뀐 파일 " + $changed + "개 반영, 지워진 파일 " + $moved + "개 백업 폴더로 이동 (브랜치: " + $Branch + ")")
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
