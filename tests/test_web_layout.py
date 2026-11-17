"""레이아웃 회귀 — 데스크톱 3열·활성 우선 정렬·상태배지, 폰 화면전환이 살아있는지.

폰 입력 회귀(test_web_ime.py)와 같은 가짜 브릿지를 쓴다. 여기서 보는 것은 '화면 구조'다:
사이드바가 터미널을 열어도 남아 있는가, 세션을 갈아탈 때 소켓이 하나만 열리는가,
목록 갱신(7초 폴링이 부르는 loadSessions)이 터미널 입력을 끊지 않는가.
"""
import json

import pytest
from conftest import fs_of, LONG_DOC, SESSIONS             # 가짜 브릿지와 한 값을 쓴다

pytest.importorskip("playwright", reason="브라우저 회귀 테스트 — pip install playwright 필요")

from playwright.sync_api import sync_playwright     # noqa: E402


# 이 파일은 '터미널 한 칸' 동작을 지킨다 — 기본값이 4칸(grid.js)이 된 뒤로는 1칸을 심고 시작한다.
# 탭마다 한 번만 심는다(sessionStorage) — 새로고침 테스트가 바꾼 기억을 다시 덮지 않게. 멀티 칸은 test_web_grid.py.
ONE_PANE = """try { if (!sessionStorage.getItem("frog.test.grid")) {
  localStorage.setItem("frog.grid", JSON.stringify({ n: 1, names: [] }));
  sessionStorage.setItem("frog.test.grid", "1"); } } catch (e) {}"""


def _page(pw, bridge, **ctx):
    browser = pw.chromium.launch(args=["--ignore-certificate-errors"])
    context = browser.new_context(ignore_https_errors=True, **ctx)
    context.add_init_script(ONE_PANE)
    page = context.new_page()
    page.goto(bridge.url)
    page.wait_for_selector("#sessions li")
    return browser, page


@pytest.fixture
def desktop(bridge):
    """데스크톱 폭(1280) — 사이드바가 상시 보이는 3열 쪽."""
    with sync_playwright() as pw:
        browser, page = _page(pw, bridge, viewport={"width": 1280, "height": 800})
        yield page
        browser.close()


@pytest.fixture
def phone(bridge):
    """폰 폭(412) — 목록·터미널이 한 화면씩 교대하는 쪽."""
    with sync_playwright() as pw:
        browser, page = _page(pw, bridge, has_touch=True, is_mobile=True,
                              viewport={"width": 412, "height": 900})
        yield page
        browser.close()


def open_terminal(page, nth=1):
    page.click(f"#sessions li:nth-child({nth}) b")   # 이름을 누른다 — 행 중앙은 좁은 폭에서 액션 버튼과 겹칠 수 있다
    page.wait_for_selector(".xterm-helper-textarea", state="attached")
    page.wait_for_timeout(500)                       # WS 연결·초기 렌더


def wait_ws(page, bridge, n):
    """열린 소켓 수가 n 으로 잦아들 때까지 기다린다 — 앞 테스트가 닫는 중일 수 있다(가짜 브릿지는 세션 공용)."""
    for _ in range(30):
        if bridge.ws_open == n:
            return
        page.wait_for_timeout(100)
    assert bridge.ws_open == n


# ---------- 데스크톱 3열 ----------

def test_데스크톱_사이드바는_터미널_열어도_보인다(desktop):
    open_terminal(desktop)
    assert desktop.is_visible("#sessions")            # 사이드바 상시 — 프로젝트를 수시로 왕복
    assert desktop.is_visible("#topbar")
    assert desktop.is_visible("#terminal")


def test_상태배지와_요약(desktop):
    assert "세션" in desktop.text_content("#sum-alive")
    assert "결재" in desktop.text_content("#sum-pending")
    assert desktop.locator("#sessions li .st").count() >= 1
    assert "hot" in (desktop.get_attribute("#sum-pending", "class") or "")   # 대기 1건 → 빨강


def test_활성_프로젝트가_먼저_보인다(desktop):
    """결재 대기 → 진행중 → 완료 → 그 외, 같은 상태 안에서만 이름순. 손댈 것이 항상 맨 위.

    픽스처 이름은 가나다순이 상태순과 정반대다(이름순이면 apex·blog·frog·mall·wiki) —
    정렬에서 상태 항을 지우고 이름순으로만 세우면 이 목록이 통째로 뒤집혀 곧바로 깨진다.
    """
    assert desktop.locator("#sessions li b").all_text_contents() == \
        ["wiki", "mall", "blog", "frog", "apex"]
    assert "결재" in desktop.text_content("#sessions li:nth-child(1) .st")
    assert "진행중" in desktop.text_content("#sessions li:nth-child(2) .st")
    assert "완료" in desktop.text_content("#sessions li:nth-child(3) .st")   # blog·frog 는 둘 다 완료
    assert "완료" in desktop.text_content("#sessions li:nth-child(4) .st")   # → 이름순으로 blog 다음
    assert "꺼짐" in desktop.text_content("#sessions li:nth-child(5) .st")


def test_사이드바_검색으로_프로젝트를_거른다(desktop):
    """프로젝트가 열댓 개면 눈으로 못 찾는다 — 이름·경로 어느 쪽에 걸려도, 대소문자 무관하게.
    검색어는 화면에서만 거른다(서버 목록·결재 수는 그대로). Esc = 지우고 전체로."""
    box = desktop.locator("#proj-filter")
    try:
        box.fill("WI")                                           # 대문자로 쳐도 wiki 가 나와야 한다
        desktop.wait_for_timeout(400)
        assert desktop.locator("#sessions li b").all_text_contents() == ["wiki"]
        box.fill("이런프로젝트없음")
        desktop.wait_for_timeout(400)
        assert desktop.locator("#sessions li").count() == 0
        assert desktop.locator("#proj-empty").is_visible()       # 빈 화면엔 이유를 적는다
        box.press("Escape")                                      # Esc 한 번 = 전체 목록 복귀
        desktop.wait_for_timeout(400)
        assert desktop.locator("#sessions li").count() == 5
        assert not desktop.locator("#proj-empty").is_visible()
    finally:
        desktop.evaluate("projFilter = ''; document.getElementById('proj-filter').value = ''; loadSessions()")
        desktop.wait_for_timeout(200)


def test_결재버튼은_인박스를_연다(desktop):
    desktop.click("#sum-pending")
    assert desktop.is_visible("#inbox-view")
    assert desktop.is_visible("#sessions")            # 인박스를 봐도 사이드바는 남는다


# ---------- 세션 갈아타기 ----------

# ── 전체 끄기 / 전체 복원 ──────────────────────────────
# 되돌릴 수 없는 조작이 헤더에 상시 노출돼 있다 — 잘못 눌러 세션 열댓 개가 한 번에 죽는 일이
# 없어야 하고, 되살리는 쪽은 '그때 열려 있던 것만' 이어야 한다.

def test_전체끄기는_되묻기_전에는_아무것도_안_끈다(desktop, bridge):
    bridge.kill_alls = 0                              # 가짜 브릿지는 세션 공용 — 세는 값은 여기서 0부터
    desktop.click("#btn-killall")
    assert desktop.is_visible("#bulk-confirm")
    assert "전부 끕니다" in desktop.text_content("#bulk-ask")
    assert bridge.kill_alls == 0                      # 아직 아무 일도 일어나지 않았다
    desktop.click("#bulk-no")
    assert not desktop.is_visible("#bulk-confirm")
    assert bridge.kill_alls == 0


def test_되묻기에_모두_끄기를_눌러야_끈다(desktop, bridge):
    bridge.kill_alls = 0
    desktop.click("#btn-killall")
    desktop.click("#bulk-yes")
    desktop.wait_for_function("() => document.querySelector('#conn-hint')?.textContent.includes('껐습니다')")
    assert bridge.kill_alls == 1
    assert not desktop.is_visible("#bulk-confirm")


def test_복원_버튼은_복원거리가_있을_때만_눌린다(desktop, bridge):
    """눌러 보고서야 '복원할 게 없다'는 말을 듣게 하지 않는다."""
    bridge.restores, bridge.restorable = 0, 0         # 앞선 테스트가 남긴 복원거리를 지우고 시작
    desktop.reload()
    desktop.wait_for_selector("#sessions li")
    assert desktop.locator("#btn-restore").is_disabled()
    desktop.click("#btn-killall")
    desktop.click("#bulk-yes")                        # 끄고 나면 복원거리가 생긴다
    desktop.wait_for_function("() => !document.querySelector('#btn-restore').disabled")
    desktop.click("#btn-restore")
    desktop.wait_for_function("() => document.querySelector('#conn-hint')?.textContent.includes('되살렸습니다')")
    assert bridge.restores == 1


def test_폰_헤더는_버튼이_늘어도_가로로_안_넘친다(phone):
    """폰(412)에서 헤더가 넘치면 ⚙️ 설정이 화면 밖으로 밀려 영영 못 누른다."""
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    for sel in ("#btn-cpu", "#btn-killall", "#btn-restore", "#btn-settings"):
        box = phone.locator(sel).bounding_box()
        assert box and box["x"] + box["width"] <= 413, sel      # 전부 화면 안에 있다


def test_세션을_갈아타면_이전_소켓이_닫힌다(desktop, bridge):
    """소켓이 두 개 열려 있으면 두 세션의 글자가 한 화면에 섞인다."""
    open_terminal(desktop, 1)
    wait_ws(desktop, bridge, 1)
    open_terminal(desktop, 2)
    wait_ws(desktop, bridge, 1)                       # 둘이 아니라 하나 — 이전 것이 접혔다
    assert bridge.ws_log[-2:] == ["wiki", "mall"]
    assert desktop.text_content("#hdr-name") == "mall"


def test_터미널_한_바퀴_동안_페이지_오류가_없다(desktop, bridge):
    """칸(Pane) 구조로 옮기며 생긴 참조 오류는 화면이 멀쩡해 보여도 콘솔에서만 터진다 —
    열기·입력·갈아타기·창 크기·닫기·다시 열기 한 바퀴 동안 pageerror 가 0건이어야 한다."""
    errors = []
    desktop.on("pageerror", lambda e: errors.append(str(e)))
    open_terminal(desktop, 1)
    desktop.keyboard.type("ls")
    open_terminal(desktop, 2)
    desktop.set_viewport_size({"width": 1100, "height": 700})
    desktop.wait_for_timeout(300)
    desktop.evaluate("closeTerminal(); showList()")
    open_terminal(desktop, 1)
    desktop.keyboard.type("가")
    desktop.wait_for_timeout(300)
    assert errors == []


def test_목록_갱신은_터미널_입력을_끊지_않는다(desktop, bridge):
    """7초 폴링이 하는 일(loadSessions)을 그대로 시켜도 포커스·입력이 살아있어야 한다."""
    open_terminal(desktop)
    bridge.received.clear()
    desktop.evaluate("loadSessions()")
    desktop.wait_for_timeout(300)
    assert desktop.evaluate("document.activeElement.classList.contains('xterm-helper-textarea')")
    desktop.keyboard.type("ls")
    desktop.wait_for_timeout(500)
    assert bytes(bridge.received) == b"ls"


def test_쉬프트엔터는_전송이_아니라_줄바꿈(desktop, bridge):
    """Zed·VS Code 처럼 ⇧Enter = 줄바꿈. 터미널은 Enter 에 ⇧를 못 실으므로
    클로드 공식 /terminal-setup 과 같은 백슬래시+CR 을 보낸다 — 클로드 입력창이 줄바꿈으로 받는다."""
    open_terminal(desktop)
    bridge.received.clear()
    desktop.keyboard.press("Shift+Enter")
    desktop.wait_for_timeout(400)
    assert bytes(bridge.received) == b"\\\r"
    bridge.received.clear()
    desktop.keyboard.press("Enter")                   # 민짜 Enter 는 그대로 전송(CR)
    desktop.wait_for_timeout(400)
    assert bytes(bridge.received) == b"\r"


# ---------- 폰 화면 전환 ----------

def test_창을_좁히면_폰_배치로_바뀐다(desktop):
    """터미널을 연 채 창 폭이 경계를 넘어도 목록이 터미널을 밀고 들어오지 않는다."""
    open_terminal(desktop)
    desktop.set_viewport_size({"width": 412, "height": 900})
    desktop.wait_for_timeout(200)
    assert not desktop.is_visible("#sessions")
    assert desktop.is_visible("#terminal")
    desktop.set_viewport_size({"width": 1280, "height": 800})
    desktop.wait_for_timeout(200)
    assert desktop.is_visible("#sessions")            # 다시 넓히면 사이드바 복귀



def test_폰은_목록과_터미널을_오간다(phone):
    open_terminal(phone)
    assert not phone.is_visible("#sessions")          # 폰은 한 화면씩 — 목록을 치운다
    assert phone.is_visible("#terminal")
    phone.click("#back")
    phone.wait_for_selector("#sessions li")
    assert phone.is_visible("#sessions")
    assert not phone.is_visible("#term-view")


# ---------- 결재 인박스 ----------
# 이 프로젝트의 핵심. 사용자가 "내가 뭘 시켰는지·뭐가 나를 기다리는지"를 잊지 않게 하는 화면이라,
# 카드가 보이는 것만으로는 부족하고 "누르면 그 터미널로 답이 실제로 나가는가"까지가 계약이다.

def open_inbox(page):
    page.click("#sum-pending")
    page.wait_for_selector(".inbox-card")
    return page.locator(".inbox-card")


def test_인박스_카드와_선택지_전송(desktop, bridge):
    """섹션 안에서는 오래된 것이 먼저(= 놓친 것부터), 선택지 버튼은 그 세션에 숫자 답을 쏜다."""
    bridge.answers.clear()
    cards = open_inbox(desktop)
    assert cards.count() == 4                             # 결재 1 + 완료 2 + 세션 꺼짐 1
    assert "wiki" in cards.nth(0).text_content()          # 12:00 — 지금 답할 수 있는 결재
    assert "이 파일을 지울까요?" in cards.nth(0).text_content()
    assert "gone" in cards.nth(3).text_content()          # 답을 못 보내는 카드는 맨 아래 섹션으로
    cards.nth(0).locator(".opt-btn").first.click()
    desktop.wait_for_timeout(400)
    assert bridge.answers == [{"project": "wiki", "sessionId": "wiki-1",
                               "type": "option", "value": "1"}]


def test_인박스_텍스트_답_전송(desktop, bridge):
    """피커 선택지로 안 되는 지시는 직접 쳐서 그 터미널로 보낸다."""
    bridge.answers.clear()
    card = open_inbox(desktop).nth(0)
    card.locator("textarea").fill("2번으로 가고 테스트 먼저 돌려")
    card.locator(".send-btn").click()
    desktop.wait_for_timeout(400)
    assert bridge.answers[0]["type"] == "text"
    assert bridge.answers[0]["value"] == "2번으로 가고 테스트 먼저 돌려"
    assert bridge.answers[0]["project"] == "wiki"


def test_결재_머리글과_헤드라인은_같은_수를_말한다(desktop):
    """상단 헤드라인 '결재 대기 N건'은 살아 있는 것만 센다(서버가 live 로 거른다). 머리글
    '🔴 결재 필요 (N)'이 죽은 세션 카드까지 세면 두 숫자가 어긋나, 정확한 수를 알려주려고
    있는 화면이 사용자에게 '어느 쪽을 믿어야 하나'를 떠넘긴다 — 실측 1건 vs (2).
    죽은 카드는 숨기지 않는다: 답만 못 보낼 뿐 사용자가 손봐야 할 일이라 따로 모아 둔다."""
    open_inbox(desktop)
    # 헤드라인은 상태를 다 말한다 — 결재·지시 대기, 꺼짐은 있을 때만(가짜 브릿지: 1·2·1)
    assert desktop.text_content("#sum-pending") == "결재 대기 1건 · 지시 대기 2건 · 꺼짐 1건"
    heads = desktop.locator("#inbox-cards h3")
    assert heads.first.text_content() == "결재 필요 (1)"             # 헤드라인과 같은 수
    assert "꺼짐" in heads.last.text_content()                       # 못 누르는 카드는 맨 아래 따로
    assert "(1)" in heads.last.text_content()
    assert desktop.locator(".inbox-card.dead").count() == 1          # 사라지지는 않는다


def test_이미_넘어간_화면은_숫자가_아니라_사람_말로_알린다(desktop, bridge):
    """답은 '물어본 클로드'가 아니라 '같은 이름의 tmux 세션'으로 간다. 그래서 보내려는 순간 그
    화면에 피커가 없으면 숫자가 명령으로 실행돼 버리는데, 브릿지가 이제 그걸 409 로 막는다
    (아무것도 보내지 않고 카드도 지우지 않는다). 화면이 '서버 응답 409' 라는 숫자를 보여주면
    사용자는 무슨 일인지도, 뭘 해야 하는지도 알 수 없다 — 그리고 아무것도 안 갔으니 다시 누를 수
    있어야 한다."""
    bridge.answers.clear()
    card = open_inbox(desktop).nth(0)                    # wiki 결재(피커)
    with bridge.picker_gone():
        card.locator(".opt-btn").first.click()
        desktop.wait_for_timeout(500)
    note = desktop.text_content("#inbox-note")
    assert bridge.answers == []                          # 정말 아무것도 나가지 않았다
    assert "409" not in note                             # 숫자를 사용자에게 떠넘기지 않는다
    assert "넘어갔" in note and "보내지 않았" in note      # 무슨 일이 있었는지
    assert "터미널 열기" in note                          # 다음에 뭘 하면 되는지까지 한 문장에
    assert "꺼져" not in note                             # 404(세션이 죽음)와 헷갈리면 안 된다
    # 아무것도 안 갔으니 잠기면 안 된다 — 다시 누르면 이번엔 들어간다
    assert desktop.locator(".inbox-card.sent").count() == 0
    again = desktop.locator(".inbox-card").nth(0)
    assert again.locator(".opt-btn").first.is_enabled()
    again.locator(".opt-btn").first.click()
    desktop.wait_for_timeout(400)
    assert bridge.answers == [{"project": "wiki", "sessionId": "wiki-1",
                               "type": "option", "value": "1"}]


def test_열번째_이상_선택지는_버튼으로_안_만든다(desktop, bridge):
    """tmux send-keys "12" 는 '1','2' 두 키를 보낸다(실증). 클로드 피커는 숫자 하나로 즉시
    고르므로 옵션이 10개 넘는 화면에서 '12번' 버튼을 누르면 1번이 선택되고 남은 '2' 가 다음
    프롬프트로 흘러간다 — 화면엔 '보냄: 12. …' 가 남아 사용자는 12번을 골랐다고 믿는다.
    그러니 그런 버튼은 애초에 만들지 않는다: 무엇이 있는지는 글로 보여주고 고르는 일은
    [터미널 열기]에 맡긴다. 1~9번 버튼은 그대로 눌린다."""
    bridge.answers.clear()
    open_inbox(desktop)
    many = {"sessionId": "wiki-9", "project": "wiki", "cwd": "/tmp/wiki", "machine": "mac",
            "kind": "picker", "message": "어느 모델로 갈까요?", "question": "어느 모델로 갈까요?",
            "updatedAt": "2026-08-05T15:00:00+09:00", "live": True,
            "options": [{"n": i, "label": f"옵션{i}"} for i in range(1, 13)]}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"decisions": [many]})))
    desktop.evaluate("loadInbox()")
    desktop.wait_for_timeout(300)
    card = desktop.locator(".inbox-card")
    assert card.locator(".opt-btn").count() == 9                  # 한 키로 보낼 수 있는 것만 버튼
    assert card.locator(".opt-btn").last.text_content().startswith("9.")
    text = card.text_content()
    assert "12. 옵션12" in text                                    # 무엇이 있는지는 보여준다
    assert "터미널" in text                                        # 어떻게 고르는지도 써 준다
    assert card.locator(".open-btn").count() == 1                 # 그 길이 실제로 카드에 있다
    card.locator(".opt-btn").last.click()                         # 1~9 는 그대로 눌린다
    desktop.wait_for_timeout(400)
    assert bridge.answers == [{"project": "wiki", "sessionId": "wiki-9",
                               "type": "option", "value": "9"}]


def test_죽은_세션_카드는_답을_못_보낸다(desktop, bridge):
    """live=False = tmux 세션이 죽은 유령 카드. 답을 보내면 서버가 404 다.
    흐려진 보내기 버튼도 '눌러 볼 수 있는 것'으로 읽힌다(디자이너 지적) — 입력창·버튼을
    아예 그리지 않는다. 이 카드의 길은 [터미널 열기] 하나(new-session -A 로 다시 켜 준다)."""
    bridge.answers.clear()
    open_inbox(desktop)
    dead = desktop.locator(".inbox-card.dead")
    assert dead.count() == 1
    assert "gone" in dead.text_content()
    # '세션 꺼짐' 안내는 카드가 아니라 그 섹션 제목이 말한다 — 카드마다 반복하면 소음
    assert dead.locator("textarea").count() == 0           # 가짜 활성 버튼 금지 — 아예 없다
    assert dead.locator(".send-btn").count() == 0
    assert dead.locator(".opt-btn").count() == 0
    assert dead.locator(".open-btn").is_enabled()
    assert bridge.answers == []                            # 누를 것 자체가 없다 — 아무것도 안 나갔다


def test_보낸_카드는_다시_못_누른다(desktop, bridge):
    """같은 답이 두 번 들어가면 터미널에 숫자가 두 번 찍힌다.
    서버 반영에는 시차가 있어 7초 폴링이 같은 카드를 되살리는데, 그 뒤에도 잠겨 있어야 한다."""
    bridge.answers.clear()
    open_inbox(desktop).nth(0).locator(".opt-btn").first.click()
    desktop.wait_for_timeout(400)
    assert len(bridge.answers) == 1
    desktop.evaluate("loadInbox()")                        # 폴링이 하는 일 그대로
    desktop.wait_for_timeout(400)
    sent = desktop.locator(".inbox-card.sent")
    assert sent.count() == 1
    assert "보냄" in sent.text_content()                   # 보냈다는 표시가 있어야 다시 안 누른다
    assert sent.locator(".opt-btn").first.is_disabled()
    sent.locator(".opt-btn").first.click(force=True)
    desktop.wait_for_timeout(300)
    assert len(bridge.answers) == 1                        # 두 번째는 안 나갔다


def test_카드가_되살아나도_같은_질문이면_잠긴다(desktop, bridge):
    """진짜 브릿지는 답을 넣고 카드 파일을 지우지만, 화면스캔(capture-pane)이 아직 그 피커를 보고
    있으면 같은 질문이 sessionId 없이 다시 나타난다. 답은 sessionId 가 아니라 project(tmux 세션)로
    나가므로 그 카드도 잠겨야 한다 — 반대로 '다음 질문'은 곧바로 답할 수 있어야 한다."""
    bridge.answers.clear()
    open_inbox(desktop).nth(0).locator(".opt-btn").first.click()
    desktop.wait_for_timeout(400)
    assert len(bridge.answers) == 1

    ghost = {"sessionId": None, "project": "wiki", "cwd": "/tmp/wiki", "machine": "mac",
             "kind": "picker", "message": "이 파일을 지울까요?", "question": "이 파일을 지울까요?",
             "updatedAt": "", "live": True,
             "options": [{"n": 1, "label": "네, 지웁니다"}, {"n": 2, "label": "아니요"}]}
    payload = {"decisions": [ghost]}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(payload)))

    desktop.evaluate("loadInbox()")
    desktop.wait_for_timeout(300)
    assert desktop.locator(".inbox-card.sent").count() == 1
    desktop.locator(".opt-btn").first.click(force=True)
    desktop.wait_for_timeout(300)
    assert len(bridge.answers) == 1                        # 되살아난 카드로도 두 번 안 나간다

    payload["decisions"] = [{**ghost, "message": "다른 파일도 지울까요?",
                             "question": "다른 파일도 지울까요?"}]
    desktop.evaluate("loadInbox()")
    desktop.wait_for_timeout(300)
    assert desktop.locator(".inbox-card.sent").count() == 0    # 새 질문까지 잠그면 일이 멈춘다
    desktop.locator(".opt-btn").first.click()
    desktop.wait_for_timeout(400)
    assert len(bridge.answers) == 2


# 인박스 카드 순서(픽스처 기준): 0=wiki 결재(피커), 1=wiki 완료, 2=frog 완료, 3=gone(세션 꺼짐).
# 2번 섹션(✅ 완료 — 다음 지시 대기)은 사용자가 "다음엔 이거 해" 를 넣는 자리라 가장 자주 쓴다.

def test_완료카드_지시는_다음_완료카드를_잠그지_않는다(desktop, bridge):
    """완료 카드에는 묻는 말이 없다 — 훅이 Stop 에서 message:"" 로 쓴다(hooks/frog_hook.py).
    잠금을 질문 글로 대조하면 완료 카드끼리 빈 문자열로 겹쳐, 지시를 한 번 보낸 프로젝트는
    다음 턴이 끝나 새 완료 카드가 떠도 잠긴 채 '보냄: 옛 지시' 를 단다 — 보내지도 않은 것을
    보냈다고 표시하고 다음 지시를 60초간 못 넣는다(이 앱이 막으려던 '일이 멈춘 걸 모르고
    넘어감' 그대로)."""
    bridge.answers.clear()
    cards = open_inbox(desktop)
    cards.nth(1).locator("textarea").fill("테스트부터 돌려")      # wiki 완료 카드(13:00)
    cards.nth(1).locator(".send-btn").click()
    desktop.wait_for_timeout(400)
    assert len(bridge.answers) == 1
    assert desktop.locator(".inbox-card.sent").count() == 1      # 잠기는 건 그 카드 하나뿐

    # 다음 턴이 끝나 새로 뜬 완료 카드. 훅은 클로드 세션당 카드 1장을 덮어쓰므로 sessionId 는
    # 그대로고 시각만 나아간다 — sessionId 만으로 잠그면 이 카드도 함께 잠겨 버린다.
    nxt = {"sessionId": "wiki-2", "project": "wiki", "cwd": "/tmp/wiki", "machine": "mac",
           "kind": "done", "message": "", "updatedAt": "2026-08-05T14:00:00+09:00", "live": True}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"decisions": [nxt]})))
    desktop.evaluate("loadInbox()")
    desktop.wait_for_timeout(300)
    assert desktop.locator(".inbox-card.sent").count() == 0      # 새 완료 카드는 열려 있어야 한다
    card = desktop.locator(".inbox-card")
    card.locator("textarea").fill("이번엔 배포 준비")
    card.locator(".send-btn").click()
    desktop.wait_for_timeout(400)
    assert [a["value"] for a in bridge.answers] == ["테스트부터 돌려", "이번엔 배포 준비"]


def test_완료카드_연타는_막는다(desktop, bridge):
    """같은 지시가 두 번 들어가면 그 터미널에 두 번 타이핑된다. 브릿지가 카드를 지워도 7초
    폴링이 같은 카드를 잠깐 되살리는데, 그 카드는 그동안 잠겨 있어야 한다."""
    bridge.answers.clear()
    card = open_inbox(desktop).nth(2)                            # frog 완료 카드(13:20)
    card.locator("textarea").fill("문서까지 마저 정리해")
    card.locator(".send-btn").click()
    desktop.wait_for_timeout(400)
    assert len(bridge.answers) == 1
    desktop.evaluate("loadInbox()")                              # 폴링이 하는 일 그대로
    desktop.wait_for_timeout(400)
    sent = desktop.locator(".inbox-card.sent")
    assert sent.count() == 1
    assert "frog" in sent.text_content()
    assert "보냄: 문서까지 마저 정리해" in sent.text_content()      # 뭘 보냈는지 화면에 남는다
    assert sent.locator("textarea").is_disabled()
    assert sent.locator(".send-btn").is_disabled()
    sent.locator(".send-btn").click(force=True)                  # 비활성인데도 억지로 눌러본다
    desktop.wait_for_timeout(300)
    assert len(bridge.answers) == 1                              # 두 번째는 안 나갔다


def test_폰에서도_인박스를_쓴다(phone):
    """폰은 한 화면씩 — 인박스를 열면 목록이 빠지고, 선택지는 손가락으로 누를 크기여야 한다."""
    cards = open_inbox(phone)
    assert not phone.is_visible("#sessions")
    box = cards.nth(0).locator(".opt-btn").first.bounding_box()
    assert box["height"] >= 44                       # 최소 탭 영역
    assert box["width"] > 300                        # 폰 폭(412)을 꽉 채운다
    assert phone.is_visible("#inbox-back")           # 목록으로 돌아갈 길


def test_인박스_카드에서_터미널로_간다(desktop):
    """카드에서 바로 그 프로젝트 터미널로 — 답만으로 안 되는 일은 직접 본다."""
    open_inbox(desktop).nth(0).locator(".open-btn").click()
    desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
    assert desktop.text_content("#hdr-name") == "wiki"
    assert desktop.text_content("#hdr-path") == "/tmp/wiki"
    assert not desktop.is_visible("#inbox-view")


# ---------- 파일 패널 ----------
# 폰에서 "이 프로젝트에 뭐가 들었지"를 보고, md 를 그 자리에서 고쳐 저장하는 화면.
# 계약의 핵심 셋: ① 파일 내용은 textContent 로만 들어간다 ② 파일 버전 토큰은 문자열 그대로 왕복한다
# ③ 저장 안 한 수정은 어떤 경우에도 말없이 사라지지 않는다.

def open_panel(page, nth=1):
    open_terminal(page, nth)
    if not page.is_visible("#file-panel"):    # 데스크톱은 자동으로 열린다 — 폰(좁은 화면)만 손으로 연다
        page.click("#files-toggle")
    page.wait_for_selector("#fp-body li")


def open_file(page, name):
    page.click(f"#fp-body li:has-text('{name}')")
    page.wait_for_selector("#fp-edit")


def tree_rows(page):
    """지금 트리에 보이는 줄 — 깊이·꺾쇠·이름. 화면에 실제로 늘어선 순서 그대로 온다."""
    return page.eval_on_selector_all("#fp-tree li", """els => els.map(e => ({
        depth: e.style.getPropertyValue('--depth'),
        chev: e.querySelector('.tree-chev')?.textContent ?? '',
        name: e.querySelector('.tree-name')?.textContent ?? e.textContent}))""")


def names(page):
    return [r["name"] for r in tree_rows(page)]


def expand(page, folder, until):
    """폴더 줄을 눌러 편다 — until 이 그 줄 아래에 나타날 때까지 기다린다."""
    page.click(f"#fp-tree li:has-text('{folder}')")
    page.wait_for_selector(f"#fp-tree li:has-text('{until}')")


def test_터미널에서_생긴_폴더가_트리에_저절로_나타난다(desktop, bridge):
    """오른쪽 패널에 폴더가 생겨도 실시간으로 안 보이던 문제 — 트리가 첫 읽기 그대로 멈춰 있었다."""
    bridge.fs_patch.clear()
    open_panel(desktop)
    assert "새폴더" not in names(desktop)
    bridge.fs_patch[("wiki", "")] = fs_of("wiki")[""][:1] + [{"name": "새폴더", "dir": True, "size": None}] \
        + fs_of("wiki")[""][1:]
    desktop.wait_for_selector("#fp-tree li:has-text('새폴더')", timeout=10000)   # 7초 폴링 안에
    assert "새폴더" in names(desktop)
    bridge.fs_patch.clear()


def test_갱신해도_펼쳐_둔_폴더와_선택은_그대로다(desktop, bridge):
    """다시 읽는다고 트리를 접어 버리면 사용자가 펼쳐 놓은 자리를 매번 잃는다."""
    bridge.fs_patch.clear()
    open_panel(desktop)
    expand(desktop, "docs", "노트.md")
    bridge.fs_patch[("wiki", "docs")] = fs_of("wiki")["docs"] + [{"name": "메모2.md", "dir": False, "size": 3}]
    desktop.wait_for_selector("#fp-tree li:has-text('메모2.md')", timeout=10000)
    rows = tree_rows(desktop)
    docs = next(r for r in rows if r["name"] == "docs")
    assert docs["chev"] == "▾"                                        # 여전히 펼쳐져 있다
    assert [r["name"] for r in rows if r["depth"] == "1"] == ["설계", "노트.md", "메모2.md"]
    bridge.fs_patch.clear()


def test_파일패널은_기본으로_같이_열린다(desktop):
    """넓은 화면에서 터미널을 열면 파일 패널이 저절로 같이 열린다(열림이 기본값)."""
    open_terminal(desktop)
    desktop.wait_for_selector("#fp-body li")
    assert desktop.is_visible("#file-panel")
    assert desktop.text_content("#fp-crumb").startswith("wiki")   # 지금 그 프로젝트를 본다


def test_패널을_닫은_선택은_기기에_남는다(desktop):
    """✕ 로 닫으면 다음 터미널부터 닫힌 채로(선택 기억), 📁 로 다시 열면 기본값(열림)으로 돌아온다."""
    open_terminal(desktop, 1)
    desktop.wait_for_selector("#fp-body li")
    desktop.click("#fp-close")
    desktop.wait_for_timeout(100)
    open_terminal(desktop, 2)                        # 다른 세션으로 갈아타도
    desktop.wait_for_timeout(300)
    assert not desktop.is_visible("#file-panel")     # 닫은 선택이 이긴다
    desktop.click("#files-toggle")                   # 손으로 다시 열면 기본값 복귀
    desktop.wait_for_selector("#fp-body li")
    open_terminal(desktop, 1)
    desktop.wait_for_selector("#fp-body li")         # 다음 터미널부터 도로 자동 열림
    assert desktop.is_visible("#file-panel")


def test_폰에서는_파일패널이_자동으로_안_열린다(phone):
    """폰은 패널이 화면을 통째로 덮는다 — 자동으로 열면 터미널이 안 보인다. 📁 로만 연다."""
    open_terminal(phone)
    phone.wait_for_timeout(300)
    assert not phone.is_visible("#file-panel")


def test_좁은_데스크톱_폭에서는_파일패널이_자동으로_안_열린다(desktop):
    """폴드 펼침(~904px)은 3열을 깔면 터미널이 40칸대로 짜부라진다 — 자동 열림은 1100px 부터.
    📁 로 손으로 여는 것은 어느 폭에서든 된다."""
    desktop.set_viewport_size({"width": 904, "height": 800})
    open_terminal(desktop)
    desktop.wait_for_timeout(300)
    assert not desktop.is_visible("#file-panel")
    desktop.click("#files-toggle")                   # 손으로는 열린다
    desktop.wait_for_selector("#fp-body li")


# ---------- 병렬 터미널 탭 — 같은 프로젝트에서 터미널 여러 개(Zed 차용) ----------

def test_플러스로_같은_프로젝트_터미널을_하나_더_연다(desktop, bridge):
    """+ 를 누르면 wiki@2 세션으로 새 터미널 — 헤더 이름·파일 패널은 여전히 프로젝트(wiki) 기준."""
    open_terminal(desktop)                                  # wiki
    desktop.click("#term-tabs .tab-plus")
    desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
    desktop.wait_for_timeout(500)
    assert bridge.ws_log[-1] == "wiki@2"                    # 새 소켓은 탭 세션으로 붙는다
    assert desktop.text_content("#hdr-name") == "wiki"      # 헤더 이름은 프로젝트 그대로
    tabs = desktop.locator("#term-tabs .tab")
    assert tabs.count() == 2
    assert "on" in (tabs.nth(1).get_attribute("class") or "")    # 2번 탭이 활성
    assert desktop.text_content("#fp-crumb").startswith("wiki")  # 파일 패널도 프로젝트 기준


def test_탭_닫기는_되묻고_그_탭만_끈다(desktop, bridge):
    bridge.kills.clear()
    open_terminal(desktop)
    desktop.click("#term-tabs .tab-plus")                   # wiki@2 열고 그 위에서
    desktop.wait_for_timeout(500)
    desktop.locator("#term-tabs .tab.on .tab-x").click()    # ✕ → 되묻기
    desktop.wait_for_timeout(200)
    assert bridge.kills == []                               # 확인 전에는 아무것도 안 죽는다
    desktop.locator("#term-tabs button.danger").click()
    desktop.wait_for_timeout(600)
    assert bridge.kills == ["wiki@2"]                       # 그 탭만 죽는다
    assert bridge.ws_log[-1] == "wiki"                      # 화면은 기본 터미널로 복귀
    assert desktop.locator("#term-tabs .tab").count() == 1


def test_서버가_알려준_탭은_어느_기기에서든_보인다(desktop):
    """탭 목록의 정본은 서버(sessions[].tabs) — 폰에서 연 탭이 맥 탭 바에도 나타난다."""
    open_terminal(desktop, 2)                               # mall — 가짜 브릿지가 tabs:[3] 을 준다
    desktop.wait_for_timeout(500)
    tabs = desktop.locator("#term-tabs .tab")
    assert tabs.count() == 2
    assert tabs.nth(1).text_content().startswith("3")


def test_포커스를_받으면_focus_in_이_터미널로_간다(desktop, bridge):
    """tmux(focus-events on)가 1004 를 켜면 xterm 은 포커스 때 \\x1b[I 를 보낸다 — 폰과 맥이
    같은 세션에 붙어 크기를 서로 뺏을 때 '지금 보고 있는 쪽'이 창 크기를 되찾는 핵심 고리.
    1004 는 이 테스트 안에서만 켠다 — 인사말에서 전역으로 켜면 입력 바이트를 정확 일치로
    검사하는 IME·키바 테스트에 focus 신호가 섞인다(실측 6개 파손)."""
    open_terminal(desktop)
    desktop.evaluate("term.write('\\x1b[?1004h')")   # tmux 가 켜 주는 것을 재현
    desktop.wait_for_timeout(100)
    bridge.received.clear()
    desktop.locator(".xterm-helper-textarea").evaluate("e => e.blur()")
    desktop.wait_for_timeout(100)
    desktop.locator(".xterm-helper-textarea").evaluate("e => e.focus()")
    desktop.wait_for_timeout(200)
    data = bytes(bridge.received)
    assert b"\x1b[O" in data, "블러 때 focus-out 이 안 갔다"
    assert b"\x1b[I" in data, "포커스 때 focus-in 이 안 갔다"


def test_파일패널_탐색과_보기(desktop):
    open_panel(desktop)
    expand(desktop, "docs", "노트.md")                        # 폴더 펼치기
    desktop.click("#fp-body li:has-text('노트.md')")          # 파일 열기
    desktop.wait_for_selector("#fp-body pre")
    assert desktop.text_content("#fp-body pre") == "wiki 메모"    # 본문에 프로젝트 이름이 박혀 있다
    assert "wide" in (desktop.get_attribute("#file-panel", "class") or "")   # 파일을 열면 넓어진다
    assert desktop.text_content("#fp-crumb").startswith("wiki")   # 어느 프로젝트인지 항상 앞에 붙는다


def test_파일패널_이미지는_그자리에서_읽기전용으로_보인다(desktop, bridge):
    """이미지 클릭 → 같은 #fp-view에 표시, 받는 동안 안내, 편집·저장 길은 없음."""
    open_panel(desktop)
    # 공용 트리의 정확한 정렬 회귀는 그대로 두고, 이 테스트에만 서버가 가진 이미지 줄을 보탠다.
    desktop.evaluate("""() => {
      fp.root.children.push(fpNode('shot.png', 'shot.png', false));
      fpRenderTree();
    }""")
    with bridge.slow_files("wiki", 0.6):
        desktop.locator(".tree-row", has_text="shot.png").click()
        assert "이미지를 불러오는 중" in desktop.text_content("#fp-note")
        desktop.wait_for_selector("#fp-view img.fp-image:not([hidden])")
    img = desktop.locator("#fp-view img.fp-image")
    assert img.evaluate("e => [e.naturalWidth, e.naturalHeight]") == [192, 192]
    assert desktop.locator("#fp-edit, #fp-save, #fp-cancel, #fp-view textarea").count() == 0
    assert desktop.locator("#fp-view-close").count() == 1
    assert "shot.png" in desktop.text_content(".fp-image-meta")
    assert img.evaluate("e => getComputedStyle(e).maxWidth") == "100%"   # 폰에서도 폭을 넘지 않는다
    assert desktop.locator("#fp-body").evaluate("e => getComputedStyle(e).overflowY") == "auto"


def test_파일뷰는_닫기버튼과_폴더이동으로_닫힌다(desktop):
    """열어 둔 파일이 계속 남아 있다 — ✕ 닫기로 손으로, 폴더로 나가면 자동으로 닫힌다.
    저장 안 한 수정이 있을 때 안 닫는 계약은 test_편집_중_폴더를_펴도_수정은_남는다 가 지킨다."""
    open_panel(desktop)
    open_file(desktop, "README.md")
    desktop.click("#fp-view-close")                           # 수동 ✕
    desktop.wait_for_timeout(200)
    assert desktop.locator("#fp-body pre").count() == 0
    assert "wide" not in (desktop.get_attribute("#file-panel", "class") or "")
    open_file(desktop, "README.md")
    expand(desktop, "docs", "노트.md")                         # 다른 폴더로 나간다 → 자동으로 닫힘
    desktop.wait_for_timeout(200)
    assert desktop.locator("#fp-body pre").count() == 0


def test_새폴더는_루트와_폴더안_양쪽에_만든다(desktop, bridge):
    """머리의 [새 폴더] = 루트에, 폴더 우클릭 '새 폴더 만들기' = 그 폴더 안에.
    모달 없이 트리 인라인 입력줄로 — Enter 만들기, 중복이면 이유를 한 줄로, Esc 취소."""
    bridge.mkdirs.clear()
    open_panel(desktop)
    desktop.click("#fp-newdir")
    desktop.fill(".tree-row.mkdir input", "기획안")
    desktop.press(".tree-row.mkdir input", "Enter")
    desktop.wait_for_timeout(400)
    assert bridge.mkdirs == [{"project": "wiki", "path": "기획안"}]
    assert desktop.locator(".tree-row.mkdir").count() == 0    # 성공 — 입력줄이 걷힌다
    desktop.locator("#fp-tree li:has-text('docs')").click(button="right")
    desktop.click("#fp-menu button:has-text('새 폴더 만들기')")
    desktop.fill(".tree-row.mkdir input", "자료")
    desktop.press(".tree-row.mkdir input", "Enter")
    desktop.wait_for_timeout(400)
    assert bridge.mkdirs[-1] == {"project": "wiki", "path": "docs/자료"}
    desktop.click("#fp-newdir")                               # 중복 → 이유 한 줄, 입력줄은 남는다
    desktop.fill(".tree-row.mkdir input", "이미있음")
    desktop.press(".tree-row.mkdir input", "Enter")
    desktop.wait_for_timeout(400)
    assert "이미 있습니다" in desktop.text_content("#fp-note")
    desktop.press(".tree-row.mkdir input", "Escape")
    desktop.wait_for_timeout(200)
    assert desktop.locator(".tree-row.mkdir").count() == 0


def test_파일패널_글자크기_스텝퍼(desktop):
    """패널 머리의 −/+ 가 트리(폴더·파일명)·본문 글자를 함께 키운다 — 이 기기 설정에 남는다.
    ★ 반드시 트리 줄로 잰다 — 본문만 커지고 트리는 고정 13px 에 박혀 '버튼이 안 된다'로
    보인 실측 버그의 재발 방지."""
    open_panel(desktop)
    size = lambda: float(desktop.evaluate(
        "getComputedStyle(document.querySelector('#fp-tree .tree-row')).fontSize")[:-2])
    try:
        base = size()
        assert desktop.text_content("#fp-fs-val") == str(int(base))   # 숫자가 늘 보인다
        desktop.click("#fp-fs-plus")
        assert size() == base + 1
        # 1px 변화는 눈에 안 띈다 — 숫자가 즉시 바뀌는 것이 '눌렸다'는 피드백이다(실측)
        assert desktop.text_content("#fp-fs-val") == str(int(base + 1))
        assert '"fpFontSize":' in (desktop.evaluate("localStorage.getItem('frog.prefs')") or "")
        desktop.click("#fp-fs-minus")
        assert size() == base
    finally:
        desktop.evaluate("localStorage.removeItem('frog.prefs'); applyPrefs()")   # 공용 브라우저 원위치


def test_파일_편집_저장(desktop, bridge):
    bridge.writes.clear()
    open_panel(desktop)
    open_file(desktop, "README.md")
    desktop.click("#fp-edit")
    desktop.fill("#fp-body textarea", "# 수정본\n")
    desktop.click("#fp-save")
    desktop.wait_for_timeout(400)
    assert bridge.writes == [{"project": "wiki", "path": "README.md",
                              "content": "# 수정본\n", "baseMtimeNs": "1754377200111111117"}]
    assert "저장" in desktop.text_content("#fp-note")


def test_버전토큰은_문자열_그대로_돌아온다(desktop, bridge):
    """19자리 mtimeNs 를 숫자로 다루면 하위 자릿수가 뭉개져(…456789 → …456800) 저장이 영영 409 다.
    JS 가 한 번이라도 Number 로 바꿨다면 여기 되돌아온 값이 int 이거나 자릿수가 달라진다."""
    bridge.writes.clear()
    open_panel(desktop)
    desktop.click("#fp-body li:has-text('docs')")
    desktop.wait_for_selector("#fp-body li:has-text('노트.md')")
    open_file(desktop, "노트.md")
    desktop.click("#fp-edit")
    desktop.fill("#fp-body textarea", "메모2")
    desktop.click("#fp-save")
    desktop.wait_for_timeout(400)
    sent = bridge.writes[-1]["baseMtimeNs"]
    assert isinstance(sent, str)                       # 숫자로 보냈으면 파이썬엔 int 로 온다
    assert sent == "1754377200123456789"               # conftest MTIMES 와 한 자리도 다르지 않게
    assert "저장" in desktop.text_content("#fp-note")   # 서버가 받아들였다(400·409 가 아니다)


def test_저장_충돌은_고친_내용을_지우지_않는다(desktop):
    """409 = 다른 곳(클로드·에디터)에서 먼저 바뀜. 여기서 화면을 갈아치우면 방금 쓴 글이 사라진다."""
    open_panel(desktop)
    open_file(desktop, "README.md")
    desktop.click("#fp-edit")
    desktop.fill("#fp-body textarea", "손으로 오래 쓴 글")
    desktop.route("**/api/file", lambda route, request: route.fulfill(
        status=409, content_type="application/json", body='{"error":"Conflict"}')
        if request.method == "PUT" else route.continue_())
    desktop.click("#fp-save")
    desktop.wait_for_timeout(400)
    assert "먼저 바뀌" in desktop.text_content("#fp-note")
    assert desktop.input_value("#fp-body textarea") == "손으로 오래 쓴 글"   # 내 글은 그대로 — 복사 가능
    assert desktop.is_visible("#fp-note-act")            # 덮어쓸지 말지는 사용자가 고른다
    desktop.unroute("**/api/file")


def test_편집_중_이탈은_한번_막고_묻는다(desktop):
    """md 를 고치다 다른 폴더를 누르면 고친 내용이 말없이 사라지던 자리 — 모달 없이 한 줄로 막는다."""
    open_panel(desktop)
    open_file(desktop, "README.md")
    desktop.click("#fp-edit")
    desktop.fill("#fp-body textarea", "아직 저장 안 한 글")
    desktop.click("#fp-crumb button")                    # 프로젝트 루트로 = 편집 중 이탈
    desktop.wait_for_timeout(200)
    assert desktop.is_visible("#fp-body textarea")        # 아직 편집 중 — 사라지지 않았다
    assert "저장하지 않은" in desktop.text_content("#fp-note")
    desktop.click("#fp-note-act")                        # [수정 버리고 이동] — 두 번째 누름은 통과
    desktop.wait_for_selector("#fp-body li")
    assert desktop.locator("#fp-body textarea").count() == 0


def test_세션을_갈아타면_패널도_그_프로젝트로(desktop):
    """엉뚱한 프로젝트 파일을 그 프로젝트 것으로 착각하는 게 이 앱이 막으려는 사고다."""
    open_panel(desktop, 1)                               # wiki
    desktop.click("#fp-body li:has-text('docs')")
    desktop.wait_for_selector("#fp-body li:has-text('노트.md')")
    desktop.click("#sessions li:nth-child(2) b")           # mall 로 갈아탄다
    desktop.wait_for_timeout(500)
    assert desktop.text_content("#fp-crumb").startswith("mall")
    assert desktop.is_visible("#fp-body li:has-text('mall.md')")     # 새 프로젝트의 루트 목록
    assert desktop.locator("#fp-body li:has-text('wiki.md')").count() == 0   # 옛 트리는 남지 않는다


def test_늦게_온_파일_응답은_바뀐_프로젝트를_덮지_않는다(desktop, bridge):
    """파일을 연 직후(응답이 늦는 사이) 세션을 갈아타면 늦게 도착한 옛 응답이 마지막에 이겨,
    'mall' 이라 써 놓은 패널에 wiki 파일 내용이 그려졌다 — 이 앱이 막으려던 바로 그 사고다.
    화면이 스스로 회복되지 않아 사용자가 그대로 고쳐 저장할 시간은 얼마든지 있고, 저장은 409 로
    한 번 막히지만 그때 뜨는 안내가 '다른 곳에서 먼저 바뀌었습니다'라는 거짓 설명이라
    그 옆 [덮어쓰기] 한 번이면 mall/README.md 가 wiki 내용으로 대체된다."""
    open_panel(desktop, 1)                                   # wiki
    with bridge.slow_files("wiki", 1.2):                     # wiki 파일 응답만 늦춘다
        desktop.click("#fp-body li:has-text('README.md')")   # 여는 요청 — 답은 1.2초 뒤에 온다
        desktop.click("#sessions li:nth-child(2) b")           # 그 사이 mall 로 갈아탄다
        desktop.wait_for_timeout(2500)                       # 늦은 응답이 도착하고도 남을 시간
    crumb, body = desktop.text_content("#fp-crumb"), desktop.text_content("#fp-body")
    assert crumb.startswith("mall")
    assert "wiki" not in crumb and "wiki" not in body        # 두 프로젝트가 한 화면에 섞이지 않는다
    assert desktop.is_visible("#fp-body li:has-text('mall.md')")     # mall 루트 목록 그대로
    assert desktop.locator("#fp-body pre").count() == 0      # 늦게 온 파일이 목록을 갈아치우지 않았다


def test_늦게_온_저장_응답은_바뀐_프로젝트를_건드리지_않는다(desktop, bridge):
    """저장 응답도 await 뒤에 화면을 만진다 — 고친 것 없이 저장하면 곧바로 이탈이 가능하므로,
    저장을 누르자마자 세션을 갈아타면 늦게 온 응답이 이미 닫힌 파일을 다시 열려다 거짓 오류를
    띄웠다. 409 로 왔다면 더 나쁘다: [덮어쓰기] 가 새 프로젝트의 파일을 옛 내용으로 덮으러 간다."""
    bridge.writes.clear()
    open_panel(desktop, 1)                                   # wiki
    open_file(desktop, "README.md")
    desktop.click("#fp-edit")
    with bridge.slow_files("wiki", 1.2):
        desktop.click("#fp-save")                            # 고친 것 없는 저장 = 이탈이 막히지 않는다
        desktop.click("#sessions li:nth-child(2) b")           # mall 로 갈아탄다
        desktop.wait_for_timeout(2500)
    assert bridge.writes[-1]["project"] == "wiki"            # 저장 자체는 wiki 로 제대로 나갔다
    assert desktop.text_content("#fp-note") == ""            # 새 프로젝트 화면에 거짓 오류가 없다
    assert desktop.text_content("#fp-crumb").startswith("mall")
    assert desktop.is_visible("#fp-body li:has-text('mall.md')")


def test_편집_중_세션을_갈아타도_수정은_남는다(desktop):
    """고치던 글을 놔둔 채 사이드바에서 다른 프로젝트를 누르는 길 — 이 앱에서 가장 위험한 자리다.

    패널은 '지금 그 프로젝트'를 따라가야 하지만(위 테스트), 따라가면서 편집 중인 내용을 그냥
    지워 버리면 사용자가 쓰던 글이 말없이 사라진다. 터미널만 옮기고 패널은 그 자리에 세워 둔 뒤
    버릴지 말지를 되묻는 것이 계약이다."""
    open_panel(desktop, 1)                               # wiki
    open_file(desktop, "README.md")
    desktop.click("#fp-edit")
    desktop.fill("#fp-body textarea", "폰으로 오래 쓴 글")
    desktop.click("#sessions li:nth-child(2) b")           # mall 로 갈아탄다
    desktop.wait_for_timeout(500)
    assert desktop.input_value("#fp-body textarea") == "폰으로 오래 쓴 글"   # 내 글은 그대로
    assert "저장 안 한 수정" in desktop.text_content("#fp-note")   # 왜 안 따라갔는지 화면에 쓴다
    assert desktop.text_content("#fp-crumb").startswith("wiki")   # 아직 wiki 것을 보고 있다
    assert desktop.text_content("#hdr-name") == "mall"            # 터미널은 막지 않는다
    desktop.click("#fp-note-act")                        # [수정 버리고 나가기] — 두 번째 누름은 통과
    desktop.wait_for_selector("#fp-body li")
    assert desktop.text_content("#fp-crumb").startswith("mall")
    assert desktop.locator("#fp-body textarea").count() == 0


def test_인박스는_팝업이라_터미널이_그대로_남는다(desktop):
    """인박스는 화면 교대가 아니라 오른쪽 팝업(터미널 전체를 가리지 않는다).
    그래서 터미널 헤더(📁 포함)와 파일 패널은 인박스가 떠 있어도 그대로 살아 있고,
    ✕ 로 닫으면 아무 일도 없었던 듯 이어서 일한다."""
    open_panel(desktop)
    desktop.click("#sum-pending")
    desktop.wait_for_selector(".inbox-card")
    assert desktop.is_visible("#term-view")                # 터미널은 뒤에 그대로
    assert desktop.is_visible("#files-toggle")
    assert desktop.text_content("#fp-crumb").startswith("wiki")
    desktop.click("#inbox-back")                           # ✕ 닫기
    desktop.wait_for_timeout(200)
    assert not desktop.is_visible("#inbox-view")
    assert desktop.is_visible("#term-view")


def test_폰에서_파일패널은_전체화면(phone):
    open_panel(phone)
    box = phone.locator("#file-panel").bounding_box()
    assert box["y"] <= 0 and box["width"] >= 400          # 화면을 통째로 덮는다(반쪽 패널 금지)
    assert phone.locator("#fp-body li").first.bounding_box()["height"] >= 44   # 손가락 탭 크기
    phone.click("#fp-close")
    assert not phone.is_visible("#file-panel")


# ---------- 파일 트리 (VS Code 식 접기·펴기) ----------
# 폴더를 누르면 그 안으로 '들어가던' 화면(빵부스러기로 되돌아오는 드릴다운)을 걷어낸 자리다.
# 여기서 지키는 것: 부모가 남은 채 자식이 그 줄 아래에 끼어들고, 여러 층·여러 폴더가 한 화면에
# 동시에 펼쳐져 있고, 폴더 하나를 못 읽어도 트리 전체가 죽지 않는다.

def test_트리_폴더는_그_자리에서_펴지고_다시_접힌다(desktop):
    """드릴다운이었다면 'docs' 줄 자체가 사라지고 자식만 남는다 — 그걸 여기서 가른다."""
    open_panel(desktop)                                       # wiki
    assert names(desktop) == ["docs", "src", "잠긴폴더", "README.md", "wiki.md"]
    assert tree_rows(desktop)[0]["chev"] == "▸"               # 접혀 있다는 표시

    expand(desktop, "docs", "노트.md")
    rows = tree_rows(desktop)
    assert [r["name"] for r in rows] == \
        ["docs", "설계", "노트.md", "src", "잠긴폴더", "README.md", "wiki.md"]
    assert rows[0]["chev"] == "▾"                             # 펴졌다는 표시
    assert (rows[0]["depth"], rows[1]["depth"], rows[3]["depth"]) == ("0", "1", "0")

    desktop.click("#fp-tree li:has-text('docs')")             # 같은 줄을 다시 누르면 접힌다
    desktop.wait_for_timeout(200)
    assert names(desktop) == ["docs", "src", "잠긴폴더", "README.md", "wiki.md"]


def test_트리_두_폴더를_동시에_펴_둔다(desktop):
    """한 번에 한 층만 보이던 것이 이 화면이 고치려는 불편이다 — 세 층이 함께 보여야 한다."""
    open_panel(desktop)
    expand(desktop, "docs", "노트.md")
    expand(desktop, "설계", "최종본")                          # docs 안쪽을 한 층 더
    expand(desktop, "src", "app.js")                          # 옆 폴더도 함께 편 채로
    rows = tree_rows(desktop)
    assert [r["name"] for r in rows][:5] == ["docs", "설계", LONG_DOC, "노트.md", "src"]
    assert [r["depth"] for r in rows][:5] == ["0", "1", "2", "1", "0"]
    assert [r["name"] for r in rows][5:] == ["app.js", "잠긴폴더", "README.md", "wiki.md"]

    # --depth 를 심기만 하고 CSS 가 못 받으면 화면은 평평하다 — 실제 들여쓰기(px)로 확인한다.
    pads = desktop.eval_on_selector_all(
        "#fp-tree li", "els => els.map(e => parseFloat(getComputedStyle(e).paddingLeft))")
    assert pads[0] < pads[1] < pads[2]                        # 깊어질수록 오른쪽으로


def test_트리_못_읽는_폴더는_그_줄만_알린다(desktop):
    """권한·삭제로 폴더 하나를 못 읽어도 트리 전체가 죽으면 안 된다(그 줄만 알린다)."""
    open_panel(desktop)
    desktop.click("#fp-tree li:has-text('잠긴폴더')")
    desktop.wait_for_selector("#fp-tree .tree-err")
    assert names(desktop)[-2:] == ["README.md", "wiki.md"]    # 나머지 줄은 그대로 살아 있다
    expand(desktop, "docs", "노트.md")                         # 다른 폴더는 아무 일 없이 펴진다
    assert "설계" in names(desktop)


def test_트리_접었다_펴도_다시_받지_않는다(desktop):
    """이미 받아 둔 자식은 캐시다 — 접었다 펼 때마다 다시 받으면 폰에서 눈에 띄게 굼뜨다."""
    open_panel(desktop)
    calls = []
    desktop.on("request", lambda r: calls.append(r.url) if "/api/files" in r.url else None)
    expand(desktop, "docs", "노트.md")
    assert len(calls) == 1
    desktop.click("#fp-tree li:has-text('docs')")             # 접고
    desktop.wait_for_timeout(200)
    expand(desktop, "docs", "노트.md")                         # 다시 편다 — 요청은 늘지 않는다
    assert len(calls) == 1


def test_트리는_프로젝트를_갈아타면_통째로_버린다(desktop):
    """캐시를 프로젝트별로 버리지 않으면 mall 트리에 wiki 의 자식이 그대로 붙는다."""
    open_panel(desktop, 1)                                    # wiki
    expand(desktop, "docs", "노트.md")
    desktop.click("#sessions li:nth-child(2) b")                # mall 로 갈아탄다
    desktop.wait_for_timeout(600)
    assert names(desktop) == ["docs", "src", "잠긴폴더", "README.md", "mall.md"]   # 펴 둔 것도 접힌다
    calls = []
    desktop.on("request", lambda r: calls.append(r.url) if "/api/files" in r.url else None)
    expand(desktop, "docs", "노트.md")
    assert any("project=mall" in u for u in calls)            # 옛 프로젝트 자식을 재활용하지 않는다


def test_늦게_온_폴더_응답은_바뀐_프로젝트_트리를_덮지_않는다(desktop, bridge):
    """트리는 폴더마다 따로 요청한다 — 펼치는 중에 세션을 갈아타면 늦게 온 wiki 의 자식이
    mall 트리에 끼어들 수 있다. 세대(fpGen)가 그것을 버리는지 실제 지연으로 확인한다."""
    open_panel(desktop, 1)                                    # wiki
    with bridge.slow_files("wiki", 1.2):                      # wiki 응답만 늦춘다
        desktop.click("#fp-tree li:has-text('docs')")         # 펴는 요청 — 답은 1.2초 뒤
        desktop.click("#sessions li:nth-child(2) b")            # 그 사이 mall 로 갈아탄다
        desktop.wait_for_timeout(2500)                        # 늦은 응답이 도착하고도 남을 시간
    assert desktop.text_content("#fp-crumb").startswith("mall")
    assert names(desktop) == ["docs", "src", "잠긴폴더", "README.md", "mall.md"]
    assert "설계" not in desktop.text_content("#fp-body")      # wiki 의 자식이 새 트리에 끼지 않았다


def test_파일을_열어도_트리는_남는다(desktop):
    """파일을 고르면 패널이 넓어지며 문서가 보인다. 그러면서도 트리는 사라지지 않는다 —
    문서 하나 보려고 폴더를 처음부터 다시 펴야 하면 트리를 만든 뜻이 없다."""
    open_panel(desktop)
    expand(desktop, "docs", "노트.md")
    desktop.click("#fp-tree li:has-text('노트.md')")
    desktop.wait_for_selector("#fp-body pre")
    assert desktop.text_content("#fp-body pre") == "wiki 메모"
    assert "wide" in (desktop.get_attribute("#file-panel", "class") or "")     # 넓어진다
    assert names(desktop)[:3] == ["docs", "설계", "노트.md"]                    # 펴 둔 트리 그대로
    sel = desktop.locator("#fp-tree li.sel")
    assert sel.count() == 1 and "노트.md" in sel.text_content()                 # 어느 파일인지 표시
    desktop.click("#fp-crumb button")                        # 프로젝트 이름 = 파일 닫기
    desktop.wait_for_timeout(200)
    assert desktop.locator("#fp-body pre").count() == 0
    assert "wide" not in (desktop.get_attribute("#file-panel", "class") or "")
    assert names(desktop)[:3] == ["docs", "설계", "노트.md"]                    # 펴 둔 자리는 그대로


def test_편집_중_폴더를_펴도_수정은_남는다(desktop):
    """트리는 아래 칸(본문·편집기)을 건드리지 않는다. 한 칸에 같이 그리면 폴더 하나 펼 때마다
    저장 안 한 수정이 말없이 사라진다 — 이 앱이 가장 무서워하는 사고다."""
    open_panel(desktop)
    open_file(desktop, "README.md")
    desktop.click("#fp-edit")
    desktop.fill("#fp-body textarea", "쓰다 만 글")
    expand(desktop, "docs", "노트.md")                        # 편집 중에 폴더를 편다
    assert desktop.input_value("#fp-body textarea") == "쓰다 만 글"   # 그대로 있다
    assert desktop.text_content("#fp-note") == ""             # 되묻지도 않는다(버리는 게 없으니까)


def test_폰_트리는_깊어져도_옆으로_넘치지_않는다(phone):
    """깊이만큼 들여쓰면 좁은 폰에서 이름 자리가 줄어든다 — 가로 스크롤이 생기는 대신 … 로 잘려야 한다."""
    open_panel(phone)
    expand(phone, "docs", "노트.md")
    expand(phone, "설계", "최종본")
    row = phone.locator("#fp-tree li:has-text('최종본')")
    panel = phone.locator("#fp-body").bounding_box()
    assert row.bounding_box()["width"] <= panel["width"] + 1
    assert phone.evaluate("(b => b.scrollWidth <= b.clientWidth)(document.getElementById('fp-body'))")
    assert row.bounding_box()["height"] >= 44                 # 깊은 줄도 손가락 탭 크기


# ---------- 카드 대화 맥락 ----------
# 판단하려면 이 화면에서 무슨 내용인지 보여야 하고, 클로드가 무엇을 요청했는지
# 그 대화 내역이 보여야 한다. 훅 원문(message)은 "Claude is waiting for your input" 이라
# 아무것도 알려주지 않는다. 브릿지가 대화에서 뽑아 주는 asked(내가 시킨 것)·said(클로드가 한 말)로
# 그 자리를 채우는 것이 이 절의 계약이다.

def test_카드는_기계문구_대신_대화_맥락을_보여준다(desktop):
    """카드만 보고 '아 이거 하다 멈췄구나'를 알 수 있어야 한다."""
    open_inbox(desktop)
    text = desktop.locator(".inbox-card.dead").text_content()      # gone — 훅 원문만 있던 카드
    assert "Claude is waiting for your input" not in text          # 기계 문구는 쓰지 않는다
    assert "브릿지 재기동하고 로그 확인해줘" in text                # ① 내가 시킨 것
    assert "포트가 안 열립니다" in text                            # ② 클로드가 한 말 = 진짜 질문
    assert text.index("브릿지 재기동") < text.index("포트가 안 열립니다")   # 시간순(시킨 것 → 답)


def test_피커_카드는_같은_질문을_두_번_보여주지_않는다(desktop):
    """피커에는 화면에서 긁은 question 이 이미 있다 — 대화의 said 가 같은 문장이면 한 카드에
    같은 말이 두 번 뜬다(둘 다 400자에서 잘렸을 수 있어 앞머리로 견준다)."""
    card = open_inbox(desktop).nth(0)                              # wiki 피커
    text = card.text_content()
    assert text.count("이 파일을 지울까요?") == 1
    assert "낡은 초안 파일들 정리하고 커밋해" in text              # 무슨 일을 하다 멈췄는지는 남는다


def test_맥락이_빈_카드도_화면이_멀쩡하다(desktop):
    """/clear 직후처럼 대화가 없는 카드는 asked·said 가 둘 다 빈 문자열로 온다."""
    card = open_inbox(desktop).nth(2)                              # frog 완료 — 맥락 없음
    text = card.text_content()
    assert "frog" in text
    assert "undefined" not in text and "🙋" not in text            # 빈 줄·undefined 가 새지 않는다
    assert card.locator("textarea").is_enabled()                   # 다음 지시를 넣는 자리는 그대로


def test_긴_맥락은_대시보드에서_접혀_있다가_펴진다(desktop):
    """400자를 카드에 다 펼치면 대시보드가 화면을 통째로 먹는다. 모달·alert 금지라 그 자리에서 편다."""
    long_say = "이렇게 고치면 " + "가" * 380 + " 진행할까요?"
    card = {"sessionId": "wiki-8", "project": "wiki", "cwd": "/tmp/wiki", "machine": "mac",
            "kind": "permission", "message": "Claude needs your permission", "live": True,
            "updatedAt": "2026-08-05T16:00:00+09:00",
            "asked": "리팩터링 해줘", "said": long_say}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"decisions": [card]})))
    desktop.evaluate("loadDash()")
    desktop.wait_for_selector(".dash-card")
    box = desktop.locator(".dash-card").first
    assert "…" in box.locator(".dash-q").first.text_content()      # 앞머리만 보여준다
    full = box.locator("details p")
    assert not full.first.is_visible()                             # 나머지는 접혀 있다
    box.locator("details summary").click()
    desktop.wait_for_timeout(200)
    assert full.first.is_visible()                                 # 눌러서 그 자리에서 편다
    assert long_say[-20:] in full.first.text_content() or long_say[-20:] in full.last.text_content()


# ---------- 설정 화면 ----------

def test_설정에서_시작_에이전트를_고른다(desktop, bridge):
    """⚙️ → 시작 에이전트(클로드·코덱스·안티·셸) 선택이 서버로 저장되고, 연결 상태가 읽힌다."""
    bridge.agent_posts.clear()
    bridge.agent = "claude"
    try:
        desktop.click("#btn-settings")
        desktop.wait_for_selector("#set-agent .set-choice")
        assert desktop.is_visible("#settings-view")
        assert desktop.is_visible("#sessions")           # 사이드바는 남는다 (데스크톱 3열)
        choices = desktop.locator("#set-agent .set-choice")      # 다른 무리(테마·글꼴)와 섞이지 않게
        assert choices.count() == 4                              # 클로드·코덱스·안티그래비티·셸만
        assert choices.nth(2).text_content().startswith("안티그래비티")
        assert "on" in choices.nth(0).get_attribute("class")     # 현재 설정 = 클로드
        choices.nth(1).click()                                   # 코덱스로 바꾼다
        desktop.wait_for_timeout(400)
        assert bridge.agent_posts == [{"agent": "codex"}]
        assert "on" in desktop.locator("#set-agent .set-choice").nth(1).get_attribute("class")
        body = desktop.text_content("#settings-body")
        assert "test@example.com" in body                        # 클로드 — 누구로 로그인돼 있나
        assert "codex login" in body                             # 코덱스 — 로그인 필요 안내
    finally:
        bridge.agent = "claude"                # 세션 공용 픽스처 — 다음 테스트로 새지 않게
    desktop.click("#settings-back" if desktop.is_visible("#settings-back") else "#btn-settings")
    desktop.evaluate("showList()")             # 화면도 원위치 — 다른 테스트의 시작 상태를 지킨다


def test_에이전트_새버전배지와_업데이트뒤_재시작안내(desktop, bridge):
    bridge.agent_updates.clear()
    desktop.click("#btn-settings")
    desktop.wait_for_selector("#set-status .set-update-btn")
    body = desktop.text_content("#set-status")
    assert "최신" in body and "새 버전 9.9.10" in body
    assert "자동 업데이트 켜짐 · 최근 8/14 성공" in body
    assert "새 버전은 알려주지만 설치는 눌러야 합니다" in body
    desktop.locator("#set-status .set-row").nth(1).locator(".set-update-btn").click()
    desktop.wait_for_function("() => document.querySelector('#set-note').textContent.includes('새 버전이 설치됐습니다')")
    assert bridge.agent_updates == ["codex"]
    note = desktop.text_content("#set-note")
    assert "지금 열려 있는 터미널은 끄고 다시 켜야 새 버전으로 돕니다" in note
    assert "지금 켜져 있는 세션 8개" in note
    desktop.wait_for_function(
        "() => document.querySelector('#set-update-result-codex').textContent.includes('테스트 출력')")
    desktop.evaluate("showList()")


def test_설정_바이패스_토글과_실행명령이_저장된다(desktop, bridge):
    """실행 별칭은 [저장] 버튼과 Enter 둘 다 서버로 보내고 새 셸 안내를 보인다."""
    bridge.agent_posts.clear()
    try:
        desktop.click("#btn-settings")
        desktop.wait_for_selector("#set-flags .set-choice")
        flags = desktop.locator("#set-flags .set-choice")
        # 순서 = 바이패스 · 이어서 시작 · RC (web/settings.js renderFlags)
        assert "on" in flags.nth(0).get_attribute("class")       # bypass 기본 켬
        assert "on" in flags.nth(1).get_attribute("class")       # 이어서 시작 기본 켬
        assert "on" not in (flags.nth(2).get_attribute("class") or "").split()  # RC 기본 꺼짐
        flags.nth(2).click()                                     # RC 켠다
        desktop.wait_for_timeout(400)
        assert {"rc": True} in bridge.agent_posts
        cmds = desktop.locator("#set-cmds input")
        cmds.nth(0).fill("ca")                                  # 클로드 실행 별칭
        cmds.nth(1).fill("cx")                                  # 코덱스 실행 별칭
        assert desktop.is_visible("#set-cmds .set-cmd-save")
        desktop.click("#set-cmds .set-cmd-save")                # 명시적 저장 버튼
        desktop.wait_for_timeout(400)
        assert {"commands": {"claude": "ca", "codex": "cx"}} in bridge.agent_posts
        assert "새로 여는 터미널부터 ca · cx로 시작" in desktop.text_content("#set-note")
        cmds = desktop.locator("#set-cmds input")                # 저장 뒤 서버 값으로 다시 렌더됨
        cmds.nth(0).fill("cl")
        cmds.nth(0).press("Enter")                              # 기존 Enter 저장 습관도 유지
        desktop.wait_for_timeout(400)
        assert {"commands": {"claude": "cl", "codex": "cx"}} in bridge.agent_posts
        cmds = desktop.locator("#set-cmds input")
        cmds.nth(0).fill("rm")                                  # 기존 중요 명령 덮어쓰기는 서버가 거부
        desktop.click("#set-cmds .set-cmd-save")
        desktop.wait_for_timeout(400)
        assert "별칭을 저장하지 않았습니다" in desktop.text_content("#set-note")
        assert desktop.locator("#set-cmds input").nth(0).input_value() == "cl"  # 서버값으로 복구
    finally:
        bridge.flags.update(bypass=True, cont=True, rc=False)    # 세션 공용 픽스처 원위치
        bridge.commands.update(claude="", codex="")
    desktop.evaluate("showList()")


def test_설정에서_프로젝트_루트를_저장하고_실패사유를_보여준다(desktop, bridge):
    bridge.agent_posts.clear()
    bridge.projects_root = "/tmp/projects"
    try:
        desktop.click("#btn-settings")
        desktop.wait_for_selector("#set-root input")
        root = desktop.locator("#set-root input")
        assert root.input_value() == "/tmp/projects"
        root.fill("/tmp/new-workspace")
        desktop.click("#set-root .set-cmd-save")
        desktop.wait_for_timeout(600)
        assert {"projectsRoot": "/tmp/new-workspace"} in bridge.agent_posts
        assert "사이드바를 새 작업공간으로 바꿨습니다" in desktop.text_content("#set-note")
        root = desktop.locator("#set-root input")
        root.fill("/없는/폴더")
        root.press("Enter")
        desktop.wait_for_timeout(600)
        assert "폴더가 존재하지 않습니다" in desktop.text_content("#set-note")
        assert desktop.locator("#set-root input").input_value() == "/tmp/new-workspace"
        assert "살아 있는 tmux 세션은 종료되지 않습니다" in desktop.text_content("#settings-body")
    finally:
        bridge.projects_root = "/tmp/projects"
    desktop.evaluate("showList()")


def test_설정_화이트_테마는_이_기기에_남는다(desktop):
    """테마·글자 설정은 기기(localStorage) 몫 — 폰과 맥이 서로 다른 걸 원한다."""
    desktop.click("#btn-settings")
    desktop.wait_for_selector("#set-ui .set-choice")
    desktop.locator("#set-ui .set-choice").nth(2).click()        # 라이트 (개구리 다크·One Dark·라이트)
    desktop.wait_for_timeout(200)
    assert desktop.evaluate("document.documentElement.classList.contains('light')")
    assert '"ui":"light"' in (desktop.evaluate("localStorage.getItem('frog.prefs')") or "")
    desktop.locator("#set-ui .set-choice").nth(0).click()        # 다크로 되돌린다(공용 브라우저)
    desktop.wait_for_timeout(200)
    assert not desktop.evaluate("document.documentElement.classList.contains('light')")
    desktop.evaluate("showList()")


def test_테마는_터미널과_패널이_한_세트로_바뀐다(desktop):
    """테마에서 One Dark 를 고르면 패널(root 클래스)과 터미널 색(termTheme)이 같이 One Dark 가
    된다 — 3세트(개구리 다크·One Dark·라이트) 일치."""
    desktop.click("#btn-settings")
    desktop.wait_for_selector("#set-ui .set-choice")
    labels = desktop.locator("#set-ui .set-choice").all_text_contents()
    assert labels == ["개구리 다크", "One Dark", "라이트"]
    try:
        desktop.locator("#set-ui .set-choice").nth(1).click()    # One Dark
        desktop.wait_for_timeout(200)
        assert desktop.evaluate("document.documentElement.classList.contains('onedark')")
        prefs = desktop.evaluate("localStorage.getItem('frog.prefs')") or ""
        assert '"ui":"onedark"' in prefs and '"termTheme":"onedark"' in prefs
        desktop.locator("#set-ui .set-choice").nth(0).click()    # 개구리 다크 → 터미널도 frog 세트로
        desktop.wait_for_timeout(200)
        assert not desktop.evaluate("document.documentElement.classList.contains('onedark')")
        assert '"termTheme":"frog"' in (desktop.evaluate("localStorage.getItem('frog.prefs')") or "")
    finally:
        desktop.evaluate("localStorage.removeItem('frog.prefs'); applyPrefs()")   # 공용 브라우저 원위치
        desktop.evaluate("showList()")


def test_하단_상태바가_요약과_사용량을_말한다(desktop):
    """Orca 차용 — 연결·세션·결재 + 클로드 계정·사용량(80% 넘으면 경고색)."""
    desktop.wait_for_selector("#statusbar")
    desktop.evaluate("refreshSummary(); refreshStatusMeta()")
    desktop.wait_for_timeout(600)
    assert "연결됨" in desktop.text_content("#sb-conn")
    assert "세션" in desktop.text_content("#sb-counts")
    usage = desktop.locator("#sb-usage")
    assert "5h 38%" in usage.text_content() and "7d 85%" in usage.text_content()
    assert "warn" in (usage.get_attribute("class") or "")        # 85% — 경고
    assert "test@example.com" in desktop.text_content("#sb-account")


def test_파일트리_우클릭으로_맥에서_연다(desktop, bridge):
    """PDF·HTML·큰 이미지·SVG·HEIC는 우클릭 → 프로그램으로 열기(맥의 앱)가 남은 길이다."""
    bridge.opens.clear()
    open_panel(desktop)                                          # wiki 파일 패널
    row = desktop.locator(".tree-row", has_text="README.md").first
    row.click(button="right")
    desktop.wait_for_selector("#fp-menu button")
    labels = desktop.locator("#fp-menu button").all_text_contents()
    assert labels == ["기본 앱으로 열기", "미리보기로 열기", "크롬으로 열기", "파인더에서 보기"]
    desktop.locator("#fp-menu button", has_text="크롬으로 열기").click()
    desktop.wait_for_timeout(400)
    assert bridge.opens == [{"project": "wiki", "path": "README.md", "app": "chrome"}]
    assert desktop.locator("#fp-menu").is_hidden()               # 고르면 메뉴는 접힌다
    desktop.evaluate("fpClose(); showList()")


def test_파일트리_드래그를_터미널이_경로_입력으로_받는다(desktop, bridge):
    """VS Code·Zed 차용 — 트리에서 터미널로 끌면 그 경로가 입력된다(실행은 안 한다)."""
    open_terminal(desktop)                                       # wiki 터미널
    wait_ws(desktop, bridge, 1)
    bridge.received.clear()
    desktop.evaluate("""() => {
      const dt = new DataTransfer();
      dt.setData("application/x-frog-path", "/tmp/wiki/docs/노트.md");
      document.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }""")
    desktop.wait_for_timeout(400)
    assert bytes(bridge.received).decode() == "'/tmp/wiki/docs/노트.md' "   # 경로만 — 엔터 없음
    desktop.evaluate("closeTerminal(); showList()")


# ---------- 상단 결재 대시보드(피커) ----------
# 지금 결정해야 할 것이 있으면 '결재 대기' 글자만 띄우지 않고 상단 대시보드에 선택 버튼(피커)으로
# 보여 준다 — 인박스까지 들어가지 않고 상단에서 바로 고른다.

def dash_card(page, nth=0):
    page.wait_for_selector(".dash-card")
    return page.locator(".dash-card").nth(nth)


def test_대시보드_선택지_버튼이_그_세션으로_답을_쏜다(desktop, bridge):
    bridge.answers.clear()
    card = dash_card(desktop)
    assert "wiki" in card.text_content()
    assert "이 파일을 지울까요?" in card.text_content()             # 무엇을 고르는지 함께 보인다
    card.locator(".dash-opt").first.click()
    desktop.wait_for_timeout(400)
    assert bridge.answers == [{"project": "wiki", "sessionId": "wiki-1",
                               "type": "option", "value": "1"}]


IDLE_CARD = {"sessionId": "mall-9", "project": "mall", "cwd": "/tmp/mall", "machine": "mac",
             "kind": "idle", "message": "Claude is waiting for your input", "live": True,
             "updatedAt": "2026-08-05T17:00:00+09:00",
             "asked": "커밋해줘", "said": "커밋했습니다"}


def test_놀고있는_세션은_빨간불을_켜지_않는다(desktop):
    """★ 이미 끝난 이전 결재가 '결재 대기'로 계속 남아 있던 문제(2026-08-14).

    그 카드는 할 말을 마치고 빈 프롬프트에서 쉬는 세션(idle)이었다 — 결정할 거리가 없는데도
    빨간불을 켜서, 정작 답해야 할 결재가 그 안에 묻혔다. 대시보드에는 '답이 없으면 클로드가
    한 발짝도 못 나가는 것'만 올린다. 그런 카드가 갈 곳은 인박스의 '다음 지시 대기' 쪽이다.
    """
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"decisions": [IDLE_CARD]})))
    desktop.evaluate("loadDash()")
    desktop.wait_for_timeout(300)
    assert desktop.locator(".dash-card").count() == 0
    assert desktop.is_hidden("#dash")                              # 결정할 것이 없으면 자리를 비운다
    # 인박스에서는 사라지지 않는다 — 다음 지시를 넣는 자리로 남는다
    cards = open_inbox(desktop)
    assert cards.count() == 1
    heads = desktop.locator("#inbox-cards h3")
    assert heads.first.text_content() == "결재 필요 (0)"
    assert "완료 — 다음 지시 대기 (1)" in heads.nth(1).text_content()
    assert not cards.first.locator("textarea").is_disabled()       # 지시를 바로 넣을 수 있다


def test_tmux밖_세션_카드는_잠기고_이유를_적는다(desktop, bridge):
    """★ 살아있는데 답은 못 보내는 카드(canAnswer=False) — 방어선 확인.

    서버가 이제 tmux 밖 세션 카드를 아예 안 보내지만(inTmux 필터), 화면은 이 필드를 계속
    존중해야 한다 — 서버·훅이 어긋나는 순간에도 눌러 보고서야 404 를 읽는 일이 없게.
    입력창·버튼은 아예 그리지 않고(잠금 규칙과 동일) [터미널 열기]만 남긴다.
    """
    bridge.answers.clear()
    card = {"sessionId": "zed-1", "project": "mall", "cwd": "/tmp/mall", "machine": "mac",
            "kind": "permission", "message": "Claude needs your permission",
            "updatedAt": "2026-08-05T18:00:00+09:00", "live": True, "canAnswer": False,
            "asked": "배포해줘", "said": "배포 명령을 실행할까요?"}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"decisions": [card]})))
    desktop.evaluate("loadDash()")
    box = dash_card(desktop)
    assert "tmux 밖" in box.text_content()                          # 왜 못 누르는지 화면에 쓴다
    assert "세션 꺼짐" not in box.text_content()                    # 꺼진 것과는 다른 상태다
    assert box.locator(".dash-ask").count() == 0                    # 가짜 활성 입력 금지 — 아예 없다
    assert box.locator(".dash-send").count() == 0
    assert box.locator(".dash-term").is_enabled()                   # 갈 길은 남겨 둔다
    assert bridge.answers == []


def test_대시보드_숫자는_상단_헤드라인과_같다(desktop):
    """살아 있는 것만 세는 헤드라인('결재 대기 N건')과 다른 수를 말하면, 정확한 수를 알려주려고
    있는 화면이 사용자에게 '어느 쪽을 믿어야 하나'를 떠넘긴다."""
    dash_card(desktop)
    assert desktop.text_content("#sum-pending").startswith("결재 대기 1건")
    head = desktop.text_content("#dash-toggle")
    assert "결재 대기 1건" in head
    assert "세션 꺼짐 1건" in head                                 # 답 못 보내는 카드는 따로 적는다


def test_대시보드_죽은_세션은_잠긴다(desktop, bridge):
    """live=False = tmux 세션이 죽은 유령 카드 — 답을 보내면 404 다. 입력창·버튼을 아예
    그리지 않고(인박스와 같은 규칙 — 디자이너 지적) [터미널]만 살린다."""
    bridge.answers.clear()
    dead = desktop.locator(".dash-card.dead")
    desktop.wait_for_selector(".dash-card.dead")
    assert dead.count() == 1
    assert "세션 꺼짐" in dead.text_content()                      # 왜 못 누르는지 화면에 쓴다
    assert dead.locator(".dash-ask").count() == 0                  # 가짜 활성 입력 금지 — 아예 없다
    assert dead.locator(".dash-send").count() == 0
    assert dead.locator(".dash-opt").count() == 0
    assert dead.locator(".dash-term").is_enabled()


def test_대시보드도_열번째_이상은_버튼이_아니다(desktop, bridge):
    """tmux send-keys "12" 는 '1','2' 두 키를 보낸다 — 1번이 눌리고 '2' 가 다음으로 샌다(실증)."""
    bridge.answers.clear()
    many = {"sessionId": "wiki-9", "project": "wiki", "cwd": "/tmp/wiki", "machine": "mac",
            "kind": "picker", "message": "어느 모델로 갈까요?", "question": "어느 모델로 갈까요?",
            "updatedAt": "2026-08-05T15:00:00+09:00", "live": True, "asked": "", "said": "",
            "options": [{"n": i, "label": f"옵션{i}"} for i in range(1, 13)]}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"decisions": [many]})))
    desktop.evaluate("loadDash()")
    desktop.wait_for_timeout(300)
    card = dash_card(desktop)
    assert card.locator(".dash-opt").count() == 9                  # 한 키로 보낼 수 있는 것만 버튼
    text = card.text_content()
    assert "12. 옵션12" in text                                    # 무엇이 있는지는 보여준다
    assert "터미널" in text                                        # 어떻게 고르는지도 써 준다
    card.locator(".dash-opt").last.click()                         # 1~9 는 그대로 눌린다
    desktop.wait_for_timeout(400)
    assert bridge.answers == [{"project": "wiki", "sessionId": "wiki-9",
                               "type": "option", "value": "9"}]


def test_대시보드_보낸_카드는_다시_못_누른다(desktop, bridge):
    """7초 폴링이 같은 카드를 되살려도 잠겨 있어야 한다 — 같은 숫자가 두 번 찍히면 엉뚱한 항목이 눌린다."""
    bridge.answers.clear()
    dash_card(desktop).locator(".dash-opt").first.click()
    desktop.wait_for_timeout(400)
    assert len(bridge.answers) == 1
    desktop.evaluate("loadDash()")                                 # 폴링이 하는 일 그대로
    desktop.wait_for_timeout(400)
    card = dash_card(desktop)
    assert "보냄" in card.text_content()                           # 보냈다는 표시가 있어야 안 누른다
    assert card.locator(".dash-opt").first.is_disabled()
    card.locator(".dash-opt").first.click(force=True)
    desktop.wait_for_timeout(300)
    assert len(bridge.answers) == 1                                # 두 번째는 안 나갔다


def test_대시보드에서_보낸_답은_인박스에서도_잠긴다(desktop, bridge):
    """두 화면이 각자 잠그면 대시보드에서 보낸 뒤 인박스에서 한 번 더 보낼 수 있다 —
    잠금은 화면이 아니라 카드(project + 표식)에 걸어야 한다."""
    bridge.answers.clear()
    dash_card(desktop).locator(".dash-opt").first.click()
    desktop.wait_for_timeout(400)
    assert len(bridge.answers) == 1
    open_inbox(desktop)
    sent = desktop.locator(".inbox-card.sent")
    assert sent.count() == 1 and "wiki" in sent.text_content()
    assert sent.locator(".opt-btn").first.is_disabled()
    sent.locator(".opt-btn").first.click(force=True)
    desktop.wait_for_timeout(300)
    assert len(bridge.answers) == 1                                # 다른 화면에서도 두 번 안 나간다


def test_인박스에서_보낸_답은_대시보드에서도_잠긴다(desktop, bridge):
    """반대 방향도 같다 — 인박스에서 답한 카드가 상단에서 다시 눌려선 안 된다."""
    bridge.answers.clear()
    open_inbox(desktop).nth(0).locator(".opt-btn").first.click()
    desktop.wait_for_timeout(500)
    assert len(bridge.answers) == 1
    card = dash_card(desktop)
    assert card.locator(".dash-opt").first.is_disabled()
    card.locator(".dash-opt").first.click(force=True)
    desktop.wait_for_timeout(300)
    assert len(bridge.answers) == 1


def test_대시보드_직접_지시는_그_세션으로_간다(desktop, bridge):
    """선택지를 못 읽은 결재 카드(permission)는 사용자가 답을 글로 직접 넣는 자리다.

    (놀고 있는 세션(idle)에 다음 지시를 넣는 자리는 인박스의 '✅ 완료 — 다음 지시 대기' 쪽이다 —
     대시보드는 답이 없으면 클로드가 못 나가는 것만 올린다.)
    """
    bridge.answers.clear()
    card = {"sessionId": "mall-1", "project": "mall", "cwd": "/tmp/mall", "machine": "mac",
            "kind": "permission", "message": "Claude needs your permission", "live": True,
            "updatedAt": "2026-08-05T16:30:00+09:00",
            "asked": "결제 모듈 정리해줘", "said": "1단계까지 끝냈습니다. 다음으로 갈까요?"}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"decisions": [card]})))
    desktop.evaluate("loadDash()")
    box = dash_card(desktop)
    assert "결제 모듈 정리해줘" in box.text_content()
    box.locator(".dash-ask").fill("2단계도 진행해")
    box.locator(".dash-send").click()
    desktop.wait_for_timeout(400)
    assert bridge.answers == [{"project": "mall", "sessionId": "mall-1",
                               "type": "text", "value": "2단계도 진행해"}]


def test_결정할_것이_없으면_대시보드가_사라진다(desktop):
    done = {"sessionId": "frog-1", "project": "frog", "cwd": "/tmp/frog", "machine": "mac",
            "kind": "done", "message": "", "updatedAt": "2026-08-05T13:20:00+09:00",
            "live": True, "asked": "", "said": ""}
    desktop.route("**/api/decisions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"decisions": [done]})))
    desktop.evaluate("loadDash()")
    desktop.wait_for_timeout(300)
    assert not desktop.is_visible("#dash")                         # 빈 자리를 남기지 않는다


def test_대시보드_접기는_기억된다(desktop):
    """폰에서 자리를 많이 먹는다 — 접어 두면 다시 열어도 접힌 채여야 한다."""
    dash_card(desktop)
    assert desktop.is_visible("#dash-cards")
    desktop.click("#dash-toggle")
    desktop.wait_for_timeout(200)
    assert not desktop.is_visible("#dash-cards")
    assert "1건" in desktop.text_content("#dash-toggle")           # 몇 건인지는 접어도 보인다
    desktop.reload()
    desktop.wait_for_selector("#sessions li")
    desktop.wait_for_timeout(500)
    assert not desktop.is_visible("#dash-cards")
    desktop.click("#dash-toggle")                                  # 다시 편다
    desktop.wait_for_timeout(200)
    assert desktop.is_visible("#dash-cards")


def test_폰_대시보드_선택지는_손가락_크기다(phone):
    phone.wait_for_selector(".dash-card")
    box = phone.locator(".dash-opt").first.bounding_box()
    assert box["height"] >= 44


# ---------- 분할선 드래그 ----------
# 3열 분할선으로 좌우 폭을 조정할 수 있어야 한다 — 색은 CSS 에서 고쳤고, 여기서는 폭이다.

LIST_SPLIT = '.splitter[data-pane="list"]'
FILE_SPLIT = '.splitter[data-pane="file"]'


def drag(page, selector, dx):
    box = page.locator(selector).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y, steps=8)
    page.mouse.up()
    page.wait_for_timeout(250)


def width_of(page, selector):
    return page.locator(selector).bounding_box()["width"]


# ---------- 사이드바 접기 — 터미널 헤더의 « » (슬라이드 숨김) ----------

def test_사이드바_접기_버튼은_폭을_0으로_밀고_다시_펴면_원래_폭이다(desktop):
    desktop.evaluate("localStorage.removeItem('frog.side.collapsed')")
    open_terminal(desktop)
    before = width_of(desktop, "#list-view")
    term_before = width_of(desktop, "#term-view")
    assert before >= 160
    assert desktop.locator("#side-toggle").text_content() == "«"
    desktop.click("#side-toggle")
    desktop.wait_for_timeout(400)                                   # .2s 슬라이드가 끝난 뒤
    assert width_of(desktop, "#list-view") == 0
    assert not desktop.locator(LIST_SPLIT).is_visible()              # 분할선도 같이 접힌다
    assert desktop.locator("#side-toggle").text_content() == "»"
    # 터미널이 그 자리를 먹는다(파일 패널이 옆에 열려 있어도 — 1100px↑ 자동 열림)
    assert width_of(desktop, "#term-view") >= term_before + before - 8
    desktop.click("#side-toggle")
    desktop.wait_for_timeout(400)
    assert width_of(desktop, "#list-view") == pytest.approx(before, abs=2)   # 끌어 둔 폭 그대로
    assert desktop.locator(LIST_SPLIT).is_visible()


def test_사이드바_접힘은_이_기기에_남고_빈_화면에서는_접지_않는다(desktop):
    """접은 채 터미널을 닫으면 목록마저 사라져 빈 화면이 된다 — 그때는 펴 보인다."""
    open_terminal(desktop)
    desktop.click("#side-toggle")
    desktop.wait_for_timeout(400)
    assert desktop.evaluate("localStorage.getItem('frog.side.collapsed')") == "1"
    desktop.evaluate("showList()")                                   # 터미널 없음(.idle)
    desktop.wait_for_timeout(400)
    assert width_of(desktop, "#list-view") >= 160                    # 접힘 상태여도 목록은 보인다
    desktop.reload()
    desktop.wait_for_selector("#sessions li")
    open_terminal(desktop)
    desktop.wait_for_timeout(400)
    assert width_of(desktop, "#list-view") == 0                      # 새로고침해도 접힘을 기억한다
    desktop.click("#side-toggle")                                    # 공용 브라우저 — 되돌려 둔다
    desktop.wait_for_timeout(400)
    assert desktop.evaluate("localStorage.getItem('frog.side.collapsed')") == "0"


def test_폰에는_사이드바_접기_버튼이_없다(phone):
    """폰은 한 화면씩 갈아끼운다 — ← 목록 이 그 역할이라 버튼이 둘이면 헷갈린다."""
    open_terminal(phone)
    assert not phone.locator("#side-toggle").is_visible()
    assert phone.locator("#back").is_visible()


def test_분할선을_끌면_사이드바_폭이_바뀌고_유지된다(desktop):
    before = width_of(desktop, "#list-view")
    drag(desktop, LIST_SPLIT, 140)
    after = width_of(desktop, "#list-view")
    assert after == pytest.approx(before + 140, abs=8)
    desktop.reload()                                               # 다시 열어도 그 폭 그대로
    desktop.wait_for_selector("#sessions li")
    desktop.wait_for_timeout(300)
    assert width_of(desktop, "#list-view") == pytest.approx(after, abs=4)


def test_분할선은_사이드바를_0으로_만들지_않는다(desktop):
    """0 이 되면 다시 넓힐 분할선조차 사라져 되돌릴 방법이 없다. 반대쪽도 마찬가지 —
    가운데(터미널)가 지나치게 좁아지면 클로드 피커 상자가 깨진다."""
    drag(desktop, LIST_SPLIT, -600)
    narrow = width_of(desktop, "#list-view")
    assert narrow >= 160
    assert desktop.is_visible(LIST_SPLIT)                          # 잡을 선이 남아 있다
    drag(desktop, LIST_SPLIT, 2000)
    assert 1280 - width_of(desktop, "#list-view") >= 360           # 가운데 자리를 남긴다


def test_분할선을_끌면_터미널_크기가_따라온다(desktop, bridge):
    """PTY 크기가 화면과 어긋나면 클로드의 `/` 피커 상자가 깨진다(이 repo 의 알려진 증상)."""
    open_terminal(desktop)
    bridge.ws_text.clear()
    cols_before = desktop.evaluate("term.cols")
    drag(desktop, LIST_SPLIT, 200)                                 # 사이드바를 넓히면 터미널이 좁아진다
    desktop.wait_for_timeout(500)
    sizes = [json.loads(t) for t in bridge.ws_text if "resize" in t]
    assert sizes, "폭이 바뀌었는데 PTY 크기를 아무도 안 알려줬다"
    assert sizes[-1]["cols"] < cols_before
    assert desktop.evaluate("term.cols") == sizes[-1]["cols"]      # 화면과 서버가 같은 크기를 안다


def test_파일패널_분할선은_패널을_열어야_나온다(desktop):
    assert not desktop.is_visible(FILE_SPLIT)                      # 접힌 패널 옆에 잡을 선만 남으면 안 된다
    open_panel(desktop)
    assert desktop.is_visible(FILE_SPLIT)
    before = width_of(desktop, "#file-panel")
    drag(desktop, FILE_SPLIT, -150)                                # 왼쪽으로 끌면 넓어진다
    assert width_of(desktop, "#file-panel") == pytest.approx(before + 150, abs=8)


def test_직접_끈_파일패널_폭은_파일을_열어도_그대로다(desktop):
    """파일을 열면 .wide(55%)가 걸린다 — 하지만 사용자가 직접 끈 폭이 있으면 그쪽이 이긴다.
    '폭 조절이 안 된다'가 이 작업의 출발점인데 화면이 제멋대로 되돌리면 고친 뜻이 없다."""
    open_panel(desktop)
    drag(desktop, FILE_SPLIT, -100)
    mine = width_of(desktop, "#file-panel")
    open_file(desktop, "README.md")
    desktop.wait_for_timeout(300)
    assert "wide" in (desktop.get_attribute("#file-panel", "class") or "")
    assert width_of(desktop, "#file-panel") == pytest.approx(mine, abs=4)


def test_창을_좁히면_끈_폭을_놓는다(desktop):
    """인라인 폭은 폰 배치의 width:100% 를 이긴다 — 폰 폭에서는 반드시 놓아야 한다."""
    drag(desktop, LIST_SPLIT, 200)
    desktop.set_viewport_size({"width": 412, "height": 900})
    desktop.wait_for_timeout(300)
    assert not desktop.is_visible(LIST_SPLIT)
    assert width_of(desktop, "#list-view") == pytest.approx(412, abs=2)
    desktop.set_viewport_size({"width": 1280, "height": 800})
    desktop.wait_for_timeout(300)
    assert width_of(desktop, "#list-view") == pytest.approx(450, abs=8)   # 데스크톱으로 오면 다시


# ---------- 사이드바 끄기·숨김 ----------
# tmux 터미널을 끌 수 있어야 한다(안 그러면 메모리만 계속 는다). 끄기 버튼, 목록에서
# 더 이상 안 나오게 하는 숨김 버튼, 숨긴 것을 다시 보이게 하는 UI 를 둔다.

def row_of(page, nth=0):
    """그 줄에 손을 얹는다 — 액션 버튼은 평소 숨어 있다가 hover/focus 때만 나온다(CSS)."""
    row = page.locator("#sessions li").nth(nth)
    row.hover()
    return row


def test_사이드바_액션은_작은_아이콘_세로_스택이다(clean_favs, desktop):
    """글자 버튼('끄기')이 커서 자꾸 잘못 눌렸다 — 아이콘으로 줄이고 뜻은 title 로.

    순서 계약: 즐겨찾기가 맨 앞, 끄기는 .danger, 숨기기는 늘 :last-child.
    (숨기기를 집는 기존 테스트들이 :last-child 를 쓰므로 새 버튼은 스택 앞쪽에 선다.)
    """
    row = row_of(desktop)
    btns = row.locator(".row-acts button")
    assert btns.nth(0).text_content() == "☆" and btns.nth(0).get_attribute("title") == "즐겨찾기"
    assert btns.nth(1).text_content() == "🗑" and btns.nth(1).get_attribute("title") == "끄기"
    assert btns.nth(2).text_content() == "🙈" and btns.nth(2).get_attribute("title") == "숨기기"
    assert row.locator(".row-acts button.danger").text_content() == "🗑"
    assert row.locator(".row-acts button:last-child").text_content() == "🙈"
    boxes = [btns.nth(i).bounding_box() for i in range(3)]
    assert all(abs(boxes[0]["x"] - b["x"]) < 2 for b in boxes)      # 가로 한 줄이 아니라 세로로
    assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
    assert boxes[0]["width"] <= 60                                 # '끄기' 글자 버튼 시절보다 작다


def test_끄기는_되묻고_확인해야_kill_이_나간다(desktop, bridge):
    """그 세션 안에서 돌던 클로드가 같이 죽는다 — 그 사실이 문구에 드러나야 한다.
    confirm() 은 쓰지 않는다(모달은 포커스·자동화를 망가뜨린다)."""
    bridge.kills.clear()
    row = row_of(desktop)                                          # wiki (켜짐)
    row.locator(".row-acts button.danger").click()
    desktop.wait_for_timeout(200)
    assert bridge.kills == []                                      # 되묻기 전에는 아무것도 안 죽는다
    confirm = row.locator(".row-confirm")
    assert "클로드도 같이 종료" in confirm.text_content()
    assert desktop.locator("#term-view").is_hidden()               # 액션 클릭이 터미널을 열지 않는다
    confirm.locator("button").first.click()
    desktop.wait_for_timeout(600)
    assert bridge.kills == ["wiki"]


def test_끄기_취소면_아무것도_안_간다(desktop, bridge):
    bridge.kills.clear()
    row = row_of(desktop)
    row.locator(".row-acts button.danger").click()
    desktop.wait_for_timeout(200)
    row.locator(".row-confirm button.cancel").click()
    desktop.wait_for_timeout(300)
    assert row.locator(".row-confirm").count() == 0
    assert bridge.kills == []


def test_끄기_404는_경고로_띄우지_않는다(desktop, bridge):
    """폴더 없이 tmux 세션만 있던 항목은 끈 뒤 이름 자체가 사라져 두 번째 요청이 404 다.
    결과는 어차피 '꺼짐'이라, 사용자가 할 일이 하나도 없는 경고를 띄우면 안 된다."""
    with bridge.kill_gone():
        row = row_of(desktop)
        row.locator(".row-acts button.danger").click()
        desktop.wait_for_timeout(200)
        row.locator(".row-confirm button").first.click()
        desktop.wait_for_timeout(600)
    assert not desktop.is_visible("#conn-hint")
    assert desktop.locator("#sessions li").count() == 5             # 목록은 그대로 다시 읽힌다


def test_꺼진_세션에는_끄기_버튼이_없다(desktop):
    row = row_of(desktop, 4)                                        # apex — 꺼짐
    assert row.locator(".row-acts button.danger").count() == 0
    assert row.locator(".row-acts button").count() == 2             # 즐겨찾기·숨김만 남는다
    assert row.locator(".row-acts button:last-child").text_content() == "🙈"


def test_꺼진_세션은_되묻고_켜기를_눌러야_열린다(desktop):
    """목록을 스치기만 해도 프로젝트가 우수수 켜지던 문제.
    꺼진 것을 누르면 tmux 세션이 새로 서므로 끄기와 같은 자리·같은 모양으로 한 번 더 묻는다."""
    row = row_of(desktop, 4)                                        # apex — 꺼짐
    row.locator("b").click()
    desktop.wait_for_timeout(300)
    assert desktop.locator("#term-view").is_hidden()                # 되묻기 전에는 안 열린다
    assert "새로 시작" in row.locator(".row-confirm").text_content()
    row.locator(".row-confirm button").first.click()                # [켜기]
    desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
    assert desktop.is_visible("#term-view")


def test_꺼진_세션_되묻기를_취소하면_안_열린다(desktop):
    row = row_of(desktop, 4)                                        # apex — 꺼짐
    row.locator("b").click()
    desktop.wait_for_timeout(300)
    row.locator(".row-confirm button.cancel").click()
    desktop.wait_for_timeout(300)
    assert row.locator(".row-confirm").count() == 0
    assert desktop.locator("#term-view").is_hidden()


def test_살아있는_세션은_되묻지_않고_바로_열린다(desktop):
    """평소 세션 이동까지 두 번 누르게 하면 도구가 못 쓸 만큼 느려진다 —
    켜진 것을 누르는 것은 화면 전환일 뿐이라 잘못 눌러도 새로 켜지는 것이 없다."""
    row = row_of(desktop)                                           # wiki — 켜짐
    row.locator("b").click()
    desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
    assert row.locator(".row-confirm").count() == 0
    assert desktop.is_visible("#term-view")


def test_숨기면_목록에서_빠지고_되돌릴_수_있다(clean_hidden, desktop):
    bridge = clean_hidden
    assert not desktop.is_visible("#hidden-bar")                    # 0개면 바 자체가 없다
    row = row_of(desktop)                                           # wiki
    row.locator(".row-acts button:last-child").click()
    desktop.wait_for_timeout(600)
    assert bridge.hides[-1] == {"project": "wiki", "hidden": True}
    assert desktop.locator("#term-view").is_hidden()                # 숨김 클릭도 터미널을 열지 않는다
    names = desktop.locator("#sessions li b").all_text_contents()
    assert "wiki" not in names and len(names) == 4
    bar = desktop.locator("#hidden-bar")
    assert bar.is_visible() and "1개" in bar.text_content()
    assert not desktop.is_visible("#hidden-list")                   # 기본은 접힘
    desktop.click("#hidden-toggle")
    desktop.wait_for_timeout(300)
    item = desktop.locator("#hidden-list li")
    assert item.count() == 1 and "wiki" in item.text_content()
    item.locator("button").click()                                  # 되돌리기
    desktop.wait_for_timeout(600)
    assert bridge.hides[-1] == {"project": "wiki", "hidden": False}
    assert "wiki" in desktop.locator("#sessions li b").all_text_contents()
    assert not desktop.is_visible("#hidden-bar")                    # 다시 0개 — 바가 사라진다


def test_숨긴_항목은_결재_수에_영향을_주지_않는다(clean_hidden, desktop):
    """숨김은 사이드바를 접는 것일 뿐이다 — 그 프로젝트가 사용자를 기다리고 있다는 사실은 그대로다."""
    row_of(desktop).locator(".row-acts button:last-child").click()  # wiki 숨김(결재 1건짜리)
    desktop.wait_for_timeout(600)
    assert desktop.text_content("#sum-pending").startswith("결재 대기 1건")
    assert desktop.locator(".dash-card").count() >= 1               # 상단 대시보드에도 그대로 남는다


def test_프로젝트아닌_폴더는_자동숨김이지만_열수있고_사용자숨김과_구분된다(
        clean_hidden, desktop):
    bridge = clean_hidden
    item = {"name": "plain-folder", "alive": True, "path": "/tmp/plain-folder",
            "status": "on", "pending": 0, "project": False}
    SESSIONS.append(item)
    bridge.hidden.add("plain-folder")                 # 자동 숨김과 사용자 숨김이 동시에 켜진 상태
    try:
        desktop.evaluate("loadSessions()")
        desktop.wait_for_timeout(400)
        assert "plain-folder" not in desktop.locator("#sessions li b").all_text_contents()
        desktop.click("#hidden-toggle")
        hidden = desktop.locator("#hidden-list li", has_text="plain-folder")
        assert hidden.count() == 1 and "프로젝트 아님" in hidden.text_content()
        hidden.locator("button", has_text="되돌리기").click()
        desktop.wait_for_timeout(500)
        assert bridge.hides[-1] == {"project": "plain-folder", "hidden": False}
        assert "plain-folder" not in bridge.hidden
        # 사용자 숨김만 풀렸으므로 자동 분류는 남고, 숨김 칸에서 이름을 눌러 터미널은 열 수 있다.
        hidden = desktop.locator("#hidden-list li", has_text="plain-folder")
        assert hidden.count() == 1 and hidden.locator("button").count() == 0
        hidden.locator("span").click()
        desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
        assert desktop.text_content("#hdr-name") == "plain-folder"
    finally:
        bridge.hidden.discard("plain-folder")
        SESSIONS.remove(item)
        desktop.evaluate("showList(); loadSessions()")


def test_폰에서는_액션_버튼이_늘_보인다(phone):
    """손가락엔 hover 가 없다 — 폰에서는 끄기·숨김이 항상 보여야 누를 수 있다."""
    acts = phone.locator("#sessions li").first.locator(".row-acts button")
    assert acts.first.is_visible()


# ---------- 되묻기가 열리는 동안의 겹침 ----------
# 실측(스크린샷): 🗑 를 눌러 확인 상자가 뜨자 아이콘 스택이 그 위에 겹쳐 앉아 문구와 [끄기]를 가렸다.
# 원인은 스택이 absolute + top:50% 중앙 정렬 + 불투명 배경이라, 확인 상자가 붙어 줄이 세로로
# 커지면 그 한가운데(=확인 영역)로 내려앉는 것. 열려 있는 동안 통째로 비켜나야 한다.

def _hits_row_acts(page, locator):
    """그 요소의 한가운데를 실제로 집는 것이 무엇인가 — 아이콘 스택이면 가려진 것이다."""
    box = locator.bounding_box()
    return page.evaluate(
        """([x, y]) => { const hit = document.elementFromPoint(x, y);
                         return !!(hit && hit.closest('.row-acts')); }""",
        [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])


def test_되묻기가_열리면_아이콘_스택이_비켜난다(desktop, bridge):
    bridge.kills.clear()
    row = row_of(desktop)                                           # wiki (켜짐)
    row.locator(".row-acts button.danger").click()
    desktop.wait_for_timeout(200)
    confirm = row.locator(".row-confirm")
    assert confirm.is_visible()
    assert row.locator(".row-acts").is_hidden()                     # 스택은 통째로 비켜난다
    kill = confirm.locator("button").first
    assert not _hits_row_acts(desktop, kill)                        # [끄기] 를 가리는 것이 없다
    assert not _hits_row_acts(desktop, confirm.locator("span"))     # 문구도 읽힌다
    kill.click()                                                    # 가려져 있으면 여기서 막힌다
    desktop.wait_for_timeout(600)
    assert bridge.kills == ["wiki"]


def test_폰에서도_되묻기_동안은_아이콘_스택이_비켜난다(phone, bridge):
    """폰은 hover 가 없어 스택이 늘 보인다 — 미디어쿼리의 상시 표시보다 이 규칙이 세야 한다."""
    bridge.kills.clear()
    row = phone.locator("#sessions li").first                       # wiki (켜짐)
    row.locator(".row-acts button.danger").click()
    phone.wait_for_timeout(200)
    assert row.locator(".row-confirm").is_visible()
    assert row.locator(".row-acts").is_hidden()
    row.locator(".row-confirm button.cancel").click()                # 되돌려 두고 끝낸다
    phone.wait_for_timeout(300)
    assert row.locator(".row-acts").is_visible()                     # 닫으면 다시 나온다
    assert bridge.kills == []


# ---------- 사이드바 즐겨찾기 ----------
# 즐겨찾기한 프로젝트는 무조건 목록 맨 위에, 즐겨찾기한 순서대로 둔다.
# 그 아래는 기존 규칙(결재>진행중>완료>그 외, 같은 상태 안에서 이름순)이 그대로 이어진다.
# 픽스처의 상태순은 wiki·mall·blog·frog·apex — 맨 아래 apex(꺼짐)를 올려야 순서 변화가 드러난다.

def row_named(page, name):
    """이름으로 그 줄을 집는다 — 즐겨찾기를 누르면 자리가 바뀌므로 nth 로는 못 따라간다."""
    row = page.locator(f'#sessions li:has(b:text-is("{name}"))')
    row.hover()
    return row


def fav_btn(row):
    return row.locator(".row-acts button").first          # 즐겨찾기는 스택 맨 앞


def names_of(page):
    return page.locator("#sessions li b").all_text_contents()


def test_즐겨찾기를_누르면_POST_가_가고_별이_붙는다(clean_favs, desktop):
    bridge = clean_favs
    assert names_of(desktop) == ["wiki", "mall", "blog", "frog", "apex"]   # 상태순으로 시작
    assert desktop.locator("#sessions li .row-name i").count() == 0        # 아직 별이 없다
    fav_btn(row_named(desktop, "apex")).click()                            # 맨 아래(꺼짐)를 올린다
    desktop.wait_for_timeout(600)
    assert bridge.favs_posts[-1] == {"project": "apex", "fav": True}
    assert desktop.locator("#term-view").is_hidden()                       # 액션 클릭이 터미널을 열지 않는다
    assert names_of(desktop) == ["apex", "wiki", "mall", "blog", "frog"]   # 상태를 제치고 맨 위로
    star = desktop.locator("#sessions li").first.locator(".row-name i")
    assert star.count() == 1 and star.text_content() == "★"                # 이름 앞에 ★
    assert desktop.locator("#sessions li .row-name i").count() == 1        # 그 줄에만
    assert fav_btn(row_named(desktop, "apex")).text_content() == "★"       # 버튼도 켜진 모양으로


def test_즐겨찾기_2개는_누른_순서로_맨_위에_온다(clean_favs, desktop):
    """이름순도 상태순도 아니다 — 사용자가 누른 순서가 그대로 맨 위의 순서다."""
    fav_btn(row_named(desktop, "apex")).click()            # 꺼짐(맨 아래) 먼저
    desktop.wait_for_timeout(600)
    fav_btn(row_named(desktop, "blog")).click()            # 완료(가운데) 다음
    desktop.wait_for_timeout(600)
    assert clean_favs.favs == ["apex", "blog"]
    # apex 가 blog 보다 상태도 낮고 이름도 뒤지만, 먼저 눌렀으므로 위에 선다
    assert names_of(desktop) == ["apex", "blog", "wiki", "mall", "frog"]
    assert desktop.locator("#sessions li .row-name i").count() == 2
    # 나머지 셋은 기존 규칙 그대로 (결재 > 진행중 > 완료)
    assert "결재" in desktop.text_content("#sessions li:nth-child(3) .st")
    assert "진행중" in desktop.text_content("#sessions li:nth-child(4) .st")


def test_즐겨찾기를_해제하면_원래_자리로_돌아간다(clean_favs, desktop):
    bridge = clean_favs
    fav_btn(row_named(desktop, "apex")).click()
    desktop.wait_for_timeout(600)
    assert names_of(desktop)[0] == "apex"
    fav_btn(row_named(desktop, "apex")).click()            # 같은 버튼이 이제 해제다
    desktop.wait_for_timeout(600)
    assert bridge.favs_posts[-1] == {"project": "apex", "fav": False}
    assert bridge.favs == []
    assert names_of(desktop) == ["wiki", "mall", "blog", "frog", "apex"]   # 상태순 제자리
    assert desktop.locator("#sessions li .row-name i").count() == 0        # 별도 사라진다


def test_즐겨찾기해도_숨김이_이긴다(clean_favs, clean_hidden, desktop):
    """숨긴 프로젝트는 즐겨찾기여도 접힌 채다 — 숨김은 '보지 않겠다'는 더 센 뜻이다."""
    fav_btn(row_named(desktop, "apex")).click()
    desktop.wait_for_timeout(600)
    assert names_of(desktop)[0] == "apex"
    row_named(desktop, "apex").locator(".row-acts button:last-child").click()   # 숨기기
    desktop.wait_for_timeout(600)
    assert "apex" not in names_of(desktop)                                 # 맨 위여도 접힌다
    assert clean_favs.favs == ["apex"]                                     # 즐겨찾기는 그대로 남고
    bar = desktop.locator("#hidden-bar")
    assert bar.is_visible() and "1개" in bar.text_content()


def test_즐겨찾기는_결재_수에_영향을_주지_않는다(clean_favs, desktop):
    fav_btn(row_named(desktop, "apex")).click()
    desktop.wait_for_timeout(600)
    assert desktop.text_content("#sum-pending").startswith("결재 대기 1건")


# ---------- CPU 팝업 — 제목 옆 [CPU] 버튼이 htop(전용 tmux 세션)을 켰다 끈다 ----------

def test_CPU_버튼은_htop_팝업을_켰다_끈다(desktop, bridge):
    desktop.click("#btn-cpu")
    desktop.wait_for_timeout(500)
    assert desktop.is_visible("#cpu-pop")
    assert bridge.ws_log[-1] == "_frogtop"           # 전용 세션으로 붙는다
    assert "on" in (desktop.get_attribute("#btn-cpu", "class") or "")
    desktop.click("#btn-cpu")                        # 토글 — 다시 누르면 접힌다
    desktop.wait_for_timeout(300)
    assert not desktop.is_visible("#cpu-pop")
    assert "on" not in (desktop.get_attribute("#btn-cpu", "class") or "")


# ---------- 이미지·파일 붙여넣기(⌘V 경로) — 업로드하고 경로만 찍는다 ----------

PASTE_JS = """() => {
  const dt = new DataTransfer();
  dt.items.add(new File(["fake-png"], "shot.png", { type: "image/png" }));
  document.dispatchEvent(new ClipboardEvent("paste",
    { clipboardData: dt, bubbles: true, cancelable: true }));
}"""


def test_붙여넣기는_업로드하고_경로만_찍는다(desktop, bridge):
    open_terminal(desktop)
    desktop.evaluate(PASTE_JS)
    desktop.wait_for_timeout(600)
    assert bridge.uploads[-1] == {"project": "wiki", "name": "shot.png"}
    got = bytes(bridge.received)
    assert b"'/tmp/_uploads/wiki/shot.png' " in got
    assert b"\r" not in got                          # 엔터는 절대 같이 안 들어간다(실행 방지)


def test_연결_전_붙여넣기도_연결되면_찍힌다(desktop, bridge):
    """접속 직후(WS 열리기 전)의 붙여넣기가 소리 없이 사라졌다 — "처음 실행 때만 안 된다"의
    정체 후보. 큐에 담아 두었다가 연결이 열리는 순간 찍는다."""
    open_terminal(desktop)
    desktop.evaluate("ws.close()")                   # 연결 없는 창(재접속 0.5초 전)을 재현
    desktop.evaluate(PASTE_JS)
    desktop.wait_for_timeout(2500)                   # 재접속 + 플러시
    assert b"'/tmp/_uploads/wiki/shot.png' " in bytes(bridge.received)


def test_목록_화면_붙여넣기는_안내한다(desktop, bridge):
    bridge.uploads.clear()                           # 가짜 브릿지는 테스트 간 공유 — 앞 기록을 비운다
    desktop.evaluate(PASTE_JS)                       # 터미널을 안 연 상태
    desktop.wait_for_timeout(400)
    assert not bridge.uploads                        # 업로드 자체가 안 나간다
    assert "터미널을 연 뒤" in desktop.text_content("#conn-hint")


def test_새_배포를_감지하면_새로고침_배너가_뜬다(desktop, bridge):
    """PWA·오래 연 탭은 껐다 켜기 전까지 옛 코드를 문다(2026-08-15 한글 소동의 절반) —
    서버가 프론트 버전을 요약에 실어 주고, 달라지면 새로고침 배너가 뜬다."""
    desktop.wait_for_timeout(1000)                   # 첫 폴링이 지금 버전을 기억
    assert not desktop.is_visible("#update-bar")
    bridge.web_version = 2                           # 새 배포 흉내
    desktop.wait_for_timeout(8500)                   # 다음 7초 폴링에서 감지
    assert desktop.is_visible("#update-bar")


def test_파인더_폴더_드롭은_통째로_올리고_폴더_경로를_찍는다(desktop, bridge):
    """웹은 폴더의 원본 경로를 알 수 없다(브라우저 보안) — 대신 하위 구조 그대로 업로드하고
    저장된 폴더 경로를 한 번 찍는다(파인더·탐색기에서 마음대로 드래그).
    entry 객체는 webkitGetAsEntry 와 같은 모양으로 조립한다(진짜 디렉터리 드래그는 브라우저
    밖에서만 만들 수 있어 여기서는 API 계약을 그대로 흉내낸다)."""
    bridge.uploads.clear()
    open_terminal(desktop)
    bridge.received.clear()                  # 공유 브릿지 — 앞 테스트가 찍은 경로 바이트를 비운다
    desktop.evaluate("""async () => {
      const fileEntry = (name, body) => ({ name, isFile: true, isDirectory: false,
        file: ok => ok(new File([body], name)) });
      const dirEntry = (name, children) => ({ name, isFile: false, isDirectory: true,
        createReader: () => { let sent = false;
          return { readEntries: ok => { const b = sent ? [] : children; sent = true; ok(b); } }; } });
      await uploadEntries([dirEntry("사진", [
        fileEntry("a.png", "x"),
        fileEntry(".DS_Store", "junk"),
        dirEntry("여름", [fileEntry("b.png", "y")]),
      ])]);
    }""")
    desktop.wait_for_timeout(800)
    assert {"project": "wiki", "name": "a.png", "dir": "사진"} in bridge.uploads
    assert {"project": "wiki", "name": "b.png", "dir": "사진/여름"} in bridge.uploads
    assert all(u["name"] != ".DS_Store" for u in bridge.uploads)   # 숨김 잡동사니는 걷어낸다
    assert "'/tmp/_uploads/wiki/사진' ".encode() in bytes(bridge.received)   # 폴더 경로 한 번만
    assert bytes(bridge.received).count(b"/tmp/_uploads/") == 1


def test_드롭한_파일은_원본을_찾으면_그_경로만_찍는다(desktop, bridge):
    """예전엔 뭘 끌든 _uploads 사본으로 가 같은 이름끼리 뭉개지고 캐시만 늘었다 —
    맥에서 원본이 특정되면 사본 없이 진짜 경로를 찍는다."""
    bridge.uploads.clear()
    bridge.locate_map["대본.md"] = "/Users/x/문서/대본.md"
    open_terminal(desktop)
    bridge.received.clear()
    desktop.evaluate("""async () => {
      const fe = { name: "대본.md", isFile: true, isDirectory: false,
        file: ok => ok(new File(["x"], "대본.md")) };
      await dropItems([fe]); }""")
    desktop.wait_for_timeout(600)
    assert not bridge.uploads                            # 사본을 만들지 않는다
    assert "'/Users/x/문서/대본.md' ".encode() in bytes(bridge.received)


def test_원본을_못_찾으면_업로드로_물러선다(desktop, bridge):
    bridge.uploads.clear()
    bridge.locate_map.clear()
    open_terminal(desktop)
    bridge.received.clear()
    desktop.evaluate("""async () => {
      const fe = { name: "낯선.md", isFile: true, isDirectory: false,
        file: ok => ok(new File(["x"], "낯선.md")) };
      await dropItems([fe]); }""")
    desktop.wait_for_timeout(600)
    assert bridge.uploads and bridge.uploads[-1]["name"] == "낯선.md"
    assert b"/tmp/_uploads/wiki/" in bytes(bridge.received)


# ---------- 📎 사진·파일 넣기 — 폰의 유일한 입력 경로 ----------

def test_키바에_파일_넣기_버튼이_있다(desktop):
    """폰에는 파인더 드래그도 쓸 만한 클립보드 이미지도 없다 — 이 버튼이 없으면 사진을
    넣을 길이 아예 없다."""
    open_terminal(desktop)
    assert desktop.locator("#keybar #pick-file").count() == 1
    picker = desktop.locator("#file-pick")
    assert picker.get_attribute("type") == "file"
    # accept 를 걸면 폰에서 영상·pdf 를 못 고른다 — 일부러 비워 둔다
    assert picker.get_attribute("accept") is None


def test_고른_파일도_원본을_찾으면_그_경로만_찍는다(desktop, bridge):
    """드래그와 같은 규칙이어야 한다 — 맥에서 고른 파일이 사본으로 갈리면 안 된다."""
    bridge.uploads.clear()
    bridge.locate_map["대본.md"] = "/Users/x/문서/대본.md"
    open_terminal(desktop)
    bridge.received.clear()
    desktop.set_input_files("#file-pick",
                            {"name": "대본.md", "mimeType": "text/markdown", "buffer": b"x"})
    desktop.wait_for_timeout(600)
    assert not bridge.uploads                            # 사본을 만들지 않는다
    assert "'/Users/x/문서/대본.md' ".encode() in bytes(bridge.received)


def test_폰_사진처럼_맥에_원본이_없으면_업로드한다(desktop, bridge):
    """폰에서 고른 사진은 맥에 원본이 없다 — 그때만 사본이 정답이다."""
    bridge.uploads.clear()
    bridge.locate_map.clear()
    open_terminal(desktop)
    bridge.received.clear()
    desktop.set_input_files("#file-pick",
                            {"name": "폰사진.png", "mimeType": "image/png", "buffer": b"x"})
    desktop.wait_for_timeout(600)
    assert bridge.uploads and bridge.uploads[-1]["name"] == "폰사진.png"
    assert b"/tmp/_uploads/wiki/" in bytes(bridge.received)


# ---------- 터미널 우클릭 메뉴 + 프로젝트 밖(스크래치패드) 경로 열기 ----------

SCRATCH_PATH = "/private/tmp/claude-501/-Users-x/sess/scratchpad/d-160.4.jpg"


def right_click_on_path(page, path):
    """터미널에 경로를 찍고 그 글자 위에서 우클릭한다."""
    page.keyboard.type(path)
    page.wait_for_timeout(400)
    box = page.locator(".xterm-screen").bounding_box()
    cell = page.evaluate("term._core._renderService.dimensions.css.cell")
    page.mouse.click(box["x"] + cell["width"] * 6, box["y"] + cell["height"] * 0.5, button="right")
    page.wait_for_selector("#fp-menu:not([hidden])")


def test_터미널_우클릭_메뉴로_기본앱에서_연다(desktop, bridge):
    """브라우저·tmux 메뉴를 막았으니 대신 쓸 메뉴를 우리가 준다.
    커서 밑 경로를 기본 앱·미리보기·파인더로 열고 경로 복사도 된다."""
    bridge.opens.clear()
    open_terminal(desktop)
    right_click_on_path(desktop, SCRATCH_PATH)
    labels = desktop.locator("#fp-menu button").all_text_contents()
    assert any("기본 앱으로 열기" in l for l in labels)
    assert any("미리보기" in l for l in labels) and any("파인더" in l for l in labels)
    desktop.locator("#fp-menu button", has_text="기본 앱으로 열기").click()
    desktop.wait_for_timeout(500)
    assert bridge.opens[-1] == {"abs": SCRATCH_PATH, "app": "default"}


def test_프로젝트_밖_절대경로도_Ctrl클릭으로_열린다(desktop, bridge):
    """클로드가 만든 이미지는 ~/work 밖(스크래치패드)에 있다 — 예전엔 '밖이라 못 연다'로
    막혔다. 이제 브릿지가 열어도 되는 자리인지 판정하고 맥 기본 앱으로 넘긴다."""
    bridge.opens.clear()
    open_terminal(desktop)
    desktop.keyboard.type(SCRATCH_PATH)
    desktop.wait_for_timeout(400)
    box = desktop.locator(".xterm-screen").bounding_box()
    cell = desktop.evaluate("term._core._renderService.dimensions.css.cell")
    desktop.keyboard.down("Control")
    desktop.mouse.click(box["x"] + cell["width"] * 6, box["y"] + cell["height"] * 0.5)
    desktop.keyboard.up("Control")
    desktop.wait_for_timeout(600)
    assert bridge.opens[-1] == {"abs": SCRATCH_PATH, "app": "default"}


# ---------- 터미널 링크 — 폰은 탭으로 열고, localhost 는 이 화면의 호스트로 ----------

def cell_xy(page, col, row):
    """터미널 (col,row) 칸의 가운데 화면 좌표 — app.js 의 pathAtEvent 와 같은 셈법."""
    box = page.locator("#terminal .terminal").bounding_box()
    cell = page.evaluate("term._core._renderService.dimensions.css.cell")
    return box["x"] + (col + 0.5) * cell["width"], box["y"] + (row + 0.5) * cell["height"]


def write_link_row0(page, url):
    # 식 끝이 함수면 playwright 가 그 함수를 인자 없이 한 번 부른다(실측: [None, url]) — 끝을 true 로 막는다
    page.evaluate("window.__opened = []; window.open = u => { window.__opened.push(u); return null; }; true")
    page.evaluate("u => term.write('\\x1b[H\\x1b[2J' + u)", url)   # 홈으로 가 지우고 0행에 찍는다
    page.wait_for_timeout(300)


def test_폰에서는_링크를_탭_한_번으로_연다(phone):
    """폰엔 ⌘·Ctrl 이 없다 — 수정키 없는 탭이 링크를 열어야 한다(지금까지는 아무 일도 안 났다)."""
    open_terminal(phone)
    url = "http://localhost:3010/dashboard"
    write_link_row0(phone, url)
    x, y = cell_xy(phone, 6, 0)
    phone.mouse.move(x, y)                                           # 터치도 mousemove 를 먼저 흉내 낸다
    phone.wait_for_timeout(150)
    phone.touchscreen.tap(x, y)
    phone.wait_for_timeout(400)
    assert phone.evaluate("window.__opened") == [url]                # 테스트 호스트가 루프백이라 치환은 없다


def test_마우스_기기는_여전히_수정키_클릭으로만_연다(desktop):
    """실수로 안 열리게 한 기존 계약(Zed·VS Code 방식)은 그대로다."""
    open_terminal(desktop)
    url = "http://localhost:3010/dashboard"
    write_link_row0(desktop, url)
    x, y = cell_xy(desktop, 6, 0)
    desktop.mouse.move(x, y); desktop.wait_for_timeout(150)
    desktop.mouse.click(x, y); desktop.wait_for_timeout(300)
    assert desktop.evaluate("window.__opened") == []
    desktop.mouse.move(x, y); desktop.wait_for_timeout(150)
    desktop.keyboard.down("Control"); desktop.mouse.click(x, y); desktop.keyboard.up("Control")
    desktop.wait_for_timeout(300)
    assert desktop.evaluate("window.__opened") == [url]


def test_localhost_링크는_이_화면을_내준_호스트로_바뀐다(desktop):
    """localhost 는 폰에선 폰 자신이다 — 같은 테일넷의 맥 이름으로 바꿔야 닿는다(3010 실측 200).
    사설 LAN IP 는 손대지 않고, 이 화면 자체가 루프백이면(맥에서 직접) 바꿀 이유가 없다."""
    open_terminal(desktop)
    host = "bridge.example.ts.net"
    f = lambda u, h=host: desktop.evaluate("([u, h]) => reachableUrl(u, h)", [u, h])
    assert f("http://localhost:3010/dashboard") == f"http://{host}:3010/dashboard"
    assert f("http://127.0.0.1:8080/a?b=1#c") == f"http://{host}:8080/a?b=1#c"
    assert f("http://0.0.0.0:3000") == f"http://{host}:3000/"
    assert f("http://172.30.1.150:3010") == "http://172.30.1.150:3010"    # 사설 IP 는 그대로
    assert f("https://github.com/x") == "https://github.com/x"
    assert f("http://localhost:3010/dashboard", "127.0.0.1") == "http://localhost:3010/dashboard"


def test_괄호_든_경로도_통째로_링크된다(desktop, bridge):
    """★실사고(2026-08-15): 'episodes/(대본28)부장기분/대본.md' 가 '(' 에서 끊겨
    '부장기분/대본.md' 로 잡히는 바람에 Ctrl+클릭이 '없는 경로'로 튕겼다."""
    open_terminal(desktop)
    got = desktop.evaluate("""() => {
      const line = "d, episodes/(대본28)부장기분/대본.md (파인더에 열려 있습니다)";
      const out = [];
      for (const m of line.matchAll(PATH_RE)) {
        const t = trimPathToken(m[0]);
        if (t && t.includes(".")) out.push(t);
      }
      return out; }""")
    assert got == ["episodes/(대본28)부장기분/대본.md"]
    # 문장 속에서 통째로 감싼 괄호는 벗긴다 — 반대 방향 회귀도 함께 잠근다
    assert desktop.evaluate("trimPathToken('(docs/설계/최종.md')") == "docs/설계/최종.md"
    assert desktop.evaluate("trimPathToken('(a/b.md)')") == "a/b.md"


def test_설정에서_캐시를_확인하고_비운다(desktop, bridge):
    """사본이 계속 쌓이니 설정에 비우기 버튼을 둔다.
    되돌릴 수 없으므로 한 번 되묻고, 비운 뒤에는 '비어 있음'으로 바뀐다."""
    bridge.cache = {"bytes": 3 * 1024 * 1024, "files": 7}
    bridge.cache_cleared = 0
    desktop.click("#btn-settings")
    desktop.wait_for_timeout(600)
    assert "7개" in desktop.text_content("#set-cache-size")
    assert "3.0MB" in desktop.text_content("#set-cache-size")
    desktop.click("#set-cache-clear")                     # 첫 누름 = 되묻기
    desktop.wait_for_timeout(200)
    assert bridge.cache_cleared == 0
    assert "정말" in desktop.text_content("#set-cache-clear")
    desktop.click("#set-cache-clear")                     # 두 번째 = 실행
    desktop.wait_for_timeout(800)
    assert bridge.cache_cleared == 1
    assert desktop.text_content("#set-cache-size") == "비어 있음"
    assert "확보" in desktop.text_content("#set-note")


def test_새_프로젝트를_만들면_바로_그_터미널이_열린다(desktop, bridge):
    """사이드바 ＋ 로 이름만 정하면 폴더가 생기고 곧바로 실행된다."""
    bridge.new_projects.clear()
    assert desktop.locator("#proj-new-name").is_hidden()       # 평소엔 접혀 있다
    desktop.click("#proj-new")
    desktop.fill("#proj-new-name", "새프로젝트")
    desktop.press("#proj-new-name", "Enter")
    desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
    desktop.wait_for_timeout(800)
    assert bridge.new_projects == ["새프로젝트"]
    assert bridge.ws_log[-1] == "새프로젝트"                    # 그 세션으로 붙었다
    assert desktop.text_content("#hdr-name") == "새프로젝트"
    assert desktop.locator("#proj-new-name").is_hidden()       # 입력칸은 다시 접힌다
    assert "새프로젝트" in desktop.locator("#sessions li b").all_text_contents()


def test_이미_있는_이름이면_만들지_않고_그_터미널을_연다(desktop, bridge):
    bridge.new_projects.clear()
    desktop.click("#proj-new")
    desktop.fill("#proj-new-name", "wiki")                     # 이미 있는 프로젝트
    desktop.press("#proj-new-name", "Enter")
    desktop.wait_for_timeout(900)
    assert bridge.new_projects == ["wiki"]
    assert desktop.text_content("#hdr-name") == "wiki"         # 에러 대신 그냥 열어 준다


# ---------- 오래 비워 둔 창으로 돌아오기 (프리징 대책) ----------

def test_폴링은_겹치지_않고_숨은_동안은_쉰다(desktop):
    """응답이 느린 동안 7초마다 새 요청이 쌓이면, 창을 다시 열 때 밀린 요청이 한꺼번에 몰린다.
    single-flight 로 한 번에 하나만 돌고, 안 보이는 동안은 아예 안 돈다."""
    got = desktop.evaluate("""async () => {
      let running = 0, maxRunning = 0;
      const real = window.refreshSummary;
      window.refreshSummary = async () => {                 // 느린 응답 흉내
        running++; maxRunning = Math.max(maxRunning, running);
        await new Promise(r => setTimeout(r, 300));
        running--;
      };
      await Promise.all([poll(), poll(), poll()]);          // 동시에 세 번 불러도
      window.refreshSummary = real;
      return maxRunning; }""")
    assert got == 1                                          # 한 번에 하나만 돈다


def test_창으로_돌아오면_끊긴_소켓을_바로_다시_붙인다(desktop, bridge):
    """잠들었다 깬 뒤 백오프(최대 15초)를 기다리느라 옛 화면이 남아 있던 것 — 돌아오는 즉시 잇는다."""
    open_terminal(desktop)
    desktop.wait_for_timeout(500)
    before = bridge.ws_log[-1]
    desktop.evaluate("ws.close()")                           # 자는 동안 소켓이 죽은 상황
    desktop.evaluate("clearTimeout(reconnectTimer)")         # 백오프 대기 중으로 고정
    desktop.wait_for_timeout(300)
    desktop.evaluate("document.dispatchEvent(new Event('visibilitychange'))")   # 창 복귀
    desktop.wait_for_timeout(900)
    assert bridge.ws_log[-1] == before                       # 같은 세션으로 다시 붙었다
    assert desktop.evaluate("ws.readyState") == 1            # 기다리지 않고 즉시 연결


def test_파일_편집_색을_따로_고른다(desktop):
    """오른쪽 파일 편집 창의 밝기를 앱 껍데기와 따로 잡는다.
    기본은 '화면과 같게' — 고르기 전에는 지금 보이던 모습 그대로다."""
    open_panel(desktop)
    desktop.click("#btn-settings")
    desktop.wait_for_timeout(400)
    labels = desktop.locator("#set-fp-theme button").all_text_contents()
    assert any("화면과 같게" in l for l in labels) and any("라이트" in l for l in labels)
    cls = desktop.get_attribute("#file-panel", "class") or ""
    assert "pal-" not in cls                              # 기본값은 아무것도 덮지 않는다

    desktop.locator("#set-fp-theme button", has_text="라이트").click()
    desktop.wait_for_timeout(300)
    assert "pal-light" in (desktop.get_attribute("#file-panel", "class") or "")
    panel_bg = desktop.evaluate("getComputedStyle(document.getElementById('file-panel')).backgroundColor")
    term_bg = desktop.evaluate("getComputedStyle(document.getElementById('terminal')).backgroundColor")
    assert panel_bg != term_bg                            # 패널만 밝아진다 — 터미널은 그대로

    desktop.locator("#set-fp-theme button", has_text="화면과 같게").click()
    desktop.wait_for_timeout(300)
    assert "pal-" not in (desktop.get_attribute("#file-panel", "class") or "")


# ---------- 브라우저 패널(4번째 칸) — 이 프로젝트의 개발 서버 미리보기 ----------
BROWSER_SPLIT = '.splitter[data-pane="browser"]'


def open_browser(page, name="wiki"):
    """이름으로 연다 — 사이드바 순서는 세션 공용 가짜 브릿지의 즐겨찾기·숨김 상태에 따라 흔들린다."""
    page.click(f"#sessions li:has(b:text-is('{name}')) b")
    page.wait_for_selector(".xterm-helper-textarea", state="attached")
    page.wait_for_timeout(500)
    if page.is_hidden("#browser-panel"):      # 그 프로젝트에서 열어 뒀으면 이미 떠 있다(프로젝트별 기억)
        page.click("#browser-toggle")
    page.wait_for_selector("#browser-panel", state="visible")


def test_브라우저_버튼은_그_프로젝트의_서버가_하나면_바로_연다(desktop, bridge):
    """wiki(첫 줄)는 4321 하나 — 묻지 않고 연다. 주소는 Serve https(이 화면의 호스트 + 외부 포트)로 통일
    (localhost 는 폰에선 폰 자신이고 사파리는 https 안의 http://localhost iframe 을 막는다)."""
    bridge.preview_opens.clear()
    open_browser(desktop)
    desktop.wait_for_function("document.getElementById('bp-frame').getAttribute('src')")
    assert desktop.get_attribute("#bp-frame", "src") == "https://127.0.0.1:4321/"
    assert bridge.preview_opens == [{"id": "11:4321", "project": "wiki"}]
    assert desktop.input_value("#bp-url") == "https://127.0.0.1:4321/"
    assert desktop.is_visible(BROWSER_SPLIT)                       # 열린 칸 옆에 잡을 선
    assert desktop.is_visible("#file-panel")                       # 4칸이 들어가면 파일 패널은 그대로 산다
    assert width_of(desktop, "#term-view") >= 360                  # 터미널 하한은 지킨다
    # 안내는 판정이 아니다 — 3초 뒤 '새 탭' 출구만 띄우고 iframe 은 그대로 둔다
    desktop.wait_for_selector("#bp-note button", timeout=6000)
    assert "새 탭" in desktop.text_content("#bp-note button")
    assert desktop.get_attribute("#bp-frame", "src") == "https://127.0.0.1:4321/"
    desktop.click("#bp-close")
    assert not desktop.is_visible("#browser-panel") and not desktop.is_visible(BROWSER_SPLIT)


def test_브라우저_패널_분할선도_터미널_360을_남긴다(desktop):
    open_browser(desktop)
    drag(desktop, BROWSER_SPLIT, -900)                             # 왼쪽으로 한껏 끌어 넓혀도
    assert width_of(desktop, "#term-view") >= 360
    assert width_of(desktop, "#browser-panel") >= 320


def test_서버가_여럿이면_고르게_하고_숫자만_치면_그_포트를_연다(desktop, bridge):
    """mall(둘째 줄)은 3000·5173 둘 — 묻는다. 주소칸에 5173 만 치면 그 서비스로 연다(포트를 직접 내주지 않는다)."""
    bridge.preview_opens.clear()
    open_browser(desktop, "mall")
    desktop.wait_for_function("document.getElementById('bp-note').textContent.includes('여럿')")   # '읽는 중…' 다음
    assert bridge.preview_opens == []
    desktop.fill("#bp-url", "5173")
    desktop.press("#bp-url", "Enter")
    desktop.wait_for_function("document.getElementById('bp-frame').getAttribute('src')")
    assert desktop.get_attribute("#bp-frame", "src") == "https://127.0.0.1:5173/"
    assert bridge.preview_opens == [{"id": "13:5173", "project": "mall"}]
    desktop.fill("#bp-url", "4444")
    desktop.press("#bp-url", "Enter")
    assert "4444" in desktop.text_content("#bp-note")              # 없는 포트는 열지 않고 말한다


def test_폰에서_브라우저_패널은_전면이고_닫기로_터미널로_돌아온다(phone):
    open_browser(phone)
    box = phone.locator("#browser-panel").bounding_box()
    assert box["x"] == 0 and box["width"] == pytest.approx(412, abs=2)   # 전면 — 헤더(🌐)까지 덮는다
    assert box["y"] == 0 and box["height"] == pytest.approx(900, abs=2)  # 그래서 패널 자체의 ✕ 가 출구다
    phone.click("#bp-close")
    assert not phone.is_visible("#browser-panel")
    assert phone.is_visible("#terminal")


def test_브라우저_패널은_터미널을_갈아타면_그_프로젝트로_따라간다(desktop, bridge):
    bridge.preview_opens.clear()
    open_browser(desktop)                                          # wiki → 4321
    desktop.wait_for_function("document.getElementById('bp-frame').getAttribute('src')")
    open_browser(desktop, "mall")                                  # mall 로 — 거기서도 열면 그 프로젝트 것이 보인다
    desktop.wait_for_function("document.getElementById('bp-note').textContent.includes('여럿')")
    assert not desktop.get_attribute("#bp-frame", "src")           # 옛 프로젝트 화면을 남기지 않는다
    assert [o["project"] for o in bridge.preview_opens] == ["wiki"]


def test_HTTP에_답하지_않는_포트는_자동으로_열지_않는다(desktop, bridge):
    """blog 의 유일한 포트는 듣기만 한다(pytest 가짜 서버 같은 것) — 흰 화면을 자동으로 띄우지 않고 표시만 한다."""
    bridge.preview_opens.clear()
    open_browser(desktop, "blog")
    desktop.wait_for_selector("#bp-services option:nth-child(2)", state="attached")   # 목록이 그려진 뒤
    desktop.wait_for_function("document.getElementById('bp-note').textContent.includes('없습니다')")
    assert bridge.preview_opens == []
    assert not desktop.get_attribute("#bp-frame", "src")
    labels = desktop.eval_on_selector_all("#bp-services option", "os => os.map(o => o.textContent)")
    assert any(l.startswith("57697") and "HTTP 아님" in l for l in labels)


def test_주소칸에_검색어를_치면_호스트로_해석하지_않고_새_탭_검색(desktop, bridge):
    open_browser(desktop, "wiki")
    desktop.wait_for_function("document.getElementById('bp-frame').getAttribute('src')")
    with desktop.context.expect_page() as popup:
        desktop.fill("#bp-url", "네이버")
        desktop.press("#bp-url", "Enter")
    assert "google.com/search?q=" in popup.value.url
    popup.value.close()
    assert desktop.get_attribute("#bp-frame", "src") == "https://127.0.0.1:4321/"   # 미리보기는 그대로
    assert "검색" in desktop.text_content("#bp-note")


def test_브라우저_모드는_원격_크롬_화면을_그리고_주소칸_이동은_그_탭에_간다(desktop, bridge):
    """2단계: [브라우저] 를 누르면 원격 크롬 소켓에 붙어 프레임을 캔버스에 그리고, 주소칸 입력은 (검색어면 구글 검색으로)
    그 탭의 navigate 가 된다. 새로고침·뒤로도 그 탭 명령이다 — 바깥 페이지는 건드리지 않는다."""
    bridge.browser_cmds.clear()
    open_browser(desktop, "wiki")
    desktop.click("#bp-mode-browser")
    desktop.wait_for_selector("#rb-wrap", state="visible")
    desktop.wait_for_function("document.getElementById('rb-canvas').width === 1")       # 1×1 프레임을 그렸다
    assert desktop.is_hidden("#bp-frame") and desktop.is_visible("#bp-tabs")
    assert "탭하나" in desktop.text_content("#bp-tabs")
    desktop.fill("#bp-url", "네이버")
    desktop.press("#bp-url", "Enter")
    desktop.wait_for_function("document.getElementById('bp-url').value.includes('google.com/search')")
    cmds = [c for p, c in bridge.browser_cmds if p == "wiki"]
    vp = [c for c in cmds if c["type"] == "viewport"]
    assert vp and vp[0]["dpr"] >= 1 and vp[0]["w"] >= 320                             # 패널 크기·화면 배율을 먼저 알린다
    assert [c for c in cmds if c["type"] == "navigate"] == [{"type": "navigate", "url": "https://www.google.com/search?q=%EB%84%A4%EC%9D%B4%EB%B2%84"}]
    desktop.fill("#bp-url", "3000")
    desktop.press("#bp-url", "Enter")
    desktop.wait_for_function("document.getElementById('bp-url').value === 'http://localhost:3000/'")   # 맥의 개발 서버
    desktop.click("#bp-reload")
    desktop.click("#bp-back")
    desktop.wait_for_timeout(200)
    kinds = [c["type"] for p, c in bridge.browser_cmds if p == "wiki" and c["type"] != "viewport"]   # 크기 알림은 수시로 온다
    assert kinds[-2:] == ["reload", "back"]
    assert desktop.url.endswith("/")                                                     # 바깥 페이지는 새로고침되지 않았다
    desktop.reload()
    desktop.wait_for_selector("#sessions li")
    open_browser(desktop, "wiki")
    assert desktop.get_attribute("#browser-panel", "data-mode") == "browser"            # 모드는 프로젝트별로 기억


def test_브라우저_모드_캔버스_클릭과_글자는_원격_탭으로_간다(desktop, bridge):
    bridge.browser_cmds.clear()
    open_browser(desktop, "wiki")
    desktop.click("#bp-mode-browser")
    desktop.wait_for_function("document.getElementById('rb-canvas').width === 1")
    box = desktop.locator("#rb-canvas").bounding_box()
    desktop.mouse.click(box["x"] + box["width"] / 2, box["y"] + 10)
    desktop.keyboard.type("ab")
    desktop.keyboard.press("Enter")
    desktop.wait_for_timeout(300)
    cmds = [c for p, c in bridge.browser_cmds if p == "wiki"]
    press = [c for c in cmds if c["type"] == "mouse" and c["kind"] == "mousePressed"]
    # 좌표는 원격 CSS 뷰포트(가짜 state 의 800×600) 기준 — 캔버스 가로 중앙을 눌렀으니 x≈400, y 는 위쪽
    assert press and abs(press[0]["x"] - 400) <= 8 and 0 <= press[0]["y"] <= 40
    assert [c["text"] for c in cmds if c["type"] == "text"] == ["a", "b"]
    assert any(c["type"] == "key" and c["key"] == "Enter" and c["kind"] == "keyDown" for c in cmds)


def test_안_연_프로젝트는_패널이_저절로_붙어도_크롬을_띄우지_않는다(desktop, bridge):
    """연 것은 유지, 안 연 것은 열지 않는다 — 메모리 절약. 손으로 [브라우저 열기] 를 눌러야 뜬다."""
    bridge.browser_running.clear(); bridge.browser_cmds.clear()
    open_browser(desktop, "wiki")
    desktop.click("#bp-mode-browser")                                   # 손으로 누른 것 = 띄워도 된다
    desktop.wait_for_function("rb.frameMeta && rb.frameMeta.seq >= 1")
    assert bridge.browser_running == {"wiki"}
    bridge.browser_running.clear()                                      # 밖에서 그 크롬이 꺼졌다(⏻·재부팅 등)
    desktop.reload(); desktop.wait_for_selector("#sessions li")
    open_browser(desktop, "wiki")                                       # 저장된 모드는 browser — 저절로 붙는다
    desktop.wait_for_function("document.getElementById('bp-note').textContent.includes('꺼져 있습니다')")
    assert bridge.browser_running == set()                              # 붙기만 하고 띄우지는 않았다
    assert desktop.is_visible("#bp-note button")
    desktop.click("#bp-note button")                                    # [브라우저 열기] 를 손으로
    desktop.wait_for_function("rb.frameMeta && rb.frameMeta.seq >= 1")
    assert bridge.browser_running == {"wiki"}


def test_끄기_버튼은_그_프로젝트_브라우저만_종료한다(desktop, bridge):
    bridge.browser_running.clear()
    open_browser(desktop, "wiki")
    desktop.click("#bp-mode-browser")
    desktop.wait_for_function("rb.frameMeta && rb.frameMeta.seq >= 1")
    assert desktop.is_visible("#bp-power")                              # 브라우저 모드에서만 보인다
    desktop.click("#bp-power")
    desktop.wait_for_function("document.getElementById('bp-note').textContent.includes('꺼져 있습니다')")
    assert bridge.browser_stops == ["wiki"]
    desktop.click("#bp-mode-preview")
    assert desktop.is_hidden("#bp-power")


def test_브라우저_패널_열림은_프로젝트마다_따로_기억된다(desktop, bridge):
    """예전엔 wiki 에서 열었는데 mall 로 가도 열려 있고, 한쪽에서 끄면 다른 쪽도 꺼져 보였다.
    패널의 hidden 은 화면 하나뿐이라 전역이었다 — 이제 프로젝트마다 열림 여부를 기억한다."""
    bridge.browser_running.clear()
    open_browser(desktop, "wiki")                               # wiki 에서 🌐 로 연다
    assert desktop.is_visible("#browser-panel")
    desktop.click("#sessions li:has(b:text-is('mall')) b")      # mall 로 — 연 적 없으니 접혀야 한다
    desktop.wait_for_selector("#browser-panel", state="hidden")
    desktop.click("#sessions li:has(b:text-is('wiki')) b")      # 다시 wiki — 열어 뒀으니 되살아난다
    desktop.wait_for_selector("#browser-panel", state="visible")
    desktop.click("#browser-toggle")                            # mall 에서도 열어 둔다
    desktop.wait_for_selector("#browser-panel", state="hidden")  # (wiki 는 여기서 닫혔다)
    desktop.click("#sessions li:has(b:text-is('mall')) b")
    desktop.click("#browser-toggle")
    desktop.wait_for_selector("#browser-panel", state="visible")
    desktop.click("#sessions li:has(b:text-is('wiki')) b")      # wiki 는 닫아 뒀다 → 접힌 채
    desktop.wait_for_selector("#browser-panel", state="hidden")
    desktop.click("#sessions li:has(b:text-is('mall')) b")      # mall 은 열어 뒀다 → 열린 채
    desktop.wait_for_selector("#browser-panel", state="visible")
    desktop.reload(); desktop.wait_for_selector("#sessions li")  # 새로고침해도 기억한다
    desktop.click("#sessions li:has(b:text-is('mall')) b")
    desktop.wait_for_selector("#browser-panel", state="visible")
    desktop.click("#bp-close")                                   # ✕ 로 닫으면 mall 만 닫힌다
    desktop.wait_for_selector("#browser-panel", state="hidden")


# ---------- 다른 PC 터미널 배지 — ssh 로 다른 기계에 붙은 세션 ----------

def test_ssh_세션은_이름앞_host_배지_로컬세션은_그대로(desktop):
    """브릿지가 host 를 실어 주면 사이드바 줄 앞과 위 머리줄에 [host] 가 붙고, host 가 없는 맥 로컬 줄은
    배지 없이 지금 모양(이름 <b> 하나) 그대로다."""
    item = {"name": "srvpc", "alive": True, "path": None, "status": "on", "pending": 0,
            "host": "srvpc"}                         # tmux new -s srvpc "ssh -t srvpc"
    SESSIONS.append(item)
    try:
        desktop.evaluate("loadSessions()")
        desktop.wait_for_timeout(400)
        row = desktop.locator("#sessions li", has_text="srvpc")
        assert row.count() == 1
        badge = row.locator(".row-name .host")
        assert badge.text_content() == "[srvpc]"
        # 배지는 이름 앞 — 이름 칸의 첫 글자가 '[' 로 시작한다
        assert row.locator(".row-name").text_content().startswith("[srvpc]srvpc")
        assert row.locator("b").text_content() == "srvpc"          # 이름 <b> 는 온전히 이름만
        # 로컬 세션 줄(host 없음)에는 배지가 하나도 없다 — 회귀 방지
        assert desktop.locator("#sessions li .row-name .host").count() == 1
        # 열면 위 머리줄의 경로 자리에도 [host] 가 선다
        row.click()
        desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
        assert desktop.text_content("#hdr-name") == "srvpc"
        assert desktop.text_content("#hdr-path") == "[srvpc] 다른 PC 터미널 (ssh)"
        # 원격 폴더(@remote_path 또는 ssh 원격 명령의 cd)를 알면 그 경로가 줄 아래와 머리줄에 선다 —
        # "서버 PC 의 C:\\work 프로젝트"처럼 폴더까지 매칭돼 보여야 한다
        item["path"] = "C:\\rproj"
        desktop.evaluate("showList(); loadSessions()")
        desktop.wait_for_timeout(400)
        row = desktop.locator("#sessions li", has_text="srvpc")
        assert row.locator("small").text_content() == "C:\\rproj"
        row.click()
        desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
        assert desktop.text_content("#hdr-path") == "[srvpc] C:\\rproj"
    finally:
        SESSIONS.remove(item)
        desktop.evaluate("showList(); loadSessions()")


def test_켜기_버튼은_클로드_코덱스_안티_셋이고_회사색_클래스를_입는다(desktop, bridge):
    """꺼진 프로젝트를 누르면 '○○로 켜기' 가 세 개(클로드·코덱스·안티) 서고, 각 버튼은 회사 대표색을
    입히는 agent-* 클래스를 단다. '안티로 켜기' 는 ?agent=anti 로 붙는다."""
    row = row_of(desktop, 4)                                        # apex — 꺼짐
    row.locator("b").click()
    desktop.wait_for_timeout(300)
    btns = row.locator(".row-confirm button")
    labels = [btns.nth(i).text_content() for i in range(btns.count())]
    assert labels == ["클로드로 켜기", "코덱스로 켜기", "안티로 켜기", "취소"]
    assert row.locator(".row-confirm button.agent-claude").text_content() == "클로드로 켜기"
    assert row.locator(".row-confirm button.agent-codex").text_content() == "코덱스로 켜기"
    assert row.locator(".row-confirm button.agent-anti").text_content() == "안티로 켜기"
    # 세 버튼의 글자색이 서로 다르다 — 토큰(--claude·--gpt·--anti)이 실제로 먹는지
    colors = {cls: desktop.evaluate(
        f"getComputedStyle(document.querySelector('#sessions li .row-confirm button.{cls}')).color")
        for cls in ("agent-claude", "agent-codex", "agent-anti")}
    assert len(set(colors.values())) == 3, colors
    row.locator(".row-confirm button.agent-anti").click()
    desktop.wait_for_selector(".xterm-helper-textarea", state="attached")
    assert bridge.ws_agents[-1] == "anti"
    # 새 프로젝트 칸에도 같은 세 버튼·같은 클래스
    desktop.evaluate("showList()")
    new_btns = desktop.locator(".proj-new-acts button")
    assert [new_btns.nth(i).text_content() for i in range(new_btns.count())] == \
        ["클로드로 시작", "코덱스로 시작", "안티로 시작"]
    assert desktop.locator(".proj-new-acts button.agent-anti").get_attribute("id") == "proj-new-anti"


def test_안티_세션은_보라_점_코덱스는_파란_점(desktop):
    """브릿지가 agent 를 실어 주면 줄 앞의 점 색이 에이전트를 말한다 — 클로드(기본)·코덱스·안티가 셋 다 다르다."""
    anti = {"name": "landing", "alive": True, "path": "/tmp/landing", "status": "on", "pending": 0,
            "agent": "anti"}
    codex = {"name": "kakao", "alive": True, "path": "/tmp/kakao", "status": "on", "pending": 0,
             "agent": "codex"}
    SESSIONS.extend([anti, codex])
    try:
        desktop.evaluate("loadSessions()")
        desktop.wait_for_timeout(400)
        assert desktop.locator("#sessions li.anti", has_text="landing").count() == 1
        assert desktop.locator("#sessions li.codex", has_text="kakao").count() == 1
        dot = lambda name: desktop.evaluate(
            f"getComputedStyle([...document.querySelectorAll('#sessions li')]"
            f".find(li => li.dataset.name === '{name}').querySelector('em')).color")
        assert len({dot("landing"), dot("kakao"), dot("frog")}) == 3     # 보라·파랑·초록
    finally:
        SESSIONS.remove(anti); SESSIONS.remove(codex)
        desktop.evaluate("loadSessions()")
