"""PostToolUse 훅: app/ 또는 tests/의 파이썬 파일을 고치면 pytest를 돌리고, 실패하면 Claude에게 알린다."""

import json
import os
import subprocess
import sys
from pathlib import Path

data = json.load(sys.stdin)
project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
path = Path(data.get("tool_input", {}).get("file_path") or "").resolve()
try:
    rel = path.relative_to(project)
except ValueError:
    sys.exit(0)
if path.suffix != ".py" or rel.parts[0] not in ("app", "tests"):
    sys.exit(0)

try:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
        cwd=project, capture_output=True, text=True, timeout=300,
    )
except subprocess.TimeoutExpired:
    print("pytest가 5분 안에 끝나지 않았습니다.", file=sys.stderr)
    sys.exit(2)
if result.returncode == 5 or "No module named pytest" in result.stderr:
    sys.exit(0)  # 테스트 환경이 없으면 건너뜀
if result.returncode != 0:
    print(f"{rel} 수정 후 pytest 실패:\n{result.stdout[-3000:]}{result.stderr[-1000:]}", file=sys.stderr)
    sys.exit(2)
