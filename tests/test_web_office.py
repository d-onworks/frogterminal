"""3D 모션 모드(사무실 화면) — 켜고 끄기·캐릭터·꼬마·결재 문구·캐릭터 눌러 터미널로.

가짜 브릿지의 /api/office(conftest.office_people)를 쓴다: wiki = 결재 대기, mall = 일하는 중 + 서브에이전트
셋, frog = 쉬는 중, blog = 커밋 적은 저장소(꼬마). 그림은 캔버스라 DOM 으로 못 보므로, 화면이 내주는
officeDebug()(누가 어디서 무엇을 하는지 + 화면 좌표)로 확인하고, 누르기는 그 좌표를 실제로 누른다.
"""
import pytest
from conftest import office_people

pytest.importorskip("playwright", reason="브라우저 회귀 테스트 — pip install playwright 필요")

from playwright.sync_api import sync_playwright     # noqa: E402

ONE_PANE = """try { if (!sessionStorage.getItem("frog.test.grid")) {
  localStorage.setItem("frog.grid", JSON.stringify({ n: 1, names: [] }));
  localStorage.removeItem("frog.office");
  sessionStorage.setItem("frog.test.grid", "1"); } } catch (e) {}"""


def _page(pw, bridge, **ctx):
    browser = pw.chromium.launch(args=["--ignore-certificate-errors"])
    context = browser.new_context(ignore_https_errors=True, **ctx)
    context.add_init_script(ONE_PANE)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(bridge.url)
    page.wait_for_selector("#sessions li")
    page.errors = errors
    return browser, page


@pytest.fixture
def desktop(bridge):
    bridge.office = office_people()
    with sync_playwright() as pw:
        browser, page = _page(pw, bridge, viewport={"width": 1280, "height": 800})
        yield page
        assert page.errors == [], page.errors           # 캔버스 그리기에서 난 오류도 여기서 걸린다
        browser.close()
    bridge.office = office_people()


@pytest.fixture
def phone(bridge):
    bridge.office = office_people()
    with sync_playwright() as pw:
        browser, page = _page(pw, bridge, has_touch=True, is_mobile=True,
                              viewport={"width": 412, "height": 900})
        yield page
        browser.close()


def open_office(page):
    page.click("#btn-office")
    page.wait_for_selector("#office-view", state="visible")
    page.wait_for_function("officeDebug().length === 4 && officeDebug().every(p => p.at)")


def who(page, name):
    return next(p for p in page.evaluate("officeDebug()") if p["name"] == name)


def test_3D모션_버튼은_결재_버튼_바로_옆이고_누르면_가운데가_사무실이_된다(desktop):
    page = desktop
    assert page.evaluate("document.getElementById('btn-office').previousElementSibling.id") == "sum-pending"
    assert page.inner_text("#btn-office").strip().endswith("3D 사무실")
    open_office(page)
    assert page.is_visible("#list-view")                # 왼쪽 목록은 그대로(요청: 왼쪽 목록 + 오른쪽 사무실)
    assert page.is_hidden("#term-view")
    assert page.get_attribute("#btn-office", "aria-pressed") == "true"
    bar = page.inner_text("#office-bar")
    assert "일하는 중 1 · 결재 대기 1 · 쉬는 중 2 · 서브에이전트 3" in bar
    assert "캐릭터를 누르면 그 터미널로" in bar
    box = page.locator("#office-canvas").bounding_box()
    stage = page.locator("#office-stage").bounding_box()   # 목록과 오른쪽 패널 사이를 꽉 채운다
    assert box["width"] > 600 and abs(box["height"] - stage["height"]) < 2
    assert page.is_visible("#office-side")              # 오른쪽 = 사무실 사람들 + 활동 로그


def test_캐릭터는_상태대로_자리를_잡고_커밋_적은_저장소는_꼬마(desktop):
    page = desktop
    open_office(page)
    mall, wiki, frog, blog = (who(page, n) for n in ("mall", "wiki", "frog", "blog"))
    assert mall["act"] == "desk"                        # 일하는 중 = 모니터 앞에 앉아 코딩
    assert wiki["act"] == "ask"                         # 결재 대기 = 책상 앞에서 폴짝
    assert frog["act"] not in ("desk", "ask") and blog["act"] not in ("desk", "ask")   # 쉬는 중
    assert blog["kid"] is True and mall["kid"] is False
    assert mall["minis"] == 3                           # 서브에이전트 셋 = 꼬마 셋이 줄 선다
    for p in (mall, wiki, frog, blog):
        assert p["species"] in ("frog", "capy")
    # 색은 프로젝트 이름으로 고정 — 다시 켜도 같은 색(사이드바와 사무실을 오가도 헷갈리지 않게)
    colors = {p["name"]: p["color"] for p in page.evaluate("officeDebug()")}
    page.click("#btn-office")
    open_office(page)
    assert {p["name"]: p["color"] for p in page.evaluate("officeDebug()")} == colors


def test_결재_캐릭터를_누르면_그_프로젝트_터미널로_간다(desktop):
    page = desktop
    open_office(page)
    x, y = who(page, "wiki")["at"]
    box = page.locator("#office-canvas").bounding_box()
    page.mouse.click(box["x"] + x, box["y"] + y)
    page.wait_for_selector("#term-view", state="visible")
    assert page.inner_text("#hdr-name") == "wiki"
    assert page.is_hidden("#office-view")
    assert page.get_attribute("#btn-office", "aria-pressed") == "false"


def test_꼬마를_눌러도_주인_프로젝트로_가고_빈_바닥은_그대로(desktop):
    page = desktop
    open_office(page)
    box = page.locator("#office-canvas").bounding_box()
    page.mouse.click(box["x"] + 6, box["y"] + box["height"] - 6)     # 그림 바깥 여백 — 아무도 없다
    page.wait_for_timeout(300)
    assert page.is_visible("#office-view")
    page.wait_for_function("officeDebug().find(p => p.name === 'mall').minisAt.length === 3")
    x, y = who(page, "mall")["minisAt"][-1]             # 줄 맨 끝 꼬마(주인과 안 겹치는 자리)
    page.mouse.click(box["x"] + x, box["y"] + y)
    page.wait_for_selector("#term-view", state="visible")
    assert page.inner_text("#hdr-name") == "mall"


def test_서브에이전트가_끝나면_꼬마가_줄에서_빠져_퇴근한다(desktop, bridge):
    page = desktop
    open_office(page)
    assert who(page, "mall")["minis"] == 3
    people = office_people()
    people[1]["subs"] = people[1]["subs"][:1]
    people[1]["subCount"] = 1
    bridge.office = people
    page.wait_for_function("officeDebug().find(p => p.name === 'mall').minis === 1", timeout=8000)


def test_결재가_풀려_일을_시작하면_책상에_앉는다(desktop, bridge):
    page = desktop
    open_office(page)
    people = office_people()
    people[0].update(status="working", pending=0, ask="")
    bridge.office = people
    page.wait_for_function("officeDebug().find(p => p.name === 'wiki').act === 'desk'", timeout=10000)


def test_사이드바를_누르면_사무실을_접고_터미널이_열린다(desktop):
    page = desktop
    open_office(page)
    page.click("#sessions li:has-text('frog') b")
    page.wait_for_selector("#term-view", state="visible")
    assert page.is_hidden("#office-view")
    assert page.inner_text("#hdr-name") == "frog"


def test_끄면_빈_가운데로_돌아가고_폴링도_멈춘다(desktop, bridge):
    page = desktop
    open_office(page)
    page.click("#btn-office")
    assert page.is_hidden("#office-view")
    assert "idle" in page.get_attribute("#main", "class")   # 열린 터미널이 없으면 처음 화면
    calls = bridge.office_calls
    page.wait_for_timeout(5000)
    assert bridge.office_calls == calls                 # 안 보는 사무실은 데이터를 안 읽는다


def test_새로고침해도_켜_둔_사무실이_그대로(desktop):
    page = desktop
    open_office(page)
    page.reload()
    page.wait_for_selector("#office-view", state="visible")
    page.wait_for_function("officeDebug().length === 4")
    assert page.is_hidden("#term-view")


def test_폰에서는_사무실이_전면이고_다시_누르면_목록으로(phone):
    page = phone
    open_office(page)
    assert page.is_hidden("#list-view")
    assert page.locator("#btn-office").is_visible()     # 좁은 헤더에서도 버튼은 남는다(글자만 접힌다)
    page.click("#btn-office")
    page.wait_for_selector("#list-view", state="visible")
    assert page.is_hidden("#office-view")


def test_오른쪽_패널은_사람들_상태와_하는_일_꼬마_수를_보여준다(desktop):
    page = desktop
    open_office(page)
    page.wait_for_function("document.querySelectorAll('#office-people li').length === 4")
    rows = page.locator("#office-people li")
    assert "결재 대기" in rows.nth(0).inner_text() and "wiki" in rows.nth(0).inner_text()   # 결재가 맨 위
    mall = page.locator("#office-people li:has-text('mall')").inner_text()
    assert "일하는 중" in mall and "꼬마 3" in mall and "office-view.js 읽는 중" in mall


def test_활동_로그는_흘러_들어오고_출근_퇴근도_적힌다(desktop, bridge):
    page = desktop
    open_office(page)
    page.wait_for_function("document.querySelectorAll('#office-log li').length >= 2")
    assert "office-view.js 읽는 중" in page.inner_text("#office-log")
    people = office_people()
    people[1]["subs"], people[1]["subCount"] = [], 0           # mall 의 꼬마 셋이 일을 끝냈다
    bridge.office = people
    page.wait_for_function("document.getElementById('office-log').innerText.includes('꼬마 퇴근')", timeout=10000)


def test_본체가_쉬어도_꼬마가_일하면_책상에_앉아_일한다(desktop, bridge):
    page = desktop
    open_office(page)
    people = office_people()
    people[2].update(subs=[{"id": "f1", "type": "Plan", "desc": "계획", "doing": ""}], subCount=1)   # frog = done
    bridge.office = people
    # 소파·탕비실에서 책상까지 걸어온다 — 걸음을 36픽셀/초로 늦춘 뒤(2026-10-22) 7~28초가 걸려 30초는 빠듯했다
    page.wait_for_function("officeDebug().find(p => p.name === 'frog').act === 'desk'", timeout=60000)


def test_캐릭터를_누르면_사무실이_가운데로_빨려_들어가며_터미널이_펼쳐진다(desktop):
    page = desktop
    open_office(page)
    x, y = who(page, "wiki")["at"]
    box = page.locator("#office-canvas").bounding_box()
    page.mouse.click(box["x"] + x, box["y"] + y)
    page.wait_for_selector("#office-fx", state="attached", timeout=2000)   # 지니 효과 막(잠깐만 있다)
    assert page.is_visible("#office-view")              # 빨려 드는 동안은 사무실이 그대로 — 끝나고 바꾼다(깜빡임 방지)
    page.wait_for_function("document.getElementById('term-view').classList.contains('genie-in')", timeout=3000)
    page.wait_for_selector("#office-fx", state="detached", timeout=3000)
    assert page.inner_text("#hdr-name") == "wiki"
    assert page.is_hidden("#office-view")


def test_3D모션_동안은_화면_전체가_흰_테마고_끄면_원래_테마로(desktop):
    page = desktop
    before = page.evaluate("document.documentElement.className")
    open_office(page)
    assert "light" in page.evaluate("document.documentElement.className")
    assert page.evaluate("getComputedStyle(document.getElementById('list-view')).backgroundColor") != "rgb(16, 19, 18)"
    page.click("#btn-office")
    assert page.evaluate("document.documentElement.className") == before


def test_벽과_가구는_뚫고_지나가지_않는다(desktop):
    """길 그래프·자리 진입선이 벽(유리벽)·가구(탁자)를 가로지르면 안 된다 — 지도를 고칠 때마다 여기서 잡힌다."""
    page = desktop
    bad = page.evaluate("""() => {
      const out = [], B = OfficeLife.blocked, F = OfficeLife.onFloor, N = OFFICE_NODES;
      for (const [k, p] of Object.entries(N)) if (!F(p)) out.push('바닥 밖 ' + k);
      for (const [a, b] of OFFICE_EDGES) if (B(N[a], N[b])) out.push('벽 넘는 길 ' + a + '-' + b);
      for (const [kind, list] of Object.entries(OFFICE_SPOTS)) list.forEach((s, i) => {
        const at = [s.at[0], s.at[1] + (/sofa|cafe/.test(kind) ? 16 : kind === 'meeting' ? 18 : 0)];
        if (!F(at) || B(N[s.via], at)) out.push('못 가는 자리 ' + kind + i);
      });
      OFFICE_DESKS.forEach((d, i) => { if (B(N[d.via], [d.seat[0], d.seat[1] + 18])) out.push('못 가는 책상 ' + (i + 1)); });
      return out;
    }""")
    assert bad == []


def test_어른은_고른_걸음으로_걸은_거리만큼_걷기_그림_8장을_넘긴다(desktop):
    """요청(2026-10-22) "통통 튀는 거 말고 사무실을 저벅저벅": 어른 걷기는 블렌더로 뽑은 한 걸음 8장(앞
    stepF·뒤 stepB)이고, 장은 시간이 아니라 걸은 거리로 넘긴다 — 발이 바닥에서 안 미끄러진다. 걸음 속도도 고르다
    (예전엔 한 걸음마다 0.7~1.3배로 빨라졌다 느려졌다 하며 통통 튀었다)."""
    page = desktop
    open_office(page)
    r = page.evaluate("""() => new Promise(done => {
      const L = OfficeLife.L, p = [...L.people.values()].find(q => !q.kid);
      const far = Object.values(OFFICE_NODES).sort((a, b) => Math.hypot(b[0] - p.x, b[1] - p.y) - Math.hypot(a[0] - p.x, a[1] - p.y))[0];
      p.steps = null; p.act = null; p.over = null; p.until = Infinity;
      p.path = OfficeLife.route([p.x, p.y], far);
      const out = [], t0 = performance.now();
      const tick = () => {
        const d = officeDebug().find(q => q.name === p.name);
        out.push({ pose: d.pose, dist: p.dist || 0, t: L.t });
        if (performance.now() - t0 < 1500 && p.path.length) requestAnimationFrame(tick); else done(out);
      };
      requestAnimationFrame(tick);
    })""")
    poses = [s["pose"] for s in r]
    assert all(p and p[:5] in ("stepF", "stepB") and p[5] in "01234567" for p in poses), poses
    assert len(set(p[5] for p in poses)) == 8                    # 1.5초면 한 걸음(8장)을 다 돈다
    speeds = [(b["dist"] - a["dist"]) / (b["t"] - a["t"]) for a, b in zip(r, r[1:]) if b["t"] - a["t"] > 0.02]
    assert speeds and max(speeds) < 36 * 1.05, speeds          # 빨라지는 박자가 없다 = 안 튄다


def test_앉는_자리는_의자_등받이에서_잰_값과_같다(desktop):
    """요청(2026-10-22) "의자에 앉을 때 등받이가 캐릭터를 가리게": 좌석 = 등받이 아래끝 기준으로 그림 만들 때
    잰 값(office-atlas.js chairs). 지도의 좌석 숫자를 손으로 바꾸면 등받이와 어긋난다 — 여기서 잡는다."""
    got = desktop.evaluate("""() => ({
      chairs: OFFICE_ATLAS.chairs.map(c => c.seat),
      desks: OFFICE_DESKS.map(d => d.seat),
      meet: [...OFFICE_SPOTS.meeting, ...OFFICE_OVERFLOW].map(s => [s.chair, s.at || s.seat]),
    })""")
    assert len(got["chairs"]) == len(got["desks"]) + 3          # 책상 11 + 회의실 앞줄 3
    assert got["desks"] == got["chairs"][:len(got["desks"])]
    for chair, seat in got["meet"]:
        assert seat == got["chairs"][chair], (chair, seat)


def _alone_at_chair(page, chair, pose="sitBack"):
    """한 사람만 그 의자에 앉히고 나머지는 화면 밖으로 — 그리기 결과를 픽셀로 비교하려고."""
    return page.evaluate("""([chair, pose]) => {
      const L = OfficeLife.L, c = OFFICE_ATLAS.chairs[chair];
      for (const m of L.minis.values()) { m.x = -900; m.y = -900; m.path = []; }
      [...L.people.values()].forEach((p, i) => {
        Object.assign(p, { over: null, path: [], until: 1e9, steps: null, bubble: null, nextSay: 1e9, kid: false });
        if (i) { p.x = -900; p.y = -900; p.act = { pose: 'stand', dur: 1e9 }; return; }
        p.x = c.seat[0]; p.y = c.seat[1] + OfficeLife.SIT_DESK; p.act = { pose, dur: 1e9, chair };
      });
      return c.box;
    }""", [chair, pose])


def _pixels(page, pts):
    return page.evaluate("""(pts) => {
      const cv = document.getElementById('office-canvas'), ctx = cv.getContext('2d');
      const dpr = cv.width / cv.getBoundingClientRect().width;
      return pts.map(([x, y]) => { const [sx, sy] = officeDebug.scr(x, y);
        return [...ctx.getImageData(Math.round(sx * dpr), Math.round(sy * dpr), 1, 1).data]; });
    }""", pts)


@pytest.mark.parametrize("chair", [5, 12])               # 책상 의자 하나, 회의실 앞줄 의자 하나
def test_앉은_사람의_허리_아래는_의자_등받이가_가린다(desktop, chair):
    page = desktop
    open_office(page)
    x, y, w, h = _alone_at_chair(page, chair)
    page.wait_for_timeout(300)
    seat = page.evaluate(f"OFFICE_ATLAS.chairs[{chair}].seat")
    back = [(seat[0], y + h * 0.62), (seat[0] - 6, y + h * 0.7)]          # 등받이 아래쪽 — 엉덩이·허리 자리
    head = [(seat[0], y - 12)]                                            # 등받이 위 — 머리·어깨 자리
    with_person = _pixels(page, back + head)
    page.evaluate("for (const p of OfficeLife.L.people.values()) { p.x = -900; p.y = -900; }")
    page.wait_for_timeout(300)
    empty = _pixels(page, back + head)
    assert with_person[:2] == empty[:2]                   # 허리 아래는 등받이에 가려 빈 의자와 똑같이 보인다
    assert with_person[2] != empty[2]                     # 그래도 머리는 등받이 위로 보인다


SIM = r"""([minutes, seed]) => {
  let a = seed >>> 0;                                   // 같은 결과가 나오게 난수 고정(mulberry32)
  Math.random = () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const O = OfficeLife, L = O.L;
  const names = ["server", "shorts", "frogterminal", "kakao", "video", "landing", "notes",
                 "obsidian", "blender", "wiki", "mall", "frog", "blog"];
  const roll = () => names.map((n, i) => {
    const r = Math.random(), st = r < 0.35 ? "working" : r < 0.45 ? "decision" : "done";
    const subs = st === "working" && Math.random() < 0.4 ? 1 + Math.floor(Math.random() * 3) : 0;
    return { name: n, agent: "claude", status: st, pending: st === "decision" ? 1 : 0, ask: st === "decision" ? "확인" : "",
      commits: i % 3 ? 300 : 20, kid: i % 3 === 0, subCount: subs, path: "/tmp/" + n, host: null,
      subs: Array.from({ length: subs }, (_, k) => ({ id: `${n}-${k}-${Math.floor(L.t / 40)}`, type: "Explore", desc: "조사" })) };
  });
  for (const k of [...L.people.keys()]) L.people.delete(k);
  for (const k of [...L.minis.keys()]) L.minis.delete(k);
  L.desks.clear(); L.taken.clear(); L.chats.clear(); L.synced = false; L.t = 0;
  O.sync(roll());
  const EDGES = [...OFFICE_WALLS, ...OFFICE_BLOCKS.flatMap(poly => poly.map((pt, i) => [pt, poly[(i + 1) % poly.length]]))];
  const near = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]) < 3;
  // 한 프레임에 걷기·밀어내기 두 번 움직여 벽 끝 모서리를 돌아간 것은 뚫은 게 아니다
  const crossed = (p, q) => O.blocked(p, q) && !EDGES.some(([a, b]) => near(p, a) || near(p, b) || near(q, a) || near(q, b));
  const dt = 1 / 30, out = { offFloor: [], wall: [], stuck: [], overlap: [], poses: {} };
  const still = new Map(), over = new Map();
  for (let i = 0, n = Math.round(minutes * 60 / dt); i < n; i++) {
    if (i && i % Math.round(45 / dt) === 0) O.sync(roll());
    const ents = [...L.people.values(), ...L.minis.values()];
    const prev = new Map(ents.map(e => [e, [e.x, e.y]]));
    O.update(dt);
    for (const e of [...L.people.values(), ...L.minis.values()]) {
      const vis = e.alpha > 0.95 && !e.gone && !e.leaving && e.state !== "out" && e.state !== "bye";
      const p0 = prev.get(e), who = e.name || e.owner + "의 꼬마";
      if (!Number.isFinite(e.x) || !Number.isFinite(e.y)) out.offFloor.push(who + " NaN");
      if (vis && !O.onFloor([e.x, e.y])) out.offFloor.push(`${who} ${L.t.toFixed(1)}s`);
      if (vis && p0 && crossed(p0, [e.x, e.y])) out.wall.push(`${who} ${L.t.toFixed(1)}s`);
      if (e.owner) continue;
      const pose = e.over && L.t < e.over.until ? e.over.pose : e.act?.pose;
      if (pose) out.poses[pose] = (out.poses[pose] || 0) + 1;
      if (!e.path.length) { still.delete(e); continue; }       // 걷는 중인데 4초 넘게 6픽셀도 못 가면 끼인 것
      const s = still.get(e);
      if (!s || Math.hypot(e.x - s.x, e.y - s.y) > 6) still.set(e, { x: e.x, y: e.y, t: L.t });
      else if (L.t - s.t > 4) { out.stuck.push(`${who} ${L.t.toFixed(1)}s (${Math.round(e.x)},${Math.round(e.y)})`); still.set(e, { x: e.x, y: e.y, t: L.t }); }
    }
    const ppl = [...L.people.values()].filter(p => p.alpha > 0.95 && !p.gone);
    for (let x = 0; x < ppl.length; x++) for (let y = x + 1; y < ppl.length; y++) {
      const p = ppl[x], q = ppl[y], key = p.name + "·" + q.name;
      if (Math.abs(p.x - q.x) < 16 && Math.abs(p.y - q.y) < 7) {        // 3초 넘게 한 자리에 겹쳐 있으면 문제
        const s = over.get(key) ?? L.t; over.set(key, s);
        if (L.t - s > 3) { out.overlap.push(`${key} ${L.t.toFixed(1)}s`); over.set(key, L.t); }
      } else over.delete(key);
    }
  }
  return out;
}"""


def test_빨리_감아_8분을_돌려도_벽을_안_넘고_안_끼이고_쉬는_사람은_가끔_휴대폰을_본다(desktop):
    """요청(2026-10-22): "앉아서 쉴 때, 서서 있을 때 가끔 휴대폰 보는 모습" + "끼이지 않게, 벽을 넘지 않게".
    13명이 45초마다 일·결재·쉼을 뒤섞는 8분을 한 번에 돌린다(그리기 없이 행동만, 난수 둘). 끼임 = 걷는 중 4초
    넘게 제자리, 겹침 = 둘이 3초 넘게 한 자리. 2026-10-22 첫 측정은 20분에 끼임 238·겹침 24·벽 1, 60분 측정에서
    첫 화면 (0,0) 바닥 밖·한 의자에 둘(카페 예약·회의실 배정)이 더 나왔다 — 고친 뒤 60분×16회에서 바닥 밖·벽·
    겹침 0, 끼임 1건(회의실 앞 4초 남짓, 스스로 풀림)."""
    for seed in (7, 21):
        _sim_ok(desktop.evaluate(SIM, [8, seed]))


def _sim_ok(out):
    assert out["offFloor"] == [] and out["wall"] == []
    assert out["stuck"] == [], out["stuck"][:10]
    assert out["overlap"] == [], out["overlap"][:10]
    poses = out["poses"]
    assert sum(poses.get(k, 0) for k in ("sitScroll", "sitText", "sitLol", "sitBackPhone")) > 0     # 앉아서 휴대폰
    assert sum(poses.get(k, 0) for k in ("phScroll", "phText", "phLol")) > 0                         # 서서 휴대폰
    sitting_rest = poses.get("sitFront", 0) + poses.get("sitScroll", 0) + poses.get("sitText", 0) + poses.get("sitLol", 0)
    assert 0.15 < (poses.get("sitScroll", 0) + poses.get("sitText", 0) + poses.get("sitLol", 0)) / sitting_rest < 0.7   # '가끔'
