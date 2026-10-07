# ai-shortform-video

대본 한 편을 입력하면 **Gemini**가 씬을 기획하고, **Edge TTS**로 나레이션을, **Imagen**으로 씬 이미지를 만든 뒤
**FFmpeg**로 자막·켄 번스 효과가 들어간 9:16 쇼츠 영상을 합성하는 FastAPI 서버입니다.

## 파이프라인

```
대본 ─▶ Gemini 씬 기획 (JSON 스키마 강제, 씬 정규화)
        └▶ 씬별 병렬 처리 (동시 실행 수 제한)
             ├ Edge TTS 나레이션        (재시도)
             ├ Imagen 이미지            (재시도 → 실패 시 대체 배경 + 경고)
             ├ Pillow 자막 PNG          (한글 폰트, 자동 줄바꿈, 외곽선, 반투명 박스)
             └ FFmpeg 씬 렌더링          (켄 번스 줌 인/아웃 교차, 음성 길이에 맞춤)
        └▶ FFmpeg 무손실 이어 붙이기 (+faststart)
```

## 실행

### Docker (권장)

```bash
cp .env.example .env   # GEMINI_API_KEY 입력
docker build -t ai-shorts .
docker run --env-file .env -p 8000:8000 -v "$PWD/output:/app/output" ai-shorts
```

### 로컬

필요: Python 3.11+, `ffmpeg`/`ffprobe`, 한글 폰트(예: `fonts-noto-cjk`, 나눔고딕). 폰트는 자동 탐색하며 `FONT_PATH`로 지정할 수도 있습니다.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # GEMINI_API_KEY 입력
python main.py         # 또는 uvicorn app.main:app --reload
```

웹 화면: http://localhost:8000 — 대본 입력, 분위기 선택, 진행 상황, 영상 미리보기·다운로드, 최근 작업 목록

API 문서: http://localhost:8000/docs

## API

### 인증

`.env`의 `API_KEYS`에 키를 넣으면 `/api/v1/*` 요청에 키가 필요합니다. 쉼표로 여러 개를 넣을 수 있어 키를 바꿀 때
새 키를 추가하고 옛 키를 나중에 지우면 됩니다. 비워 두면 인증 없이 실행됩니다 (로컬 개발용).

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # 키 생성
```

- 헤더: `X-API-Key: <키>` 또는 `Authorization: Bearer <키>`
- 웹 화면은 서버가 인증을 요구하면 키 입력란을 보여주고, 입력한 키를 그 브라우저에만 저장합니다.
- `/`, `/health`, `/docs`는 공개입니다.

### 영상 링크

완성 영상은 `<video>` 태그로 재생해야 해서 API 키 대신 **서명된 링크**로 엽니다.

- 상태 조회 응답의 `video_url`은 `/videos/{job_id}/{파일}?expires=...&sig=...` 형태이며, `video_url_expires_at`까지만
  열립니다 (기본 1시간, `VIDEO_URL_TTL_SEC`). 서명은 HMAC-SHA256이라 주소나 만료 시각을 바꾸면 403이 됩니다.
- 만료되면 상태를 다시 조회해 새 링크를 받으세요. 웹 화면은 이를 자동으로 처리합니다.
- `URL_SIGNING_SECRET`을 비워 두면 서버가 시작할 때마다 새 비밀키를 만들어, 재시작하면 기존 링크가 모두 무효가 됩니다.

```bash
# 작업 생성 (202 Accepted)
curl -X POST localhost:8000/api/v1/shorts/generate \
  -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"script": "하루 10분 투자로 인생이 바뀌는 습관 3가지를 알려드릴게요...", "tone": "energetic"}'
# → {"job_id": "3f9c...", "status": "QUEUED", "status_url": "/api/v1/shorts/status/3f9c...", ...}

# 상태 조회
curl -H "X-API-Key: $API_KEY" localhost:8000/api/v1/shorts/status/3f9c...
# → {"status": "COMPLETED", "progress": 100, "video_url": "/videos/3f9c.../final_3f9c....mp4?expires=...&sig=...",
#    "video_url_expires_at": "...",
#    "title": "...", "scene_count": 6, "duration_sec": 27.4, "warnings": [], ...}
```

상태: `QUEUED → PLANNING → GENERATING_ASSETS → RENDERING_FINAL → COMPLETED | FAILED`

## 설정

모든 설정은 환경변수(.env)로 바꿀 수 있습니다. 전체 목록은 `app/config.py`, 예시는 `.env.example`을 참고하세요.

## 테스트

외부 API 없이 가짜 공급자로 실제 FFmpeg 렌더링까지 검증합니다.

```bash
pip install -r requirements-dev.txt
pytest
```

## 운영 시 참고

- 작업 상태는 프로세스 메모리에 저장되므로 **워커 1개**로 실행하세요. 수평 확장이 필요하면 `JobStore`를 Redis로,
  작업 실행을 Celery/arq 같은 큐로 바꾸면 됩니다 (`ShortsPipeline`은 그대로 재사용 가능).
- 완료된 작업과 파일은 `JOB_TTL_HOURS` 후 자동 삭제됩니다.
- 외부에 공개할 때는 `API_KEYS`를 꼭 설정하고 HTTPS(리버스 프록시) 뒤에서 실행하세요. 키가 평문 헤더로 전송됩니다.
