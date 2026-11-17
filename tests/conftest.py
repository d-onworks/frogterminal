"""브라우저 회귀 테스트용 가짜 브릿지 — 진짜 web/ 파일을 https 로 내주고 WS 수신 바이트를 기록한다.

실물과 같은 조건을 만드는 것이 목적이라 타협하지 않은 부분:
  * https + wss — app.js 가 wss 를 고정으로 쓰므로 자체서명 인증서로 TLS 를 실제로 켠다.
  * 에코 — 진짜 셸처럼 받은 글자를 돌려줘야 xterm 의 커서·textarea 상태가 실기와 같아진다.
  * 마우스 리포트 on — tmux 가 항상 켜 두는 모드. 터치 스크롤 경로가 여기에 걸려 있다.
"""
import asyncio
import base64
import json
import re
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from bridge import pad_api

WEB_DIR = Path(__file__).parent.parent / "web"
STATIC = {
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
    "/pad.js": ("pad.js", "text/javascript"),
    "/office-world.js": ("office-world.js", "text/javascript"),
    "/office-atlas.js": ("office-atlas.js", "text/javascript"),
    "/office-life.js": ("office-life.js", "text/javascript"),
    "/office-view.js": ("office-view.js", "text/javascript"),
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
}

# 3D 모션 모드(사무실) — 진짜 브릿지 /api/office(bridge/office.py)와 같은 모양. 켜진 프로젝트만 온다.
# wiki = 결재 대기(묻는 말이 20자를 넘는다 — 화면이 20자 + … 로 자르는지), mall = 일하는 중 + 서브에이전트
# 셋(꼬마들이 줄 선다), frog = 쉬는 중, blog = 커밋 적은 저장소(꼬마 캐릭터).
def office_people() -> list[dict]:
    return [
        {"name": "wiki", "agent": "claude", "status": "decision", "pending": 1,
         "ask": "이 파일을 지울까요? 낡은 초안 파일 정리 중입니다", "commits": 120, "kid": False,
         "subs": [], "subCount": 0, "path": "/tmp/wiki", "host": None},
        {"name": "mall", "agent": "codex", "status": "working", "pending": 0, "ask": "", "commits": 300,
         "kid": False, "subs": [{"id": f"m{i}", "type": "Explore", "desc": f"조사 {i}", "doing": "🔍 'seat' 찾는 중"}
                                for i in range(3)],
         "subCount": 3, "path": "/tmp/mall", "host": None, "doing": "📖 office-view.js 읽는 중",
         "log": [{"at": "2026-10-22T01:40:00Z", "text": "📖 app.js 읽는 중"},
                 {"at": "2026-10-22T01:40:05Z", "text": "📖 office-view.js 읽는 중"}]},
        {"name": "frog", "agent": "claude", "status": "done", "pending": 0, "ask": "", "commits": 210,
         "kid": False, "subs": [], "subCount": 0, "path": "/tmp/frog", "host": None},
        {"name": "blog", "agent": "claude", "status": "done", "pending": 0, "ask": "", "commits": 8,
         "kid": True, "subs": [], "subCount": 0, "path": "/tmp/blog", "host": None},
    ]

# 일부러 뒤섞어 둔다 — 정렬(결재>진행중>완료>그 외)은 화면이 하는 일이라는 것을 테스트가 증명해야 한다.
# 이름은 '가나다순과 상태순이 정반대'가 되게 골랐다(이름순: apex<blog<frog<mall<wiki,
# 상태순: wiki>mall>blog·frog>apex). 예전 이름(ask/busy/cook/demo/zzz)은 알파벳순이 상태순과
# 우연히 같아, 정렬에서 상태 항을 통째로 지워도 테스트가 통과했다 — 정렬 회귀가 무방비였다.
# 완료(done)가 둘인 것도 일부러다: 같은 상태 안에서는 이름순이라는 두 번째 규칙까지 걸어 둔다.
SESSIONS = [
    {"name": "frog", "alive": True,  "path": "/tmp/frog", "status": "done",     "pending": 0},
    {"name": "apex", "alive": False, "path": None,        "status": "off",      "pending": 0},
    {"name": "wiki", "alive": True,  "path": "/tmp/wiki", "status": "decision", "pending": 1},
    {"name": "blog", "alive": True,  "path": "/tmp/blog", "status": "done",     "pending": 0},
    {"name": "mall", "alive": True,  "path": "/tmp/mall", "status": "working",  "pending": 0,
     "tabs": [3]},                    # 병렬 탭 — 다른 기기에서 열어 둔 mall@3 이 있는 상황
]
# 진짜 브릿지의 DECISION_KINDS 와 같아야 한다(bridge/decisions.py) — idle 은 결재가 아니다.
DECISION_KINDS = {"permission", "picker"}
# live=False = 그 프로젝트의 tmux 세션이 죽었다(답을 보내면 404). 헤드라인 결재 수에서 빠진다.
# kind="done" = 훅의 Stop 이벤트가 쓴 '한 턴을 끝내고 다음 지시를 기다리는' 카드. 묻는 말이 없어
# message 가 항상 빈 문자열이다(hooks/frog_hook.py) — 사용자가 다음 지시를 넣는 자리라 가장 자주 쓴다.
# wiki 는 피커 카드와 완료 카드를 함께 갖는다(한 프로젝트에 클로드 세션이 둘이거나, 훅 카드 옆에
# 화면스캔 피커가 따로 붙는 실제 상황) — 둘이 서로의 잠금을 옮겨 받지 않는지 여기서 본다.
#
# ★ asked/said = 브릿지가 대화 기록에서 뽑아 주는 맥락(bridge/decisions.py, 각 400자 상한).
#   훅 원문(message)만 있는 카드로는 사용자가 아무 판단도 못 한다("Claude is waiting for your
#   input"). 그래서 화면은 message 대신 이 둘을 보여줘야 한다 — 아래 픽스처는 그 네 경우를
#   한 번에 깐다: ①피커(question 과 said 가 같은 말) ②기계 문구뿐인 결재(맥락이 진짜 내용)
#   ③완료(다음 지시 자리) ④맥락이 통째로 빈 카드(/clear 직후 — 화면이 이상해지면 안 된다).
DECISIONS = [
    {"sessionId": "wiki-1", "project": "wiki", "cwd": "/tmp/wiki", "machine": "mac",
     "kind": "picker", "message": "이 파일을 지울까요?", "updatedAt": "2026-08-05T12:00:00+09:00",
     "live": True, "question": "이 파일을 지울까요?",
     "asked": "낡은 초안 파일들 정리하고 커밋해",
     "said": "이 파일을 지울까요?",                    # question 과 같은 말 — 두 번 보이면 안 된다
     "options": [{"n": 1, "label": "네, 지웁니다"}, {"n": 2, "label": "아니요"}]},
    {"sessionId": "gone-1", "project": "gone", "cwd": "/tmp/gone", "machine": "mac",
     # 죽은 세션에 남은 진짜 결재 — 답을 보내면 404 라 화면이 잠가야 하는 그 카드다.
     "kind": "permission", "message": "Claude needs your permission to use Bash",  # 기계 문구
     "updatedAt": "2026-08-05T11:00:00+09:00", "live": False,
     "asked": "브릿지 재기동하고 로그 확인해줘",
     "said": "launchctl 로 재기동했는데 포트가 안 열립니다. 로그를 더 볼까요?"},
    {"sessionId": "wiki-2", "project": "wiki", "cwd": "/tmp/wiki", "machine": "mac",
     "kind": "done", "message": "", "updatedAt": "2026-08-05T13:00:00+09:00", "live": True,
     "asked": "테스트 돌리고 결과 알려줘", "said": "테스트 47개 전부 통과했습니다"},
    {"sessionId": "frog-1", "project": "frog", "cwd": "/tmp/frog", "machine": "mac",
     "kind": "done", "message": "", "updatedAt": "2026-08-05T13:20:00+09:00", "live": True,
     "asked": "", "said": ""},                        # 맥락 없는 카드 — 브릿지는 빈 문자열로 준다
]


# 가짜 프로젝트 파일 트리 — 폴더 펼치기·파일 열기·저장이 화면에서 실제로 왕복하는지 본다.
#
# ★ 프로젝트마다 다른 것을 내준다. 경로만 보고 답하는 가짜 브릿지는 화면이 'mall' 자리에 wiki 파일을
#   그려도 아무 차이를 못 내서, 그 사고를 테스트가 구조적으로 못 본다(v2 리뷰 열두 번이 놓친 이유다).
#   그래서 셋 다 프로젝트별로 가른다:
#     ① 루트의 '<프로젝트>.md' 표식 파일 — 목록만 봐도 어느 프로젝트 트리인지 드러난다
#     ② 본문 첫머리의 프로젝트 이름 — 열어 둔 파일이 어느 프로젝트 것인지 드러난다
#     ③ 버전 토큰 — PUT 본문만 봐도 어느 프로젝트에서 연 파일을 저장하려는 것인지 드러난다
FS_PATHS = ("", "docs", "docs/설계", "src")              # 트리 모양은 프로젝트 공통(펼칠 수 있는 폴더)
# ★ 층이 셋이다(루트 > docs > 설계). 한 층짜리 트리로는 '펼쳐도 부모가 남는가'·'깊이만큼 들여쓰는가'를
#   가릴 수 없어, 드릴다운(폴더 안으로 들어가 버리는 옛 방식)으로 되돌려도 테스트가 그대로 통과한다.
#   '잠긴폴더' 는 목록엔 있는데 열면 404 인 폴더다(권한·삭제) — FS_PATHS 에 일부러 넣지 않았다.
LONG_DOC = "아주긴이름의문서파일-2026-08-05-최종본.md"      # 깊은 자리의 긴 이름 — 들여쓰기가 폭을 먹는 자리
# (이름에 '설계'·'docs' 같은 다른 줄의 글자를 넣지 않는다 — :has-text 가 두 줄을 함께 잡으면
#  테스트가 엉뚱한 줄을 누르고도 통과한다.)
# 프로젝트별 버전 토큰 — (README.md, docs/노트.md, <프로젝트>.md, docs/설계/<긴 이름>, src/app.js) 순서.
# 전부 19자리다: 실물 st_mtime_ns 와 같은 길이라, 화면이 어디선가 Number 로 바꾸면 하위 자릿수가
# 뭉개져(…456789 → …456800) 되돌아온 값이 달라지고 테스트가 곧바로 깨진다.
NS = {
    "wiki": ("1754377200111111117", "1754377200123456789", "1754377200155555557",
             "1754377200177777777", "1754377200199999997"),
    "mall": ("1754377200222222227", "1754377200987654321", "1754377200255555557",
             "1754377200277777777", "1754377200299999997"),
    "blog": ("1754377200333333337", "1754377200333456789", "1754377200355555557",
             "1754377200377777777", "1754377200399999997"),
    "frog": ("1754377200444444447", "1754377200443456789", "1754377200455555557",
             "1754377200477777777", "1754377200499999997"),
    "apex": ("1754377200555555557", "1754377200553456789", "1754377200555555558",
             "1754377200577777777", "1754377200599999997"),
    "gone": ("1754377200666666667", "1754377200663456789", "1754377200665555557",
             "1754377200677777777", "1754377200699999997"),
}


def fs_of(project: str) -> dict[str, list[dict]]:
    """그 프로젝트의 폴더 트리 — 루트에 '<프로젝트>.md' 표식 파일이 하나씩 더 있다.

    순서는 진짜 브릿지와 같게 둔다: 폴더 먼저, 그 안에서 이름순(bridge/files.py list_dir).
    화면은 다시 거르거나 정렬하지 않으므로 여기 순서가 곧 트리 줄 순서다.
    """
    return {"": [{"name": "docs", "dir": True, "size": None},
                 {"name": "src", "dir": True, "size": None},
                 {"name": "잠긴폴더", "dir": True, "size": None},
                 {"name": "README.md", "dir": False, "size": 10},
                 {"name": f"{project}.md", "dir": False, "size": 8}],
            "docs": [{"name": "설계", "dir": True, "size": None},
                     {"name": "노트.md", "dir": False, "size": 6}],
            "docs/설계": [{"name": LONG_DOC, "dir": False, "size": 4}],
            "src": [{"name": "app.js", "dir": False, "size": 5}]}


def files_of(project: str) -> dict[str, tuple[str, str]]:
    """그 프로젝트의 파일 → (본문, 버전 토큰). 같은 경로라도 프로젝트마다 값이 다르다."""
    readme, note, mark, draft, app = NS[project]
    return {"README.md": (f"# {project} 데모\n", readme),
            "docs/노트.md": (f"{project} 메모", note),
            f"{project}.md": (f"{project} 전용 파일", mark),
            f"docs/설계/{LONG_DOC}": (f"{project} 설계 초안", draft),
            "src/app.js": (f"// {project} 앱", app)}


class FakeBridge:
    """테스트가 들여다보는 것: received(받은 바이트 전부)·ws_open(열려 있는 소켓 수)·ws_log(접속 순서)
    ·ws_text(터미널 크기 등 TEXT 제어 메시지 — 분할선을 끌면 여기 resize 가 쌓인다)
    ·answers(결재 답으로 POST 된 본문 전부 — 같은 답이 두 번 나가는지도 여기서 센다)
    ·writes(파일 저장으로 PUT 된 본문 전부 — 버전 토큰이 문자열 그대로 돌아왔는지 여기서 본다)
    ·kills(끄기로 지목된 세션 이름)·hides(숨김 POST 본문)·hidden(지금 숨겨져 있는 이름들)
    ·favs_posts(즐겨찾기 POST 본문 전부)·favs(지금 즐겨찾기된 이름들 — 누른 순서 그대로)
    ·agent(지금 설정된 시작 에이전트)·agent_posts(설정 POST 본문 전부).
    돌리는 손잡이: slow_files(프로젝트, 초) — 그 프로젝트의 파일 응답만 늦춘다.
                   picker_gone() — 결재 답을 409 로 / kill_gone() — 끄기를 404 로 되돌린다."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.received = bytearray()
        self.ws_open = 0
        self.ws_log: list[str] = []
        self.ws_agents: list[str | None] = []          # 접속마다 ?agent= (사이드바 '○○로 켜기' 가 넘긴 값)
        self.killed: set[str] = set()                   # 진짜 브릿지의 KILLED — 끈 세션에 저절로 붙기를 4410 으로 닫는다
        self.by_session: dict[str, bytearray] = {}      # 세션별 받은 바이트 — 멀티 칸에서 입력이 제 칸으로만 가는지
        self.ws_text: list[str] = []
        self.answers: list[dict] = []
        self.writes: list[dict] = []
        self.kills: list[str] = []
        self.kill_alls = 0                              # 헤더 [전체 끄기] 를 부른 횟수
        self.restores = 0                               # 헤더 [복원] 을 부른 횟수
        self.restorable = 0                             # 복원 명단 크기 — [복원] 버튼을 켜는 근거
        self.hides: list[dict] = []
        self.hidden: set[str] = set()                   # 진짜 브릿지의 hidden.json 자리
        self.favs_posts: list[dict] = []                # 즐겨찾기 POST 본문 전부
        self.favs: list[str] = []                       # 진짜 브릿지의 favorites.json 자리
                                                        # (set 이 아니라 list — 누른 순서가 계약이다)
        self.file_delay = 0.0                           # 파일 응답 지연(초) — 0 이면 지연 없음
        self.slow_project: str | None = None            # 그 프로젝트만 늦춘다 (None = 전부)
        self.answer_status = 0                          # 0 이 아니면 결재 답을 그 코드로 되돌린다
        self.kill_status = 0                            # 0 이 아니면 끄기를 그 코드로 되돌린다
        self.opens: list[dict] = []                     # '맥에서 열기' POST 본문 전부
        self.uploads: list[dict] = []                   # 붙여넣기 업로드(project·파일명) 전부
        self.web_version = 1                            # 프론트 버전 — 올리면 새 배포 배너가 떠야 한다
        self.copy_buffer = ""                           # tmux 복사 버퍼(드래그로 고른 글자)
        self.copy_age: float | None = None              # 그 버퍼가 만들어진 지 몇 초(None = 응답에 안 싣는다)
        self.new_projects: list[str] = []                # 새 프로젝트 POST 로 온 이름들
        self.cache = {"bytes": 3 * 1024 * 1024, "files": 7}   # 붙여넣기 캐시 현황(설정 화면)
        self.office = office_people()                   # 사무실 화면 데이터 — 테스트가 바꿔 끼운다
        self.office_calls = 0                           # /api/office 를 부른 횟수(꺼져 있으면 안 불러야 한다)
        self.cache_cleared = 0                          # 캐시 비우기 요청 횟수
        self.captures: list[dict] = []                  # 캡처 원본 조회(가로·세로) 전부
        self.capture_path = ""                          # 있으면 '캡처 원본을 찾았다'는 응답
        self.locates: list[dict] = []                   # 원본 찾기 POST 본문 전부
        self.locate_map: dict[str, str] = {}            # 이름 → 원본 경로 (있으면 '특정 성공' 응답)
        self.mkdirs: list[dict] = []                    # '새 폴더' POST 본문 전부
        self.agent = "claude"                           # 진짜 브릿지의 settings.json 자리
        self.projects_root = "/tmp/projects"             # 설정 화면의 단일 프로젝트 루트
        # bypass·cont 기본 켬, rc 기본 꺼짐 — 진짜 브릿지(bridge/settings.py)와 동일
        self.fs_patch = {}                                # (project, path) → entries 덮어쓰기
        self.flags = {"bypass": True, "cont": True, "rc": False}
        self.commands = {"claude": "", "codex": ""}
        self.agent_posts: list[dict] = []
        self.agent_updates: list[str] = []              # 설정 화면 업데이트 버튼의 고정 agent 경로
        # 브라우저 패널 — 지금 맥에서 듣는 개발 서버(가짜). wiki 가 4321 하나, mall 은 둘, 나머지는 없음.
        self.services = [
            {"id": "11:4321", "port": 4321, "pid": 11, "cmd": "node", "cwd": "/tmp/wiki", "project": "wiki", "http": 200},
            {"id": "12:3000", "port": 3000, "pid": 12, "cmd": "next-server", "cwd": "/tmp/mall", "project": "mall", "http": 200},
            {"id": "13:5173", "port": 5173, "pid": 13, "cmd": "node", "cwd": "/tmp/mall", "project": "mall", "http": 200},
            {"id": "14:9090", "port": 9090, "pid": 14, "cmd": "python", "cwd": "/opt/x", "project": None, "http": 200},
            # blog 는 듣기만 하는 포트 하나뿐(예: pytest 가짜 서버) — 자동으로 열면 안 된다
            {"id": "15:57697", "port": 57697, "pid": 15, "cmd": "Python", "cwd": "/tmp/blog", "project": "blog", "http": None}]
        self.preview_opens: list[dict] = []             # /api/preview/open 본문 전부
        self.preview_renews: list[int] = []
        self.browser_cmds: list[tuple] = []             # 원격 브라우저 소켓으로 온 명령 (프로젝트, 명령)
        self.browser_running: set[str] = set()          # 지금 크롬이 떠 있는 프로젝트 — start=1 로만 늘어난다
        self.browser_stops: list[str] = []              # ⏻ 로 끈 프로젝트

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.port}/"

    @contextmanager
    def slow_files(self, project: str, seconds: float = 1.0):
        """그 프로젝트의 /api/files·/api/file 응답을 늦춘다 — '답이 늦게 오는 사이 화면을 바꾸는'
        경합을 결정적으로 재현하는 손잡이. 세션 공용 픽스처라 빠져나올 때 반드시 되돌린다."""
        self.slow_project, self.file_delay = project, seconds
        try:
            yield self
        finally:
            self.slow_project, self.file_delay = None, 0.0

    @contextmanager
    def picker_gone(self):
        """결재 답을 409(picker gone)로 되돌린다 — 보내려는 순간 그 세션 화면에 피커가 없어
        브릿지가 답을 막은 상황(server.py 의 decisions.NoPicker). 답은 나가지 않고 카드도
        지워지지 않는다. 그래서 answers 에 아무것도 쌓이지 않는 것까지가 계약이다."""
        self.answer_status = 409
        try:
            yield self
        finally:
            self.answer_status = 0

    @contextmanager
    def kill_gone(self):
        """끄기를 404 로 되돌린다 — 폴더 없이 tmux 세션만 있던 항목을 끄고 나면 이름 자체가
        브릿지 관문에서 사라져 두 번째 요청이 404 다. 결과는 어차피 '꺼짐'이라 화면은 이것을
        에러로 띄우면 안 된다(사용자가 아무것도 할 게 없는 경고창이 뜬다)."""
        self.kill_status = 404
        try:
            yield self
        finally:
            self.kill_status = 0


def _make_cert(dest: Path) -> tuple[str, str]:
    cert, key = dest / "cert.pem", dest / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key), "-out", str(cert),
         "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return str(cert), str(key)


@pytest.fixture(scope="session")
def bridge(tmp_path_factory):
    from aiohttp import WSMsgType, web

    cert, key = _make_cert(tmp_path_factory.mktemp("tls"))
    state: dict = {}

    async def static_file(request):
        name, ctype = STATIC[request.path]
        charset = None if ctype.startswith("image/") else "utf-8"   # 진짜 브릿지와 같게 — 그림엔 charset 없음
        return web.Response(body=(WEB_DIR / name).read_bytes(), content_type=ctype, charset=charset)

    async def api_office(request):
        state["bridge"].office_calls += 1
        return web.json_response({"people": state["bridge"].office})

    async def api_sessions(request):
        # 진짜 브릿지와 같게 — 숨긴 것도 목록에 남기고 'hidden' 표시만 붙인다(접는 것은 화면의 몫).
        # 즐겨찾기도 같은 잣대다: 순번('fav')만 실어보내고 목록 순서는 건드리지 않는다.
        # ★ 여기서 미리 정렬해 주면 '맨 위로 올리는 것은 화면의 몫'이라는 계약을 테스트가 못 본다.
        hidden, favs = state["bridge"].hidden, state["bridge"].favs
        return web.json_response({"sessions": [
            {**s, "hidden": s["name"] in hidden,
             "fav": favs.index(s["name"]) if s["name"] in favs else None} for s in SESSIONS],
            "restorable": state["bridge"].restorable})

    async def api_session_kill(request):
        body = await request.json()
        bridge = state["bridge"]
        if bridge.kill_status:                          # kill_gone 손잡이
            return web.json_response({"error": "unknown project"}, status=bridge.kill_status)
        bridge.kills.append(body.get("project"))
        bridge.killed.add(body.get("project"))
        # 세션 목록(SESSIONS)까지 죽이지는 않는다 — 세션 공용 픽스처라 다른 테스트로 샌다.
        return web.json_response({"ok": True, "killed": True})

    async def api_sessions_kill_all(request):
        bridge = state["bridge"]
        bridge.kill_alls += 1
        bridge.restorable = len(SESSIONS)               # 끄고 나면 복원거리가 생긴다(진짜와 같게)
        bridge.killed.update(s["name"] for s in SESSIONS)
        return web.json_response({"ok": True, "killed": [s["name"] for s in SESSIONS], "failed": []})

    async def api_sessions_restore(request):
        bridge = state["bridge"]
        bridge.restores += 1
        bridge.killed.clear()
        return web.json_response({"ok": True, "restored": [s["name"] for s in SESSIONS],
                                  "already": [], "failed": []})

    async def api_hidden_get(request):
        return web.json_response({"hidden": sorted(state["bridge"].hidden)})

    async def api_hidden_set(request):
        body = await request.json()
        bridge = state["bridge"]
        bridge.hides.append(body)
        project, hidden = body.get("project"), body.get("hidden")
        if not isinstance(project, str) or not isinstance(hidden, bool):
            return web.json_response({"error": "bad body"}, status=400)
        bridge.hidden.add(project) if hidden else bridge.hidden.discard(project)
        return web.json_response({"hidden": sorted(bridge.hidden)})

    async def api_favorites_get(request):
        return web.json_response({"favorites": list(state["bridge"].favs)})

    async def api_favorites_set(request):
        body = await request.json()
        bridge = state["bridge"]
        bridge.favs_posts.append(body)
        project, fav = body.get("project"), body.get("fav")
        if not isinstance(project, str) or not isinstance(fav, bool):
            return web.json_response({"error": "bad body"}, status=400)
        if fav:
            if project not in bridge.favs:
                bridge.favs.append(project)                 # 새로 누른 것은 맨 뒤 — 정렬하지 않는다
        else:
            bridge.favs.remove(project) if project in bridge.favs else None
        return web.json_response({"favorites": list(bridge.favs)})

    async def api_summary(request):
        return web.json_response({
            "alive": sum(1 for s in SESSIONS if s["alive"]),
            "pending": sum(1 for c in DECISIONS if c["live"] and c["kind"] in DECISION_KINDS),
            "waiting": sum(1 for c in DECISIONS if c["live"] and c["kind"] not in DECISION_KINDS),
            "dead": sum(1 for c in DECISIONS if not c["live"]),
            "web": state["bridge"].web_version,          # 새 배포 감지 배너용
        })

    async def api_decisions(request):
        return web.json_response({"decisions": DECISIONS})

    async def api_settings(request):
        bridge = state["bridge"]
        if request.method == "POST":
            body = await request.json()
            bridge.agent_posts.append(body)
            if "projectsRoot" in body:
                if body["projectsRoot"].startswith("/없는"):
                    return web.json_response({"error": "invalid projects root", "code": "not_found",
                                              "reason": "폴더가 존재하지 않습니다"}, status=400)
                bridge.projects_root = body["projectsRoot"]
            # 진짜 브릿지와 같은 규칙 — 에이전트 목록 밖 값은 400, 나머지 키는 부분 갱신
            if "agent" in body:
                if body["agent"] not in ("claude", "codex", "anti", "shell"):
                    return web.json_response({"error": "unknown agent"}, status=400)
                bridge.agent = body["agent"]
            for key in ("bypass", "cont", "rc"):
                if key in body:
                    bridge.flags[key] = body[key] is True
            if isinstance(body.get("commands"), dict):
                protected = {"agy", "alias", "bash", "cd", "claude", "codex", "command", "cp", "eval",
                             "exec", "exit", "git", "kill", "ls", "mv", "rm", "sh", "source",
                             "sudo", "tmux", "unalias", "zsh"}
                for value in body["commands"].values():
                    if not isinstance(value, str) or (value and
                            (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", value)
                             or value in protected)):
                        return web.json_response({"error": "invalid alias",
                                                  "reason": "사용할 수 없는 별칭 이름입니다"}, status=400)
                bridge.commands.update({k: str(v) for k, v in body["commands"].items()
                                        if k in bridge.commands})
        return web.json_response({"settings": {"projectsRoot": bridge.projects_root,
                                               "agent": bridge.agent, **bridge.flags,
                                               "commands": dict(bridge.commands)},
                                  "agents": ["claude", "codex", "anti", "shell"]})

    async def api_agents(request):
        # 실측 형태 그대로(bridge/settings.probe) — 화면이 이 모양에 기대 그린다
        return web.json_response({"agents": {
            "claude": {"installed": True, "version": "9.9.9 (Claude Code)",
                       "email": "test@example.com", "org": "테스트팀",
                       "latestVersion": "9.9.9", "updateAvailable": False,
                       "updateStatus": {"autoUpdates": "enabled", "channel": "latest",
                                        "lastAttempt": {"status": "success", "version": "9.9.9",
                                                        "date": "2026-08-14"}}},
            "codex": {"installed": True, "version": "codex-cli 9.9.9", "loggedIn": False,
                      "latestVersion": "9.9.10", "updateAvailable": True},
        }})

    async def api_agent_update(request):
        agent = request.match_info["agent"]
        if agent not in ("claude", "codex"):
            return web.json_response({"error": "unknown agent"}, status=400)
        state["bridge"].agent_updates.append(agent)
        return web.json_response({"agent": agent, "success": True, "updated": True,
                                  "beforeVersion": "9.9.9", "afterVersion": "9.9.10",
                                  "activeSessions": 8,
                                  "output": "가짜 업데이트 성공 — 테스트 출력"})

    async def api_open(request):
        state["bridge"].opens.append(await request.json())
        return web.json_response({"ok": True})

    async def api_mkdir(request):
        body = await request.json()
        state["bridge"].mkdirs.append(body)
        if body.get("path", "").endswith("이미있음"):    # 중복 오류 화면 검증용
            return web.json_response({"error": "exists"}, status=409)
        return web.json_response({"ok": True})

    async def api_upload(request):
        bridge = state["bridge"]
        project = request.query.get("project", "")
        form = await request.post()                       # multipart — 진짜 브릿지와 같은 모양
        f = form.get("file")
        name = getattr(f, "filename", "?")
        d = request.query.get("dir", "")                  # 폴더 드롭 — 하위 구조 상대경로
        rec = {"project": project, "name": name}
        if d:
            rec["dir"] = d
        bridge.uploads.append(rec)
        resp = {"path": f"/tmp/_uploads/{project}/{(d + '/') if d else ''}{name}"}
        if d:
            resp["top"] = f"/tmp/_uploads/{project}/{d.split('/')[0]}"
        return web.json_response(resp)

    async def api_clipboard(request):
        b = state["bridge"]
        return web.json_response({"text": b.copy_buffer, **({} if b.copy_age is None else {"age": b.copy_age})})

    async def api_project_new(request):
        body = await request.json()
        bridge = state["bridge"]
        name = str(body.get("name", ""))
        bridge.new_projects.append(name)
        if name in [x["name"] for x in SESSIONS]:
            return web.json_response({"error": "exists"}, status=409)
        if not name or "/" in name or name.startswith("."):
            return web.json_response({"error": "bad name"}, status=400)
        SESSIONS.append({"name": name, "alive": False, "path": f"/tmp/{name}",
                         "status": "off", "pending": 0})
        return web.json_response({"ok": True, "name": name, "path": f"/tmp/{name}"})

    async def api_uploads_usage(request):
        return web.json_response(state["bridge"].cache)

    async def api_uploads_clear(request):
        bridge = state["bridge"]
        got = dict(bridge.cache)
        bridge.cache = {"bytes": 0, "files": 0}
        bridge.cache_cleared += 1
        return web.json_response({**got, "left": bridge.cache})

    async def api_capture(request):
        bridge = state["bridge"]
        bridge.captures.append({"w": request.query.get("w"), "h": request.query.get("h")})
        p = bridge.capture_path
        return web.json_response({"path": p} if p else {})

    async def api_locate(request):
        body = await request.json()
        bridge = state["bridge"]
        bridge.locates.append(body)
        p = bridge.locate_map.get(body.get("name", ""))
        return web.json_response({"path": p} if p else {"matches": 0})

    async def api_hud(request):
        # 진짜 브릿지가 statusline.py --accounts 로 받는 모양 — ANSI 색 그대로. 마지막 줄은 일부러
        # HTML 처럼 생긴 글자를 넣었다: 웹이 글자로만 그리는지(태그로 해석하지 않는지) 본다.
        return web.json_response({"lines": [
            "\x1b[36m▶\x1b[0m\x1b[1mdigi\x1b[0m \x1b[2m5h\x1b[0m \x1b[94m█\x1b[2m░░░░░\x1b[0m \x1b[94m 22%\x1b[0m",
            " \x1b[2mceo \x1b[0m \x1b[2m5h\x1b[0m \x1b[31m██████\x1b[0m \x1b[31m 95%\x1b[0m",
            "\x1b[32m●\x1b[0m\x1b[2mcdx \x1b[0m 7d 63%",
            "\x1b[33m▲\x1b[0m\x1b[2manti\x1b[0m <img src=x onerror=alert(1)>",
        ]})

    async def api_usage(request):
        # cswap 실측 파싱 결과 형태(bridge/settings.parse_usage) — 상태바가 이 모양에 기대 그린다
        return web.json_response({"usage": {"account": "test@example.com", "windows": [
            {"name": "5h", "pct": 38, "reset": "04:19", "left": "3h 34m"},
            {"name": "7d", "pct": 85, "reset": "Aug 20 22:59", "left": "5d 22h"},
        ]}})

    async def api_decision_answer(request):
        # 진짜 브릿지는 답을 보낸 뒤 카드를 지우지만, 여기선 일부러 남긴다 —
        # "카드가 아직 남아 있는 동안 사용자가 한 번 더 누르는" 상황이 화면에서 막히는지 봐야 한다.
        body = await request.json()
        bridge = state["bridge"]
        if bridge.answer_status:                        # picker_gone 손잡이 — 답이 나가지 않는다
            return web.json_response({"error": "picker gone"}, status=bridge.answer_status)
        bridge.answers.append(body)
        return web.json_response({"ok": True})

    async def slow_down(project: str) -> None:
        """slow_files 손잡이가 걸려 있으면 그만큼 응답을 늦춘다(경합 재현용)."""
        b = state["bridge"]
        if b.file_delay and b.slow_project in (None, project):
            await asyncio.sleep(b.file_delay)

    async def api_files(request):
        project, path = request.query.get("project", ""), request.query.get("path", "")
        await slow_down(project)
        if project not in NS or path not in FS_PATHS:
            return web.json_response({"error": "NotAllowed"}, status=404)
        # fs_patch 손잡이: (프로젝트, 경로) → 목록 전체. 터미널에서 폴더가 생기는 '파일시스템이 바뀐
        # 뒤' 를 흉내 낸다 — 화면이 다시 읽어야만 새 항목이 보인다.
        entries = state["bridge"].fs_patch.get((project, path)) or fs_of(project)[path]
        return web.json_response({"entries": entries})

    async def api_file_get(request):
        project, path = request.query.get("project", ""), request.query.get("path", "")
        await slow_down(project)
        found = files_of(project).get(path) if project in NS else None
        if not found:
            return web.json_response({"error": "NotAllowed"}, status=404)
        content, mtime = found
        return web.json_response({"content": content, "mtimeNs": mtime,
                                  "size": len(content.encode())})

    async def api_file_image_get(request):
        project, path = request.query.get("project", ""), request.query.get("path", "")
        await slow_down(project)
        if project not in NS or path != "shot.png":
            return web.json_response({"error": "NotAllowed"}, status=404)
        return web.Response(body=(WEB_DIR / "icon-192.png").read_bytes(), content_type="image/png",
                            headers={"Content-Disposition": "inline",
                                     "X-Content-Type-Options": "nosniff"})

    async def api_file_put(request):
        # 진짜 브릿지와 같은 규칙 — baseMtimeNs 는 문자열이어야 하고(숫자면 400), 그 프로젝트의
        # 지금 버전과 문자열로 같아야 쓴다(다르면 409 — 프로젝트가 뒤바뀐 저장도 여기서 걸린다).
        # 다만 내용·버전을 실제로 갱신하지는 않는다: 세션 공용 픽스처라 갱신하면 테스트끼리
        # 실행 순서에 얽힌다.
        body = await request.json()
        state["bridge"].writes.append(body)
        project, path = body.get("project"), body.get("path")
        await slow_down(project)
        if not isinstance(body.get("baseMtimeNs"), str):
            return web.json_response({"error": "baseMtimeNs must be string"}, status=400)
        found = files_of(project).get(path) if project in NS else None
        if not found:
            return web.json_response({"error": "NotAllowed"}, status=404)
        if body["baseMtimeNs"] != found[1]:
            return web.json_response({"error": "Conflict"}, status=409)
        return web.json_response({"ok": True, "mtimeNs": found[1]})

    async def api_preview_services(request):
        project = request.query.get("project", "")
        svcs = sorted(state["bridge"].services,
                      key=lambda s: (s["project"] != project, s["project"] is None, s["port"]))
        return web.json_response({"services": svcs, "active": {}})

    async def api_preview_open(request):
        body = await request.json()
        state["bridge"].preview_opens.append(body)
        svc = next((s for s in state["bridge"].services if s["id"] == body.get("id")), None)
        if svc is None:
            return web.json_response({"error": "그 서비스가 지금 LISTEN 중이 아닙니다"}, status=409)
        return web.json_response({"port": svc["port"], "dest": f"http://127.0.0.1:{svc['port']}", "expires": 0})

    async def api_preview_renew(request):
        state["bridge"].preview_renews.append((await request.json()).get("port"))
        return web.json_response({"renewed": True})

    TINY_JPEG = base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
        "wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q==")

    async def api_browser_stop(request):
        project = request.match_info["project"]
        state["bridge"].browser_stops.append(project)
        state["bridge"].browser_running.discard(project)
        return web.json_response({"stopped": True})

    async def ws_browser(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        bridge = state["bridge"]
        project = request.match_info["project"]
        if project not in bridge.browser_running and request.query.get("start") != "1":
            await ws.send_json({"type": "closed", "message": f"'{project}' 브라우저가 꺼져 있습니다"})
            await ws.close()
            return ws
        bridge.browser_running.add(project)
        async def push(url):
            await ws.send_json({"type": "state", "url": url, "title": "t", "canBack": url != "about:blank",
                                "canForward": False, "active": "T1",
                                "tabs": [{"id": "T1", "title": "탭하나", "url": url}], "viewport": [800, 600]})
        await push("about:blank")
        await ws.send_json({"type": "frame", "seq": 1, "w": 1, "h": 1})
        await ws.send_bytes(TINY_JPEG)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            cmd = json.loads(msg.data)
            bridge.browser_cmds.append((project, cmd))
            if cmd.get("type") == "navigate":
                await push(cmd["url"])
        return ws

    async def ws_terminal(request):
        bridge = state["bridge"]
        name = request.match_info["name"]
        if name in bridge.killed:                           # 진짜 브릿지와 같은 규칙(세션 목록은 안 죽는 가짜라
            if request.query.get("re") == "1":              # '살아 있나' 검사는 생략 — 끈 것은 꺼졌다고 본다)
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                await ws.close(code=4410, message=b"killed")
                return ws
            bridge.killed.discard(name)
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        bridge.ws_open += 1                                 # 세션을 갈아탈 때 소켓이 겹치는지 본다
        bridge.ws_log.append(request.match_info["name"])
        bridge.ws_agents.append(request.query.get("agent"))
        await ws.send_bytes(b"\x1b[?1000h\x1b[?1006h$ ")   # tmux 처럼 마우스 리포트 on + 프롬프트
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    bridge.received.extend(msg.data)
                    bridge.by_session.setdefault(request.match_info["name"], bytearray()).extend(msg.data)
                    await ws.send_bytes(msg.data)           # 셸 에코
                elif msg.type == WSMsgType.TEXT:
                    bridge.ws_text.append(msg.data)         # {"type":"resize",…} — 크기 동기화 증거
        finally:
            bridge.ws_open -= 1
        return ws

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = web.Application()
        for route in STATIC:
            app.router.add_get(route, static_file)
        app.router.add_get("/api/sessions", api_sessions)
        app.router.add_post("/api/sessions/kill", api_session_kill)
        app.router.add_post("/api/sessions/kill-all", api_sessions_kill_all)
        app.router.add_post("/api/sessions/restore", api_sessions_restore)
        app.router.add_get("/api/hidden", api_hidden_get)
        app.router.add_post("/api/hidden", api_hidden_set)
        app.router.add_get("/api/favorites", api_favorites_get)
        app.router.add_post("/api/favorites", api_favorites_set)
        app.router.add_get("/api/summary", api_summary)
        app.router.add_get("/api/settings", api_settings)
        app.router.add_post("/api/settings", api_settings)
        app.router.add_get("/api/agents", api_agents)
        app.router.add_post("/api/agents/{agent}/update", api_agent_update)
        app.router.add_get("/api/usage", api_usage)
        app.router.add_get("/api/hud", api_hud)
        app.router.add_post("/api/open", api_open)
        app.router.add_post("/api/mkdir", api_mkdir)
        app.router.add_post("/api/upload", api_upload)
        app.router.add_post("/api/locate", api_locate)
        app.router.add_get("/api/clipboard", api_clipboard)
        app.router.add_get("/api/capture", api_capture)
        app.router.add_post("/api/projects", api_project_new)
        app.router.add_get("/api/uploads", api_uploads_usage)
        app.router.add_post("/api/uploads/clear", api_uploads_clear)
        app.router.add_get("/api/decisions", api_decisions)
        app.router.add_get("/api/office", api_office)
        pad_api.add_routes(app)                       # 게임패드 신호 — 진짜 모듈 그대로
        app.router.add_post("/api/decisions/answer", api_decision_answer)
        app.router.add_get("/ws/browser/{project}", ws_browser)
        app.router.add_post("/api/browser/{project}/stop", api_browser_stop)
        app.router.add_get("/api/preview/services", api_preview_services)
        app.router.add_post("/api/preview/open", api_preview_open)
        app.router.add_post("/api/preview/renew", api_preview_renew)
        app.router.add_get("/api/files", api_files)
        app.router.add_get("/api/file", api_file_get)
        app.router.add_get("/api/file/image", api_file_image_get)
        app.router.add_put("/api/file", api_file_put)
        app.router.add_get("/ws/{name}", ws_terminal)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ctx)
        loop.run_until_complete(site.start())
        state["bridge"] = FakeBridge(site._server.sockets[0].getsockname()[1])
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    for _ in range(200):                                    # 포트가 잡힐 때까지 (최대 10초)
        if "bridge" in state:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("가짜 브릿지가 뜨지 않았다")
    return state["bridge"]


@pytest.fixture
def clean_hidden(bridge):
    """숨김 목록을 비운 채 시작하고, 끝나면 되돌린다 — 브릿지가 세션 공용이라 숨긴 항목을
    그대로 두면 다음 테스트의 사이드바에서 프로젝트 하나가 통째로 사라진다."""
    bridge.hidden.clear()
    bridge.hides.clear()
    yield bridge
    bridge.hidden.clear()


@pytest.fixture
def clean_favs(bridge):
    """즐겨찾기를 비운 채 시작하고, 끝나면 되돌린다 — 브릿지가 세션 공용이라 하나라도 남기면
    다음 테스트의 사이드바 순서(상태순)가 통째로 뒤집힌다."""
    bridge.favs.clear()
    bridge.favs_posts.clear()
    yield bridge
    bridge.favs.clear()
