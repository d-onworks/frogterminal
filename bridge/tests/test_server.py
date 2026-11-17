"""통합 테스트 — 실제 tmux를 쓴다(맥 로컬 전제). 테스트 세션은 frogtest- 접두사만 만들고 반드시 지운다."""
import asyncio
import json
import subprocess
import uuid

import pytest
from aiohttp import WSMsgType, web

from bridge import config, server

GOOD = {
    "Tailscale-User-Login": "tester@example.com",
    "Origin": "https://bridge.example.ts.net",
}
TEST_SESSION = f"frogtest-{uuid.uuid4().hex[:6]}"
PNG = b"\x89PNG\r\n\x1a\n" + b"server-image"


@pytest.fixture
def tmux_session():
    subprocess.run(["tmux", "new-session", "-d", "-s", TEST_SESSION], check=True)
    yield TEST_SESSION
    subprocess.run(["tmux", "kill-session", "-t", TEST_SESSION], check=False)


async def wait_until_attached(name: str) -> None:
    """attach 클라이언트가 tmux 서버에 실제로 붙을 때까지 기다린다.

    pty는 우리가 써넣은 글자를 그대로 echo 하므로, WS 출력만 보고 붙었다고 판단하면 안 된다.
    """
    async with asyncio.timeout(15):
        while True:
            out = subprocess.run(["tmux", "list-clients", "-t", name],
                                 capture_output=True, text=True)
            if out.returncode == 0 and out.stdout.strip():
                return
            await asyncio.sleep(0.05)


@pytest.fixture
def dying_session():
    """테스트 도중 죽여도 되는 전용 세션 — 다른 테스트·다른 세션과 절대 겹치지 않는다."""
    name = f"frogtest-die-{uuid.uuid4().hex[:6]}"
    subprocess.run(["tmux", "new-session", "-d", "-s", name], check=True)
    yield name
    subprocess.run(["tmux", "kill-session", "-t", name], check=False)


LOGIN_ONLY = {"Tailscale-User-Login": "tester@example.com"}   # 같은 출처 요청 = Origin 없음


async def test_sessions_requires_auth(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/sessions")).status == 403                     # 헤더 없음
    assert (await client.get("/api/sessions", headers={**GOOD, "Origin": "https://evil.x"})).status == 403


async def test_login_required_regardless_of_origin(aiohttp_client):
    """로그인 헤더는 어떤 경우에도 면제되지 않는다 — Origin 이 허용목록이어도, 아예 없어도 403."""
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/sessions",
                             headers={"Origin": "https://bridge.example.ts.net"})).status == 403
    assert (await client.get("/")).status == 403                                 # Origin 없음 + 로그인 없음


VERCEL = {**LOGIN_ONLY, "Origin": "https://frogterminal.vercel.app"}


@pytest.mark.parametrize("path", ["/", "/api/sessions", "/ws/anything"])
async def test_vercel_origin_is_now_rejected(aiohttp_client, path):
    """문패(vercel.app)는 브릿지를 부르지 않으므로 허용목록에서 뺐다 — 이제 전부 403.

    남겨두면 그 공개 오리진의 페이지가 WS 로 완전한 대화형 셸을 얻는다(사파리·파이어폭스엔 LNA 가 없다).
    """
    client = await aiohttp_client(server.create_app())
    assert (await client.get(path, headers=VERCEL)).status == 403, path


async def test_same_host_origin_is_allowed(aiohttp_client, tmux_session):
    """접속한 주소 그대로의 Origin 은 통과 — 테일넷 IP(100.x)로 열어도 WS 가 붙어야 한다."""
    client = await aiohttp_client(server.create_app())
    host = f"{client.host}:{client.port}"
    resp = await client.get("/api/sessions",
                            headers={**LOGIN_ONLY, "Origin": f"https://{host}"})
    assert resp.status == 200


async def test_same_origin_request_without_origin_header_is_allowed(aiohttp_client, tmux_session):
    """같은 출처 GET 은 Origin 을 안 보낸다 — 이걸 403 으로 막으면 정적 페이지가 영영 안 열린다."""
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/sessions", headers=LOGIN_ONLY)).status == 200


async def test_sessions_lists(aiohttp_client, tmux_session):
    client = await aiohttp_client(server.create_app())
    resp = await client.get("/api/sessions", headers=GOOD)
    assert resp.status == 200
    names = [s["name"] for s in (await resp.json())["sessions"]]
    assert tmux_session in names


async def test_summary_요약(aiohttp_client, monkeypatch):
    from bridge import decisions, sessions
    monkeypatch.setattr(decisions, "overview", lambda: {
        "cards": [{"kind": "permission", "project": "a", "live": True},
                  {"kind": "done", "project": "b", "live": False}],
        "states": {}})
    monkeypatch.setattr(sessions, "merged_sessions",
                        lambda: [{"name": "a", "alive": True}, {"name": "b", "alive": False}])
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/summary", headers=GOOD)
    # done 은 결재 수에 안 들고(살아 있으면 waiting), 죽은 카드는 dead 로
    got = await res.json()
    assert got.pop("web") > 0                        # 새 배포 감지용 프론트 버전(mtime)
    assert got == {"alive": 1, "pending": 1, "waiting": 0, "dead": 1}


async def test_summary_죽은세션_유령카드는_결재수에_안잡힌다(aiohttp_client, monkeypatch):
    """훅이 SessionEnd를 못 남기고 죽은 세션의 카드는 TTL 동안 남는다(decisions.overview가
    live=False로 표시). 헤드라인 결재 수는 지금 답을 보낼 수 있는 것만 세야 한다 —
    안 그러면 사용자가 그 수를 보고 눌렀을 때 답이 404로 새는 최악의 경험이 된다."""
    from bridge import decisions, sessions
    monkeypatch.setattr(decisions, "overview", lambda: {
        "cards": [{"kind": "permission", "project": "dead", "live": False},
                  {"kind": "permission", "project": "alive", "live": True}],
        "states": {}})
    monkeypatch.setattr(sessions, "merged_sessions", lambda: [
        {"name": "dead", "alive": False}, {"name": "alive", "alive": True}])
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/summary", headers=GOOD)
    # 죽은 세션 카드는 결재 대신 dead 로
    got = await res.json()
    assert got.pop("web") > 0                        # 새 배포 감지용 프론트 버전(mtime)
    assert got == {"alive": 1, "pending": 1, "waiting": 0, "dead": 1}


async def test_sessions_상태_병합(aiohttp_client, monkeypatch):
    from bridge import decisions, sessions
    monkeypatch.setattr(sessions, "merged_sessions", lambda: [
        {"name": "a", "path": "/x/a", "alive": True},
        {"name": "b", "path": "/x/b", "alive": True},
        {"name": "c", "path": "/x/c", "alive": False}])
    monkeypatch.setattr(decisions, "overview", lambda: {
        "cards": [], "states": {"a": {"status": "decision", "pending": 2}}})
    client = await aiohttp_client(server.create_app())
    got = (await (await client.get("/api/sessions", headers=GOOD)).json())["sessions"]
    assert got[0] == {"name": "a", "path": "/x/a", "alive": True, "status": "decision", "pending": 2}
    assert got[1]["status"] == "on" and got[2]["status"] == "off"


@pytest.mark.parametrize("status", ["decision", "working"])
async def test_sessions_tmux밖에서_도는_세션도_상태를_보여준다(aiohttp_client, monkeypatch, status):
    """★ 클로드는 대부분 tmux 밖(Zed·iTerm)에서 돈다 — 그 세션이 지금 결재를 기다리는데도
    사이드바는 '○ 꺼짐'이라고 말했다(alive=tmux 존재 여부로 상태를 한 번 더 걸렀기 때문).

    죽은 세션이 빨간 배지를 달던 옛 문제는 이제 overview 가 프로세스로 실측해 걸러 준다
    (states 자체가 안 만들어진다 — test_overview_죽은세션_카드는_상태를_만들지_않는다).
    여기서 또 거르면 살아 있는 것까지 함께 지워진다.
    """
    from bridge import decisions, sessions
    monkeypatch.setattr(sessions, "merged_sessions",
                        lambda: [{"name": "zed에서도는중", "path": "/x/p", "alive": False}])
    monkeypatch.setattr(decisions, "overview", lambda: {
        "cards": [], "states": {"zed에서도는중": {"status": status, "pending": 1}}})
    client = await aiohttp_client(server.create_app())
    (got,) = (await (await client.get("/api/sessions", headers=GOOD)).json())["sessions"]
    assert got["status"] == status and got["pending"] == 1


async def test_settings_왕복과_검증(aiohttp_client, monkeypatch, tmp_path):
    from bridge import config as cfg
    from bridge import settings as st
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(st, "ZSHRC_FILE", tmp_path / ".zshrc")  # 실제 홈의 셸 설정은 절대 건드리지 않는다
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/settings", headers=GOOD)
    got = await res.json()
    assert got["agents"] == ["claude", "codex", "anti", "shell"]
    assert got["settings"] == {"projectsRoot": str(config.DEFAULT_PROJECTS_ROOT),
                               "agent": "claude", "bypass": True, "rc": False, "cont": True,
                               "commands": {"claude": "", "codex": ""}}   # bypass·이어서 시작 기본 켬
    res = await client.post("/api/settings", headers=GOOD,
                            json={"agent": "codex", "bypass": True,
                                  "commands": {"claude": "cl"}, "잡키": 1})
    merged = (await res.json())["settings"]
    assert res.status == 200
    assert merged["agent"] == "codex" and merged["bypass"] is True
    assert merged["commands"]["claude"] == "cl"
    assert 'alias cl="claude"' in st.ZSHRC_FILE.read_text()
    res = await client.post("/api/settings", headers=GOOD, json={"commands": {"claude": "rm"}})
    assert res.status == 400
    assert (await res.json())["error"] == "invalid alias"
    # 에이전트 목록 밖 — 400 (임의 값이 설정에 박히지 않게)
    res = await client.post("/api/settings", headers=GOOD, json={"agent": "zsh -c evil"})
    assert res.status == 400
    res = await client.post("/api/settings", headers=GOOD, data="배열아님")
    assert res.status == 400
    assert (await (await client.get("/api/settings", headers=GOOD)).json())["settings"]["agent"] == "codex"


@pytest.fixture
def root_settings_env(tmp_path, monkeypatch):
    """루트 API 공격 재현용 임시 홈 — 실제 ~/.frogterminal과 ~/work은 건드리지 않는다."""
    from bridge import decisions, sessions, settings as st
    home = tmp_path / "home"
    old = home / "work"
    old.mkdir(parents=True)
    (old / "old-project").mkdir()
    (old / "old-project" / "CLAUDE.md").write_text("# old")
    monkeypatch.setattr(st, "HOME_DIR", home.resolve())
    monkeypatch.setattr(config, "DEFAULT_PROJECTS_ROOT", old)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "app" / "settings.json")
    monkeypatch.setattr(st, "ZSHRC_FILE", tmp_path / ".zshrc")
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(decisions, "overview", lambda: {"cards": [], "states": {}})
    return home, old


async def test_공격재현_루트를_슬래시나_홈으로_바꾸면_실제요청이_400(
        aiohttp_client, root_settings_env):
    home, _ = root_settings_env
    client = await aiohttp_client(server.create_app())
    for raw in ("/", str(home)):
        res = await client.post("/api/settings", headers=GOOD, json={"projectsRoot": raw})
        body = await res.json()
        assert res.status == 400 and body["error"] == "invalid projects root"
        assert body["code"] == "too_broad"


@pytest.mark.parametrize("kind", ["not_found", "not_directory"])
async def test_루트_저장실패는_사람말_사유를_돌려준다(
        aiohttp_client, root_settings_env, kind):
    home, _ = root_settings_env
    target = home / ("없는폴더" if kind == "not_found" else "파일.txt")
    if kind == "not_directory":
        target.write_text("file")
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/settings", headers=GOOD, json={"projectsRoot": str(target)})
    body = await res.json()
    assert res.status == 400 and body["code"] == kind
    assert body["reason"] == ("폴더가 존재하지 않습니다" if kind == "not_found" else "디렉터리가 아닙니다")


async def test_공격재현_루트변경뒤_dotdot_파일읽기는_실제요청이_404(
        aiohttp_client, root_settings_env):
    home, old = root_settings_env
    root = home / "workspaces"
    project = root / "safe"
    project.mkdir(parents=True)
    (project / "AGENTS.md").write_text("# safe")
    (root / "outside.txt").write_text("프로젝트 밖 비밀")
    client = await aiohttp_client(server.create_app())
    saved = await client.post("/api/settings", headers=GOOD, json={"projectsRoot": str(root)})
    assert saved.status == 200
    assert config.projects_root() == root.resolve()                 # 재시작 없이 즉시 바뀐다
    names = [s["name"] for s in (await (await client.get(
        "/api/sessions", headers=GOOD)).json())["sessions"]]
    assert names == ["safe"] and "old-project" not in names
    escaped = await client.get("/api/file", headers=GOOD,
                               params={"project": "safe", "path": "../outside.txt"})
    assert escaped.status == 404


async def test_공격재현_루트밖_심링크_프로젝트는_실제요청이_404(
        aiohttp_client, root_settings_env):
    home, _ = root_settings_env
    root = home / "workspaces"
    outside = home / "outside-project"
    root.mkdir(); outside.mkdir()
    (outside / "CLAUDE.md").write_text("# outside")
    (outside / "secret.txt").write_text("secret")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    client = await aiohttp_client(server.create_app())
    assert (await client.post("/api/settings", headers=GOOD,
                              json={"projectsRoot": str(root)})).status == 200
    res = await client.get("/api/file", headers=GOOD,
                           params={"project": "escape", "path": "secret.txt"})
    assert res.status == 404


async def test_agents_상태는_읽기전용_요약만(aiohttp_client, monkeypatch):
    from bridge import agent_updates as updates, settings as st
    monkeypatch.setattr(st, "status", lambda: {"claude": {"installed": True}})
    async def no_network():
        return {"claude": None, "codex": None}
    monkeypatch.setattr(updates, "latest_versions", no_network)
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/agents", headers=GOOD)
    assert await res.json() == {"agents": {"claude": {"installed": True}}}


async def test_agent_update_API는_인증뒤_고정명령만_가짜_subprocess로_실행한다(
        aiohttp_client, monkeypatch, tmp_path):
    """진짜 CLI는 절대 부르지 않는다. 별칭·본문 없이 코드의 [resolved, update]만 호출되는 계약."""
    from bridge import agent_updates as updates
    calls = []
    versions = iter(["codex-cli 0.147.0", "codex-cli 0.148.0"])

    class Done:
        returncode = 0
        stdout = "Update ran successfully! Please restart Codex."
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Done()

    monkeypatch.setattr(updates.settings, "which", lambda name: "/fixed/bin/codex")
    monkeypatch.setattr(updates.settings, "_version", lambda name: next(versions))
    monkeypatch.setattr(updates.settings, "invalidate_status", lambda: None)
    monkeypatch.setattr(updates, "invalidate_latest", lambda: None)
    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    monkeypatch.setattr(server.sessions, "list_tmux_sessions",
                        lambda: ["one", "two", config.MONITOR_SESSION])
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())

    denied = await client.post("/api/agents/codex/update")
    assert denied.status == 403 and calls == []
    res = await client.post("/api/agents/codex/update", headers=GOOD)
    body = await res.json()
    assert res.status == 200
    assert body["success"] is True and body["updated"] is True
    assert body["beforeVersion"] == "codex-cli 0.147.0"
    assert body["afterVersion"] == "codex-cli 0.148.0"
    assert body["activeSessions"] == 2
    assert calls == [(["/fixed/bin/codex", "update"], {
        "capture_output": True, "text": True, "timeout": config.AGENT_UPDATE_TIMEOUT})]
    assert "shell" not in calls[0][1]                # shell=True를 우회로 넣지 않는다


async def test_agent_update_API는_목록밖_에이전트를_명령전에_거부한다(
        aiohttp_client, monkeypatch):
    from bridge import agent_updates as updates
    called = []
    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: called.append(a))
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/agents/not-user-input/update", headers=GOOD)
    assert res.status == 400 and called == []


async def test_open_은_검증된_경로만_화이트리스트_앱으로(aiohttp_client, monkeypatch, tmp_path):
    """'맥에서 열기' — 경로는 읽기와 같은 한 겹(files)으로 검증되고, 앱은 고정 목록만.
    임의 앱 문자열이 open 인자로 흘러가면 그대로 명령 주입이다."""
    from bridge import files, server as sv
    target = tmp_path / "그림.png"
    target.write_bytes(b"\x89PNG")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        class R: returncode = 0
        return R()

    monkeypatch.setattr(files, "resolve_existing",
                        lambda project, rel: target if rel == "docs/그림.png" else
                        (_ for _ in ()).throw(files.NotAllowed(rel)))
    monkeypatch.setattr(sv.subprocess, "run", fake_run)
    monkeypatch.setattr(sv, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    ok = await client.post("/api/open", headers=GOOD,
                           json={"project": "wiki", "path": "docs/그림.png", "app": "chrome"})
    assert ok.status == 200
    assert calls == [["open", "-a", "Google Chrome", str(target)]]
    # 기본 앱·파인더 보기
    await client.post("/api/open", headers=GOOD, json={"project": "wiki", "path": "docs/그림.png"})
    assert calls[-1] == ["open", str(target)]
    await client.post("/api/open", headers=GOOD,
                      json={"project": "wiki", "path": "docs/그림.png", "app": "finder"})
    assert calls[-1] == ["open", "-R", str(target)]
    # 목록 밖 앱 = 400, 검증 실패 경로 = 404 — open 은 아예 안 불린다
    n = len(calls)
    assert (await client.post("/api/open", headers=GOOD,
            json={"project": "wiki", "path": "docs/그림.png", "app": "Terminal"})).status == 400
    assert (await client.post("/api/open", headers=GOOD,
            json={"project": "wiki", "path": "../탈출"})).status == 404
    assert len(calls) == n


async def test_mkdir_은_생성_중복_검증실패를_구분한다(aiohttp_client, monkeypatch, tmp_path):
    """파일 패널의 새 폴더 — 200 생성 / 409 중복 / 404 검증실패(밖·비밀이름은 뭉갠다)."""
    from bridge import config as cf, files, server as sv
    root = tmp_path / "work"
    (root / "alpha").mkdir(parents=True)
    monkeypatch.setattr(cf, "projects_root", lambda: root)
    monkeypatch.setattr(files.sessions, "is_valid_session", lambda n: n == "alpha")
    monkeypatch.setattr(sv, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    ok = await client.post("/api/mkdir", headers=GOOD, json={"project": "alpha", "path": "새폴더"})
    assert ok.status == 200 and (root / "alpha" / "새폴더").is_dir()
    assert (await client.post("/api/mkdir", headers=GOOD,
            json={"project": "alpha", "path": "새폴더"})).status == 409
    assert (await client.post("/api/mkdir", headers=GOOD,
            json={"project": "alpha", "path": "../탈출"})).status == 404
    assert (await client.post("/api/mkdir", headers=GOOD,
            json={"project": "alpha", "path": ""})).status == 404
    assert not (tmp_path / "탈출").exists()


async def test_decision_answer_화면이_넘어갔으면_409(aiohttp_client, monkeypatch):
    """피커가 사라진 화면(또는 답을 기다린 적 없는 빈 셸)에 번호를 밀어넣지 않는다."""
    from bridge import decisions
    def 넘어감(p, t, v):
        raise decisions.NoPicker(p)
    monkeypatch.setattr(decisions, "send_answer", 넘어감)
    monkeypatch.setattr(decisions, "clear_card", lambda sid: pytest.fail("카드를 지우면 안 된다"))
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/decisions/answer", headers=GOOD,
                            json={"project": "a", "sessionId": "s1", "type": "option", "value": "1"})
    assert res.status == 409


async def test_decisions_정상경로(aiohttp_client, monkeypatch):
    """인증 403 테스트만 있고 정상 응답 본문을 확인하는 테스트가 없었다 — 프로덕션 엔드포인트가
    무검증으로 나가는 구멍을 막는다."""
    from bridge import decisions
    monkeypatch.setattr(decisions, "overview", lambda: {
        "cards": [{"kind": "permission", "project": "a", "live": True}], "states": {}})
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/decisions", headers=GOOD)
    assert res.status == 200
    assert await res.json() == {"decisions": [{"kind": "permission", "project": "a", "live": True}]}


async def test_decision_answer_전송_및_카드정리(aiohttp_client, monkeypatch):
    from bridge import decisions
    sent, cleared = [], []
    monkeypatch.setattr(decisions, "send_answer", lambda p, t, v: sent.append((p, t, v)))
    monkeypatch.setattr(decisions, "clear_card", lambda sid: cleared.append(sid))
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/decisions/answer", headers=GOOD,
                            json={"project": "a", "sessionId": "s1", "type": "option", "value": "1"})
    assert res.status == 200 and sent == [("a", "option", "1")] and cleared == ["s1"]


async def test_decision_answer_죽은세션은_404(aiohttp_client, monkeypatch):
    from bridge import decisions
    def boom(p, t, v):
        raise LookupError(p)
    monkeypatch.setattr(decisions, "send_answer", boom)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/decisions/answer", headers=GOOD,
                            json={"project": "x", "type": "text", "value": "hi"})
    assert res.status == 404


@pytest.mark.parametrize("raw", ["[1,2,3]", '"x"', "42", "null"])
async def test_decision_answer_객체아닌_json은_400(aiohttp_client, raw):
    """문법은 맞지만 객체가 아닌 JSON(배열·문자열·숫자·null)이 오면 body.get(...)에서
    AttributeError가 나 500으로 새면 안 된다 — 400으로 닫는다."""
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/decisions/answer",
                            headers={**GOOD, "Content-Type": "application/json"}, data=raw)
    assert res.status == 400


async def test_결재집계는_이벤트루프_밖에서_한_주기에_한번만(aiohttp_client, monkeypatch):
    """★ overview() 는 살아있는 세션마다 capture-pane 을 블로킹으로 돈다(실측 4.3ms/세션).

    세션 15개면 1회 65ms. 데스크톱은 7초마다 refreshSummary+loadSessions 로 2~3회 부르므로
    7초마다 140~200ms 동안 WS 펌프와 pty 읽기가 멈춘다(tmux 가 늦으면 timeout=5 만큼 언다).
    → ①스레드로 빼 루프를 놓아주고 ②한 폴링 주기의 중복 호출은 캐시로 접는다.
    """
    import threading
    from bridge import decisions, sessions
    쓰레드 = []
    def 집계():
        쓰레드.append(threading.current_thread())
        return {"cards": [], "states": {}}
    monkeypatch.setattr(decisions, "overview", 집계)
    monkeypatch.setattr(sessions, "merged_sessions",
                        lambda: 쓰레드.append(threading.current_thread()) or [])
    client = await aiohttp_client(server.create_app())
    for path in ("/api/summary", "/api/sessions", "/api/decisions"):
        assert (await client.get(path, headers=GOOD)).status == 200, path
    assert 쓰레드, "집계가 아예 안 돌았다"
    assert [t for t in 쓰레드 if t is threading.main_thread()] == []   # 루프를 막지 않았다
    assert len([t for t in 쓰레드 if t]) == 3       # overview 1회 + merged_sessions 2회


async def test_답을_보낸_직후에는_집계를_다시_돈다(aiohttp_client, monkeypatch, tmp_path):
    """캐시가 그대로면 '보냈는데 카드가 안 사라진다' → 사용자가 다시 누르게 된다."""
    from bridge import decisions
    횟수 = []
    monkeypatch.setattr(decisions, "overview",
                        lambda: (횟수.append(1), {"cards": [], "states": {}})[1])
    monkeypatch.setattr(decisions, "send_answer", lambda p, t, v: None)
    monkeypatch.setattr(decisions, "clear_card", lambda sid: None)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    await client.get("/api/decisions", headers=GOOD)
    await client.get("/api/decisions", headers=GOOD)
    assert len(횟수) == 1                            # 같은 주기 안 — 캐시가 접는다
    res = await client.post("/api/decisions/answer", headers=GOOD,
                            json={"project": "a", "sessionId": "s1", "type": "text", "value": "네"})
    assert res.status == 200
    await client.get("/api/decisions", headers=GOOD)
    assert len(횟수) == 2                            # 답 뒤에는 곧바로 다시 본다


async def test_결재_라우트도_인증_필수(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    for path in ("/api/summary", "/api/decisions"):
        assert (await client.get(path)).status == 403


async def test_decision_감사로그_개행주입_차단(aiohttp_client, monkeypatch, tmp_path):
    """filewrite 는 project 를 !r 로 쓰는데 decision 만 날것이었다 — 두 핸들러의 잣대를 맞춘다."""
    from bridge import decisions
    monkeypatch.setattr(decisions, "send_answer", lambda p, t, v: None)
    monkeypatch.setattr(decisions, "clear_card", lambda sid: None)
    audit = tmp_path / "audit.log"
    monkeypatch.setattr(server, "audit_path", lambda: audit)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/decisions/answer", headers=GOOD, json={
        "project": "a\n2026-08-05T00:00:00+09:00 decision user=사용자 project=위조 type=option",
        "sessionId": "s1", "type": "text", "value": "네"})
    assert res.status == 200
    assert len(audit.read_text().splitlines()) == 1, audit.read_text()


@pytest.mark.parametrize("path", ["/api/sessions", "/api/summary", "/api/decisions"])
async def test_api응답에도_보안헤더가_붙는다(aiohttp_client, path, monkeypatch):
    """정적 파일에만 붙어 있었다 — JSON 응답도 nosniff·프레임 차단을 받아야 한다."""
    from bridge import decisions, sessions
    monkeypatch.setattr(decisions, "overview", lambda: {"cards": [], "states": {}})
    monkeypatch.setattr(sessions, "merged_sessions", lambda: [])
    client = await aiohttp_client(server.create_app())
    resp = await client.get(path, headers=GOOD)
    assert resp.status == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


async def test_거부응답에도_보안헤더가_붙는다(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    resp = await client.get("/")                    # 로그인 헤더 없음 — 인증 미들웨어가 바로 끊는다
    assert resp.status == 403
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


async def test_upload_saves_and_returns_path(aiohttp_client, tmp_path, monkeypatch, tmux_session):
    monkeypatch.setattr(config, "upload_root", lambda: tmp_path)
    client = await aiohttp_client(server.create_app())
    resp = await client.post(f"/api/upload?project={tmux_session}", headers=GOOD,
                             data={"file": open(__file__, "rb")})
    assert resp.status == 200
    saved = (await resp.json())["path"]
    assert saved.startswith(str(tmp_path)) and tmux_session in saved


async def test_upload_rejects_unknown_project(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "upload_root", lambda: tmp_path)
    client = await aiohttp_client(server.create_app())
    resp = await client.post("/api/upload?project=../etc", headers=GOOD,
                             data={"file": open(__file__, "rb")})
    assert resp.status == 400


async def test_upload_rejects_oversize_and_leaves_no_partial(aiohttp_client, tmp_path, monkeypatch,
                                                            tmux_session):
    """상한 초과는 413이고, 쓰다 만 조각 파일이 남으면 안 된다."""
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(config, "upload_root", lambda: upload_root)
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1024)      # 50MB 대신 1KB로 상한 확인
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 5000)

    client = await aiohttp_client(server.create_app())
    resp = await client.post(f"/api/upload?project={tmux_session}", headers=GOOD,
                             data={"file": open(big, "rb")})
    assert resp.status == 413
    assert [p for p in upload_root.rglob("*") if p.is_file()] == []   # 잔여 조각 0


async def test_ws_roundtrip_resize_and_survival(aiohttp_client, tmux_session):
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{tmux_session}", headers=GOOD)
    ws_marker = f"MARK_{uuid.uuid4().hex[:6]}"
    await ws.send_str(json.dumps({"type": "resize", "cols": 123, "rows": 40}))
    await ws.send_bytes(f"echo {ws_marker}\n".encode())

    collected = b""
    async with asyncio.timeout(15):
        while ws_marker.encode() not in collected.replace(b"\r", b""):
            msg = await ws.receive()
            assert msg.type in (WSMsgType.BINARY, WSMsgType.TEXT), msg
            collected += msg.data if isinstance(msg.data, bytes) else msg.data.encode()
    await ws.close()

    # 스파이크 4: 브릿지 연결이 끊겨도 tmux 세션은 살아 있다
    alive = subprocess.run(["tmux", "has-session", "-t", tmux_session])
    assert alive.returncode == 0


async def test_ws_survives_malformed_resize(aiohttp_client, tmux_session):
    """망가진 resize(키 없음·숫자 아님·범위 초과)가 와도 연결이 죽지 않고 왕복이 계속돼야 한다."""
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{tmux_session}", headers=GOOD)
    for bad in ({"type": "resize", "rows": 40},               # cols 없음 → KeyError
                {"type": "resize", "cols": "abc", "rows": 40},  # 숫자 아님 → ValueError
                {"type": "resize", "cols": None, "rows": 40},   # None → TypeError
                {"type": "resize", "cols": 999999, "rows": 40}):  # H 범위 초과 → 클램프
        await ws.send_str(json.dumps(bad))

    marker = f"MARK_{uuid.uuid4().hex[:6]}"                   # 연결이 살아 있으면 에코가 돌아온다
    await ws.send_bytes(f"echo {marker}\n".encode())
    collected = b""
    async with asyncio.timeout(15):
        while marker.encode() not in collected.replace(b"\r", b""):
            msg = await ws.receive()
            assert msg.type in (WSMsgType.BINARY, WSMsgType.TEXT), msg
            collected += msg.data if isinstance(msg.data, bytes) else msg.data.encode()
    await ws.close()


async def test_ws_closes_and_stops_reading_when_child_dies(aiohttp_client, dying_session,
                                                          tmp_path, monkeypatch):
    """자식(tmux attach)이 먼저 죽는 경로 — pty EOF에서 reader를 떼지 않으면 콜백이 폭주한다."""
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    loop = asyncio.get_running_loop()
    real_add, real_remove = loop.add_reader, loop.remove_reader
    counted = {"fired": 0, "removed": 0}

    def spy_add(fd, cb, *args):                  # 콜백을 감싸 실제 발화 횟수를 센다
        def wrapped():
            counted["fired"] += 1
            return cb()
        real_add(fd, wrapped, *args)

    def spy_remove(fd):
        counted["removed"] += 1
        return real_remove(fd)

    monkeypatch.setattr(loop, "add_reader", spy_add, raising=False)
    monkeypatch.setattr(loop, "remove_reader", spy_remove, raising=False)

    # 느린 회선 흉내 — 게이트가 열릴 때까지 서버의 전송이 막힌다.
    # 전송이 막혀 있어야 "자식이 죽었는데도 reader를 떼지 않는" 결함이 드러난다.
    gate = asyncio.Event()
    real_send = web.WebSocketResponse.send_bytes

    async def gated_send(self, data, *args, **kwargs):
        await gate.wait()
        return await real_send(self, data, *args, **kwargs)

    monkeypatch.setattr(web.WebSocketResponse, "send_bytes", gated_send)

    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{dying_session}", headers=GOOD)
    await wait_until_attached(dying_session)     # 붙기 전에 죽이면 경합이라 테스트가 흔들린다

    subprocess.run(["tmux", "kill-session", "-t", dying_session], check=True)   # 자식이 먼저 죽는다
    fired_at_death = counted["fired"]
    await asyncio.sleep(1.0)                     # 전송이 막힌 채로 1초 버틴다
    after_death = counted["fired"] - fired_at_death

    # ② 자식이 죽은 뒤로는 콜백이 멈춰야 한다(고치기 전엔 1초에 수십만 회 헛돌며 큐를 채운다)
    assert after_death < 100, {"after_death": after_death, **counted}
    assert counted["removed"] >= 1, counted      # EOF에서 reader를 뗐다

    gate.set()                                   # 회선이 뚫리면
    async with asyncio.timeout(15):              # ① 유한 시간 안에 서버가 연결을 닫는다
        while True:
            msg = await ws.receive()
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break


async def test_ws_audit_logs_attach_and_detach(aiohttp_client, tmux_session, tmp_path, monkeypatch):
    """감사로그는 attach·detach 둘 다 남아야 한다(연결 종료 때 핸들러가 취소돼도)."""
    audit = tmp_path / "audit.log"
    monkeypatch.setattr(server, "audit_path", lambda: audit)
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{tmux_session}", headers=GOOD)
    await ws.close()

    async with asyncio.timeout(10):
        while not (audit.exists() and "detach" in audit.read_text()):
            await asyncio.sleep(0.05)
    written = audit.read_text()
    assert f"attach user=tester@example.com session={tmux_session}" in written
    assert f"detach session={tmux_session}" in written


async def test_ws_rejects_bad_session(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    resp = await client.get("/ws/..%2Fetc", headers=GOOD)
    assert resp.status in (400, 403, 404)


async def test_no_cors_headers_anywhere(aiohttp_client, tmux_session):
    """같은 출처 서빙이라 CORS 가 필요 없다 — 허가 헤더를 아예 내보내지 않는다(교차출처 = 차단)."""
    client = await aiohttp_client(server.create_app())
    for resp in (await client.get("/api/sessions", headers=GOOD),
                 await client.get("/", headers=LOGIN_ONLY)):
        assert resp.status == 200
        assert "Access-Control-Allow-Origin" not in resp.headers


async def test_static_index_is_served_at_root(aiohttp_client):
    """브릿지가 프론트를 직접 내준다 — 크로미움 LNA 우회의 핵심."""
    client = await aiohttp_client(server.create_app())
    resp = await client.get("/", headers=LOGIN_ONLY)
    assert resp.status == 200
    assert resp.content_type == "text/html" and resp.charset == "utf-8"
    assert "frogterminal" in await resp.text()


@pytest.mark.parametrize("path,ctype", [
    ("/app.js", "text/javascript"),
    ("/config.js", "text/javascript"),
    ("/style.css", "text/css"),
])
async def test_static_assets_are_served(aiohttp_client, path, ctype):
    client = await aiohttp_client(server.create_app())
    resp = await client.get(path, headers=LOGIN_ONLY)
    assert resp.status == 200
    assert resp.content_type == ctype and resp.charset == "utf-8"
    assert await resp.text()


@pytest.mark.parametrize("path", [
    "/.env.local",          # ★ OIDC 토큰 — 절대 나가면 안 된다
    "/.env",
    "/vercel.json",
    "/.gitignore",
    "/.vercel/project.json",
    "/index.html",          # 진입은 "/" 하나뿐 — 파일명 직접 접근도 허용하지 않는다
    "/README.md",
])
async def test_static_serves_whitelist_only(aiohttp_client, path):
    """디렉터리 통째 서빙(add_static)이 아님을 못박는다 — 화이트리스트 외에는 전부 404."""
    client = await aiohttp_client(server.create_app())
    resp = await client.get(path, headers=LOGIN_ONLY)
    assert resp.status == 404, path
    assert "VERCEL_OIDC_TOKEN" not in await resp.text(), f"{path} 로 비밀값이 샜다"


async def test_static_pwa_manifest_and_icons(aiohttp_client):
    """PWA 설치(맥 독 앱)용 매니페스트·아이콘 — 이것도 화이트리스트로만 나간다."""
    client = await aiohttp_client(server.create_app())
    resp = await client.get("/manifest.json", headers=LOGIN_ONLY)
    assert resp.status == 200
    assert resp.content_type == "application/manifest+json"
    man = json.loads(await resp.text())
    assert man["display"] == "standalone" and man["start_url"] == "/"
    icons = {i["src"]: i["sizes"] for i in man["icons"]}
    assert icons == {"icon-192.png": "192x192", "icon-512.png": "512x512"}
    for src in icons:
        r = await client.get(f"/{src}", headers=LOGIN_ONLY)
        assert r.status == 200 and r.content_type == "image/png"
        assert (await r.read())[:8] == b"\x89PNG\r\n\x1a\n"   # 진짜 PNG (charset 오염 없음)


@pytest.mark.parametrize("path", ["/", "/app.js", "/config.js", "/style.css"])
async def test_static_sends_framing_and_sniff_guards(aiohttp_client, path):
    """클릭재킹 차단 — 남의 페이지가 iframe 으로 우리 UI 를 심어 클릭을 유도하지 못하게."""
    client = await aiohttp_client(server.create_app())
    resp = await client.get(path, headers=LOGIN_ONLY)
    assert resp.status == 200
    assert resp.headers["X-Frame-Options"] == "DENY", path
    assert resp.headers["X-Content-Type-Options"] == "nosniff", path


async def test_file_put_감사로그와_409(aiohttp_client, monkeypatch, tmp_path):
    from bridge import files
    monkeypatch.setattr(files, "write_text", lambda *a: "42")
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    res = await client.put("/api/file", headers=GOOD,
                           json={"project": "a", "path": "x.md", "content": "y", "baseMtimeNs": "1"})
    assert res.status == 200 and (await res.json())["mtimeNs"] == "42"   # 버전 토큰은 문자열

    def conflict(*a):
        raise files.Conflict("x.md")
    monkeypatch.setattr(files, "write_text", conflict)
    res = await client.put("/api/file", headers=GOOD,
                           json={"project": "a", "path": "x.md", "content": "y", "baseMtimeNs": "1"})
    assert res.status == 409


async def test_file_get_버전토큰은_문자열로_나간다(aiohttp_client, monkeypatch):
    """★ JSON 숫자로 내보내면 브라우저 JSON.parse 가 하위 자릿수를 반올림해 저장이 전부 409 가 된다."""
    from bridge import files
    monkeypatch.setattr(files, "read_text",
                        lambda *a: {"content": "x", "mtimeNs": "1754377200123456789", "size": 1})
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/file?project=a&path=x.md", headers=GOOD)
    raw = await res.text()
    assert '"mtimeNs": "1754377200123456789"' in raw or '"mtimeNs":"1754377200123456789"' in raw
    assert (await res.json())["mtimeNs"] == "1754377200123456789"


@pytest.fixture
def image_api_env(tmp_path, monkeypatch):
    """이미지 API가 safe_path의 실물 관문을 타는지 실제 프로젝트 트리로 재현한다."""
    root = tmp_path / "projects"
    project = root / "alpha"
    project.mkdir(parents=True)
    (project / "AGENTS.md").write_text("# project")
    (project / "img.png").write_bytes(PNG)
    (project / "가짜.png").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    (project / "그림.svg").write_text("<svg><script>alert(1)</script></svg>")
    (project / "사진.heic").write_bytes(b"\x00\x00\x00\x18ftypheic")
    (project / ".env.png").write_bytes(PNG)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    (project / "밖링크.png").symlink_to(outside)
    for internal in ("_uploads", "_frogterminal"):
        (root / internal).mkdir()
        (root / internal / "secret.png").write_bytes(PNG)
    monkeypatch.setattr(config, "projects_root", lambda: root)
    return project


async def test_image_get_은_정확한_타입과_inline_nosniff로_응답한다(
        aiohttp_client, image_api_env):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/file/image", headers=GOOD,
                           params={"project": "alpha", "path": "img.png"})
    assert res.status == 200 and await res.read() == PNG
    assert res.headers["Content-Type"] == "image/png"
    assert res.headers["Content-Disposition"] == "inline"
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert (await client.get("/api/file/image",
                             params={"project": "alpha", "path": "img.png"})).status == 403


@pytest.mark.parametrize("path, phrase", [
    ("가짜.png", "실제 파일 형식"),
    ("그림.svg", "안전 문제"),
    ("사진.heic", "브라우저에서 바로 표시"),
])
async def test_image_get_은_가짜확장자_SVG_HEIC를_사람말로_거부한다(
        aiohttp_client, image_api_env, path, phrase):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/file/image", headers=GOOD,
                           params={"project": "alpha", "path": path})
    assert res.status == 415
    assert phrase in (await res.json())["reason"]


async def test_image_get_은_이미지_상한초과를_사람말로_거부한다(
        aiohttp_client, image_api_env, monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGE_FILE_BYTES", len(PNG) - 1)
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/file/image", headers=GOOD,
                           params={"project": "alpha", "path": "img.png"})
    assert res.status == 413
    assert "이미지가 너무 커서" in (await res.json())["reason"]


@pytest.mark.parametrize("project, path", [
    ("alpha", "../outside.png"),                 # ..로 프로젝트 루트 밖
    ("alpha", "밖링크.png"),                    # 심볼릭 링크로 프로젝트 루트 밖
    ("alpha", ".env.png"),                      # 숨김/비밀 denylist
    ("_uploads", "secret.png"),                 # 감사로그 내부 폴더
    ("_frogterminal", "secret.png"),            # 결재 내부 폴더
])
async def test_image_get_도_기존_파일관문을_실제요청에서_그대로_탄다(
        aiohttp_client, image_api_env, project, path):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/file/image", headers=GOOD,
                           params={"project": project, "path": path})
    assert res.status == 404


async def test_image_get_이중URL디코딩_dotdot도_실제요청에서_막는다(
        aiohttp_client, image_api_env):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/file/image?project=alpha&path=..%252Foutside.png", headers=GOOD)
    assert res.status == 404


@pytest.mark.parametrize("base", [1754377200123456789, 1, None, 1.5, True])
async def test_file_put_문자열아닌_토큰은_400(aiohttp_client, monkeypatch, base):
    """옛 정수 형식(=JS 에서 이미 뭉개진 값)은 조용히 받지 않고 400 으로 곱게 거절한다."""
    from bridge import files
    monkeypatch.setattr(files, "write_text", lambda *a: "42")
    client = await aiohttp_client(server.create_app())
    res = await client.put("/api/file", headers=GOOD,
                           json={"project": "a", "path": "x.md", "content": "y", "baseMtimeNs": base})
    assert res.status == 400


async def test_file_put_감사로그_개행주입_차단(aiohttp_client, monkeypatch, tmp_path):
    """파일명에 개행이 legal 하다 — 그대로 넣으면 append-only 감사로그에 가짜 줄이 위조된다."""
    from bridge import files
    monkeypatch.setattr(files, "write_text", lambda *a: "42")
    audit = tmp_path / "audit.log"
    monkeypatch.setattr(server, "audit_path", lambda: audit)
    client = await aiohttp_client(server.create_app())
    res = await client.put("/api/file", headers=GOOD, json={
        "project": "a",
        "path": "x.md\n2026-08-05T00:00:00+09:00 filewrite user=사용자 project=a path=위조.md bytes=0",
        "content": "한글", "baseMtimeNs": "1"})
    assert res.status == 200
    written = audit.read_text()
    assert len(written.splitlines()) == 1, f"감사로그에 가짜 줄이 생겼다: {written!r}"
    assert "bytes=6" in written                      # '한글' = 6바이트 — 글자수 2 가 아니다


@pytest.mark.parametrize("raw", ["[1,2,3]", '"x"', "42", "null"])
async def test_file_put_객체아닌_json은_400(aiohttp_client, raw):
    """api_decision_answer와 같은 구멍 — 객체 아닌 JSON 본문이 body.get(...)에서 500으로 새면 안 된다."""
    client = await aiohttp_client(server.create_app())
    res = await client.put("/api/file",
                           headers={**GOOD, "Content-Type": "application/json"}, data=raw)
    assert res.status == 400


async def test_uploads_폴더는_파일API로_읽지도_덮어쓰지도_못한다(aiohttp_client, tmp_path, monkeypatch):
    """★ append-only 감사로그가 그 감사 대상 API 로 통째로 지워지던 구멍 — 테일넷 신원이
    오용됐을 때 유일한 흔적이다. 목록(list_project_folders)만 막고 관문은 열어뒀었다."""
    from bridge import sessions
    audit = tmp_path / "_uploads" / "frogterminal-audit.log"
    audit.parent.mkdir()
    audit.write_text("2026-08-05T00:00:00+09:00 attach user=사용자\n")
    monkeypatch.setattr(config, "projects_root", lambda: tmp_path)
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/files?project=_uploads", headers=GOOD)).status == 404
    assert (await client.get("/api/file?project=_uploads&path=frogterminal-audit.log",
                             headers=GOOD)).status == 404
    res = await client.put("/api/file", headers=GOOD, json={
        "project": "_uploads", "path": "frogterminal-audit.log",
        "content": "지워짐\n", "baseMtimeNs": "1"})
    assert res.status == 404
    assert (await client.post("/api/upload?project=_uploads", headers=GOOD,
                              data={"file": open(__file__, "rb")})).status == 400
    assert audit.read_text() == "2026-08-05T00:00:00+09:00 attach user=사용자\n"   # 원본 그대로


KILL = "/api/sessions/kill"


@pytest.fixture
def hidden_file(tmp_path, monkeypatch):
    """숨김 목록은 서버 파일 하나 — 테스트가 진짜 ~/work 을 건드리지 않게 옮겨 둔다."""
    from bridge import sessions
    path = tmp_path / "hidden.json"
    monkeypatch.setattr(sessions.config, "hidden_file", lambda: path)
    return path


@pytest.fixture
def fav_file(tmp_path, monkeypatch):
    """즐겨찾기도 서버 파일 하나 — 숨김과 같은 자리, 다만 순서 있는 목록이다."""
    from bridge import sessions
    path = tmp_path / "favorites.json"
    monkeypatch.setattr(sessions.config, "favorites_file", lambda: path)
    return path


async def test_kill_지목한_세션을_실제로_끈다(aiohttp_client, dying_session, tmp_path, monkeypatch):
    from bridge import sessions
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    res = await client.post(KILL, headers=GOOD, json={"project": dying_session})
    assert res.status == 200 and await res.json() == {"ok": True, "killed": True}
    assert dying_session not in sessions.list_tmux_sessions()      # 실측 — 진짜 꺼졌다


async def test_끈_세션에_저절로_다시_붙기는_켜지_않고_4410으로_닫힌다(aiohttp_client, dying_session, tmp_path,
                                                         monkeypatch):
    """폰에서 끈 세션이 맥 칸의 자동 재접속(new-session -A)으로 에이전트까지 되살아나던 구멍(2026-10-11).
    끄기 API 로 끈 이름에 ?re=1 로 붙으면 tmux 를 띄우지 않고 4410 으로 닫아 웹이 재접속을 멈추게 한다."""
    from bridge import sessions
    (tmp_path / dying_session).mkdir()                             # 실사용처럼 프로젝트 폴더가 있는 세션
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    assert (await client.post(KILL, headers=GOOD, json={"project": dying_session})).status == 200
    ws = await client.ws_connect(f"/ws/{dying_session}?re=1", headers=GOOD)
    msg = await ws.receive(timeout=5)
    assert msg.type == WSMsgType.CLOSE and msg.data == server.WS_KILLED
    await asyncio.sleep(0.3)
    assert dying_session not in sessions.list_tmux_sessions()      # 되살아나지 않았다


async def test_끄지_않은_세션은_re1_로도_그대로_붙는다(aiohttp_client, tmux_session):
    """자동 재접속 표시(re=1)는 끈 세션만 막는다 — 와이파이 끊김 뒤 다시 붙기는 평소대로."""
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{tmux_session}?re=1", headers=GOOD)
    await wait_until_attached(tmux_session)
    assert not ws.closed
    await ws.close()


async def test_kill_이미_꺼진_프로젝트는_에러창을_안_띄운다(aiohttp_client, tmp_path, monkeypatch):
    """사용자가 두 번 눌러도 200 — 원하던 결과(꺼짐)는 이미 이뤄졌다. killed=false 로만 알린다."""
    from bridge import sessions
    (tmp_path / "myproj").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    res = await client.post(KILL, headers=GOOD, json={"project": "myproj"})
    assert res.status == 200 and await res.json() == {"ok": True, "killed": False}


async def test_전체끄기는_명단을_남기고_전부_끈다(aiohttp_client, tmp_path, monkeypatch):
    """되돌릴 수 없는 조작 — 무엇을 껐는지 이름으로 답하고, 복원 재료(명단)를 남겨야 한다."""
    from bridge import restore, sessions
    (tmp_path / "myproj").mkdir()
    (tmp_path / "other").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(restore.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["myproj", "other"])
    monkeypatch.setattr(sessions, "kill_session", lambda n: True)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/sessions/kill-all", headers=GOOD)
    body = await res.json()
    assert res.status == 200 and body["killed"] == ["myproj", "other"]
    assert restore.load_snapshot()["sessions"] == ["myproj", "other"]
    assert "kill-all" in (tmp_path / "audit.log").read_text()      # 되돌릴 수 없는 것은 남긴다


async def test_복원은_끄기_직전_명단만_되살린다(aiohttp_client, tmp_path, monkeypatch):
    from bridge import restore, sessions
    (tmp_path / "myproj").mkdir()
    (tmp_path / "other").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(restore.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    만든것 = []
    monkeypatch.setattr(restore, "_create_detached", lambda cmd: 만든것.append(cmd[5]))
    restore.save_snapshot(["myproj"])                              # 'other' 는 그때 꺼져 있었다
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/sessions/restore", headers=GOOD)
    assert res.status == 200 and (await res.json())["restored"] == ["myproj"]
    assert 만든것 == ["myproj"]


async def test_세션목록이_복원거리_수를_함께_준다(aiohttp_client, tmp_path, monkeypatch):
    """[복원] 버튼을 켤지의 근거 — 없으면 눌러 보고서야 '복원할 게 없다'를 알게 된다."""
    from bridge import restore, sessions
    (tmp_path / "myproj").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(restore.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    client = await aiohttp_client(server.create_app())
    assert (await (await client.get("/api/sessions", headers=GOOD)).json())["restorable"] == 0
    restore.save_snapshot(["myproj", "other"])
    assert (await (await client.get("/api/sessions", headers=GOOD)).json())["restorable"] == 2


@pytest.mark.parametrize("project", ["_uploads", "_frogterminal", "../etc", "", "nope", "dev\n"])
async def test_kill_관문을_못_넘는_이름은_404(aiohttp_client, tmp_path, monkeypatch, project):
    """★ _uploads(감사로그)·_frogterminal(결재 카드)은 실재하는 폴더다 — 관문이 없으면
    같은 이름의 tmux 세션까지 웹에서 꺼진다. is_valid_session 하나로 막는다."""
    from bridge import sessions
    (tmp_path / "_uploads").mkdir()
    (tmp_path / "_frogterminal").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "kill_session", lambda n: pytest.fail(f"관문을 넘었다: {n!r}"))
    client = await aiohttp_client(server.create_app())
    res = await client.post(KILL, headers=GOOD, json={"project": project})
    assert res.status == 404


async def test_kill_tmux_실패가_500으로_새지_않는다(aiohttp_client, dying_session, tmp_path, monkeypatch):
    """tmux 가 실패하면(CalledProcessError·타임아웃) sessions 가 LookupError 로 바꿔 준다 → 404."""
    from bridge import sessions
    def boom(name):
        raise LookupError(name)
    monkeypatch.setattr(sessions, "kill_session", boom)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    res = await client.post(KILL, headers=GOOD, json={"project": dying_session})
    assert res.status == 404


async def test_kill은_이벤트루프_밖에서_돈다(aiohttp_client, tmp_path, monkeypatch):
    """tmux 호출은 블로킹이다 — 루프에서 돌면 그동안 WS 펌프와 pty 읽기가 통째로 멈춘다."""
    import threading
    from bridge import sessions
    쓰레드 = []
    monkeypatch.setattr(sessions, "is_valid_session",
                        lambda n, root=None: 쓰레드.append(threading.current_thread()) or True)
    monkeypatch.setattr(sessions, "kill_session",
                        lambda n: 쓰레드.append(threading.current_thread()) or True)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    assert (await client.post(KILL, headers=GOOD, json={"project": "a"})).status == 200
    assert len(쓰레드) == 2
    assert [t for t in 쓰레드 if t is threading.main_thread()] == []


async def test_kill_직후에는_집계를_다시_돈다(aiohttp_client, tmp_path, monkeypatch):
    """캐시가 그대로면 사이드바가 계속 '● 켜짐'이라 사용자가 '안 꺼졌나?' 하고 또 누른다."""
    from bridge import decisions, sessions
    횟수 = []
    monkeypatch.setattr(decisions, "overview",
                        lambda: (횟수.append(1), {"cards": [], "states": {}})[1])
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    monkeypatch.setattr(sessions, "kill_session", lambda n: True)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    await client.get("/api/decisions", headers=GOOD)
    await client.get("/api/decisions", headers=GOOD)
    assert len(횟수) == 1                                  # 같은 주기 안 — 캐시가 접는다
    assert (await client.post(KILL, headers=GOOD, json={"project": "a"})).status == 200
    await client.get("/api/decisions", headers=GOOD)
    assert len(횟수) == 2                                  # 끈 뒤에는 곧바로 다시 본다


async def test_kill_감사로그_개행주입_차단(aiohttp_client, tmp_path, monkeypatch):
    """세션을 끄면 그 안에서 돌던 것(클로드 포함)이 같이 죽는다 — 반드시 한 줄 남기고,
    사용자가 준 이름은 attach·decision·filewrite 와 같은 잣대로 !r 로 적는다."""
    from bridge import sessions
    audit = tmp_path / "audit.log"
    monkeypatch.setattr(server, "audit_path", lambda: audit)
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    monkeypatch.setattr(sessions, "kill_session", lambda n: True)
    client = await aiohttp_client(server.create_app())
    res = await client.post(KILL, headers=GOOD, json={
        "project": "a\n2026-08-05T00:00:00+09:00 kill user=사용자 project=위조"})
    assert res.status == 200
    line = audit.read_text().splitlines()
    assert len(line) == 1, audit.read_text()
    assert "kill " in line[0] and "tester@example.com" in line[0]


async def test_kill_해도_결재카드는_지우지_않는다(aiohttp_client, dying_session, tmp_path, monkeypatch):
    """카드를 지우면 '무엇을 하다 껐는지'가 사라지고 인박스의 [터미널 열기] 복귀 경로도 끊긴다.
    세션이 죽으면 카드는 live=false 가 되어 결재 수·사이드바에서 이미 빠지므로 유령도 아니다."""
    from bridge import config as cfg
    d = tmp_path / "decisions"
    d.mkdir()
    (d / "s1.json").write_text(json.dumps(
        {"sessionId": "s1", "project": dying_session, "inTmux": True, "kind": "permission", "message": "하던 일"}))
    monkeypatch.setattr(cfg, "decisions_dir", lambda: d)
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    client = await aiohttp_client(server.create_app())
    assert (await client.post(KILL, headers=GOOD, json={"project": dying_session})).status == 200
    assert (d / "s1.json").exists()                        # 기록은 남는다
    cards = (await (await client.get("/api/decisions", headers=GOOD)).json())["decisions"]
    mine = [c for c in cards if c["project"] == dying_session]
    assert mine and mine[0]["live"] is False                # 답은 못 보내는 상태로 표시
    assert (await (await client.get("/api/summary", headers=GOOD)).json())["pending"] == 0


async def test_hidden_넣고_빼기(aiohttp_client, hidden_file, monkeypatch):
    from bridge import sessions
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: n == "hotel")
    client = await aiohttp_client(server.create_app())
    assert await (await client.get("/api/hidden", headers=GOOD)).json() == {"hidden": []}
    res = await client.post("/api/hidden", headers=GOOD, json={"project": "hotel", "hidden": True})
    assert res.status == 200 and await res.json() == {"hidden": ["hotel"]}
    assert await (await client.get("/api/hidden", headers=GOOD)).json() == {"hidden": ["hotel"]}
    assert json.loads(hidden_file.read_text()) == ["hotel"]      # 기기가 아니라 서버에 남는다
    res = await client.post("/api/hidden", headers=GOOD, json={"project": "hotel", "hidden": False})
    assert res.status == 200 and await res.json() == {"hidden": []}


async def test_hidden_없는_프로젝트는_숨길_수_없다(aiohttp_client, hidden_file, monkeypatch):
    from bridge import sessions
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: False)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/hidden", headers=GOOD, json={"project": "nope", "hidden": True})
    assert res.status == 404
    assert not hidden_file.exists()                        # 임의 이름이 파일에 쌓이지 않는다


async def test_hidden_해제는_폴더가_사라진_뒤에도_된다(aiohttp_client, hidden_file, monkeypatch):
    """숨긴 뒤 폴더를 지우면 목록에서 영영 못 빼는 덫이 된다 — 빼기는 존재를 묻지 않는다."""
    from bridge import sessions
    hidden_file.write_text('["gone"]')
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: False)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/hidden", headers=GOOD, json={"project": "gone", "hidden": False})
    assert res.status == 200 and await res.json() == {"hidden": []}


@pytest.mark.parametrize("body", [
    {"project": "../etc", "hidden": True},                 # 경로조작
    {"project": "", "hidden": True},
    {"project": "a b", "hidden": True},
    {"project": "../etc", "hidden": False},                # 빼기도 이름 검사는 탄다
    {"project": "dev"},                                    # hidden 빠짐
    {"project": "dev", "hidden": "true"},                  # 문자열은 불리언이 아니다
])
async def test_hidden_잘못된_요청은_400(aiohttp_client, hidden_file, monkeypatch, body):
    from bridge import sessions
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    client = await aiohttp_client(server.create_app())
    assert (await client.post("/api/hidden", headers=GOOD, json=body)).status == 400
    assert not hidden_file.exists()


async def test_sessions_에_숨김표시가_실린다(aiohttp_client, hidden_file, tmp_path, monkeypatch):
    """목록에서 빼지 않고 표시만 한다 — 웹이 접었다 폈다 할 수 있어야 하기 때문."""
    from bridge import decisions, sessions
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(decisions, "overview", lambda: {"cards": [], "states": {}})
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    client = await aiohttp_client(server.create_app())
    await client.post("/api/hidden", headers=GOOD, json={"project": "alpha", "hidden": True})
    got = (await (await client.get("/api/sessions", headers=GOOD)).json())["sessions"]
    by = {s["name"]: s for s in got}
    assert set(by) == {"alpha", "beta"}                    # 숨겨도 목록에는 남는다
    assert by["alpha"]["hidden"] is True and by["beta"]["hidden"] is False


async def test_숨겨도_결재수는_그대로다(aiohttp_client, hidden_file, tmp_path, monkeypatch):
    """★ 목록에서 안 보인다고 결정해야 할 일이 사라지는 것은 아니다 —
    숨김이 결재 수를 깎으면 '놓치지 않게 한다'는 이 앱의 목적을 배반한다."""
    from bridge import decisions, sessions
    monkeypatch.setattr(decisions, "overview", lambda: {
        "cards": [{"kind": "permission", "project": "alpha", "live": True}],
        "states": {"alpha": {"status": "decision", "pending": 1}}})
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    client = await aiohttp_client(server.create_app())
    before = await (await client.get("/api/summary", headers=GOOD)).json()
    assert {k: before[k] for k in ("alive", "pending", "waiting", "dead")} \
        == {"alive": 1, "pending": 1, "waiting": 0, "dead": 0}   # web(배포 버전)은 값이 유동이라 제외
    await client.post("/api/hidden", headers=GOOD, json={"project": "alpha", "hidden": True})
    assert await (await client.get("/api/summary", headers=GOOD)).json() == before
    cards = (await (await client.get("/api/decisions", headers=GOOD)).json())["decisions"]
    assert len(cards) == 1                                 # 인박스에서도 안 사라진다


async def test_favorites_넣고_빼기는_누른_순서로_남는다(aiohttp_client, fav_file, monkeypatch):
    from bridge import sessions
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    client = await aiohttp_client(server.create_app())
    assert await (await client.get("/api/favorites", headers=GOOD)).json() == {"favorites": []}
    res = await client.post("/api/favorites", headers=GOOD, json={"project": "hotel", "fav": True})
    assert res.status == 200 and await res.json() == {"favorites": ["hotel"]}
    res = await client.post("/api/favorites", headers=GOOD, json={"project": "alpha", "fav": True})
    # 이름순이면 alpha 가 앞이다 — 서버도 응답도 누른 순서를 지킨다
    assert await res.json() == {"favorites": ["hotel", "alpha"]}
    assert json.loads(fav_file.read_text()) == ["hotel", "alpha"]  # 기기가 아니라 서버에 남는다
    res = await client.post("/api/favorites", headers=GOOD, json={"project": "hotel", "fav": False})
    assert res.status == 200 and await res.json() == {"favorites": ["alpha"]}


async def test_favorites_없는_프로젝트는_올릴_수_없다(aiohttp_client, fav_file, monkeypatch):
    from bridge import sessions
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: False)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/favorites", headers=GOOD, json={"project": "nope", "fav": True})
    assert res.status == 404
    assert not fav_file.exists()                           # 임의 이름이 파일에 쌓이지 않는다


async def test_favorites_해제는_폴더가_사라진_뒤에도_된다(aiohttp_client, fav_file, monkeypatch):
    from bridge import sessions
    fav_file.write_text('["gone"]')
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: False)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/favorites", headers=GOOD, json={"project": "gone", "fav": False})
    assert res.status == 200 and await res.json() == {"favorites": []}


@pytest.mark.parametrize("body", [
    {"project": "../etc", "fav": True},                    # 경로조작
    {"project": "", "fav": True},
    {"project": "a b", "fav": True},
    {"project": "hotel@2", "fav": True},                # 탭 — 즐겨찾기는 프로젝트 단위다
    {"project": "../etc", "fav": False},                   # 빼기도 이름 검사는 탄다
    {"project": "dev"},                                    # fav 빠짐
    {"project": "dev", "fav": "true"},                     # 문자열은 불리언이 아니다
])
async def test_favorites_잘못된_요청은_400(aiohttp_client, fav_file, monkeypatch, body):
    from bridge import sessions
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    client = await aiohttp_client(server.create_app())
    assert (await client.post("/api/favorites", headers=GOOD, json=body)).status == 400
    assert not fav_file.exists()


async def test_favorites_는_uploads_같은_관문_밖_폴더를_못_올린다(aiohttp_client, fav_file,
                                                              tmp_path, monkeypatch):
    """_uploads(감사로그)·_frogterminal(결재 카드)은 실재하는 폴더다 — is_valid_session 하나로 막는다."""
    from bridge import sessions
    (tmp_path / "_uploads").mkdir()
    (tmp_path / "_frogterminal").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    client = await aiohttp_client(server.create_app())
    for project in ("_uploads", "_frogterminal"):
        res = await client.post("/api/favorites", headers=GOOD, json={"project": project, "fav": True})
        assert res.status == 404
    assert not fav_file.exists()


async def test_sessions_에_즐겨찾기_순번이_실린다(aiohttp_client, fav_file, tmp_path, monkeypatch):
    """목록 순서는 서버가 바꾸지 않는다 — 웹이 이 순번을 보고 맨 위로 세운다(숨김과 같은 잣대)."""
    from bridge import decisions, sessions
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(decisions, "overview", lambda: {"cards": [], "states": {}})
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    client = await aiohttp_client(server.create_app())
    await client.post("/api/favorites", headers=GOOD, json={"project": "beta", "fav": True})
    got = (await (await client.get("/api/sessions", headers=GOOD)).json())["sessions"]
    by = {s["name"]: s for s in got}
    assert by["beta"]["fav"] == 0 and by["alpha"]["fav"] is None
    assert [s["name"] for s in got] == ["alpha", "beta"]    # 서버 순서는 그대로(이름순)


async def test_즐겨찾기해도_결재수는_그대로다(aiohttp_client, fav_file, tmp_path, monkeypatch):
    from bridge import decisions, sessions
    monkeypatch.setattr(decisions, "overview", lambda: {
        "cards": [{"kind": "permission", "project": "alpha", "live": True}],
        "states": {"alpha": {"status": "decision", "pending": 1}}})
    monkeypatch.setattr(sessions.config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(sessions, "is_valid_session", lambda n, root=None: True)
    client = await aiohttp_client(server.create_app())
    before = await (await client.get("/api/summary", headers=GOOD)).json()
    assert {k: before[k] for k in ("alive", "pending", "waiting", "dead")} \
        == {"alive": 1, "pending": 1, "waiting": 0, "dead": 0}   # web(배포 버전)은 값이 유동이라 제외
    await client.post("/api/favorites", headers=GOOD, json={"project": "alpha", "fav": True})
    assert await (await client.get("/api/summary", headers=GOOD)).json() == before


@pytest.mark.parametrize("path", [KILL, "/api/hidden", "/api/favorites"])
@pytest.mark.parametrize("raw", ["[1,2,3]", '"x"', "42", "null", "{망가진"])
async def test_새_POST의_객체아닌_json은_400(aiohttp_client, path, raw):
    client = await aiohttp_client(server.create_app())
    res = await client.post(path, headers={**GOOD, "Content-Type": "application/json"}, data=raw)
    assert res.status == 400


async def test_끄기_숨김_라우트도_인증_필수(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/hidden")).status == 403
    assert (await client.post("/api/hidden", json={"project": "a", "hidden": True})).status == 403
    assert (await client.get("/api/favorites")).status == 403
    assert (await client.post("/api/favorites", json={"project": "a", "fav": True})).status == 403
    assert (await client.post(KILL, json={"project": "a"})).status == 403


async def test_file_라우트_인증_필수(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/files?project=a")).status == 403


async def test_locate_이상한_이름은_400(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    for bad in ({"name": "a/../b"}, {"name": ".env", "size": 1, "mtimeMs": 1}, {"name": ""}):
        assert (await client.post("/api/locate", headers=GOOD, json=bad)).status == 400, bad


async def test_locate_는_유일할_때만_경로를_준다(aiohttp_client, monkeypatch):
    from bridge import locate as loc
    client = await aiohttp_client(server.create_app())
    monkeypatch.setattr(loc, "find_file", lambda n, s, m: ["/a/x.md"])
    got = await (await client.post("/api/locate", headers=GOOD,
                                   json={"name": "x.md", "size": 1, "mtimeMs": 1})).json()
    assert got == {"path": "/a/x.md"}
    monkeypatch.setattr(loc, "find_file", lambda n, s, m: ["/a/x.md", "/b/x.md"])   # 애매 — 특정 실패
    got = await (await client.post("/api/locate", headers=GOOD,
                                   json={"name": "x.md", "size": 1, "mtimeMs": 1})).json()
    assert got == {"matches": 2}


async def test_clipboard_는_tmux_버퍼를_준다(aiohttp_client, monkeypatch):
    from bridge import sessions
    monkeypatch.setattr(sessions, "copy_buffer", lambda: ("긁은 글자", 2.5))
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/clipboard", headers=GOOD)
    assert res.status == 200 and (await res.json()) == {"text": "긁은 글자", "age": 2.5}
    assert (await client.get("/api/clipboard")).status == 403      # 인증은 그대로 필수


async def test_open_abs_는_열_수_있는_뿌리만_통과시킨다(aiohttp_client, monkeypatch, tmp_path):
    """터미널에 찍힌 절대경로(스크래치패드 이미지)를 여는 길 — 판정은 files.resolve_openable."""
    from bridge import files
    shot = tmp_path / "d-160.4.jpg"
    shot.write_text("img")
    monkeypatch.setattr(files, "resolve_openable", lambda raw: shot)
    ran = []
    monkeypatch.setattr(server.subprocess, "run",
                        lambda *a, **k: ran.append(a[0]) or type("R", (), {"returncode": 0})())
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/open", headers=GOOD, json={"abs": str(shot), "app": "preview"})
    assert res.status == 200
    assert ran[-1] == ["open", "-a", "Preview", str(shot)]

    def deny(raw):
        raise files.NotAllowed(raw)
    monkeypatch.setattr(files, "resolve_openable", deny)
    res = await client.post("/api/open", headers=GOOD, json={"abs": "/etc/passwd"})
    assert res.status == 404


async def test_새_프로젝트_폴더를_만든다(aiohttp_client, monkeypatch, tmp_path):
    """사이드바 ＋ — 설정 루트에 프로젝트를 만들고, 감사로그도 같은 루트를 따른다."""
    monkeypatch.setattr(config, "projects_root", lambda: tmp_path)
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/projects", headers=GOOD, json={"name": "새프로젝트"})
    assert res.status == 200 and (await res.json())["name"] == "새프로젝트"
    assert (tmp_path / "새프로젝트").is_dir()

    again = await client.post("/api/projects", headers=GOOD, json={"name": "새프로젝트"})
    assert again.status == 409                       # 이미 있으면 만들지 않는다(웹이 그냥 연다)

    for bad in ("../탈출", "a/b", ".숨김", "", "_uploads", "_frogterminal", "탭@2", "이름 공백"):
        r = await client.post("/api/projects", headers=GOOD, json={"name": bad})
        assert r.status == 400, bad
    # 성공 감사로그의 부산물(_uploads)과 새 프로젝트 명단(_frogterminal/created.json) 외에는
    # 아무것도 만들지 않는다.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["_frogterminal", "_uploads", "새프로젝트"]
    # 빈 폴더지만 명단에 올랐으니 사이드바에서 '프로젝트'로 보인다(2026-08-22 demo 실종 재발방지)
    assert json.loads((tmp_path / "_frogterminal" / "created.json").read_text()) == ["새프로젝트"]
    monkeypatch.setattr(server.sessions, "list_tmux_sessions", lambda: [])
    listed = await client.get("/api/sessions", headers=GOOD)
    by = {s["name"]: s for s in (await listed.json())["sessions"]}
    assert by["새프로젝트"]["project"] is True


@pytest.mark.parametrize("query, expected", [
    ("?agent=codex", "codex"),                # 사이드바 '코덱스로 켜기' — 이번 접속만
    ("?cols=80&rows=24&agent=codex", "codex"),
    ("", "claude"),                           # 없으면 설정값
    ("?agent=zsh%20-c%20evil", "claude"),     # 고정 목록 밖 = 무시(셸에 들어가는 값)
])
async def test_ws_agent_query_는_고정목록만_받는다(aiohttp_client, tmux_session, monkeypatch,
                                                  query, expected):
    from bridge import sessions as se
    seen = {}
    def fake_attach(name, root=None, agent="shell"):
        seen["agent"] = agent
        return ["true"]                       # tmux 대신 곧바로 끝나는 프로세스
    monkeypatch.setattr(se, "attach_cmd", fake_attach)
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{tmux_session}{query}", headers=GOOD)
    await ws.close()
    assert seen["agent"] == expected


async def test_ws_unmode_는_복사모드를_빠져나온다(aiohttp_client, tmux_session):
    """휠 스크롤(copy-mode) 뒤 첫 키 앞에 웹이 보내는 unmode — tmux 모드가 풀리고 키는 그대로 간다."""
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{tmux_session}", headers=GOOD)
    subprocess.run(["tmux", "copy-mode", "-t", tmux_session], check=True)
    in_mode = lambda: subprocess.run(["tmux", "display", "-p", "-t", tmux_session, "#{pane_in_mode}"],
                                     capture_output=True, text=True).stdout.strip()
    assert in_mode() == "1"
    await ws.send_str(json.dumps({"type": "unmode"}))
    marker = f"MARK_{uuid.uuid4().hex[:6]}"
    await ws.send_bytes(f"echo {marker}\n".encode())
    collected = b""
    async with asyncio.timeout(15):
        while marker.encode() not in collected.replace(b"\r", b""):
            msg = await ws.receive()
            assert msg.type in (WSMsgType.BINARY, WSMsgType.TEXT), msg
            collected += msg.data if isinstance(msg.data, bytes) else msg.data.encode()
    assert in_mode() == "0"
    await ws.close()


async def test_ws_ping_에_답이_온다(aiohttp_client, tmux_session):
    """브라우저의 생존 확인 — 핑을 보내면 빈 바이트 프레임이 돌아와야 웹이 '살아 있다'고 안다.
    접속 직후 tmux 가 그리는 화면(빈 프레임이 아니다)이 앞에 섞여 올 수 있어 빈 것이 올 때까지 읽는다."""
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect(f"/ws/{tmux_session}", headers=GOOD)
    await ws.send_str(json.dumps({"type": "ping"}))
    async with asyncio.timeout(10):
        while True:
            msg = await ws.receive()
            assert msg.type == WSMsgType.BINARY, msg
            if msg.data == b"":
                break
    await ws.close()


async def test_여러_칸_표시는_보는_연결이_하나라도_있으면_달고_다_떠나면_뗀다(monkeypatch):
    """폰(1칸)과 맥(4칸)이 같은 세션에 붙어도 맥이 남아 있는 동안은 짧은 HUD 가 유지된다.
    tmux 는 상태가 바뀔 때만 부른다 — 1칸만 쓰면 한 번도 안 부른다."""
    from bridge import sessions
    calls = []
    monkeypatch.setattr(sessions, "set_compact", lambda name, on: calls.append((name, on)))
    app = server.create_app()
    mac, mac2, phone = object(), object(), object()
    await server._set_compact(app, "wiki", phone, False)      # 1칸 연결 — 아무 일도 없다
    assert calls == []
    await server._set_compact(app, "wiki", mac, True)
    await server._set_compact(app, "wiki", mac2, True)        # 이미 달려 있다 — 다시 안 부른다
    assert calls == [("wiki", True)]
    await server._set_compact(app, "wiki", mac, False)        # 하나 남았다 — 그대로
    assert calls == [("wiki", True)]
    await server._set_compact(app, "wiki", mac2, False)       # 다 떠났다 — 뗀다
    assert calls == [("wiki", True), ("wiki", False)]
    assert "wiki" not in app[server.COMPACT]


async def test_hud_는_statusline_계정줄을_그대로_준다(aiohttp_client, tmp_path, monkeypatch):
    script = tmp_path / "statusline.py"
    script.write_text("import sys\nassert '--accounts' in sys.argv\nprint('\\x1b[36m>\\x1b[0mdigi 5h 22%')\nprint('ceo 5h 19%')\n")
    monkeypatch.setattr(config, "HUD_SCRIPT", script)
    monkeypatch.setitem(server._hud_cache, "at", 0.0)
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/hud", headers=GOOD)
    assert res.status == 200
    assert (await res.json())["lines"] == ["\x1b[36m>\x1b[0mdigi 5h 22%", "ceo 5h 19%"]


async def test_hud_스크립트가_없으면_빈_목록(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HUD_SCRIPT", tmp_path / "없음.py")
    monkeypatch.setitem(server._hud_cache, "at", 0.0)
    client = await aiohttp_client(server.create_app())
    assert (await (await client.get("/api/hud", headers=GOOD)).json()) == {"lines": []}


# ---------- 다른 PC 터미널의 파일 패널 — ssh 원격 폴더 읽기 전용 ----------

async def test_원격_세션의_파일_목록과_읽기는_원격모듈로_가고_쓰기는_405(aiohttp_client, monkeypatch):
    from bridge import remote_files
    rm = {"host": "srvpc", "path": "C:\\rproj"}
    monkeypatch.setattr(remote_files, "remote_of", lambda p: rm if p == "rproj" else None)
    monkeypatch.setattr(remote_files, "list_dir", lambda r, rel: [{"name": "README.md", "dir": False, "size": 3}])
    monkeypatch.setattr(remote_files, "read_text",
                        lambda r, rel: {"content": "rproj\n", "mtimeNs": "1", "size": 4})
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/files?project=rproj", headers=GOOD)
    assert res.status == 200 and (await res.json())["entries"][0]["name"] == "README.md"
    res = await client.get("/api/file?project=rproj&path=README.md", headers=GOOD)
    assert res.status == 200 and (await res.json())["content"] == "rproj\n"
    res = await client.put("/api/file", headers=GOOD, json={
        "project": "rproj", "path": "README.md", "content": "x", "baseMtimeNs": "1"})
    assert res.status == 405                                                 # 읽기 전용
    res = await client.post("/api/mkdir", headers=GOOD, json={"project": "rproj", "path": "new"})
    assert res.status == 405


async def test_원격_PC_가_안_붙으면_502_이고_로컬_404_와_구분된다(aiohttp_client, monkeypatch):
    from bridge import remote_files
    monkeypatch.setattr(remote_files, "remote_of", lambda p: {"host": "srvpc", "path": "C:\\rproj"})
    def down(r, rel=""):
        raise remote_files.RemoteUnavailable("srvpc")
    monkeypatch.setattr(remote_files, "list_dir", down)
    monkeypatch.setattr(remote_files, "read_text", down)
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/files?project=rproj", headers=GOOD)
    assert res.status == 502 and "srvpc" in (await res.json())["reason"]
    assert (await client.get("/api/file?project=rproj&path=a.txt", headers=GOOD)).status == 502


async def test_로컬_프로젝트의_파일_API_는_원격모듈을_타지_않는다(aiohttp_client, tmp_path, monkeypatch):
    """회귀 — 맥 프로젝트는 지금 경로 그대로(remote_of 가 None)."""
    from bridge import remote_files
    (tmp_path / "proj").mkdir(); (tmp_path / "proj" / "a.txt").write_text("hi")
    monkeypatch.setattr(config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(remote_files.sessions, "session_remotes", lambda: {})
    def boom(*a, **k):
        raise AssertionError("원격 모듈이 불렸다")
    monkeypatch.setattr(remote_files, "list_dir", boom)
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/files?project=proj", headers=GOOD)
    assert res.status == 200 and [e["name"] for e in (await res.json())["entries"]] == ["a.txt"]
