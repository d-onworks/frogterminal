"""대화 기록 꼬리 → '지금 무엇을 하나' 한 줄 + 최근 활동 몇 줄 (3D 모션 모드 말풍선·활동 로그).

요청(2026-10-22): 일하는 캐릭터는 머리 위에 실제 일하는 내용을 띄우고, 오른쪽에 살아 움직이는
활동 로그를 단다. 클로드 대화 파일(jsonl)의 assistant 줄에 도구 호출(이름 + 입력)과 글이 남으므로,
그것을 "📖 office.js 읽는 중" 같은 쉬운 말로 바꾼다. 형식은 클로드 코드 내부 형식이라 바뀔 수 있다 —
모르는 모양은 조용히 건너뛰고, 모르는 도구는 이름만 말한다.

비밀값: Bash 는 사람이 읽으라고 쓴 description 을 먼저 쓴다(명령 원문에는 토큰이 섞일 수 있다).
명령 원문은 description 이 없을 때만 앞 40자를 쓴다. 화면은 테일넷 + 토큰 쿠키 뒤에서만 열린다.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

TAIL = 256 * 1024          # 꼬리만 — 실측 165MB 대화 파일도 있다
LOG_N = 8                  # 사람마다 넘기는 최근 활동 수
CACHE_MAX = 300            # 파일별 캐시 상한 — 끝난 세션 파일이 쌓여도 메모리가 자라지 않게

_cache: dict[str, tuple[float, int, dict]] = {}


def forget() -> None:
    _cache.clear()                            # 테스트 격리용


def tail_entries(path: Path, tail: int = TAIL) -> list[dict]:
    """끝에서 tail 바이트 안의 대화 줄(assistant/user)만 — 반쪽 줄·깨진 줄·기록용 줄은 뺀다."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - tail)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]                     # seek 지점의 첫 줄은 반쪽 JSON
    out = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") in ("assistant", "user"):
            out.append(entry)
    return out


def _one_line(text, n: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _name(path) -> str:
    return os.path.basename(str(path or "").rstrip("/")) or "파일"


def describe(block: dict) -> str | None:
    """도구 호출·글 한 덩어리 → 말풍선 한 줄. 생각(thinking)·빈 글은 None."""
    kind = block.get("type")
    if kind == "text":
        # 말풍선엔 마크다운 기호(**굵게**·## 제목·`코드`)가 그대로 보이면 지저분하다 — 글자만 남긴다
        text = _one_line(re.sub(r"\*\*|__|`+|^#+\s*|\n#+\s*", " ", str(block.get("text") or "")), 60)
        return f"💬 {text}" if text else None
    if kind != "tool_use":
        return None
    name = str(block.get("name") or "")
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    if name == "Read":
        return f"📖 {_name(inp.get('file_path'))} 읽는 중"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"✏️ {_name(inp.get('file_path') or inp.get('notebook_path'))} 고치는 중"
    if name == "Write":
        return f"📝 {_name(inp.get('file_path'))} 쓰는 중"
    if name == "Bash":
        desc = inp.get("description")
        return f"🔧 {_one_line(desc, 44)}" if desc else f"🔧 {_one_line(inp.get('command'), 40)}"
    if name == "Grep":
        return f"🔍 '{_one_line(inp.get('pattern'), 24)}' 찾는 중"
    if name == "Glob":
        return f"🔍 {_one_line(inp.get('pattern'), 28)} 파일 찾는 중"
    if name == "WebFetch":
        return f"🌐 {urlparse(str(inp.get('url') or '')).hostname or '웹'} 읽는 중"
    if name == "WebSearch":
        return f"🌐 '{_one_line(inp.get('query'), 24)}' 검색 중"
    if name in ("Agent", "Task"):
        return f"🐣 꼬마에게 맡김: {_one_line(inp.get('description'), 30)}"
    if name == "TodoWrite":
        return "🗒️ 할 일 정리 중"
    if name == "Skill":
        return f"📚 {_one_line(inp.get('skill'), 24)} 펼치는 중"
    if name.startswith("mcp__"):
        return f"🔌 {name.split('__')[-1]} 쓰는 중"
    return f"⚙️ {_one_line(name, 24)} 쓰는 중" if name else None


def activity(path_str: str) -> dict:
    """그 대화 파일의 지금 하는 일(doing)과 최근 활동(log: [{at, text}]) — 파일이 그대로면 캐시."""
    path = Path(path_str)
    try:
        st = path.stat()
    except OSError:
        return {"doing": "", "log": []}
    hit = _cache.get(str(path))
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    events = []
    for entry in tail_entries(path):
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            text = describe(block) if isinstance(block, dict) else None
            if text:
                events.append({"at": str(entry.get("timestamp") or ""), "text": text})
    result = {"doing": events[-1]["text"] if events else "", "log": events[-LOG_N:]}
    if len(_cache) >= CACHE_MAX:
        _cache.clear()
    _cache[str(path)] = (st.st_mtime, st.st_size, result)
    return result
