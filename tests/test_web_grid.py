"""멀티 칸(4·6·8분할) 회귀 — 4칸이든 6칸이든 각 입력창에 지금처럼 입력되고
붙여넣기가 잘되는 것은 그대로 유지해야 한다. 지금 기능을 깨뜨리면 안 된다.

그래서 여기서 지키는 것은 '칸이 여럿이어도 입력이 제 세션으로만 간다'가 핵심이다:
타이핑·한글·붙여넣기(업로드 경로)·파일 끌어넣기가 각각 활성 칸·떨어뜨린 칸의 세션에만 닿고 옆 칸에는
한 바이트도 새지 않는가. 그 위에 칸 수 바꾸기·칸 고르기·기억·폰 폭·끄기를 얹는다.

가짜 브릿지(conftest)의 켜진 세션은 wiki(결재)·mall(진행중)·blog·frog(완료), 꺼진 것은 apex.
사이드바 순서(결재>진행중>완료 이름순)가 곧 기본 4칸의 채움 순서다: wiki, mall, blog, frog.
"""
import re

import pytest

pytest.importorskip("playwright", reason="브라우저 회귀 테스트 — pip install playwright 필요")

from playwright.sync_api import sync_playwright     # noqa: E402

WIDE = {"width": 1600, "height": 900}
FILL = ["wiki", "mall", "blog", "frog"]
MOUSE = re.compile(rb"\x1b\[<\d+;\d+;\d+[Mm]")       # 칸을 누를 때 tmux 로 가는 마우스 리포트 — 입력이 아니다


def typed(bridge, name):
    """그 세션이 받은 '입력'만 — 칸을 누르며 나간 마우스 리포트는 걷어낸다."""
    return MOUSE.sub(b"", bytes(bridge.by_session.get(name, b"")))


def wait_until(page, js, timeout=8000):
    page.wait_for_function(js, timeout=timeout)


def all_connected(n):
    return f"panes.length === {n} && panes.filter(p => p.session).every(p => p.ws && p.ws.readyState === 1)"


def picks(page):
    return page.eval_on_selector_all(".pane-pick", "els => els.map(e => e.value)")


@pytest.fixture
def grid(bridge):
    """기억 없는 새 기기 — 기본 4칸으로 뜬다. 끝날 때 페이지 오류가 0건이어야 한다."""
    for _ in range(50):                              # 앞 테스트의 소켓이 다 닫힌 뒤 시작한다(브릿지는 세션 공용)
        if bridge.ws_open == 0:
            break
        __import__("time").sleep(0.1)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--ignore-certificate-errors"])
        page = browser.new_context(ignore_https_errors=True, viewport=WIDE).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(bridge.url)
        page.wait_for_selector("#sessions li")
        wait_until(page, all_connected(4) + " && panes.every(p => p.session)")
        bridge.by_session.clear()
        yield page
        assert errors == []
        browser.close()


def click_pane(page, i):
    """칸 i 의 터미널 한가운데를 누른다 — 사용자가 실제로 하는 것(그 칸이 활성이 되고 키보드가 간다)."""
    page.locator(".pane").nth(i).locator(".xterm-screen").click()
    page.wait_for_timeout(150)


def test_기본은_4칸이고_켜진_세션을_사이드바_순서로_채운다(grid, bridge):
    assert picks(grid) == FILL
    assert bridge.ws_open == 4
    assert "apex" not in bridge.ws_log[-4:]          # 꺼진 것은 저절로 켜지 않는다
    assert grid.get_attribute("#panes", "data-n") == "4"
    assert "on" in grid.get_attribute('#grid-bar button[data-n="4"]', "class")
    # 칸마다 제 크기 — 한 칸 폭(1600 전체)이 아니라 반쪽 폭의 열 수로 붙는다
    cols = grid.evaluate("panes.map(p => p.term.cols)")
    assert all(30 < c < 130 for c in cols), cols


def test_타이핑은_누른_칸의_세션으로만_간다(grid, bridge):
    click_pane(grid, 2)                              # blog
    grid.keyboard.type("ls -al")
    click_pane(grid, 0)                              # wiki
    grid.keyboard.type("가나")                        # 한글도 같은 길
    grid.wait_for_timeout(400)
    assert typed(bridge, "blog") == b"ls -al"
    assert typed(bridge, "wiki") == "가나".encode()
    assert typed(bridge, "mall") == b"" and typed(bridge, "frog") == b""
    assert grid.text_content("#hdr-name") == "wiki"  # 위 머리줄 = 활성 칸
    assert "active" in grid.locator(".pane").nth(0).get_attribute("class")


def test_붙여넣기는_활성_칸의_세션에만_찍힌다(grid, bridge):
    click_pane(grid, 3)                              # frog
    grid.evaluate("""() => {
      const dt = new DataTransfer();
      dt.items.add(new File(["fake-png"], "shot.png", { type: "image/png" }));
      document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    }""")
    grid.wait_for_timeout(800)
    assert bridge.uploads[-1] == {"project": "frog", "name": "shot.png"}
    assert typed(bridge, "frog") == b"'/tmp/_uploads/frog/shot.png' "
    for other in ("wiki", "mall", "blog"):
        assert typed(bridge, other) == b""
    # 글자 붙여넣기(⌘V 의 xterm paste)도 활성 칸으로
    bridge.by_session.clear()
    grid.evaluate("term.paste('붙임')")
    grid.wait_for_timeout(300)
    assert "붙임".encode() in typed(bridge, "frog")
    assert typed(bridge, "wiki") == b""


def test_끌어넣기는_떨어뜨린_칸으로_간다(grid, bridge):
    click_pane(grid, 0)                              # 활성은 wiki 인데
    bridge.by_session.clear()
    grid.evaluate("""() => {
      const dt = new DataTransfer();
      dt.setData("application/x-frog-path", "/tmp/mall/docs/노트.md");
      const target = document.querySelectorAll(".pane-term")[1];   // mall 칸 위에 떨어뜨린다
      target.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }""")
    grid.wait_for_timeout(400)
    assert typed(bridge, "mall").decode() == "'/tmp/mall/docs/노트.md' "
    assert typed(bridge, "wiki") == b""


def test_사이드바에서_이미_띄운_것을_누르면_그_칸으로_간다(grid, bridge):
    opened = len(bridge.ws_log)
    grid.click('#sessions li[data-name="mall"] b')
    grid.wait_for_timeout(300)
    assert len(bridge.ws_log) == opened              # 새로 붙지 않는다(같은 세션 두 칸 금지)
    assert grid.text_content("#hdr-name") == "mall"
    assert "active" in grid.locator(".pane").nth(1).get_attribute("class")
    assert grid.evaluate("document.activeElement.closest('.pane') === panes[1].root")


def test_6칸은_빈_칸을_남기고_꺼진_것은_되묻고_켠다(grid, bridge):
    grid.click('#grid-bar button[data-n="6"]')
    wait_until(grid, all_connected(6))
    assert picks(grid) == FILL + ["", ""]            # 켜진 것이 넷뿐 — 나머지는 비워 둔다
    assert grid.locator(".pane-term").nth(4).inner_text().strip() == ""
    # 다른 칸에 떠 있는 것은 못 고른다
    disabled = grid.eval_on_selector_all(".pane:nth-child(5) .pane-pick option:disabled", "os => os.map(o => o.value)")
    assert sorted(disabled) == sorted(FILL)
    grid.select_option(".pane:nth-child(5) .pane-pick", "apex")
    grid.wait_for_selector(".pane:nth-child(5) .pane-ask button")
    assert "apex" not in bridge.ws_log[-1:]          # 고르기만으로는 안 켠다
    grid.click(".pane:nth-child(5) .pane-ask button:has-text('클로드로 켜기')")
    wait_until(grid, "panes[4].ws && panes[4].ws.readyState === 1")
    assert bridge.ws_log[-1] == "apex"
    assert grid.text_content("#hdr-name") == "apex"


def test_칸을_비우면_소켓만_떼고_격자는_남는다(grid, bridge):
    grid.select_option(".pane:nth-child(2) .pane-pick", "")
    grid.wait_for_timeout(400)
    assert bridge.ws_open == 3
    assert grid.evaluate("panes.length") == 4
    assert grid.evaluate("panes[1].session") is None
    assert bridge.kills == [] or "mall" not in bridge.kills[-1:]   # 세션은 끄지 않는다


def test_1칸으로_줄이면_지금_치던_칸이_남는다(grid, bridge):
    click_pane(grid, 3)                              # frog 에서 치다가
    grid.click('#grid-bar button[data-n="1"]')
    wait_until(grid, "panes.length === 1")
    grid.wait_for_timeout(400)
    assert grid.text_content("#hdr-name") == "frog"
    assert bridge.ws_open == 1
    grid.keyboard.type("ok")
    grid.wait_for_timeout(300)
    assert typed(bridge, "frog").endswith(b"ok")


def test_고른_칸_수와_자리는_새로고침해도_남는다(grid, bridge):
    grid.click('#grid-bar button[data-n="6"]')
    wait_until(grid, all_connected(6))
    grid.select_option(".pane:nth-child(1) .pane-pick", "")       # 첫 칸을 비우고
    grid.select_option(".pane:nth-child(6) .pane-pick", "wiki")   # wiki 를 마지막 칸으로
    wait_until(grid, "panes[5].ws && panes[5].ws.readyState === 1")
    grid.reload()
    grid.wait_for_selector("#sessions li")
    wait_until(grid, all_connected(6) + " && panes[5].session")
    got = picks(grid)
    assert got[5] == "wiki" and got[1:4] == ["mall", "blog", "frog"]


def test_폰_폭이면_1칸으로_접고_돌아오면_되살린다(grid, bridge):
    grid.set_viewport_size({"width": 600, "height": 900})
    wait_until(grid, "panes.length === 1")
    assert not grid.is_visible("#grid-bar")
    grid.set_viewport_size(WIDE)
    wait_until(grid, all_connected(4) + " && panes.every(p => p.session)")
    assert sorted(picks(grid)) == sorted(FILL)


def test_끄면_그_칸만_비고_나머지는_산다(grid, bridge):
    li = grid.locator('#sessions li[data-name="blog"]')
    li.hover()
    li.locator(".row-acts .danger").click()
    li.locator(".row-confirm button:has-text('끄기')").click()
    grid.wait_for_timeout(600)
    assert bridge.kills[-1] == "blog"
    assert grid.evaluate("panes.length") == 4
    assert grid.evaluate("panes[2].session") is None
    assert grid.evaluate("[0, 1, 3].every(i => panes[i].ws && panes[i].ws.readyState === 1)")


def test_설정의_글자_크기는_모든_칸에_든다(grid, bridge):
    """설정 화면의 + 를 누르면 활성 칸만이 아니라 네 칸 모두 글자가 커지고 열 수가 줄어든다."""
    before = grid.evaluate("panes.map(p => p.term.cols)")
    size = grid.evaluate("termOptions().fontSize")
    grid.click("#btn-settings")
    grid.wait_for_selector("#set-font-size .set-step")
    try:
        for _ in range(3):
            grid.locator("#set-font-size .set-step").nth(1).click()   # +
            grid.wait_for_timeout(120)
        grid.wait_for_timeout(300)
        assert grid.evaluate("panes.map(p => p.term.options.fontSize)") == [size + 3] * 4
        after = grid.evaluate("panes.map(p => p.term.cols)")
        assert all(a < b for a, b in zip(after, before)), (before, after)
        bg = grid.eval_on_selector_all(".pane-term", "els => new Set(els.map(e => e.style.background)).size")
        assert bg == 1                               # 칸 배경도 한 색
    finally:
        grid.evaluate(f"setPref('fontSize', {size})")


def test_1칸은_칸_머리줄이_없는_예전_화면이고_패널_머리줄은_산다(grid, bridge):
    """1칸은 지금까지와 똑같아야 한다. 칸 머리줄 이름이 오른쪽 패널 머리줄(.pane-head)과
    겹쳐 1칸에서도 콤보가 보였던 실수(2026-10-11 실화면 검수에서 발견)를 막는다."""
    assert grid.locator(".cell-head").nth(0).is_visible()          # 여러 칸 — 보인다
    click_pane(grid, 1)
    grid.click('#grid-bar button[data-n="1"]')
    wait_until(grid, "panes.length === 1")
    assert not grid.locator(".cell-head").first.is_visible()        # 1칸 — 숨는다
    grid.wait_for_selector("#file-panel:not([hidden])")             # 1칸 복귀 — 파일 패널이 원래 규칙대로 열린다
    assert grid.locator("#file-panel .pane-head").is_visible()      # 패널 머리줄은 그대로
    grid.click("#btn-settings")
    assert grid.locator("#settings-view .pane-head").is_visible()


# ---------- 안정화(4단계) — 끈 세션·복원·절전 복귀·대시보드 ----------

def test_다른_곳에서_끈_세션은_칸에서_되살아나지_않는다(grid, bridge):
    """폰에서 끈 세션이 맥 칸의 자동 재접속(new-session -A)으로 에이전트까지 새로 뜨던 구멍.
    브릿지가 4410 으로 닫으면 칸은 다시 붙지 않고 비운다 — 나머지 칸은 그대로."""
    before = bridge.ws_log.count("blog")
    grid.evaluate("""fetch('/api/sessions/kill', { method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({ project: 'blog' }) })""")   # 다른 기기에서 끈 것과 같은 요청
    grid.wait_for_timeout(200)
    grid.evaluate("panes[2].ws.close()")             # 세션이 죽어 소켓이 끊긴 것을 재현
    wait_until(grid, "panes[2].session === null")
    grid.wait_for_timeout(1200)                      # 백오프 재시도가 있었다면 이 사이에 붙었을 것
    assert bridge.ws_log.count("blog") == before     # 다시 붙지(=되살리지) 않았다
    assert "다른 곳에서 꺼졌습니다" in grid.text_content("#copy-toast")
    assert grid.evaluate("[0, 1, 3].every(i => panes[i].ws && panes[i].ws.readyState === 1)")


def test_끄지_않은_세션은_끊겨도_다시_붙는다(grid, bridge):
    """와이파이·절전으로 끊긴 것은 평소대로 다시 붙는다 — 4410 은 '끈 것'에만."""
    grid.evaluate("panes[1].ws.close()")
    wait_until(grid, "panes[1].ws && panes[1].ws.readyState === 1 && panes[1].session.name === 'mall'")


def test_전체끄기_뒤_복원하면_칸이_제자리로_다시_찬다(grid, bridge):
    grid.click("#btn-killall")
    grid.click("#bulk-yes")
    wait_until(grid, "panes.every(p => !p.session)")
    assert grid.evaluate("panes.length") == 4        # 칸은 남는다
    grid.wait_for_selector("#btn-restore:not([disabled])")
    grid.click("#btn-restore")
    wait_until(grid, all_connected(4) + " && panes.every(p => p.session)")
    assert picks(grid) == FILL


def test_절전에서_돌아오면_네_칸이_모두_바로_붙는다(grid, bridge):
    grid.evaluate("panes.forEach(p => p.ws.close())")          # 자는 동안 소켓이 다 죽은 상황
    grid.wait_for_timeout(100)
    grid.evaluate("panes.forEach(p => clearTimeout(p.reconnectTimer))")   # 백오프 대기 중으로 고정
    grid.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    grid.wait_for_timeout(350)                       # 백오프(0.5초)보다 먼저 — 기다리지 않고 즉시
    assert grid.evaluate("panes.every(p => p.ws && p.ws.readyState === 1)")


def test_여러_칸이면_결재_대시보드는_접히고_1칸이면_원래대로(grid, bridge):
    """칸이 세로로 납작해지는 주범 — 여러 칸에서는 한 줄로 접는다. 1칸의 펴 둔 기억은 안 건드린다."""
    grid.wait_for_selector("#dash:not([hidden])")
    assert not grid.is_visible("#dash-cards")
    assert grid.text_content("#dash-toggle").startswith("▸")
    click_pane(grid, 0)
    grid.click('#grid-bar button[data-n="1"]')
    grid.wait_for_timeout(500)
    assert grid.is_visible("#dash-cards")            # 1칸 — 예전처럼 펼쳐진다
    grid.click('#grid-bar button[data-n="4"]')
    grid.wait_for_timeout(500)
    grid.click("#dash-toggle")                        # 여러 칸에서 직접 펴면 그 선택은 기억된다
    grid.wait_for_selector("#dash-cards", state="visible")   # 펴기는 결재 목록을 다시 받아 그린다(loadDash)
    assert grid.evaluate("localStorage.getItem('frog.dash.open.grid')") == "1"
    assert grid.evaluate("localStorage.getItem('frog.dash.open')") is None


# ---------- HUD — 칸 안은 짧게, 계정 줄은 하단 한 세트 ----------

def compact_msgs(bridge):
    import json
    return [json.loads(t)["on"] for t in bridge.ws_text if '"compact"' in t]


def test_여러_칸이면_칸마다_HUD를_짧게_하라고_알리고_하단에_한_세트를_띄운다(grid, bridge):
    assert compact_msgs(bridge).count(True) >= 4          # 네 칸이 붙으며 각자 알렸다
    grid.wait_for_selector("#hud:not([hidden]) .hud-line")
    assert grid.locator("#hud .hud-line").count() == 4
    assert grid.locator("#hud .h-red").count() >= 1       # ANSI 색이 칠해졌다(95% 빨강)
    assert grid.locator("#hud img").count() == 0          # 글자는 글자로만 — 태그로 해석하지 않는다
    assert "<img" in grid.text_content("#hud")
    box = grid.locator("#hud").bounding_box()
    assert box["height"] < 60                             # 4줄이 두 단 = 두 줄 높이
    # 사이드바 밑까지 퍼지지 않고 칸들의 왼쪽 선에서 시작한다
    assert abs(box["x"] - grid.locator("#panes").bounding_box()["x"]) < 2
    # 두 열은 표처럼 붙어 있다 — 둘째 열이 화면 가운데까지 밀려나지 않는다
    first, second = (grid.locator("#hud .hud-line").nth(i).bounding_box() for i in (0, 1))
    assert abs(first["y"] - second["y"]) < 2              # 같은 줄(1행)에 나란히
    assert second["x"] - (first["x"] + first["width"]) < 40


def test_1칸으로_돌아오면_HUD를_원래대로_하라고_알리고_하단_HUD는_접는다(grid, bridge):
    grid.wait_for_selector("#hud:not([hidden])")
    bridge.ws_text.clear()
    click_pane(grid, 0)
    grid.click('#grid-bar button[data-n="1"]')
    grid.wait_for_timeout(500)
    assert compact_msgs(bridge) == [False]                # 남은 한 칸이 '원래대로'를 알렸다
    assert not grid.is_visible("#hud")


# ---------- 더블클릭 크게 보기 ----------
PRESS = re.compile(rb"\x1b\[<0;\d+;\d+M")              # 왼쪽 버튼 누름 리포트(tmux 로 가는 것)


def test_더블클릭하면_그_칸만_크게_다시_더블클릭하면_원래대로(grid, bridge):
    cols = grid.evaluate("panes[2].term.cols")
    grid.locator(".pane").nth(2).locator(".xterm-screen").dblclick()
    grid.wait_for_timeout(500)
    assert "max" in grid.get_attribute("#panes", "class")
    assert [grid.locator(".pane").nth(i).is_visible() for i in range(4)] == [False, False, True, False]
    assert grid.evaluate("panes[2].term.cols") > cols * 1.5          # 넓어진 만큼 PTY 도 따라 커졌다
    assert grid.text_content("#hdr-name") == "blog"
    # 두 번째 누름은 tmux 로 안 갔다 — 단어 선택·복사 모드에 들어가지 않는다
    assert len(PRESS.findall(bytes(bridge.by_session.get("blog", b"")))) <= 1
    grid.keyboard.type("ok")                                           # 크게 본 칸에 바로 친다
    grid.wait_for_timeout(300)
    assert typed(bridge, "blog").endswith(b"ok")
    grid.locator(".pane").nth(2).locator(".xterm-screen").dblclick()
    grid.wait_for_timeout(500)
    assert "max" not in (grid.get_attribute("#panes", "class") or "")
    assert all(grid.locator(".pane").nth(i).is_visible() for i in range(4))
    assert grid.evaluate("panes[2].term.cols") == cols                 # 원래 크기로
    assert grid.evaluate("panes.every(p => p.ws && p.ws.readyState === 1)")   # 감춘 칸도 계속 붙어 있었다


def test_크게_본_채로_칸_수를_바꾸면_크게_보기는_풀린다(grid, bridge):
    grid.locator(".pane").nth(1).locator(".xterm-screen").dblclick()
    grid.wait_for_timeout(300)
    grid.click('#grid-bar button[data-n="6"]')
    wait_until(grid, "panes.length === 6")
    assert "max" not in (grid.get_attribute("#panes", "class") or "")
    assert grid.locator(".pane.maxed").count() == 0


def test_1칸에서는_더블클릭이_예전처럼_tmux_로_간다(grid, bridge):
    click_pane(grid, 0)
    grid.click('#grid-bar button[data-n="1"]')
    wait_until(grid, "panes.length === 1")
    bridge.by_session.clear()
    grid.locator(".pane").nth(0).locator(".xterm-screen").dblclick()
    grid.wait_for_timeout(300)
    assert "max" not in (grid.get_attribute("#panes", "class") or "")
    assert len(PRESS.findall(bytes(bridge.by_session.get("wiki", b"")))) == 2   # 두 번 다 tmux 로(단어 선택)


def pad(page, slot, project=""):
    """패드 상주 프로그램가 브릿지에 보내는 것과 같은 POST — 같은 출처라 페이지 안에서 쏜다."""
    page.evaluate("([slot, project]) => fetch('/api/pad', {method: 'POST', "
                  "headers: {'Content-Type': 'application/json'}, body: JSON.stringify({slot, project})})",
                  [slot, project])


def test_패드_버튼은_여러_칸이면_그_번호_칸으로_간다(grid, bridge):
    click_pane(grid, 0)
    pad(grid, 3, "beta")                         # 칸이 넷이면 프로젝트 이름과 상관없이 3번 칸
    wait_until(grid, "activePane === panes[2]")
    assert grid.text_content("#hdr-name") == "blog"
    assert "3번 칸" in grid.text_content("#copy-toast")
    grid.keyboard.type("pad")                        # 키보드도 그 칸으로 간다
    grid.wait_for_timeout(300)
    assert typed(bridge, "blog") == b"pad"


def test_패드_버튼은_한_칸이면_그_프로젝트를_연다(grid, bridge):
    grid.click('#grid-bar button[data-n="1"]')
    wait_until(grid, all_connected(1))
    pad(grid, 2, "mall")
    wait_until(grid, "panes[0].session?.name === 'mall'")
    assert grid.text_content("#hdr-name") == "mall"
    pad(grid, 4, "apex")                             # 꺼진 프로젝트는 사이드바처럼 켤지 묻는다
    grid.wait_for_selector('#sessions li[data-name="apex"] .row-confirm')
