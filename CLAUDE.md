# ai-shortform-video

대본 한 편으로 9:16 쇼츠 영상을 만드는 FastAPI 서버. 사용자 문서는 `README.md`, 설정 전체 목록은 `app/config.py`.

## 구조

- `app/main.py` — FastAPI 앱, `/api/v1/shorts/*` 라우트, 웹 화면(`app/web/index.html`) 제공
- `app/pipeline.py` — `ShortsPipeline`: 씬 기획 → 씬별 병렬(나레이션·이미지·자막·렌더) → 이어 붙이기
- `app/services/planner.py` — Gemini 씬 기획 (JSON 스키마 강제, `normalize_plan`으로 정리)
- `app/services/tts.py` — 나레이션: Edge TTS → Gemini 음성 → gTTS (`TTSChain`)
- `app/services/images.py` — 이미지: Gemini → Pexels → Pixabay → Openverse → 그라디언트 배경 (`ImageSourceChain`)
- `app/services/video.py` — Pillow 자막 PNG, FFmpeg 렌더링·한글 폰트 탐색, 배경음·효과음 합성(`mix_soundtrack`)
- `app/services/audio.py` — 배경음악(Openverse 오디오 검색), 전환 효과음 합성
- `app/fallback.py`, `app/retry.py` — 공용 예비 수단 체인과 재시도
- `app/jobs.py` — 작업 상태를 프로세스 메모리에 저장 (워커 1개 전제)
- `app/auth.py`, `app/signing.py` — API 키 인증, 영상 링크 HMAC 서명
- `desktop.py`, `packaging/`, `windows/` — Windows 실행 파일·설치 배치 파일
- `scripts/termux-*.sh` — 안드로이드(Termux) 설치·실행

## 실행과 테스트

```bash
pip install -r requirements-dev.txt
pytest            # 외부 API 없이 가짜 공급자로 실제 FFmpeg 렌더링까지 검증 (약 15초)
python main.py    # http://localhost:8000
```

FFmpeg와 한글 폰트(`fonts-noto-cjk` 등)가 필요하다. CI(`.github/workflows/windows-exe.yml`)는 Windows에서 테스트 후 exe를 만든다.

## 규칙

- 외부 서비스 호출은 실패를 전제로 짠다. 새 공급자는 기존 체인(`FallbackChain`)에 끼워 넣고, 한 공급자가 실패해도 영상 생성은 끝까지 가야 한다.
- Gemini 모델 이름은 자주 바뀌고 내려간다. 고정 버전보다 `-latest` 별칭을 기본값으로 쓰고, 바꿀 때는 실제 키로 목록을 확인한다.
- 테스트는 실제 API 키나 네트워크에 기대지 않는다. 환경변수에 키가 있어도 결과가 같아야 한다 (`monkeypatch.delenv`).
- 설정은 `Settings` 필드 + `.env.example` 주석 + README를 함께 고친다.
- `.env`는 사용자의 실제 키가 들어 있는 파일이라 고치지 않는다(훅이 막는다). 예시는 `.env.example`에 쓴다.
- 사용자 대상 메시지·주석·문서는 한국어로 쓴다.

## Claude Code 설정

- `.claude/settings.json` 훅: `.env` 수정 차단, `app/`·`tests/`의 파이썬 파일을 고치면 `pytest` 자동 실행
- 작업 관찰(task-observer) 시작 규칙: @.claude/task-observer.md
