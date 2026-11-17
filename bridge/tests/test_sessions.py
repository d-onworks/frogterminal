import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bridge import sessions

def make_root(tmp_path: Path) -> Path:
    (tmp_path / "hotel").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "_uploads").mkdir()          # 제외 대상
    (tmp_path / ".hidden").mkdir()           # 제외 대상
    (tmp_path / "afile.txt").write_text("x") # 파일은 제외
    return tmp_path

def test_folders_listed_sorted_and_filtered(tmp_path):
    assert sessions.list_project_folders(make_root(tmp_path)) == ["alpha", "hotel"]

def test_merged_marks_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["dev", "hotel"])
    monkeypatch.setattr(sessions, "session_agents", lambda: {"dev": "codex"})
    got = sessions.merged_sessions(make_root(tmp_path))
    by = {s["name"]: s for s in got}
    assert by["dev"]["agent"] == "codex" and by["hotel"]["agent"] is None   # 코덱스만 표시
    assert by["hotel"]["alive"] is True and by["hotel"]["path"]
    assert by["alpha"]["alive"] is False
    assert by["dev"]["alive"] is True and by["dev"]["path"] is None  # 폴더 없는 기존 세션
    assert [s["name"] for s in got][:2] == ["dev", "hotel"]       # alive 우선


def test_CLAUDE나_AGENTS가_바로_아래_있는_폴더만_프로젝트(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    (root / "hotel" / "CLAUDE.md").write_text("# project")
    (root / "alpha" / "AGENTS.md").write_text("# project")
    (root / "plain").mkdir()
    (root / "nested").mkdir()
    (root / "nested" / "docs").mkdir()
    (root / "nested" / "docs" / "AGENTS.md").write_text("바로 아래가 아님")
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["orphan"])
    by = {s["name"]: s for s in sessions.merged_sessions(root)}
    assert by["hotel"]["project"] is True
    assert by["alpha"]["project"] is True
    assert by["plain"]["project"] is False and by["nested"]["project"] is False
    assert by["orphan"]["project"] is True and by["orphan"]["path"] is None


def test_git_저장소면_마커가_없어도_프로젝트(tmp_path, monkeypatch):
    """마커 파일만 기준으로 삼으면 실제 프로젝트가 무더기로 숨는다.

    2026-08-18 실측 — ~/work 의 git 저장소 44개 중 CLAUDE.md·AGENTS.md 를 가진 건
    22개뿐이라, delta·donworks·kakao·storymaker 는 물론 frogterminal 자기 자신까지
    '프로젝트 아님'으로 숨겨졌다. 마커는 있으면 좋은 것이지 프로젝트의 정의가 아니다.

    git worktree 는 .git 이 디렉터리가 아니라 파일(gitdir 포인터)이라, is_dir 로 보면
    워크트리를 또 놓친다. exists 로 봐야 한다.
    """
    root = make_root(tmp_path)
    (root / "hotel" / ".git").mkdir()
    (root / "alpha" / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt")
    (root / "plain").mkdir()                       # git 도 마커도 없는 잡동사니
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    by = {s["name"]: s for s in sessions.merged_sessions(root)}
    assert by["hotel"]["project"] is True
    assert by["alpha"]["project"] is True       # 워크트리(.git 이 파일)도 프로젝트다
    assert by["plain"]["project"] is False         # 이런 것만 숨어야 한다


def test_새_프로젝트_버튼으로_만든_빈_폴더는_프로젝트다(tmp_path, monkeypatch):
    """2026-08-22 실사고 — [+ 새 프로젝트] 로 'demo' 를 만들고 다른 세션에 갔다 오니
    사라졌다. 빈 폴더라 .git 도 마커도 없어 '프로젝트 아님'으로 접힌 것. 사용자가 만들겠다고
    한 것은 그 자체로 프로젝트다 — 명단(created.json)에 있으면 내용과 무관하게 보여야 한다.
    """
    root = make_root(tmp_path)
    created = root / "_frogterminal" / "created.json"
    monkeypatch.setattr(sessions.config, "created_file", lambda: created)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    (root / "demo").mkdir()                        # 버튼이 만든 그대로 — 텅 빈 폴더
    (root / "plain").mkdir()                       # 손으로 만든 잡동사니 — 여전히 숨는다
    assert sessions.load_created() == set()        # 파일 없음 = 빈 명단
    assert sessions.mark_created("demo") == ["demo"]
    assert sessions.mark_created("demo") == ["demo"]             # 두 번 눌러도 한 줄
    assert json.loads(created.read_text()) == ["demo"]           # 기기가 아니라 서버에 남는다
    by = {s["name"]: s for s in sessions.merged_sessions(root)}
    assert by["demo"]["project"] is True
    assert by["plain"]["project"] is False
    # 폴더를 지우면 명단에 이름이 남아도 유령으로 뜨지 않는다
    (root / "demo").rmdir()
    assert "demo" not in {s["name"] for s in sessions.merged_sessions(root)}


@pytest.mark.parametrize("raw", ['{"a": 1}', "42", "{망가진", '["../etc", 3, "탭@2", "ok"]'])
def test_새_프로젝트_명단_파일이_이상해도_걸러진다(tmp_path, monkeypatch, raw):
    created = tmp_path / "_frogterminal" / "created.json"
    created.parent.mkdir(parents=True)
    created.write_text(raw)
    monkeypatch.setattr(sessions.config, "created_file", lambda: created)
    assert sessions.load_created() in (set(), {"ok"})


def test_루트밖_심링크_폴더는_목록과_세션_관문을_못_뚫는다(tmp_path, monkeypatch):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (outside / "CLAUDE.md").write_text("# outside")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["escape"])
    assert "escape" not in sessions.list_project_folders(root)
    assert sessions.is_valid_session("escape", root) is False

def test_ignored_폴더는_세션이_아니다(tmp_path, monkeypatch):
    """_uploads(감사로그)·_frogterminal(결재 카드)은 파일 API·attach 의 대상이 아니다.

    목록에서만 빼면 관문(is_valid_session)은 그대로 열려 있어, append-only 감사로그가
    그 감사 대상 API 로 읽히고 덮어써진다(실증). 관문에서 같이 막는다.
    """
    root = make_root(tmp_path)
    (tmp_path / "_frogterminal").mkdir()
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["_uploads"])
    assert sessions.is_valid_session("_uploads", root) is False   # 같은 이름의 tmux 세션이 있어도
    assert sessions.is_valid_session("_frogterminal", root) is False
    assert "_frogterminal" not in sessions.list_project_folders(root)   # 사이드바 유령 프로젝트

def test_valid_rejects_traversal_and_unknown(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["dev"])
    assert sessions.is_valid_session("hotel", root) is True
    assert sessions.is_valid_session("dev", root) is True
    assert sessions.is_valid_session("../etc", root) is False
    assert sessions.is_valid_session("nope", root) is False
    assert sessions.is_valid_session("", root) is False

MOUSE_ON = [";", "set-option", "-t", "dev", "mouse", "on"]

def test_attach_cmd(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["dev"])
    cmd = sessions.attach_cmd("hotel", root)
    assert cmd[:5] == ["tmux", "new-session", "-A", "-s", "hotel"]
    assert cmd[5] == "-c" and cmd[6] == str(root / "hotel")
    assert sessions.attach_cmd("dev", root)[:5] == ["tmux", "new-session", "-A", "-s", "dev"]

def test_attach_cmd_turns_on_mouse_for_that_session_only(tmp_path, monkeypatch):
    """폰 스와이프가 휠 리포트로 오면 tmux 가 copy-mode 스크롤로 받아야 한다 → 그 세션만 mouse on."""
    root = make_root(tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["dev"])
    for name in ("dev", "hotel"):                     # 폴더 없는 세션·있는 세션 둘 다
        cmd = sessions.attach_cmd(name, root)
        # mouse on + status/pane-border-status off(창 1개뿐 — 이름표 반복 제거) — 그 세션·창만
        opts = cmd[-19 - len(sessions._COPY_BINDS):-len(sessions._COPY_BINDS)]
        t = f"={name}:"
        assert opts == [";", "set-option", "-t", t, "mouse", "on",
                        ";", "set-option", "-t", t, "status", "off",
                        ";", "set-option", "-w", "-t", t, "pane-border-status", "off"]
        assert "-g" not in cmd                           # 전역 '옵션'은 절대 건드리지 않는다
        # 드래그 복사 뒤 복사 모드에 남는 바인딩(키 테이블은 세션 단위가 없어 전역 — 2026-09-15)
        assert cmd[-len(sessions._COPY_BINDS):] == sessions._COPY_BINDS
        assert "copy-pipe-and-cancel" not in cmd         # 무조건 cancel 이 화면을 입력창으로 튀게 하던 원인
        # 맨 아래(scroll_position 0)면 복사 뒤 빠져나오고, 휠로 맨 아래에 닿아도 빠져나온다 — 안 그러면
        # 복사 모드에 갇혀 새 출력이 화면에 안 그려진다(2026-09-20 실사고)
        assert cmd.count("send-keys -X cancel") == 4     # 드래그·휠 × copy-mode·copy-mode-vi
        assert cmd.count(";") == 7                       # mouse·status·border + 바인딩 넷

# ── 이름 모양 검사 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "../etc", "a/b", ".git", "a b", "dev\n", "d\ne"])
def test_is_valid_name_경로조작과_개행을_막는다(bad):
    """숨김 목록에 들어가는 이름도 이 검사를 탄다 — 임의 문자열이 파일에 쌓이면 안 된다.

    'dev\\n' 이 통과하면 안 되는 이유: 정규식의 '$' 는 끝 개행을 허용한다(clear_card 가 겪은 함정).
    """
    assert sessions.is_valid_name(bad) is False


def test_is_valid_name_평범한_이름은_통과():
    assert sessions.is_valid_name("hotel") is True and sessions.is_valid_name("frog-1.2_a") is True


def test_is_valid_name_한글_이름도_통과():
    """★ 이 맥의 프로젝트 폴더에는 한글 이름이 실재한다 — 영문만 허용하던 옛 규칙이
    '보고서양식-시안' 의 숨기기(400)·터미널 열기(404)를 전부 막았다(2026-08-15 실측)."""
    assert sessions.is_valid_name("보고서양식-시안") is True
    assert sessions.is_valid_name("한글_프로젝트.v2") is True
    assert sessions.is_valid_name("한글 공백") is False            # 공백·경로문자는 여전히 막는다
    assert sessions.is_valid_name("한글/탈출") is False

# ── 세션 끄기 ────────────────────────────────────────────────────────────────

def test_kill_session_지목한_이름_하나만_끈다(monkeypatch):
    """'다른 세션은 죽이지 않는다' 원칙의 예외는 여기 하나뿐 — 사용자가 콕 집은 그 세션.

    kill-server(전체 몰살)나 이름 없는 kill 이 새어 들어가면 다른 프로젝트가 통째로 날아간다.
    """
    calls = []
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["dev", "hotel"])
    monkeypatch.setattr(sessions.subprocess, "run",
                        lambda cmd, **kw: calls.append((cmd, kw))
                        or subprocess.CompletedProcess(cmd, 0))
    assert sessions.kill_session("dev") is True
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd == ["tmux", "kill-session", "-t", "=dev:"]
    assert kw["check"] is True and kw["timeout"] == 5     # 멈춘 tmux 가 브릿지를 붙잡지 않게


def test_kill_session_이미_꺼졌으면_멱등하게_False(monkeypatch):
    """사용자가 두 번 눌러도 에러가 아니라 '이미 꺼져 있었다'로 조용히 닫는다."""
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(sessions, "_kill_tmux",
                        lambda n: pytest.fail("죽은 세션에 tmux 를 부르면 안 된다"))
    assert sessions.kill_session("dev") is False


def test_kill_session_tmux_실패는_LookupError(monkeypatch):
    """서브프로세스 실패가 그대로 새면 핸들러가 못 잡아 500 이 된다 —
    send_answer 와 같은 잣대로 LookupError(=404 세션 없음)에 맞춘다."""
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["dev"])   # 실패 뒤에도 살아있다
    def boom(name):
        raise subprocess.CalledProcessError(1, "tmux")
    monkeypatch.setattr(sessions, "_kill_tmux", boom)
    with pytest.raises(LookupError):
        sessions.kill_session("dev")


def test_kill_session_먼저_죽어_실패한_경우는_멱등(monkeypatch):
    """검사와 kill 사이에 세션이 죽는 창(TOCTOU). 결과가 '꺼짐'이면 원하던 그대로라 에러가 아니다."""
    live = ["dev"]
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: list(live))
    def boom(name):
        live.clear()
        raise subprocess.TimeoutExpired("tmux", 5)
    monkeypatch.setattr(sessions, "_kill_tmux", boom)
    assert sessions.kill_session("dev") is False

# ── 프로젝트 숨김 ─────────────────────────────────────────────────────────────

@pytest.fixture
def hidden_file(tmp_path, monkeypatch):
    """숨김 목록은 기기(localStorage)가 아니라 서버 파일 — 폰·맥이 같은 것을 본다."""
    path = tmp_path / "_frogterminal" / "hidden.json"
    monkeypatch.setattr(sessions.config, "hidden_file", lambda: path)
    return path


def test_hidden_넣고_빼기가_파일에_남는다(hidden_file):
    assert sessions.load_hidden() == set()                       # 파일 없음 = 빈 목록
    assert sessions.set_hidden("hotel", True) == ["hotel"]
    assert sessions.set_hidden("alpha", True) == ["alpha", "hotel"]   # 정렬해서 돌려준다
    assert sessions.load_hidden() == {"alpha", "hotel"}
    assert sessions.set_hidden("hotel", False) == ["alpha"]
    assert json.loads(hidden_file.read_text()) == ["alpha"]


def test_hidden_깨진_json이어도_죽지_않고_다시_쓴다(hidden_file):
    hidden_file.parent.mkdir(parents=True)
    hidden_file.write_text("{망가진")
    assert sessions.load_hidden() == set()                       # 빈 목록으로 시작
    assert sessions.set_hidden("dev", True) == ["dev"]           # 그 위에 정상 파일로 덮는다


@pytest.mark.parametrize("raw", ['{"a": 1}', '"dev"', "42", "null", '["../etc", 3, null, "ok"]'])
def test_hidden_파일_내용이_이상하면_걸러진다(hidden_file, raw):
    """손으로 고친 파일이 이상한 이름을 그대로 API 응답에 실어보내면 안 된다."""
    hidden_file.parent.mkdir(parents=True, exist_ok=True)
    hidden_file.write_text(raw)
    assert sessions.load_hidden() <= {"ok"}


def test_hidden_원자적으로_써서_잔재를_안_남긴다(hidden_file):
    sessions.set_hidden("dev", True)
    assert [p.name for p in hidden_file.parent.iterdir()] == ["hidden.json"]


def test_merged_sessions_숨김은_표시만_하고_목록에서_빼지_않는다(tmp_path, monkeypatch, hidden_file):
    """웹이 접어 두고 '숨긴 항목 N개 보기'로 다시 펴야 하므로 서버는 목록에서 빼지 않는다."""
    root = make_root(tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    sessions.set_hidden("hotel", True)
    by = {s["name"]: s for s in sessions.merged_sessions(root)}
    assert set(by) == {"alpha", "hotel"}                   # 빠지지 않았다
    assert by["hotel"]["hidden"] is True
    assert by["alpha"]["hidden"] is False


# ── 프로젝트 즐겨찾기 ────────────────────────────────────────────────────────
# 숨김과 같은 자리(서버 파일)에 두되 set 이 아니라 list 다 — 사용자가 올린 '순서'가 곧 맨 위 순서다.

@pytest.fixture
def fav_file(tmp_path, monkeypatch):
    path = tmp_path / "_frogterminal" / "favorites.json"
    monkeypatch.setattr(sessions.config, "favorites_file", lambda: path)
    return path


def test_favorites_누른_순서가_그대로_남는다(fav_file):
    assert sessions.load_favorites() == []                       # 파일 없음 = 빈 목록
    assert sessions.set_favorite("hotel", True) == ["hotel"]
    # 이름순이었다면 alpha 가 앞이다 — 정렬하지 않는다는 것이 이 기능의 전부다
    assert sessions.set_favorite("alpha", True) == ["hotel", "alpha"]
    assert sessions.load_favorites() == ["hotel", "alpha"]
    assert json.loads(fav_file.read_text()) == ["hotel", "alpha"]
    assert sessions.set_favorite("hotel", False) == ["alpha"]
    assert json.loads(fav_file.read_text()) == ["alpha"]


def test_favorites_이미_있는_이름은_자리를_옮기지_않는다(fav_file):
    """7초 폴링이 다시 그릴 때마다 줄이 뛰면 사용자가 누르려던 줄을 놓친다."""
    sessions.set_favorite("hotel", True)
    sessions.set_favorite("alpha", True)
    assert sessions.set_favorite("hotel", True) == ["hotel", "alpha"]


def test_favorites_깨진_json이어도_죽지_않고_다시_쓴다(fav_file):
    fav_file.parent.mkdir(parents=True)
    fav_file.write_text("{망가진")
    assert sessions.load_favorites() == []                       # 빈 목록으로 시작
    assert sessions.set_favorite("dev", True) == ["dev"]         # 그 위에 정상 파일로 덮는다


@pytest.mark.parametrize("raw", ['{"a": 1}', '"dev"', "42", "null",
                                 '["../etc", 3, null, "ok", "a b"]'])
def test_favorites_파일_내용이_이상하면_걸러진다(fav_file, raw):
    fav_file.parent.mkdir(parents=True, exist_ok=True)
    fav_file.write_text(raw)
    assert sessions.load_favorites() in ([], ["ok"])


def test_favorites_탭_이름은_읽을_때_걸러진다(fav_file):
    """즐겨찾기는 프로젝트(base) 단위다 — 손으로 넣은 탭 이름이 API 응답에 실리면 안 된다."""
    fav_file.parent.mkdir(parents=True)
    fav_file.write_text('["hotel@2", "hotel", "hotel"]')
    assert sessions.load_favorites() == ["hotel"]             # 탭도, 중복도 빠진다


def test_favorites_원자적으로_써서_잔재를_안_남긴다(fav_file):
    sessions.set_favorite("dev", True)
    assert [p.name for p in fav_file.parent.iterdir()] == ["favorites.json"]


def test_merged_sessions_에_즐겨찾기_순번이_실린다(tmp_path, monkeypatch, fav_file):
    """웹은 이 순번만 보고 맨 위로 세운다 — 서버는 목록 순서를 바꾸지 않는다(숨김과 같은 잣대)."""
    root = make_root(tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    sessions.set_favorite("hotel", True)
    by = {s["name"]: s for s in sessions.merged_sessions(root)}
    assert by["hotel"]["fav"] == 0
    assert by["alpha"]["fav"] is None                         # 즐겨찾기가 아니면 null
    sessions.set_favorite("alpha", True)
    by = {s["name"]: s for s in sessions.merged_sessions(root)}
    assert (by["hotel"]["fav"], by["alpha"]["fav"]) == (0, 1)   # 누른 순서 그대로


# ---------- 병렬 터미널 탭 (base@2~9) — 같은 프로젝트에서 터미널 여러 개 ----------

def test_base_of_는_탭_이름에서_프로젝트를_뽑는다():
    assert sessions.base_of("hotel@2") == "hotel"
    assert sessions.base_of("hotel") == "hotel"
    assert sessions.base_of("a@10") == "a@10"        # 두 자리는 탭 접미사가 아니다
    assert sessions.base_of("a@1") == "a@1"          # 1번 = 기본 세션, 접미사로 안 쓴다


def test_탭_이름은_실제_프로젝트에만_새로_연다(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    assert sessions.is_valid_session("hotel@2", root) is True
    assert sessions.is_valid_session("ghost@2", root) is False       # 없는 폴더엔 못 연다
    assert sessions.is_valid_session("_uploads@2", root) is False    # 관문 우회 불가
    assert sessions.is_valid_session("hotel@1", root) is False    # @1·@10 은 이름 자체가 무효
    assert sessions.is_valid_session("hotel@10", root) is False
    assert sessions.is_valid_session("..@2", root) is False


def test_살아있는_고아_탭은_관문을_통과한다(tmp_path, monkeypatch):
    """폴더를 지운 뒤에도 살아있는 탭은 붙어서 확인하고 끌 수 있어야 한다. 단 _uploads 는 예외 없이 차단."""
    root = make_root(tmp_path)
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["ghost@2", "_uploads@2"])
    assert sessions.is_valid_session("ghost@2", root) is True
    assert sessions.is_valid_session("_uploads@2", root) is False


def test_attach_cmd_탭도_프로젝트_폴더에서_시작한다(tmp_path):
    root = make_root(tmp_path)
    cmd = sessions.attach_cmd("hotel@2", root)
    assert cmd[cmd.index("-c") + 1] == str(root / "hotel")        # cwd = 프로젝트 폴더
    assert "hotel@2" in cmd                                       # 세션 이름은 탭 이름 그대로


def test_merged_sessions_탭은_줄이_아니라_tabs_로_접힌다(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "list_tmux_sessions",
                        lambda: ["hotel", "hotel@3", "hotel@2", "ghost@2"])
    got = sessions.merged_sessions(make_root(tmp_path))
    by = {s["name"]: s for s in got}
    assert by["hotel"]["tabs"] == [2, 3]
    assert "hotel@2" not in by                    # 사이드바 줄로는 안 나온다
    assert by["ghost@2"]["alive"] is True            # 고아 탭(폴더 없음)은 줄로 남아 보인다
    assert by["alpha"]["tabs"] == []


def test_merged_sessions_탭만_살아도_프로젝트는_켜짐(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["hotel@2"])
    by = {s["name"]: s for s in sessions.merged_sessions(make_root(tmp_path))}
    assert by["hotel"]["alive"] is True and by["hotel"]["tabs"] == [2]


# ---------- CPU 팝업 세션 (_frogtop) — 사이드바에 없는 htop 전용 세션 ----------

def test_모니터_세션은_관문을_통과하고_목록에는_없다(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    assert sessions.is_valid_session("_frogtop", root) is True
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["_frogtop", "hotel"])
    names = [s["name"] for s in sessions.merged_sessions(root)]
    assert "_frogtop" not in names and "hotel" in names


def test_모니터_attach는_htop을_띄운다(tmp_path):
    cmd = sessions.attach_cmd("_frogtop", make_root(tmp_path))
    assert "zsh -lc 'htop || top'" in cmd     # 설정 에이전트 대신 htop(없으면 top)
    assert "-c" not in cmd                    # 프로젝트 폴더 개념이 없다


def test_copy_buffer_는_tmux_버퍼를_상한까지만_준다(monkeypatch):
    class R:
        returncode = 0
        stdout = "복" * 200_000
    monkeypatch.setattr(sessions.subprocess, "run", lambda *a, **k: R())
    text, age = sessions.copy_buffer()
    assert len(text) == sessions.MAX_COPY_BYTES
    assert age is None                        # 만든 시각이 숫자로 안 오면 나이는 모른다


def test_copy_buffer_는_버퍼의_나이를_초로_준다(monkeypatch):
    """웹이 '누르기 전에 있던 옛 버퍼'를 가려내는 눈금."""
    now = 1_800_000_000
    outs = iter([("긁은 글자", 0), (f"{now - 30}\n{now - 999}\n", 0)])

    def run(*a, **k):
        stdout, code = next(outs)
        return type("R", (), {"stdout": stdout, "returncode": code})()
    monkeypatch.setattr(sessions.subprocess, "run", run)
    monkeypatch.setattr(sessions.time, "time", lambda: now)
    assert sessions.copy_buffer() == ("긁은 글자", 30)        # 맨 위(최신) 버퍼의 나이


def test_copy_buffer_는_버퍼가_없거나_실패해도_빈_문자열(monkeypatch):
    class Fail:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(sessions.subprocess, "run", lambda *a, **k: Fail())
    assert sessions.copy_buffer() == ("", None)
    def boom(*a, **k):
        raise OSError("tmux 없음")
    monkeypatch.setattr(sessions.subprocess, "run", boom)
    assert sessions.copy_buffer() == ("", None)


def test_session_agents_는_pane_명령이_codex_agy_인_세션만(monkeypatch):
    """코덱스 = "codex", Antigravity CLI = "agy"(→ anti) 만 잡는다. 클로드(버전 이름·node)·셸은 없는 것."""
    class Out:
        returncode = 0
        stdout = "kakao\tcodex\nother\t2.1.261\ndev\tzsh\nfrogterminal\tcodex\nlanding\tagy\n"
    monkeypatch.setattr(sessions.subprocess, "run", lambda *a, **k: Out())
    assert sessions.session_agents() == {"kakao": "codex", "frogterminal": "codex", "landing": "anti"}


def test_session_agents_는_tmux_가_없으면_빈_dict(monkeypatch):
    def boom(*a, **k):
        raise OSError("no tmux")
    monkeypatch.setattr(sessions.subprocess, "run", boom)
    assert sessions.session_agents() == {}


def test_leave_copy_mode_는_tmux_안에서_판단한다(monkeypatch):
    calls = []
    monkeypatch.setattr(sessions.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    sessions.leave_copy_mode("proj")
    assert calls == [["tmux", "if-shell", "-F", "-t", "=proj:", "#{pane_in_mode}",
                      "send-keys -t =proj: -X cancel"]]


def test_점_든_이름은_다시_붙을_때_정확한_표기로_붙는다(tmp_path, monkeypatch):
    """'Dr.thesis' 폴더 — new-session -A 가 '-s Dr.thesis' 를 'Dr 세션의 thesis 칸'으로 읽어 이미 켜진
    세션에 못 붙고 `can't find pane: thesis` 로 끝났다(2026-11-17 다른 사용자 신고)."""
    root = make_root(tmp_path)
    (root / "Dr.thesis").mkdir()
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: [])
    assert sessions.attach_cmd("Dr.thesis", root)[:5] == ["tmux", "new-session", "-A", "-s", "Dr.thesis"]
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["Dr.thesis", "Dr"])
    cmd = sessions.attach_cmd("Dr.thesis", root)
    assert cmd[:4] == ["tmux", "attach-session", "-t", "=Dr.thesis:"]
    assert "new-session" not in cmd
    assert all(cmd[i + 1] == "=Dr.thesis:" for i, a in enumerate(cmd) if a == "-t")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux 없음")
def test_점_든_이름_실제_tmux_로_재접속과_대상(tmp_path):
    """진짜 tmux(격리 소켓)로 — 켜진 'Dr.thesis' 에 다시 붙는 명령이 성공하고, 옆의 'Dr' 세션은 안 건드린다."""
    sock = ["-L", f"frogdot-{os.getpid()}"]
    run = lambda *a: subprocess.run(["tmux", *sock, *a], capture_output=True, text=True, timeout=5)
    try:
        run("new-session", "-d", "-s", "Dr.thesis", "sleep 30")
        run("new-session", "-d", "-s", "Dr", "sleep 30")
        t = sessions.tmux_target("Dr.thesis")
        assert run("set-option", "-t", t, "@frogdot", "yes").returncode == 0
        assert run("show-options", "-v", "-t", "=Dr:", "@frogdot").stdout.strip() == ""
        assert run("show-options", "-v", "-t", t, "@frogdot").stdout.strip() == "yes"
        assert run("has-session", "-t", t).returncode == 0
        assert run("kill-session", "-t", t).returncode == 0
        assert run("list-sessions", "-F", "#{session_name}").stdout.split() == ["Dr"]
    finally:
        run("kill-server")


def test_leave_copy_mode_는_실패를_삼킨다(monkeypatch):
    def boom(*a, **k):
        raise OSError("no tmux")
    monkeypatch.setattr(sessions.subprocess, "run", boom)
    sessions.leave_copy_mode("proj")           # 예외 없이 끝난다


# ---------- 다른 PC 터미널 배지 — ssh 로 다른 기계에 붙은 세션 ----------

def test_ssh_target_은_옵션을_건너뛰고_목적지_호스트만_뽑는다():
    assert sessions.ssh_target(["ssh", "-t", "srvpc"]) == "srvpc"
    assert sessions.ssh_target(["ssh", "srvpc"]) == "srvpc"
    assert sessions.ssh_target(["ssh", "-p", "2222", "-l", "admin", "winpc"]) == "winpc"
    assert sessions.ssh_target(["ssh", "-o", "StrictHostKeyChecking=no", "admin@100.67.6.106"]) == "100.67.6.106"
    assert sessions.ssh_target(["ssh", "ssh://admin@winpc2:22"]) == "winpc2"
    assert sessions.ssh_target(["ssh", "-tt", "winpc", "cmd"]) == "winpc"     # 원격 명령은 호스트가 아니다
    assert sessions.ssh_target(["ssh"]) is None
    assert sessions.ssh_target(["zsh", "-lic", "cl"]) is None


def test_ssh_remote_cwd_는_원격_명령의_cd_폴더를_읽는다():
    assert sessions.ssh_remote_cwd(["ssh", "-t", "srvpc", "cd /d C:\\rproj && cmd"]) == "C:\\rproj"
    assert sessions.ssh_remote_cwd(["ssh", "-t", "srvpc", 'cd "C:\\my app" && cmd']) == "C:\\my app"
    assert sessions.ssh_remote_cwd(["ssh", "admin@winpc", "cd", "/srv/app;", "bash"]) == "/srv/app"
    assert sessions.ssh_remote_cwd(["ssh", "-t", "srvpc"]) is None
    assert sessions.ssh_remote_cwd(["zsh", "-lic", "cl"]) is None


def test_session_remotes_는_ssh_pane_만_시작명령에서_호스트와_폴더를_읽는다(monkeypatch):
    class Out:
        returncode = 0
        stdout = ('srvpc\tssh\t90985\tC:\\rproj\t"ssh -t srvpc"\n'         # @remote_path 옵션이 1순위
                  'srv\tssh\t90986\t\t"ssh -t winpc \'cd /d D:\\srv && cmd\'"\n'  # 옵션 없으면 원격 명령의 cd
                  'bare\tssh\t90987\t\t"ssh winpc2"\n'                        # 둘 다 없으면 폴더 None
                  'frogterminal\t2.1.289\t15124\t\t"zsh -lic \'cl --remote-control frogterminal\'"\n'
                  'dev\tzsh\t100\t\t""\n')
    monkeypatch.setattr(sessions.subprocess, "run", lambda *a, **k: Out())
    assert sessions.session_remotes() == {
        "srvpc": {"host": "srvpc", "path": "C:\\rproj"},
        "srv": {"host": "winpc", "path": "D:\\srv"},
        "bare": {"host": "winpc2", "path": None},
    }


def test_session_remotes_는_셸에서_손으로_친_ssh_도_ps_로_찾는다(monkeypatch):
    """pane 시작 명령이 셸이고 그 안에서 `ssh winpc` 을 쳤으면 자식 프로세스 인자에서 호스트를 읽는다."""
    class Tmux:
        returncode = 0
        stdout = 'proj\tssh\t500\t\t"zsh -lic \'cl --remote-control proj\'"\n'
    class Ps:
        returncode = 0
        stdout = "    1   500 zsh -lic cl --remote-control proj\n  500   777 ssh -p 2222 winpc\n  500   778 node hud.js\n"
    monkeypatch.setattr(sessions.subprocess, "run",
                        lambda cmd, **k: Tmux() if cmd[0] == "tmux" else Ps())
    assert sessions.session_remotes() == {"proj": {"host": "winpc", "path": None}}


def test_session_remotes_는_tmux_가_없으면_빈_dict(monkeypatch):
    def boom(*a, **k):
        raise OSError("no tmux")
    monkeypatch.setattr(sessions.subprocess, "run", boom)
    assert sessions.session_remotes() == {}


def test_merged_sessions_에_host_와_원격폴더가_실리고_로컬은_None(tmp_path, monkeypatch):
    """맥 로컬 세션은 지금과 똑같이(host=None, path=로컬 폴더) — 배지·원격 폴더는 ssh 세션에만."""
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["srvpc", "hotel"])
    monkeypatch.setattr(sessions, "session_agents", lambda: {})
    monkeypatch.setattr(sessions, "session_remotes",
                        lambda: {"srvpc": {"host": "srvpc", "path": "C:\\rproj"}})
    by = {s["name"]: s for s in sessions.merged_sessions(make_root(tmp_path))}
    assert by["srvpc"]["host"] == "srvpc" and by["srvpc"]["path"] == "C:\\rproj"
    assert by["hotel"]["host"] is None and by["hotel"]["path"].endswith("/hotel")
    assert by["alpha"]["host"] is None

@pytest.mark.parametrize("selected, copied", [
    ("", None), ("   ", None), (" \n\t\n", None),          # 빈 선택 — 클립보드(방금 찍은 캡처)를 덮지 않는다
    ("abc", "abc"), ("한글 줄\n둘째 줄", "한글 줄\n둘째 줄"),
])
def test_drag_copy_skips_blank_selection(tmp_path, selected, copied):
    """손이 살짝 움직인 클릭이 빈칸을 pbcopy 로 넘겨 캡처를 덮던 사고(2026-11-08) — 글자가 있을 때만 복사."""
    fake = tmp_path / "pbcopy"
    fake.write_text(f"#!/bin/sh\ncat > '{tmp_path}/out'\n")
    fake.chmod(0o755)
    subprocess.run(["sh", "-c", sessions._PBCOPY_NONEMPTY], input=selected.encode(), check=True,
                   env={"PATH": f"{tmp_path}:/usr/bin:/bin"})
    out = tmp_path / "out"
    assert (out.read_text() if out.exists() else None) == copied
    assert sessions._PBCOPY_NONEMPTY in sessions._COPY_BINDS

def test_켜진_세션이_있는_폴더는_마커가_없어도_프로젝트(tmp_path, monkeypatch):
    """파인더에서 만든 빈 폴더에 세션을 띄우면 숨긴 칸으로 접혀 안 보이던 사고(2026-11-08 avatar)."""
    root = make_root(tmp_path)
    (root / "avatar").mkdir()
    (root / "avatar" / "prompt.md").write_text("메모 하나")
    (root / "plain").mkdir()
    monkeypatch.setattr(sessions, "list_tmux_sessions", lambda: ["avatar"])
    by = {s["name"]: s for s in sessions.merged_sessions(root)}
    assert by["avatar"]["project"] is True and by["avatar"]["alive"] is True
    assert by["plain"]["project"] is False             # 꺼진 빈 폴더는 그대로 숨는다
