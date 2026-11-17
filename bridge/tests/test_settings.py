"""설정(시작 에이전트·bypass·RC·실행 별칭)·에이전트 상태 테스트.

commands는 자유 명령이 아니라 claude·codex를 가리키는 안전한 별칭 이름이다. 저장 테스트는
반드시 tmp_path의 가짜 .zshrc만 사용한다 — 개발자·사용자 계정의 실제 셸 설정은 시험 대상이 아니다.
"""
import json
import uuid
from pathlib import Path

import pytest

from bridge import agent_updates, config, sessions, settings

# bypass 기본 켬 — Orca 와 같은 기본값(전권한 플래그 기본 적용), 실사용도 늘 bypass.
# cont(이어서 시작) 도 기본 켬 — exit 로 나갔다 다시 뜰 때마다 맥락이 날아가던 것을 잇는다.
DEFAULTS = {"projectsRoot": str(config.DEFAULT_PROJECTS_ROOT),
            "agent": "claude", "bypass": True, "rc": False, "cont": True,
            "commands": {"claude": "", "codex": ""}}


def claude_body(command: str) -> str:
    """아래 실행문 비교를 읽기 쉽게 두는 작은 테스트 도우미."""
    return command


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", p)
    monkeypatch.setattr(settings, "ZSHRC_FILE", tmp_path / ".zshrc")
    return p


def test_load_기본값(settings_file):
    assert settings.load() == DEFAULTS


def test_설정을_안_건드리면_bridge_json_의_기본_루트(settings_file, monkeypatch):
    """설정 파일이 없으면 bridge.json 의 defaultProjectsRoot(없으면 ~/projects)를 쓴다 — 코드에 기기 경로를 박지 않는다."""
    monkeypatch.setattr(config, "DEFAULT_PROJECTS_ROOT", Path.home() / "work")
    assert settings.projects_root() == Path.home() / "work"
    assert settings.save({"projectsRoot": ""})["projectsRoot"] == str(Path.home() / "work")   # 빈 값 = 기본값 복귀


def test_루트_검증은_홈_안_실제_디렉터리만_허용(tmp_path, monkeypatch):
    home = tmp_path / "home"
    good = home / "work"
    good.mkdir(parents=True)
    file = home / "file.txt"
    file.write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "outside-link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(settings, "HOME_DIR", home.resolve())
    assert settings.validate_projects_root(str(good)) == good.resolve()
    for raw, code in ((str(home), "too_broad"), ("/", "too_broad"),
                      (str(home / "없음"), "not_found"), (str(file), "not_directory"),
                      (str(home / "outside-link"), "too_broad"),
                      ("relative/path", "not_absolute"), (str(good / ".." / "work"), "invalid_path")):
        with pytest.raises(settings.InvalidProjectsRoot) as exc:
            settings.validate_projects_root(raw)
        assert exc.value.code == code


def test_루트_저장직후_모든_작업공간_경로가_새값을_쓴다(settings_file, tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "workspace"
    root.mkdir(parents=True)
    monkeypatch.setattr(settings, "HOME_DIR", home.resolve())
    settings.save({"projectsRoot": str(root)})
    assert config.projects_root() == root.resolve()
    assert config.upload_root() == root / "_uploads"
    assert config.audit_file() == root / "_uploads" / "frogterminal-audit.log"
    assert config.hidden_file() == root / "_frogterminal" / "hidden.json"
    assert config.favorites_file() == root / "_frogterminal" / "favorites.json"
    assert config.decisions_dir() == root / "_frogterminal" / "decisions"


def test_save_load_왕복_부분갱신(settings_file):
    assert settings.save({"agent": "codex"})["agent"] == "codex"
    settings.save({"bypass": False, "rc": True, "cont": False})
    settings.save({"commands": {"claude": "cl"}})
    assert settings.load() == {"projectsRoot": str(config.DEFAULT_PROJECTS_ROOT),
                               "agent": "codex", "bypass": False, "rc": True, "cont": False,
                               "commands": {"claude": "cl", "codex": ""}}


def test_새_전역설정이_없으면_예전_루트설정을_호환해_읽는다(tmp_path, monkeypatch):
    global_file = tmp_path / "app" / "settings.json"
    legacy = tmp_path / "old-root" / "_frogterminal" / "settings.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"agent": "codex", "commands": {"claude": "cl"}}))
    monkeypatch.setattr(config, "DEFAULT_SETTINGS_FILE", global_file)
    monkeypatch.setattr(config, "SETTINGS_FILE", global_file)
    monkeypatch.setattr(config, "LEGACY_SETTINGS_FILE", legacy)
    assert settings.load()["agent"] == "codex"
    assert settings.load()["commands"]["claude"] == "cl"


def test_에이전트는_목록_밖이면_거부(settings_file):
    with pytest.raises(ValueError):
        settings.save({"agent": "zsh"})
    settings_file.write_text(json.dumps({"agent": "evil", "bypass": "yes"}))
    got = settings.load()                          # 손으로 고친 이상한 값은 기본값으로
    assert got["agent"] == "claude" and got["bypass"] is False


@pytest.mark.parametrize("name", [
    "rm", "cd", "ls", "sudo", "git", "zsh", "sh", "a;b", "a b", "a\nb", "a&b", "a|b",
    "$a", "`a`", 'a"b', "1abc", "a" * 33,
])
def test_위험하거나_셸에_주입되는_별칭은_저장을_거부한다(settings_file, name):
    with pytest.raises(settings.InvalidAlias):
        settings.save({"commands": {"claude": name}})
    assert settings.load()["commands"]["claude"] == ""
    assert not settings.ZSHRC_FILE.exists()


def test_빈_별칭은_거부하지_않고_해당_줄과_블록을_지운다(settings_file):
    """A의 명시 계약: 빈 값은 '명령 없음'이 아니라 별칭 삭제이고 기본 claude/codex로 돌아간다."""
    settings.save({"commands": {"claude": "ca", "codex": "cx"}})
    settings.save({"commands": {"claude": ""}})
    text = settings.ZSHRC_FILE.read_text()
    assert 'alias ca="claude"' not in text
    assert 'alias cx="codex"' in text
    settings.save({"commands": {"codex": ""}})
    assert settings.ALIAS_BLOCK_START not in settings.ZSHRC_FILE.read_text()


def test_옛_cl_두줄을_마커_블록으로_이동하고_사용자_줄과_백업을_보존한다(settings_file):
    zshrc = settings.ZSHRC_FILE
    before = ("alias mine=\"keep-me\"\n"
              "# frogterminal — 클로드 약칭 (설정의 실행 명령 'cl' 이 동작하도록)\n"
              "alias cl=\"claude\"\n"
              "export USER_SETTING=\"한 글자도 바꾸지 마\"\n").encode()
    zshrc.write_bytes(before)

    settings.save({"commands": {"claude": "ca", "codex": "cx"}})

    got = zshrc.read_bytes()
    assert b'alias mine="keep-me"\n' in got
    assert 'export USER_SETTING="한 글자도 바꾸지 마"\n'.encode() in got
    assert _outside_frog_block(got) == (b'alias mine="keep-me"\n' +
                                        'export USER_SETTING="한 글자도 바꾸지 마"\n'.encode())
    assert "클로드 약칭".encode() not in got and b'alias cl="claude"' not in got
    assert got.count(settings.ALIAS_BLOCK_START.encode()) == 1
    assert b'alias ca="claude"\n' in got and b'alias cx="codex"\n' in got
    assert zshrc.with_name(".zshrc.frogterminal.bak").read_bytes() == before


def _outside_frog_block(data: bytes) -> bytes:
    """테스트 비교용: 생성 블록만 덜어낸 나머지 바이트는 원본 사용자 줄과 정확히 같아야 한다."""
    start = data.index(settings.ALIAS_BLOCK_START.encode())
    end = data.index(settings.ALIAS_BLOCK_END.encode(), start) + len(settings.ALIAS_BLOCK_END.encode())
    if data[end:end + 1] == b"\n":
        end += 1
    return data[:start] + data[end:]


def test_settings_json_저장이_실패하면_zshrc도_원상복구한다(settings_file, monkeypatch):
    zshrc = settings.ZSHRC_FILE
    before = b'alias personal="stay"\n'
    zshrc.write_bytes(before)
    real_replace = settings.os.replace

    def fail_settings_only(src, dst):
        if dst == settings_file:
            raise OSError("settings write failed")
        return real_replace(src, dst)

    monkeypatch.setattr(settings.os, "replace", fail_settings_only)
    with pytest.raises(OSError, match="settings write failed"):
        settings.save({"commands": {"claude": "ca"}})
    assert zshrc.read_bytes() == before
    assert not settings_file.exists()


def test_깨진_설정파일에도_기본값(settings_file):
    settings_file.write_text("{부서진")
    assert settings.load() == DEFAULTS
    settings_file.write_text("[1,2]")
    assert settings.load() == DEFAULTS


# ── 시작 명령 조립 ──────────────────────────────────────────────────────────

def test_launch_command_기본과_플래그(settings_file):
    # 기본 = 전권한(bypass). 끄면 순정 claude, RC 켜면 세션 이름을 붙여 Remote Control 로.
    assert claude_body(settings.launch_command("claude", "proj")) == "claude --dangerously-skip-permissions"
    settings.save({"bypass": False})
    assert claude_body(settings.launch_command("claude", "proj")) == "claude"
    settings.save({"rc": True})
    assert claude_body(settings.launch_command("claude", "proj")) == "claude --remote-control proj"
    settings.save({"bypass": True})
    assert claude_body(settings.launch_command("claude", "proj")) == \
        "claude --dangerously-skip-permissions --remote-control proj"
    assert settings.launch_command("codex", "proj") == "codex --yolo"   # bypass 켜짐 = 코덱스도 전권한
    settings.save({"bypass": False})
    assert settings.launch_command("codex", "proj") == "codex"          # RC·cont 는 클로드에만
    assert settings.launch_command("shell", "proj") is None
    assert settings.launch_command("없는에이전트", "proj") is None


def test_launch_command_커스텀_약칭(settings_file):
    """claude 대신 cl 같은 별칭으로도 뜨게. zsh -lic 라 .zshrc alias 가 산다.
    bypass·RC 토글은 커스텀 명령에도 그대로 붙는다(토글이 명령보다 우선하는 스위치다)."""
    settings.save({"commands": {"claude": "cl", "codex": "cx"}})
    assert claude_body(settings.launch_command("claude", "p")) == "cl --dangerously-skip-permissions"
    assert settings.launch_command("codex", "p") == "cx --yolo"
    settings.save({"bypass": False})
    assert claude_body(settings.launch_command("claude", "p")) == "cl"
    assert settings.launch_command("codex", "p") == "cx"


def test_attach_cmd_는_시작명령을_새세션_명령으로_붙인다(settings_file, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRANSCRIPT_ROOT", tmp_path / "projects")   # 이어가기 끄고 본다
    (tmp_path / "proj").mkdir()
    settings.save({"cont": False})
    got = sessions.attach_cmd("proj", root=tmp_path, agent="claude")
    semi = got.index(";")
    # 명령은 -c 디렉터리 뒤, ';' 앞 — 이미 있는 세션엔 -A 가 이 명령을 무시하고 붙는다(실측).
    assert got[semi - 1] == "zsh -lic 'claude --dangerously-skip-permissions'"  # 통째로 -c 한 인자
    settings.save({"bypass": False})
    assert sessions.attach_cmd("proj", root=tmp_path, agent="claude")[semi - 1] == "zsh -lic claude"


# ── 이어서 시작(cont) — 프로젝트마다 하나의 대화 ────────────────────────────

def test_기본세션은_continue_로_직전_대화를_잇는다(settings_file, tmp_path):
    """exit 로 나갔다 오면 나가기 직전 대화로 돌아온다.
    고정 UUID(--session-id)는 --remote-control 과 함께 쓰면 무시된다 — 지정한 ID 에 아무것도
    안 쌓여 다음 --resume 이 낡은 대화를 열었다(2026-08-24 실측). 클로드가 관리하는
    --continue 로 간다: 그 폴더의 가장 최근 대화 = 방금 나간 그 대화."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    assert claude_body(settings.launch_command("claude", "proj", cwd=cwd)) == (
        "claude --dangerously-skip-permissions --continue"
        " || claude --dangerously-skip-permissions")


def test_탭은_새_대화로_둔다(settings_file, tmp_path):
    """탭(base@N)은 기본 세션과 같은 폴더다 — 둘 다 --continue 면 서로의 대화를 가로챈다.
    탭은 '옆에서 따로 돌리는 자리'라 이어가기를 걸지 않는다."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    assert claude_body(settings.launch_command("claude", "proj@2", cwd=cwd)) == \
        "claude --dangerously-skip-permissions"


def test_이어가기는_끄거나_폴더를_모르면_안_붙는다(settings_file, tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    assert claude_body(settings.launch_command("claude", "proj")) == "claude --dangerously-skip-permissions"
    assert claude_body(settings.launch_command("claude", "", cfg=None, cwd=cwd)) == \
        "claude --dangerously-skip-permissions"
    settings.save({"cont": False})
    assert claude_body(settings.launch_command("claude", "proj", cwd=cwd)) == \
        "claude --dangerously-skip-permissions"
    assert settings.launch_command("codex", "proj", cwd=cwd) == "codex --yolo"   # 이어가기는 클로드에만


def test_attach_cmd_가_이어가기_명령을_넣는다(settings_file, tmp_path):
    (tmp_path / "proj").mkdir()
    got = sessions.attach_cmd("proj", root=tmp_path, agent="claude")
    assert "--continue" in got[got.index(";") - 1]
    tab = sessions.attach_cmd("proj@2", root=tmp_path, agent="claude")[got.index(";") - 1]
    assert "--continue" not in tab                      # 탭은 기본 세션 대화를 가로채지 않는다


def test_attach_cmd_shell_과_기본은_명령이_없다(settings_file, tmp_path):
    (tmp_path / "proj").mkdir()
    for got in (sessions.attach_cmd("proj", root=tmp_path),
                sessions.attach_cmd("proj", root=tmp_path, agent="shell")):
        assert not any(a.startswith("zsh -lic") for a in got)


# ── 에이전트 연결 상태 ──────────────────────────────────────────────────────

def test_probe_는_토큰을_내보내지_않는다(monkeypatch, tmp_path):
    """계정 표시는 email·org 뿐 — oauth 토큰·API 키가 API 응답으로 새면 안 된다."""
    claude_cfg = tmp_path / ".claude.json"
    claude_cfg.write_text(json.dumps({"oauthAccount": {
        "emailAddress": "me@example.com", "organizationName": "Example Org",
        "accountUuid": "비밀-uuid"}}))
    codex_auth = tmp_path / "auth.json"
    codex_auth.write_text(json.dumps({"OPENAI_API_KEY": "sk-비밀", "tokens": {"access_token": "비밀"}}))
    monkeypatch.setattr(config, "CLAUDE_CONFIG", claude_cfg)
    monkeypatch.setattr(config, "CODEX_AUTH", codex_auth)
    monkeypatch.setattr(settings, "_version", lambda b: "9.9.9")
    monkeypatch.setattr(settings, "_claude_doctor", lambda: {})
    got = settings.probe()
    assert got["claude"] == {"installed": True, "version": "9.9.9",
                             "email": "me@example.com", "org": "Example Org"}
    assert got["codex"] == {"installed": True, "version": "9.9.9", "loggedIn": True}
    assert "비밀" not in json.dumps(got, ensure_ascii=False)


def test_probe_은_아무것도_없어도_그려질_모양을_준다(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CLAUDE_CONFIG", tmp_path / "없음.json")
    monkeypatch.setattr(config, "CODEX_AUTH", tmp_path / "없음2.json")
    monkeypatch.setattr(settings, "_version", lambda b: None)
    got = settings.probe()
    assert got["claude"]["installed"] is False
    assert got["codex"] == {"installed": False, "version": None, "loggedIn": False}


DOCTOR_OUT = """\
Auto-updates: enabled
Auto-update channel: latest
Last update attempt: success → 2.1.233 (2026-08-14)
"""


def test_claude_doctor_자동업데이트_상태를_세줄만_파싱한다():
    assert settings.parse_claude_doctor(DOCTOR_OUT) == {
        "autoUpdates": "enabled", "channel": "latest",
        "lastAttempt": {"status": "success", "version": "2.1.233", "date": "2026-08-14"},
    }
    assert settings.parse_claude_doctor("형식이 바뀐 출력") == {}


# ── 최신 버전 비교·6시간 캐시 ─────────────────────────────────────────────

def test_버전비교는_문자열이_아니라_숫자파트로_한다():
    assert agent_updates.compare_versions("2.1.9", "2.1.10") == -1
    assert agent_updates.compare_versions("2.1.10", "2.1.9") == 1
    assert agent_updates.compare_versions("1.2", "1.2.0") == 0


def test_codex_github_tag와_설치본은_같은_버전으로_판정한다():
    assert agent_updates.compare_versions("rust-v0.147.0", "codex-cli 0.147.0") == 0


def test_최신버전_조회실패면_배지필드를_생략한다():
    local = {"claude": {"installed": True, "version": "2.1.233 (Claude Code)"},
             "codex": {"installed": True, "version": "codex-cli 0.147.0"}}
    got = agent_updates.merge_latest(local, {"claude": None, "codex": "깨진-tag"})
    assert "latestVersion" not in got["claude"]
    assert "updateAvailable" not in got["claude"]
    assert "latestVersion" not in got["codex"]


def test_최신버전_성공이면_새판과_최신을_숫자비교해_표시한다():
    local = {"claude": {"installed": True, "version": "2.1.9 (Claude Code)"},
             "codex": {"installed": True, "version": "codex-cli 0.147.0"}}
    got = agent_updates.merge_latest(local, {"claude": "2.1.10", "codex": "rust-v0.147.0"})
    assert got["claude"]["latestVersion"] == "2.1.10"
    assert got["claude"]["updateAvailable"] is True
    assert got["codex"]["latestVersion"] == "0.147.0"
    assert got["codex"]["updateAvailable"] is False


async def test_최신버전_HTTP응답_파싱은_가짜_session만_쓴다():
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def json(self): return {"tag_name": "rust-v0.147.0"}

    class Session:
        def get(self, url):
            assert url == agent_updates.LATEST_SPECS["codex"][0]
            return Response()

    assert await agent_updates._fetch_one(
        Session(), *agent_updates.LATEST_SPECS["codex"]) == "rust-v0.147.0"


async def test_최신버전_API결과는_성공실패모두_6시간_캐시한다(monkeypatch):
    calls = []

    async def fake_fetch():
        calls.append(1)
        return {"claude": "2.1.233", "codex": None}  # 실패(None)도 매번 재호출하지 않는다

    monkeypatch.setattr(agent_updates, "_fetch_latest_versions", fake_fetch)
    monkeypatch.setattr(agent_updates, "_LATEST_CACHE", [0.0, None])
    monkeypatch.setattr(agent_updates, "_LATEST_FETCH_TASK", None)
    clock = [100.0]
    now = lambda: clock[0]
    assert await agent_updates.latest_versions(now) == {"claude": "2.1.233", "codex": None}
    clock[0] += config.AGENT_LATEST_TTL - 1
    assert await agent_updates.latest_versions(now) == {"claude": "2.1.233", "codex": None}
    assert len(calls) == 1
    clock[0] += 1
    await agent_updates.latest_versions(now)
    assert len(calls) == 2


def test_업데이트_실행은_별칭없이_고정_리스트인자만_쓴다(monkeypatch):
    calls = []
    versions = iter(["2.1.232 (Claude Code)", "2.1.233 (Claude Code)"])

    class Done:
        returncode, stdout, stderr = 0, "updated", ""

    monkeypatch.setattr(settings, "which", lambda name: "/fixed/bin/claude")
    monkeypatch.setattr(settings, "_version", lambda name: next(versions))
    monkeypatch.setattr(settings, "invalidate_status", lambda: None)
    monkeypatch.setattr(agent_updates, "invalidate_latest", lambda: None)
    monkeypatch.setattr(agent_updates.subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)) or Done())
    got = agent_updates.run_update("claude")
    assert got["success"] is True and got["updated"] is True
    assert calls == [(["/fixed/bin/claude", "update"], {
        "capture_output": True, "text": True, "timeout": config.AGENT_UPDATE_TIMEOUT})]
    assert "shell" not in calls[0][1]


USAGE_JSON = {
    "schemaVersion": 2,
    "accounts": {
        "1": {"email": "me@example.com", "fetchedAt": 1_000_000.0,
              "lastGood": {"five_hour": {"pct": 15.0, "resets_at": "2026-10-12T23:00:00+00:00", "clock": "08:00"},
                           "seven_day": {"pct": 19.0, "resets_at": "2026-10-18T15:00:00+00:00", "clock": "Oct 19 00:00"},
                           "scoped": [{"name": "Fable", "pct": 4.0, "resets_at": "2026-10-18T15:00:00+00:00",
                                       "clock": "Oct 19 00:00"}]}},
        "2": {"email": "work@example.com", "fetchedAt": 1_000_100.0,
              "lastGood": {"five_hour": {"pct": 38.0, "resets_at": "2026-10-12T23:00:00+00:00", "clock": "04:19"},
                           "seven_day": {"pct": 26.0, "resets_at": "2026-10-18T15:00:00+00:00", "clock": "Aug 20 22:59"},
                           "scoped": []}},
    },
}
NOW = 1_000_200.0


def test_parse_usage_는_캐시_파일을_읽고_로그인_계정을_고른다():
    got = settings.parse_usage(USAGE_JSON, "me@example.com", now=NOW)
    assert got["account"] == "me@example.com"
    assert [(w["name"], w["pct"], w["reset"]) for w in got["windows"]] == [
        ("5h", 15, "08:00"), ("7d", 19, "Oct 19 00:00"), ("Fable", 4, "Oct 19 00:00")]
    assert all(w["left"].endswith("h") for w in got["windows"])   # NOW(1970년대) 기준 며칠 뒤 — "Nd Nh" 꼴로 다시 센다


def test_parse_usage_는_로그인_계정이_없으면_가장_최근_것():
    assert settings.parse_usage(USAGE_JSON, "", now=NOW)["account"] == "work@example.com"
    assert settings.parse_usage(USAGE_JSON, "nobody@x.y", now=NOW)["account"] == "work@example.com"


def test_parse_usage_는_낡은_캐시와_이상한_형식에_빈값():
    """cswap 이 멈춰 캐시가 안 갱신되면 그 숫자를 지금 값인 양 보여 주지 않는다 — 칸을 접을 뿐."""
    assert settings.parse_usage(USAGE_JSON, "me@example.com", now=NOW + settings.USAGE_STALE_S + 1) == {}
    assert settings.parse_usage({}, "", now=NOW) == {}
    assert settings.parse_usage({"accounts": []}, "", now=NOW) == {}
    assert settings.parse_usage({"accounts": {"1": {"email": "a@b", "fetchedAt": NOW, "lastGood": {}}}}, "", now=NOW) == {}
    assert settings.parse_usage("garbage", "", now=NOW) == {}


def test_countdown_은_절대시각으로_다시_센다():
    assert settings._countdown("2026-10-12T23:00:00+00:00", 1_791_836_425.0) == "2h 39m"   # 캐시 실측과 같은 값
    assert settings._countdown("2026-10-18T15:00:00+00:00", 1_791_836_425.0) == "5d 18h"
    assert settings._countdown(None, 0) == "" and settings._countdown("not-a-date", 0) == ""


def test_probe_usage_는_파일만_읽고_어떤_명령도_실행하지_않는다(tmp_path, monkeypatch):
    """★ 2026-10-13 05:25 사고 — 5분마다 돌던 `cswap status` 가 라이브 토큰을 소비해 claude -p 크론 4시간 전멸.
    cswap 링크가 다시 살아나도 브릿지는 실행 자체를 하지 않는다."""
    def boom(*a, **k):
        raise AssertionError("subprocess 가 불렸다")
    monkeypatch.setattr(settings.subprocess, "run", boom)
    monkeypatch.setattr(settings.shutil, "which", lambda *a, **k: "/usr/local/bin/cswap")
    f = tmp_path / "usage.json"; f.write_text(json.dumps(USAGE_JSON))
    cj = tmp_path / "claude.json"; cj.write_text('{"oauthAccount": {"emailAddress": "work@example.com"}}')
    monkeypatch.setattr(settings, "USAGE_FILE", f)
    monkeypatch.setattr(settings, "CLAUDE_JSON", cj)
    monkeypatch.setattr(settings.time, "time", lambda: NOW)
    assert settings.probe_usage()["account"] == "work@example.com"
    monkeypatch.setattr(settings, "USAGE_FILE", tmp_path / "없음.json")
    assert settings.probe_usage() == {}


def test_status_는_TTL_동안_캐시(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "probe", lambda: calls.append(1) or {"n": len(calls)})
    monkeypatch.setattr(settings, "_STATUS_CACHE", [0.0, None])
    clock = [100.0]
    now = lambda: clock[0]
    assert settings.status(now) == {"n": 1}
    clock[0] += 10
    assert settings.status(now) == {"n": 1}          # TTL(60초) 안 — 다시 안 잰다
    clock[0] += config.AGENT_STATUS_TTL
    assert settings.status(now) == {"n": 2}


# ── 타입이 틀린 값은 400 으로 거절한다 (코덱스 교차점검 2026-10-11: 배열 agent 는 TypeError 500, "true" 는 조용히 False 저장) ──

@pytest.mark.parametrize("patch", [{"agent": ["claude"]}, {"agent": {"x": 1}}, {"agent": None},
                                   {"bypass": "true"}, {"rc": 1}, {"cont": None}])
def test_save_는_타입이_틀린_값을_ValueError_로_거절(settings_file, patch):
    with pytest.raises(ValueError):
        settings.save(patch)
    assert not settings_file.exists()          # 아무것도 저장되지 않았다


def test_save_는_불리언만_그대로_저장(settings_file):
    assert settings.save({"bypass": False, "rc": True})["bypass"] is False
    assert settings.load()["rc"] is True


def test_anti_시작_명령은_agy_에_bypass_만_붙고_이어가기는_안_붙는다(settings_file, tmp_path):
    """Antigravity CLI(agy 1.4.0)는 클로드와 같은 --dangerously-skip-permissions 를 쓴다. --continue 는
    폴더 기준인지 확인되지 않아 붙이지 않는다(엉뚱한 프로젝트 대화를 이을 위험). RC 도 클로드만."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    assert settings.AGENTS["anti"] == "agy"
    assert settings.launch_command("anti", "proj", cwd=cwd) == "agy --dangerously-skip-permissions"
    settings.save({"rc": True})                      # RC 켜도 anti 명령은 그대로
    assert settings.launch_command("anti", "proj", cwd=cwd) == "agy --dangerously-skip-permissions"
    settings.save({"bypass": False})
    assert settings.launch_command("anti", "proj", cwd=cwd) == "agy"
    assert settings.save({"agent": "anti"})["agent"] == "anti"   # 기본 에이전트로도 고를 수 있다
    with pytest.raises(settings.InvalidAlias):       # agy 는 보호 명령 — 별칭 이름으로 못 쓴다
        settings.save({"commands": {"claude": "agy"}})
