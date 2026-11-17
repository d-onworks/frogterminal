"""다른 PC 터미널의 파일 패널 — ssh 로 붙은 원격 폴더를 읽기 전용으로 본다.

세션이 ssh 로 다른 기계에 붙어 있고(sessions.session_remotes) 원격 폴더(@remote_path)를 알면, 파일 패널의
목록·읽기는 맥 디스크 대신 그 기계에 물어본다. 지금은 윈도우(OpenSSH + PowerShell)만 — 지금 쓰는 다른 PC 가
전부 윈도우다(srvpc·winpc·winpc2). 리눅스는 뒤로 미룬다.

잠그는 방식은 로컬(files.py)과 같은 잣대: 원격 폴더 아래만(`..`·절대경로 거부), 비밀파일 denylist 그대로,
크기 상한 그대로, 쓰기·새 폴더·업로드는 아예 없다(서버가 405). 원격 명령은 PowerShell -EncodedCommand 로
넘겨 cmd 따옴표 해석을 아예 타지 않고, 결과는 base64 로 받아 윈도우 콘솔 코드페이지(CP949)에 안 깨진다.
"""
import base64
import json
import re
import subprocess
from urllib.parse import unquote

from bridge import config, files, sessions

_HOST_RE = re.compile(r"^[\w.-]+$")           # ssh 별칭·호스트명·IP — 옵션처럼 생긴 값(-oProxyCommand=…)은 거부
_BAD_PART_CHARS = re.compile(r'[\\:*?"<>|\x00-\x1f]')   # 윈도우 경로 조각에 못 들어가는 글자 — 들어오면 조작 시도다
SSH_TIMEOUT = 25


class RemoteUnavailable(Exception): pass     # ssh 자체가 안 됐다(꺼짐·테일넷 끊김) — 502 로, 404 와 구분한다


def remote_of(project: str) -> dict | None:
    """이 프로젝트(세션)가 ssh 로 붙은 다른 PC 이고 원격 폴더를 알면 {"host","path"}, 아니면 None.

    로컬 프로젝트 폴더가 실재하면 그쪽이 우선이다 — 같은 이름의 맥 폴더가 있으면 원격이 아니라 그 폴더다.
    """
    if not sessions.is_valid_name(project):
        return None
    if sessions.project_dir(config.projects_root(), project) is not None:
        return None
    r = sessions.session_remotes().get(project)
    if not r or not r.get("path") or not r.get("host"):
        return None
    if not _HOST_RE.match(r["host"]) or r["host"].startswith("-"):
        return None
    return {"host": r["host"], "path": r["path"]}


def remote_path(base: str, rel: str) -> str:
    """웹이 준 상대경로(a/b/c.txt)를 원격 윈도우 절대경로로. 검증은 files.safe_path 와 같은 잣대.

    `..`·절대경로·드라이브·백슬래시·비밀파일 이름이 한 조각이라도 있으면 NotAllowed — 존재를 안 알린다(404).
    """
    rel = unquote(rel or "")
    if rel.startswith("/") or rel.startswith("\\"):
        raise files.NotAllowed(rel)
    parts = [p for p in rel.split("/") if p != ""]
    for p in parts:
        if p in (".", "..") or files._denied(p) or _BAD_PART_CHARS.search(p) or p != p.strip():
            raise files.NotAllowed(rel)
    base = base.rstrip("\\/")
    return base if not parts else base + "\\" + "\\".join(parts)


def _ps_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _run_ps(host: str, script: str) -> tuple[int, str]:
    """원격 PowerShell 을 ssh 로 돌린다. (종료코드, stdout). ssh 가 못 붙으면 RemoteUnavailable.

    ControlMaster 로 첫 접속 뒤 소켓을 2분 재사용한다 — 클릭마다 TCP·키교환을 다시 하면 1초씩 더 걸린다.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           "-o", "ControlMaster=auto", "-o", f"ControlPath={config.APP_DIR}/ssh-%C",
           "-o", "ControlPersist=120", "--", host,
           "powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    try:
        # 바이트로 받는다 — 윈도우 PowerShell 의 오류 문구는 CP949 라 text=True 면 디코딩에서 터진다(실측).
        # stdout 은 우리가 base64 만 쓰게 했으니 ASCII 다. 깨진 바이트는 뭉개고 결과 판정은 종료코드로 한다.
        out = subprocess.run(cmd, capture_output=True, timeout=SSH_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise RemoteUnavailable(host) from e
    if out.returncode == 255:                 # ssh 자체의 실패 코드 — 원격 스크립트의 종료코드가 아니다
        raise RemoteUnavailable(host)
    return out.returncode, out.stdout.decode("ascii", "replace").strip()


def _b64_json(text: str):
    try:
        return json.loads(base64.b64decode(text).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RemoteUnavailable("bad output") from e


_LIST_PS = """$ErrorActionPreference='Stop'
$d = Get-Item -LiteralPath {path} -Force
if (-not $d.PSIsContainer) {{ exit 3 }}
$items = @(Get-ChildItem -LiteralPath {path} -Force | ForEach-Object {{
  @{{ name = $_.Name; dir = [bool]$_.PSIsContainer; size = $(if ($_.PSIsContainer) {{ $null }} else {{ [long]$_.Length }}) }} }})
$json = ConvertTo-Json -InputObject $items -Compress -Depth 2
[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
"""

_READ_PS = """$ErrorActionPreference='Stop'
$f = Get-Item -LiteralPath {path} -Force
if ($f.PSIsContainer) {{ exit 3 }}
if ($f.Length -gt {max}) {{ exit 4 }}
$b = [IO.File]::ReadAllBytes($f.FullName)
$json = ConvertTo-Json -InputObject @{{ mtime = [string]$f.LastWriteTimeUtc.Ticks; size = [long]$f.Length; data = [Convert]::ToBase64String($b) }} -Compress
[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
"""


def list_dir(remote: dict, rel: str = "") -> list[dict]:
    """원격 폴더 목록 — files.list_dir 와 같은 모양({name, dir, size}), 폴더 먼저. 비밀파일은 목록에서 뺀다."""
    path = remote_path(remote["path"], rel)
    code, out = _run_ps(remote["host"], _LIST_PS.format(path=_ps_literal(path)))
    if code != 0:
        raise files.NotAllowed(rel)           # 없음·폴더 아님·권한 없음 — 전부 404 로 뭉갠다(로컬과 같은 원칙)
    raw = _b64_json(out)
    if isinstance(raw, dict):                 # ConvertTo-Json 은 항목이 하나면 배열을 풀어 버린다
        raw = [raw]
    entries = []
    for e in raw or []:
        name = str(e.get("name", ""))
        if not name or files._denied(name):
            continue
        is_dir = bool(e.get("dir"))
        entries.append({"name": name, "dir": is_dir, "size": None if is_dir else e.get("size")})
    entries.sort(key=lambda e: (not e["dir"], e["name"]))
    return entries


def read_text(remote: dict, rel: str) -> dict:
    """원격 텍스트 파일 — files.read_text 와 같은 모양({content, mtimeNs, size}). 바이너리·CP949 는 415."""
    path = remote_path(remote["path"], rel)
    code, out = _run_ps(remote["host"],
                        _READ_PS.format(path=_ps_literal(path), max=config.MAX_TEXT_FILE_BYTES))
    if code == 4:
        raise files.TooLarge(rel)
    if code != 0:
        raise files.NotAllowed(rel)
    got = _b64_json(out)
    try:
        data = base64.b64decode(got["data"])
    except (KeyError, TypeError, ValueError) as e:
        raise RemoteUnavailable("bad output") from e
    if len(data) > config.MAX_TEXT_FILE_BYTES:
        raise files.TooLarge(rel)
    if b"\x00" in data[:8192]:
        raise files.IsBinary(rel)
    try:
        content = data.decode("utf-8-sig")    # 윈도우 메모장이 남기는 BOM 은 벗긴다 — 글자가 아니다
    except UnicodeDecodeError as e:
        raise files.IsBinary(rel) from e      # CP949 파일 — 로컬과 같은 이유로 "여기선 못 고친다"
    return {"content": content, "mtimeNs": str(got.get("mtime", "")), "size": len(data)}

