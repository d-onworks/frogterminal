"""결재 카드 — 훅 상태 스캔 + tmux 화면 피커 파싱 + 응답 전송.

카드 = 클로드 세션 1개의 현재 상태(라이브). 훅(frog_hook.py)이 쓰고 여기서 읽는다.
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from bridge import config, sessions
from bridge.config import encode_cwd          # 대화 파일 경로 규칙 — settings 와 같은 것을 쓴다

# 사람 답이 없으면 클로드가 한 발짝도 못 나가는 상태만 '결재'다.
# idle("Claude is waiting for your input")은 뺐다 — 그건 할 말을 마치고 빈 프롬프트에서 쉬는
# 것이라, 결정할 거리가 없는데도 빨간불이 켜져 진짜 결재가 그 안에 묻혔다.
# 그런 카드는 '완료 — 다음 지시 대기'로 간다. 화면에 진짜 선택지가 떠 있으면 화면스캔이
# picker 로 올려주므로, 답해야 할 것을 놓치지는 않는다.
DECISION_KINDS = {"permission", "picker"}
_PRIORITY = {"decision": 0, "working": 1, "done": 2}
_EMPTY_CONTEXT = {"asked": "", "said": "", "lastAt": ""}


_BOOT_RE = re.compile(r"sec\s*=\s*(\d+)")


def boot_time() -> float | None:                     # 여기 두는 이유: 테스트 monkeypatch 지점
    """이번 부팅 시각(epoch). 못 읽으면 None = 판정불가 → 아무 카드도 지우지 않는다(fail-open).

    `sysctl -n kern.boottime` 이 '{ sec = 1786764970, usec = 850840 } Sat Aug 15 12:36:10 2026'
    을 준다. 앞의 sec 만 쓴다 — 뒤 문자열은 로케일을 타지만 sec 는 안 탄다.
    """
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:                          # 맥이 아닌 곳(리눅스)엔 이 키가 없다
        return None
    m = _BOOT_RE.search(out.stdout)
    if not m:
        return None
    boot = float(m.group(1))
    # 부팅 시각이 '미래'로 보이면 시계가 뒤로 튄 것이다(NTP 보정 등). 그대로 믿으면 부팅 뒤에
    # 쓰인 멀쩡한 카드까지 통째로 지운다 — 그럴 땐 판정불가로 물러선다.
    return boot if boot <= time.time() else None


def _load_cards() -> list[dict]:
    d = config.decisions_dir()
    if not d.is_dir():
        return []
    now = time.time()
    # 재부팅 전에 쓰인 카드는 예외 없이 유령이다 — 그 세션의 클로드도, 붙어 있던 tmux 서버도
    # 부팅에서 함께 죽는다. 재부팅에서는 SessionEnd 훅이 울릴 수 없으니 여기서 대신 지운다.
    # (실측 2026-08-15: 맥스튜디오 이관 뒤 12:36 재부팅으로 오전 카드 3장이 '세션 꺼짐'으로
    #  최대 72시간 남았다. 카드의 machine 이 옛 호스트네임인 것은 같은 재부팅의 부산물일 뿐,
    #  살아있음 판정은 machine 을 보지 않는다.)
    boot = boot_time()
    cards = []
    for p in sorted(d.glob("*.json")):
        try:
            mtime = p.stat().st_mtime
            if (now - mtime > config.DECISION_TTL_HOURS * 3600
                    or (boot is not None and mtime < boot)):
                p.unlink(missing_ok=True)            # 유령 카드 청소 (SessionEnd 못 받은 경우)
                continue
            card = json.loads(p.read_text())
            if isinstance(card, dict):               # 객체가 아닌 JSON(배열·숫자)은 카드가 아니다
                cards.append(card)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return cards


def capture_pane(name: str) -> str:                  # Task 4 에서 사용 — 여기 두는 이유: monkeypatch 지점
    try:
        out = subprocess.run(["tmux", "capture-pane", "-p", "-t", sessions.tmux_target(name)],
                             capture_output=True, text=True, timeout=5)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


_OPTION_RE = re.compile(r"^\s*(?P<marker>❯\s*)?(?P<n>\d{1,2})[.)]\s+(?P<label>.+?)\s*$")
_TRIM = " \t│┃╭╮╰╯─━●○◯>*"                     # 클로드 UI 장식문자 — 질문·라벨에서 벗겨낸다


def parse_picker(text: str) -> dict | None:
    """화면의 마지막 피커(❯ 선택 마커가 붙은, 1부터 시작하는 연속 번호 목록 ≥2개)를 찾는다."""
    lines = text.splitlines()
    best = None
    i = 0
    while i < len(lines):
        m = _OPTION_RE.match(lines[i])
        if m and m.group("n") == "1":
            opts, marked, j = [], False, i
            while j < len(lines):
                m2 = _OPTION_RE.match(lines[j])
                if not m2 or int(m2.group("n")) != len(opts) + 1:
                    break
                opts.append({"n": len(opts) + 1, "label": m2.group("label").strip(_TRIM)})
                marked = marked or bool(m2.group("marker"))
                j += 1
            # ❯ 마커가 하나라도 있어야 피커로 인정 — fail-closed.
            # 마커 없는 진짜 피커를 놓쳐도 훅의 permission/idle 카드가 결재로 잡아주지만,
            # 평범한 번호 목록(README·도움말·할일)을 결재로 오인하면 막아줄 안전망이 없다.
            if len(opts) >= 2 and marked:
                question = ""
                for k in range(i - 1, max(-1, i - 6), -1):   # 옵션 위 5줄 안에서 질문 탐색
                    s = lines[k].strip(_TRIM)               # _TRIM 에 공백·탭이 들어 있다
                    if s:
                        question = s
                        break
                best = {"question": question, "options": opts}
                i = j
                continue
        i += 1
    return best


# ── 대화 맥락 (asked/said) ──────────────────────────────────────────────────
# "Claude is waiting for your input" 만 있는 카드로는 아무도 판단할 수 없다. 클로드 코드가
# 세션마다 남기는 대화 JSONL 을 꼬리만 읽어, 마지막에 시킨 것/마지막에 한 말을 얹는다.

# 슬래시 명령(그 껍데기 <command-name>·<local-command-stdout>·<system-reminder> 포함)과 인터럽트
# 표식은 사용자가 친 말이 아니다 — 이게 asked 로 올라오면 카드가 또 아무 정보도 못 준다.
_NOISE_USER_RE = re.compile(r"^(<|\[Request interrupted|/[A-Za-z])")
_CONTEXT_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}


def forget_context() -> None:
    _CONTEXT_CACHE.clear()                       # 테스트 격리용 — 캐시가 테스트 사이로 새지 않게


def transcript_path(card: dict) -> Path | None:
    """카드가 가리키는 대화 파일. ~/.claude/projects 밖이면 None(=맥락 없음)."""
    raw = card.get("transcriptPath")
    if raw and isinstance(raw, str):
        candidate = Path(raw)                    # 훅이 준 실제 경로 — 계산보다 정확하다
    else:
        sid, cwd = card.get("sessionId"), card.get("cwd")
        # 화면스캔으로 합성된 피커 카드는 sessionId 가 None 이다 — 찾을 대화가 없다.
        if not (isinstance(sid, str) and _SID_RE.fullmatch(sid) and cwd):
            return None
        candidate = config.TRANSCRIPT_ROOT / encode_cwd(cwd) / f"{sid}.jsonl"
    try:
        # encode_cwd 가 '/' 와 '.' 를 지워 폴더명 자체로는 탈출할 수 없지만, 훅이 준 경로는
        # 검증을 안 거친 값이다. files.py 와 같은 잣대로 심링크까지 따라가 루트 안을 재확인한다.
        root, path = config.TRANSCRIPT_ROOT.resolve(), candidate.resolve()
    except OSError:
        return None
    return path if path.is_relative_to(root) else None


def _read_tail(path: Path) -> str:
    """끝에서 TRANSCRIPT_TAIL_BYTES 만 읽는다 — 실측 165MB 파일을 통째로 읽으면 브릿지가 죽는다."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(0, size - config.TRANSCRIPT_TAIL_BYTES)
        f.seek(start)
        data = f.read()
    text = data.decode("utf-8", "replace")        # 꼬리 경계에서 멀티바이트가 반토막 날 수 있다
    if start:                                    # seek 지점의 첫 줄은 반쪽 JSON — 통째로 버린다
        _, sep, text = text.partition("\n")
        if not sep:
            return ""
    return text


def _texts(message) -> str:
    """message.content 에서 '글'만. 배열이면 text 블록만이 글이다(도구·생각 블록은 아니다)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # tool_result 만 든 user 항목은 여기서 ""가 되어 자연히 건너뛰어진다.
        return " ".join(b["text"] for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                        and isinstance(b.get("text"), str))
    return ""


def _cap(value: str) -> str:
    """카드 한 줄로 접고 상한에서 자른다. 잘랐으면 '…'로 표시를 남긴다."""
    folded = " ".join(value.split())              # 줄바꿈·탭을 접어야 상한이 의미를 가진다
    clean = "".join(ch for ch in folded if ch.isprintable())   # 제로폭·양방향 제어문자 제거
    if len(clean) > config.CARD_CONTEXT_CHARS:
        return clean[:config.CARD_CONTEXT_CHARS].rstrip() + "…"
    return clean


def _bookkeeping(entry: dict, kind: str, text: str) -> bool:
    """대화의 '진행'이 아니라 살림살이인 항목인가 — 압축 요약 주입과 슬래시 명령 부산물.

    이걸 진행으로 세면 끝난 카드(done/idle)가 _behind 로 '진행중'으로 뒤집힌다. /compact 는
    대화 파일에 자기 부산물(명령 껍데기·요약·완료 출력)을 남기는데, 그 시각이 마지막 Stop 훅보다
    한참 뒤라 사이드바가 '진행중…'에 묶였다(실측 2026-08-28 donworksmall: Stop 19:35 카드 →
    압축 부산물 19:45 → 60초 뒤 idle 알림이 올 때까지 계속 진행중).
    슬래시 명령을 통째로 걸러도 잃는 게 없다 — 진짜 지시였다면 UserPromptSubmit 훅이 이미
    working 카드를 썼고, _behind 는 working 이 아닌 카드에만 물어보기 때문이다.
    """
    if entry.get("isCompactSummary"):
        return True
    return kind == "user" and bool(text) and bool(_NOISE_USER_RE.match(text))


def _scan(text: str) -> dict:
    """뒤에서부터 훑어 마지막 user 발화·마지막 assistant 글, 그리고 마지막 대화 시각을 찾는다."""
    asked = said = last_at = ""
    for line in reversed(text.split("\n")):
        if asked and said:
            break
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue                              # 깨진 줄 하나가 스캔을 끝내지 않게
        # isSidechain=True 는 서브에이전트 대화다 — 사용자가 시킨 것도, 사용자에게 한 말도 아니다.
        # isMeta=True 는 시스템이 user 턴에 끼워 넣은 것(스킬 본문·캐비앳·붙인 이미지 경로).
        # 실측: 이걸 안 거르면 "Base directory for this skill: …" 이 사용자의 지시로 올라온다.
        if not isinstance(entry, dict) or entry.get("isSidechain") or entry.get("isMeta"):
            continue
        kind = entry.get("type")
        if kind not in ("user", "assistant"):     # last-prompt·bridge-session 같은 살림살이 항목
            continue
        text = _texts(entry.get("message")).strip()
        if _bookkeeping(entry, kind, text):       # 압축·슬래시 부산물은 진행이 아니다
            continue
        # 대화가 카드보다 앞서 갔는지 재는 눈금(_behind). 글이 없는 항목도 진행이다 — 승인 직후
        # 들어오는 tool_result 가 바로 그것이고, 그게 '이 결재는 끝났다'는 유일한 신호다.
        if not last_at and isinstance(entry.get("timestamp"), str):
            last_at = entry["timestamp"]
        if kind == "user" and not asked:
            asked = text
        elif kind == "assistant" and not said:
            said = text
    return {"asked": _cap(asked), "said": _cap(said), "lastAt": last_at}


def context_for(card: dict) -> dict:
    """카드 하나의 {asked, said}. 대화가 없거나 깨졌으면 빈 값 — 카드는 그대로 나간다.

    mtime+size 로 캐시한다. 폴링이 7초마다 돌고 카드가 7장이면 매번 파일 7개의 꼬리(각 400KB)를
    읽게 되는데, 정작 결재를 기다리는 카드(idle/permission)는 클로드가 멈춰 있어 대화가
    안 늘어난다 — 그래서 결재 카드일수록 캐시가 100% 맞는다. 진행중 세션만 다시 읽힌다.
    """
    path = transcript_path(card)
    if path is None:
        return dict(_EMPTY_CONTEXT)
    try:
        st = path.stat()
    except OSError:                               # 없음·권한없음 — 다른 cwd 에서 돈 세션일 수 있다
        return dict(_EMPTY_CONTEXT)
    key, stamp = str(path), (st.st_mtime_ns, st.st_size)
    hit = _CONTEXT_CACHE.get(key)
    if hit and hit[0] == stamp:
        return dict(hit[1])                       # 사본 — 호출자가 고쳐도 캐시가 오염되지 않게
    try:
        text = _read_tail(path)
    except (OSError, ValueError):
        return dict(_EMPTY_CONTEXT)
    context = _scan(text)
    if len(_CONTEXT_CACHE) >= config.CONTEXT_CACHE_MAX:
        _CONTEXT_CACHE.clear()                    # 죽은 세션 키가 무한정 쌓이지 않게 (통째로 비움)
    _CONTEXT_CACHE[key] = (stamp, context)
    return dict(context)


def _moment(value) -> float | None:
    """ISO 시각 → epoch. 훅은 KST(+09:00)로, 대화 기록은 UTC('…Z')로 쓴다 — 둘 다 받는다."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _behind(card: dict) -> bool:
    """훅이 이 카드를 쓴 뒤로 대화가 더 진행됐나 = 카드가 말하는 상태는 이미 지나갔다.

    권한 프롬프트를 웹이 아니라 터미널에서 직접 승인하면 훅이 울리지 않는다. 클로드는 하던 일을
    계속하는데 카드만 '결재 필요'로 영영 남았다 — 사용자가 본 그 '이전 것'이 이것이다.
    승인 직후 대화에는 tool_result 가 붙으므로, 마지막 대화 시각이 카드보다 뒤면 결재는 끝난 것이다.
    """
    last, wrote = _moment(card.get("lastAt")), _moment(card.get("updatedAt"))
    return last is not None and wrote is not None and last > wrote


def _live_check(starts: dict | None, tmux_names: set):
    """카드가 지금 살아있는지 판정하는 함수를 만든다 — 기준은 '그 클로드 프로세스가 아직 도는가'.

    예전 기준(같은 이름의 tmux 세션이 있나)은 실측이 정반대로 나왔다. 클로드는 대부분 tmux 밖
    (Zed·iTerm)에서 돌아서 ①지금 일하는 세션이 '세션 꺼짐'으로 분류되고 ②[터미널 열기]가
    new-session -A 로 만든 빈 셸이 죽은 카드를 '살아있음'으로 되살려 빨간 결재로 올렸다.
    pid 를 안 적은 옛 카드와 ps 판정불가(None)일 때만 그 예전 기준으로 물러선다.
    """
    def check(card: dict) -> bool:
        pid, start = card.get("pid"), card.get("pidStart")
        if starts is not None and isinstance(pid, int) and isinstance(start, str):
            return starts.get(pid) == start
        return card.get("project") in tmux_names
    return check


def overview() -> dict:
    """열린 결재 카드 + 프로젝트별 상태 한 번에. working 카드는 상태 집계에만 쓴다."""
    # 프로그터미널이 연 세션(tmux 안)만 — Zed·iTerm 클로드의 카드가 따라 들어오면, 사이드바가
    # "완료 대기"라는 프로젝트를 눌렀을 때 정작 새 tmux 클로드가 뜨는 어긋남이 생긴다.
    # 표식 없는 옛 카드도 제외한다: 그 세션이 다음에 뭐라도 하면 훅이 표식을 달아 다시 올라온다.
    cards = [c for c in _load_cards() if c.get("inTmux") is True]
    open_cards = [c for c in cards if c.get("kind") != "working"]
    live = set(sessions.list_tmux_sessions())
    # 화면스캔 보강 — 훅이 못 잡은 피커를 잡고, 잡힌 카드에는 선택지를 얹는다
    for name in live:
        picker = parse_picker(capture_pane(name))    # capture_pane 은 실패해도 "" 를 준다
        if not picker:
            continue
        mine = [c for c in open_cards
                if c.get("project") == name and c.get("kind") in ("permission", "idle")]
        if mine:
            # 파일명순(_load_cards) 마지막이 아니라 updatedAt 최신 카드로 — 죽은 세션의
            # 유령 카드(TTL 72h)가 살아있는 세션의 피커를 가로채지 않게
            latest = max(mine, key=lambda c: str(c.get("updatedAt") or ""))
            latest.update(picker)                    # message 는 훅이 쓴 원문 유지
            latest["kind"] = "picker"
        else:
            open_cards.append({"sessionId": None, "project": name, "cwd": "",
                               "machine": "", "kind": "picker",
                               "message": picker["question"] or "선택지 대기 중",
                               "updatedAt": "", **picker})
    # live = 이 카드의 tmux 세션이 지금 실제로 붙어있는가. 훅(SessionEnd)이 정상 경로에서는
    # 카드를 지우지만, 크래시·kill -9 로 죽으면 카드가 TTL(72h) 동안 유령으로 남는다. 그 카드에
    # 답을 보내면 send_answer 가 LookupError → 404 이므로, 헤드라인 결재 수(api_summary)는
    # 이 필드로 걸러야 한다. 카드 자체는 목록에 남겨 사용자가 터미널을 다시 열어 볼 길을 열어둔다.
    # 대화 맥락 — 카드 하나가 터져도 목록 전체를 죽이지 않는다(전역 제약). 세 필드는 항상 있다.
    for c in open_cards:
        try:
            c.update(context_for(c))
        except Exception:
            c.update(_EMPTY_CONTEXT)
        # 화면에 지금 떠 있는 피커는 대화 기록보다 강한 증거다 — 그것만은 내리지 않는다.
        if c.get("kind") != "picker" and _behind(c):
            c["kind"] = "working"                 # 대화가 앞서 갔다 = 답을 기다리는 중이 아니다
    # 방금 내려온 카드도 여기 함께 잡힌다(open_cards 는 cards 와 같은 객체를 들고 있다).
    working = [c for c in cards if c.get("kind") == "working"]
    open_cards = [c for c in open_cards if c.get("kind") != "working"]
    is_live = _live_check(sessions.process_starts(), live)
    for c in open_cards + working:                # working 카드도 같은 잣대로 — 이게 빠져 있어
        c["live"] = is_live(c)                    # 죽은 프로젝트가 '진행중'으로 남았다
        # 살아있는 것과 '여기서 답을 보낼 수 있는 것'은 다르다. 답은 tmux send-keys 로만 들어가므로
        # Zed·iTerm 에서 도는 세션은 살아 있어도 웹에서 답할 길이 없다(send_answer 가 404 를 낸다).
        # 화면이 이 둘을 구분해야 버튼을 눌러 보고서야 "안 된다"는 말을 듣는 일이 없다.
        c["canAnswer"] = c["live"] and c.get("project") in live
    states: dict[str, dict] = {}
    # 3D 모션 모드(office.py)가 서브에이전트를 세는 데 쓰는 '살아 있는 클로드 대화 파일'. 상태와 같은
    # 잣대(살아 있음 + tmux 있음)로 고른다. states 에 섞지 않는 이유: 사이드바·테스트가 states 를
    # {"status","pending"} 그대로 비교한다.
    transcripts: dict[str, list[str]] = {}
    # 상태는 '열 수 있는 터미널'이 있을 때만 — tmux 세션(탭 포함)이 없으면 만들지 않는다.
    # 프로세스 생존(live)만으로 상태를 만들면, tmux 세션을 끈 뒤에도 그 안에서 떠난 헤드리스
    # claude -p(자동화)가 살아 있는 동안 사이드바가 '진행중…'을 달고 점은 꺼짐인 채 어긋난다
    # (실측 2026-08-23: marketing 카드 pid 가 `claude -p --disallowedTools …` 였다).
    live_bases = {sessions.base_of(n) for n in live}
    for c in open_cards + working:
        project = c.get("project")
        # 죽은 세션은 지금 상태가 없다. 상태를 만들면 사이드바가 유령 프로젝트를 '결재 대기'로
        # 최상단에 띄우는데(decision 최우선 정렬), 헤드라인 결재 수는 0 이라 서로 어긋난다.
        if not project or not c["live"] or project not in live_bases:
            continue
        kind = c.get("kind")                          # kind 없는 카드 한 장이 전체 집계를 죽이지 않게
        status = ("decision" if kind in DECISION_KINDS
                  else "working" if kind == "working" else "done")
        st = states.setdefault(project, {"status": status, "pending": 0})
        if _PRIORITY[status] < _PRIORITY[st["status"]]:
            st["status"] = status
        if kind in DECISION_KINDS:
            st["pending"] += 1
        path = transcript_path(c)                     # ~/.claude/projects 밖(코덱스 등)은 None
        if path is not None:
            transcripts.setdefault(project, []).append(str(path))
    return {"cards": open_cards, "states": states, "transcripts": transcripts}


class NoPicker(Exception):
    """그 화면에 (그 번호까지 있는) 피커가 없다 — 보내면 엉뚱한 항목이 선택된다."""


_SID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _tmux(*args: str) -> None:
    subprocess.run(["tmux", *args], capture_output=True, timeout=5, check=True)


def _clean_answer(value: str) -> str:
    """웹에서 온 답을 pty 에 그대로 넣어도 안전한 한 줄로 만든다.

    send-keys -l 은 인자를 사람이 친 키처럼 pty 에 넣으므로, ^C(\\x03)·ESC(\\x1b) 같은
    제어문자가 남아 있으면 돌던 명령이 중단되거나 셸이 escape 시퀀스를 해석한다(실측 확인).
    str.split() 은 공백류만 접고 제어문자는 공백으로 보지 않아 그대로 살아남는다.
    그래서 블랙리스트 대신 화이트리스트 — 출력 가능한 문자만 통과시킨다(제로폭·양방향
    제어문자까지 함께 걷힌다. 감사로그·화면에서 답을 위장하는 데 쓰일 수 있다).
    """
    folded = " ".join(value.split())              # 개행=중간제출 위험, 탭=보완 트리거 → 공백으로
    return "".join(ch for ch in folded if ch.isprintable())[:config.MAX_ANSWER_CHARS]


def send_answer(project: str, type_: str, value: str) -> None:
    """웹 결재 답을 그 프로젝트의 tmux 세션에 키입력으로 넣는다. 실행은 클로드 쪽 UI가 한다."""
    if project not in sessions.list_tmux_sessions():
        raise LookupError(project)
    if type_ == "option":
        # 한 자리만 허용한다. send-keys "12" 는 '12' 한 키가 아니라 '1' 다음 '2' 를 보내므로(실측),
        # 옵션 10개 이상 화면에서는 1번이 선택되고 남은 '2' 가 다음 프롬프트로 흘러간다.
        # (\d 대신 명시 문자셋 — \d 는 아라비아-인도 숫자 같은 유니코드 숫자까지 통과한다.)
        if not re.fullmatch(r"[1-9]", value):        # 0 은 클로드 피커에 없는 번호
            raise ValueError(value)
        # 카드의 project 는 훅이 cwd 로 만든 이름일 뿐 — 물어본 클로드가 그 tmux 세션 안에
        # 있다는 보장이 없다. attach_cmd 의 new-session -A 탓에 웹에서 한 번 열기만 해도 같은
        # 이름의 빈 셸이 생기고, 거기로 간 답은 셸 명령으로 실행된다. 값싼 1차 방어로
        # 지금 그 화면에 그 번호의 피커가 떠 있는지 확인한다(화면 못 읽으면 안 보낸다 — fail-closed).
        picker = parse_picker(capture_pane(project))
        if not picker or len(picker["options"]) < int(value):
            raise NoPicker(project)
        keystrokes = [(value,)]                      # 클로드 피커는 숫자키가 곧 선택
    elif type_ == "text":
        text = _clean_answer(value)
        if not text:
            raise ValueError("empty")
        keystrokes = [("-l", "--", text), ("Enter",)]
    else:
        raise ValueError(type_)
    try:
        for keys in keystrokes:
            _tmux("send-keys", "-t", sessions.tmux_target(project), *keys)
    except (OSError, subprocess.SubprocessError) as e:
        # 살아있음 검사와 send-keys 사이에 세션이 죽는 창(TOCTOU)이 실재한다. 그대로 두면
        # 핸들러가 못 잡아 500 — 답이 안 들어갔다는 뜻이므로 LookupError(=404 세션 없음)로 맞춘다.
        raise LookupError(project) from e


def clear_card(session_id) -> None:
    # fullmatch — '$' 는 끝 개행을 허용하므로 match 로는 's9\n' 이 통과한다
    if isinstance(session_id, str) and _SID_RE.fullmatch(session_id):
        (config.decisions_dir() / f"{session_id}.json").unlink(missing_ok=True)
