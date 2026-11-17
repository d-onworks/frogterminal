"""전체 끄기 / 전체 복원 테스트 — 끄는 쪽이 틀리면 남의 세션이 죽고,
복원이 틀리면 사용자가 '방금까지 있던 것'을 잃는다. 둘 다 되돌릴 수 없어 계약을 촘촘히 못박는다.
"""
import json

import pytest

from bridge import config, restore, sessions


@pytest.fixture
def 작업공간(tmp_path, monkeypatch):
    """프로젝트 루트를 tmp 로 — 스냅샷 파일도 그 안(_frogterminal/)에 생긴다."""
    for name in ("hotel", "alpha", "delta"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(config, "projects_root", lambda: tmp_path)
    # 설정은 기본값 그대로 — attach_cmd 가 launch_command 로 다시 읽으므로 전체를 준다.
    monkeypatch.setattr(restore.settings, "load", lambda: dict(restore.settings.DEFAULTS))
    return tmp_path


def 살아있는세션(monkeypatch, names):
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: list(names))


# ── 무엇을 끄는가 ────────────────────────────────────────────────────────────

def test_사이드바가_보여주는_것만_끈다(작업공간, monkeypatch):
    """관문(is_valid_session)을 못 넘는 이름은 애초에 명단에 없다 — _uploads·_frogterminal 등."""
    살아있는세션(monkeypatch, ["hotel", "delta@2", "_uploads", "_frogterminal"])
    assert restore.killable() == ["hotel", "delta@2"]


def test_CPU_팝업은_전체끄기에_안_들어간다(작업공간, monkeypatch):
    """터미널이 아니라 도구창이다. 여기 섞이면 [전체 끄기] 한 번에 CPU 창이 함께 닫힌다."""
    살아있는세션(monkeypatch, ["hotel", config.MONITOR_SESSION])
    assert restore.killable() == ["hotel"]


def test_한_번에_쓸지_않고_이름마다_하나씩_끈다(작업공간, monkeypatch):
    """tmux 서버에는 프로그터미널이 안 만든 세션도 산다 — kill-server 는 그것까지 죽인다."""
    살아있는세션(monkeypatch, ["hotel", "alpha"])
    죽인것 = []
    monkeypatch.setattr(sessions, "kill_session", lambda n: 죽인것.append(n) or True)
    result = restore.kill_all()
    assert 죽인것 == ["hotel", "alpha"] == result["killed"]


def test_끄기_전에_명단부터_남긴다(작업공간, monkeypatch):
    """중간에 무엇이 터져도 복원 재료는 이미 디스크에 있어야 한다."""
    살아있는세션(monkeypatch, ["hotel", "alpha"])
    본것 = []

    def 끄면서_확인(name):
        본것.append(json.loads(restore.snapshot_file().read_text())["sessions"])
        return True
    monkeypatch.setattr(sessions, "kill_session", 끄면서_확인)
    restore.kill_all()
    assert 본것[0] == ["hotel", "alpha"]        # 첫 번째를 끄기 전에 이미 다 적혀 있다


def test_끌_것이_없으면_있던_명단을_지우지_않는다(작업공간, monkeypatch):
    """이미 다 꺼진 상태에서 버튼을 또 누르면 복원거리가 사라지는 것이 제일 나쁘다."""
    restore.save_snapshot(["hotel", "alpha"])
    살아있는세션(monkeypatch, [])
    restore.kill_all()
    assert restore.load_snapshot()["sessions"] == ["hotel", "alpha"]


def test_하나가_실패해도_나머지는_끈다(작업공간, monkeypatch):
    살아있는세션(monkeypatch, ["hotel", "alpha", "delta"])

    def 가끔실패(name):
        if name == "alpha":
            raise LookupError(name)
        return True
    monkeypatch.setattr(sessions, "kill_session", 가끔실패)
    result = restore.kill_all()
    assert result["killed"] == ["hotel", "delta"] and result["failed"] == ["alpha"]


# ── 무엇을 되살리는가 ────────────────────────────────────────────────────────

def test_복원은_명단에_있던_것만_되살린다(작업공간, monkeypatch):
    """'지금 열 수 있는 전부'가 아니라 '그때 열려 있던 것만' — 이게 이 기능의 핵심이다."""
    restore.save_snapshot(["hotel", "delta"])
    살아있는세션(monkeypatch, [])
    만든것 = []
    monkeypatch.setattr(restore, "_create_detached", lambda cmd: 만든것.append(cmd))
    result = restore.restore_all()
    assert result["restored"] == ["hotel", "delta"]
    assert [c[5] for c in 만든것] == ["hotel", "delta"]      # tmux new-session -d -A -s <이름>
    assert "alpha" not in str(만든것)                        # 명단에 없던 프로젝트는 안 켠다


def test_복원은_붙지_않고_만들기만_한다(작업공간, monkeypatch):
    """브릿지에는 붙을 화면이 없다 — -d 가 없으면 tmux 가 브릿지 프로세스에 붙어 매달린다."""
    restore.save_snapshot(["hotel"])
    살아있는세션(monkeypatch, [])
    만든것 = []
    monkeypatch.setattr(restore, "_create_detached", lambda cmd: 만든것.append(cmd))
    restore.restore_all()
    cmd = 만든것[0]
    assert cmd[:5] == ["tmux", "new-session", "-d", "-A", "-s"]
    assert "-c" in cmd and str(작업공간 / "hotel") in cmd     # 그 프로젝트 폴더에서 시작
    assert any("claude" in a or "cl " in a for a in cmd)        # 설정한 에이전트가 함께 뜬다


def test_이미_살아있으면_건드리지_않는다(작업공간, monkeypatch):
    """복원을 두 번 눌러도 멀쩡해야 한다 — 두 번째가 세션을 흔들면 하던 일이 끊긴다."""
    restore.save_snapshot(["hotel", "delta"])
    살아있는세션(monkeypatch, ["hotel"])
    monkeypatch.setattr(restore, "_create_detached",
                        lambda cmd: pytest.fail("살아있는 세션에 손대면 안 된다")
                        if "hotel" in cmd else None)
    result = restore.restore_all()
    assert result["already"] == ["hotel"] and result["restored"] == ["delta"]


def test_폴더가_사라진_이름은_실패로_알린다(작업공간, monkeypatch):
    """조용히 건너뛰면 사용자는 '복원됐다'고 믿는다. 못 살린 것은 이름으로 말한다."""
    restore.save_snapshot(["hotel", "없어진프로젝트"])
    살아있는세션(monkeypatch, [])
    monkeypatch.setattr(restore, "_create_detached", lambda cmd: None)
    result = restore.restore_all()
    assert result["restored"] == ["hotel"] and result["failed"] == ["없어진프로젝트"]


# ── 명단 파일 자체 ───────────────────────────────────────────────────────────

def test_명단이_없거나_깨져도_복원은_조용히_빈손(작업공간, monkeypatch):
    살아있는세션(monkeypatch, [])
    assert restore.load_snapshot()["sessions"] == []
    restore.snapshot_file().parent.mkdir(parents=True, exist_ok=True)
    for 쓰레기 in ("깨진 json", "[]", "null", '{"sessions": "hotel"}'):
        restore.snapshot_file().write_text(쓰레기)
        assert restore.load_snapshot()["sessions"] == []
    assert restore.restore_all() == {"restored": [], "already": [], "failed": []}


def test_손으로_고친_이름은_명단에서_걸러진다(작업공간, monkeypatch):
    """이 명단은 그대로 tmux 인자가 된다 — 파일이 곧 신뢰경계다."""
    restore.snapshot_file().parent.mkdir(parents=True, exist_ok=True)
    restore.snapshot_file().write_text(json.dumps(
        {"sessions": ["hotel", "../탈출", "; rm -rf /", 7, None]}))
    assert restore.load_snapshot()["sessions"] == ["hotel"]


def test_명단은_원자적으로_쓴다(작업공간):
    """반쪽 JSON 을 읽고 '복원할 게 없다'고 답하면 그 순간 복원거리가 사라진 것처럼 보인다."""
    restore.save_snapshot(["hotel"])
    saved = json.loads(restore.snapshot_file().read_text())
    assert saved["sessions"] == ["hotel"] and saved["at"]
    assert not list(restore.snapshot_file().parent.glob("*.tmp"))     # 임시파일이 남지 않는다


# ── 복원 명단의 에이전트 ─────────────────────────────────────────

def test_코덱스로_돌던_세션은_코덱스로_되살린다(작업공간, monkeypatch):
    """명단에 에이전트를 같이 적는다 — 없으면 코덱스 칸이 클로드로 뜬다."""
    살아있는세션(monkeypatch, ["hotel", "delta"])
    monkeypatch.setattr(sessions, "session_agents", lambda: {"delta": "codex"})
    monkeypatch.setattr(sessions, "kill_session", lambda name: True)
    restore.kill_all()
    assert restore.load_snapshot()["agents"] == {"delta": "codex"}
    살아있는세션(monkeypatch, [])
    만든것 = []
    monkeypatch.setattr(restore, "_create_detached", lambda cmd: 만든것.append(cmd))
    restore.restore_all()
    by_name = {c[5]: " ".join(c) for c in 만든것}
    assert "codex" in by_name["delta"] and "codex" not in by_name["hotel"]


def test_명단의_에이전트는_아는_것만(작업공간):
    snap = restore.parse_snapshot({"sessions": ["hotel", "../x"],
                                   "agents": {"hotel": "rm -rf", "delta": "codex"}})
    assert snap["sessions"] == ["hotel"] and snap["agents"] == {}
