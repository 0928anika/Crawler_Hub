#!/bin/bash
# 최신 버전을 받은 뒤 Crawler Hub 실행 (GitHub에서 Clone 한 폴더에서만 업데이트)
cd "$(dirname "$0")"
if command -v git >/dev/null 2>&1 && [ -d .git ]; then
  echo "최신 버전 확인 중... 브랜치: $(git rev-parse --abbrev-ref HEAD)"
  if git pull --ff-only; then echo "업데이트 완료"; else echo "[업데이트 실패] 이 Mac에서 직접 바꾼 파일이 있거나 네트워크 문제입니다. 기존 버전으로 실행합니다."; fi
else
  echo "[업데이트 건너뜀] git이 없거나 ZIP으로 받은 폴더입니다. GitHub Desktop으로 Clone 한 폴더에서 실행하세요."
fi
echo
exec "./Crawler_Hub_Mac.command" "$@"
