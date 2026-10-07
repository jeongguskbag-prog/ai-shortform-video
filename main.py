"""로컬 실행 진입점.

    python main.py            # 일반 실행
    python main.py --reload   # 코드 수정 시 자동 재시작 (개발용)
"""

import sys

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload="--reload" in sys.argv)
