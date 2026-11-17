"""frogterminal 브릿지 본체 — 웹소켓 ↔ pty ↔ tmux attach 글자 중계 + 세션/업로드 API.

보안 원칙: 127.0.0.1 전용 · fail-closed 인증 · Funnel이면 기동 거부 · 파일 브라우저 없음.
"""
import asyncio
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from bridge import (agent_updates, browser_api, config, decisions, files, locate, office, pad_api, preview_api,
                    remote_files, restore, security, sessions, settings, uploads)

log = logging.getLogger("frogterminal")


def audit_path():
    """설정 저장 직후부터 새 작업공간의 append-only 로그를 사용한다."""
    return config.audit_file()


def _audit(line: str) -> None:
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:                       # append-only 감사로그
        from datetime import datetime
        f.write(f"{datetime.now(config.KST).isoformat()} {line}\n")


@web.middleware
async def security_headers_middleware(request, handler):
    """SECURITY_HEADERS 를 모든 응답에 한곳에서 붙인다 — 라우트가 늘 때 빠뜨리지 않게.

    인증 미들웨어보다 바깥(리스트 첫 자리)에 둬야 403 응답에도 붙는다.
    """
    resp = await handler(request)
    if not resp.prepared:                            # 이미 나간 응답(WS)의 헤더는 못 바꾼다
        resp.headers.update(SECURITY_HEADERS)
    return resp


@web.middleware
async def auth_middleware(request, handler):
    # ①Tailscale 신원(bridge.json 에 allowedLogins 가 있을 때 필수) ②Origin — "있으면 허용목록이거나 같은
    # 출처, 없으면 통과"(request.host 를 넘기는 이유는 security.origin_ok 참고) ③자체 토큰 쿠키(항상).
    # ①②가 먼저라 "Tailscale 없이는 로그인 화면조차 안 뜬다"는 성질이 그대로고, 그 뒤에 ③이 한 겹 더 선다.
    if not security.allowed_login(request.headers) or not security.origin_ok(request.headers, request.host):
        return web.json_response({"error": "forbidden"}, status=403)
    if not security.token_ok(request.cookies.get(security.COOKIE)):
        return await login_gate(request)
    return await handler(request)


# ── 자체 토큰 로그인 ──────────────────────────────────────────────────────────
LIMITER = web.AppKey("limiter", security.LoginLimiter)
LOGIN_PAGE = config.WEB_DIR / "login.html"      # STATIC_FILES 밖 — 인증 전에 미들웨어가 직접 내준다


def _login_page(message: str = "", status: int = 401) -> web.Response:
    # message 는 서버가 정한 고정 문구만 들어온다(사용자 입력 아님) — 치환 한 번으로 충분하다.
    html = LOGIN_PAGE.read_text(encoding="utf-8").replace("{{error}}", message)
    return web.Response(text=html, content_type="text/html", charset="utf-8", status=status)


def _is_https(request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


def _who(request) -> str:
    return (f"user={request.headers.get('Tailscale-User-Login')} "
            f"ip={request.headers.get('X-Forwarded-For') or request.remote}")


async def login_gate(request):
    """토큰 쿠키가 없거나 틀린 요청 — 화면 진입은 로그인 페이지, 그 밖은 403.

    맞는 토큰은 POST /login(form) 또는 GET /?token=… 으로 들어온다. 둘 다 쿠키를 심고 303 으로 `/` 에
    보낸다(주소에 남은 토큰을 떼기 위해). 실패는 전역 10분·10회 제한 뒤 429.
    """
    presented = None
    if request.method == "POST" and request.path == "/login":
        form = await request.post()
        presented = str(form.get("token", "")).strip()
    elif request.method == "GET" and request.path == "/" and "token" in request.query:
        presented = request.query["token"].strip()
    elif request.method == "GET" and request.path == "/":
        return _login_page()
    else:
        return web.json_response({"error": "forbidden"}, status=403)

    limiter = request.app[LIMITER]
    if not limiter.allowed():
        _audit(f"login throttled {_who(request)}")
        return _login_page("시도가 너무 많습니다 — 10분 뒤에 다시 하세요", status=429)
    if not security.token_ok(presented):
        limiter.record_failure()
        _audit(f"login fail {_who(request)}")
        return _login_page("토큰이 맞지 않습니다")
    _audit(f"login ok {_who(request)}")
    resp = web.HTTPSeeOther("/")
    resp.set_cookie(security.COOKIE, presented, max_age=security.COOKIE_MAX_AGE, path="/",
                    httponly=True, samesite="Lax", secure=_is_https(request))
    return resp


# 정적 파일 화이트리스트 — 경로 → (파일명, Content-Type)
# ⚠️ add_static(디렉터리 통째 서빙) 절대 금지: web/ 안에 .env.local(OIDC 토큰)·.vercel/ 이 있다.
# 여기 적힌 것만 나가고 나머지는 라우터가 404 를 낸다.
STATIC_FILES = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/pane.js": ("pane.js", "text/javascript"),
    "/grid.js": ("grid.js", "text/javascript"),
    "/inbox.js": ("inbox.js", "text/javascript"),
    "/dash.js": ("dash.js", "text/javascript"),
    "/settings.js": ("settings.js", "text/javascript"),
    "/split.js": ("split.js", "text/javascript"),
    "/files.js": ("files.js", "text/javascript"),
    "/browser.js": ("browser.js", "text/javascript"),
    "/remote.js": ("remote.js", "text/javascript"),
    "/pad.js": ("pad.js", "text/javascript"),                     # 게임패드 신호 롱폴 → 칸 이동
    "/office-world.js": ("office-world.js", "text/javascript"),   # 3D 모션 모드 — 사무실 지도·대사·색
    "/office-atlas.js": ("office-atlas.js", "text/javascript"),   # 그림 칸 규격(process_assets.py 자동 생성)
    "/office-life.js": ("office-life.js", "text/javascript"),     # 하루 — 걷기·할 일·수다·꼬마
    "/office-view.js": ("office-view.js", "text/javascript"),     # 그리기·오른쪽 로그·지니 효과
    # 사무실 그림(scripts/office/process_assets.py 가 만든다) — 이름을 하나씩 적는다(폴더 통째 서빙 금지)
    "/office/floor.webp": ("office/floor.webp", "image/webp"),
    "/office/frog.webp": ("office/frog.webp", "image/webp"),
    "/office/frog-key.webp": ("office/frog-key.webp", "image/webp"),
    "/office/frog2.webp": ("office/frog2.webp", "image/webp"),
    "/office/frog2-key.webp": ("office/frog2-key.webp", "image/webp"),
    "/office/frog3.webp": ("office/frog3.webp", "image/webp"),
    "/office/frog3-key.webp": ("office/frog3-key.webp", "image/webp"),
    "/office/frogmini.webp": ("office/frogmini.webp", "image/webp"),
    "/office/frogmini-key.webp": ("office/frogmini-key.webp", "image/webp"),
    "/office/frogwalk.webp": ("office/frogwalk.webp", "image/webp"),
    "/office/frogwalk-key.webp": ("office/frogwalk-key.webp", "image/webp"),
    "/office/capy.webp": ("office/capy.webp", "image/webp"),
    "/office/capy-key.webp": ("office/capy-key.webp", "image/webp"),
    "/office/capy2.webp": ("office/capy2.webp", "image/webp"),
    "/office/capy2-key.webp": ("office/capy2-key.webp", "image/webp"),
    "/office/capy3.webp": ("office/capy3.webp", "image/webp"),
    "/office/capy3-key.webp": ("office/capy3-key.webp", "image/webp"),
    "/office/capymini.webp": ("office/capymini.webp", "image/webp"),
    "/office/capymini-key.webp": ("office/capymini-key.webp", "image/webp"),
    "/office/capywalk.webp": ("office/capywalk.webp", "image/webp"),
    "/office/capywalk-key.webp": ("office/capywalk-key.webp", "image/webp"),
    "/office/frogphone.webp": ("office/frogphone.webp", "image/webp"),
    "/office/frogphone-key.webp": ("office/frogphone-key.webp", "image/webp"),
    "/office/capyphone.webp": ("office/capyphone.webp", "image/webp"),
    "/office/capyphone-key.webp": ("office/capyphone-key.webp", "image/webp"),
    "/office/chairs.webp": ("office/chairs.webp", "image/webp"),
    "/config.js": ("config.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),  # PWA 설치(독 앱)
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


# 프레임 차단 — 남의 페이지가 <iframe> 으로 우리 UI 를 심는 클릭재킹을 막는다.
# 내비게이션은 Origin 을 안 보내 origin_ok 를 통과하고, Serve 가 신원 헤더를 붙여 완전 동작 UI 가
# 렌더되기 때문에, 내용을 못 읽어도 클릭 유도만으로 세션 진입·키바(↑+⏎ 직전 명령 재실행)·업로드가 된다.
# CSP 는 우선 '관찰만'(Report-Only) — 위반이 콘솔에만 찍히고 막지는 않는다. 렌더가 전부 textContent 라
# 지금은 깨질 게 없지만, 공개본은 남이 코드를 고친다 — 며칠 콘솔을 보고 강제(Content-Security-Policy)로
# 올린다. 인라인 스크립트는 index.html 의 테마 선적용 한 개뿐이라 해시로 허용한다(고치면 해시도 고칠 것).
CSP_REPORT_ONLY = ("default-src 'self'; "
                   "script-src 'self' https://cdn.jsdelivr.net 'sha256-SPbnfuG0MS+B8S1dS3sqfFLOXM9Vik2rB6As4/dNDcA='; "
                   "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "     # xterm 이 인라인 style 을 쓴다
                   "font-src 'self' https://cdn.jsdelivr.net data:; "
                   "img-src 'self' data: blob:; connect-src 'self'; "
                   "frame-src *; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",        # no-referrer 는 크롬이 같은 출처 폼 POST 에도 Origin: null 을 보내 /login 이 403 (2026-10-11)
    "Cache-Control": "no-cache",             # 폰이 낡은 app.js 를 붙들지 않게
    "Content-Security-Policy-Report-Only": CSP_REPORT_ONLY,
}


async def static_file(request):
    """프론트를 브릿지와 같은 출처에서 내준다 — 크로미움 LNA 가 공개 오리진→테일넷 fetch 를 막기 때문."""
    name, ctype = STATIC_FILES[request.path]
    path = config.WEB_DIR / name
    if not path.is_file():
        return web.Response(status=404)
    charset = None if ctype.startswith("image/") else "utf-8"   # PNG 에 charset 이 붙으면 안 된다
    return web.Response(body=path.read_bytes(), content_type=ctype, charset=charset)


class OverviewCache:
    """결재 집계를 이벤트 루프 밖에서 돌리고, 한 폴링 주기에 한 번만 돈다.

    decisions.overview() 는 살아있는 세션마다 tmux capture-pane(실측 4.3ms)을 블로킹으로
    돈다 — 세션 15개면 1회 65ms. 화면 한 번 갱신에 세 핸들러가 각각 부르면 그 3배 동안
    WS 펌프와 pty 읽기가 통째로 멈춘다(tmux 가 한 번 늦으면 timeout=5 만큼 터미널이 언다).
    앱마다 하나씩 둔다 — 전역 변수로 두면 테스트·재기동 사이에 낡은 값이 새어 나온다.
    """

    def __init__(self, ttl: float = config.OVERVIEW_TTL_SECONDS):
        self._ttl, self._at, self._data = ttl, 0.0, None
        self._lock = asyncio.Lock()

    async def get(self) -> dict:
        async with self._lock:                       # 동시 폴링이 tmux 를 겹쳐 두드리지 않게
            if self._data is None or time.monotonic() - self._at >= self._ttl:
                self._data = await asyncio.to_thread(decisions.overview)
                self._at = time.monotonic()          # 집계에 걸린 시간은 TTL 에서 뺀다
            return self._data

    def invalidate(self) -> None:
        self._data = None        # 답을 보낸 직후 — 안 지우면 "보냈는데 카드가 그대로"로 보인다


OVERVIEW = web.AppKey("overview", OverviewCache)
# 끄기 버튼(하나·전체)으로 끈 세션 이름. 웹의 칸이 연결이 끊기면 저절로 다시 붙는데(?re=1), 붙는 길이
# new-session -A 라 방금 끈 세션을 에이전트까지 새로 켜 버렸다 — 폰에서 끈 것이 맥 칸에서 되살아남
# (멀티 칸 2026-10-11). 그래서 '끈 것'만 기억했다가 저절로 붙기를 거절한다. /exit 로 끝난 세션은 여기
# 없으므로 다시 붙으면 '이어서 시작'으로 새로 뜬다(attach_cmd 주석의 의도된 동작 그대로).
# 사용자가 직접 여는 접속(re 없음)이나 [복원]이 오면 지운다. 메모리에만 둔다 — 브릿지를 다시 켜면 잊는다.
KILLED = web.AppKey("killed", set)
WS_KILLED = 4410                                    # 웹이 '다시 붙지 말 것'으로 읽는 닫기 코드
# 세션별로 '여러 칸으로 보는 중'인 연결들 — 하나라도 있으면 tmux 표시(@frog_compact)를 달고, 다 떠나면 뗀다.
# 폰(1칸)과 맥(4칸)이 같은 세션에 붙어도 맥이 남아 있는 동안은 짧은 HUD 가 유지된다.
COMPACT = web.AppKey("compact", dict)


async def _set_compact(app: web.Application, name: str, conn: object, on: bool) -> None:
    viewers = app[COMPACT].setdefault(name, set())
    before = bool(viewers)
    if on:
        viewers.add(conn)
    else:
        viewers.discard(conn)
    if bool(viewers) != before:                     # 바뀔 때만 tmux 를 부른다(1칸은 한 번도 안 부른다)
        await asyncio.to_thread(sessions.set_compact, name, bool(viewers))
    if not viewers:
        app[COMPACT].pop(name, None)


def _web_version() -> int:
    """프론트 파일들의 최신 mtime — 값이 바뀌면 화면이 '새 버전 배포' 배너를 띄운다.

    PWA·오래 열린 탭은 껐다 켜기 전까지 옛 코드를 물고 있다(2026-08-15 한글 소동의 절반이
    이것) — 고친 것이 실제 화면에 도달했는지를 서버가 알려 주는 장치다.
    """
    try:
        return max(int((config.WEB_DIR / name).stat().st_mtime) for name, _ in STATIC_FILES.values())
    except OSError:
        return 0


async def api_summary(request):
    ov = await request.app[OVERVIEW].get()
    alive = sum(1 for s in await asyncio.to_thread(sessions.merged_sessions) if s["alive"])
    # live=False 카드(죽은 세션의 유령 카드, TTL 동안 남음)는 헤드라인 결재 수에서 뺀다 —
    # 안 그러면 그 수를 보고 카드를 눌러도 답이 404로 샌다.
    pending = sum(1 for c in ov["cards"]
                 if c.get("kind") in decisions.DECISION_KINDS and c.get("live"))
    # 지시 대기 = 할 말을 마치고 쉬는 살아있는 세션(done·idle) — 인박스 '완료' 섹션과 같은 셈법.
    # 헤드라인이 결재만 말하면 "결재 0건"인데 인박스엔 지시 넣을 세션이 쌓여 있는 걸 못 본다.
    waiting = sum(1 for c in ov["cards"]
                  if c.get("kind") not in decisions.DECISION_KINDS and c.get("live"))
    # 죽은 세션 수는 따로 준다 — 결재 0건이어도 '살펴야 할 것'이 있으면 진입점이 알려야 한다
    # (디자이너 지적: 헤더가 0을 말하는데 인박스엔 꺼짐 카드가 쌓여 있으면 영영 못 본다).
    dead = sum(1 for c in ov["cards"] if not c.get("live"))
    return web.json_response({"alive": alive, "pending": pending, "waiting": waiting, "dead": dead,
                              "web": await asyncio.to_thread(_web_version)})


async def api_sessions(request):
    items = await asyncio.to_thread(sessions.merged_sessions)
    # 복원거리 수 — 헤더의 [복원] 버튼을 켤지 끌지의 근거. 없으면 버튼이 꺼진 채로 있어야
    # 사용자가 눌러 보고서야 "복원할 게 없다"는 말을 듣는 일이 없다.
    restorable = len((await asyncio.to_thread(restore.load_snapshot))["sessions"])
    states = (await request.app[OVERVIEW].get())["states"]
    for s in items:
        # states 는 이미 '살아있는 카드'만으로 만들어진다(decisions.overview 가 프로세스로 실측).
        # 그래서 여기서 tmux 존재(alive)로 한 번 더 거르면 안 된다 — 클로드는 대부분 tmux 밖에서
        # 돌기 때문에, 지금 결재를 기다리는 프로젝트가 사이드바에서는 '○ 꺼짐'으로 보였다.
        # (유령 카드가 빨간 배지를 달던 옛 문제는 overview 쪽에서 걸러진다 — 두 번 거르지 않는다.)
        st = states.get(s["name"])
        s["status"] = st["status"] if st else ("on" if s["alive"] else "off")
        s["pending"] = st["pending"] if st else 0
    return web.json_response({"sessions": items, "restorable": restorable})


async def api_office(request):
    """3D 모션 모드 — 켜진 프로젝트마다 캐릭터 한 명분(상태·결재 문구·커밋 수·일하는 서브에이전트).

    화면이 사무실을 보는 동안에만 4초마다 부른다. 결재 집계는 사이드바와 같은 캐시를 쓰고, 커밋 수는
    office 가 10분 캐시한다 — git·대화 파일 읽기는 루프 밖에서.
    """
    items = await asyncio.to_thread(sessions.merged_sessions)
    ov = await request.app[OVERVIEW].get()
    return web.json_response(await asyncio.to_thread(office.snapshot, items, ov))


async def api_decisions(request):
    return web.json_response({"decisions": (await request.app[OVERVIEW].get())["cards"]})


async def _json_object(request):
    """본문을 '객체 JSON'으로 읽는다 — 문법 오류·배열·문자열·숫자·null 은 None(=400 신호).

    .get() 을 바로 부르면 객체가 아닌 본문에서 AttributeError → 500 으로 샌다.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


async def api_session_kill(request):
    """사용자가 콕 집어 지목한 tmux 세션 하나를 끈다 — '다른 세션은 안 죽인다' 원칙의 유일한 예외.

    그 안에서 돌던 것(클로드 포함)이 같이 죽으므로 되묻기는 웹이 하고, 여기서는 반드시 남긴다.
    """
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    project = str(body.get("project", ""))
    # 관문은 하나 — is_valid_session. _uploads·_frogterminal 과 경로조작 이름이 여기서 걸린다.
    # (subprocess 를 부르므로 루프 밖에서: 안 그러면 tmux 가 늦는 동안 터미널이 통째로 언다.)
    if not await asyncio.to_thread(sessions.is_valid_session, project):
        return web.json_response({"error": "unknown project"}, status=404)
    try:
        killed = await asyncio.to_thread(sessions.kill_session, project)
    except LookupError:                              # tmux 실행 실패·TOCTOU — 500 으로 새지 않게
        return web.json_response({"error": "session not alive"}, status=404)
    request.app[OVERVIEW].invalidate()               # 사이드바가 곧바로 '꺼짐'으로 갱신되게
    request.app[KILLED].add(project)                 # 다른 기기의 칸이 저절로 다시 켜지 않게
    _audit(f"kill user={request.headers.get('Tailscale-User-Login')} "     # 개행 든 이름으로 가짜
           f"project={project!r} killed={killed}")                        # 줄을 위조하지 못하게 !r
    # 이미 꺼져 있었으면 killed=False — 사용자가 두 번 눌러도 에러창이 뜨면 안 된다(멱등).
    return web.json_response({"ok": True, "killed": killed})


async def api_sessions_kill_all(request):
    """헤더의 [전체 끄기] — 사이드바에 보이는 살아있는 터미널을 전부 끈다.

    되돌릴 수 없는 조작이라 되묻기는 웹이 하고, 여기서는 끄기 전에 명단부터 남긴다 —
    그 명단이 [복원]의 유일한 재료다. 무엇을 끄고 무엇을 못 껐는지 이름으로 돌려준다.
    """
    result = await asyncio.to_thread(restore.kill_all)
    request.app[OVERVIEW].invalidate()               # 사이드바가 곧바로 '꺼짐'으로 갱신되게
    request.app[KILLED].update(result["killed"])     # 다른 기기의 칸이 저절로 다시 켜지 않게
    _audit(f"kill-all user={request.headers.get('Tailscale-User-Login')} "
           f"killed={result['killed']!r} failed={result['failed']!r}")
    return web.json_response({"ok": True, **result})


async def api_sessions_restore(request):
    """헤더의 [복원] — 전체 끄기 직전에 열려 있던 그 세션들만 되살린다.

    '지금 열 수 있는 전부'가 아니다. 이미 살아 있는 것은 건드리지 않으므로 두 번 눌러도 안전하다.
    """
    result = await asyncio.to_thread(restore.restore_all)
    request.app[OVERVIEW].invalidate()
    request.app[KILLED].clear()                      # 되살렸다 — 칸들이 다시 붙어도 된다
    _audit(f"restore user={request.headers.get('Tailscale-User-Login')} "
           f"restored={result['restored']!r} failed={result['failed']!r}")
    return web.json_response({"ok": True, **result})


async def api_hidden_get(request):
    return web.json_response({"hidden": sorted(await asyncio.to_thread(sessions.load_hidden))})


async def api_hidden_set(request):
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    project, hidden = str(body.get("project", "")), body.get("hidden")
    if not isinstance(hidden, bool) or not sessions.is_valid_name(project):
        return web.json_response({"error": "bad body"}, status=400)
    # 넣을 때만 '실재하는 프로젝트인가'를 묻는다. 뺄 때도 물으면, 숨긴 뒤 폴더를 지운 이름이
    # 목록에 영영 갇힌다 — 빼기는 항상 지우기만 하므로 임의 문자열이 쌓일 일도 없다.
    if hidden and not await asyncio.to_thread(sessions.is_valid_session, project):
        return web.json_response({"error": "unknown project"}, status=404)
    return web.json_response({"hidden": await asyncio.to_thread(sessions.set_hidden, project, hidden)})


async def api_favorites_get(request):
    # 정렬하지 않는다 — 누른 순서가 곧 사이드바 맨 위의 줄 순서다(숨김과 다른 점).
    return web.json_response({"favorites": await asyncio.to_thread(sessions.load_favorites)})


async def api_favorites_set(request):
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    project, fav = str(body.get("project", "")), body.get("fav")
    # is_valid_name 이 '@' 를 막는다 = 탭(base@N)은 여기서 400 — 즐겨찾기는 프로젝트 단위다.
    if not isinstance(fav, bool) or not sessions.is_valid_name(project):
        return web.json_response({"error": "bad body"}, status=400)
    # 숨김과 같은 잣대 — 넣을 때만 '실재하는 프로젝트인가'를 묻는다. 뺄 때도 물으면 즐겨찾기해 둔
    # 뒤 폴더를 지운 이름이 목록에 영영 갇힌다(빼기는 지우기만 하므로 임의 문자열이 쌓일 일도 없다).
    if fav and not await asyncio.to_thread(sessions.is_valid_session, project):
        return web.json_response({"error": "unknown project"}, status=404)
    return web.json_response(
        {"favorites": await asyncio.to_thread(sessions.set_favorite, project, fav)})


async def api_settings_get(request):
    return web.json_response({"settings": await asyncio.to_thread(settings.load),
                              "agents": list(settings.AGENTS),
                              "home": str(Path.home())})       # 웹이 `~/경로` 를 펼칠 때 — 루트 이름을 코드에 박지 않는다


async def api_settings_post(request):
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    patch = {k: body[k] for k in
             ("projectsRoot", "agent", "bypass", "rc", "cont", "commands") if k in body}
    try:
        merged = await asyncio.to_thread(settings.save, patch)
    except ValueError as e:                          # 에이전트 목록 밖·켜짐/꺼짐이 불리언이 아님 — 400
        return web.json_response({"error": "unknown agent" if str(e) == "agent" else "invalid value",
                                  "field": str(e)}, status=400)
    except settings.InvalidAlias as e:               # 셸 파일에 쓸 수 없는 이름·보호 명령 — fail-closed
        return web.json_response({"error": "invalid alias", "alias": e.alias[:80],
                                  "reason": e.reason}, status=400)
    except settings.InvalidProjectsRoot as e:
        return web.json_response({"error": "invalid projects root", "code": e.code,
                                  "reason": e.reason}, status=400)
    except OSError:
        return web.json_response({"error": "write failed"}, status=500)
    _audit(f"settings user={request.headers.get('Tailscale-User-Login')} "
           f"projectsRoot={merged['projectsRoot']!r} "
           f"agent={merged['agent']} bypass={merged['bypass']} rc={merged['rc']} "
           f"commands={merged['commands']!r}")       # 별칭은 식별자 화이트리스트를 거쳤다 + !r
    return web.json_response({"settings": merged})


async def api_agents(request):
    """로컬 연결 상태(60초) + 공개 최신판(6시간). 어느 쪽 실패도 나머지 상태는 보인다."""
    local = await asyncio.to_thread(settings.status)
    latest = await agent_updates.latest_versions()
    return web.json_response({"agents": agent_updates.merge_latest(local, latest)})


async def api_agent_update(request):
    """사람이 누른 에이전트 하나만 고정 명령으로 갱신한다. 전역 인증 미들웨어를 그대로 탄다."""
    agent = request.match_info["agent"]
    if agent not in agent_updates.UPDATE_ARGS:
        return web.json_response({"error": "unknown agent"}, status=400)
    result = await asyncio.to_thread(agent_updates.run_update, agent)
    if result["updated"]:
        live = set(await asyncio.to_thread(sessions.list_tmux_sessions))
        live.discard(config.MONITOR_SESSION)           # htop 팝업은 다시 켤 에이전트 세션이 아니다
        result["activeSessions"] = len(live)
    _audit(f"agent-update user={request.headers.get('Tailscale-User-Login')} agent={agent} "
           f"success={result['success']} updated={result['updated']} "
           f"before={result['beforeVersion']!r} after={result['afterVersion']!r}")
    return web.json_response(result)


async def api_usage(request):
    """클로드 사용량(레이트리밋) — cswap 캐시 파일 읽기 전용(명령 실행 없음), 5분 캐시. 없으면 빈 값."""
    return web.json_response({"usage": await asyncio.to_thread(settings.usage)})


# 웹 하단 공통 HUD(여러 칸일 때) — 칸마다 찍히던 계정 사용량 줄을 한 세트만. bridge.json 의 hudScript
# (우리는 ~/.claude/scripts/statusline.py)를 --accounts 로 돌려 클로드 안의 HUD 와 글자 하나까지 같은 내용을
# 낸다(ANSI 색 그대로 — 웹이 칠한다). 10초 캐시. 설정이 없거나(공개본 기본) 실패하면 빈 목록 = HUD 숨김.
# 홈의 스크립트를 브릿지가 실행하는 일이라 기본은 꺼짐이다 — 켠 사람만(S8, 2026-10-11).
HUD_TTL = 10.0
_hud_cache: dict = {"at": 0.0, "lines": []}


def _hud_lines() -> list[str]:
    if time.monotonic() - _hud_cache["at"] < HUD_TTL:
        return _hud_cache["lines"]
    lines: list[str] = []
    if config.HUD_SCRIPT is not None and config.HUD_SCRIPT.is_file():
        try:
            out = subprocess.run(["/usr/bin/python3", str(config.HUD_SCRIPT), "--accounts"],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
                                 env={k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")})
            if out.returncode == 0 and out.stdout.strip() != "statusline error":
                lines = [l for l in out.stdout.splitlines() if l.strip()][:8]
        except (OSError, subprocess.SubprocessError):
            pass
    _hud_cache.update(at=time.monotonic(), lines=lines)
    return lines


async def api_hud(request):
    return web.json_response({"lines": await asyncio.to_thread(_hud_lines)})


async def api_decision_answer(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad json"}, status=400)
    if not isinstance(body, dict):        # 문법은 맞지만 배열·문자열·숫자·null → .get()이 500을 낸다
        return web.json_response({"error": "bad body"}, status=400)
    project = str(body.get("project", ""))
    type_, value = str(body.get("type", "")), str(body.get("value", ""))
    try:
        # capture-pane + send-keys — 블로킹 서브프로세스라 루프 밖에서 돌린다
        await asyncio.to_thread(decisions.send_answer, project, type_, value)
    except LookupError:
        return web.json_response({"error": "session not alive"}, status=404)
    except decisions.NoPicker:                       # 화면이 이미 넘어갔다 — 카드도 지우지 않는다
        return web.json_response({"error": "picker gone"}, status=409)
    except ValueError:
        return web.json_response({"error": "bad answer"}, status=400)
    decisions.clear_card(body.get("sessionId"))
    request.app[OVERVIEW].invalidate()                # 카드가 곧바로 사라져 보이게
    _audit(f"decision user={request.headers.get('Tailscale-User-Login')} "   # project 도 !r —
           f"project={project!r} type={type_} value={value[:80]!r}")         # 개행으로 가짜 줄 위조 방지
    return web.json_response({"ok": True})


# '맥에서 열기' — macOS open 의 인자 화이트리스트. 임의 앱 이름을 받지 않는다(명령 주입 금지).
# 기본(default)은 그 파일의 기본 앱: 이미지·PDF→미리보기, HTML→기본 브라우저.
OPEN_APPS = {"default": [], "preview": ["-a", "Preview"],
             "chrome": ["-a", "Google Chrome"], "finder": ["-R"]}


async def api_open(request):
    """파일 패널 우클릭 → 그 파일을 '맥에서' 연다(PDF·HTML·큰 이미지·SVG·HEIC에도 쓰는 길).

    경로 검증은 읽기 API 와 같은 한 겹(files.safe_path 계열) — 프로젝트 밖·비밀파일은 404.
    GUI 는 맥 화면에 뜬다(폰에서 눌러도 해가 없고, 요구 범위도 데스크톱 전용이다).
    """
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    project, path = str(body.get("project", "")), str(body.get("path", ""))
    raw_abs = str(body.get("abs", ""))       # 터미널에 찍힌 절대경로(스크래치패드 이미지 등)
    args = OPEN_APPS.get(str(body.get("app", "default")))
    if args is None:
        return web.json_response({"error": "unknown app"}, status=400)
    if raw_abs and not config.OPEN_ANYWHERE:
        # 절대경로 어디든 열기는 bridge.json openAnywhere 로 켠 사람만(S3). 기본은 프로젝트 안 상대경로만.
        return web.json_response({"error": "NotAllowed"}, status=404)
    try:
        target = await asyncio.to_thread(files.resolve_openable, raw_abs) if raw_abs \
            else await asyncio.to_thread(files.resolve_existing, project, path)
    except files.NotAllowed:
        return web.json_response({"error": "NotAllowed"}, status=404)
    try:
        out = await asyncio.to_thread(
            lambda: subprocess.run(["open", *args, str(target)], capture_output=True, timeout=15))
    except (OSError, subprocess.SubprocessError):
        return web.json_response({"error": "open failed"}, status=500)
    if out.returncode != 0:
        return web.json_response({"error": "open failed"}, status=500)
    _audit(f"open user={request.headers.get('Tailscale-User-Login')} "     # 실제로 연 절대경로까지
           f"project={project!r} path={path!r} target={str(target)!r} "    # 남긴다(폴더 제한이 없으므로)
           f"app={body.get('app', 'default')!r}")
    return web.json_response({"ok": True})


async def api_mkdir(request):
    """파일 패널의 새 폴더. 검증은 읽기와 같은 잣대 — 밖·비밀이름은 404 로 뭉갠다."""
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    project, path = str(body.get("project", "")), str(body.get("path", ""))
    if await asyncio.to_thread(remote_files.remote_of, project):      # 다른 PC 폴더는 읽기 전용
        return web.json_response({"error": "ReadOnly", "reason": "다른 PC 에는 여기서 폴더를 만들 수 없습니다"}, status=405)
    try:
        await asyncio.to_thread(files.make_dir, project, path)
    except files.NotAllowed:
        return web.json_response({"error": "NotAllowed"}, status=404)
    except FileExistsError:
        return web.json_response({"error": "exists"}, status=409)
    except FileNotFoundError:                  # 부모 폴더가 그 사이 사라졌다
        return web.json_response({"error": "no parent"}, status=404)
    except OSError:
        return web.json_response({"error": "mkdir failed"}, status=500)
    _audit(f"mkdir user={request.headers.get('Tailscale-User-Login')} "
           f"project={project!r} path={path!r}")
    return web.json_response({"ok": True})


_FILE_ERRORS = {files.NotAllowed: 404, files.TooLarge: 413,
                files.IsBinary: 415, files.UnsupportedImage: 415, files.Conflict: 409}


def _file_error(exc):
    return web.json_response({"error": type(exc).__name__}, status=_FILE_ERRORS[type(exc)])


# 다른 PC 터미널(ssh 세션 + 원격 폴더)의 파일 패널은 그 PC 에 물어본다 — 읽기 전용.
# 판정(remote_of)은 tmux 한 번이라 싸고, ssh 왕복은 스레드로 보내 이벤트 루프(터미널 소켓)를 안 막는다.
# 로컬 프로젝트 폴더가 실재하면 remote_of 가 None 이라 지금 경로 그대로다.
def _remote_unavailable(host) -> web.Response:
    return web.json_response({"error": "RemoteUnavailable",
                              "reason": f"원격 PC({host})에 연결하지 못했습니다 — 켜져 있고 테일넷에 붙어 있는지 보세요"},
                             status=502)


async def api_files(request):
    project, rel = request.query.get("project", ""), request.query.get("path", "")
    try:
        remote = await asyncio.to_thread(remote_files.remote_of, project)
        if remote:
            entries = await asyncio.to_thread(remote_files.list_dir, remote, rel)
        else:
            entries = files.list_dir(project, rel)
    except remote_files.RemoteUnavailable as e:
        return _remote_unavailable(e)
    except tuple(_FILE_ERRORS) as e:
        return _file_error(e)
    return web.json_response({"entries": entries})


async def api_file_get(request):
    project, rel = request.query.get("project", ""), request.query.get("path", "")
    try:
        remote = await asyncio.to_thread(remote_files.remote_of, project)
        if remote:
            got = await asyncio.to_thread(remote_files.read_text, remote, rel)
        else:
            got = files.read_text(project, rel)
    except remote_files.RemoteUnavailable as e:
        return _remote_unavailable(e)
    except tuple(_FILE_ERRORS) as e:
        return _file_error(e)
    return web.json_response(got)


async def api_file_image_get(request):
    """파일 패널 이미지 미리보기 — 텍스트 JSON·PUT 계약과 섞지 않는 읽기 전용 통로."""
    try:
        got = await asyncio.to_thread(
            files.read_image, request.query.get("project", ""), request.query.get("path", ""))
    except files.TooLarge:
        mb = config.MAX_IMAGE_FILE_BYTES // (1024 * 1024)
        return web.json_response(
            {"error": "TooLarge", "reason": f"이미지가 너무 커서 열 수 없습니다 ({mb}MB 넘음)"},
            status=413)
    except files.UnsupportedImage as e:
        return web.json_response({"error": "UnsupportedImage", "reason": str(e)}, status=415)
    except files.NotAllowed as e:
        return _file_error(e)
    # nosniff는 security_headers_middleware가 모든 응답에 붙인다. inline을 명시해 내려받기 대신
    # 패널 안 표시임을 고정하고, Content-Type은 매직 바이트로 확정한 값만 쓴다.
    return web.Response(body=got["content"], content_type=got["contentType"],
                        headers={"Content-Disposition": "inline"})


async def api_file_put(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad json"}, status=400)
    if not isinstance(body, dict):        # 문법은 맞지만 배열·문자열·숫자·null → .get()이 500을 낸다
        return web.json_response({"error": "bad body"}, status=400)
    base = body.get("baseMtimeNs")
    if not isinstance(base, str):         # 숫자로 온 토큰은 JS 왕복에서 이미 뭉개졌다(files.version_of 참고).
        return web.json_response({"error": "baseMtimeNs must be string"}, status=400)
    project, path = str(body.get("project", "")), str(body.get("path", ""))
    content = str(body.get("content", ""))
    if await asyncio.to_thread(remote_files.remote_of, project):      # 다른 PC 폴더는 읽기 전용
        return web.json_response({"error": "ReadOnly", "reason": "다른 PC 의 파일은 여기서 저장할 수 없습니다 — 터미널 안에서 고치세요"}, status=405)
    try:
        mtime = files.write_text(project, path, content, base)
    except (TypeError, ValueError):
        return web.json_response({"error": "bad body"}, status=400)
    except tuple(_FILE_ERRORS) as e:
        return _file_error(e)
    _audit(f"filewrite user={request.headers.get('Tailscale-User-Login')} "   # 개행 든 파일명으로 가짜
           f"project={project!r} path={path!r} "                             # 줄을 위조하지 못하게 !r
           f"bytes={len(content.encode())}")                                 # 글자수 아닌 실제 바이트
    return web.json_response({"ok": True, "mtimeNs": mtime})


async def api_upload(request):
    project = request.query.get("project", "")
    if not sessions.is_valid_session(project):
        return web.json_response({"error": "unknown project"}, status=400)
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file":
        return web.json_response({"error": "no file"}, status=400)
    from datetime import datetime
    now = datetime.now(config.KST)
    sub = uploads.sanitize_subdir(request.query.get("dir", ""))   # 폴더 드롭 — 하위 구조 유지
    upload_root = config.upload_root()
    dest = uploads.build_upload_path(project, part.filename or "file",
                                     root=upload_root, now=now, subdir=sub)
    size = 0
    with open(dest, "wb") as f:
        while chunk := await part.read_chunk(64 * 1024):
            size += len(chunk)
            if size > config.MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                return web.json_response({"error": "too large"}, status=413)
            f.write(chunk)
    _audit(f"upload user={request.headers.get('Tailscale-User-Login')} project={project} path={dest} bytes={size}")
    resp = {"path": str(dest)}
    if sub:                                   # 웹이 '폴더 하나당 경로 한 번'을 찍을 수 있게 꼭대기를 준다
        resp["top"] = str(upload_root / project / now.strftime("%Y-%m-%d") / sub.split("/")[0])
    return web.json_response(resp)


async def api_clipboard(request):
    """터미널에서 드래그해 고른 글자(tmux 복사 버퍼) — 브라우저가 자기 클립보드에 넣으려고 읽는다.

    읽기 전용이고, 이미 터미널 전체를 볼 수 있는 화면에만 열리므로 새로 드러나는 것은 없다.
    """
    text, age = await asyncio.to_thread(sessions.copy_buffer)
    return web.json_response({"text": text, "age": age})


async def api_project_new(request):
    """새 프로젝트 폴더를 만든다(사이드바 [+ 새 프로젝트]) — <프로젝트 루트>/<이름> 하나뿐.

    이름 검사는 다른 API 와 같은 관문(is_valid_name)이라 경로조작·숨김·이상문자가 걸린다.
    이미 있으면 409 — 웹은 그때 '이미 있는 프로젝트'라고 알리고 그냥 그 세션을 연다.
    """
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    name = str(body.get("name", "")).strip()
    if not sessions.is_valid_name(name) or name in config.IGNORED_FOLDERS:
        return web.json_response({"error": "bad name"}, status=400)
    target = config.projects_root() / name
    if target.exists():
        return web.json_response({"error": "exists"}, status=409)
    try:
        target.mkdir(parents=False)          # parents=False — 루트가 없으면 만들지 않는다
    except OSError:
        return web.json_response({"error": "mkdir failed"}, status=500)
    # 빈 폴더는 프로젝트 판정(.git·마커)에 걸려 다음 새로고침에 사라진다 — 명단에 올려 붙잡는다.
    # 명단 쓰기가 실패해도 폴더는 이미 생겼으니 200 이다(다음 attach 가 다시 올릴 길은 없지만
    # 사용자가 파일 하나만 넣어도 살아나고, 감사로그에 남아 추적된다).
    try:
        sessions.mark_created(name)
    except OSError:
        _audit(f"newproject-list-failed name={name!r}")
    _audit(f"newproject user={request.headers.get('Tailscale-User-Login')} name={name!r}")
    return web.json_response({"ok": True, "name": name, "path": str(target)})


async def api_uploads_usage(request):
    """붙여넣기 사본이 얼마나 쌓였나 — 설정 화면이 보여 주는 수치."""
    return web.json_response(await asyncio.to_thread(uploads.usage))


async def api_uploads_clear(request):
    """캐시 비우기(설정 화면 버튼) — 날짜 폴더만 지우고 감사로그는 남긴다."""
    got = await asyncio.to_thread(uploads.clear)
    _audit(f"cacheclear user={request.headers.get('Tailscale-User-Login')} "
           f"bytes={got['bytes']} files={got['files']}")
    return web.json_response(got)


async def api_capture(request):
    """붙여넣은 이미지와 같은 크기의 '방금 찍은 캡처 파일' 경로 — 사본 업로드를 없애는 길.

    못 찾으면 빈 응답. 웹은 그때만 업로드로 물러선다.
    """
    try:
        w, h = int(request.query.get("w", 0)), int(request.query.get("h", 0))
    except ValueError:
        return web.json_response({"error": "bad size"}, status=400)
    if w <= 0 or h <= 0 or w > 30000 or h > 30000:
        return web.json_response({"error": "bad size"}, status=400)
    path = await asyncio.to_thread(locate.recent_capture, w, h)
    return web.json_response({"path": path} if path else {})


async def api_locate(request):
    """드래그된 파일·폴더의 원본 경로 찾기(bridge/locate.py) — 딱 하나로 특정될 때만 경로를 준다.

    못 찾거나 애매하면 웹이 사본 업로드로 물러선다. 검색은 읽기 전용(mdfind + stat)이다.
    """
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    name = str(body.get("name", ""))
    if not name or "/" in name or name.startswith(".") or len(name) > 255:
        return web.json_response({"error": "bad name"}, status=400)
    if body.get("kind") == "dir":
        children = body.get("children")
        hits = await asyncio.to_thread(locate.find_dir, name,
                                       children if isinstance(children, list) else [])
    else:
        try:
            size, mtime = int(body.get("size", -1)), int(body.get("mtimeMs", -1))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad body"}, status=400)
        if size < 0 or mtime < 0:
            return web.json_response({"error": "bad body"}, status=400)
        hits = await asyncio.to_thread(locate.find_file, name, size, mtime)
    if len(hits) == 1:
        return web.json_response({"path": hits[0]})
    return web.json_response({"matches": len(hits)})


def _clamp(value: int, lo: int = 1, hi: int = 500) -> int:
    """터미널 크기 상·하한 — struct.pack("H") 범위 밖 값이 들어와 터지는 것을 막는다."""
    return max(lo, min(hi, value))


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def ws_terminal(request):
    name = request.match_info["name"]
    if not sessions.is_valid_session(name):
        return web.json_response({"error": "unknown session"}, status=404)
    # 설정한 시작 에이전트 — 세션을 새로 만들 때만 tmux 가 실행한다(붙기만 하면 무시).
    # ?agent= 로 이번 접속에만 다른 에이전트를 고를 수 있다(사이드바의 '코덱스로 켜기'). 설정은
    # 안 바뀐다. 고정 목록(settings.AGENTS) 밖의 값은 무시하고 설정값으로 간다 — 셸에 들어가는 값이다.
    agent = (await asyncio.to_thread(settings.load))["agent"]
    if (picked := request.query.get("agent")) in settings.AGENTS:
        agent = picked

    killed = request.app[KILLED]
    if name in killed:
        reconnect = request.query.get("re") == "1"
        if reconnect and name not in await asyncio.to_thread(sessions.list_tmux_sessions):
            ws = web.WebSocketResponse()           # 끈 세션에 저절로 붙으려는 것 — 켜지 않고 닫는다
            await ws.prepare(request)
            await ws.close(code=WS_KILLED, message=b"killed")
            return ws
        killed.discard(name)                       # 직접 연 것(또는 이미 다시 살아 있다) — 기억을 지운다

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    _audit(f"attach user={request.headers.get('Tailscale-User-Login')} session={name} agent={agent}")

    # 처음부터 실제 화면 크기로 연다. 80×24로 열고 나중에 키우면 tmux 가 그 사이 화면을 한 번
    # 그리고 리사이즈 때 통째로 다시 그린다 — 그 잔상이 xterm 스크롤백에 쌓여 클로드 배너가
    # 여러 번 찍힌 화면이 됐다(스크린샷 실측). 값이 없거나 이상하면 80×24.
    try:
        cols = _clamp(int(request.query.get("cols", 80)))
        rows = _clamp(int(request.query.get("rows", 24)))
    except ValueError:
        cols, rows = 80, 24
    master, slave = pty.openpty()
    _set_winsize(master, cols, rows)
    proc = await asyncio.create_subprocess_exec(
        *sessions.attach_cmd(name, agent=agent),
        stdin=slave, stdout=slave, stderr=slave,
        env={**os.environ, "TERM": "xterm-256color", "LANG": "ko_KR.UTF-8"},
        preexec_fn=os.setsid,
    )
    os.close(slave)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)   # 상한 = 역압 장치
    reading = eof = False

    def pause_reading():
        nonlocal reading
        if reading:
            loop.remove_reader(master)
            reading = False

    def resume_reading():
        nonlocal reading
        if not reading and not eof:
            loop.add_reader(master, on_readable)
            reading = True

    def on_readable():
        nonlocal eof
        if queue.full():                             # 소비가 밀리면 읽기를 멈춘다
            pause_reading()                          # — 남은 글자는 pty 버퍼가 들고 있는다
            return
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        if not data:                                 # pty 닫힘 — 콜백 재발화부터 끊는다
            eof = True                               # (안 끊으면 EOF fd가 계속 readable이라 폭주)
            pause_reading()
        queue.put_nowait(data or None)               # None = 종료 신호

    resume_reading()

    async def pump_out():
        while (data := await queue.get()) is not None:
            resume_reading()                         # 큐가 빠졌으니 읽기 재개
            await ws.send_bytes(data)
        await ws.close()

    out_task = asyncio.create_task(pump_out())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                os.write(master, msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    ctrl = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if ctrl.get("type") == "ping":
                    # 브라우저의 생존 확인 — 답은 빈 바이트 하나를 출력 큐로. 큐를 타야 pump_out 과
                    # 같은 순서·같은 소켓 쓰기 경로라 동시 send 가 없다. 웹은 '무슨 메시지든 왔다'
                    # 를 생존으로 본다(반쯤 죽은 소켓은 여기 답이 못 온다 → 웹이 스스로 다시 붙는다).
                    await queue.put(b"")
                elif ctrl.get("type") == "compact":
                    # 웹이 칸 수가 바뀔 때·붙을 때 알린다 — 여러 칸이면 HUD 를 첫 줄만(statusline.py)
                    await _set_compact(request.app, name, ws, bool(ctrl.get("on")))
                elif ctrl.get("type") == "unmode":
                    # 스크롤 뒤 첫 키 앞에 온다 — 여기서 기다렸다 다음 BINARY 를 쓰므로 순서가 지켜진다
                    await asyncio.to_thread(sessions.leave_copy_mode, name)
                elif ctrl.get("type") == "resize":
                    try:                             # 망가진 resize 하나로 연결이 끊기면 안 된다
                        cols = _clamp(int(ctrl["cols"]))
                        rows = _clamp(int(ctrl["rows"]))
                        _set_winsize(master, cols, rows)
                        if proc.returncode is None:  # tmux가 즉시 다시 그리도록 (자식 생존 시에만)
                            os.killpg(proc.pid, signal.SIGWINCH)
                    except (KeyError, ValueError, TypeError, struct.error, ProcessLookupError):
                        continue
    finally:
        eof = True                                    # 정리 시작 — reader 재등록 금지
        pause_reading()
        out_task.cancel()
        os.close(master)
        _audit(f"detach session={name}")              # 대기 중 취소돼도 남도록 await 앞에서 기록
        await _set_compact(request.app, name, ws, False)   # 이 연결이 떠난다 — 남은 칸이 없으면 표시를 뗀다
        if proc.returncode is None:
            proc.terminate()                          # attach 클라이언트만 종료 — 세션은 tmux에 남는다
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
    return ws


def create_app() -> web.Application:
    security.ensure_token()                       # ~/.frogterminal/token — 없으면 지금 만든다
    app = web.Application(middlewares=[security_headers_middleware, auth_middleware])
    app[LIMITER] = security.LoginLimiter()
    app[OVERVIEW] = OverviewCache()
    app[KILLED] = set()
    app[COMPACT] = {}
    for route in STATIC_FILES:
        app.router.add_get(route, static_file)
    app.router.add_get("/api/sessions", api_sessions)
    app.router.add_post("/api/sessions/kill", api_session_kill)
    app.router.add_post("/api/sessions/kill-all", api_sessions_kill_all)   # 헤더 [전체 끄기]
    app.router.add_post("/api/sessions/restore", api_sessions_restore)     # 헤더 [복원]
    app.router.add_get("/api/hidden", api_hidden_get)
    app.router.add_post("/api/hidden", api_hidden_set)
    app.router.add_get("/api/favorites", api_favorites_get)
    app.router.add_post("/api/favorites", api_favorites_set)
    app.router.add_get("/api/summary", api_summary)
    app.router.add_get("/api/settings", api_settings_get)
    app.router.add_post("/api/settings", api_settings_post)
    app.router.add_get("/api/agents", api_agents)
    app.router.add_post("/api/agents/{agent}/update", api_agent_update)
    app.router.add_get("/api/usage", api_usage)
    app.router.add_get("/api/hud", api_hud)        # 여러 칸일 때 웹 하단 공통 HUD(계정 줄)
    app.router.add_get("/api/decisions", api_decisions)
    app.router.add_get("/api/office", api_office)          # 3D 모션 모드(사무실 화면)
    app.router.add_post("/api/decisions/answer", api_decision_answer)
    app.router.add_get("/api/files", api_files)
    app.router.add_post("/api/open", api_open)
    app.router.add_post("/api/mkdir", api_mkdir)
    app.router.add_get("/api/file", api_file_get)
    app.router.add_get("/api/file/image", api_file_image_get)
    app.router.add_put("/api/file", api_file_put)
    app.router.add_post("/api/upload", api_upload)   # 같은 출처라 CORS 사전요청(OPTIONS)이 없다
    app.router.add_post("/api/locate", api_locate)   # 드래그 원본 찾기 — 사본 업로드 대신 진짜 경로
    app.router.add_get("/api/clipboard", api_clipboard)   # tmux 복사 버퍼 → 브라우저 클립보드
    app.router.add_get("/api/capture", api_capture)       # 캡처 원본 경로(사본 업로드 회피)
    app.router.add_post("/api/projects", api_project_new)        # 새 프로젝트 폴더
    app.router.add_get("/api/uploads", api_uploads_usage)        # 캐시 용량
    app.router.add_post("/api/uploads/clear", api_uploads_clear)  # 캐시 비우기
    app.router.add_get("/ws/{name}", ws_terminal)
    preview_api.add_routes(app)                   # 브라우저 패널 1단계 — /api/preview/* (별도 모듈)
    browser_api.add_routes(app)                   # 브라우저 패널 2단계 — /api/browser/*, /ws/browser/* (CDP 크롬)
    pad_api.add_routes(app)                       # 게임패드 칸 이동 — /api/pad, /api/pad/wait (패드 상주 프로그램)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    security.assert_safe_to_start()                   # Funnel 게이트 — 확인 안 되면 안 뜬다
    sessions.clear_compact_all()                  # 지난 실행의 '여러 칸' 표시 — 보던 칸들이 다시 붙으며 새로 단다
    web.run_app(create_app(), host=config.BIND_HOST, port=config.PORT,
                access_log=None)                  # 접근 로그 끔 — HUD 폴링이 err.log 를 하루 11MB씩 키움(2026-10-19)


if __name__ == "__main__":
    main()
