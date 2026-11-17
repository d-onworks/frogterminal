"""폰 입력 회귀 테스트 — 헤드리스 크롬 + CDP 로 IME 조합·되끌어오기를 실제로 재현한다.

배경(2026-08-18): 삼성 예측입력은 확정 문장을 다음 조합 field로 되끌어온다. xterm의 textarea
diff는 그 문서를 새 입력으로 오인해 문장 전체를 재전송하므로, Android에서만 Orca mirror가
field와 이미 보낸 문서의 공통 접두부를 DEL+append로 맞춘다. 비Android는 기존 xterm 경로다.

playwright 가 없는 환경에서는 통째로 skip 되므로 bridge 단위 테스트에는 영향을 주지 않는다.
"""
import pytest

pytest.importorskip("playwright", reason="브라우저 회귀 테스트 — pip install playwright 필요")

from playwright.sync_api import sync_playwright     # noqa: E402

ENTER = "#keybar button:last-child"                 # ⏎
ESC, TAB, UP, DOWN, CTRL = (f"#keybar button:nth-child({n})" for n in (1, 2, 3, 4, 5))


@pytest.fixture
def term(bridge):
    """터미널 화면까지 연 폰(터치) 브라우저 — (page, cdp, bridge) 를 준다."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--ignore-certificate-errors"])
        ctx = browser.new_context(ignore_https_errors=True, has_touch=True, is_mobile=True,
                                  viewport={"width": 412, "height": 900})
        page = ctx.new_page()
        page.goto(bridge.url)
        page.click("#sessions li b")
        page.wait_for_selector(".xterm-helper-textarea", state="attached")
        page.wait_for_timeout(500)                  # WS 연결·초기 렌더
        page.eval_on_selector(".xterm-helper-textarea", "el => el.focus()")
        page.wait_for_timeout(100)
        bridge.received.clear()                     # 프롬프트·resize 이전 잡음 제거
        yield page, ctx.new_cdp_session(page), bridge
        browser.close()


@pytest.fixture
def android_term(bridge):
    """Android mirror를 URL 게이트로 강제한 폰 브라우저 — 실제 UA와 무관하게 새 경로를 고정한다."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--ignore-certificate-errors"])
        ctx = browser.new_context(ignore_https_errors=True, has_touch=True, is_mobile=True,
                                  viewport={"width": 412, "height": 900})
        page = ctx.new_page()
        page.goto(f"{bridge.url}?androidime=1")
        page.click("#sessions li b")
        page.wait_for_selector(".xterm-helper-textarea", state="attached")
        page.wait_for_timeout(500)
        page.eval_on_selector(".xterm-helper-textarea", "el => el.focus()")
        page.wait_for_timeout(100)
        bridge.received.clear()
        yield page, ctx.new_cdp_session(page), bridge
        browser.close()


def compose(page, cdp, text):
    """확정하지 않은 IME 조합 — 안드로이드 예측입력이 글자를 물고 있는 그 상태."""
    cdp.send("Input.imeSetComposition",
             {"text": text, "selectionStart": len(text), "selectionEnd": len(text)})
    page.wait_for_timeout(250)


def settle(page, ms=700):
    page.wait_for_timeout(ms)


def replace_composition(page, cdp, text, field_range=None):
    """삼성 setComposingRegion처럼 기존 field 구간을 새 조합으로 되끌어 바꾼다."""
    if field_range is not None:
        page.eval_on_selector(".xterm-helper-textarea", "(el, r) => el.setSelectionRange(r[0], r[1])",
                              list(field_range))
    cdp.send("Input.imeSetComposition",
             {"text": text, "selectionStart": len(text), "selectionEnd": len(text)})
    page.wait_for_timeout(40)                   # 300ms held 타이머 전, 조합 이벤트만 처리한다


def commit_composition(page, cdp, text):
    cdp.send("Input.insertText", {"text": text})
    page.wait_for_timeout(80)


def apply_del_payload(data):
    """PTY 입력줄의 DEL 의미를 코드포인트 단위로 적용해 최종 문서를 확인한다."""
    out = []
    for char in data.decode():
        if char == "\x7f":
            if out:
                out.pop()
        else:
            out.append(char)
    return "".join(out)


def rewrite_recomposition_before_compositionstart(page, text):
    """keydown 229 뒤 compositionstart 전 field 전체가 바뀌는 삼성 재조합 구간을 만든다.

    xterm CompositionHelper는 조합 밖 keyCode 229에서 현재 value를 저장하고 0ms 뒤 비교한다.
    그 사이 같은 길이의 문서로 바뀌면 suffix diff가 아니라 새 value 전체를 onData로 보낸다.
    insertCompositionText input은 xterm 자체 전송 경로가 아니므로 그 결함만 고립해 재현한다.
    """
    page.eval_on_selector(".xterm-helper-textarea", """(textarea, text) => {
        const keydown = new KeyboardEvent('keydown', { bubbles: true, key: 'Process' });
        Object.defineProperties(keydown, {
            keyCode: { get: () => 229 },
            which: { get: () => 229 },
        });
        textarea.dispatchEvent(keydown);             // old value를 동기 캡처, 비교는 0ms 뒤
        textarea.value = text;                       // 같은 태스크에서 삼성의 field 재조합
        textarea.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            data: text,
            inputType: 'insertCompositionText',
            isComposing: false,
        }));
    }""", text)
    page.wait_for_timeout(40)                        # _handleAnyTextareaChanges의 0ms 비교를 발화


def commit_through_xterm_then_blur(page, cdp, text):
    """실제 compositionend → xterm onData → blur 순서를 결정적으로 만든다.

    CDP 로 시작한 조합에 합성 CompositionEvent 만 던지면 브라우저의 실제 조합은 끝나지 않는다.
    그 상태에서 blur 하면 compositionend 가 한 번 더 와 테스트 자체가 글자를 중복 확정한다.
    그래서 Input.insertText 로 조합을 진짜 끝내고, 앱보다 나중에 등록한 onData 리스너가 앱의
    전송·차감 직후 blur 하게 한다. xterm onData 는 등록 순서대로 동기 발화한다.
    """
    page.evaluate("""text => {
        const textarea = document.querySelector('.xterm-helper-textarea');
        const once = term.onData(data => {
            if (data !== text) return;
            once.dispose();
            textarea.blur();                         // 앱 onData 리스너가 먼저 실행된 바로 다음 순서
        });
    }""", text)
    cdp.send("Input.insertText", {"text": text})     # 합성이 아닌 실제 compositionend 한 번


def commit_partial_through_xterm_then_blur(page, text, forwarded):
    """브라우저 조합 상태 없이 부분 onData 만 합성한다 — blur 의 두 번째 확정을 만들지 않는다."""
    page.evaluate("""({ text, forwarded }) => {
        const textarea = document.querySelector('.xterm-helper-textarea');
        textarea.dispatchEvent(new CompositionEvent('compositionend', { data: text, bubbles: true }));
        const once = term.onData(data => {
            if (data !== forwarded) return;
            once.dispose();
            textarea.blur();
        });
        term.input(forwarded, true);
    }""", {"text": text, "forwarded": forwarded})


def forward_during_composition_then_blur(page, text, forwarded="", *, start=True):
    """compositionstart → 조합 중 onData → compositionend → 별도 blur 순서를 합성한다."""
    forwarded = [forwarded] if isinstance(forwarded, str) and forwarded else forwarded
    page.evaluate("""({ text, forwarded, start }) => {
        const textarea = document.querySelector('.xterm-helper-textarea');
        if (start)
            textarea.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
        for (const data of forwarded) term.input(data, true);  // committed 가 아직 비었을 때 먼저 나간다
        textarea.dispatchEvent(new CompositionEvent('compositionend', { data: text, bubbles: true }));
        textarea.blur();                                  // compositionend 의 0ms 정리보다 같은 태스크에서 먼저
    }""", {"text": text, "forwarded": forwarded or [], "start": start})


# ---------- Android Orca mirror — 삼성 천지인 되끌어오기 ----------

def test_되끌어온_문장이_정확히_한번만_남는다(android_term):
    """old xterm의 keyCode 229 지연 diff가 되끌어온 문서 전체를 재전송하지 않아야 한다."""
    page, cdp, bridge = android_term
    original = "지금 키보드치고있어"
    rewritten = "지금 키보드치고있다"             # 원문과 같은 UTF-16 길이 — xterm의 전체값 분기
    replace_composition(page, cdp, original)
    commit_composition(page, cdp, original)
    assert page.eval_on_selector(".xterm-helper-textarea", "el => el.value") == original

    # 삼성 재조합 중 field 전체가 수정됐다가 원문으로 돌아오는 두 번의 update를 고립한다.
    # old app에서는 original + rewritten 전체 + original 전체가 실제 PTY로 나간다.
    rewrite_recomposition_before_compositionstart(page, rewritten)
    rewrite_recomposition_before_compositionstart(page, original)

    raw = bytes(bridge.received)
    # 이 assert를 먼저 둔다. 수정 전 실패 원인은 DEL 포맷 차이가 아니라 original 전체가 두 번
    # 들어간 진짜 중복이어야 한다(old raw: original + rewritten + original, count == 2).
    assert raw.decode().count(original) == 1, f"문장 전체 중복: {raw!r}"
    assert raw == original.encode() + b"\x7f" + "다".encode() + b"\x7f" + "어".encode()
    assert apply_del_payload(raw) == original


def test_되끌어온_글자를_고치면_코드포인트수만큼_DEL이_나간다(android_term):
    page, cdp, bridge = android_term
    replace_composition(page, cdp, "해봦")
    commit_composition(page, cdp, "해봦")
    replace_composition(page, cdp, "해보자", (0, 2))
    commit_composition(page, cdp, "해보자")
    settle(page, 350)
    # 공통 접두부 '해'는 유지한다 — 지울 것은 '봦' 한 코드포인트뿐이다.
    assert bytes(bridge.received) == "해봦".encode() + b"\x7f" + "보자".encode()
    assert apply_del_payload(bytes(bridge.received)) == "해보자"


def test_조합중_백스페이스는_field_diff로_DEL_하나만_만든다(android_term):
    page, cdp, bridge = android_term
    replace_composition(page, cdp, "안녕")
    page.wait_for_timeout(340)                 # 열린 조합의 held를 먼저 한 번 커밋
    replace_composition(page, cdp, "안")       # 조합 중 Backspace와 같은 field 축소
    commit_composition(page, cdp, "안")
    settle(page, 350)
    assert bytes(bridge.received) == "안녕".encode() + b"\x7f"
    assert apply_del_payload(bytes(bridge.received)) == "안"


def test_열린_조합은_300ms_정지뒤_held가_커밋된다(android_term):
    page, cdp, bridge = android_term
    replace_composition(page, cdp, "보류")
    page.wait_for_timeout(220)
    assert bytes(bridge.received) == b""
    page.wait_for_timeout(120)
    assert bytes(bridge.received) == "보류".encode()


def test_Enter는_held_CR_순서로_보내고_다음문서를_처음부터_연다(android_term):
    page, cdp, bridge = android_term
    replace_composition(page, cdp, "끝")
    page.keyboard.press("Enter")
    settle(page, 150)
    replace_composition(page, cdp, "다음")
    commit_composition(page, cdp, "다음")
    settle(page, 350)
    assert bytes(bridge.received) == "끝\r다음".encode()


def test_붙여넣기_직후_mirror가_새문서로_리셋된다(android_term):
    page, cdp, bridge = android_term
    replace_composition(page, cdp, "앞")
    commit_composition(page, cdp, "앞")
    page.evaluate("term.paste('붙임')")
    replace_composition(page, cdp, "뒤")
    commit_composition(page, cdp, "뒤")
    settle(page, 350)
    assert bytes(bridge.received) == "앞붙임뒤".encode()


def test_xterm_6_CompositionHelper_실제심볼_네개가_noop이다(android_term):
    page, _, _ = android_term
    assert page.evaluate("""() => {
        const h = term._core._compositionHelper;
        const names = ['compositionstart', 'compositionupdate', 'compositionend',
                       '_handleAnyTextareaChanges'];
        return names.every(n => typeof h[n] === 'function') && new Set(names.map(n => h[n])).size === 1;
    }""")


def test_조합_중_xterm이_아무것도_못보냈으면_blur가_전체를_보낸다(term):
    """삼성키보드 마지막 글자 누락 방지인 blur 보상은 그대로 살아 있어야 한다."""
    page, _, bridge = term
    text = "마지막 글자도 보낸다"
    forward_during_composition_then_blur(page, text)
    settle(page)
    assert bytes(bridge.received) == text.encode()


def test_xterm이_확정문자열을_보낸_뒤_blur가_와도_한번만_보낸다(term):
    """소프트키보드를 내리는 blur 는 xterm 정상 전송 뒤에도 온다 — 재전송하면 안 된다."""
    page, cdp, bridge = term
    compose(page, cdp, "안녕")
    commit_through_xterm_then_blur(page, cdp, "안녕")
    settle(page)
    assert bytes(bridge.received) == "안녕".encode()


def test_blur_focus_뒤_재타이핑해도_확정문자열을_중복하지_않는다(term):
    """실기 순서: 키보드를 내렸다 다시 올리고 계속 타이핑해도 커밋은 각각 한 번이다."""
    page, cdp, bridge = term
    compose(page, cdp, "처음")
    cdp.send("Input.insertText", {"text": "처음"})
    settle(page, 300)
    page.eval_on_selector(".xterm-helper-textarea", "el => el.blur()")
    page.eval_on_selector(".xterm-helper-textarea", "el => el.focus()")
    compose(page, cdp, "다시")
    commit_through_xterm_then_blur(page, cdp, "다시")
    settle(page)
    assert bytes(bridge.received) == "처음다시".encode()


def test_xterm이_확정문자열의_일부만_보냈으면_blur는_나머지만_보낸다(term):
    """단어 단위 커밋을 xterm 이 나눠 흘려도 이미 나간 접두부를 되풀이하지 않는다."""
    page, _, bridge = term
    commit_partial_through_xterm_then_blur(page, "안녕하세요", forwarded="안녕")
    settle(page)
    assert bytes(bridge.received) == "안녕하세요".encode()


@pytest.mark.parametrize("word", ["model", "안녕"])
def test_조합_중_키바_엔터는_글자를_먼저_보낸다(term, word):
    page, cdp, bridge = term
    page.keyboard.type("/")
    settle(page, 250)
    compose(page, cdp, word)                        # 확정 안 된 조합 — 화면엔 보이지만 미전송
    page.tap(ENTER)
    settle(page)
    assert bytes(bridge.received) == f"/{word}\r".encode()


def test_조합_중_다른_키바키도_글자를_먼저_보낸다(term):
    """⏎ 뿐 아니라 포커스를 뺏는 모든 키바 버튼에 같은 계약이 걸린다."""
    page, cdp, bridge = term
    compose(page, cdp, "abc")
    page.tap(TAB)
    settle(page)
    assert bytes(bridge.received) == b"abc\t"


# ---------- 회귀: 조합이 없을 때는 아무것도 달라지지 않는다 ----------

def test_조합_없는_일반_입력과_엔터(term):
    page, _, bridge = term
    page.keyboard.type("ls")
    settle(page, 250)
    page.tap(ENTER)
    settle(page)
    assert bytes(bridge.received) == b"ls\r"


def test_키바_특수키_시퀀스(term):
    page, _, bridge = term
    for sel in (ESC, TAB, UP, DOWN):
        page.tap(sel)
        settle(page, 200)
    assert bytes(bridge.received) == b"\x1b\t\x1b[A\x1b[B"


def test_ctrl_토글은_다음_한_글자를_제어문자로(term):
    page, _, bridge = term
    page.tap(CTRL)
    settle(page, 200)
    page.keyboard.type("c")
    settle(page)
    assert bytes(bridge.received) == b"\x03"


def test_커맨드_백스페이스는_줄을_통째로_지운다(term):
    """xterm 은 ⌘(meta) 조합을 브라우저 단축키로 보고 pty 로 아무것도 보내지 않는다 —
    ⌘C·⌘V 가 살아야 하니 옳은 기본값이지만, 그 바람에 맥에서 몸에 밴 ⌘+⌫(줄 통째 지우기)만
    웹 터미널에서 먹통이었다. 같은 뜻의 제어문자 Ctrl+U 로 옮겨 준다.
    정확히 한 바이트여야 한다 — xterm 까지 이 키를 처리하면 같은 것이 두 번 나간다.
    """
    page, _, bridge = term
    page.keyboard.type("rm -rf 잘못친명령")
    settle(page, 250)
    bridge.received.clear()                         # 지금부터 나가는 것만 본다
    page.keyboard.press("Meta+Backspace")
    settle(page)
    assert bytes(bridge.received) == b"\x15"


def test_커맨드_조합_나머지는_그대로_둔다(term):
    """⌘+⌫ 하나만 가로챈다 — ⌘C(복사) 같은 브라우저 기능까지 pty 로 새면 안 된다."""
    page, _, bridge = term
    page.keyboard.press("Meta+KeyC")
    settle(page, 300)
    assert bytes(bridge.received) == b""


def test_터치_스와이프는_휠_리포트로_스크롤한다(term):
    """직전 커밋(a311ad1)의 스와이프→휠 합성 회귀 — 마우스 리포트가 켜져 있어야 한다."""
    page, cdp, bridge = term
    box = page.locator("#terminal").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    cdp.send("Input.dispatchTouchEvent",
             {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for step in range(1, 7):                        # 아래로 쓸기 = 과거 화면(휠 위)
        cdp.send("Input.dispatchTouchEvent",
                 {"type": "touchMove", "touchPoints": [{"x": x, "y": y + step * 30}]})
        page.wait_for_timeout(30)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    settle(page, 400)
    assert b"\x1b[<64;" in bytes(bridge.received)   # SGR 휠 위 리포트


# ---------- 세션 이탈 잠금 — 단축키 한 번에 클로드가 멈추거나 세션이 죽지 않게 ----------

def test_Ctrl_Z_는_실행취소로_가고_Ctrl_D_는_잠긴다(term):
    """Ctrl+Z 를 그대로 보내면 클로드가 일시중단된다(fg 로만 복구 — 2026-08-15 실사고).
    사용자가 그 키로 기대하는 것은 '실행 취소'라, 클로드 코드·zsh 공통의 undo(Ctrl+_)로 옮긴다.
    Ctrl+D(EOF)는 세션을 통째로 닫으므로 계속 잠근다."""
    page, _, bridge = term
    page.keyboard.press("Control+z")
    settle(page)
    assert bytes(bridge.received) == b"\x1f"         # 일시중단(0x1a) 대신 실행취소(0x1f)
    bridge.received.clear()
    page.keyboard.press("Control+d")
    settle(page)
    assert bytes(bridge.received) == b""             # EOF 는 안 나간다
    assert page.is_visible("#copy-toast")            # 왜 안 나가는지 알려 준다


def test_커맨드_Z_도_실행취소다(term):
    """맥 사람이 실제로 누르는 키는 ⌘Z 다 — xterm 은 ⌘ 조합에 아무것도 안 보내므로 우리가 옮긴다."""
    page, _, bridge = term
    page.keyboard.press("Meta+z")
    settle(page)
    assert bytes(bridge.received) == b"\x1f"


def test_커맨드_방향키는_줄_처음과_끝으로_간다(term):
    """⌘←/⌘→ = 줄 처음·끝(맥 관례). 브라우저가 ⌘← 를 '뒤로 가기'로 먹어
    아무 일도 안 났다 — Home/End 와 같은 바이트로 옮긴다. 정확히 그 바이트만 나가야 한다."""
    page, _, bridge = term
    page.keyboard.type("echo hello")
    settle(page, 250)
    bridge.received.clear()
    page.keyboard.press("Meta+ArrowLeft")
    settle(page)
    assert bytes(bridge.received) == b"\x1b[H"          # Home — 그 외엔 아무것도 안 나간다
    bridge.received.clear()
    page.keyboard.press("Meta+ArrowRight")
    settle(page)
    assert bytes(bridge.received) == b"\x1b[F"          # End


def test_커서키_모드에서는_ESC_O_꼴로_보낸다(term):
    """DECCKM(\x1b[?1h)을 켠 프로그램은 Home/End 를 ESC O H/F 로 기대한다 — xterm 이 실제 Home 키에
    보내는 것과 똑같이 가른다. 어긋나면 글자가 찍히거나 아무 일도 안 난다."""
    page, _, bridge = term
    page.evaluate("term.write('\\x1b[?1h')")
    settle(page, 150)
    bridge.received.clear()
    page.keyboard.press("Meta+ArrowRight")
    settle(page)
    assert bytes(bridge.received) == b"\x1bOF"
    page.evaluate("term.write('\\x1b[?1l')")            # 되돌려 둔다


def test_ctrl_토글_z_조합도_실행취소로_간다(term):
    """폰 키바의 Ctrl 토글로 만든 Ctrl+Z 도 같은 길 — 일시중단은 어느 경로로도 안 나간다."""
    page, _, bridge = term
    page.tap(CTRL)
    settle(page, 200)
    page.keyboard.type("z")
    settle(page)
    assert bytes(bridge.received) == b"\x1f"


def test_ctrl_토글에_한글이_오면_제어문자로_만들지_않는다(term):
    """Ctrl 를 켜 둔 채 한글이 들어오면 & 0x1f 접기로 엉뚱한 제어문자(Ctrl+L 류)가 나가던
    사고 방지 — 비ASCII 는 글자 그대로 보낸다."""
    page, _, bridge = term
    page.tap(CTRL)
    settle(page, 200)
    page.keyboard.insert_text("ㅋ")
    settle(page)
    assert bytes(bridge.received) == "ㅋ".encode()


# ---------- Ctrl 복사·붙여넣기 — 윈도우 습관 지원 ----------
# 맥 크롬에서 Ctrl+C/V 는 복사·붙여넣기가 아니다(⌘ 만 됨) — "복사했는데 안 붙는다"의 정체.

def grant_clipboard(page, bridge):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=bridge.url)


def test_Ctrl_V_는_클립보드_글자를_붙여넣는다(term):
    page, _, bridge = term
    grant_clipboard(page, bridge)
    page.evaluate("navigator.clipboard.writeText('hello frog')")
    page.keyboard.press("Control+v")
    settle(page)
    assert b"hello frog" in bytes(bridge.received)


def test_Ctrl_C_는_선택이_있으면_복사_없으면_중단신호(term):
    page, _, bridge = term
    grant_clipboard(page, bridge)
    page.keyboard.type("abc")
    settle(page)
    bridge.received.clear()
    page.evaluate("term.select(0, 0, 5)")          # 화면 첫 줄 '$ abc' 선택
    page.keyboard.press("Control+c")
    settle(page)
    assert bytes(bridge.received) == b""           # 복사일 뿐 — 중단신호가 안 나간다
    assert page.evaluate("navigator.clipboard.readText()").strip().endswith("abc")
    page.keyboard.press("Control+c")               # 선택이 걷힌 뒤엔 원래대로 중단신호
    settle(page)
    assert bytes(bridge.received) == b"\x03"


def test_Ctrl_V_는_클립보드_이미지를_업로드하고_경로를_찍는다(term):
    page, _, bridge = term
    grant_clipboard(page, bridge)
    page.evaluate("""async () => {                    // 크롬은 진짜 PNG 만 클립보드에 받는다
        const c = document.createElement('canvas'); c.width = c.height = 2;
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]); }""")
    page.keyboard.press("Control+v")
    settle(page, 1200)
    assert bridge.uploads and bridge.uploads[-1]["project"] == "wiki"
    assert b"/tmp/_uploads/wiki/" in bytes(bridge.received)   # 경로가 터미널에 타이핑됐다


def test_링크는_데스크톱에선_Ctrl_클릭_폰에선_그냥_탭(term):
    """마우스에선 Zed·VS Code 방식(수정자 클릭만) — 손가락에는 수정자가 없으니 탭이 곧 열기다.

    갈림길은 포인터 종류 하나뿐이다(matchMedia "(pointer: coarse)"). 테스트 브라우저는 IME 때문에
    터치가 켜져 있어 저절로 '폰'으로 잡히므로, 두 세상을 matchMedia 를 갈아끼워 각각 못박는다.
    """
    page, _, bridge = term
    page.evaluate("window.open = u => { window._opened = u; }")
    page.keyboard.type("https://example.com/x")
    settle(page)
    box = page.locator(".xterm-screen").bounding_box()
    cell = page.evaluate("term._core._renderService.dimensions.css.cell")
    x = box["x"] + cell["width"] * 6                 # '$ ' 프롬프트 뒤 링크 글자 위
    y = box["y"] + cell["height"] * 0.5
    page.evaluate("window.matchMedia = () => ({ matches: false })")     # 데스크톱(정밀 포인터)
    page.keyboard.down("Control")
    page.mouse.click(x, y)
    page.keyboard.up("Control")
    settle(page, 300)
    assert page.evaluate("window._opened") == "https://example.com/x"
    page.evaluate("window._opened = null")
    page.mouse.click(x, y)                           # 수정자 없이 — 마우스에선 안 열린다
    settle(page, 300)
    assert page.evaluate("window._opened") is None
    page.evaluate("window.matchMedia = () => ({ matches: true })")      # 폰(거친 포인터)
    page.mouse.click(x, y)
    settle(page, 300)
    assert page.evaluate("window._opened") == "https://example.com/x"


# ---------- 한글 마지막 글자 미확정 문제 — "글자가 내려간다"의 방어선 ----------
# 맥 한글의 고전 문제: 마지막 글자가 조합 중(미확정)인 채 엔터를 치면, 터미널에 따라
# 엔터가 글자보다 먼저 나가 글자가 다음 줄로 떨어진다. 이 웹 터미널은 조합을 먼저
# 확정해 보낸다(xterm CompositionHelper) — 그 순서를 여기서 계약으로 못박는다.
# (실측 2026-08-15: '글자→엔터' 순서면 진짜 클로드도 온전히 받는다 — 버스트·분리 전송 모두.)

def test_조합_중_하드웨어_엔터도_글자를_먼저_보낸다(term):
    page, cdp, bridge = term
    compose(page, cdp, "안")                        # 확정 안 된 마지막 글자
    page.keyboard.press("Enter")
    settle(page)
    assert bytes(bridge.received) == "안\r".encode()   # 글자가 엔터보다 먼저 — 안 내려간다


def test_조합_중_화살표도_글자를_먼저_보낸다(term):
    page, cdp, bridge = term
    compose(page, cdp, "안")
    page.keyboard.press("ArrowDown")
    settle(page)
    assert bytes(bridge.received) == "안".encode() + b"\x1b[B"


def test_조합_중_시프트엔터는_글자를_지키고_줄바꿈은_다음_누름에(term):
    """★실사고 2건의 균형점(2026-08-15). ①조합 중 ⇧Enter 로 줄바꿈이 글자보다 먼저
    나가 글자가 다음 줄로 내려갔다 ②1차 처방(blur 강제확정)은 맥 IME 조합 연결을 죽여
    이후 타이핑 전체가 자모로 풀렸다. 최종 계약: 조합 중 ⇧Enter 는 삼킨다(글자 유실·역전
    금지) — IME 확정 뒤 다시 누르면 줄바꿈이 나간다. blur 는 절대 안 쓴다."""
    page, cdp, bridge = term
    compose(page, cdp, "면")                       # 확정 안 된 마지막 글자
    page.keyboard.press("Shift+Enter")             # 조합 중 — 삼켜진다
    settle(page, 300)
    assert bytes(bridge.received) == b""           # 줄바꿈이 글자를 앞지르지 않는다
    cdp.send("Input.insertText", {"text": "면"})   # IME 확정(조합 종료)
    settle(page, 300)
    page.keyboard.press("Shift+Enter")             # 확정 뒤 — 이제 줄바꿈
    settle(page)
    assert bytes(bridge.received) == "면".encode() + b"\\\r"   # 글자 먼저, 유실 없음


def test_파일_경로는_Ctrl_클릭으로_파일패널에_열린다(term):
    """터미널에 찍힌 경로(docs/노트.md)를 Ctrl+클릭하면 파일 패널이 그 파일을 보여준다."""
    page, _, bridge = term
    page.keyboard.type("docs/노트.md")
    settle(page)
    box = page.locator(".xterm-screen").bounding_box()
    cell = page.evaluate("term._core._renderService.dimensions.css.cell")
    x = box["x"] + cell["width"] * 4               # '$ ' 뒤 'docs' 글자 위
    y = box["y"] + cell["height"] * 0.5
    page.keyboard.down("Control")
    page.mouse.click(x, y)
    page.keyboard.up("Control")
    page.wait_for_selector("#fp-body pre", timeout=5000)
    assert page.text_content("#fp-body pre") == "wiki 메모"     # 그 프로젝트의 그 파일


def test_우클릭은_아무_메뉴도_안_띄우고_tmux_로도_안_간다(term):
    """우클릭에 브라우저 메뉴 + tmux 메뉴(Kill·Respawn 포함)가 겹쳐 떴다.
    우클릭은 양쪽 다 차단 — 마우스 리포트가 tmux 로 나가지 않는 것으로 확인한다."""
    page, _, bridge = term
    box = page.locator("#terminal").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    bridge.received.clear()
    page.mouse.click(x, y, button="right")
    settle(page, 300)
    assert b"\x1b[<2" not in bytes(bridge.received)      # 우클릭 리포트 없음
    bridge.received.clear()
    page.mouse.click(x, y)                               # 좌클릭 리포트는 그대로 살아 있다
    settle(page, 300)
    assert b"\x1b[<0" in bytes(bridge.received)


def test_다른_프로젝트에만_있는_경로는_맥_기본앱으로_넘긴다(term):
    """★실사고: 클로드가 말한 상대경로가 볼트 등 다른 프로젝트 기준이라 404 가 떴다 —
    전 프로젝트를 훑어 딱 한 곳에만 있으면 맥 기본 앱으로 연다."""
    page, _, bridge = term
    page.keyboard.type("mall.md")                        # wiki 엔 없고 mall 에만 있는 표식 파일
    settle(page)
    box = page.locator(".xterm-screen").bounding_box()
    cell = page.evaluate("term._core._renderService.dimensions.css.cell")
    x = box["x"] + cell["width"] * 4
    y = box["y"] + cell["height"] * 0.5
    page.keyboard.down("Control")
    page.mouse.click(x, y)
    page.keyboard.up("Control")
    settle(page, 1200)
    assert bridge.opens[-1] == {"project": "mall", "path": "mall.md", "app": "default"}


def test_캡처_직후_Ctrl_V_는_클립보드가_찰_때까지_재시도한다(term):
    """휠클릭 캡처는 셔터 뒤에야 클립보드에 이미지가 실린다 — 바로 붙여넣으면 빈 클립보드를
    읽고 조용히 실패했다. 최대 ~1.2초 재시도로 잡는다."""
    page, _, bridge = term
    grant_clipboard(page, bridge)
    bridge.uploads.clear()
    page.evaluate("navigator.clipboard.writeText('')")   # 캡처 직전 — 아직 빈 클립보드
    page.evaluate("""setTimeout(async () => {            // 400ms 뒤에야 이미지가 도착하는 상황
        const c = document.createElement('canvas'); c.width = c.height = 2;
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]); }, 400)""")
    page.keyboard.press("Control+v")                     # 이미지 도착 전에 누른다
    settle(page, 2500)
    assert bridge.uploads and bridge.uploads[-1]["name"].startswith("클립보드")


def test_휠클릭은_터미널로_새지_않는다(term):
    """휠클릭을 화면 캡처에 쓰는 환경이 있다. 터미널 위에서 눌러도 tmux 가운데버튼 붙여넣기(옛 버퍼)가
    끼어들지 않게 가운데버튼 리포트를 끊는다. 좌클릭 리포트는 그대로."""
    page, _, bridge = term
    box = page.locator("#terminal").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    bridge.received.clear()
    page.mouse.click(x, y, button="middle")
    settle(page, 300)
    assert b"\x1b[<1" not in bytes(bridge.received)      # 가운데버튼(SGR 1) 리포트 없음
    bridge.received.clear()
    page.mouse.click(x, y)
    settle(page, 300)
    assert b"\x1b[<0" in bytes(bridge.received)          # 좌클릭은 살아 있다


# ---------- 드래그 복사 → 이 기기 클립보드 ----------
# tmux 가 mouse on 이라 드래그는 tmux 선택이고 결과가 pbcopy(맥)로 간다 — 폰에서 긁으면
# 복사물이 폰이 아니라 맥으로 가 붙여넣기가 영영 안 됐다. 드래그 끝에 브라우저가 받아 온다.

def test_드래그가_끝나면_tmux_복사물이_이_기기_클립보드로(term):
    page, _, bridge = term
    grant_clipboard(page, bridge)
    bridge.copy_buffer = "긁어온 글자 42"
    box = page.locator("#terminal").bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 30, y)
    page.mouse.down()
    page.mouse.move(box["x"] + 200, y, steps=5)
    page.mouse.up()
    settle(page, 900)
    assert page.evaluate("navigator.clipboard.readText()") == "긁어온 글자 42"
    assert page.is_visible("#copy-toast")                # '복사됨' 알림


def test_새로_복사한_게_없으면_터미널을_눌러도_클립보드를_안_덮는다(term):
    """신고(2026-10-24): 휠클릭 캡처 직후 ⌘V 가 한 번에 안 들어갔다. 실기 기록 — ⌘V 1초 전에
    크롬이 클립보드에 '어제 복사한 글자'를 다시 써 넣고 있었다(Maccy 기록). 캡처 뒤 터미널을 누르다 손이
    조금(4픽셀 넘게) 움직이면 드래그로 보고, tmux 가 새로 복사한 게 없는데도 남아 있던 옛 버퍼를 써 넣어
    방금 찍은 그림을 덮었다. 버퍼가 이번 누름보다 먼저 있던 것이면 건드리지 않고, Ctrl+C 도 중단신호로 둔다."""
    page, _, bridge = term
    grant_clipboard(page, bridge)
    bridge.copy_buffer, bridge.copy_age = "어제 복사한 글자", 86400.0
    page.evaluate("""(async () => { const c = document.createElement('canvas'); c.width = c.height = 2;
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]); })()""")
    box = page.locator("#terminal").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 6, y, steps=2)                   # 누르다 손이 6픽셀 움직였다
    page.mouse.up()
    settle(page, 900)
    types = page.evaluate("(async () => (await navigator.clipboard.read()).flatMap(i => i.types))()")
    assert "image/png" in types                          # 캡처한 그림이 그대로
    assert not page.is_visible("#copy-toast")
    bridge.received.clear()
    page.keyboard.press("Control+c")
    settle(page, 400)
    assert bytes(bridge.received) == b"\x03"             # 복사가 아니었으니 Ctrl+C 는 중단신호
    bridge.copy_age = 0.3                                # 이번 드래그로 막 만든 버퍼는 그대로 복사한다
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 200, y, steps=5)
    page.mouse.up()
    settle(page, 900)
    assert page.evaluate("navigator.clipboard.readText()") == "어제 복사한 글자"


def test_드래그_직후_Ctrl_C_는_복사이고_평소엔_중단신호(term):
    """★사고의 핵심: tmux 선택은 브라우저에 '선택 없음'으로 보여, Ctrl+C 가 복사 대신
    SIGINT 로 나가 클로드를 끊어 먹었다. 방금 드래그했으면 복사, 아니면 원래대로 중단."""
    page, _, bridge = term
    grant_clipboard(page, bridge)
    bridge.copy_buffer = "선택한 명령어"
    box = page.locator("#terminal").bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 30, y)
    page.mouse.down()
    page.mouse.move(box["x"] + 200, y, steps=5)
    page.mouse.up()
    settle(page, 900)
    bridge.received.clear()
    page.keyboard.press("Control+c")                     # 드래그 직후 — 복사여야 한다
    settle(page, 600)
    assert bytes(bridge.received) == b""                 # 클로드를 끊지 않는다
    assert page.evaluate("navigator.clipboard.readText()") == "선택한 명령어"
    page.evaluate("lastCopyAt = 0")                      # 시간이 흐른 상태
    page.keyboard.press("Control+c")
    settle(page, 400)
    assert bytes(bridge.received) == b"\x03"             # 평소 Ctrl+C 는 그대로 중단신호


def test_캡처_붙여넣기는_원본_경로를_넣고_사본을_안_만든다(term):
    """캡처를 붙여넣을 때마다 _uploads 에 사본이 쌓였다 — 맥 캡처는 파일로도
    남으니 같은 크기의 최신 캡처를 찾아 그 경로만 넣는다."""
    page, _, bridge = term
    grant_clipboard(page, bridge)
    bridge.uploads.clear()
    bridge.captures.clear()
    bridge.capture_path = "/Users/x/캡처폴더/스크린샷 2026-08-15.png"
    page.evaluate("""async () => {
        const c = document.createElement('canvas'); c.width = 12; c.height = 7;
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]); }""")
    page.keyboard.press("Control+v")
    settle(page, 1500)
    assert bridge.captures[-1] == {"w": "12", "h": "7"}      # 픽셀 크기로 대조한다
    assert not bridge.uploads                                # 사본을 안 만든다
    assert "'/Users/x/캡처폴더/스크린샷 2026-08-15.png' ".encode() in bytes(bridge.received)


def test_캡처_원본을_못_찾으면_예전처럼_업로드한다(term):
    page, _, bridge = term
    grant_clipboard(page, bridge)
    bridge.uploads.clear()
    bridge.capture_path = ""                                 # 못 찾음
    page.evaluate("""async () => {
        const c = document.createElement('canvas'); c.width = c.height = 3;
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]); }""")
    page.keyboard.press("Control+v")
    settle(page, 1500)
    assert bridge.uploads and bridge.uploads[-1]["name"].startswith("클립보드")
