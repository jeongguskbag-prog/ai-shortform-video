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

```bash
# 작업 생성 (202 Accepted)
curl -X POST localhost:8000/api/v1/shorts/generate \
  -H 'Content-Type: application/json' \
  -d '{"script": "하루 10분 투자로 인생이 바뀌는 습관 3가지를 알려드릴게요...", "tone": "energetic"}'
# → {"job_id": "3f9c...", "status": "QUEUED", "status_url": "/api/v1/shorts/status/3f9c...", ...}

# 상태 조회
curl localhost:8000/api/v1/shorts/status/3f9c...
# → {"status": "COMPLETED", "progress": 100, "video_url": "/static/3f9c.../final_3f9c....mp4",
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
- 이 API에는 인증이 없습니다. 외부에 공개할 때는 리버스 프록시나 API 키 인증을 앞에 두세요.
