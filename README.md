# 라즈베리파이 SNS 자동 글쓰기 — 예제 소스

전자책 『라즈베리파이 한 대로 만드는 SNS·블로그 자동 글쓰기』의 예제 코드입니다.
**Threads · Instagram 릴스 · 네이버 블로그**에 자동으로 글을 올리는 파이썬 프로그램과, 라즈베리파이 cron 으로 굴리는 방법이 들어 있습니다.

> ⚠️ 각 서비스의 약관·API 정책은 수시로 바뀝니다. 제휴 링크를 쓰는 글에는 **대가성(광고) 문구**를 꼭 표기하고,
> **본인 계정에만** 사용하세요. 네이버 블로그는 공식 API 가 없어 브라우저 자동화를 쓰므로 계정 제재 위험이 있습니다.

## 폴더 구성

```
.env.example          ← 설정 예시 (복사해서 .env 로 사용)
requirements.txt      ← 설치할 파이썬 패키지
content/posts.example.json   ← 올릴 글 목록 예시 (복사해서 posts.json 으로 사용)
common/               ← 공통: .env 읽기, 중복 게시 방지 DB, 글감 소스, AI 문구, 메일
threads/              ← Threads 게시 (토큰 발급·갱신, 게시, 자동 댓글 답글)
reels/                ← 릴스 (영상 제작 + 인스타그램 게시, 토큰 점검·갱신)
naver/                ← 네이버 블로그 (로그인 세션 저장 + 글쓰기)
cron/                 ← 라즈베리파이 자동 실행 (run.sh, crontab.example)
examples/             ← 새 채널을 붙이는 연습용 예제 (텔레그램, 책 7장)
tools/                ← 운영 도구 (현황 보기, 백업, 생존 신고, 결과 불명 항목 확정)
tests/                ← 자동 테스트 (네트워크 없이 실행)
```

## 1. 설치 (라즈베리파이 OS / Linux 기준)

```bash
sudo apt update
sudo apt install -y python3-venv ffmpeg fonts-noto-cjk xvfb

cd ~/autopost                      # 이 폴더를 받은 위치
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # 네이버 블로그를 쓸 때만
```

Windows/macOS 에서도 개발·테스트는 가능합니다(ffmpeg 는 별도 설치). 자동 실행(cron)은 리눅스/파이 기준입니다.

## 2. 설정 — `.env.example` → `.env`

```bash
cp .env.example .env
nano .env            # 사용할 채널의 값만 채웁니다
```

* `.env` 에는 토큰/키가 들어갑니다. **절대 공유하지 마세요.** (`.gitignore` 에 이미 제외돼 있습니다)
* 사용하지 않는 채널의 항목은 비워 두면 됩니다.
* 값 설명은 `.env.example` 안의 주석을 보세요.

## 3. 올릴 글 준비 — `content/posts.json`

```bash
cp content/posts.example.json content/posts.json
```

```json
[
  {
    "id": "my-001",                     // 글을 구분하는 고유 값 (중복 게시 방지에 사용)
    "title": "제목",
    "text": "본문. 비워 두면 AI 가 title 로 만들어 줍니다.",
    "image_url": "https://.../a.jpg",    // (선택) Threads 이미지 / 릴스 배경
    "image_path": "images/a.jpg",        // (선택) 내 컴퓨터의 이미지 → 릴스 배경
    "link": "https://...",               // (선택) 있으면 대가성 문구가 자동으로 붙습니다
    "hashtags": ["태그1", "태그2"]
  }
]
```

* 위의 `//` 설명은 이해를 돕기 위한 것입니다. JSON 에는 주석을 쓸 수 없으니 실제 파일에는 넣지 마세요(`posts.example.json` 이 올바른 형태).
* 한 번 올린 글(`id`)은 다시 올리지 않습니다. 같은 글을 또 올리려면 `id` 를 바꾸세요.
* 글을 직접 쓰는 대신 RSS 를 쓰려면 `.env` 에서 `SOURCE_TYPE=rss`, `RSS_URL=...` 로 바꾸세요.
* 쇼핑 API·뉴스 크롤링 등 나만의 소스를 쓰려면 `common/sources.py` 의 `get_items()` 가 `Item` 리스트를 돌려주도록 만들면 됩니다.

## 4. 먼저 "연습 실행" (아무것도 게시하지 않음)

```bash
python -m threads.post_threads --dry-run --count 3     # 만들어질 Threads 문구 확인
python -m naver.post_naver --preview --count 1         # 만들어질 블로그 글 확인
python -m reels.post_reels --dry-run --count 1         # 릴스 영상만 만들어 data/reels_preview 에 저장
python -m pytest -q                                    # 자동 테스트
```

## 5. 채널별 시작하기

### 5-1. Threads

1. [Meta for Developers](https://developers.facebook.com) 에서 앱을 만들고 **Threads API** 제품을 추가합니다.
2. 앱 역할에서 본인 계정을 **Threads 테스터**로 추가하고 수락합니다(앱이 Live 가 되기 전에는 테스터만 사용 가능).
3. Threads 설정에서 **리디렉션 콜백 URL**(https) 을 등록하고, 앱 ID/시크릿/콜백 URL 을 `.env` 의
   `THREADS_APP_ID`, `THREADS_APP_SECRET`, `THREADS_REDIRECT_URI` 에 적습니다. (콜백 페이지가 실제로 열리지 않아도 됩니다)
4. 토큰 발급:
   ```bash
   python -m threads.oauth_helper auth-url                       # 출력된 주소를 브라우저에서 열어 로그인/허용
   python -m threads.oauth_helper exchange "<주소창의 전체 URL>" --save   # 60일 장기 토큰을 .env 에 저장
   ```
5. 게시: `python -m threads.post_threads --count 1`
6. 장기 토큰 갱신(주 1회 cron 권장): `python -m threads.token_refresh --save`
7. 댓글 자동 답글: `python -m threads.reply_comments --dry-run`

필요 권한: `threads_basic`, `threads_content_publish`, `threads_manage_replies`, `threads_read_replies`.
제휴 링크는 **본문이 아니라 자기 댓글**로 답니다(본문이 깔끔해지고 대가성 문구도 댓글에 함께 들어갑니다).

### 5-2. Instagram 릴스

1. 인스타그램 계정을 **프로페셔널(비즈니스/크리에이터)** 계정으로 전환합니다.
2. Meta 앱에 **Instagram → API setup with Instagram login** 을 추가하고, 본인 계정을 테스터로 연결한 뒤
   **토큰 생성**으로 얻은 값을 `.env` 의 `IG_ACCESS_TOKEN` 에, 계정 ID 를 `IG_USER_ID` 에 넣습니다.
3. 확인: `python -m reels.token_tool check --save` (`IG_USER_ID` 를 몰라도 되며, 확인된 값을 `.env` 에 저장합니다)
4. 영상만 만들어 보기: `python -m reels.post_reels --dry-run` (ffmpeg·gTTS 필요, 인터넷 연결 필요)
5. 제작 + 게시: `python -m reels.post_reels --count 1`
6. 토큰 갱신(주 1회 cron 권장): `python -m reels.token_tool refresh --save`

영상은 **이미지(없으면 단색 배경) + 나레이션(gTTS) + 자막**으로 만들어지는 1080×1920 세로 영상입니다.
인스타그램은 영상을 **공개 주소(URL)** 로 가져가므로, 만든 영상을 잠깐 올려 둘 곳이 필요합니다. 기본은 Firebase Storage(`REELS_UPLOAD_MODE=firebase`)이고, S3 호환 저장소(`s3`, Cloudflare R2/AWS S3 등)도 쓸 수 있으며, 내가 정한 명령으로 올리는 `command` 방식도 있습니다(`reels/hosting.py`, `.env.example` 참고). `resumable` 은 실험적입니다. Meta 의 엔드포인트가 바뀌면 `reels/instagram_client.py` 를 조정하세요.

### 5-3. 네이버 블로그

```bash
python -m naver.login_setup          # 브라우저가 뜨면 "직접" 로그인 (비밀번호는 저장하지 않고 세션만 저장)
python -m naver.post_naver --dry-run # 글 입력까지만 해 보고 화면 확인
python -m naver.post_naver --count 1 # 실제 발행
```

* 파이에서 모니터가 없다면 **VNC/원격 데스크톱**으로 접속해 최초 로그인을 하세요. 이후 cron 은 `xvfb-run` 으로 화면 없이 실행합니다.
* 세션이 풀리면 종료 코드 2 + 알림 메일이 옵니다 → `login_setup` 을 다시 실행합니다.
* 에디터 화면이 바뀌어 실패하면 `data/debug/` 에 스크린샷이 남습니다. `naver/post_naver.py` 의 선택자(`PUBLISH_SELECTORS` 등)를 조정하세요.

## 6. 라즈베리파이에서 자동 실행 (cron)

```bash
chmod +x cron/run.sh
crontab -e            # cron/crontab.example 내용을 경로를 바꿔 붙여 넣기
```

`cron/run.sh` 가 하는 일: 가상환경 사용 · 중복 실행 방지(`flock`) · 네이버는 가상 화면(`xvfb-run`) · 날짜별 로그(`logs/`) · 실패 시 메일.

운영 도구:

```bash
python -m tools.status            # 채널별 게시 수, 문제 항목(pending/실패), 디스크 여유 (--mail 로 메일 발송)
python -m tools.backup            # 게시 기록(state.db) 백업, 최근 14개 보관
python -m tools.heartbeat         # 생존 신고(.env 의 HEALTHCHECK_URL, 선택)
python -m tools.resolve_pending list   # 결과 불명(pending) 항목 확인·확정
```

## 7. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| `.env 에 다음 값을 채워 주세요` | `.env` 가 없거나 해당 값이 비어 있음 → `.env.example` 을 복사해 채우기 |
| Threads `code=190` | 토큰 만료/무효 → `oauth_helper` 로 다시 발급 |
| Threads 댓글만 계속 실패 | `threads_manage_replies` 권한 없이 받은 토큰 → 권한 포함해 다시 로그인 동의 |
| `이전 시도의 결과를 알 수 없습니다` | 발행 요청 후 응답을 못 받은 상태 → 실제 게시 여부 확인 후 `python -m tools.resolve_pending ...` |
| 릴스 자막이 □□ 로 보임 | 한글 글꼴 없음 → `sudo apt install fonts-noto-cjk` |
| 네이버 종료 코드 2 | 로그인 세션 만료 → `python -m naver.login_setup` |
| 네이버 종료 코드 3 | 발행 단계 실패(에디터 변경/봇 검증/한도) → `data/debug/` 스크린샷 확인, 간격·건수 줄이기 |

## 8. 책 장별 대응

| 책 | 코드 |
|---|---|
| 2장 환경 준비 | `requirements.txt`, `.env.example`, `common/config.py` |
| 3장 Threads | `threads/` , `common/store.py`(중복 방지), `common/ai.py` |
| 4장 Reels | `reels/` |
| 5장 네이버 블로그 | `naver/` |
| 6장 운영 | `cron/`, `tools/resolve_pending.py`, `common/notify.py` |

## 라이선스 / 면책

이 예제 소스는 **MIT 라이선스**로 공개됩니다. 전문은 [`LICENSE`](LICENSE) 파일을 보세요. 사용·수정·배포는 자유이며, 사본에는 저작권 표시와 라이선스 문구를 포함해야 합니다.
(이 라이선스는 소스 코드에만 적용되며, 함께 제공되는 책(PDF)에는 적용되지 않습니다.)

학습·개인 사용 목적의 예제이며 **어떤 보증도 없이 "있는 그대로" 제공**됩니다. 각 서비스(Meta, 네이버 등)의 약관과 정책을 지켜 본인 계정에서 사용하세요.
사용으로 인한 계정 제재·서비스 중단·손해에 대해 저자는 책임지지 않습니다.
