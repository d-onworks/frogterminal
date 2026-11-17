"""3D 모션 모드(사무실 화면) 데이터 — 열린 프로젝트 하나 = 캐릭터 하나.

화면(web/office.js)이 그리는 데 필요한 것만 모은다:
- 상태(결재·진행중·완료)와 결재 문구 — 사이드바와 같은 집계(decisions.overview)를 그대로 쓴다.
- 커밋 수 — 많은 저장소는 보통 크기, 적은 저장소(KID_COMMITS 미만)는 꼬마로 그린다.
- 지금 일하는 서브에이전트 — 꼬마들이 서류를 들고 줄 서는 수.

서브에이전트는 클로드 대화 파일 옆 `<session-id>/subagents/agent-*.jsonl` 로 센다(설정을 하나도
안 건드리고 읽기만 한다). 이 형식은 클로드 코드 내부 형식이라 버전마다 바뀔 수 있다(공식 문서
"세션 데이터 내보내기 및 찾기") — 그래서 못 읽는 줄은 조용히 넘기고, 틀려도 꼬마 수만 어긋날 뿐
나머지 화면은 그대로 서게 짰다.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from bridge import decisions, office_activity

KID_COMMITS = 50            # 이보다 커밋이 적으면 꼬마(70% 크기) — 2026-10-22 실측: 열린 저장소 3~770개
COMMIT_TTL = 600            # 커밋 수는 10분 캐시 — git 을 3초마다 두드리지 않게
SUB_QUIET = 1800            # 30분 넘게 기록이 멈춘 서브에이전트는 끝난(끊긴) 것으로 본다
SUB_TAIL = 256 * 1024       # 마지막 줄만 보면 된다 — 실측 22MB 파일도 있어 통째로 읽지 않는다
MAX_SUBS = 12               # 화면에 줄 세우는 꼬마 상한 — 넘으면 수만 말한다
ASK_CHARS = 80              # 결재 문구는 화면이 20자로 다시 자른다 — 여기선 넉넉히 상한만

_commits: dict[str, tuple[float, int | None]] = {}


def forget() -> None:
    _commits.clear()                          # 테스트 격리용


def commit_count(path: str | None) -> int | None:
    """그 폴더의 커밋 수. git 저장소가 아니거나 못 세면 None(=보통 크기로 그린다)."""
    if not path:
        return None
    now = time.monotonic()
    hit = _commits.get(path)
    if hit and now - hit[0] < COMMIT_TTL:
        return hit[1]
    try:
        out = subprocess.run(["git", "-C", path, "rev-list", "--count", "HEAD"],
                             capture_output=True, text=True, timeout=3)
        n = int(out.stdout.strip()) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        n = None
    _commits[path] = (now, n)
    return n


def _last_entry(path: Path) -> dict | None:
    """파일 끝에서 마지막 대화 줄(assistant/user). api-request 같은 기록용 줄은 건너뛴다."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - SUB_TAIL)
            f.seek(start)
            data = f.read()
    except OSError:
        return None
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]                     # seek 지점의 첫 줄은 반쪽 JSON
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") in ("assistant", "user"):
            return entry
    return None


def subagent_running(path: Path, now: float | None = None) -> bool:
    """서브에이전트가 아직 일하는가 — 마지막 줄이 '말을 마친 답'(end_turn)이 아니면 일하는 중.

    실측(2026-10-22, 최근 60개): 끝난 것은 전부 마지막 줄이 assistant + stop_reason end_turn 이었다.
    도구를 부르는 중(tool_use)·결과를 받은 직후(user)·생각 중은 모두 '일하는 중'이다.
    """
    now = time.time() if now is None else now
    try:
        if now - path.stat().st_mtime > SUB_QUIET:
            return False
    except OSError:
        return False
    entry = _last_entry(path)
    if entry is None:
        return True                           # 막 생겨 아직 쓴 게 없다 — 출근 중으로 본다
    msg = entry.get("message")
    stop = msg.get("stop_reason") if isinstance(msg, dict) else None
    return not (entry.get("type") == "assistant" and stop == "end_turn")


def _meta(path: Path) -> dict:
    try:
        meta = json.loads(path.with_name(path.name.replace(".jsonl", ".meta.json")).read_text())
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def running_subagents(transcript: str, now: float | None = None) -> list[dict]:
    """그 대화가 띄운 서브에이전트 중 지금 일하는 것 — 먼저 출근한 순서."""
    folder = Path(transcript).with_suffix("") / "subagents"
    try:
        files = list(folder.glob("agent-*.jsonl"))
    except OSError:
        return []
    found = []
    for f in files:
        if not subagent_running(f, now):
            continue
        meta = _meta(f)
        try:
            born = f.stat().st_ctime
        except OSError:
            continue
        found.append((born, {
            "id": f.stem.removeprefix("agent-"),
            "type": str(meta.get("agentType") or ""),
            "desc": str(meta.get("description") or "")[:60],
            "doing": office_activity.activity(str(f))["doing"],      # 꼬마 머리 위 말풍선
        }))
    return [item for _, item in sorted(found, key=lambda t: t[0])]


def snapshot(items: list[dict], ov: dict) -> dict:
    """사이드바에 보이는 '켜진' 프로젝트마다 캐릭터 한 명분의 정보."""
    asks: dict[str, str] = {}
    for c in ov.get("cards", []):
        if c.get("live") and c.get("kind") in decisions.DECISION_KINDS and c.get("project"):
            text = c.get("question") or c.get("message") or ""
            asks.setdefault(c["project"], " ".join(str(text).split())[:ASK_CHARS])
    states = ov.get("states", {})
    transcripts = ov.get("transcripts", {})
    now = time.time()
    people = []
    for s in items:
        # 사이드바와 같은 잣대 — 숨긴 것·프로젝트 아닌 폴더는 사무실에도 안 나온다
        if not s.get("alive") or s.get("hidden") or s.get("project") is False:
            continue
        name = s["name"]
        st = states.get(name) or {}
        commits = None if s.get("host") else commit_count(s.get("path"))
        subs, acts = [], []
        for t in transcripts.get(name, []):
            subs.extend(running_subagents(t, now))
            acts.append(office_activity.activity(t))
        # 한 프로젝트에 대화가 둘 이상이면(탭·/clear 직후) 가장 최근에 움직인 쪽이 '지금 하는 일'
        log = sorted((e for a in acts for e in a["log"]), key=lambda e: e["at"])[-office_activity.LOG_N:]
        latest = max(acts, key=lambda a: a["log"][-1]["at"] if a["log"] else "", default=None)
        people.append({
            "name": name,
            "agent": s.get("agent") or "claude",
            "status": st.get("status", "on"),
            "pending": st.get("pending", 0),
            "ask": asks.get(name, ""),
            "commits": commits,
            "kid": commits is not None and commits < KID_COMMITS,
            "subs": subs[:MAX_SUBS],
            "subCount": len(subs),
            "doing": latest["doing"] if latest else "",
            "log": log,
            "path": s.get("path"),
            "host": s.get("host"),
        })
    return {"people": people}
