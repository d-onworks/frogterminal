"""세션 = 프로젝트 폴더당 tmux 1개. 다른 세션은 절대 죽이지 않는다(목록·attach만).

예외는 kill_session 하나뿐 — 사용자가 사이드바에서 콕 집어 지목한 그 세션만 끈다(한 번 연
세션이 영영 남아 메모리만 늘어나기 때문). attach·detach·목록은 그대로, 아무것도 죽이지 않는다.
"""
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from bridge import config, settings
from bridge.config import IGNORED_FOLDERS, MONITOR_SESSION

# \w(유니코드) — 이 맥의 프로젝트 폴더에는 한글 이름이 실재한다('보고서양식-시안' 등).
# 영문만 허용하던 옛 규칙은 한글 폴더의 숨기기·터미널 열기를 전부 400/404 로 막았다(실측).
# 막는 것은 그대로 막힌다: 경로 구분자(/), 공백, 개행·제어문자, 선행 점(숨김·상대경로).
_NAME_RE = re.compile(r"^[\w.-]+$")


def is_valid_name(name: str) -> bool:
    """이름 '모양'만 본다 — 경로조작·이상문자 차단. 존재 여부는 묻지 않는다.

    match 가 아니라 fullmatch 인 이유: '$' 는 끝 개행을 허용해 match 로는 'dev\\n' 이
    통과한다(decisions.clear_card 가 같은 함정을 겪고 fullmatch 로 고쳤다).
    """
    return bool(name) and bool(_NAME_RE.fullmatch(name)) and not name.startswith(".")


# ── 병렬 터미널(탭) — 같은 프로젝트에서 터미널을 여러 개 (Zed 처럼)
# 이름 = "<프로젝트>@N" (N 2~9). '@' 는 폴더 이름 규칙(_NAME_RE)에 없어서 진짜 폴더와
# 절대 충돌하지 않고, tmux 세션 이름으로는 유효하다(실측). 탭도 독립 tmux 세션이라
# 브릿지·서버가 죽어도 살아남고, 폰·맥 어디서든 같은 탭 목록이 보인다.
_TAB_RE = re.compile(r"^(?P<base>[\w.-]+)@(?P<n>[2-9])$")


def base_of(name: str) -> str:
    """탭 이름이면 그 프로젝트 이름을, 아니면 그대로 — 파일·업로드·카드가 쓰는 기준."""
    m = _TAB_RE.fullmatch(name or "")
    return m["base"] if m else name


def list_project_folders(root: Path | None = None) -> list[str]:
    root = root if root is not None else config.projects_root()
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    # is_dir()만 보면 루트 밖을 가리키는 심링크도 폴더 행세를 한다. 실제 목적지가 현재 루트
    # 안에 있는 하위 폴더인지까지 확인해 목록과 is_valid_session의 기준을 맞춘다.
    return sorted(p.name for p in children
                  if not p.name.startswith(".") and p.name not in IGNORED_FOLDERS
                  and project_dir(root, p.name) is not None)


def project_dir(root: Path, name: str) -> Path | None:
    """이름의 실제 디렉터리가 realpath 기준으로도 root 안이면 그 목적지를 돌려준다."""
    try:
        real_root = root.resolve(strict=True)
        raw = root / name
        if not raw.is_dir():
            return None
        real = raw.resolve(strict=True)
        if real == real_root or not real.is_relative_to(real_root):
            return None
        return real
    except OSError:
        return None


def is_project_folder(name: str, root: Path | None = None) -> bool:
    """바로 아래에 .git 또는 CLAUDE.md/AGENTS.md 가 있는 안전한 하위 폴더인가.

    마커 파일만 기준으로 삼았더니 실제 프로젝트가 무더기로 '프로젝트 아님'이 됐다
    (2026-08-18 실측: git 저장소 44개 중 마커 보유 22개 — 사내 프로젝트 여럿·
    storymaker 는 물론 frogterminal 자기 자신까지 숨겨졌다). 마커는 있으면 좋은 것이지
    프로젝트의 정의가 아니다. git 저장소인지가 훨씬 정확한 기준이라 이쪽을 먼저 본다.

    .git 은 is_dir 이 아니라 exists 로 본다 — git worktree 에서는 .git 이 디렉터리가
    아니라 gitdir 을 가리키는 파일이라, is_dir 로 보면 워크트리를 통째로 놓친다.

    사이드바 [+ 새 프로젝트] 로 만든 폴더는 .git 도 마커도 없는 빈 폴더라 이 기준에 걸려
    다음 새로고침에 사라졌다(2026-08-22 실사고 — 'demo' 를 만들고 다른 세션에 갔다
    오니 없어짐). 사용자가 만들겠다고 한 것은 그 자체로 프로젝트다 → 명단(created.json)에
    있으면 내용과 무관하게 프로젝트로 친다.
    """
    root = root if root is not None else config.projects_root()
    folder = project_dir(root, name)
    if not folder:
        return False
    if name in load_created():
        return True
    if (folder / ".git").exists():
        return True
    return any((folder / marker).is_file()
               for marker in ("CLAUDE.md", "AGENTS.md"))


def tmux_target(name: str) -> str:
    """tmux -t 에 넘길 '정확히 이 세션' 표기 — `=이름:`.

    맨 이름을 -t 에 주면 tmux 는 점(.)을 '세션.칸' 구분자로 읽는다. 'Dr.thesis' 폴더가
    'Dr 세션의 thesis 칸'이 되어 다시 붙을 때마다 `can't find pane: thesis` 로 실패했고,
    'Dr' 세션이 따로 있으면 그 세션의 옵션을 바꾸고 끄기까지 할 수 있었다(2026-11-17 다른 사용자 신고·실측).
    '=' = 이름 정확히 일치(앞부분 일치 금지), 끝 ':' = 여기까지가 세션 이름.
    """
    return f"={name}:"


def list_tmux_sessions() -> list[str]:
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:              # tmux 서버 미기동
        return []
    return [l for l in out.stdout.splitlines() if l]


# pane 현재 명령 → 에이전트 키(settings.AGENTS 의 키). 클로드는 여기 없다(아래 docstring).
_PANE_AGENTS = {"codex": "codex", "agy": "anti"}


def session_agents() -> dict[str, str]:
    """세션 이름 → 그 안에서 지금 도는 에이전트("codex"·"anti"). 사이드바 점 색의 근거.

    tmux 가 보는 pane 의 현재 명령(#{pane_current_command})만 읽는다 — 코덱스는 단일 바이너리라
    "codex", Antigravity CLI 는 "agy" 로 보이고, 클로드는 버전 이름("2.1.261")·node 로 보여 특정할
    수 없다. 그래서 그 둘만 표시하고 나머지는 없는 것으로 둔다(클로드가 기본 그림이라 별도 표시가
    필요 없다 — 코덱스 = 파랑, 안티 = 보라, 요청 2026-09-05·10-18). 탭(base@N)은
    자기 줄이 없으므로 기본 세션의 pane 만 본다. 실패하면 빈 dict(=색 없음) — 목록 자체는 안 막는다.
    """
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_command}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    agents = {}
    for line in out.stdout.splitlines():
        name, _, cmd = line.partition("\t")
        if name and (agent := _PANE_AGENTS.get(cmd.strip())):
            agents[name] = agent
    return agents


# ssh 에서 값을 하나 더 먹는 옵션들(`man ssh` 의 SYNOPSIS). 이 뒤 토큰은 호스트가 아니다.
_SSH_OPTS_WITH_ARG = set("bcDEeFIiJLlmOopQRSWwBP")


def ssh_target(argv: list[str]) -> str | None:
    """ssh 명령줄(argv)에서 붙으려는 호스트만 뽑는다. ssh 가 아니거나 못 찾으면 None.

    `ssh -t srvpc` → "srvpc", `ssh -p 2222 admin@host` → "host", `ssh ssh://u@h:22` → "h".
    ~/.ssh/config 의 Host 별칭(srvpc·winpc2·winpc)은 별칭 그대로 — 사용자가 부르는 이름이
    곧 배지다. 호스트 뒤의 토큰(원격 명령)은 보지 않는다.
    """
    if not argv or Path(argv[0]).name != "ssh":
        return None
    it = iter(argv[1:])
    for tok in it:
        if tok == "--":
            tok = next(it, None)
            break
        if tok.startswith("-"):
            # "-p2222" 처럼 값이 붙은 꼴과 "-p 2222" 처럼 떨어진 꼴 둘 다. "-tt" 같은 묶음 불리언은 통과.
            flag = tok[1:2]
            if flag in _SSH_OPTS_WITH_ARG and len(tok) == 2:
                next(it, None)
            continue
        break
    else:
        return None
    if tok is None:
        return None
    if tok.startswith("ssh://"):
        tok = tok[len("ssh://"):].split("/", 1)[0]
        tok = tok.rpartition("@")[2]
        if tok.startswith("["):                          # [IPv6]:port
            tok = tok[1:].split("]", 1)[0]
        else:
            tok = tok.split(":", 1)[0]
        return tok or None
    return tok.rpartition("@")[2] or None


# 원격 명령 안의 cd — `cd /d C:\rproj && cmd`(윈도우) · `cd /srv/app; bash`(리눅스). 따옴표 묶음도 한 토큰.
_REMOTE_CD_RE = re.compile(r"""(?:^|[\s;&|])cd\s+(?:/d\s+)?("[^"]+"|'[^']+'|[^\s;&|]+)""", re.I)


def ssh_remote_cwd(argv: list[str]) -> str | None:
    """ssh 명령줄의 원격 명령(호스트 뒤 토큰들)에서 `cd` 로 들어가는 폴더를 뽑는다. 없으면 None."""
    host = ssh_target(argv)
    if not host:
        return None
    rest = " ".join(argv[argv.index(next(t for t in argv[1:] if t.rpartition("@")[2].endswith(host))) + 1:])
    m = _REMOTE_CD_RE.search(rest)
    return m.group(1).strip("\"'") if m else None


def _ssh_children(pane_pids: set[int]) -> dict[int, str]:
    """pane 의 셸(pane_pid) 바로 아래에서 도는 ssh 의 pane_pid → 호스트. ps 한 번으로 전부."""
    try:
        out = subprocess.run(["ps", "-eo", "ppid=,pid=,args="],
                             capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    found = {}
    for line in out.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            ppid = int(parts[0])
        except ValueError:
            continue
        if ppid in pane_pids and ppid not in found:
            host = ssh_target(parts[2].split())
            if host:
                found[ppid] = host
    return found


def session_remotes() -> dict[str, dict]:
    """세션 이름 → {"host": 붙어 있는 다른 기계, "path": 그 기계의 작업 폴더}. 사이드바 [host] 배지와
    경로 줄의 근거(요청 2026-10-13: "서버 PC 의 C:\\work 프로젝트"처럼 폴더까지 매칭돼야 한다).

    pane 의 현재 명령이 ssh 인 것만 본다. 호스트는 ①pane 시작 명령(`tmux new -s rproj "ssh -t srvpc"`)
    에서, 그게 셸이면 ②셸 바로 아래 ssh 자식의 인자에서(손으로 `ssh winpc` 을 친 경우) 읽는다.
    원격 폴더는 ①tmux 세션 옵션 `@remote_path`(만드는 쪽이 `tmux set-option -t <세션> @remote_path 'C:\\rproj'`
    로 적어 둔다 — 세션이 사는 동안 유지) ②없으면 ssh 원격 명령의 `cd …` 에서 읽는다. 둘 다 없으면 None.
    맥 로컬 세션은 여기 안 실려 지금과 똑같이 보인다. 실패하면 빈 dict(목록 자체는 안 막는다).
    """
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}\t#{pane_current_command}\t#{pane_pid}\t#{@remote_path}\t#{pane_start_command}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    remotes: dict[str, dict] = {}
    need_ps: dict[int, str] = {}                        # pane_pid → 세션 (시작 명령으로 못 푼 ssh pane)
    for line in out.stdout.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5 or parts[1].strip() != "ssh" or parts[0] in remotes:
            continue
        name, _, pid, opt_path, start = parts
        # tmux 는 시작 명령을 통째로 따옴표에 싸서 준다("ssh -t srvpc") — 한 번 벗기고 다시 쪼갠다.
        try:
            argv = shlex.split(start)
            if len(argv) == 1 and " " in argv[0]:
                argv = shlex.split(argv[0])
        except ValueError:
            argv = []
        host = ssh_target(argv)
        if host:
            remotes[name] = {"host": host, "path": opt_path.strip() or ssh_remote_cwd(argv)}
        elif pid.isdigit():
            need_ps.setdefault(int(pid), (name, opt_path.strip() or None))
    if need_ps:
        for pid, host in _ssh_children({p for p in need_ps}).items():
            name, opt_path = need_ps[pid]
            remotes.setdefault(name, {"host": host, "path": opt_path})
    return remotes


def process_starts() -> dict[int, str] | None:
    """살아있는 프로세스의 pid → 시작시각 문자열. 한 번의 ps 로 전부. 실패하면 None(=판정불가).

    훅이 카드에 적어 둔 (pid, pidStart) 와 문자열끼리 견주기만 한다 — 시각을 해석하지 않는다.
    pid 번호는 재사용되지만 시작시각까지 같을 수는 없어, 남의 프로세스를 그 세션으로 오인하지 않는다.
    (판정불가를 None 으로 되돌리는 이유: 빈 dict 로 주면 ps 가 한 번 실패한 순간 살아있는 세션이
     전부 '꺼짐'으로 뒤집힌다. 호출자가 옛 기준으로 물러설 수 있게 '모름'을 구분해 준다.)

    LC_ALL=C 고정 필수 — ps 의 lstart 는 로케일을 탄다. 한글 환경의 클로드에서 뜬 훅은
    "2026년 8월 15일 토요일 10시…"를 적고 영어 환경의 브릿지는 "Sat Aug 15 …"를 읽어,
    살아있는 세션이 전부 '꺼짐'으로 판정된 실사고(2026-08-15)가 있다. 양쪽 다 C 로 못박는다.
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid=,lstart="],
                             capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    starts = {}
    for line in out.stdout.splitlines():
        pid, _, start = line.strip().partition(" ")
        try:
            starts[int(pid)] = " ".join(start.split())    # 자릿수 맞춤 공백을 접어 훅 쪽과 같은 모양으로
        except ValueError:
            continue
    return starts


def merged_sessions(root: Path | None = None) -> list[dict]:
    root = root if root is not None else config.projects_root()
    alive = set(list_tmux_sessions())
    alive.discard(MONITOR_SESSION)           # CPU 팝업(htop) 세션은 프로젝트가 아니다 — 목록에서 뺀다
    hidden = load_hidden()
    # 즐겨찾기는 '이름 → 누른 순번' 으로 실어보낸다 — 웹이 그 순번만 보고 맨 위로 세운다.
    # 탭(base@N)은 여기 들어올 수 없다: 파일을 읽을 때 is_valid_name 이 '@' 를 걸러낸다.
    fav_rank = {n: i for i, n in enumerate(load_favorites())}
    agents = session_agents() if alive else {}
    remotes = session_remotes() if alive else {}     # ssh 로 다른 PC 에 붙은 세션 → [host] 배지 + 원격 폴더
    folders = set(list_project_folders(root))
    # 탭 세션(base@N)은 사이드바 줄이 아니라 그 프로젝트 항목의 tabs 로 접는다.
    # 프로젝트 폴더가 사라진 고아 탭은 접을 곳이 없으니 그냥 줄로 남긴다(보이고 끌 수 있게).
    tabs: dict[str, list[int]] = {}
    for s in sorted(alive):
        m = _TAB_RE.fullmatch(s)
        if m and m["base"] in (folders | alive - {s}):
            alive.discard(s)
            tabs.setdefault(m["base"], []).append(int(m["n"]))
    names = sorted(folders | alive)
    items = [
        {
            "name": n,
            # 로컬 프로젝트 폴더, 없으면 ssh 세션의 원격 폴더(C:\\rproj 같은 것 — 표시용, 맥에서 열리지 않는다)
            "path": str(root / n) if n in folders else remotes.get(n, {}).get("path"),
            # 기본 세션이 꺼져 있어도 탭이 살아 있으면 이 프로젝트는 켜진 것이다
            "alive": n in alive or bool(tabs.get(n)),
            # 숨김은 '표시'일 뿐 — 목록에서 빼지 않는다. 웹이 접어 두고 "숨긴 항목 N개 보기"로
            # 다시 펴야 하고, 결재 수(api_summary)도 숨김과 무관하게 세야 하기 때문이다.
            "hidden": n in hidden,
            # 폴더가 없는 살아있는 tmux 세션은 기존 규칙대로 보이고 끌 수 있어야 한다. 따라서
            # marker 판별은 실제 폴더 항목에만 적용하고, 고아 세션은 project=True로 둔다.
            # 세션이 켜져 있으면 폴더 내용과 무관하게 프로젝트다 — 파인더에서 만든 빈 폴더(.md 하나)에
            # 코덱스를 띄웠더니 '프로젝트 아님'으로 숨긴 칸에 접혀 사이드바에서 사라졌다.
            "project": (n in alive or bool(tabs.get(n)) or is_project_folder(n, root)) if n in folders else True,
            # 즐겨찾기 순번(0부터) — 즐겨찾기가 아니면 None(JSON null). 숨김과 마찬가지로 '표시'일
            # 뿐이라 목록을 바꾸지 않는다: 맨 위로 올리는 것은 화면의 몫이다.
            "fav": fav_rank.get(n),
            "tabs": sorted(tabs.get(n, [])),
            # 지금 코덱스가 도는 세션이면 "codex"(파란 점)·Antigravity 면 "anti"(보라 점) — 웹이 칠한다.
            "agent": agents.get(n),
            # ssh 로 다른 기계에 붙어 있으면 그 호스트(별칭 그대로) — 웹이 이름 앞에 [host] 배지를 단다.
            # 맥 로컬 세션은 None(JSON null)이라 지금 모양 그대로다.
            "host": remotes.get(n, {}).get("host"),
        }
        for n in names
    ]
    return sorted(items, key=lambda s: (not s["alive"], s["name"]))


def is_valid_session(name: str, root: Path | None = None) -> bool:
    # 파일 API·attach·업로드·끄기가 모두 이 한 관문을 탄다 → 목록에서 뺀 폴더는 여기서도 빼야 한다.
    # 안 그러면 _uploads(append-only 감사로그)·_frogterminal(결재 카드)이 그대로 열린다.
    # 같은 이름의 tmux 세션이 있어도 거부한다 — fail-closed.
    root = root if root is not None else config.projects_root()
    if name == MONITOR_SESSION:              # CPU 팝업(htop) 전용 — 폴더 없이 항상 열 수 있다
        return True
    if m := _TAB_RE.fullmatch(name or ""):
        # 탭은 실제 프로젝트 폴더에만 새로 연다 — _uploads@2 같은 우회는 여기서 같이 죽는다.
        # 이미 살아있는 탭은 폴더가 사라졌어도(고아) 통과 — 붙어서 확인하고 끌 수 있어야 한다.
        base = m["base"]
        if base in IGNORED_FOLDERS or not is_valid_name(base):
            return False
        if project_dir(root, base) is not None:
            return True
        # 루트 밖 심링크가 '폴더 없는 세션' 예외로 되살아나면 안 된다.
        return not (root / base).is_dir() and name in list_tmux_sessions()
    if not is_valid_name(name) or name in IGNORED_FOLDERS:
        return False
    if project_dir(root, name) is not None:
        return True
    return not (root / name).is_dir() and name in list_tmux_sessions()


COMPACT_OPT = "@frog_compact"


def set_compact(name: str, on: bool) -> None:
    """'여러 칸으로 보는 중' 표시를 그 세션에 달거나 뗀다 (멀티 칸 HUD).

    ~/.claude/scripts/statusline.py 가 자기 세션($TMUX_PANE)의 이 표시를 보고 HUD 를 첫 줄만 찍는다 —
    좁은 칸마다 계정 사용량 4줄이 반복되던 것을 웹 하단 한 세트로 모은다. 세션 옵션(-g 아님)이라
    다른 세션·프로그터미널 밖 터미널은 영향이 없다. 실패는 조용히 삼킨다(HUD 가 길게 남을 뿐이다).
    """
    args = ["tmux", "set-option", "-q", "-t", tmux_target(name)]
    args += [COMPACT_OPT, "1"] if on else ["-u", COMPACT_OPT]
    try:
        subprocess.run(args, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def clear_compact_all() -> None:
    """브릿지가 뜰 때 — 지난 실행이 남긴 표시를 전부 뗀다(누가 보고 있는지 기억은 메모리라 재시작에 사라진다)."""
    for name in list_tmux_sessions():
        set_compact(name, False)


def leave_copy_mode(name: str) -> None:
    """그 세션이 tmux 복사 모드(휠 스크롤·드래그로 들어간 상태)면 빠져나온다 — 아니면 아무 일도 없다.

    복사 모드에선 키 입력이 앱(클로드)에 안 가고 화면 안을 돌아다닌다. "위로 스크롤은 조회일 뿐,
    커서는 입력창에 있어야 한다" — 웹이 스크롤 뒤 첫 키 앞에 이걸 부른다.
    if-shell -F 로 tmux 안에서 판단·실행하므로(서버 왕복 1회) 모드 밖에서 Esc 같은 잉여 키가
    앱으로 새지 않는다(Esc 는 클로드의 작업 중단 키라 함부로 못 보낸다). 실패는 조용히 삼킨다.
    """
    try:
        subprocess.run(["tmux", "if-shell", "-F", "-t", tmux_target(name), "#{pane_in_mode}",
                        f"send-keys -t {shlex.quote(tmux_target(name))} -X cancel"],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _kill_tmux(name: str) -> None:                   # 여기 두는 이유: 테스트 monkeypatch 지점
    subprocess.run(["tmux", "kill-session", "-t", tmux_target(name)],
                   capture_output=True, timeout=5, check=True)


def kill_session(name: str) -> bool:
    """지목한 세션 하나를 끈다. 껐으면 True, 이미 꺼져 있었으면 False(멱등).

    관문(is_valid_session)은 호출자가 먼저 통과시킨다. 여기서는 '-t 이름' 하나만 다룬다 —
    kill-server 처럼 전체를 쓸어버리는 명령은 이 파일 어디에도 없어야 한다.
    """
    if name not in list_tmux_sessions():
        return False                                 # 두 번 눌러도 에러가 아니라 조용한 성공
    try:
        _kill_tmux(name)
    except (OSError, subprocess.SubprocessError) as e:
        # 검사와 kill 사이에 세션이 죽는 창(TOCTOU)이 실재한다. 그대로 두면 핸들러가 못 잡아
        # 500 — decisions.send_answer 와 같은 잣대로 LookupError(=404 세션 없음)에 맞춘다.
        # 다만 결과가 '꺼짐'이면 원하던 그대로이므로 한 번 더 실측해 멱등하게 닫는다.
        if name in list_tmux_sessions():
            raise LookupError(name) from e
        return False
    return True


MAX_COPY_BYTES = 100 * 1024                  # 화면 한가득 드래그해도 이 안이다(폭주 방어)


def copy_buffer() -> tuple[str, float | None]:
    """tmux 의 최신 복사 버퍼(드래그로 방금 고른 글자)와 그 버퍼가 만들어진 지 몇 초. 없으면 ("", None).

    왜 필요한가: tmux 가 mouse on 이라 드래그는 브라우저 선택이 아니라 tmux 선택이 되고,
    이 맥의 ~/.tmux.conf 는 그 결과를 pbcopy(맥 클립보드)로 보낸다. 폰에서 드래그하면
    복사물이 폰이 아니라 맥으로 가 버려 붙여넣기가 영영 안 됐다(2026-08-15 실사고).
    그래서 브라우저가 이 버퍼를 읽어 자기 클립보드에도 넣는다.

    나이(초)를 같이 주는 이유: 터미널을 누르다 손이 조금 움직이면 웹은 드래그로
    보는데, tmux 는 새로 복사한 게 없어 '어제 버퍼'가 그대로 왔다 — 그걸 써 넣어 방금 캡처한 그림을 덮었다.
    웹은 이 나이가 '누른 뒤 흐른 시간'보다 크면 쓰지 않는다. 브릿지 시계로 잰 나이라 폰 시계와 무관하다.
    """
    try:
        out = subprocess.run(["tmux", "show-buffer"], capture_output=True, text=True, timeout=5)
        made = subprocess.run(["tmux", "list-buffers", "-F", "#{buffer_created}"],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "", None
    if out.returncode != 0:                  # 버퍼가 하나도 없을 때 — 에러가 아니라 '빈 것'
        return "", None
    first = made.stdout.split("\n", 1)[0].strip()   # 최신 버퍼가 맨 위 — show-buffer 가 보여 준 그것
    age = max(0.0, time.time() - int(first)) if made.returncode == 0 and first.isdigit() else None
    return out.stdout[:MAX_COPY_BYTES], age


def load_hidden() -> set[str]:
    """사이드바에서 접어둔 프로젝트 이름들. 파일이 없거나 깨졌으면 빈 목록으로 시작한다."""
    try:
        data = json.loads(config.hidden_file().read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    # 손으로 고친 파일이 이상한 이름을 API 응답에 그대로 실어보내지 않게 읽을 때도 다시 거른다
    return {n for n in data if isinstance(n, str) and is_valid_name(n)}


def _save_names(path: Path, names: list[str]) -> list[str]:
    """이름 목록을 그 파일에 원자적으로 쓴다 — 숨김·즐겨찾기가 함께 쓰는 한 가지 저장법."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".frogtmp")
    try:
        tmp.write_text(json.dumps(names))
        os.replace(tmp, path)                        # 같은 폴더 안에서만 이동 — 원자적 교체
    except OSError:
        tmp.unlink(missing_ok=True)                  # 실패 잔재를 남기지 않는다
        raise
    return names


def set_hidden(name: str, hidden: bool) -> list[str]:
    """숨김 목록에 넣거나 뺀다 — 갱신된 전체 목록(이름순)을 돌려준다.

    기기(localStorage)가 아니라 서버 파일에 둔다: 폰과 맥이 같은 목록을 봐야 한다.
    """
    names = load_hidden()
    if hidden:
        names.add(name)
    else:
        names.discard(name)
    return _save_names(config.hidden_file(), sorted(names))


def load_favorites() -> list[str]:
    """맨 위로 올린 프로젝트 이름들 — 사용자가 누른 순서 그대로(정렬하지 않는다).

    숨김(set)과 달리 list 인 이유가 이것이다: 순서가 곧 사이드바 맨 위의 줄 순서다.
    손으로 고친 파일이 이상한 이름을 API 응답에 실어보내지 않게 읽을 때도 다시 거른다 —
    is_valid_name 이 '@' 를 막으므로 탭 이름(base@N)은 여기서 통째로 걸린다(즐겨찾기는
    프로젝트 단위다). 같은 이름이 두 번 적혀 있으면 첫 자리만 남긴다(순번이 흔들리지 않게).
    """
    try:
        data = json.loads(config.favorites_file().read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for n in data:
        if isinstance(n, str) and is_valid_name(n) and n not in names:
            names.append(n)
    return names


def set_favorite(name: str, fav: bool) -> list[str]:
    """즐겨찾기에 넣거나 뺀다 — 갱신된 전체 목록(누른 순서)을 돌려준다.

    이름순으로 정렬하지 않는다. 새로 누른 것은 맨 뒤에 붙고, 이미 들어 있으면 자리를 옮기지
    않는다 — 7초 폴링이 다시 그릴 때마다 줄이 뛰면 사용자가 누르려던 줄을 놓친다.
    """
    names = load_favorites()
    if fav:
        if name not in names:
            names.append(name)
    else:
        names = [n for n in names if n != name]
    return _save_names(config.favorites_file(), names)


def load_created() -> set[str]:
    """[+ 새 프로젝트] 로 만든 폴더 이름들 — 빈 폴더여도 사이드바에 남아야 하는 것들.

    숨김과 같은 모양(set)이다: 순서는 뜻이 없고 '명단에 있나'만 본다. 폴더가 지워지면
    목록(list_project_folders)에 안 잡히므로 명단에 이름이 남아 있어도 유령이 뜨지 않는다.
    """
    try:
        data = json.loads(config.created_file().read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {n for n in data if isinstance(n, str) and is_valid_name(n)}


def mark_created(name: str) -> list[str]:
    """새 프로젝트 명단에 넣는다 — 갱신된 전체 목록(이름순)을 돌려준다. 이미 있으면 그대로."""
    names = load_created()
    names.add(name)
    return _save_names(config.created_file(), sorted(names))


# 드래그 복사 뒤 어디로 가나 — "위로 올려 보던 중이면 그 자리에 남고, 맨 아래였으면 바로 실시간으로".
# 기본(tmux-yank 도 같다)은 copy-pipe-and-cancel 이라 손을 떼는 순간 화면이 맨 아래로 튀어 "위로 올려
# 보다가 필요한 부분만 복사"가 안 됐다. 그래서 취소를 뺐더니 이번엔 반대 사고 —
# 드래그로 들어간 복사 모드는 휠을 내려도 자동으로 안 빠져나와(휠로 들어간 것만 -e 로 빠진다) 맨 아래에서
# 복사 한 번 하면 그 뒤 새 출력이 화면에 안 그려졌다. 키를 쳐야 unmode 로 풀리며 밀린 출력이 한꺼번에
# 튀어나왔다(2026-09-20 실측: 5개 세션이 in_mode=1·scroll=0 에 갇혀 있었다 — 윈도우 "갱신 안 됨"도 이것).
# 그래서 ①복사 뒤 scroll_position 이 0(맨 아래)이면 취소, 아니면 남는다 ②복사 모드에서 휠을 내려 맨 아래에
# 닿으면(선택 중이 아닐 때) 빠져나온다. 키 테이블은 tmux 에 세션 단위가 없어 전역 바인딩이다
# (이 맥의 tmux 는 사실상 프로그터미널 전용). 명령 안의 `\;` 는 바인딩 내부 구분자(argv 로는 백슬래시+세미콜론).
#
# 빈 선택은 클립보드에 넣지 않는다 — 터미널을 누를 때 손이 조금만 움직여도 드래그 끝으로 잡혀 빈칸(0바이트·
# 공백뿐)을 pbcopy 로 넘겼고, 그게 방금 휠클릭으로 찍은 캡처를 덮어 "⌘V 가 한 번에 안 들어간다"가 됐다
# (2026-11-08 실측: 하룻밤 빈 글자 쓰기 51번, 매번 앞창 = 프로그터미널. Maccy 는 빈 글자를 안 적어 멀쩡해 보였다).
_PBCOPY_NONEMPTY = "perl -0777 -ne 'exit unless /\\S/; open(my $p, \"|-\", \"pbcopy\") or exit 1; print $p $_'"
_AT_BOTTOM = "#{==:#{scroll_position},0}"
_AT_BOTTOM_NO_SEL = "#{&&:#{==:#{scroll_position},0},#{==:#{selection_present},0}}"
_COPY_BINDS = []
for _table in ("copy-mode", "copy-mode-vi"):
    _COPY_BINDS += [";", "bind-key", "-T", _table, "MouseDragEnd1Pane",
                    "send-keys", "-X", "copy-pipe", _PBCOPY_NONEMPTY, "\\;",
                    "if-shell", "-F", _AT_BOTTOM, "send-keys -X cancel"]
    _COPY_BINDS += [";", "bind-key", "-T", _table, "WheelDownPane",
                    "select-pane", "\\;", "send-keys", "-X", "-N", "5", "scroll-down", "\\;",
                    "if-shell", "-F", _AT_BOTTOM_NO_SEL, "send-keys -X cancel"]


def _session_opts(name: str) -> list[str]:
    """attach 때마다 그 세션에만 못박는 tmux 옵션 — 사유는 attach_cmd 본문 주석 참조."""
    t = tmux_target(name)
    return [";", "set-option", "-t", t, "mouse", "on",
            ";", "set-option", "-t", t, "status", "off",
            ";", "set-option", "-w", "-t", t, "pane-border-status", "off"] + _COPY_BINDS


def attach_cmd(name: str, root: Path | None = None, agent: str = "shell") -> list[str]:
    root = root if root is not None else config.projects_root()
    # 점(.) 든 이름은 new-session -A 가 이미 있는 세션을 못 찾는다 — -s 를 -t 처럼 '세션.칸'으로
    # 읽어 `can't find pane` 으로 끝난다(tmux 3.7c 실측). 있으면 정확한 표기로 붙기만 한다.
    # 없으면 아래 new-session 이 그대로 잘 만든다(-s 의 새 이름은 점을 그대로 받는다).
    if "." in name and name in list_tmux_sessions():
        return ["tmux", "attach-session", "-t", tmux_target(name)] + _session_opts(name)
    cmd = ["tmux", "new-session", "-A", "-s", name]
    if name == MONITOR_SESSION:
        # CPU 팝업 — 설정 에이전트 대신 htop 을 곧바로. zsh -lc(로그인 셸 PATH)라 brew htop 이
        # 잡히고, 없으면 top 으로 물러선다. htop 을 q 로 끝내면 pane 과 세션도 같이 닫힌다.
        return cmd + ["zsh -lc 'htop || top'"] + _session_opts(name)
    base = base_of(name)                    # 탭(base@N)도 그 프로젝트 폴더에서 시작한다
    work = project_dir(root, base)
    if work:
        cmd += ["-c", str(work)]
    # 새로 만드는 세션이면 설정한 에이전트(claude·codex·anti)를 곧바로 띄운다 — 셸에 이름을 치는
    # 한 단계를 없앤다(Orca 차용). 이미 있는 세션에는 -A 가 명령을 무시하고 붙기만 한다(실측).
    # zsh -lic: 브릿지(launchd)의 앙상한 PATH 대신 로그인 셸 PATH 를 타고, -i 로 .zshrc 의
    # alias(cl 같은 약칭)까지 살린다. shlex.quote — 플래그 붙은 명령이 통째로 -c 한 인자가 되게.
    # 에이전트가 끝나면 pane 도 닫힌다 = 빈 셸 좀비 세션이 남지 않는다(의도). 그래서 exit 한 번에
    # 세션이 통째로 사라지고 다시 만들어진다 — 그때 맥락을 잇는 것이 settings 의 '이어서 시작'이다.
    if launch := settings.launch_command(agent, session=name, cwd=work):
        cmd += [f"zsh -lic {shlex.quote(launch)}"]
    # 붙는 그 세션에만 mouse on. 폰 스와이프는 웹에서 휠 리포트로 바뀌어 오는데,
    # 이걸 tmux 가 받아 copy-mode 스크롤로 이어주려면 이 옵션이 필요하다.
    # 지금은 ~/.tmux.conf 의 `set -g mouse on` 덕에 이미 켜져 있지만, 그 파일에 기대다
    # 조용히 깨지지 않도록 붙을 때마다 못박는다. 전역(-g)·다른 세션은 건드리지 않는다.
    # (`;` 는 tmux 의 명령 구분자 — 셸을 안 거치므로 그대로 인자로 넘긴다.)
    # status off — 프로그터미널 세션은 창이 하나뿐이라 tmux 상태줄이 패널 헤더의 세션 이름을
    # 세 번째로 반복할 뿐이고(디자이너 지적), 파란 기본색이 팔레트 밖이다. 그 세션만 끈다.
    # pane-border-status 도 함께 — ~/.tmux.conf 가 pane 상단에 이름표를 또 그린다(-w = 창 옵션).
    return cmd + _session_opts(name)
