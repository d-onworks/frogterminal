"""3D 모션 모드(사무실) 데이터 — 서브에이전트 세기·커밋 수·캐릭터 한 명분 묶기."""
import json
import os
import subprocess
import time

import pytest

from bridge import config, decisions, office, server

GOOD = {"Tailscale-User-Login": "tester@example.com", "Origin": "https://bridge.example.ts.net"}


def line(kind, stop=None, content=("text",)):
    return json.dumps({"type": kind, "message": {"stop_reason": stop,
                                                 "content": [{"type": c} for c in content]}})


def write_agent(folder, aid, lines, meta=None, age=0.0):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"agent-{aid}.jsonl"
    f.write_text("\n".join(lines) + "\n")
    if meta is not None:
        (folder / f"agent-{aid}.meta.json").write_text(json.dumps(meta))
    if age:
        t = time.time() - age
        os.utime(f, (t, t))
    return f


@pytest.fixture(autouse=True)
def _fresh():
    office.forget()
    yield
    office.forget()


# ---------- 서브에이전트가 아직 일하나 ----------
# 실측(2026-10-22, 최근 60개): 끝난 것은 전부 마지막 대화 줄이 assistant + end_turn 이었다.

def test_말을_마친_서브에이전트는_끝난_것(tmp_path):
    f = write_agent(tmp_path, "a1", [line("user"), line("assistant", "end_turn")])
    assert office.subagent_running(f) is False


@pytest.mark.parametrize("last", [
    line("assistant", "tool_use", ("tool_use",)),      # 도구를 부르는 중
    line("user", None, ("tool_result",)),               # 결과를 받고 생각하는 중
    line("assistant", None, ("thinking",)),             # 아직 쓰는 중
])
def test_도구를_부르거나_생각하는_서브에이전트는_일하는_중(tmp_path, last):
    f = write_agent(tmp_path, "a1", [line("user"), last])
    assert office.subagent_running(f) is True


def test_기록용_줄은_건너뛰고_마지막_대화_줄로_판정한다(tmp_path):
    """api-request 같은 기록 줄이 맨 끝에 붙어도 '끝남'을 놓치지 않는다."""
    f = write_agent(tmp_path, "a1", [line("assistant", "end_turn"), json.dumps({"type": "api-request"}),
                                     "{반쪽 JSON"])
    assert office.subagent_running(f) is False


def test_30분_넘게_조용한_서브에이전트는_끊긴_것(tmp_path):
    f = write_agent(tmp_path, "a1", [line("assistant", "tool_use", ("tool_use",))],
                    age=office.SUB_QUIET + 60)
    assert office.subagent_running(f) is False


def test_꼬리만_읽는다_큰_파일도_마지막_줄로(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "SUB_TAIL", 200)
    big = [line("user", None, ("text",)) + " " * 300 for _ in range(50)]
    f = write_agent(tmp_path, "a1", big + [line("assistant", "end_turn")])
    assert office.subagent_running(f) is False


def test_대화_옆_subagents_폴더에서_일하는_것만_출근순으로(tmp_path):
    transcript = tmp_path / "proj" / "sid-1.jsonl"
    transcript.parent.mkdir()
    transcript.write_text("")
    subs = tmp_path / "proj" / "sid-1" / "subagents"
    write_agent(subs, "first", [line("assistant", "tool_use", ("tool_use",))],
                meta={"agentType": "Explore", "description": "코드 찾기"})
    time.sleep(0.01)
    write_agent(subs, "done", [line("assistant", "end_turn")], meta={"agentType": "Plan"})
    write_agent(subs, "second", [line("user", None, ("tool_result",))])          # meta 없음도 버틴다
    got = office.running_subagents(str(transcript))
    assert [s["id"] for s in got] == ["first", "second"]
    assert got[0] == {"id": "first", "type": "Explore", "desc": "코드 찾기", "doing": ""}
    assert got[1]["type"] == ""


def test_subagents_폴더가_없으면_빈_줄(tmp_path):
    assert office.running_subagents(str(tmp_path / "없음.jsonl")) == []


# ---------- 커밋 수 — 꼬마 판정 ----------

def test_커밋_수를_세고_10분_캐시한다(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git[:3], "init", "-q"], check=True)
    for i in range(3):
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", f"c{i}"], check=True)
    assert office.commit_count(str(repo)) == 3
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "c3"], check=True)
    assert office.commit_count(str(repo)) == 3          # 캐시 — git 을 4초마다 두드리지 않는다
    office.forget()
    assert office.commit_count(str(repo)) == 4


def test_git_저장소가_아니거나_경로가_없으면_None(tmp_path):
    assert office.commit_count(str(tmp_path)) is None
    assert office.commit_count(None) is None


# ---------- 캐릭터 한 명분 묶기 ----------

def items(*names, **extra):
    return [{"name": n, "alive": True, "hidden": False, "project": True, "path": f"/p/{n}",
             "agent": None, "host": None, **extra.get(n, {})} for n in names]


def test_snapshot_사이드바와_같은_잣대로_켜진_것만(monkeypatch):
    monkeypatch.setattr(office, "commit_count", lambda path: 10)
    its = items("on", "dead", "hid", "notproj",
                dead={"alive": False}, hid={"hidden": True}, notproj={"project": False})
    got = office.snapshot(its, {"cards": [], "states": {}})
    assert [p["name"] for p in got["people"]] == ["on"]
    assert got["people"][0]["status"] == "on"            # 카드 없는 켜진 세션 = 쉬는 중


def test_snapshot_상태_결재문구_꼬마_서브에이전트(monkeypatch, tmp_path):
    counts = {"/p/big": 300, "/p/small": office.KID_COMMITS - 1}
    monkeypatch.setattr(office, "commit_count", lambda path: counts.get(path))
    transcript = tmp_path / "big.jsonl"
    write_agent(tmp_path / "big" / "subagents", "x1", [line("assistant", "tool_use", ("tool_use",))])
    ov = {
        "cards": [
            {"project": "small", "kind": "picker", "live": True,
             "question": "이   파일을\n지울까요?", "message": "기계 문구"},
            {"project": "small", "kind": "permission", "live": False, "message": "죽은 카드는 안 쓴다"},
        ],
        "states": {"big": {"status": "working", "pending": 0}, "small": {"status": "decision", "pending": 1}},
        "transcripts": {"big": [str(transcript)]},
    }
    big, small = office.snapshot(items("big", "small", big={"agent": "codex"}), ov)["people"]
    assert (big["status"], big["agent"], big["kid"], big["commits"]) == ("working", "codex", False, 300)
    assert big["subCount"] == 1 and big["subs"][0]["id"] == "x1"
    assert (small["status"], small["kid"], small["pending"]) == ("decision", True, 1)
    assert small["ask"] == "이 파일을 지울까요?"           # 공백·줄바꿈을 한 칸으로
    assert small["agent"] == "claude"                     # 표시 없는 세션 = 클로드


def test_snapshot_다른_PC_세션은_커밋을_세지_않는다(monkeypatch):
    monkeypatch.setattr(office, "commit_count", lambda path: pytest.fail("ssh 세션 폴더는 맥에 없다"))
    (p,) = office.snapshot(items("rproj", rproj={"host": "srvpc", "path": "C:\\rproj"}),
                           {"cards": [], "states": {}})["people"]
    assert p["commits"] is None and p["kid"] is False


def test_snapshot_서브에이전트가_많으면_줄은_상한까지_수는_그대로(monkeypatch, tmp_path):
    monkeypatch.setattr(office, "commit_count", lambda path: 100)
    subs = tmp_path / "t" / "subagents"
    for i in range(office.MAX_SUBS + 3):
        write_agent(subs, f"s{i:02d}", [line("user", None, ("tool_result",))])
    ov = {"cards": [], "states": {}, "transcripts": {"a": [str(tmp_path / "t.jsonl")]}}
    (p,) = office.snapshot(items("a"), ov)["people"]
    assert len(p["subs"]) == office.MAX_SUBS and p["subCount"] == office.MAX_SUBS + 3


# ---------- 결재 집계가 대화 파일 경로를 함께 준다 ----------

@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "decisions"
    d.mkdir()
    monkeypatch.setattr(config, "decisions_dir", lambda: d)
    monkeypatch.setattr(decisions, "capture_pane", lambda name: "")
    monkeypatch.setattr(decisions.sessions, "process_starts", lambda: {})
    monkeypatch.setattr(decisions, "boot_time", lambda: None)
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    return d


def test_overview_살아있는_카드의_대화_파일만_프로젝트별로(state_dir, tmp_path, monkeypatch):
    root = tmp_path / "claude-projects"
    (root / "-x").mkdir(parents=True)
    monkeypatch.setattr(config, "TRANSCRIPT_ROOT", root)
    inside = root / "-x" / "s1.jsonl"
    for sid, path, project in (("s1", inside, "alpha"), ("s2", tmp_path / "밖.jsonl", "alpha"),
                               ("s3", root / "-x" / "s3.jsonl", "beta")):     # beta = tmux 없음
        (state_dir / f"{sid}.json").write_text(json.dumps({
            "sessionId": sid, "kind": "working", "project": project, "cwd": "", "machine": "m",
            "message": "", "updatedAt": "2026-10-22T00:00:00+09:00", "inTmux": True,
            "transcriptPath": str(path)}))
    ov = decisions.overview()
    assert ov["transcripts"] == {"alpha": [str(inside.resolve())]}   # 루트 밖·죽은 프로젝트는 뺀다
    assert ov["states"]["alpha"] == {"status": "working", "pending": 0}   # 상태 모양은 그대로


# ---------- API ----------

async def test_api_office_정상경로(aiohttp_client, monkeypatch):
    from bridge import sessions
    monkeypatch.setattr(sessions, "merged_sessions", lambda: items("a"))
    monkeypatch.setattr(decisions, "overview", lambda: {"cards": [], "states": {}})
    monkeypatch.setattr(office, "commit_count", lambda path: 7)
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/office", headers=GOOD)
    assert res.status == 200
    (p,) = (await res.json())["people"]
    assert (p["name"], p["kid"], p["subs"], p["status"]) == ("a", True, [], "on")


async def test_api_office_와_사무실_그림은_인증이_필요하다(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    for path in ("/api/office", "/office/office.webp", "/office.js"):
        res = await client.get(path, allow_redirects=False)
        assert res.status in (302, 401, 403), path


async def test_사무실_그림은_webp_로_charset_없이(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/office/frog-key.webp", headers=GOOD)
    assert res.status == 200
    assert res.headers["Content-Type"] == "image/webp"
    assert (await res.read())[:4] == b"RIFF"


# ---------- 지금 하는 일·활동 로그(말풍선·오른쪽 로그) ----------
from bridge import office_activity  # noqa: E402


def tool(name, at, **inp):
    return json.dumps({"type": "assistant", "timestamp": at,
                       "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})


def say(text, at):
    return json.dumps({"type": "assistant", "timestamp": at,
                       "message": {"content": [{"type": "thinking", "thinking": "속생각"},
                                               {"type": "text", "text": text}]}})


@pytest.fixture(autouse=True)
def _fresh_activity():
    office_activity.forget()
    yield
    office_activity.forget()


@pytest.mark.parametrize("block, want", [
    ({"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b/office.js"}}, "📖 office.js 읽는 중"),
    ({"type": "tool_use", "name": "Edit", "input": {"file_path": "/a/app.js"}}, "✏️ app.js 고치는 중"),
    ({"type": "tool_use", "name": "Bash", "input": {"command": "TOKEN=xyz curl …", "description": "테스트 돌리기"}},
     "🔧 테스트 돌리기"),                                  # 설명이 있으면 명령 원문(비밀값이 섞일 수 있다)은 안 쓴다
    ({"type": "tool_use", "name": "Grep", "input": {"pattern": "officeOpen"}}, "🔍 'officeOpen' 찾는 중"),
    ({"type": "tool_use", "name": "WebFetch", "input": {"url": "https://code.claude.com/docs"}},
     "🌐 code.claude.com 읽는 중"),
    ({"type": "tool_use", "name": "Agent", "input": {"description": "그림 좌표 잡기"}}, "🐣 꼬마에게 맡김: 그림 좌표 잡기"),
    ({"type": "tool_use", "name": "mcp__blender__look", "input": {}}, "🔌 look 쓰는 중"),
    ({"type": "tool_use", "name": "Mystery", "input": "문자열 입력"}, "⚙️ Mystery 쓰는 중"),   # 모르는 모양도 버틴다
    ({"type": "text", "text": "  테스트가\n전부 통과했습니다  "}, "💬 테스트가 전부 통과했습니다"),
    ({"type": "text", "text": "## 현황\n**한 줄 요약:** `pytest` 통과"}, "💬 현황 한 줄 요약: pytest 통과"),
    ({"type": "thinking", "thinking": "x"}, None),
    ({"type": "text", "text": "   "}, None),
])
def test_도구_호출을_쉬운_말_한_줄로(block, want):
    assert office_activity.describe(block) == want


def test_activity_마지막이_지금_하는_일_로그는_최근_몇_줄(tmp_path):
    f = tmp_path / "t.jsonl"
    lines = [say("시작합니다", "2026-10-22T01:00:00Z")]
    lines += [tool("Read", f"2026-10-22T01:00:{i:02d}Z", file_path=f"/x/f{i}.py") for i in range(1, 12)]
    lines += [json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}}), "{깨진 줄"]
    f.write_text("\n".join(lines) + "\n")
    got = office_activity.activity(str(f))
    assert got["doing"] == "📖 f11.py 읽는 중"
    assert len(got["log"]) == office_activity.LOG_N and got["log"][-1]["at"] == "2026-10-22T01:00:11Z"


def test_activity_파일이_그대로면_다시_읽지_않는다(tmp_path, monkeypatch):
    f = tmp_path / "t.jsonl"
    f.write_text(tool("Read", "2026-10-22T01:00:00Z", file_path="/a.py") + "\n")
    office_activity.activity(str(f))
    monkeypatch.setattr(office_activity, "tail_entries", lambda *a: pytest.fail("캐시를 안 탔다"))
    assert office_activity.activity(str(f))["doing"] == "📖 a.py 읽는 중"


def test_activity_없는_파일은_빈_값():
    assert office_activity.activity("/없는/파일.jsonl") == {"doing": "", "log": []}


def test_snapshot_대화가_둘이면_최근_쪽이_지금_하는_일이고_로그는_합친다(monkeypatch, tmp_path):
    monkeypatch.setattr(office, "commit_count", lambda path: 100)
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    old.write_text(tool("Read", "2026-10-22T01:00:05Z", file_path="/old.py") + "\n")
    new.write_text(tool("Read", "2026-10-22T01:00:01Z", file_path="/first.py") + "\n"
                   + tool("Edit", "2026-10-22T01:00:09Z", file_path="/new.py") + "\n")
    ov = {"cards": [], "states": {}, "transcripts": {"a": [str(old), str(new)]}}
    (p,) = office.snapshot(items("a"), ov)["people"]
    assert p["doing"] == "✏️ new.py 고치는 중"         # 최근에 움직인 대화(new)의 마지막 줄
    assert [e["at"][-3:] for e in p["log"]] == ["01Z", "05Z", "09Z"]   # 두 대화를 시간순으로 합친다
    assert p["path"] == "/p/a" and p["host"] is None


def test_꼬마도_지금_하는_일을_말한다(tmp_path):
    transcript = tmp_path / "s.jsonl"
    write_agent(tmp_path / "s" / "subagents", "k1",
                [tool("Grep", "2026-10-22T01:00:00Z", pattern="seat")], meta={"description": "좌표"})
    (sub,) = office.running_subagents(str(transcript))
    assert sub["doing"] == "🔍 'seat' 찾는 중"
