"""Windows 실행 파일(.exe) 진입점.

더블클릭하면 설정을 확인하고, 서버를 켠 뒤 브라우저로 웹 화면을 엽니다.
창을 닫으면 서버도 꺼집니다.

    python desktop.py          # 개발 중 같은 방식으로 실행
"""

import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# 실행 파일 옆 폴더: .env, output/, ffmpeg.exe 를 여기서 찾습니다.
BASE_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
# 실행 파일 안에 묶인 파일(.env.example 등)이 풀리는 곳
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
PLACEHOLDER_KEYS = {"", "your_gemini_api_key_here"}
NO_BROWSER = os.environ.get("SHORTS_NO_BROWSER") == "1"  # 자동 테스트용


def pause(message: str) -> None:
    print(message)
    try:
        input("\nEnter 키를 누르면 창이 닫힙니다...")
    except EOFError:
        pass


def ensure_env_file() -> Path:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        shutil.copyfile(BUNDLE_DIR / ".env.example", env_path)
    return env_path


def open_in_editor(path: Path) -> None:
    if NO_BROWSER:
        return
    if sys.platform == "win32":
        os.startfile(path)  # 메모장 등 기본 편집기로 열림
    else:
        webbrowser.open(path.as_uri())


def find_port(preferred: int = 8000) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("사용할 수 있는 포트가 없습니다.")


def open_browser_when_ready(url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.5)


def main() -> int:
    os.chdir(BASE_DIR)  # .env와 output/ 을 실행 파일 옆에서 읽고 쓰도록
    # 함께 배포한 ffmpeg.exe / ffprobe.exe 를 먼저 찾도록 PATH 앞에 추가
    os.environ["PATH"] = str(BASE_DIR) + os.pathsep + os.environ.get("PATH", "")

    env_path = ensure_env_file()
    from app.config import Settings

    if (Settings().gemini_api_key or "").strip() in PLACEHOLDER_KEYS:
        open_in_editor(env_path)
        pause(
            "Gemini API 키가 아직 설정되지 않았어요.\n\n"
            f"방금 열린 메모장({env_path.name})에서\n"
            "  GEMINI_API_KEY=your_gemini_api_key_here\n"
            "줄의 your_gemini_api_key_here 를 지우고 키를 붙여 넣은 뒤 저장(Ctrl+S)하세요.\n"
            "키 발급: https://aistudio.google.com/apikey\n\n"
            "저장한 다음 이 프로그램을 다시 실행하면 됩니다."
        )
        return 1

    import uvicorn

    try:
        from app.main import app
    except Exception as exc:  # 설치 문제를 창이 바로 닫히지 않게 보여줌
        pause(f"프로그램을 시작하지 못했어요: {exc}")
        return 1

    port = find_port()
    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print(f"  ai-shortform-video 시작 중:  {url}")
    print("  준비되면 브라우저가 자동으로 열립니다. 이 창을 닫으면 프로그램이 종료돼요.")
    print("=" * 60, flush=True)
    if not NO_BROWSER:
        threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    try:
        server.run()
    except SystemExit:
        pass  # uvicorn은 시작 실패 시 프로그램을 바로 끝내려 하므로, 아래에서 오류를 보여줄 수 있게 막음
    except Exception as exc:
        pause(f"서버가 멈췄어요: {exc}")
        return 1
    if not server.started:
        # ffmpeg·폰트를 못 찾는 등 시작 단계에서 실패하면 uvicorn은 조용히 끝나므로 창을 붙잡아 둠
        pause("\n프로그램을 시작하지 못했어요. 위에 나온 오류 메시지를 확인해 주세요.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
