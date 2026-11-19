"""PreToolUse 훅: 실제 키가 들어 있는 .env 파일 수정을 막는다 (.env.example은 허용)."""

import json
import sys
from pathlib import Path

data = json.load(sys.stdin)
path = Path(data.get("tool_input", {}).get("file_path") or "")
if path.name == ".env":
    print(".env에는 사용자의 실제 키가 들어 있어 수정하지 않습니다. 예시는 .env.example에 쓰세요.", file=sys.stderr)
    sys.exit(2)
