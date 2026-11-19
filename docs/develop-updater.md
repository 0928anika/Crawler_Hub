# Windows 공개 develop 업데이트 v4 — admin 내부 관리

로컬 배치:

```text
Crawler_Hub/
  app/
  brands/
  Crawler_Hub_Windows.bat
  Crawler_Hub_Mac.command
  admin/
    Update.bat
    update_develop.py
    Update.bat.new                 # BAT 업데이트 대기 중일 때만
    .crawler-update.lock           # 중복 실행 방지용
    .crawler-update-state.json     # 최신 커밋/파일 지문 기록
    backups/                       # 이전 소스, 완료 백업 최근5개
```

사용자 화면에 필요한 소스 교체는 app/brands 전체와 기본 실행기2개뿐입니다.
업데이트 도구 자체의 변경은 현재 실행한 admin 폴더의 두 파일에만 반영합니다.
저장소 tools/update_develop.py는 다운로드 안에서 admin/update_develop.py로 바로 읽어오며,
로컬 tools 폴더를 만들거나 tools에 파일을 쓰지 않습니다.
백업·잠금·상태·임시 다운로드도 admin 안에만 생성합니다.

## .new 처리

Update.bat.new는 폴더가 아니라 다음 실행에 적용할 BAT 파일입니다.
실행 중인 BAT를 즉시 덮어쓰지 않기 위해 admin/Update.bat.new로 한 번만 저장합니다.
다음 admin/Update.bat 실행 시작 시 같은 폴더의 .new를 Update.bat으로 이동·교체하고
새 BAT를 실행합니다. 교체가 성공하면 .new는 없어집니다.
그 실행에서 원격 BAT가 다시 바뀌었다면 새로운 .new가 다시 생길 수 있습니다.
Python 보조 파일은 이미 메모리에 로드되어 있으므로 업데이트 성공 시 admin 안에서 교체합니다.

## 관리 파일의 필요성

- lock: 동시에 업데이트해서 폴더가 섞이는 것을 막습니다. OS 파일 잠금이며 강제 종료돼도
  잠금은 해제됩니다. 파일이 남는 것은 정상이고 매번 지울 필요가 없습니다.
- state: 마지막 적용 커밋과 관리 파일 지문을 기록합니다. 변경이 없을 때 ZIP 다운로드를
  생략하기 위해 필요합니다. 없어도 다음 실행에서 전체 검증 후 다시 생성됩니다.
- backups: 적용 실패 복구 및 이전 버전 보관용입니다. 정상 완료된 백업은 최근5개 보관합니다.

## 기존 설치 수기 정리

자동 초기 설정/이동/청소 코드는 넣지 않았습니다. 앱과 업데이트 실행을 모두 종료한 뒤:

1. 새 Update.bat과 update_develop.py 두 개를 기존 admin 폴더에 수기로 교체합니다.
2. 루트의 옛 .crawler-update-backups가 필요하면 admin/backups로 직접 옮겨 보관합니다.
3. 루트의 옛 .crawler-update-state.json은 지워도 됩니다. 새 위치에 다시 기록합니다.
4. 루트의 옛 .crawler-update.lock은 실행 중인 업데이트가 없는 것을 확인한 뒤 지워도 됩니다.
5. 루트/admin에 남은 옛 Update.bat.new는 새 도구를 수동 교체할 때 함께 지웁니다.
   옛 .new를 남기면 다음 실행에서 새로 넣은 BAT가 옛 대기본으로 바뀔 수 있습니다.
6. tools 폴더가 업데이트 보조 파일 때문에만 남아 있다면 admin의 새 보조 파일을 확인한 뒤
   직접 정리하세요. 새 BAT는 tools를 찾지 않습니다. 루트의 옛 업데이트 BAT도 직접 정리하세요.

새 버전은 옛 루트 관리 파일을 읽거나 옮기지 않습니다. 첫 실행은 ZIP 다운로드가 필요할 수
있으며 이후 같은 커밋·파일이면 빠르게 최신 상태로 끝납니다.
admin 이외 다른 이름의 하위 폴더에서 두 업데이트 파일을 함께 실행해도 관리 파일은 그 폴더에
모입니다. app/brands 안에는 두지 마세요. 폴더 숨김은 접근 권한 제한이 아닙니다.

## 실행

Git 설치/로그인/토큰 없이 공개 develop을 받습니다. Python3.10 이상 필요.
앱 콘솔 종료 후 admin/Update.bat 실행. 성공/최신 확인 뒤 앱을 실행합니다.
--no-launch는 자동 실행 생략, --check-only는 다운로드·검증만 수행합니다.
브랜드 해시/문법 검증, SHA 고정 다운로드, 교체 실패 복구, 작업·설정·결과·가상환경 보존 유지.
기존 작업의 실행 사본은 유지되므로 수정 크롤러는 새 작업으로 확인하세요.
