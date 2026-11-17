"""전체 끄기 / 전체 복원 — 한 쌍으로만 뜻이 있는 기능.

끄기는 되돌릴 수 없다. 세션 안에서 돌던 클로드가 함께 죽기 때문이다. 그래서 **끄기 전에
명단부터 파일에 남기고**, 복원은 그 명단에 적힌 이름만 되살린다 — '지금 열 수 있는 전부'가
아니라 '그때 열려 있던 것만'이다.

명단을 메모리가 아니라 파일에 두는 이유: 껐다가 브릿지가 재시작돼도 복원할 수 있어야 한다.
tmux kill-server 를 쓰지 않는 이유: 이 맥의 tmux 서버에는 프로그터미널이 만들지 않은 세션도
산다. 이름 하나씩 관문(is_valid_session)을 통과시켜, 사이드바가 보여주는 것만 끈다.
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path

from bridge import config, sessions, settings


def snapshot_file() -> Path:
    """끄기 직전 명단. 결재 카드·숨김 명단과 같은 자리에 둔다(루트를 바꾸면 함께 따라간다)."""
    return config.projects_root() / "_frogterminal" / "last-kill.json"


def killable() -> list[str]:
    """지금 끌 수 있는 세션 = 사이드바가 보여주는 살아있는 세션.

    CPU 팝업(_frogtop)은 뺀다 — 터미널이 아니라 도구창이고, [CPU] 버튼이 따로 여닫는다.
    """
    return [name for name in sessions.list_tmux_sessions()
            if name != config.MONITOR_SESSION and sessions.is_valid_session(name)]


def parse_snapshot(data: object) -> dict:
    """{"sessions": [이름…], "agents": {이름: 에이전트}, "at": "ISO"} — 깨졌으면 빈 명단.

    agents 는 코덱스·안티로 돌던 세션만 적힌다(sessions.session_agents) — 없는 이름은 설정의 기본
    에이전트로 되살린다. 손으로 고친 파일도 들어올 수 있으므로 검증은 여기 한곳에서.
    """
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        return {"sessions": [], "agents": {}, "at": ""}
    at, agents = data.get("at"), data.get("agents")
    agents = agents if isinstance(agents, dict) else {}
    # 이름 검증은 여기서 한 번 더 — 손으로 고친 파일이 복원 때 tmux 인자로 그대로 흘러가지 않게.
    names = [n for n in data["sessions"] if isinstance(n, str) and sessions.is_valid_name(n)]
    return {"sessions": names,
            "agents": {n: a for n, a in agents.items() if n in names and a in settings.AGENTS},
            "at": at if isinstance(at, str) else ""}


def load_snapshot() -> dict:
    """끄기 직전 명단 — 없거나 깨졌으면 빈 명단(복원 버튼이 꺼진다)."""
    try:
        return parse_snapshot(json.loads(snapshot_file().read_text()))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return parse_snapshot(None)


def save_snapshot(names: list[str], agents: dict[str, str] | None = None) -> None:
    """원자적으로 남긴다 — 반쪽 JSON 을 읽고 '복원할 게 없다'고 답하는 일이 없게."""
    path = snapshot_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"sessions": names, "agents": agents or {},
                               "at": datetime.now(config.KST).isoformat()}, ensure_ascii=False))
    tmp.replace(path)


def kill_all() -> dict:
    """열려 있는 터미널을 전부 끈다. 되묻기는 웹이 한다(되돌릴 수 없으므로).

    명단을 **먼저** 남긴다 — 중간에 무엇이 터져도 복원의 재료는 이미 디스크에 있다.
    """
    names = killable()
    if names:                                    # 빈 명단으로 덮어써서 복원거리를 지우지 않는다
        agents = sessions.session_agents()       # 코덱스로 돌던 칸이 클로드로 되살아나지 않게
        save_snapshot(names, {n: agents[n] for n in names if n in agents})
    killed, failed = [], []
    for name in names:
        try:
            sessions.kill_session(name)          # 이미 꺼져 있었으면 False — 그래도 '꺼짐'이 결과다
            killed.append(name)
        except LookupError:                      # tmux 실행 실패·TOCTOU
            failed.append(name)
    return {"killed": killed, "failed": failed, "snapshot": names}


def _create_detached(cmd: list[str]) -> None:    # 여기 두는 이유: 테스트 monkeypatch 지점
    subprocess.run(cmd, capture_output=True, timeout=15, check=True)


def restore_all() -> dict:
    """끄기 직전 명단에 있던 세션만 되살린다. 이미 살아 있는 것은 건드리지 않는다(멱등).

    붙지 않고 만들기만 한다(-d). 웹이 붙는 것과 같은 명령(attach_cmd)을 쓰므로, 세션이 새로
    뜰 때 설정한 에이전트가 그대로 실행되고 — 이어가기(--continue)가 켜져 있으면 나가기 직전
    대화로 돌아온다. 그것이 '직전 상태로 복원'의 실체다.
    """
    snap = load_snapshot()
    alive = set(sessions.list_tmux_sessions())
    default = settings.load()["agent"]
    restored, already, failed = [], [], []
    for name in snap["sessions"]:
        if name in alive:
            already.append(name)
            continue
        if not sessions.is_valid_session(name):  # 폴더가 사라진 이름 — 되살릴 자리가 없다
            failed.append(name)
            continue
        cmd = sessions.attach_cmd(name, agent=snap["agents"].get(name, default))
        # ["tmux", "new-session", …] 사이에 -d 를 끼운다: -A 는 있으면 붙고 없으면 만드는데,
        # -d 가 '붙지는 마라'를 더한다. 브릿지는 지금 이 세션에 붙을 화면이 없다.
        try:
            _create_detached(cmd[:2] + ["-d"] + cmd[2:])
        except (OSError, subprocess.SubprocessError):
            failed.append(name)
            continue
        restored.append(name)
    return {"restored": restored, "already": already, "failed": failed}
