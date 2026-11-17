"""frog_hook(훅 수신기)·install_hooks(등록기) 테스트 — 실제 서브프로세스로 돌려 계약을 검증."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent.parent / "hooks" / "frog_hook.py"


def _load_hook():
    """훅을 모듈로 들여온다 — 조상 탐색 같은 순수 함수는 서브프로세스 없이 직접 본다.
    (훅은 브릿지를 import 하지 않는 독립 파일이라 패키지 경로가 없다.)"""
    spec = importlib.util.spec_from_file_location("frog_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)                  # __main__ 가드가 있어 main() 은 안 돈다
    return module


frog_hook = _load_hook()

# 실측 ps 출력 그대로 — 날짜 한 자리(Aug  4)는 공백이 둘이라 split() 로만 안전하게 갈린다.
PS_SAMPLE = """\
    1     0 Tue Aug 11 20:36:18 2026     /sbin/launchd
 1833 69917 Fri Aug 14 23:05:03 2026     claude
 7292  1833 Fri Aug 14 23:30:27 2026     /bin/zsh
 7294  7292 Fri Aug  4 23:30:27 2026     /opt/homebrew/.../Python
69917     1 Fri Aug 14 22:10:00 2026     -zsh
"""


def test_parse_ps_는_공백이_섞인_시작시각을_그대로_읽는다():
    table = frog_hook.parse_ps(PS_SAMPLE)
    assert table[1833] == (69917, "Fri Aug 14 23:05:03 2026", "claude")
    assert table[7294][1] == "Fri Aug 4 23:30:27 2026"      # 한 자리 날짜 — 공백을 접어 맞춘다


def test_owner_process_는_조상중_클로드를_찾는다():
    """훅은 클로드 → sh → python 이라 두세 칸 위에 주인이 있다."""
    table = frog_hook.parse_ps(PS_SAMPLE)
    assert frog_hook.owner_process(table, 7294) == (1833, "Fri Aug 14 23:05:03 2026")


def test_owner_process_는_클로드가_없으면_빈값():
    """클로드를 못 찾으면 카드에 pid 를 안 적는다 — 브릿지가 예전 기준(tmux)으로 물러선다."""
    table = frog_hook.parse_ps(PS_SAMPLE)
    assert frog_hook.owner_process(table, 69917) == (None, None)
    assert frog_hook.owner_process(table, 99999) == (None, None)      # 없는 pid


def test_owner_process_는_고리에_갇히지_않는다():
    """있을 수 없는 부모 관계라도 훅이 멈춰 서면 그 세션의 클로드가 함께 멈춘다."""
    고리 = {10: (11, "t", "sh"), 11: (10, "t", "sh")}
    assert frog_hook.owner_process(고리, 10) == (None, None)


def test_is_claude_는_버전_바이너리도_잡는다():
    """실행 경로가 …/share/claude/versions/2.1.232 인 세션이 실제로 있다."""
    assert frog_hook.is_claude("claude")
    assert frog_hook.is_claude("/Users/x/.local/share/claude/versions/2.1.232")
    assert not frog_hook.is_claude("/bin/zsh")
    assert not frog_hook.is_claude("/opt/homebrew/opt/node/bin/node")


def test_hook도_전역설정의_현재_루트를_안전하게_읽는다(tmp_path):
    home = tmp_path / "home"
    root = home / "work"
    root.mkdir(parents=True)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"projectsRoot": str(root)}))
    assert frog_hook.configured_root(str(settings_file), str(home)) == str(root.resolve())
    # 브라우저 API 검증을 우회해 설정 파일을 손으로 고쳐도 넓은 경로·루트 밖 심링크는 활성화 안 된다.
    for raw in ("/", str(home)):
        settings_file.write_text(json.dumps({"projectsRoot": raw}))
        assert frog_hook.configured_root(str(settings_file), str(home)) == str(home / "projects")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / "escape"
    link.symlink_to(outside, target_is_directory=True)
    settings_file.write_text(json.dumps({"projectsRoot": str(link)}))
    assert frog_hook.configured_root(str(settings_file), str(home)) == str(home / "projects")


def run_hook(tmp_path, payload, **env):
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
        capture_output=True, timeout=10,
        env={"FROG_STATE_DIR": str(tmp_path / "decisions"), "FROG_ROOT": str(tmp_path / "root"),
             "PATH": "/usr/bin:/bin", **env},
    )


def card_of(tmp_path, sid):
    return json.loads((tmp_path / "decisions" / f"{sid}.json").read_text())


def test_notification_권한은_permission_카드(tmp_path):
    (tmp_path / "root" / "frogterminal").mkdir(parents=True)
    r = run_hook(tmp_path, {"hook_event_name": "Notification", "session_id": "s1",
                            "cwd": str(tmp_path / "root" / "frogterminal"),
                            "message": "Claude needs your permission to use Bash"})
    assert r.returncode == 0 and r.stdout == ""
    c = card_of(tmp_path, "s1")
    assert c["kind"] == "permission" and c["project"] == "frogterminal"
    assert c["sessionId"] == "s1" and c["machine"] and c["updatedAt"]


# ── 어떤 알림이 '결재'인가 (2026-09-01 붙박이 결재 사고) ──────────────
# 클로드 코드의 알림 종류는 실측으로 확인한 것들이다(2.1.252 바이너리):
#   permission_prompt · worker_permission_prompt · agent_needs_input · idle_prompt
#   agent_completed · auth_success · push_notification · computer_use_* · elicitation_*
# 사람이 답해야 클로드가 나아가는 것만 결재다. 나머지는 알림일 뿐 — 이 경계가 무너지면
# 빨간 배지가 상시 켜지고, 그 안에 진짜 결재가 묻힌다.

def test_사람이_답해야_하는_알림만_결재로_올린다():
    kind = frog_hook.notification_kind
    assert kind("permission_prompt", "Claude needs your permission to use Bash") == "permission"
    assert kind("worker_permission_prompt", "…") == "permission"
    assert kind("agent_needs_input", "블로그 초안 needs your input") == "permission"


def test_백그라운드_작업_알림은_결재가_아니다():
    """★ 실사고 재현 — 'PiP 렌더링 및 검증 failed'(agent_completed)가 결재 1건으로 며칠 남았다.

    클로드 코드는 `${작업이름} ${실패면 failed 아니면 finished}` 로 이 알림을 만든다(바이너리 실측).
    작업 이름은 사용자가 지은 한글이라, 문구로는 결재와 구분할 단서가 전혀 없다 — 종류로 갈라야 한다.
    """
    kind = frog_hook.notification_kind
    assert kind("agent_completed", "채널소개 영상 PiP 파이프라인 렌더링 및 검증 failed") == "idle"
    assert kind("agent_completed", "블로그 자동 작성 finished") == "idle"
    for ntype in ("auth_success", "push_notification", "computer_use_enter",
                  "elicitation_complete", "그런_종류_없음"):
        assert kind(ntype, "무슨 말이든") == "idle", ntype


def test_종류를_모르면_클로드가_쓰는_두_문구로_건진다():
    """옛 버전엔 notification_type 이 없고, 새 버전은 종류를 더할 수 있다 — 문구가 마지막 그물."""
    kind = frog_hook.notification_kind
    assert kind("", "Claude needs your permission to use Bash") == "permission"
    assert kind("", "블로그 초안 needs your input") == "permission"
    assert kind("", "Claude is waiting for your input") == "idle"
    assert kind("", "무언가 failed") == "idle"


def test_작업실패_알림은_카드까지_결재가_아니어야_한다(tmp_path):
    """훅 전체를 실제로 돌려 계약을 못박는다 — 분류 함수만 맞고 배선이 틀리면 소용없다."""
    run_hook(tmp_path, {"hook_event_name": "Notification", "session_id": "s3", "cwd": "/tmp",
                        "notification_type": "agent_completed",
                        "message": "채널소개 영상 PiP 파이프라인 렌더링 및 검증 failed"})
    c = card_of(tmp_path, "s3")
    assert c["kind"] == "idle"                        # 결재(permission) 아님
    assert "PiP" in c["message"]                      # 무슨 일이 있었는지는 카드에 남는다


def test_notification_waiting은_idle_카드(tmp_path):
    run_hook(tmp_path, {"hook_event_name": "Notification", "session_id": "s1",
                        "cwd": "/tmp", "message": "Claude is waiting for your input"})
    c = card_of(tmp_path, "s1")
    assert c["kind"] == "idle" and c["project"] is None   # 루트 밖 cwd → project 없음


def test_stop은_done_userprompt는_working_sessionend는_삭제(tmp_path):
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "s2", "cwd": "/tmp"})
    assert card_of(tmp_path, "s2")["kind"] == "done"
    run_hook(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": "s2", "cwd": "/tmp"})
    assert card_of(tmp_path, "s2")["kind"] == "working"
    run_hook(tmp_path, {"hook_event_name": "SessionEnd", "session_id": "s2", "cwd": "/tmp"})
    assert not (tmp_path / "decisions" / "s2.json").exists()


def test_수동압축은_진행중이었다가_끝나면_완료로_내려온다(tmp_path):
    """/compact 에는 Stop 이 안 울린다 — 이 두 훅이 없으면 압축 뒤 계속 '진행중…'에 묶인다."""
    run_hook(tmp_path, {"hook_event_name": "PreCompact", "session_id": "s6",
                        "cwd": "/tmp", "trigger": "manual"})
    c = card_of(tmp_path, "s6")
    assert c["kind"] == "working" and c["compactTrigger"] == "manual"
    run_hook(tmp_path, {"hook_event_name": "SessionStart", "session_id": "s6",
                        "cwd": "/tmp", "source": "compact"})
    c = card_of(tmp_path, "s6")
    assert c["kind"] == "done" and "compactTrigger" not in c    # 다음 압축을 오염시키지 않게


def test_자동압축은_하던_일이_이어지므로_진행중을_유지한다(tmp_path):
    """자동 압축은 턴 한가운데서 일어난다 — 여기서 '완료'로 내리면 일하는 세션이 완료로 보인다."""
    run_hook(tmp_path, {"hook_event_name": "PreCompact", "session_id": "s7",
                        "cwd": "/tmp", "trigger": "auto"})
    run_hook(tmp_path, {"hook_event_name": "SessionStart", "session_id": "s7",
                        "cwd": "/tmp", "source": "compact"})
    assert card_of(tmp_path, "s7")["kind"] == "working"


def test_쉬는_중_자동압축은_끝나면_다시_완료로_내려온다(tmp_path):
    """클로드 코드의 '쉬는 동안 압축'(idleCompaction)도 trigger=auto 로 온다 — 하던 일이 없으니
    압축이 끝나면 완료로 돌아가야 한다 (실측 2026-10-25: 진행중에 묶임)."""
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "s9", "cwd": "/tmp"})
    run_hook(tmp_path, {"hook_event_name": "PreCompact", "session_id": "s9",
                        "cwd": "/tmp", "trigger": "auto"})
    assert card_of(tmp_path, "s9")["kind"] == "working"
    run_hook(tmp_path, {"hook_event_name": "SessionStart", "session_id": "s9",
                        "cwd": "/tmp", "source": "compact"})
    assert card_of(tmp_path, "s9")["kind"] == "done"


def test_새_세션은_시작하자마자_완료_대기로_보인다(tmp_path):
    """startup·resume·clear 는 프롬프트 앞에 선 상태다. 압축이 아니면 앞 카드도 보지 않는다."""
    for source in ("startup", "resume", "clear"):
        run_hook(tmp_path, {"hook_event_name": "SessionStart", "session_id": "s8",
                            "cwd": "/tmp", "source": source})
        assert card_of(tmp_path, "s8")["kind"] == "done"


def test_tmux_안에서_돌면_inTmux_표식이_붙는다(tmp_path):
    """브릿지는 이 표식으로 '프로그터미널이 연 세션'만 결재에 올린다 — Zed 세션 카드 제외."""
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "s4", "cwd": "/tmp"},
             TMUX="/tmp/tmux-501/default,123,0")
    assert card_of(tmp_path, "s4")["inTmux"] is True
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "s5", "cwd": "/tmp"})
    assert card_of(tmp_path, "s5")["inTmux"] is False


def test_카드에_적힌_주인_프로세스를_브릿지가_그대로_알아본다(tmp_path):
    """★ 살아있음 판정의 양 끝이 실제로 맞물리는지 — 여기서만 확인할 수 있다.

    훅은 ps 로 주인 클로드의 시작시각을 적고, 브릿지는 다른 ps 로 읽어 문자열끼리 견준다.
    한쪽이 공백 한 칸만 달라도 살아있는 세션이 전부 '꺼짐'으로 뒤집히는데, 두 함수만
    따로 보면 그 어긋남이 안 보인다. (클로드 세션 밖에서 돌면 적을 주인이 없어 건너뛴다.)
    """
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "s3", "cwd": "/tmp"})
    card = card_of(tmp_path, "s3")
    if "pid" not in card:                       # 조상에 클로드가 없는 환경(CI 등)
        pytest.skip("클로드 세션 밖에서 돌고 있다 — 적을 주인 프로세스가 없다")
    from bridge import sessions
    assert sessions.process_starts()[card["pid"]] == card["pidStart"]


def test_한글_로케일_클로드에서도_주인_시각이_브릿지와_맞는다(tmp_path, monkeypatch):
    """★ 2026-08-15 실사고 — frogterminal 이 띄운 클로드는 한글 로케일이라 훅의 ps lstart 가
    "2026년 8월 15일 토요일 10시…"로 적혔고, 영어 로케일 브릿지의 ps 와 문자열이 절대 안 맞아
    살아있는 세션이 전부 '세션 꺼짐'으로 뒤집혔다. 양쪽 다 LC_ALL=C 로 못박았는지 실제 ps 로 검증."""
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "s9", "cwd": "/tmp"},
             LANG="ko_KR.UTF-8", LC_ALL="ko_KR.UTF-8", LC_TIME="ko_KR.UTF-8")
    card = card_of(tmp_path, "s9")
    if "pid" not in card:
        pytest.skip("클로드 세션 밖에서 돌고 있다 — 적을 주인 프로세스가 없다")
    assert card["pidStart"][:3] in {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}   # C 형식
    from bridge import sessions
    monkeypatch.setenv("LC_ALL", "ko_KR.UTF-8")     # 브릿지 쪽도 한글 환경에서 돌려 본다
    monkeypatch.setenv("LC_TIME", "ko_KR.UTF-8")
    assert sessions.process_starts()[card["pid"]] == card["pidStart"]


def test_깨진_입력에도_조용히_성공(tmp_path):
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True,
                       capture_output=True, timeout=10,
                       env={"FROG_STATE_DIR": str(tmp_path / "d"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout == ""


def test_세션id_경로조작_차단(tmp_path):
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "../evil", "cwd": "/tmp"})
    assert not (tmp_path / "evil.json").exists()          # 디렉터리 밖으로 못 나감


def test_install_hooks_백업은_처음_한번만(tmp_path):
    """.bak 을 매 실행마다 덮어쓰면 두 번째 실행이 원본 백업을 지운다 —
    ~/.claude/settings.json 은 되돌릴 값이 큰 파일이다."""
    sys.path.insert(0, str(HOOK.parent))
    import install_hooks
    settings = tmp_path / "settings.json"
    원본 = json.dumps({"model": "opus"})
    settings.write_text(원본)
    assert install_hooks.install(str(settings), "python3 /x/frog_hook.py") is True
    assert install_hooks.install(str(settings), "python3 /y/다른훅.py") is True   # 두 번째 변경
    assert (tmp_path / "settings.json.bak").read_text() == 원본                   # 첫 백업 유지


def test_install_hooks_멱등_및_기존설정_보존(tmp_path):
    sys.path.insert(0, str(HOOK.parent))
    import install_hooks
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "echo 기존훅"}]}]}}))
    cmd = "python3 /x/frog_hook.py"
    assert install_hooks.install(str(settings), cmd) is True
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"                                 # 기존 키 보존
    assert len(data["hooks"]["Stop"]) == 2                         # 기존 훅 보존 + 추가
    for ev in install_hooks.EVENTS:                                 # 6개 전부 — 압축 훅 포함
        mine = [h for e in data["hooks"][ev] for h in e["hooks"] if h.get("command") == cmd]
        assert mine == [{"type": "command", "command": cmd, "timeout": 5}]   # 정확히 1개, 세 필드 그대로
    assert (tmp_path / "settings.json.bak").exists()               # 백업
    assert install_hooks.install(str(settings), cmd) is False      # 멱등 — 재실행 무변경
    assert len(json.loads(settings.read_text())["hooks"]["Stop"]) == 2


def test_transcript_path_를_카드에_그대로_저장한다(tmp_path):
    """실측 확인: Notification·UserPromptSubmit payload 에 transcript_path 가 온다.

    브릿지가 cwd+session_id 로 계산해 낼 수도 있지만, 훅이 준 실제 경로가 항상 더 정확하다.
    """
    r = run_hook(tmp_path, {"hook_event_name": "Notification", "session_id": "s1",
                            "cwd": "/tmp", "message": "Claude is waiting for your input",
                            "transcript_path": "/Users/x/.claude/projects/-tmp/s1.jsonl"})
    assert r.returncode == 0
    assert card_of(tmp_path, "s1")["transcriptPath"] == \
        "/Users/x/.claude/projects/-tmp/s1.jsonl"


def test_transcript_path_가_없어도_카드는_정상(tmp_path):
    run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "s2", "cwd": "/tmp"})
    assert card_of(tmp_path, "s2")["transcriptPath"] == ""


def test_owner_process_는_클로드가_띄운_코덱스_카드의_주인을_코덱스로_적는다():
    """클로드 → sh → codex exec → sh → python(훅). 주인은 부모 클로드가 아니라 코덱스여야 한다.

    부모 클로드 pid 를 적으면 코덱스가 죽어도 카드가 '살아있음'으로 남아 프로젝트가 영영
    '진행중…'이 된다(실측 2026-10-20 — /clear 뒤 '꼭 진행중'으로 남던 원인)."""
    table = {
        1833: (69917, "Fri Aug 14 23:05:03 2026", "claude"),
        2001: (1833, "Fri Aug 14 23:30:00 2026", "/bin/zsh"),
        2002: (2001, "Fri Aug 14 23:30:01 2026",
               "/Users/x/.codex/packages/standalone/releases/0.163.0/bin/codex"),
        2003: (2002, "Fri Aug 14 23:30:02 2026", "/bin/sh"),
        2004: (2003, "Fri Aug 14 23:30:03 2026", "/opt/homebrew/.../Python"),
    }
    assert frog_hook.owner_process(table, 2004) == (2002, "Fri Aug 14 23:30:01 2026")
    # 코덱스 없이 클로드만 있으면 지금처럼 클로드
    del table[2002]; table[2003] = (2001, "t", "/bin/sh")
    assert frog_hook.owner_process(table, 2004) == (1833, "Fri Aug 14 23:05:03 2026")


def test_is_agent_는_codex_실행파일만_잡고_비슷한_이름은_안_잡는다():
    assert frog_hook.is_agent("/Users/x/.local/bin/codex")
    assert frog_hook.is_agent("codex")
    assert frog_hook.is_agent("/Users/x/.local/share/claude/versions/2.1.232")
    assert not frog_hook.is_agent("/Applications/ChatGPT.app/.../Codex Framework.framework/Helpers/browser_crashpad_handler")
    assert not frog_hook.is_agent("/Users/x/.codex/packages/app-server-daemon/.../bin/codex-code-mode-host")
    assert not frog_hook.is_agent("/bin/zsh")
