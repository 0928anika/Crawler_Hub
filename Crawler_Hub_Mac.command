#!/bin/bash
# Finder opens .command files in Terminal. Keep LF line endings and executable mode.
cd "$(dirname "${BASH_SOURCE[0]}")/app" || exit 1

hub_python=""
hub_candidates=(
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
  "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
  "$(command -v python3 2>/dev/null || true)"
)
for hub_candidate in "${hub_candidates[@]}"; do
  [[ -n "$hub_candidate" && -x "$hub_candidate" ]] || continue
  # Avoid triggering Apple's developer-tools installer for its python3 stub.
  if [[ "$hub_candidate" == "/usr/bin/python3" ]] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
    continue
  fi
  if "$hub_candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    hub_python="$hub_candidate"
    break
  fi
done

if [[ -z "$hub_python" ]]; then
  printf '\nPython 3.10 or later is required. Install Python 3 from https://www.python.org/downloads/macos/ and try again.\n'
  if [[ -t 0 ]]; then read -r -p 'Press Return to close...' hub_reply; fi
  exit 1
fi

"$hub_python" -B bootstrap.py "$@"
hub_status=$?
if [[ "$hub_status" -ne 0 ]]; then
  printf '\nCrawler Hub stopped with an error (exit %s). See the message above.\n' "$hub_status"
  if [[ -t 0 ]]; then read -r -p 'Press Return to close...' hub_reply; fi
fi
exit "$hub_status"
