"""설정 + 에이전트(클로드·코덱스) 연결 상태.

설정 값의 원본은 서버 파일 하나(~/.frogterminal/settings.json) — 폰과 맥이 같은 값을 본다.
프로젝트 루트 자체를 설정에 담으므로 settings.json은 루트 밖의 고정 위치여야 한다. 그렇지 않으면
재시작할 때 새 루트 안의 설정 파일을 어디서 찾아야 하는지 알 수 없는 순환이 생긴다.
실행 별칭만 그 값에서 파생한 관리 블록으로 ~/.zshrc에도 투영한다.
값은 화이트리스트로만 받는다: 여기 저장된 문자열이 tmux 명령으로 흘러가므로(sessions.attach_cmd)
임의 문자열을 받는 순간 설정 파일이 곧 원격 명령 실행이 된다 — fail-closed.

계정 상태는 '표시'만 한다. 로그인(OAuth)은 각 CLI 가 터미널에서 하는 일이고, 여기서는
누가 로그인돼 있는지 읽기 전용으로 보여준다. 토큰·비밀값은 절대 읽지도 내보내지도 않는다.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from bridge import config, sessions

# 새 세션을 열 때 시작할 것 — 키는 고정 목록. shell 은 아무것도 안 띄운다.
# 실행 명령은 commands 에 별칭 이름만 둔다. 대상은 아래 AGENTS 로 고정하고 ~/.zshrc 에 우리
# 블록을 만들어 새 셸과 frogterminal 세션이 같은 이름을 쓴다. 이 API 는 테일스케일 신원으로
# 잠겨 있지만 셸 파일에 쓰는 값이므로, 신원과 별개로 식별자 화이트리스트를 반드시 거친다.
# frogterminal 자체가 tmux·mdfind·launchd 에 의존하는 맥 전용 도구이고 세션도 zsh -lic 로
# 실행하므로, 사용자의 기본 셸이 달라도 관리 대상은 의도적으로 ~/.zshrc 하나로 고정한다.
# anti = 구글 Antigravity CLI(실행 파일 이름 agy). 키는 화면·설정·?agent= 에 쓰는 이름, 값이 실제 명령.
AGENTS = {"claude": "claude", "codex": "codex", "anti": "agy", "shell": None}
DEFAULTS = {"agent": "claude",               # 이 앱의 세션 = 클로드 세션이 기본 그림
            # bypass 기본 켬 — Orca 도 전 에이전트에 전권한 플래그를 기본 적용한다("세션이 곧
            # 작업 격리"라는 판단). 실사용도 늘 bypass 다. 설정에서 끌 수 있다.
            "bypass": True,                  # 클로드 --dangerously-skip-permissions · 코덱스 --yolo 로 시작
            "rc": False,                     # 클로드 Remote Control(claude.ai·폰 앱) 켜서 시작
            "cont": True,                    # 프로젝트의 직전 대화를 이어서 시작 — 아래 launch_command(--continue)
            "commands": {"claude": "", "codex": ""}}   # ""=기본 명령("claude"·"codex")

# 저장 가능한 루트는 사용자 홈의 자손만이다. 웹에서 이 값을 바꿀 수 있으므로 /, 홈 자체,
# /Users, /etc, /var, /System, /Applications 같은 넓은 경로가 파일 API의 기준선이 되면 안 된다.
# 홈 바로 아래 폴더(~/work)부터 허용하면 일반적인 프로젝트 모음은 쓰면서 맥 전체 노출은 막는다.
HOME_DIR = Path.home().resolve()

ZSHRC_FILE = Path.home() / ".zshrc"
ALIAS_BLOCK_START = "# >>> frogterminal aliases (자동 생성 — 이 블록은 설정 화면이 관리합니다) >>>"
ALIAS_BLOCK_END = "# <<< frogterminal aliases <<<"
_ALIAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")
# 로그인 셸의 뼈대·권한 상승·파일 변경·앱의 실제 실행 파일만 막는 최소 목록이다. 모든 설치
# 프로그램을 동적으로 막으면 사용자가 만든 새 약칭도 기기마다 달리 거부되므로 고정 목록을 쓴다.
PROTECTED_COMMANDS = frozenset({
    "agy", "alias", "bash", "cd", "claude", "codex", "command", "cp", "eval", "exec", "exit",
    "git", "kill", "ls", "mv", "rm", "sh", "source", "sudo", "tmux", "unalias", "zsh",
})
_LEGACY_COMMENT = "# frogterminal — 클로드 약칭"
_LEGACY_CLAUDE_ALIAS = 'alias cl="claude"'


class InvalidAlias(Exception):
    """셸에 안전하게 쓸 수 없는 별칭 이름 — 입력값과 사용자용 이유를 함께 보존한다."""

    def __init__(self, alias, reason: str):
        self.alias = alias if isinstance(alias, str) else ""
        self.reason = reason
        super().__init__(reason)


class InvalidProjectsRoot(Exception):
    """프로젝트 루트 검증 실패 — API가 사람 말과 안정적인 사유 코드를 함께 돌려준다."""

    def __init__(self, code: str, reason: str):
        self.code, self.reason = code, reason
        super().__init__(reason)


def validate_projects_root(raw) -> Path:
    """웹에서 받은 루트를 realpath까지 검증한다. 성공하면 정규화한 절대 Path를 돌려준다."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidProjectsRoot("empty", "프로젝트 폴더를 입력하세요")
    if "\x00" in raw or "\n" in raw or "\r" in raw or ".." in Path(raw).parts:
        raise InvalidProjectsRoot("invalid_path", "경로에 .., 줄바꿈 또는 널 문자를 넣을 수 없습니다")
    path = Path(raw)
    if not path.is_absolute():
        raise InvalidProjectsRoot("not_absolute", "절대경로로 입력하세요")
    if not path.exists():
        raise InvalidProjectsRoot("not_found", "폴더가 존재하지 않습니다")
    if not path.is_dir():
        raise InvalidProjectsRoot("not_directory", "디렉터리가 아닙니다")
    try:
        resolved = path.resolve(strict=True)          # 심볼릭 링크를 따라간 실제 기준선
    except OSError as e:
        raise InvalidProjectsRoot("not_found", "폴더가 존재하지 않습니다") from e
    if not resolved.is_dir():
        raise InvalidProjectsRoot("not_directory", "디렉터리가 아닙니다")
    try:
        resolved.relative_to(HOME_DIR)
    except ValueError as e:
        raise InvalidProjectsRoot(
            "too_broad", "홈 폴더보다 한 단계 이상 안쪽의 폴더를 선택하세요") from e
    if resolved == HOME_DIR:
        raise InvalidProjectsRoot("too_broad", "홈 폴더 자체는 선택할 수 없습니다")
    return resolved


def check_command(cmd: str) -> None:
    """실행 파일 존재 관문이 아니라, 고정 대상을 가리킬 별칭 *이름*을 검증한다.

    예전의 zsh ``type`` 실측은 별칭이 미리 있어야 저장되는 순환 관문이었다. 이제 저장 동작이
    별칭을 직접 만들기 때문에, 여기서는 셸 주입과 중요한 기존 명령 덮어쓰기만 fail-closed 한다.
    빈 값은 해당 에이전트 별칭을 없애고 기본 명령으로 돌아간다는 유효한 설정이다.
    """
    if not isinstance(cmd, str):
        raise InvalidAlias(cmd, "별칭은 문자열이어야 합니다")
    if not cmd:
        return
    if not _ALIAS_NAME.fullmatch(cmd):
        raise InvalidAlias(cmd, "영문자 또는 _로 시작하는 32자 이하 이름만 사용할 수 있습니다")
    if cmd in PROTECTED_COMMANDS:
        raise InvalidAlias(cmd, f"'{cmd}'은(는) 기존의 중요한 명령이라 덮어쓸 수 없습니다")


def _stored_alias(value) -> str:
    """손으로 고친 옛 settings.json 도 셸 명령으로 부활하지 않게, 유효한 이름만 읽는다."""
    try:
        check_command(value)
    except InvalidAlias:
        return ""
    return value


def _read_settings_data() -> dict:
    """고정 설정을 읽되, 첫 전환 전에는 예전 <프로젝트 루트>/_frogterminal 설정을 읽어 별칭 등을 보존한다."""
    paths = [config.SETTINGS_FILE]
    # 테스트가 SETTINGS_FILE을 임시 경로로 바꿨을 때 사용자의 실제 legacy 설정을 읽으면 안 된다.
    if config.SETTINGS_FILE == config.DEFAULT_SETTINGS_FILE and not config.SETTINGS_FILE.exists():
        paths.append(config.LEGACY_SETTINGS_FILE)
    for path in paths:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def load() -> dict:
    """설정 전체. 파일이 없거나 깨졌거나 모르는 값이면 그 항목만 기본값 — 설정 파일로 앱이 죽지 않는다."""
    data = _read_settings_data()
    commands = data.get("commands") if isinstance(data.get("commands"), dict) else {}
    # 켜짐/꺼짐 값: 파일에 '있으면' 그 값(불리언만 참으로), 없으면 기본값 — 기본이 True 인
    # bypass 를 data.get(...) is True 로 읽으면 파일 없는 첫 실행이 곧바로 False 가 된다.
    flag = lambda key: (data[key] is True) if key in data else DEFAULTS[key]
    try:
        root = validate_projects_root(data["projectsRoot"]) if data.get("projectsRoot") else \
            config.DEFAULT_PROJECTS_ROOT
    except InvalidProjectsRoot:
        root = config.DEFAULT_PROJECTS_ROOT       # 손으로 고친 위험 경로는 절대 활성화하지 않는다
    return {
        "projectsRoot": str(root),
        "agent": data.get("agent") if data.get("agent") in AGENTS else DEFAULTS["agent"],
        "bypass": flag("bypass"),
        "rc": flag("rc"),
        "cont": flag("cont"),
        "commands": {key: _stored_alias(commands.get(key)) for key in DEFAULTS["commands"]},
    }


def projects_root() -> Path:
    """config.projects_root()의 실제 설정 공급자. 저장 직후 다음 호출부터 새 값을 준다."""
    return Path(load()["projectsRoot"])


def save(patch: dict) -> dict:
    """부분 갱신 — 별칭 patch 는 settings.json 과 전용 zshrc 블록을 한 동작으로 바꾼다."""
    merged = load()
    if "projectsRoot" in patch:
        raw_root = patch["projectsRoot"]
        # 빈 값은 '설정 없음'과 같다 — bridge.json 의 기본 루트로 명시적으로 복귀.
        merged["projectsRoot"] = (str(config.DEFAULT_PROJECTS_ROOT)
                                  if isinstance(raw_root, str) and not raw_root.strip()
                                  else str(validate_projects_root(raw_root)))
    if "agent" in patch:
        if not isinstance(patch["agent"], str) or patch["agent"] not in AGENTS:
            raise ValueError("agent")             # 목록 밖·배열·객체 전부 400 (TypeError 로 500 나던 것을 막는다)
        merged["agent"] = patch["agent"]
    for key in ("bypass", "rc", "cont"):
        if key in patch:
            if not isinstance(patch[key], bool):
                raise ValueError(key)             # "true"·1·null 을 조용히 False 로 저장하지 않는다(코덱스 교차점검)
            merged[key] = patch[key]
    if isinstance(patch.get("commands"), dict):
        for key in DEFAULTS["commands"]:
            if key in patch["commands"]:
                alias = patch["commands"][key]
                check_command(alias)             # 셸 파일에 쓰기 전에 식별자·보호 명령을 막는다
                merged["commands"][key] = alias
        if merged["commands"]["claude"] and \
                merged["commands"]["claude"] == merged["commands"]["codex"]:
            raise InvalidAlias(merged["commands"]["claude"],
                               "클로드와 코덱스에는 서로 다른 별칭을 사용해야 합니다")
        original_zshrc = manage_aliases(merged["commands"])
    else:
        original_zshrc = None
    tmp = config.SETTINGS_FILE.with_name(config.SETTINGS_FILE.name + ".frogtmp")
    try:
        config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(merged, ensure_ascii=False))
        os.replace(tmp, config.SETTINGS_FILE)    # 같은 폴더 안 원자적 교체 (hidden 과 같은 방식)
    except OSError:
        tmp.unlink(missing_ok=True)
        if original_zshrc is not None:
            _restore_zshrc(*original_zshrc)      # 둘 중 zshrc 만 바뀐 채 남지 않게 되돌린다
        raise
    return merged


def _line_body(line: bytes) -> bytes:
    """줄끝만 떼어 마커를 비교한다 — 블록 밖 CRLF·바이트는 원문 그대로 둔다."""
    return line.rstrip(b"\r\n")


def _updated_zshrc(source: bytes, commands: dict[str, str]) -> bytes:
    """우리 마커 블록과 정확히 알려진 2026-08-15 옛 두 줄만 제거한 뒤 블록 하나를 넣는다."""
    start = ALIAS_BLOCK_START.encode()
    end = ALIAS_BLOCK_END.encode()
    legacy_comment = _LEGACY_COMMENT.encode()
    legacy_alias = _LEGACY_CLAUDE_ALIAS.encode()
    lines = source.splitlines(keepends=True)
    kept: list[bytes] = []
    insert_at: int | None = None
    i = 0
    while i < len(lines):
        body = _line_body(lines[i])
        if body == start:
            block_end = next((j for j in range(i + 1, len(lines))
                              if _line_body(lines[j]) == end), None)
            if block_end is None:
                raise OSError("frogterminal alias block end marker is missing")
            if insert_at is None:
                insert_at = len(kept)
            i = block_end + 1                    # 마커 사이는 내용이 무엇이든 우리 소유 — 통째 교체
            continue
        # 2026-08-15에 심은 정확한 '설명 주석 + cl 별칭' 쌍만 1회 마이그레이션한다.
        # 비슷한 사용자 alias 를 넓은 정규식으로 지우지 않는다(개인 zshrc 보존이 최우선).
        if (body == legacy_comment or body.startswith(legacy_comment + b" (")) and \
                i + 1 < len(lines) and \
                _line_body(lines[i + 1]) == legacy_alias:
            if insert_at is None:
                insert_at = len(kept)
            i += 2
            continue
        kept.append(lines[i])
        i += 1

    aliases = [(commands[agent], target) for agent, target in AGENTS.items()
               if target and commands.get(agent)]
    if aliases:
        block = (ALIAS_BLOCK_START + "\n" +
                 "".join(f'alias {name}="{target}"\n' for name, target in aliases) +
                 ALIAS_BLOCK_END + "\n").encode()
        at = len(kept) if insert_at is None else insert_at
        # 개행 없는 마지막 사용자 줄 뒤에 마커가 붙어 한 줄이 되지 않게 구분한다. 그 외 바이트와
        # 기존 줄끝은 손대지 않는다. 블록 자체는 항상 개행으로 끝나 다음 설정과 붙지 않는다.
        if at > 0 and not kept[at - 1].endswith((b"\n", b"\r")):
            kept[at - 1] += b"\n"
        kept.insert(at, block)
    return b"".join(kept)


def _atomic_replace(path: Path, data: bytes, mode: int | None = None) -> None:
    """같은 디렉터리 임시파일을 fsync 한 뒤 os.replace — 죽어도 반쪽 파일을 노출하지 않는다."""
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.frogterminal.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def manage_aliases(commands: dict[str, str], zshrc: Path | None = None):
    """~/.zshrc의 frogterminal 블록을 생성·교체하고, 롤백용 원본 정보를 돌려준다.

    테스트와 복구 도구는 zshrc 인자로 가짜 경로를 넣을 수 있다. 정상 API 경로는 맥 전용으로
    고정한 ZSHRC_FILE만 쓴다. 실제 변경이 있을 때 기존 파일은 먼저 .frogterminal.bak에 남긴다.
    """
    for name in commands.values():
        check_command(name)
    names = [name for name in commands.values() if name]
    if len(names) != len(set(names)):
        raise InvalidAlias(names[0], "클로드와 코덱스에는 서로 다른 별칭을 사용해야 합니다")
    path = zshrc or ZSHRC_FILE
    existed = path.exists()
    original = path.read_bytes() if existed else b""
    updated = _updated_zshrc(original, commands)
    if updated == original:
        return None
    mode = (path.stat().st_mode & 0o7777) if existed else 0o644
    if existed:
        backup = path.with_name(path.name + ".frogterminal.bak")
        _atomic_replace(backup, original, mode)     # 쓰기 직전 원본 한 벌 — 이전 백업은 최신 원본으로
    _atomic_replace(path, updated, mode)
    return path, existed, original, mode


def _restore_zshrc(path: Path, existed: bool, original: bytes, mode: int) -> None:
    """settings.json 저장 실패 시 별칭 파일도 저장 전 상태로 복구한다(백업은 그대로 보존)."""
    if existed:
        _atomic_replace(path, original, mode)
    else:
        path.unlink(missing_ok=True)


# ── 프로젝트마다 '하나의 대화' — 이어서 시작(cont) ──────────────────────────
# exit 로 나갔다 다시 떠도 하던 대화를 잇는다.
#
# 처음에는 세션 이름 → uuid5 로 ID 를 고정해 --session-id/--resume 로 이었다. 그런데
# **--remote-control 과 함께 쓰면 --session-id 가 무시된다**(2026-08-24 실측: 다른 프로젝트가
# `--session-id fe1b66f3` 로 떴는데 훅이 기록한 실제 대화는 ee70c8e8). 지정한 ID 에는 아무것도
# 안 쌓이니, 다음 실행의 --resume 은 그 빈·낡은 대화를 열었다 — "한참 전 대화로
# 계속 resume 된다"는 증상의 정체다(다른 프로젝트의 며칠 전 대화가 열리고 있었다).
#
# 그래서 클로드가 스스로 관리하는 --continue(그 폴더의 가장 최근 대화)로 간다. 우리가 ID 를
# 붙들지 않으므로 어긋날 것이 없다. 대신 같은 폴더를 두 창이 함께 이으면 서로 가로채므로,
# 이어가기는 **기본 세션에만** 건다 — 탭(base@N)은 '옆에서 따로 돌리는 자리'라 새 대화로 둔다
# (프로젝트당 하나의 대화).


def launch_command(agent: str, session: str = "", cfg: dict | None = None,
                   cwd: Path | None = None) -> str | None:
    """새 tmux 세션에서 돌릴 명령 한 줄. shell·모르는 에이전트는 None(=그냥 셸).

    클로드에는 설정에 따라 bypass permissions·Remote Control·이어서 시작 플래그를 붙인다.
    RC 세션 이름은 프로젝트명 — claude.ai·폰 앱 목록에서 어느 프로젝트인지 바로 보이게.
    cwd = 그 세션이 시작할 폴더(대화 파일이 거기 기준으로 저장된다). 모르면 이어가기는 건너뛴다.
    """
    if AGENTS.get(agent) is None:
        return None
    cfg = cfg or load()
    cmd = cfg["commands"].get(agent) or AGENTS[agent]
    if agent == "codex":
        # 코덱스의 전권한 = --yolo(= --dangerously-bypass-approvals-and-sandbox 의 별칭, 0.153 실측).
        # 클로드의 bypass 와 같은 스위치 하나로 간다 — "권한 확인 없이"라는 뜻이 둘 다 같다.
        return f"{cmd} --yolo" if cfg["bypass"] else cmd
    if agent == "anti":
        # Antigravity CLI(agy 1.4.0 실측)는 클로드와 같은 이름의 전권한 플래그를 쓴다. 이어가기(-c)는
        # 폴더 기준인지 확인되지 않아 붙이지 않는다 — 엉뚱한 프로젝트 대화를 이을 위험이 더 크다.
        return f"{cmd} --dangerously-skip-permissions" if cfg["bypass"] else cmd
    if agent != "claude":
        return cmd
    if cfg["bypass"]:
        cmd += " --dangerously-skip-permissions"
    if cfg["rc"]:
        cmd += f" --remote-control {session}" if session else " --remote-control"
    # 탭(base@N)은 같은 폴더라 --continue 가 기본 세션의 대화를 가로챈다 — 탭은 새 대화로 둔다.
    if not (cfg["cont"] and session and cwd) or sessions.base_of(session) != session:
        return cmd
    flag = "--continue"
    # `|| 원래명령` — 이어갈 대화가 아직 없거나(첫 실행) 파일이 손상됐을 때 맨 클로드로 물러선다. 이게 없으면 뜨자마자 죽고 재접속이 또 만드는 [exited] 루프가
    # 된다(2026-08-15 'cl' 별칭 실사고와 같은 계열). 정상 종료(exit 0)면 뒤는 실행되지 않는다.
    return f"{cmd} {flag} || {cmd}"


# ── 에이전트 연결 상태 ───────────────────────────────────────────────────────

# 브릿지는 launchd 가 띄워 PATH 가 앙상하다 — CLI 가 사는 표준 자리를 직접 보탠다.
# (tmux 쪽은 zsh -lc 로 로그인 셸 PATH 를 타므로 이 목록이 필요 없다 — sessions.attach_cmd)
_EXTRA_BIN = (Path.home() / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"))


def which(binary: str) -> str | None:
    if found := shutil.which(binary):
        return found
    for d in _EXTRA_BIN:
        p = d / binary
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _version(binary: str) -> str | None:      # 설치 확인을 겸한다 — 없으면 None
    resolved = which(binary)
    if not resolved:
        return None
    try:
        out = subprocess.run([resolved, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    line = out.stdout.strip().splitlines()
    return line[0][:80] if line else None


def _claude_account() -> dict:
    """~/.claude.json 의 oauthAccount 에서 '누구로 로그인돼 있나'만 — 토큰류는 이 파일을 안 거친다."""
    try:
        data = json.loads(config.CLAUDE_CONFIG.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    account = data.get("oauthAccount") if isinstance(data, dict) else None
    if not isinstance(account, dict):
        return {}
    out = {}
    for src, dst in (("emailAddress", "email"), ("organizationName", "org")):
        value = account.get(src)
        if isinstance(value, str) and value:
            out[dst] = value[:120]
    return out


_DOCTOR_AUTO_RE = re.compile(r"^Auto-updates:\s*(\S+)\s*$", re.MULTILINE)
_DOCTOR_CHANNEL_RE = re.compile(r"^Auto-update channel:\s*(\S+)\s*$", re.MULTILINE)
_DOCTOR_LAST_RE = re.compile(
    r"^Last update attempt:\s*(\S+)\s*(?:→|->)\s*(\S+)(?:\s+\((\d{4}-\d{2}-\d{2})\))?\s*$",
    re.MULTILINE)


def parse_claude_doctor(text: str) -> dict:
    """doctor의 자동 업데이트 세 줄만 읽는다. 모르는 형식이면 해당 설명을 만들지 않는다."""
    if not isinstance(text, str) or not (auto := _DOCTOR_AUTO_RE.search(text)):
        return {}
    result = {"autoUpdates": auto.group(1)[:20]}
    if channel := _DOCTOR_CHANNEL_RE.search(text):
        result["channel"] = channel.group(1)[:20]
    if last := _DOCTOR_LAST_RE.search(text):
        result["lastAttempt"] = {"status": last.group(1)[:20], "version": last.group(2)[:40]}
        if last.group(3):
            result["lastAttempt"]["date"] = last.group(3)
    return result


def _claude_doctor() -> dict:
    """고정 `claude doctor` 읽기 전용 조회. 실패하면 화면의 보조 한 줄만 생략한다."""
    resolved = which("claude")
    if not resolved:
        return {}
    try:
        out = subprocess.run([resolved, "doctor"], capture_output=True, text=True,
                             timeout=config.AGENT_DOCTOR_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_claude_doctor(f"{out.stdout}\n{out.stderr}") if out.returncode == 0 else {}


def probe() -> dict:
    """클로드·코덱스의 설치·버전·로그인 상태. 실패는 그 항목만 비운다 — 화면은 항상 그려진다."""
    claude = {"installed": False, "version": None, **_claude_account()}
    if version := _version("claude"):
        claude.update(installed=True, version=version)
        if update_status := _claude_doctor():
            claude["updateStatus"] = update_status
    codex = {"installed": False, "version": None,
             # auth.json 존재 = 로그인됨. 내용(토큰)은 열지 않는다 — 존재 여부만이 필요한 전부다.
             "loggedIn": config.CODEX_AUTH.is_file()}
    if version := _version("codex"):
        codex.update(installed=True, version=version)
    return {"claude": claude, "codex": codex}


_STATUS_CACHE: list = [0.0, None]             # [잰 시각, 결과] — --version 두 번이 1초쯤 든다


def status(now=time.monotonic) -> dict:
    at, cached = _STATUS_CACHE
    if cached is not None and now() - at < config.AGENT_STATUS_TTL:
        return cached
    result = probe()
    _STATUS_CACHE[:] = [now(), result]
    return result


def invalidate_status() -> None:
    """업데이트 직후 다음 요청이 옛 --version/doctor 결과를 60초 더 보이지 않게 한다."""
    _STATUS_CACHE[:] = [0.0, None]


# ── 클로드 사용량(레이트리밋) — Orca 의 상태바 차용 ─────────────────────────
# ★ cswap(claude-swap) 을 실행하지 않는다 — 파일만 읽는다(2026-10-14).
#   2026-10-13 05:25 브릿지의 5분 주기 `cswap status` 가 라이브 리프레시 토큰을 소비해 맥의 claude -p 크론이
#   4시간 전멸했다(cswap status 의 복구 분기가 백업 grant 를 POST 한다). 그래서 cswap 이 다시 살아나도 여기서는
#   절대 부르지 않는다. 원천은 cswap 이 써 두는 캐시 `~/.claude-swap-backup/cache/usage.json`(statusline.py 와
#   같은 파일) — 활성 계정은 실제 로그인(~/.claude.json 의 oauthAccount.emailAddress)으로 고른다.
#   캐시가 없거나·형식이 다르거나·너무 오래됐으면(USAGE_STALE_S) 빈 값 — 상태바가 그 칸을 접는다(거짓 숫자 금지).
USAGE_FILE = Path.home() / ".claude-swap-backup" / "cache" / "usage.json"
CLAUDE_JSON = Path.home() / ".claude.json"
USAGE_STALE_S = 3 * 3600
_OAUTH_EMAIL_RE = re.compile(r'"oauthAccount"\s*:\s*\{[^}]*?"emailAddress"\s*:\s*"([^"]+)"')


def _countdown(resets_at: str | None, now: float) -> str:
    """절대시각(resets_at, ISO) → "2h 39m"/"5d 18h". 캐시의 countdown 문자열은 수집 시점에 굳은 값이라 다시 센다."""
    if not resets_at:
        return ""
    try:
        from datetime import datetime
        left = int(datetime.fromisoformat(resets_at).timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        return ""
    if left <= 0:
        return "0m"
    d, h, m = left // 86400, left % 86400 // 3600, left % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m"


def _login_email(claude_json: Path | None = None) -> str:
    try:
        m = _OAUTH_EMAIL_RE.search((claude_json or CLAUDE_JSON).read_text(encoding="utf-8"))
    except OSError:
        return ""
    return m.group(1).strip().lower() if m else ""


def parse_usage(data, login_email: str = "", now: float | None = None) -> dict:
    """usage.json(schemaVersion 2) → {"account", "windows":[{name,pct,reset,left}]}. 못 읽으면 {}.

    계정은 로그인 이메일과 같은 것, 없으면 가장 최근에 받아온 것. 창구는 5h·7d·scoped(Fable 등) 순.
    """
    now = time.time() if now is None else now
    accounts = (data or {}).get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict) or not accounts:
        return {}
    acc = None
    if login_email:
        acc = next((a for a in accounts.values()
                    if isinstance(a, dict) and (a.get("email") or "").strip().lower() == login_email), None)
    if acc is None:
        acc = max((a for a in accounts.values() if isinstance(a, dict)),
                  key=lambda a: a.get("fetchedAt") or 0, default=None)
    if not acc:
        return {}
    fetched = acc.get("fetchedAt")
    if not isinstance(fetched, (int, float)) or now - fetched > USAGE_STALE_S:
        return {}                                     # 낡은 숫자를 지금 값인 양 보여 주지 않는다
    good = acc.get("lastGood") or {}
    if not isinstance(good, dict):
        return {}
    windows = []
    def add(name, w):
        if isinstance(w, dict) and isinstance(w.get("pct"), (int, float)):
            windows.append({"name": str(name)[:20], "pct": min(999, int(w["pct"])),
                            "reset": str(w.get("clock") or "")[:40],
                            "left": _countdown(w.get("resets_at"), now)[:40]})
    add("5h", good.get("five_hour"))
    add("7d", good.get("seven_day"))
    for sc in good.get("scoped") or []:
        if isinstance(sc, dict):
            add(sc.get("name") or "?", sc)
    return {"account": str(acc.get("email") or "")[:120], "windows": windows} if windows else {}


def probe_usage() -> dict:
    """캐시 파일 읽기만 — 어떤 명령도 실행하지 않는다(위 ★)."""
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parse_usage(data, _login_email())


_USAGE_CACHE: list = [0.0, None]


def usage(now=time.monotonic) -> dict:
    """5분 캐시 — 사용량은 클로드가 파일을 쓸 때만 바뀐다(Orca 도 실시간이 아니라고 명시)."""
    at, cached = _USAGE_CACHE
    if cached is not None and now() - at < 300:
        return cached
    result = probe_usage()
    _USAGE_CACHE[:] = [now(), result]
    return result
