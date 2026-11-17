"""결재 카드 스캔·집계 테스트 — 훅 카드 파일을 직접 만들어 검증한다."""
import json
import os
import subprocess
import time

import pytest

from bridge import config, decisions


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "decisions"
    d.mkdir()
    monkeypatch.setattr(config, "decisions_dir", lambda: d)
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: "")
    # 살아있음 판정의 원천 — 진짜 ps 를 부르지 않게 기본은 '아무 프로세스도 없음'으로 고정한다.
    monkeypatch.setattr(decisions.sessions, "process_starts", lambda: {})
    # 부팅 시각도 진짜 sysctl 을 부르지 않게 — 기본은 '판정불가'(부팅 기준으로는 안 지운다).
    monkeypatch.setattr(decisions, "boot_time", lambda: None)
    return d


def put_card(d, sid, kind, project="alpha", message="",
             updated_at="2026-08-05T12:00:00+09:00", **extra):
    # inTmux 기본 True — 프로그터미널이 연(tmux 안) 세션의 카드가 이 테스트들의 기본 무대다.
    (d / f"{sid}.json").write_text(json.dumps(
        {"sessionId": sid, "kind": kind, "project": project, "cwd": "", "machine": "m",
         "message": message, "updatedAt": updated_at, "inTmux": True, **extra}))


@pytest.fixture
def live(monkeypatch):
    """이 프로젝트들의 tmux 세션이 살아 있다고 본다 — 죽은 세션은 상태를 만들지 않으므로."""
    def _live(*names):
        monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: list(names))
    return _live


def test_overview_working은_카드목록에서_빠지고_상태로만(state_dir, live):
    live("alpha")
    put_card(state_dir, "s1", "working")
    ov = decisions.overview()
    assert ov["cards"] == []
    assert ov["states"]["alpha"] == {"status": "working", "pending": 0}


def test_overview_죽은세션_카드는_상태를_만들지_않는다(state_dir):
    """★ 사이드바가 죽은 세션을 '결재 대기/진행중'으로 보여주던 구멍.

    working 카드는 live 판정을 아예 안 받았고, api_sessions 는 states 를 그대로 얹었다.
    그래서 kill -9·정전으로 죽은 프로젝트가 살아있는 것들보다 위에 빨간 배지를 달고
    최대 72시간(TTL) 머물렀다 — 헤드라인(api_summary)은 0건인데 목록만 1건.
    """
    put_card(state_dir, "s1", "working", project="dead")        # state_dir 기본 = 살아있는 세션 없음
    put_card(state_dir, "s2", "permission", project="dead2")
    ov = decisions.overview()
    assert ov["states"] == {}
    assert [(c["project"], c["live"]) for c in ov["cards"]] == [("dead2", False)]  # 카드는 남는다


def test_overview_tmux_없이_프로세스만_살아있으면_상태를_만들지_않는다(state_dir, monkeypatch):
    """★ 실측(2026-08-23): marketing 터미널을 껐는데 사이드바는 '진행중…'.
    카드의 pid 가 tmux 안에서 떠난 헤드리스 claude -p(자동화)라 프로세스 기준 live 는 참인데
    tmux 세션은 없었다 — 상태는 '열 수 있는 터미널'이 있을 때만 만든다."""
    monkeypatch.setattr(decisions.sessions, "process_starts",
                        lambda: {25901: "Sun Aug 23 13:10:16 2026"})
    put_card(state_dir, "s1", "working", project="marketing",
             pid=25901, pidStart="Sun Aug 23 13:10:16 2026")
    ov = decisions.overview()                                # state_dir 기본: tmux 세션 없음
    assert ov["states"] == {}


def test_overview_탭만_살아있어도_상태는_남는다(state_dir, live, monkeypatch):
    """기본 세션을 끄고 탭(alpha@2)만 남긴 경우 — 탭도 열 수 있는 터미널이다."""
    live("alpha@2")
    monkeypatch.setattr(decisions.sessions, "process_starts", lambda: {7: "x"})
    put_card(state_dir, "s1", "working", project="alpha", pid=7, pidStart="x")
    assert decisions.overview()["states"]["alpha"] == {"status": "working", "pending": 0}


def test_overview_결재가_working보다_우선(state_dir, live):
    live("alpha", "beta")
    put_card(state_dir, "s1", "working")
    put_card(state_dir, "s2", "permission")
    put_card(state_dir, "s3", "done", project="beta")
    ov = decisions.overview()
    assert ov["states"]["alpha"] == {"status": "decision", "pending": 1}
    assert ov["states"]["beta"] == {"status": "done", "pending": 0}
    assert [c["sessionId"] for c in ov["cards"]] == ["s2", "s3"]


def test_낡은_카드는_스캔때_삭제(state_dir):
    put_card(state_dir, "old", "done")
    old = time.time() - (config.DECISION_TTL_HOURS + 1) * 3600
    os.utime(state_dir / "old.json", (old, old))
    assert decisions.overview()["cards"] == []
    assert not (state_dir / "old.json").exists()


# ── 재부팅 (2026-08-15) ─────────────────────────────────────────────────────
# 실측: 맥미니 → 맥스튜디오 이관 뒤 재부팅했더니 오전에 쓰인 카드 3장이
# "세션 꺼짐 — 터미널을 열어 확인"으로 인박스에 남았다. 카드의 machine 이 옛 호스트네임
# (Mac-mini.local)이라 그것 때문으로 보였지만, 살아있음 판정은 machine 을 보지 않는다.
# 진짜 원인은 재부팅 — 카드가 가리키던 클로드 프로세스가 부팅에서 전부 죽었는데,
# 재부팅에선 SessionEnd 훅이 울릴 수 없어 카드만 TTL(72h) 동안 남았다.

def test_재부팅_전에_쓰인_카드는_스캔때_삭제(state_dir, monkeypatch, live):
    """★ 그 세션의 클로드도, 붙어 있던 tmux 서버도 부팅에서 함께 죽는다 = 열어 볼 터미널이 없다.

    ([터미널 열기]는 new-session -A 라, 남겨 두면 같은 이름의 빈 셸을 새로 만들 뿐이다.)
    """
    live("alpha")
    boot = time.time() - 600
    put_card(state_dir, "재부팅전", "permission")
    os.utime(state_dir / "재부팅전.json", (boot - 60, boot - 60))
    put_card(state_dir, "재부팅후", "permission")          # mtime = 지금 = 부팅 뒤
    monkeypatch.setattr(decisions, "boot_time", lambda: boot)
    assert [c["sessionId"] for c in decisions.overview()["cards"]] == ["재부팅후"]
    assert not (state_dir / "재부팅전.json").exists()      # 유령이 72시간 버티지 않게 지운다


def test_옛_호스트네임_카드도_프로세스가_살아있으면_결재로_남는다(state_dir, monkeypatch, live):
    """★ 호스트네임이 바뀌었다는 이유로 세션을 죽었다고 보면 안 된다(회귀 방지).

    이관·이름변경(Mac-mini.local → dijimaegseuui-MacStudio.local)은 지금 도는 클로드와
    아무 상관이 없다. machine 은 화면에 찍어 주는 표시일 뿐 — 살아있음은 그 프로세스가
    도는가로만 본다. 부팅 뒤에 쓰인 카드는 옛 이름이 적혀 있어도 그대로 결재로 남는다.
    """
    live("alpha")
    monkeypatch.setattr(decisions, "boot_time", lambda: time.time() - 3600)
    put_card(state_dir, "s1", "permission", machine="Mac-mini.local",
             pid=4242, pidStart="Sat Aug 15 13:00:00 2026")
    monkeypatch.setattr(decisions.sessions, "process_starts",
                        lambda: {4242: "Sat Aug 15 13:00:00 2026"})
    (card,) = decisions.overview()["cards"]
    assert card["live"] is True and card["canAnswer"] is True
    assert decisions.overview()["states"]["alpha"] == {"status": "decision", "pending": 1}


def test_부팅시각_판정불가면_아무것도_안_지운다(state_dir, live):
    """sysctl 한 번 실패로 살아있는 세션의 카드가 통째로 사라지면 안 된다 — fail-open.

    (fixture 의 기본 boot_time 이 곧 '판정불가'다.)
    """
    live("alpha")
    put_card(state_dir, "s1", "permission")
    os.utime(state_dir / "s1.json", (time.time() - 3600,) * 2)
    assert len(decisions.overview()["cards"]) == 1
    assert (state_dir / "s1.json").exists()


def test_boot_time_은_sec만_읽고_미래면_물러선다(monkeypatch):
    """sysctl 출력의 뒤 문자열은 로케일을 타지만 sec 는 안 탄다 — 그래서 sec 만 쓴다.

    시계가 뒤로 튀어 부팅 시각이 '미래'로 보이면 판정불가(None) — 그대로 믿으면 부팅 뒤에
    쓰인 멀쩡한 카드까지 지운다.
    """
    def 출력(text):
        monkeypatch.setattr(decisions.subprocess, "run",
                            lambda *a, **kw: subprocess.CompletedProcess(a, 0, text, ""))

    def 못부름(*a, **kw):
        raise subprocess.TimeoutExpired("sysctl", 5)

    출력("{ sec = 1786764970, usec = 850840 } Sat Aug 15 12:36:10 2026\n")
    assert decisions.boot_time() == 1786764970.0
    출력("{ sec = 1786764970, usec = 0 } 2026년 8월 15일 토요일 12시 36분 10초\n")
    assert decisions.boot_time() == 1786764970.0          # 한글 로케일에서도 같은 값
    출력(f"{{ sec = {int(time.time()) + 3600}, usec = 0 }} 미래\n")
    assert decisions.boot_time() is None
    출력("kern.boottime: 알 수 없음\n")
    assert decisions.boot_time() is None
    monkeypatch.setattr(decisions.subprocess, "run",                  # 이 키가 없는 곳(리눅스)
                        lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "no such key"))
    assert decisions.boot_time() is None
    monkeypatch.setattr(decisions.subprocess, "run", 못부름)
    assert decisions.boot_time() is None


def test_깨진_json은_무시(state_dir):
    (state_dir / "bad.json").write_text("{부서진")
    put_card(state_dir, "ok", "idle")
    assert len(decisions.overview()["cards"]) == 1


def test_객체가_아닌_json은_카드가_아니다(state_dir):
    (state_dir / "arr.json").write_text("[1, 2]")
    put_card(state_dir, "ok", "idle")
    assert len(decisions.overview()["cards"]) == 1


def test_kind없는_카드가_전체집계를_죽이지_않는다(state_dir, live):
    live("alpha", "gamma")
    (state_dir / "nokind.json").write_text(json.dumps(
        {"sessionId": "nokind", "inTmux": True, "project": "gamma", "cwd": "", "machine": "m",
         "message": "", "updatedAt": "2026-08-05T12:00:00+09:00"}))   # kind 키 자체가 없음
    put_card(state_dir, "s1", "permission")
    ov = decisions.overview()
    assert ov["states"]["alpha"] == {"status": "decision", "pending": 1}
    assert ov["states"]["gamma"] == {"status": "done", "pending": 0}
    assert len(ov["cards"]) == 2


CLAUDE_PERMISSION = """\
● Bash(rm -rf build/)

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands
   3. No, and tell Claude what to do differently
"""

ASK_PICKER = """\
지난 출력 텍스트 1. 이런 줄은 옵션이 아니다

 어떤 라이브러리를 쓸까요?
 ❯ 1. requests
   2. httpx
"""


def test_parse_picker_권한프롬프트():
    got = decisions.parse_picker(CLAUDE_PERMISSION)
    assert got["question"] == "Do you want to proceed?"
    assert [o["label"] for o in got["options"]] == [
        "Yes", "Yes, and don't ask again for rm commands",
        "No, and tell Claude what to do differently"]


def test_parse_picker_마지막_피커만():
    got = decisions.parse_picker(ASK_PICKER)
    assert got["question"] == "어떤 라이브러리를 쓸까요?"
    assert len(got["options"]) == 2


README_LIST = """\
 사용 안내
 1. 설치
 2. 사용법
 3. 문제해결
"""

BLANK_QUESTION_PICKER = """\
 ❯ 1. 예
   2. 아니오
"""


def test_parse_picker_없으면_None():
    assert decisions.parse_picker("$ ls\nREADME.md\n") is None
    assert decisions.parse_picker("1. 하나뿐이면 피커 아님\n") is None


def test_parse_picker_마커없는_번호목록은_피커아님():
    assert decisions.parse_picker(README_LIST) is None


def test_overview_피커는_훅카드를_승격(state_dir, monkeypatch):
    put_card(state_dir, "s1", "permission")
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane",
                        lambda name: CLAUDE_PERMISSION if name == "alpha" else "")
    ov = decisions.overview()
    (card,) = ov["cards"]
    assert card["kind"] == "picker" and len(card["options"]) == 3
    assert ov["states"]["alpha"]["status"] == "decision"


def test_overview_훅카드_없어도_피커_합성(state_dir, monkeypatch):
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["beta"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: ASK_PICKER)
    ov = decisions.overview()
    (card,) = ov["cards"]
    assert card["project"] == "beta" and card["sessionId"] is None
    assert card["kind"] == "picker"
    assert ov["states"]["beta"] == {"status": "decision", "pending": 1}


def test_overview_피커는_updatedAt_최신카드를_승격(state_dir, monkeypatch):
    # 파일명순으로는 "s9-old" 가 마지막 — 그래도 최신 카드가 승격돼야 한다
    put_card(state_dir, "s1-new", "permission", updated_at="2026-08-05T18:00:00+09:00")
    put_card(state_dir, "s9-old", "permission", updated_at="2026-08-02T09:00:00+09:00")
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: CLAUDE_PERMISSION)
    by_sid = {c["sessionId"]: c for c in decisions.overview()["cards"]}
    assert by_sid["s1-new"]["kind"] == "picker"
    assert by_sid["s9-old"]["kind"] == "permission"


def test_overview_질문없는_피커도_합성카드에_제목이_있다(state_dir, monkeypatch):
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["beta"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: BLANK_QUESTION_PICKER)
    (card,) = decisions.overview()["cards"]
    assert card["question"] == "" and card["message"] == "선택지 대기 중"


def test_overview_승격카드는_원래_message를_유지(state_dir, monkeypatch):
    put_card(state_dir, "s1", "permission", message="rm -rf build/ 실행할까요?")
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: BLANK_QUESTION_PICKER)
    (card,) = decisions.overview()["cards"]
    assert card["kind"] == "picker" and card["message"] == "rm -rf build/ 실행할까요?"


def test_send_answer_option은_숫자키_전송(monkeypatch):
    calls = []
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: CLAUDE_PERMISSION)
    monkeypatch.setattr(decisions, "_tmux", lambda *a: calls.append(a))
    decisions.send_answer("alpha", "option", "2")
    assert calls == [("send-keys", "-t", "=alpha:", "2")]


def test_send_answer_option은_화면에_피커가_있을때만_보낸다(monkeypatch):
    """★ 답이 '물어본 클로드'가 아니라 '같은 이름의 tmux 세션'으로 가던 구멍.

    카드의 project 는 훅이 cwd 첫 폴더명으로 만든 값이고, attach_cmd 가 new-session -A 라
    웹에서 세션을 한 번 열기만 해도 그 이름의 빈 셸이 생긴다. Zed·iTerm 에서 돌던 클로드가
    카드를 올리면 답은 그 빈 셸에 타이핑+엔터로 들어가 셸 명령으로 실행된다(서버는 200).
    값싼 1차 방어 — 보내기 전에 그 화면에 피커가 아직 떠 있는지 본다.
    """
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: "$ ls\nREADME.md\n")   # 빈 셸
    monkeypatch.setattr(decisions, "_tmux", lambda *a: pytest.fail("빈 셸에 보내면 안 된다"))
    with pytest.raises(decisions.NoPicker):
        decisions.send_answer("alpha", "option", "1")


def test_send_answer_옵션수보다_큰_번호는_거부(monkeypatch):
    """화면이 이미 다음 질문으로 넘어가 선택지가 줄어든 경우 — 그 번호는 엉뚱한 걸 누른다."""
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: ASK_PICKER)   # 옵션 2개
    monkeypatch.setattr(decisions, "_tmux", lambda *a: pytest.fail("보내면 안 된다"))
    with pytest.raises(decisions.NoPicker):
        decisions.send_answer("alpha", "option", "3")


def test_send_answer_text는_피커가_없어도_보낸다(monkeypatch):
    """text 는 막지 않는다 — 훅의 idle 카드(피커 없는 '입력 대기')가 정상 경로라 화면검사로는
    정상 답까지 함께 막힌다. 게다가 잘못 가면 셸에 알 수 없는 명령으로 눈에 띄게 실패할 뿐,
    option 처럼 '엉뚱한 항목이 조용히 선택되는' 일이 없다."""
    calls = []
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: "$ ")
    monkeypatch.setattr(decisions, "_tmux", lambda *a: calls.append(a))
    decisions.send_answer("alpha", "text", "진행해")
    assert calls[0][:3] == ("send-keys", "-t", "=alpha:")


@pytest.mark.parametrize("value", ["10", "12", "99", "0"])
def test_send_answer_두자리_옵션은_거부(monkeypatch, value):
    """★ tmux send-keys "12" 는 '12' 한 키가 아니라 '1' 다음 '2' 를 보낸다(실측).

    옵션 10개 이상인 화면(/agents·/model)에서 12번을 누르면 1번이 선택되고 남은 '2' 가
    다음 프롬프트로 흘러간다. 화면엔 "보냄: 12" 가 남아 사용자는 12번을 골랐다고 믿는다.
    0 도 클로드 피커에 없는 번호라 함께 막는다.
    """
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: CLAUDE_PERMISSION)
    monkeypatch.setattr(decisions, "_tmux", lambda *a: pytest.fail("보내면 안 된다"))
    with pytest.raises(ValueError):
        decisions.send_answer("alpha", "option", value)


def test_send_answer_text는_literal_후_enter(monkeypatch):
    calls = []
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "_tmux", lambda *a: calls.append(a))
    decisions.send_answer("alpha", "text", "좋아 진행해\n-단 테스트 먼저")
    assert calls == [("send-keys", "-t", "=alpha:", "-l", "--", "좋아 진행해 -단 테스트 먼저"),
                     ("send-keys", "-t", "=alpha:", "Enter")]


def test_send_answer_text_제어문자는_pty에_닿지_않는다(monkeypatch):
    calls = []
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "_tmux", lambda *a: calls.append(a))
    decisions.send_answer("alpha", "text", "중단\x03해줘 \x1b[31m빨강\r\n다음줄​")
    assert calls == [("send-keys", "-t", "=alpha:", "-l", "--", "중단해줘 [31m빨강 다음줄"),
                     ("send-keys", "-t", "=alpha:", "Enter")]
    sent = "".join(arg for call in calls for arg in call)
    assert not any(ch in sent for ch in ("\x03", "\x1b", "\r", "​"))


def test_send_answer_제어문자뿐인_답은_거부(monkeypatch):
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "_tmux", lambda *a: None)
    with pytest.raises(ValueError):
        decisions.send_answer("alpha", "text", "\x03\x1b\x7f")
    with pytest.raises(ValueError):                # \d 는 유니코드 숫자까지 통과했었다
        decisions.send_answer("alpha", "option", "٣")


def test_send_answer_tmux_실행실패는_LookupError(monkeypatch):
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: CLAUDE_PERMISSION)

    def 죽은세션(*a):
        raise subprocess.CalledProcessError(1, "tmux")

    monkeypatch.setattr(decisions, "_tmux", 죽은세션)
    with pytest.raises(LookupError) as e:
        decisions.send_answer("alpha", "option", "2")
    assert isinstance(e.value.__cause__, subprocess.CalledProcessError)   # 원인 체인 유지
    with pytest.raises(LookupError):
        decisions.send_answer("alpha", "text", "진행해")

    def 응답없음(*a):
        raise subprocess.TimeoutExpired("tmux", 5)

    monkeypatch.setattr(decisions, "_tmux", 응답없음)
    with pytest.raises(LookupError):
        decisions.send_answer("alpha", "text", "진행해")


def test_send_answer_검증(monkeypatch):
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "_tmux", lambda *a: None)
    with pytest.raises(LookupError):
        decisions.send_answer("죽은세션", "text", "x")
    with pytest.raises(ValueError):
        decisions.send_answer("alpha", "option", "abc")
    with pytest.raises(ValueError):
        decisions.send_answer("alpha", "text", "   ")
    with pytest.raises(ValueError):
        decisions.send_answer("alpha", "메롱", "x")


def test_overview_카드에_세션_생존여부가_붙는다(state_dir, monkeypatch):
    """죽은 세션(SessionEnd 훅을 못 남기고 크래시)의 카드는 TTL 동안 남는다 — live=False로
    표시해야 헤드라인 결재 수(api_summary)가 그걸 걸러낼 수 있다. beta는 살아있다."""
    put_card(state_dir, "s1", "permission", project="alpha")   # alpha 세션은 죽음
    put_card(state_dir, "s2", "idle", project="beta")
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["beta"])
    by_project = {c["project"]: c for c in decisions.overview()["cards"]}
    assert by_project["alpha"]["live"] is False
    assert by_project["beta"]["live"] is True


def test_tmux_밖_세션_카드는_아예_안_올라온다(state_dir, live):
    """★ 실측(2026-08-15): Zed 에서 돌던 클로드의 '완료 대기'가 사이드바에 떴는데,
    눌러 보니 새 tmux 클로드가 떠서 카드와 터미널이 서로 다른 세션이었다.
    프로그터미널의 결재·상태는 프로그터미널이 연 세션(tmux 안, inTmux=True)만 말한다.
    표식이 없는 옛 카드도 제외 — 그 세션이 다음에 뭐라도 하면 훅이 표식을 달아 준다."""
    live("alpha")
    put_card(state_dir, "s1", "permission", inTmux=False)          # Zed 세션
    (state_dir / "s2.json").write_text(json.dumps(                 # 표식 없는 옛 카드
        {"sessionId": "s2", "kind": "permission", "project": "alpha", "cwd": "",
         "machine": "m", "message": "", "updatedAt": "2026-08-05T12:00:00+09:00"}))
    ov = decisions.overview()
    assert ov["cards"] == [] and ov["states"] == {}


# ── 실시간 상태 (2026-08-14) ────────────────────────────────────────────────
# 이미 끝난 이전 결재가 '결재 대기'로 계속 남아 있던 문제 — 원인이 둘이었고 여기가 그 계약이다.
#   ① 살아있음을 '같은 이름의 tmux 세션이 있나'로 봤다 — 클로드는 대부분 tmux 밖에서 돈다.
#   ② 카드 상태가 훅이 마지막에 쓴 값 그대로였다 — 터미널에서 직접 승인하면 훅이 안 울린다.

def test_살아있음은_tmux가_아니라_클로드_프로세스로_판정(state_dir, monkeypatch):
    """★ 판정이 정반대로 나오던 구멍.

    tmux 밖(Zed·iTerm)에서 도는 세션은 지금 일하는 중인데도 '세션 꺼짐'으로 분류됐고,
    [터미널 열기]가 new-session -A 로 만든 빈 셸은 죽은 카드를 '살아있음'으로 되살려
    빨간 결재로 올렸다 — 화면에 남아 있던 그 카드가 이것이다.
    """
    put_card(state_dir, "s1", "permission", project="밖에서도는중",
             pid=4242, pidStart="Fri Aug 14 23:02:42 2026")
    put_card(state_dir, "s2", "permission", project="빈셸만남음",
             pid=777, pidStart="Fri Aug 14 09:00:00 2026")
    # tmux 는 정반대로 말한다: 살아있는 쪽에는 세션이 없고, 죽은 쪽에는 빈 셸이 떠 있다
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["빈셸만남음"])
    monkeypatch.setattr(decisions.sessions, "process_starts",
                        lambda: {4242: "Fri Aug 14 23:02:42 2026"})       # 777 은 죽었다
    by_project = {c["project"]: c for c in decisions.overview()["cards"]}
    assert by_project["밖에서도는중"]["live"] is True
    assert by_project["빈셸만남음"]["live"] is False


def test_pid가_재사용돼도_시작시각이_다르면_죽은것(state_dir, monkeypatch):
    """번호는 돌고 돈다 — 남의 프로세스를 그 세션으로 오인하면 답이 엉뚱한 곳으로 간다."""
    put_card(state_dir, "s1", "permission", pid=4242, pidStart="Fri Aug 14 23:02:42 2026")
    monkeypatch.setattr(decisions.sessions, "process_starts",
                        lambda: {4242: "Fri Aug 15 07:00:00 2026"})       # 같은 번호, 다른 프로세스
    assert decisions.overview()["cards"][0]["live"] is False


def test_pid없는_옛카드와_ps실패는_예전기준으로_물러선다(state_dir, monkeypatch, live):
    """훅을 고치기 전에 쓰인 카드가 통째로 '꺼짐'으로 뒤집히면 안 된다."""
    put_card(state_dir, "s1", "permission")                               # pid 없음
    live("alpha")
    assert decisions.overview()["cards"][0]["live"] is True
    # ps 판정불가(None)일 때도 마찬가지 — 한 번의 실패로 살아있는 세션이 전부 꺼지면 안 된다
    put_card(state_dir, "s1", "permission", pid=4242, pidStart="Fri Aug 14 23:02:42 2026")
    monkeypatch.setattr(decisions.sessions, "process_starts", lambda: None)
    assert decisions.overview()["cards"][0]["live"] is True


def test_대화가_결재를_지나갔으면_더는_결재가_아니다(state_dir, monkeypatch, live):
    """★ 터미널에서 직접 승인하면 훅이 안 울린다 — 클로드는 계속 일하는데 카드만 남았다.

    승인 직후 대화에는 tool_result 가 붙는다. 그 시각이 카드보다 뒤면 결재는 이미 끝난 것이다.
    """
    live("alpha")
    put_card(state_dir, "s1", "permission", updated_at="2026-08-05T12:00:00+09:00")
    monkeypatch.setattr(decisions, "context_for", lambda c: {
        "asked": "", "said": "", "lastAt": "2026-08-05T03:05:00.000Z"})   # = KST 12:05, 카드보다 뒤
    ov = decisions.overview()
    assert ov["cards"] == []                                  # 결재 목록에서 빠지고
    assert ov["states"]["alpha"] == {"status": "working", "pending": 0}   # 진행중으로 잡힌다


def test_대화가_결재보다_앞서지_않으면_그대로_결재(state_dir, monkeypatch, live):
    """훅의 Notification 은 마지막 대화보다 뒤에 울린다 — 정상 결재를 내려버리면 안 된다."""
    live("alpha")
    put_card(state_dir, "s1", "permission", updated_at="2026-08-05T12:00:00+09:00")
    monkeypatch.setattr(decisions, "context_for", lambda c: {
        "asked": "", "said": "", "lastAt": "2026-08-05T02:59:00.000Z"})   # = KST 11:59
    assert decisions.overview()["states"]["alpha"]["pending"] == 1


def test_화면에_피커가_떠있으면_대화가_앞서가도_안_내린다(state_dir, monkeypatch):
    """지금 화면에 떠 있는 선택지는 대화 기록보다 강한 증거다 — 그건 진짜 답을 기다린다."""
    put_card(state_dir, "s1", "permission")
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["alpha"])
    monkeypatch.setattr(decisions, "capture_pane", lambda name: CLAUDE_PERMISSION)
    monkeypatch.setattr(decisions, "context_for", lambda c: {
        "asked": "", "said": "", "lastAt": "2026-08-09T12:00:00.000Z"})   # 카드보다 한참 뒤
    (card,) = decisions.overview()["cards"]
    assert card["kind"] == "picker"


def test_살아있어도_tmux밖_세션에는_답을_보낼_수_없다(state_dir, monkeypatch):
    """살아있음(live)과 '여기서 답할 수 있음'(canAnswer)은 다르다.

    답은 tmux send-keys 로만 들어간다 — Zed·iTerm 에서 도는 세션은 살아 있어도 길이 없다.
    화면이 이 둘을 구분하지 못하면 사용자가 버튼을 눌러 보고서야 404 안내를 읽게 된다.
    """
    put_card(state_dir, "s1", "permission", project="zed에서도는중",
             pid=4242, pidStart="Fri Aug 14 23:02:42 2026")
    monkeypatch.setattr(decisions.sessions, "process_starts",
                        lambda: {4242: "Fri Aug 14 23:02:42 2026"})
    (card,) = decisions.overview()["cards"]
    assert card["live"] is True and card["canAnswer"] is False
    # 같은 이름의 tmux 세션이 있으면 그때는 보낼 수 있다
    monkeypatch.setattr(decisions.sessions, "list_tmux_sessions", lambda: ["zed에서도는중"])
    assert decisions.overview()["cards"][0]["canAnswer"] is True


def test_idle은_결재가_아니다(state_dir, live):
    """할 말을 마치고 빈 프롬프트에서 쉬는 것뿐 — 결정할 거리가 없는데 빨간불이 켜지면
    정작 답해야 할 결재가 그 안에 묻힌다. '완료 — 다음 지시 대기' 쪽으로 간다."""
    live("alpha")
    put_card(state_dir, "s1", "idle")
    ov = decisions.overview()
    assert ov["states"]["alpha"] == {"status": "done", "pending": 0}
    assert len(ov["cards"]) == 1                              # 카드 자체는 남는다(지시를 넣는 자리)


def test_clear_card_는_정확히_일치할때만_지운다(state_dir):
    """정규식 '$' 는 끝 개행을 허용한다 — match 로는 's9\\n' 이 통과해 엉뚱한 파일이 지워진다."""
    (state_dir / "s9\n.json").write_text("{}")     # 개행은 POSIX 에서 legal 한 파일명
    decisions.clear_card("s9\n")
    assert (state_dir / "s9\n.json").exists()


def test_clear_card(state_dir):
    put_card(state_dir, "s9", "done")
    decisions.clear_card("s9")
    assert not (state_dir / "s9.json").exists()
    decisions.clear_card("../탈출")               # 조용히 무시
    decisions.clear_card(None)
