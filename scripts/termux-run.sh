#!/usr/bin/env bash
# 안드로이드 Termux에서 서버를 켜고 브라우저로 웹 화면을 엽니다.
#   bash scripts/termux-run.sh      (끄려면 Ctrl+C)
set -euo pipefail
cd "$(dirname "$0")/.."

# 화면이 꺼져도 영상 생성이 멈추지 않도록 Termux 절전 해제
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

(sleep 5 && termux-open-url http://localhost:8000 2>/dev/null) &
python main.py
