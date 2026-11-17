"use strict";
// 크로미움 LNA(로컬 네트워크 접근) 정책이 공개 오리진 → 테일넷 주소(100.x)로의 fetch/WS 를 막는다.
// 그래서 실제 화면은 브릿지가 직접 서빙하고, 문패(vercel.app·FROG.NAMEPLATES)는 그리로 보내기만 한다.
// 리다이렉트는 vercel.json 의 308(서버)이 먼저 처리한다 — 아래는 이중 안전망(문패가 그대로 열린 경우).
const REDIRECTING = !!FROG.BRIDGE && (location.hostname.endsWith(".vercel.app")
  || (FROG.NAMEPLATES || []).includes(location.hostname));
if (REDIRECTING) location.replace(`https://${FROG.BRIDGE}/`);

const API = "";                              // 같은 출처 — 상대경로로만 부른다
const enc = new TextEncoder();
// 경로를 셸 한 인자로 — 작은따옴표는 '\'' 로 닫고 잇고, 제어문자(개행 포함)는 뺀다. 파일명 속 따옴표가
// 인용을 끝내고 뒷글자가 명령이 되는 길을 막는다(코덱스 교차점검 2026-10-11).
const shellQuote = p => "'" + String(p).replace(/[\x00-\x1f\x7f]/g, "").replace(/'/g, "'\\''") + "'";
let ctrlArmed = false;                         // 키바 Ctrl — 다음 한 글자를 제어문자로(활성 칸에 걸린다)
// 터미널 칸들 — 칸 하나의 상태는 전부 Pane 객체 안에 있다(아래 '칸' 절, 멀티 칸 설계 2026-10-09).
const panes = [];
let activePane = null;                        // 사이드바·키바·붙여넣기·파일 패널이 따르는 칸
// 단일 터미널 시절의 전역 이름 — files.js·browser.js·settings.js 와 회귀 테스트가 그대로 읽는다.
// 이제 '활성 칸의 것'을 돌려주는 읽기 전용 창구다(대입은 strict 모드에서 바로 오류로 드러난다).
for (const [name, get] of Object.entries({
  term: () => activePane?.term ?? null,
  ws: () => activePane?.ws ?? null,
  currentSession: () => activePane?.session ?? null,
  reconnectTimer: () => activePane?.reconnectTimer ?? null,
})) Object.defineProperty(window, name, { get, configurable: true });

// ---------- 화면 배치 ----------
// 데스크톱(≥900px)은 목록을 왼쪽 사이드바로 상시 띄운 3열, 폰은 한 화면씩 갈아끼운다.
// 가운데를 교대하는 화면은 터미널 하나뿐이고, 인박스·설정은 오른쪽 '팝업 패널'이다
// (터미널을 가리면 안 되고, 테마 변경 같은 것은 뒤에서 즉시 보여야 한다).
const isDesktop = () => matchMedia("(min-width: 900px)").matches;

function showView(name) {                     // "term" — 가운데 영역(터미널)을 연다
  window.officeClose?.(false);                // 사무실 화면(3D 모션)은 터미널에 자리를 내준다(office-view.js)
  document.getElementById("term-view").hidden = name !== "term";
  document.getElementById("list-view").classList.toggle("offscreen", !isDesktop());
  // 빈 가운데 안내(.idle)는 JS 가 단다 — CSS :has() 로 하면 터미널의 초당 수백 DOM 변경마다
  // 재평가돼 페이지가 통째로 언다(실측 — "스크롤이 안 된다"의 정체).
  document.getElementById("main").classList.remove("idle");
}

function showList() {                         // 폰 전용 — 목록 전면 (데스크톱은 이미 사이드바로 보인다)
  document.getElementById("term-view").hidden = true;
  closeInbox(false);
  document.getElementById("settings-view").hidden = true;
  // 사무실 화면이 켜져 있으면 그게 가운데 자리다 — 빈 화면 안내(.idle)를 깔면 둘이 폭을 나눠 갖는다
  if (window.officeShown?.()) { loadSessions(); return; }
  document.getElementById("list-view").classList.remove("offscreen");
  document.getElementById("main").classList.add("idle");
  loadSessions();
}

// ---------- 결재 인박스 패널 ----------
function openInbox() {
  document.getElementById("settings-view").hidden = true;   // 오른쪽 패널은 한 번에 하나
  document.getElementById("inbox-view").hidden = false;
  if (!isDesktop())                           // 폰은 전면 — 목록을 치워 한 화면 원칙 유지
    document.getElementById("list-view").classList.add("offscreen");
  loadInbox();
}

function closeInbox(backToList = true) {
  document.getElementById("inbox-view").hidden = true;
  if (backToList && !isDesktop()) {           // 폰에서 ✕ = 목록으로 (데스크톱은 뒤가 그대로 보인다)
    document.getElementById("list-view").classList.remove("offscreen");
    loadSessions();
  }
}

// 창 폭이 폰↔데스크톱 경계를 넘으면 목록 규칙을 다시 적용한다
// (데스크톱에서 열어 둔 채 창을 좁히면 목록이 터미널을 밀고 들어오던 것 방지)
matchMedia("(min-width: 900px)").addEventListener("change", () => {
  if (!document.getElementById("term-view").hidden) showView("term");
  else document.getElementById("list-view").classList.remove("offscreen");
});

// ---------- 목록 화면 ----------
// 문구에 ●⏳✅ 기호를 넣지 않는다 — 상태는 왼쪽 점 하나가 말한다(색·맥박, style.css).
// 점과 문구가 각자 기호를 들면 한 줄에 상태 표식이 둘이 되어 서로를 흐린다.
const STATUS_META = {                         // 정렬 순서와 상태줄 문구·색 — 손댈 것이 항상 위로
  decision: { order: 0, cls: "decision", text: n => `결재 ${n}건 대기` },
  working:  { order: 1, cls: "working",  text: () => "진행중…" },
  done:     { order: 2, cls: "done",     text: () => "완료 — 다음 지시 대기" },
  on:       { order: 3, cls: "on",       text: () => "켜짐" },
  off:      { order: 4, cls: "off",      text: () => "꺼짐" },
};
// status 가 없거나 모르는 값이어도 목록 전체가 안 그려지는 일은 없게 — off 로 떨어뜨린다
const metaOf = s => STATUS_META[s.status] ?? STATUS_META.off;

// 즐겨찾기 = 브릿지가 주는 순번(0,1,2…). 아닌 줄은 맨 뒤 자리를 하나 나눠 갖는다.
// Infinity 를 쓰면 안 된다 — 둘 다 즐겨찾기가 아닐 때 Infinity-Infinity 가 NaN 이 되어
// 비교 함수가 순서를 못 말하고, 그 뒤의 상태순·이름순 규칙이 통째로 날아간다.
const isFav = s => Number.isInteger(s.fav);
const favRank = s => (isFav(s) ? s.fav : Number.MAX_SAFE_INTEGER);

function el(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

// 병렬 탭(base@2~9)도 프로젝트 기준으로 다루는 곳들(사이드바 강조·파일 패널·업로드)의 공통 기준.
const baseOf = name => name.replace(/@[2-9]$/, "");

function markActive() {                       // 지금 열어 둔 프로젝트를 사이드바에서 강조
  // 여러 칸이면 다른 칸에 떠 있는 것도 옅게 표시한다(.shown) — 어느 것이 화면에 있는지 한눈에.
  const shown = new Set(panes.map(p => p.session && baseOf(p.session.name)).filter(Boolean));
  for (const li of document.querySelectorAll("#sessions li")) {
    const active = !!currentSession && li.dataset.name === baseOf(currentSession.name);
    li.classList.toggle("active", active);
    li.classList.toggle("shown", !active && panes.length > 1 && shown.has(li.dataset.name));
  }
}

const CONN_NOTE = "맥에 연결 못 함 — 이 기기가 Tailscale에 연결돼 있는지 확인하세요";
let listNote = "";                            // 끄기·숨김이 실패한 이유 — 다음 조작이 성공하면 지운다
let lastSessions = [];                        // 마지막으로 받은 세션 목록 — 탭 바(renderTabs)가 쓴다
let hiddenOpen = false;                       // '숨긴 항목'을 펴 뒀나 (7초 폴링이 다시 그려도 유지)
let projFilter = "";                          // 사이드바 검색어 — 변수로 들고 있어 폴링 재렌더에도 유지

// 이름·경로 어느 쪽에 걸려도 찾는다. 대소문자는 무시 — 검색칸에서 소문자만 쳐도 MyProject 가 나오게.
function projMatch(s) {
  if (!projFilter) return true;
  const q = projFilter.toLowerCase();
  return s.name.toLowerCase().includes(q) || (s.path ?? "").toLowerCase().includes(q);
}

// alert() 금지(포커스·자동화를 망가뜨린다) — 사이드바 안내는 목록 위 한 줄로.
function listHint(text) {
  const hint = document.getElementById("conn-hint");
  hint.textContent = text;
  hint.hidden = !text;
}

async function loadSessions() {
  const listEl = document.getElementById("sessions");
  try {
    const res = await fetch(`${API}/api/sessions`);
    if (!res.ok) throw new Error(res.status);
    const { sessions, restorable } = await res.json();
    lastSessions = sessions;
    setRestorable(restorable ?? 0);
    // 즐겨찾기가 상태순보다 위다 — 사용자가 올려 둔 것은 '무조건 맨 위, 올린 순서대로'.
    // 그 아래로만 기존 규칙(결재>진행중>완료>그 외, 같은 상태 안에서 이름순)이 이어진다.
    sessions.sort((a, b) => (favRank(a) - favRank(b)) ||
                            (metaOf(a).order - metaOf(b).order) || a.name.localeCompare(b.name));
    // 숨김은 브릿지에서 '표시'로만 온다 — 목록에서 빼지 않는다(결재 수는 숨김과 무관해야 하므로).
    // 접어 두는 것은 화면의 몫이다. 검색 필터도 마찬가지 — 화면에서만 거른다.
    const sidebarHidden = s => s.hidden || s.project === false;
    const shown = sessions.filter(s => !sidebarHidden(s) && projMatch(s));
    listEl.replaceChildren(...shown.map(sessionRow));
    document.getElementById("proj-empty").hidden = !(projFilter && !shown.length);
    renderHiddenBar(sessions.filter(s => sidebarHidden(s) && projMatch(s)));
    markActive();
    renderTabs();                                        // 다른 기기에서 연 탭도 폴링으로 따라온다
    renderPicks();                                       // 칸 머리줄 목록도 같은 응답으로(grid.js)
    listHint(listNote);                                  // 다시 붙었다 — 남은 안내만 그대로 건다
  } catch {
    listHint(CONN_NOTE);                                 // 테일넷 미연결 안내
  }
}

// [host] 배지 — 사이드바 줄과 위 머리줄이 같은 모양을 쓴다. 호스트명도 textContent 로만 넣는다.
function hostBadge(host) {
  const b = el("span", `[${host}]`);
  b.className = "host";
  b.title = `다른 PC 터미널 — ssh ${host}`;
  return b;
}

function sessionRow(s) {
  const li = document.createElement("li");
  const meta = metaOf(s);
  li.dataset.name = s.name;
  // 경로가 <루트>/<이름> 그대로면 이름의 반복일 뿐이라 표시하지 않는다(모든 줄이 같은 접두어로
  // …잘리던 노이즈 제거). 다른 자리에 있는 폴더·폴더 없는 tmux 세션만 경로가 정보다.
  const oddPath = s.path && !s.path.endsWith(`/${s.name}`) ? s.path : "";
  // 점 채움 = 지금 무언가 살아 있다(결재를 기다리거나·일하거나·끝내고 대기). 빈 원 = 꺼짐.
  // 색·맥박은 CSS 가 상태 클래스로 정한다 — 점 하나가 상태 언어의 전부다(Orca 차용).
  const active = s.alive || ["decision", "working", "done"].includes(s.status);
  // 도는 에이전트가 점 색을 정한다 — 코덱스 = 파랑, 안티그래비티 = 보라(클로드 초록과 구분). CSS li.codex/li.anti
  if (s.agent === "codex" || s.agent === "anti") li.classList.add(s.agent);
  // 세션 이름·경로는 항상 textContent 로만 넣는다 — 어떤 문자가 와도 HTML 로 해석되지 않게
  // 이름 칸은 [★ 즐겨찾기 표식 | 이름] 한 줄. 표식을 이름 안에 섞지 않는 이유: 이름은 <b> 하나로
  // 온전히 읽혀야 한다(검색·강조·테스트가 전부 <b> 글자를 그대로 본다).
  const nameBox = document.createElement("div");
  nameBox.className = "row-name";
  if (isFav(s)) nameBox.append(el("i", "★"));      // 즐겨찾기 표식 — 한눈에 구분되게 줄 앞머리에
  // 다른 PC 터미널 — pane 이 ssh 로 다른 기계에 붙어 있으면 이름 앞에 [host] 배지.
  // 맥 로컬 세션은 host 가 null 이라 배지 없이 지금 모양 그대로다.
  if (s.host) nameBox.append(hostBadge(s.host));
  nameBox.append(el("b", s.name));
  li.append(nameBox, el("em", active ? "●" : "○"), el("small", oddPath));
  const st = el("span", meta.text(s.pending ?? 0));      // 상태줄 — 진행중·결재대기를 여기서 읽는다
  st.className = `st ${meta.cls}`;
  li.append(st, rowActs(s, li));
  // 살아 있는 것은 화면 전환뿐이라 한 번에 연다. 꺼진 것은 누르는 순간 tmux 세션이 새로 서므로
  // 반드시 되묻는다 — 목록을 스치기만 해도 프로젝트가 우수수 켜지던 문제.
  li.onclick = () => (s.alive ? openTerminal(s) : askOpen(s, li));
  return li;
}

// ---------- 줄 오른쪽 액션 — 끄기·숨김 ----------
// 줄을 누르면 터미널이 열린다. 액션 버튼이 그것까지 발동시키면 "끄려다 터미널이 열리는" 꼴이
// 되므로 클릭 전파를 반드시 끊는다.
function rowActs(s, li) {
  const acts = document.createElement("div");
  acts.className = "row-acts";
  // 글자 버튼('끄기')이 커서 자꾸 잘못 눌렸다 — 작은 아이콘 세로 스택으로.
  // 뜻은 title/aria 로 남긴다. 순서(즐겨찾기→끄기→숨기기)는 테스트 계약이라 바꾸지 않는다:
  // 끄기는 .danger, 숨기기는 늘 :last-child. 그래서 새로 든 즐겨찾기는 스택 맨 앞에 선다.
  acts.append(actBtn(isFav(s) ? "★" : "☆", "", () => setFavorite(s.name, !isFav(s)), "즐겨찾기"));
  if (s.alive) acts.append(actBtn("🗑", "danger", () => askKill(s, li), "끄기"));   // 꺼진 것은 끌 것이 없다
  acts.append(actBtn("🙈", "", () => setHidden(s.name, true), "숨기기"));
  return acts;
}

function actBtn(text, cls, run, title) {
  const btn = el("button", text);
  if (cls) btn.className = cls;
  if (title) { btn.title = title; btn.setAttribute("aria-label", title); }
  btn.onclick = e => { e.stopPropagation(); run(); };
  return btn;
}

// 끄기는 되돌릴 수 없다 — 그 tmux 세션 안에서 돌던 클로드가 같이 죽는다. 그래서 반드시 되묻고,
// 무엇이 같이 죽는지를 문구에 적는다. confirm() 은 쓰지 않는다(모달은 포커스·자동화를 망가뜨린다).
function askKill(s, li) {
  if (li.querySelector(".row-confirm")) return;          // 이미 묻는 중
  const box = document.createElement("div");
  box.className = "row-confirm";
  box.onclick = e => e.stopPropagation();
  box.append(el("span", `'${s.name}' 터미널을 끕니다 — 그 안에서 돌던 클로드도 같이 종료됩니다.`),
             actBtn("끄기", "", () => killSession(s.name)),
             actBtn("취소", "cancel", () => box.remove()));
  li.append(box);
}

// 켜기도 되묻는다 — 꺼진 프로젝트를 누르면 tmux 세션이 새로 서고 시작 에이전트까지 뜬다.
// 끄기와 같은 자리·같은 모양(.row-confirm)을 쓴다: 되묻는 방식이 하나여야 손이 헷갈리지 않는다.
function askOpen(s, li) {
  if (li.querySelector(".row-confirm")) return;          // 이미 묻는 중
  const box = document.createElement("div");
  box.className = "row-confirm";
  box.onclick = e => e.stopPropagation();
  // 무엇으로 켤지 여기서 고른다 — 설정의 기본 에이전트를 바꾸지 않고 이번 한 번만.
  // 버튼 색은 각 에이전트 회사의 대표색(agent-* 클래스, style.css) — 글자를 안 읽어도 손이 간다.
  box.append(el("span", `'${s.name}' 터미널을 켭니다 — 꺼져 있던 세션이 새로 시작됩니다.`),
             actBtn("클로드로 켜기", "agent-claude", () => openTerminal(s, "claude")),
             actBtn("코덱스로 켜기", "agent-codex", () => openTerminal(s, "codex"), "codex --yolo 로 시작"),
             actBtn("안티로 켜기", "agent-anti", () => openTerminal(s, "anti"), "Antigravity CLI(agy) 로 시작"),
             actBtn("취소", "cancel", () => box.remove()));
  li.append(box);
}

async function killSession(name) {
  let error = "";
  try {
    const res = await fetch(`${API}/api/sessions/kill`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: name }),
    });
    // 404 를 실패로 다루지 않는다 — 폴더 없이 tmux 세션만 있던 항목은 끄고 나면 그 이름이
    // 브릿지의 관문(is_valid_session)에서 사라져 두 번째 요청이 404 로 온다. 보통 프로젝트는
    // 200 killed:false 다. 둘 다 결과는 '꺼짐' 이므로 목록만 새로 읽는다.
    if (!res.ok && res.status !== 404)
      error = `'${name}' 을 끄지 못했습니다 (서버 응답 ${res.status})`;
  } catch {
    error = "맥에 연결되지 않아 끄지 못했습니다 — Tailscale 연결을 확인하세요";
  }
  // 끈 세션의 터미널이 열려 있으면 함께 접는다 — 열린 채 두면 재접속 타이머가 new-session -A 로
  // 그 세션을 곧바로 되살린다(실측: 끄기 → 몇 초 뒤 다시 켜짐 + 시작 에이전트까지 재기동).
  const hit = panes.filter(p => p.session?.name === name);   // 여러 칸이면 그 세션을 띄운 칸 모두
  hit.forEach(p => p.close());
  if (hit.length) {
    if (isGrid()) { paintHeader(); renderPicks(); }        // 칸은 비우고 격자는 남긴다
    else showList();
  }
  listNote = error;
  await loadSessions();                                  // 끈 직후 곧바로 '꺼짐'으로 갱신
  refreshSummary();
}

// 숨김은 서버 파일에 남는다(폰과 맥이 같은 목록을 봐야 한다). 되돌릴 수 있으니 되묻지 않는다.
async function setHidden(name, hidden) {
  let error = "";
  try {
    const res = await fetch(`${API}/api/hidden`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: name, hidden }),
    });
    if (!res.ok)
      error = `'${name}' 을 ${hidden ? "숨기지" : "되돌리지"} 못했습니다 (서버 응답 ${res.status})`;
  } catch {
    error = "맥에 연결되지 않아 숨김을 바꾸지 못했습니다 — Tailscale 연결을 확인하세요";
  }
  listNote = error;
  await loadSessions();                                  // 숨긴 직후 곧바로 목록에서 빠지게
}

// 즐겨찾기도 숨김과 같은 자리에 남는다(서버 파일) — 폰에서 올려 둔 순서를 맥에서도 그대로 본다.
// 되돌리기 쉬우니 되묻지 않는다.
async function setFavorite(name, fav) {
  let error = "";
  try {
    const res = await fetch(`${API}/api/favorites`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: name, fav }),
    });
    if (!res.ok)
      error = `'${name}' 즐겨찾기를 ${fav ? "켜지" : "끄지"} 못했습니다 (서버 응답 ${res.status})`;
  } catch {
    error = "맥에 연결되지 않아 즐겨찾기를 바꾸지 못했습니다 — Tailscale 연결을 확인하세요";
  }
  listNote = error;
  await loadSessions();                                  // 누른 직후 곧바로 맨 위로 올라오게
}

// 숨긴 항목 — 목록 맨 아래 접이식. 0개면 바 자체를 숨긴다(빈 줄만 남지 않게).
function renderHiddenBar(items) {
  const bar = document.getElementById("hidden-bar");
  const list = document.getElementById("hidden-list");
  bar.hidden = !items.length;
  if (!items.length) {
    hiddenOpen = false;
    list.replaceChildren();
    return;
  }
  document.getElementById("hidden-toggle").textContent =
    hiddenOpen ? `▾ 숨긴 항목 ${items.length}개` : `▸ 숨긴 항목 ${items.length}개 보기`;
  list.hidden = !hiddenOpen;
  list.replaceChildren(...items.map(s => {
    const li = document.createElement("li");
    const label = el("span", s.project === false ? `${s.name} · 프로젝트 아님` : s.name);
    li.append(label);
    // project=false는 자동 분류라 hidden.json을 고쳐도 일반 목록으로 올라가면 안 된다. 사용자가
    // 따로 숨긴 경우에만 해제 버튼을 보여 두 상태를 독립적으로 다룬다.
    if (s.hidden) {
      const back = el("button", "되돌리기");
      back.onclick = e => { e.stopPropagation(); setHidden(s.name, false); };
      li.append(back);
    }
    // 숨김 칸은 보관함이지 삭제 목록이 아니다. 이름을 누르면 일반 줄과 같은 방식으로 열 수 있다.
    li.onclick = () => (s.alive ? openTerminal(s) : askOpen(s, li));
    return li;
  }));
}

document.getElementById("hidden-toggle").onclick = () => { hiddenOpen = !hiddenOpen; loadSessions(); };

// ---------- 사이드바 검색 ----------
// 서버를 다시 부르지 않고 화면만 다시 거른다 — 글자마다 fetch 를 쏘면 타이핑이 폴링과 경합한다.
// 마지막 응답을 loadSessions 가 다시 그려 주므로, 입력 즉시는 이미 그려진 줄을 감추는 것으로 충분…
// …하지 않다: 줄 DOM 에는 path 가 없을 수 있어(oddPath 일 때만 표시) 다시 로드가 정확하다.
{
  const box = document.getElementById("proj-filter");
  box.oninput = () => { projFilter = box.value.trim(); loadSessions(); };
  box.onkeydown = e => {                       // Esc = 지우고 전체 목록으로
    if (e.key === "Escape" && box.value) {
      e.stopPropagation(); box.value = ""; projFilter = ""; loadSessions();
    }
  };
}

let webVersion = null;                       // 처음 받은 프론트 버전 — 달라지면 새 배포다

async function refreshSummary() {
  const conn = document.getElementById("sb-conn");
  try {
    const res = await fetch(`${API}/api/summary`);
    if (!res.ok) throw new Error(res.status);
    const { alive, pending, waiting = 0, dead = 0, web = 0 } = await res.json();
    // 새 프론트 배포 감지 — PWA·오래 연 탭은 껐다 켜기 전까지 옛 코드를 문다(한글 소동의 교훈).
    if (web) {
      if (webVersion === null) webVersion = web;
      else if (web !== webVersion) document.getElementById("update-bar").hidden = false;
    }
    document.getElementById("sum-alive").textContent = `세션 ${alive}개 켜짐`;
    const btn = document.getElementById("sum-pending");
    // 상태를 다 말한다 — 결재만 말하면 "0건"인데 지시 넣을 세션이 쌓인 걸 못 본다.
    // 꺼짐은 0건이면 생략(늘 붙이면 세 토막이 상시 노이즈). 폰은 '건' 없이 짧게.
    const parts = isDesktop()
      ? [`결재 대기 ${pending}건`, `지시 대기 ${waiting}건`]
      : [`결재 ${pending}`, `지시 ${waiting}`];
    if (dead > 0) parts.push(isDesktop() ? `꺼짐 ${dead}건` : `꺼짐 ${dead}`);
    btn.textContent = parts.join(" · ");
    btn.classList.toggle("hot", pending > 0);
    // 죽은 세션이 있으면 버튼에 magenta 점 — 결재 0건이어도 인박스를 열 이유가 있다는 신호
    btn.classList.toggle("has-dead", dead > 0);
    btn.title = dead > 0 ? `세션 꺼짐 ${dead}건 — 인박스에서 확인` : "";
    conn.textContent = "● 맥 연결됨";
    conn.className = "ok";
    // 폰은 짧게 — 상태바가 잘리는 순간 신뢰를 잃는다(세션 수는 목록이 말한다)
    document.getElementById("sb-counts").textContent =
      (isDesktop() ? `세션 ${alive} · 결재 ${pending} · 지시 ${waiting}` : `결재 ${pending}`)
      + (dead > 0 ? ` · 꺼짐 ${dead}` : "");
  } catch {   /* 연결 안내는 loadSessions 가 맡는다 — 상태바 점은 경고색으로 */
    conn.textContent = "○ 연결 끊김";
    conn.className = "bad";
  }
}

// 상태바 오른쪽 — 클로드 계정·사용량(레이트리밋). Orca 차용: 막히기 전에 얼마나 남았는지가
// 늘 보여야 한다. 사용량은 서버가 5분 캐시하므로 60초 폴링이면 충분하다.
async function refreshStatusMeta() {
  try {
    const { agents } = await (await fetch(`${API}/api/agents`)).json();
    const email = agents?.claude?.email;
    document.getElementById("sb-account").textContent = email ? `클로드 ${email}` : "";
  } catch { /* 상태바는 조용히 빈 칸 — 연결 안내는 왼쪽 점이 한다 */ }
  try {
    const { usage } = await (await fetch(`${API}/api/usage`)).json();
    const windows = usage?.windows ?? [];
    const seg = document.getElementById("sb-usage");
    // 폰은 앞의 두 창만 — 셋 다 넣으면 오른쪽이 말줄임돼 상태바가 잘려 보인다
    seg.textContent = windows.slice(0, isDesktop() ? 9 : 2)
                             .map(w => `${w.name} ${w.pct}%`).join(" · ");
    seg.classList.toggle("warn", windows.some(w => w.pct >= 80));   // Orca 와 같은 80% 경고
    seg.title = windows.map(w => `${w.name} ${w.pct}% — ${w.left} 뒤 리셋`).join("\n")
                || "클로드 사용량 — 5분마다 갱신";
  } catch { /* cswap 없음·실패 — 칸을 비워 둔다 */ }
}

// ---------- 터미널 화면 ----------
// 터미널 하나의 상태(xterm·소켓·타이머·붙여넣기 대기열·IME 거울)는 칸(Pane) 객체가 갖는다(아래
// '칸' 절). 여기는 화면 공통부(머리줄·탭 바·파일/브라우저 패널)를 활성 칸에 맞추고 칸을 시작시킨다.
// agent — 이번 접속에서 새로 서는 세션에 띄울 에이전트("claude"·"codex"·"anti"). 없으면 설정값.
// 이미 켜진 세션에는 아무 효과가 없다(tmux -A 는 붙기만 한다).
function openTerminal(s, agent) {
  // 같은 세션은 두 칸에 띄우지 않는다 — 이미 다른 칸에 있으면 그 칸으로 간다(멀티 칸 설계 2026-10-09).
  // 같은 tmux 에 두 번 붙으면 두 칸이 창 크기를 서로 뺏어 화면이 계속 다시 그려진다.
  const there = panes.find(p => p !== activePane && p.session?.name === s.name);
  if (there) { setActive(there); there.term?.focus(); return; }
  openIn(activePane, s, agent);
}

// 칸 하나에 세션을 연다 — 사이드바·인박스는 활성 칸으로(openTerminal), 격자 채우기·칸 고르기는 그 칸으로.
function openIn(pane, s, agent) {
  pane.close();                              // 그 칸의 이전 소켓·터미널을 먼저 접는다
  pane.session = agent ? { ...s, agent } : s;
  closeInbox(false);                         // 인박스 카드에서 넘어온 경우 — 패널이 터미널을 가리지 않게
  showView("term");
  markActive();
  if (pane === activePane) {
    bpSync?.();                              // 브라우저 패널은 프로젝트별 기억대로(browser.js) — 열어 뒀으면 열고 아니면 접는다
    fpAutoOpen();                            // 파일 패널은 기본 열림(files.js) — 터미널 생성 전에 열어
                                             // 첫 fit 이 패널 있는 폭으로 재도록 한다
    paintHeader();
  }
  pane.start();
  renderPicks();
  gridSave();
}

// 위 머리줄 = 활성 칸의 것. 세션 혼선 방지로 이름을 크게 고정 표시하고, 몇 번 탭인지는 탭 바가 말한다.
function paintHeader() {
  const s = activePane?.session;
  document.getElementById("hdr-name").textContent = s ? baseOf(s.name) : "";
  // ssh 로 다른 PC 에 붙은 세션은 경로 줄 앞에 [host] 를 붙여 어느 기계인지 바로 읽히게 한다.
  const hostTag = s?.host ? `[${s.host}] ` : "";
  document.getElementById("hdr-path").textContent = s
    ? hostTag + (s.path ?? (s.host ? "다른 PC 터미널 (ssh)" : "(폴더 없음 — 기존 tmux 세션)"))
    : isGrid() ? "빈 칸 — 칸 위 목록에서 프로젝트를 고르세요" : "";
  renderTabs();
  markActive();
  if (typeof reportFocus === "function") reportFocus();   // 게임기 frog-agent 가 따라오게(pad.js)
}

// ---------- 병렬 터미널 탭 — 같은 프로젝트에서 터미널 여러 개 (Zed 차용) ----------
// 탭 = "<프로젝트>@N"(2~9) 이름의 독립 tmux 세션(브릿지 sessions.py 의 _TAB_RE 와 한 쌍).
// 목록의 정본은 서버(sessions[].tabs)다 — 폰에서 연 탭도 맥 폴링(7초)에 따라온다.
// 방금 + 로 연 탭은 서버가 따라잡기 전까지 currentSession 으로 보강해 바로 보인다.
function renderTabs() {
  const bar = document.getElementById("term-tabs");
  if (!currentSession) { bar.replaceChildren(); return; }
  const base = baseOf(currentSession.name);
  const entry = lastSessions.find(s => s.name === base);
  const nums = new Set(entry?.tabs ?? []);
  if (currentSession.name !== base) nums.add(Number(currentSession.name.slice(-1)));
  const path = entry?.path ?? currentSession.path;
  const open = name => openTerminal({ name, path, alive: true });
  const btns = [tabBtn(base, "1", open),
                ...[...nums].sort((a, b) => a - b).map(n => tabBtn(`${base}@${n}`, String(n), open))];
  if (nums.size < 8) {                        // 2~9 = 여덟 자리가 꽉 차면 + 를 접는다
    const plus = el("button", "＋");
    plus.className = "tab-plus";
    plus.title = "터미널 추가 — 같은 프로젝트에 하나 더";
    plus.onclick = () => { for (let n = 2; n <= 9; n++) if (!nums.has(n)) return open(`${base}@${n}`); };
    btns.push(plus);
  }
  bar.replaceChildren(...btns);
}

function tabBtn(name, label, open) {
  const btn = el("button", label);
  btn.className = "tab" + (name === currentSession?.name ? " on" : "");
  btn.title = name;
  btn.onclick = () => { if (name !== currentSession?.name) open(name); };
  if (label !== "1") {                        // 기본 터미널 끄기는 사이드바 몫 — ✕ 는 탭에만
    const x = el("span", "✕");
    x.className = "tab-x";
    x.title = "이 탭 끄기";
    x.onclick = e => { e.stopPropagation(); askTabKill(name); };
    btn.append(x);
  }
  return btn;
}

// 탭도 끄면 안에서 돌던 것이 같이 죽는다 — 사이드바 끄기와 같은 잣대로 반드시 되묻는다.
function askTabKill(name) {
  const bar = document.getElementById("term-tabs");
  bar.replaceChildren(el("span", `'${name}' 을 끕니다 — 안에서 돌던 것도 종료됩니다`),
    actBtn("끄기", "danger", async () => {
      if (currentSession?.name === name)      // 보고 있던 탭이면 먼저 기본 터미널로 돌아간다
        openTerminal({ name: baseOf(name), path: currentSession.path, alive: true });
      await killSession(name);                // loadSessions → renderTabs 로 탭 바가 새로 그려진다
    }),
    actBtn("취소", "cancel", renderTabs));
}

// 세션 전환·목록 복귀의 공통 뒷정리 — 활성 칸을 접는다(정리 항목은 Pane.close).
function closeTerminal() {
  activePane?.close();
  markActive();
  if (isGrid()) { paintHeader(); renderPicks(); }   // 여러 칸 — 그 칸만 비고 격자는 남는다
}

const isMouseReport = d => /^\x1b\[<\d+;\d+;\d+[Mm]$/.test(d);   // xterm SGR 마우스 리포트

function send(data) {                       // 활성 칸으로 — 키바처럼 칸 밖에서 오는 입력의 출구
  return activePane?.send(data) ?? false;
}

// 창이 다시 앞으로 오면 터미널에 포커스를 돌려준다 — xterm 이 focus-in(\x1b[I)을 보내고,
// tmux(focus-events on + window-size latest)는 포커스 받은 클라이언트를 '최신'으로 삼아
// 창 크기를 이 화면에 맞춘다. 폰과 맥이 같은 세션에 붙어 서로 크기를 뺏던 것을,
// "지금 보고 있는 쪽이 되찾는" 걸로 정리하는 장치다(2026-08-15 실측 검증).
// 파일 패널 편집칸 등 딴 곳에 포커스가 살아 있으면 뺏지 않는다.
// ⚠️ 여기서 textarea 를 blur→focus 로 "리셋"하지 말 것 — 그 사이클이 맥 한글 IME 의
// 조합 연결을 이 칸에 대해 죽여, 이후 타이핑이 전부 자모로 풀리는 실사고(2026-08-15)의
// 원인이었다. 포커스는 아무 데도 없을 때(body) 한 번 주는 것까지만.
window.addEventListener("focus", () => {
  if (term && document.activeElement === document.body) term.focus();
});

// ---------- 새 프로젝트 (사이드바 ＋) ----------
// 폴더는 <프로젝트 루트>/<이름> 하나만 만든다 — 세션 이름·파일 패널·업로드가 전부 이 폴더 기준이다.
// 만들자마자 그 터미널을 연다(이름을 정하면 바로 실행). 설정한 시작 에이전트가 뜬다.
// 무엇으로 시작할지는 입력칸 아래 세 버튼(클로드·코덱스·안티)에서 고른다. Enter = 클로드(기본 그림).
const projNewInput = () => document.getElementById("proj-new-name");
const projNewBox = () => document.getElementById("proj-new-box");
document.getElementById("proj-new").onclick = () => {
  const box = projNewBox();
  box.hidden = !box.hidden;
  if (!box.hidden) { projNewInput().value = ""; projNewInput().focus(); }
};
projNewInput().onkeydown = e => {
  if (e.key === "Escape") { projNewBox().hidden = true; return; }
  if (e.key === "Enter") createProject(e.target.value.trim(), "claude");
};
document.getElementById("proj-new-claude").onclick = () => createProject(projNewInput().value.trim(), "claude");
document.getElementById("proj-new-codex").onclick = () => createProject(projNewInput().value.trim(), "codex");
document.getElementById("proj-new-anti").onclick = () => createProject(projNewInput().value.trim(), "anti");

async function createProject(name, agent) {
  if (!name) { projNewInput().focus(); return; }
  try {
    const res = await fetch(`${API}/api/projects`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (res.status === 400) { showToast("쓸 수 없는 이름입니다 — 슬래시·공백·점으로 시작 금지"); return; }
    if (!res.ok && res.status !== 409) { showToast(`만들지 못했습니다 (서버 응답 ${res.status})`); return; }
    showToast(res.status === 409 ? `'${name}' 은 이미 있습니다 — 그 터미널을 엽니다` : `'${name}' 만들었습니다`);
    projNewBox().hidden = true;
    projFilter = "";                         // 새 프로젝트가 검색어에 걸려 안 보이는 일이 없게
    document.getElementById("proj-filter").value = "";
    await loadSessions();                    // 목록·lastSessions 를 새 폴더까지 반영해 놓고
    openTerminal(lastSessions.find(s => s.name === name) ?? { name, path: null, alive: false }, agent);
  } catch {
    showToast("맥에 연결되지 않아 만들지 못했습니다");
  }
}

// ---------- 드래그 복사 → 이 기기 클립보드 ----------
let lastCopyAt = 0;                          // 마지막 드래그 시각 — Ctrl+C 의 뜻(복사/중단)을 가른다

function showToast(text) {
  const el = document.getElementById("copy-toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(showToast.t);
  showToast.t = setTimeout(() => { el.hidden = true; }, 1600);
}

async function syncCopyToClipboard(pane = activePane, pressedAt = 0) {
  lastCopyAt = Date.now();
  try {
    // 브라우저 자체 선택이 있으면 그것이 정답(⌥드래그). 없으면 tmux 가 방금 고른 것을 받아온다.
    const term = pane?.term;
    let text = term?.hasSelection() ? term.getSelection() : "";
    if (!text) {
      const res = await fetch(`${API}/api/clipboard`);
      if (!res.ok) return;
      let age;
      ({ text = "", age } = await res.json());
      // 이번 누름이 새로 복사한 게 아니면(버퍼가 누르기 전부터 있던 것) 클립보드를 건드리지 않는다.
      // 실사고(2026-10-24): 캡처 직후 터미널을 누르다 손이 조금 움직였을 뿐인데 어제 버퍼를 써 넣어
      // 방금 찍은 그림을 덮었다 — ⌘V 가 한 번에 안 들어간 정체. 복사가 아니었으니 Ctrl+C 도 중단신호로 돌린다.
      if (pressedAt && typeof age === "number" && age > (performance.now() - pressedAt) / 1000 + 1.5) {
        lastCopyAt = 0;
        return;
      }
    }
    if (!text) return;
    await navigator.clipboard.writeText(text);          // writeText 는 권한 팝업 없이 통과한다
    showToast(`복사됨 · ${text.length}자`);
  } catch { /* 포커스 없음 등 — 조용히 지나간다(맥에서는 pbcopy 가 이미 받아 뒀다) */ }
}

// ---------- 파일 경로 링크 — 터미널 속 경로를 Ctrl/⌘+클릭하면 파일 패널로 ----------
// URL 은 web-links 애드온 몫이라 여기서 제외한다. 두 모양만 링크로 본다:
// ①슬래시 든 경로(마지막 조각에 확장자 필수 — "결재/승인" 같은 말이 걸리지 않게)
// ②확장자 목록에 든 낱파일 이름. 끝의 :줄번호 는 허용하고 열 때 떼어낸다.
const PATH_EXTS = "md|txt|py|js|ts|tsx|jsx|json|html|css|sh|zsh|yml|yaml|toml|csv|log|sql|conf|ini|xml|svg|plist";
// 경로 글자에 괄호·대괄호를 포함한다 — 실사고(2026-08-15): 'episodes/(대본28)부장기분/대본.md'
// 가 '(' 에서 끊겨 '부장기분/대본.md' 로 잡히는 바람에 "이 프로젝트에 없는 경로"가 됐다.
// 대신 문장 속 괄호에 통째로 걸리지 않게 아래 trimPathToken 이 짝 안 맞는 괄호를 걷어낸다.
const PATH_CH = "\\w.\\-가-힣()\\[\\]+&#@%!";
const PATH_RE = new RegExp(
  `(?:~/|\\.{1,2}/|/)?(?:[${PATH_CH}]+/)+[${PATH_CH}]+\\.[A-Za-z0-9]{1,8}(?::\\d+)?` +
  `|[\\w\\-가-힣][${PATH_CH}]*\\.(?:${PATH_EXTS})(?::\\d+)?`, "g");

const chCount = (s, c) => s.split(c).length - 1;

// 문장 부호를 걷어낸다. 짝이 맞는 괄호는 이름의 일부로 남기고( (대본28)부장기분 ),
// 통째로 감싼 괄호나 짝 없는 닫힘만 벗긴다( (docs/설계.md) → docs/설계.md ).
function trimPathToken(t) {
  let prev;
  do {
    prev = t;
    t = t.replace(/[.,:;!?'"]+$/, "");
    if (t.startsWith("(") && t.endsWith(")") && chCount(t, "(") === chCount(t, ")")) t = t.slice(1, -1);
    if (t.startsWith("[") && t.endsWith("]") && chCount(t, "[") === chCount(t, "]")) t = t.slice(1, -1);
    if (t.endsWith(")") && chCount(t, ")") > chCount(t, "(")) t = t.slice(0, -1);
    if (t.endsWith("]") && chCount(t, "]") > chCount(t, "[")) t = t.slice(0, -1);
    // 여는 괄호가 남아도는 경우 — 정규식이 닫는 괄호 앞에서 끝나 '(docs/설계.md' 로 잡힌다
    if (t.startsWith("(") && chCount(t, "(") > chCount(t, ")")) t = t.slice(1);
    if (t.startsWith("[") && chCount(t, "[") > chCount(t, "]")) t = t.slice(1);
  } while (t !== prev);
  return t;
}

function filePathLinks(pane, y, cb) {        // registerLinkProvider — 그 칸의 줄 y(1-based)의 경로들
  const line = pane.term?.buffer.active.getLine(y - 1);
  if (!line) return cb(undefined);
  let s = "";                                // 화면 줄 문자열과, 문자 인덱스 → 버퍼 칸(0-based) 지도.
  const map = [];                            // 한글은 두 칸을 차지해 인덱스와 칸이 어긋난다 — 지도가 계약.
  for (let x = 0; x < line.length; x++) {
    const cell = line.getCell(x);
    if (!cell || cell.getWidth() === 0) continue;      // 넓은 글자의 뒷칸은 건너뛴다
    map.push(x); s += cell.getChars() || " ";
  }
  const links = [];
  for (const m of s.matchAll(PATH_RE)) {
    const token = s.slice(0, m.index).match(/\S*$/)[0] + m[0];
    if (token.includes("://")) continue;     // URL 조각 — web-links 애드온이 처리한다
    const text = trimPathToken(m[0]);
    if (!text || !text.includes(".")) continue;
    const from = m.index + m[0].indexOf(text);   // 앞을 깎아냈으면(감싼 괄호) 밑줄도 그만큼 민다
    const last = map[from + text.length - 1];
    links.push({
      range: { start: { x: map[from] + 1, y },
               end: { x: last + line.getCell(last).getWidth(), y } },
      text,
      activate: (e, t) => { if (e.ctrlKey || e.metaKey) openPathFromTerminal(t, pane); },
    });
  }
  cb(links.length ? links : undefined);
}

// `~/` 를 펼칠 홈 — 브릿지가 /api/settings 로 알려 준 값(settings.js 가 FROG.HOME 에 둔다). 아직 못 받았으면
// 프로젝트 경로에서 두 단계 위(루트가 홈 바로 아래라는 가정 — 기본 ~/projects 가 그렇다)로 물러선다.
const homeDir = projPath => FROG.HOME || projPath.replace(/\/[^/]+\/[^/]+$/, "");

async function openPathFromTerminal(raw, pane = activePane) {
  const projPath = pane.session?.path;
  if (!projPath) return;
  let p = raw.replace(/:\d+$/, "");          // vi 식 :줄번호 — 파일만 연다
  if (p.startsWith("~/")) p = homeDir(projPath) + p.slice(1);
  if (p.startsWith("/")) {
    if (p.startsWith(projPath + "/")) p = p.slice(projPath.length + 1);
    // 이 프로젝트 밖의 절대경로(다른 프로젝트·클로드 스크래치패드 이미지 등)는 맥 기본 앱으로.
    // 열어도 되는 자리인지는 브릿지가 판정한다(프로젝트 뿌리·스크래치패드만, 비밀파일 금지).
    else return openOnMacAbs(p);
  } else {
    p = p.replace(/^\.\//, "");
  }
  const base = baseOf(pane.session.name);
  if (await pathExistsIn(base, p)) { fpOpenFromTerminal(p); return; }   // 이 프로젝트 → 파일 패널
  // ★실사고(2026-08-15): 클로드가 말한 상대경로가 볼트 등 '다른 프로젝트' 기준이라 404 —
  // 전 프로젝트를 훑어 딱 한 곳에만 있으면 맥 기본 앱으로 연다.
  const others = lastSessions.map(s => s.name).filter(n => n !== base);
  const hits = (await Promise.all(others.map(async n => (await pathExistsIn(n, p)) ? n : null)))
    .filter(Boolean);
  if (hits.length === 1) return openOnMac(hits[0], p);
  showToast(hits.length ? `'${p}' — ${hits.join("·")} 에 있습니다(그 프로젝트에서 여세요)`
                        : `'${p}' — 이 프로젝트에 없는 경로입니다`);
}

// 존재 확인은 폴더 목록으로 — 파일 내용 API 는 2MB 초과·바이너리를 거절해 PDF 같은 실재
// 파일을 '없음'으로 오판한다. 비밀파일(.env 류)은 목록에서 빠지므로 여기서도 자연히 '없음'.
async function pathExistsIn(project, p) {
  const cut = p.lastIndexOf("/");
  const dir = cut < 0 ? "" : p.slice(0, cut), name = p.slice(cut + 1);
  try {
    const res = await fetch(`${API}/api/files?project=${encodeURIComponent(project)}&path=${encodeURIComponent(dir)}`);
    if (!res.ok) return false;
    const { entries = [] } = await res.json();
    return entries.some(en => !en.dir && en.name === name);
  } catch { return false; }
}

// 안내는 토스트로 — term.write 는 tmux 가 화면을 다시 그리는 순간 지워져 아무도 못 본다(실측).
async function openOnMac(project, path) {    // 다른 프로젝트의 파일 — 패널 계약 밖이라 맥 기본 앱으로
  await postOpen({ project, path, app: "default" }, `'${project}' 의 파일 — 맥에서 엽니다`);
}

// 절대경로(스크래치패드 이미지·다른 프로젝트 파일 등) — 열어도 되는 자리인지는 브릿지가 판정한다.
async function openOnMacAbs(abs, app = "default") {
  await postOpen({ abs, app }, "맥에서 엽니다");
}

async function postOpen(body, okMsg) {
  try {
    const res = await fetch(`${API}/api/open`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    showToast(res.ok ? okMsg
                     : res.status === 404 ? "그 자리에 없는 파일입니다"
                     : `열지 못했습니다 (서버 응답 ${res.status})`);
  } catch {
    showToast("맥에 연결되지 않아 열지 못했습니다");
  }
}

// ---------- 터미널 우클릭 메뉴 ----------
// 브라우저 메뉴·tmux 메뉴를 막았으니 대신 쓸 메뉴를 우리가 준다. 커서 밑에 경로가 있으면
// 그 파일을 여는 항목이, 없으면 붙여넣기·복사만 뜬다. 메뉴 상자는 파일 패널 것을 함께 쓴다.
function pathAtEvent(e, pane) {
  const term = pane.term;
  const dims = term?._core?._renderService?.dimensions?.css?.cell;
  if (!dims || !term.element) return null;
  const rect = term.element.getBoundingClientRect();
  const col = Math.floor((e.clientX - rect.left) / dims.width) + 1;      // 1-based
  const y = term.buffer.active.viewportY + Math.floor((e.clientY - rect.top) / dims.height) + 1;
  let hit = null;
  filePathLinks(pane, y, links => {
    hit = (links ?? []).find(l => col >= l.range.start.x && col <= l.range.end.x)?.text ?? null;
  });
  return hit;
}

const isTouchPointer = () => matchMedia("(pointer: coarse)").matches;

// 터미널이 찍은 localhost 링크를 '이 기기가 닿는 주소'로 바꾼다(폰에서
// "로컬로 열어 보라"는 링크가 안 열렸다). localhost·127.0.0.1 은 폰에선 폰 자신이다 — 이 화면을
// 내준 호스트(테일넷 이름)로 바꾸면 같은 테일넷의 폰이 그 포트에 닿는다(3010 실측 200). 서비스가
// 루프백에만 묶여 있으면 못 닿지만, 그건 지금도 못 여는 것이라 잃는 게 없다. 사설 LAN IP(172.x)는
// 손대지 않는다 — 밖에서 닿는지 알 수 없다.
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"]);
function reachableUrl(uri, host = location.hostname) {   // host 인자는 테스트용(순수 함수로 검증)
  try {
    const u = new URL(uri);
    if (LOOPBACK_HOSTS.has(u.hostname) && !LOOPBACK_HOSTS.has(host) && u.hostname !== host) {
      u.hostname = host;
      return u.toString();
    }
  } catch {}
  return uri;
}

function absOf(raw, pane) {                  // 터미널에 찍힌 경로 → 절대경로(맥에서 열기용)
  const p = raw.replace(/:\d+$/, "");
  if (p.startsWith("/")) return p;
  const base = pane.session?.path;
  if (!base) return null;
  if (p.startsWith("~/")) return homeDir(base) + p.slice(1);
  return `${base}/${p.replace(/^\.\//, "")}`;
}

function termMenu(e, pane) {
  e.preventDefault();
  const m = fpMenuEl();                      // 파일 패널 메뉴 상자를 함께 쓴다(스타일·닫기 동작 공용)
  const items = [];
  const raw = pathAtEvent(e, pane);
  const abs = raw && absOf(raw, pane);
  if (abs) {
    const short = abs.length > 42 ? "…" + abs.slice(-41) : abs;
    items.push([`기본 앱으로 열기 — ${short}`, () => openOnMacAbs(abs)],
               ["미리보기로 열기", () => openOnMacAbs(abs, "preview")],
               ["파인더에서 보기", () => openOnMacAbs(abs, "finder")],
               ["경로 복사", () => navigator.clipboard.writeText(abs)
                 .then(() => showToast("경로 복사됨")).catch(() => {})]);
  }
  items.push(["붙여넣기", () => pasteFromClipboard(pane)],
             ["복사(드래그한 것)", () => syncCopyToClipboard(pane)]);
  m.replaceChildren(...items.map(([label, run]) => {
    const btn = el("button", label);
    btn.onclick = ev => { ev.stopPropagation(); m.hidden = true; run(); };
    return btn;
  }));
  m.hidden = false;
  m.style.left = `${Math.min(e.clientX, innerWidth - m.offsetWidth - 8)}px`;
  m.style.top = `${Math.min(e.clientY, innerHeight - m.offsetHeight - 8)}px`;
}

// ---------- 전체 끄기 / 전체 복원 ----------
// 끄기는 되돌릴 수 없다 — 세션 안에서 돌던 클로드가 함께 죽는다. 그래서 헤더 아래 띠에서 한 번
// 되묻는다(모달은 이 앱의 관례상 안 쓴다). 복원은 '지금 열 수 있는 전부'가 아니라 '끄기 직전에
// 열려 있던 것'만 — 그 명단은 브릿지가 끄기 직전에 파일로 남긴다.
let restorable = 0;

function setRestorable(n) {
  restorable = n;
  const btn = document.getElementById("btn-restore");
  btn.disabled = !n;
  btn.title = n ? `전체끄기 직전에 열려 있던 터미널 ${n}개를 되살립니다`
                : "복원할 명단이 없습니다 — [전체끄기] 를 눌러야 생깁니다";
}

const bulkBar = () => document.getElementById("bulk-confirm");

function closeBulkAsk() { bulkBar().hidden = true; }

document.getElementById("btn-killall").onclick = () => {
  const alive = lastSessions.filter(s => s.alive).length;
  if (!alive) { listNote = "지금 열려 있는 터미널이 없습니다."; listHint(listNote); return; }
  document.getElementById("bulk-ask").textContent =
    `열려 있는 터미널 ${alive}개를 전부 끕니다. 그 안에서 돌던 클로드도 함께 꺼집니다 — ` +
    "[복원] 으로 방금 이 목록만 되살릴 수 있습니다.";
  bulkBar().hidden = false;
};
document.getElementById("bulk-no").onclick = closeBulkAsk;
document.getElementById("bulk-yes").onclick = () => { closeBulkAsk(); killAll(); };
document.getElementById("btn-restore").onclick = () => restoreAll();

async function killAll() {
  let note = "";
  try {
    const res = await fetch(`${API}/api/sessions/kill-all`, { method: "POST" });
    if (!res.ok) throw new Error(res.status);
    const { killed = [], failed = [] } = await res.json();
    note = `터미널 ${killed.length}개를 껐습니다 — [복원] 으로 되살립니다.`;
    if (failed.length) note += ` 못 끈 것: ${failed.join(", ")}`;
  } catch {
    note = "맥에 연결되지 않아 끄지 못했습니다 — Tailscale 연결을 확인하세요";
  }
  // 열려 있던 터미널은 함께 접는다 — 열어 둔 채로 두면 재접속 타이머가 new-session -A 로
  // 방금 끈 세션을 곧바로 되살린다(하나 끄기에서 실측한 것과 같은 함정).
  if (panes.some(p => p.session)) {
    panes.forEach(p => p.close());
    if (isGrid()) { paintHeader(); renderPicks(); }        // 칸은 비우고 격자는 남긴다
    else showList();
  }
  listNote = note;
  await loadSessions();
  refreshSummary();
}

// 다른 기기·다른 칸에서 끈 세션 — 이 칸이 다시 붙으려다 브릿지에 거절당했다(pane.js 4410).
// 1칸이면 처음 화면으로, 여러 칸이면 그 칸만 빈다. 다시 켜려면 사이드바·칸 목록에서 직접 고른다.
function paneKilled(pane) {
  const name = pane.session?.name;
  pane.close();
  showToast(`'${name}' 은 다른 곳에서 꺼졌습니다 — 칸을 비웠습니다`);
  if (isGrid()) { paintHeader(); renderPicks(); }
  else if (pane === activePane) showList();
  markActive();
}

async function restoreAll() {
  let note = "";
  try {
    const res = await fetch(`${API}/api/sessions/restore`, { method: "POST" });
    if (!res.ok) throw new Error(res.status);
    const { restored = [], already = [], failed = [] } = await res.json();
    note = `터미널 ${restored.length}개를 되살렸습니다.`;
    if (already.length) note += ` 이미 켜져 있던 것 ${already.length}개는 그대로 뒀습니다.`;
    if (failed.length) note += ` 못 살린 것: ${failed.join(", ")}`;
  } catch {
    note = "맥에 연결되지 않아 복원하지 못했습니다 — Tailscale 연결을 확인하세요";
  }
  listNote = note;
  await loadSessions();
  if (isGrid()) gridFill(gridLoad().names);  // 여러 칸 — 끄기 전 자리대로 다시 채운다(grid.js)
  refreshSummary();
}

// ---------- CPU 팝업 — htop 을 전용 tmux 세션(_frogtop)으로 ----------
// 메인 터미널(term/ws)과 완전히 분리된 작은 xterm 하나를 팝업에 띄운다. 세션이 tmux 라
// 어느 기기에서 열어도 같은 htop 이고, 닫기는 detach 일 뿐이라 htop 은 q 를 누를 때만 죽는다.
let cpuTerm = null, cpuWs = null, cpuObs = null;
const cpuPop = () => document.getElementById("cpu-pop");
document.getElementById("btn-cpu").onclick = () => (cpuPop().hidden ? openCpu() : closeCpu());
document.getElementById("cpu-close").onclick = () => closeCpu();
document.getElementById("update-reload").onclick = () => location.reload();

function openCpu() {
  cpuPop().hidden = false;
  document.getElementById("btn-cpu").classList.add("on");
  cpuTerm = new Terminal({ scrollback: 200, ...termOptions() });
  const cfit = new FitAddon.FitAddon();
  cpuTerm.loadAddon(cfit);
  cpuTerm.open(document.getElementById("cpu-term"));
  cfit.fit();
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  const sock = new WebSocket(`${wsProto}://${location.host}/ws/_frogtop` +
                             `?cols=${cpuTerm.cols}&rows=${cpuTerm.rows}`);
  cpuWs = sock;
  sock.binaryType = "arraybuffer";
  sock.onmessage = e => { if (sock === cpuWs) cpuTerm?.write(new Uint8Array(e.data)); };
  sock.onclose = () => { if (sock === cpuWs) closeCpu(); };   // htop 을 q 로 끝내면 팝업도 접힌다
  cpuTerm.onData(d => { if (cpuWs?.readyState === 1) cpuWs.send(enc.encode(d)); });  // F키·q·마우스
  cpuObs = new ResizeObserver(() => {
    cfit.fit();
    if (cpuWs?.readyState === 1)
      cpuWs.send(JSON.stringify({ type: "resize", cols: cpuTerm.cols, rows: cpuTerm.rows }));
  });
  cpuObs.observe(document.getElementById("cpu-term"));
}

function closeCpu() {                        // detach 만 — htop 세션은 tmux 에 그대로 남는다
  document.getElementById("btn-cpu").classList.remove("on");
  cpuObs?.disconnect(); cpuObs = null;
  const s = cpuWs; cpuWs = null;             // onclose 가 자기 자신을 또 부르지 않게 먼저 비운다
  s?.close();
  cpuTerm?.dispose(); cpuTerm = null;
  cpuPop().hidden = true;
}

// ---------- 맥 편집 단축키 ----------
// xterm 은 ⌘(meta) 조합을 브라우저 단축키로 보고 pty 로 아무것도 보내지 않는다 — ⌘C·⌘V 가
// 살아야 하므로 그 자체는 옳은 기본값이다. 그 바람에 맥에서 몸에 밴 ⌘+⌫(줄을 통째로 지우기)만
// 여기서 먹통이었다. 터미널에는 ⌘ 라는 개념이 없으므로 같은 뜻의 제어문자로 옮겨 준다.
//   ⌘+⌫ → Ctrl+U(\x15) = 커서 앞을 줄머리까지 지움. tty 의 kill 문자라 셸·클로드 입력줄이 함께 쓴다.
// 나머지 ⌘ 조합은 손대지 않고 그대로 브라우저에 넘긴다(복사·붙여넣기·탭 전환).
function macEditKeys(e, pane) {
  const term = pane.term;
  if (e.type !== "keydown") return true;
  // ⇧Enter = 줄바꿈(전송 아님) — Zed·VS Code 와 같은 동작. 터미널은 Enter 에 ⇧를
  // 못 실으므로, 클로드 공식 /terminal-setup 이 iTerm·VS Code 에 심는 것과 같은 백슬래시+CR 을
  // 보낸다. 클로드 입력창은 이걸 줄바꿈으로 받고, zsh 프롬프트에서도 줄 계속(continuation)이다.
  if (e.shiftKey && e.key === "Enter") {
    e.preventDefault();
    // ★실사고 2건의 균형점(2026-08-15). ①미확정 글자보다 줄바꿈이 먼저 나가 글자가
    // 다음 줄로 내려갔다 → 조합 중이면 이 키를 삼킨다(IME 는 이 키로 확정만 하고, 줄바꿈은
    // 다음 누름에). ②그 1차 처방이던 blur→focus 강제확정은 맥 IME 의 조합 연결을 그 칸에
    // 대해 죽여 이후 타이핑이 전부 자모로 풀렸다 — blur 는 절대 쓰지 않는다.
    if (e.isComposing) return false;
    // 확정 직후엔 xterm 이 글자를 지연 전송(setTimeout)으로 보낸다 — 그 뒤에 줄바꿈이 서게 미룬다.
    if (imeJustCommitted) { setTimeout(() => pane.send("\\\r")); return false; }
    pane.send("\\\r");
    return false;
  }
  // 윈도우 습관 지원: 맥 크롬에서 Ctrl+C/V 는 복사·붙여넣기가 아니라서
  // (⌘ 가 정답, Ctrl+C 는 중단신호) "복사했는데 안 붙는다"가 됐다. 윈도우 터미널 방식으로 직접
  // 처리한다 — Ctrl+C 는 '선택이 있으면 복사, 없으면 원래대로 중단', Ctrl+V 는 글자·이미지
  // 붙여넣기. e.code(물리 키)라 한글 자판 상태와 무관하게 잡힌다.
  if (e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && e.code === "KeyV") {
    e.preventDefault();                     // 윈도우 크롬의 네이티브 paste 와 중복 방지
    pasteFromClipboard(pane);
    return false;
  }
  // Ctrl+C — 복사냐 중단신호냐. ①브라우저 선택이 있으면(⌥드래그) 복사 ②방금(5초 안) 드래그로
  // 골라 놓았으면 복사(tmux 선택이라 브라우저는 '선택 없음'으로 보인다 — 이걸 SIGINT 로 보내
  // 클로드를 끊어 먹던 것이 실제로 겪은 그 사고다) ③그 외에는 원래대로 중단신호.
  if (e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && e.code === "KeyC") {
    if (term?.hasSelection()) {
      navigator.clipboard?.writeText(term.getSelection()).catch(() => {});
      term.clearSelection();
      showToast("복사됨");
      e.preventDefault();
      return false;
    }
    if (Date.now() - lastCopyAt < 5000) {   // 드래그 직후 = 복사하려는 것이 분명하다
      syncCopyToClipboard(pane);
      e.preventDefault();
      return false;
    }
  }
  // ⌘Z = 실행 취소. 터미널엔 ⌘ 개념이 없으니 같은 뜻의 제어문자로 옮긴다 —
  // 클로드 코드 입력창도 zsh 입력줄도 Ctrl+_(0x1f)가 undo 다(2026-08-15 실측).
  if (e.metaKey && !e.ctrlKey && !e.altKey && e.code === "KeyZ") {
    e.preventDefault();
    pane.send("\x1f");
    return false;
  }
  // ⌘← / ⌘→ = 줄 처음·끝(맥 텍스트 관례). 지금까지는 브라우저가 ⌘← 를
  // '뒤로 가기'로 먹어 아무 일도 안 났다. 터미널엔 ⌘ 가 없으니 Home/End 키와 같은 바이트로 옮긴다 —
  // tmux 도 클로드 입력줄도 그대로 알아듣는다. 커서키 모드(DECCKM)를 켠 프로그램은 ESC O 꼴을
  // 기대하므로 xterm 이 실제 Home/End 에 보내는 것과 똑같이 가른다. 한글 조합 중엔 IME 몫으로 둔다.
  if (e.metaKey && !e.ctrlKey && !e.altKey && (e.code === "ArrowLeft" || e.code === "ArrowRight")) {
    if (e.isComposing) return true;
    e.preventDefault();
    const app = !!term?.modes?.applicationCursorKeysMode;
    pane.send(e.code === "ArrowLeft" ? (app ? "\x1bOH" : "\x1b[H") : (app ? "\x1bOF" : "\x1b[F"));
    return false;
  }
  if (!e.metaKey || e.key !== "Backspace") return true;
  e.preventDefault();                       // 브라우저가 숨은 textarea 를 건드리지 않게
  pane.send("\x15");
  return false;                             // xterm 은 이 키를 처리하지 않는다(중복 전송 방지)
}

// ---------- 안드로이드 IME live-preedit mirror ----------
// 출처: github.com/stablyai/orca mobile/src/terminal/terminal-live-preedit-mirror.ts (MIT).
// 실기 로그에서 삼성 예측입력이 확정 문장을 다음 조합으로 되끌고, xterm 5.5·6.0 의 textarea diff 가
// 그 문장 전체를 다시 보냈다. textarea 속성 억제·password 전환·조합 중 값 비우기·blur/focus 리셋은
// 각각 무효이거나 복사·화살표·자모 결합을 깨뜨려 폐기했다. 여기서는 textarea 문서를 그대로 소유하고,
// 이미 보낸 문서의 거울과 공통 접두부만 비교해 DEL+새 꼬리를 보낸다.
// 알려진 한계: 제어키 리셋 직후 삼성이 직전 단어를 다시 밀면 그 단어 한 번은 재전송될 수 있다.
const TERMINAL_LIVE_HELD_PREEDIT_COMMIT_DELAY_MS = 300;
const TERMINAL_DEL_BYTE = "\x7f";
const LAST_ASCII_CODE_POINT = 0x7f;
const ANDROID_TERMINAL_LIVE_MIRROR = /Android/i.test(navigator.userAgent)
  || new URLSearchParams(location.search).has("androidime")
  || globalThis.FROG_FORCE_ANDROID_IME === true;

function heldPreeditLength(fieldCodePoints, stableLength, composing) {
  if (composing !== undefined) return composing ? fieldCodePoints.length - stableLength : 0;
  let held = 0;
  while (held < fieldCodePoints.length &&
    (fieldCodePoints[fieldCodePoints.length - 1 - held]?.codePointAt(0) ?? 0) > LAST_ASCII_CODE_POINT) {
    held += 1;
  }
  return Math.min(held, fieldCodePoints.length - stableLength);
}

function commonPrefixLength(left, right) {
  let length = 0;
  while (length < left.length && length < right.length && left[length] === right[length]) length += 1;
  return length;
}

function computeTerminalLiveMirrorStep(sentText, fieldText, options) {
  const fieldCodePoints = Array.from(fieldText);
  const sentCodePoints = Array.from(sentText);
  const stableLength = commonPrefixLength(sentCodePoints, fieldCodePoints);
  const heldLength = options.commitHeld ? 0
    : heldPreeditLength(fieldCodePoints, stableLength, options.composing);
  const targetCodePoints = fieldCodePoints.slice(0, fieldCodePoints.length - heldLength);
  const keptLength = Math.min(stableLength, targetCodePoints.length);
  return {
    eraseCount: sentCodePoints.length - keptLength,
    appendText: targetCodePoints.slice(keptLength).join(""),
    nextSentText: targetCodePoints.join(""),
    heldText: fieldCodePoints.slice(fieldCodePoints.length - heldLength).join(""),
  };
}

function buildTerminalLiveMirrorPayload(step) {
  return TERMINAL_DEL_BYTE.repeat(step.eraseCount) + step.appendText;
}

function installTerminalLiveMirror(openTerm, send) {
  if (!ANDROID_TERMINAL_LIVE_MIRROR) return null;
  const textarea = openTerm.textarea;
  const helper = openTerm._core?._compositionHelper;
  const patchedNames = ["compositionstart", "compositionupdate", "compositionend", "_handleAnyTextareaChanges"];
  if (!textarea || !helper || patchedNames.some(name => typeof helper[name] !== "function")) {
    console.warn("[frogterminal] Android IME mirror 비활성 — xterm CompositionHelper 6.0.0 심볼이 달라졌습니다");
    return null;                              // private API 가 바뀌면 기존 xterm+guard 경로로 물러난다
  }

  const originals = Object.fromEntries(patchedNames.map(name => [name, helper[name]]));
  const noop = () => {};
  patchedNames.forEach(name => { helper[name] = noop; });

  let sentText = "";
  let composing = false;
  let resetPending = false;
  let heldTimer = null;
  let disposed = false;

  const clearHeldTimer = () => { clearTimeout(heldTimer); heldTimer = null; };
  const applyMirror = (commitHeld = false) => {
    if (disposed || resetPending) return;
    clearHeldTimer();
    const step = computeTerminalLiveMirrorStep(sentText, textarea.value,
      { composing, commitHeld });
    const payload = buildTerminalLiveMirrorPayload(step);
    if (!payload || send(payload)) {
      sentText = step.nextSentText;
      if (payload) imeLog("→미러", payload);
    }
    if (!commitHeld && step.heldText)
      heldTimer = setTimeout(() => applyMirror(true), TERMINAL_LIVE_HELD_PREEDIT_COMMIT_DELAY_MS);
  };
  const commitHeld = () => applyMirror(true);
  const resetAfterExternalData = () => {
    commitHeld();                            // 제어키·붙여넣기는 반드시 보류 글자 뒤에 선다
    clearHeldTimer();
    sentText = "";
    if (composing) resetPending = true;      // 조합 중 textarea 를 건드리면 자모가 풀린다 — end까지 미룬다
    else textarea.value = "";
  };
  const resumeAfterPendingReset = () => {
    if (!resetPending) return false;
    // Enter 뒤 Chrome이 compositionend를 생략해도 다음 compositionupdate/input은 새 문서 세대다.
    // textarea는 그대로 두고 거울만 빈 문서에서 다시 시작한다 — 조합 중 값 변경 금지 원칙을 지킨다.
    resetPending = false;
    sentText = "";
    clearHeldTimer();
    return true;
  };

  const onCompositionStart = e => {
    composing = true;
    imeLog("미러 start", e.data);
    if (resumeAfterPendingReset()) return;    // start 때는 아직 이전 field 값일 수 있어 update를 기다린다
    applyMirror();
  };
  const onCompositionUpdate = e => {
    imeLog("미러 update", e.data);
    resumeAfterPendingReset();               // Enter 뒤 end 없이 이어진 새 조합도 여기서 다시 연다
    applyMirror();
  };
  const onCompositionEnd = e => {
    composing = false;
    imeJustCommitted = true;
    setTimeout(() => { imeJustCommitted = false; });
    imeLog("미러 END", e.data);
    if (resetPending) {
      resetPending = false;
      sentText = "";
      textarea.value = "";                  // 이제 조합이 끝났으므로 안전하게 새 문서 경계를 연다
      clearHeldTimer();
      return;
    }
    applyMirror();
  };
  const onInputCapture = e => {
    if (e.target !== textarea) return;
    e.stopPropagation();                    // xterm 6.0 _inputEvent 의 전체값 전송+value="" 를 막는다
    resumeAfterPendingReset();               // 조합 이벤트가 없는 IME 입력도 새 문서에서 시작한다
    applyMirror();
  };
  const onBlurCapture = e => {
    e.stopImmediatePropagation();           // xterm blur 의 value="" 도 막아 IME 와 같은 문서를 유지한다
    imeLog("미러 blur", textarea.value);
    commitHeld();                            // blur 는 필드·거울을 리셋하지 않는다
  };
  const onControlKeyCapture = e => {
    if (e.target !== textarea || e.type !== "keydown") return;
    const movesCursor = ["Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]
      .includes(e.key);
    const controlCombo = (e.ctrlKey || e.altKey || e.metaKey) && e.key !== "Shift";
    if (movesCursor || controlCombo) resetAfterExternalData();
  };

  textarea.addEventListener("compositionstart", onCompositionStart);
  textarea.addEventListener("compositionupdate", onCompositionUpdate);
  textarea.addEventListener("compositionend", onCompositionEnd);
  textarea.addEventListener("blur", onBlurCapture, true);
  document.addEventListener("input", onInputCapture, true);       // xterm target capture보다 먼저 받는다
  document.addEventListener("keydown", onControlKeyCapture, true);

  return {
    beforeExternalData: resetAfterExternalData,
    dispose() {
      disposed = true;
      clearHeldTimer();
      textarea.removeEventListener("compositionstart", onCompositionStart);
      textarea.removeEventListener("compositionupdate", onCompositionUpdate);
      textarea.removeEventListener("compositionend", onCompositionEnd);
      textarea.removeEventListener("blur", onBlurCapture, true);
      document.removeEventListener("input", onInputCapture, true);
      document.removeEventListener("keydown", onControlKeyCapture, true);
      patchedNames.forEach(name => { helper[name] = originals[name]; });
    },
  };
}

// ---------- 비안드로이드 IME 마지막 글자 보상 ----------
// 맥·iOS 는 xterm 기본 조합 경로가 정상이다. 확정값을 blur 순간에만 보상하고, xterm 이 먼저 보낸
// 접두부만 차감한다. 삼성 되끌어오기 때문에 추가했던 sentDuringComposition 플래그는 mirror가
// 대신하므로 여기서 제거했다.
let imeJustCommitted = false;                // 확정 직후 한 틱 — ⇧Enter(macEditKeys)가 xterm 의
                                             // 지연 전송 뒤에 줄바꿈을 세우는 데 쓴다
// ---------- IME 진단 (임시) ----------
// 주소 뒤에 ?imedebug=1 을 붙이면 화면 아래에 '무엇이 어떤 순서로 나갔는지'가 찍힌다.
// 폰 IME 는 기기·자판마다 이벤트 순서가 달라 추측으로는 못 잡는다 — 실기에서 한 번 보고 고친다.
// 평소(파라미터 없음)엔 함수가 즉시 반환해 아무 일도 하지 않는다.
const IME_DEBUG = new URLSearchParams(location.search).has("imedebug");
const imeLines = [];                         // 화면에 안 보여도 계속 쌓는다 — [보내기] 가 이걸 올린다
let imeLogBox = null;
function imeLog(tag, val) {
  if (!IME_DEBUG) return;
  // 터미널 색상 질의 응답(OSC 10/11 등)은 IME 와 무관한 잡음이라 로그에서 뺀다
  if (tag === "→보냄" && typeof val === "string" && val.startsWith("\x1b]")) return;
  imeLines.push(`${tag} ${JSON.stringify(val ?? "")}`);
  if (imeLines.length > 800) imeLines.shift();
  if (!imeLogBox) imeDebugPanel();
  imeLogBox.textContent = `IME 로그 ${imeLines.length}줄`;   // 내용은 안 띄운다 — 화면을 가리면 재현을 못 한다
}

function imeDebugPanel() {
  // 버튼을 로그와 같은 줄에 두었더니 긴 글자에 밀려 화면 밖으로 나갔다 —
  // 버튼은 화면 오른쪽 위에 따로 못박고, 로그 내용은 아예 띄우지 않는다(읽는 건 나다).
  imeLogBox = document.createElement("div");
  imeLogBox.id = "ime-debug";
  imeLogBox.style.cssText = "position:fixed;left:6px;top:6px;z-index:10000;color:#7fd97f;" +
    "background:#000c;padding:3px 6px;border-radius:5px;font:11px/1.2 monospace";
  const send = document.createElement("button");
  send.id = "ime-debug-send";
  send.textContent = "로그 보내기";
  send.style.cssText = "position:fixed;right:6px;top:6px;z-index:10000;padding:8px 12px;" +
    "background:#2a7a4a;color:#fff;border:0;border-radius:6px;font:13px/1 sans-serif";
  send.onclick = async () => {
    send.textContent = "보내는 중…";
    const body = new FormData();
    const name = `ime-log-${imeLines.length}.txt`;
    body.append("file", new Blob([imeLines.join("\n")], { type: "text/plain" }), name);
    try {
      const res = await fetch(
        `${API}/api/upload?project=${encodeURIComponent(baseOf(currentSession?.name || "frogterminal"))}`,
        { method: "POST", body });
      send.textContent = res.ok ? `보냄 (${imeLines.length}줄)` : `실패 ${res.status}`;
    } catch { send.textContent = "실패(연결)"; }
    setTimeout(() => { send.textContent = "로그 보내기"; }, 3000);
  };
  document.body.append(imeLogBox, send);
}

function guardComposition(textarea, send) {
  let committed = "";
  textarea.addEventListener("compositionstart", e => imeLog("start", e.data));
  textarea.addEventListener("compositionupdate", e => imeLog("update", e.data));
  textarea.addEventListener("compositionend", e => {
    imeLog("END(보상함)", e.data);
    committed = e.data ?? "";
    imeJustCommitted = true;
    setTimeout(() => { committed = ""; imeJustCommitted = false; });   // blur 경합창이 끝나면 평소 입력 상태로
  });
  textarea.addEventListener("blur", () => {
    imeLog("blur", committed);
    if (committed) send(committed);
    committed = "";
  });
  textarea.addEventListener("keydown", e => {
    if (e.key === "Backspace" || e.key === "Enter" || e.key === " ")
      imeLog("key", `${e.key}${e.isComposing ? " (조합중)" : ""}`);
  });
  return data => {
    if (!data || !committed) return;
    if (committed.startsWith(data)) committed = committed.slice(data.length);  // 일부만 나감
    else if (data.startsWith(committed)) committed = "";                      // 전부(또는 함께) 나감
  };
}

// ---------- 붙여넣기·드래그 = 자동저장 + 경로 자동입력 ----------

async function uploadAndType(file, pane = activePane) {
  if (file.size > FROG.MAX_UPLOAD_BYTES) { pane?.term?.write("\r\n[frogterminal] 파일이 50MB를 넘습니다\r\n"); return; }
  const target = pane?.session?.name;        // 업로드가 도는 사이 세션을 갈아탈 수 있다
  if (!target) return;
  const body = new FormData();
  body.append("file", file, file.name);
  try {
    const res = await fetch(`${API}/api/upload?project=${encodeURIComponent(baseOf(target))}`,
                            { method: "POST", body });
    if (!res.ok) throw new Error(res.status);
    const { path } = await res.json();
    if (pane.session?.name !== target) return;   // 세션이 바뀌었다 — 엉뚱한 터미널에 안 찍는다
    typeQuoted(path, pane);                  // 접속 직후면 큐(pendingPaste) — onopen 때 찍힌다
  } catch {
    pane.term?.write("\r\n[frogterminal] 업로드 실패 — 경로 입력 안 함\r\n");   // 반쪽 입력 방지
  }
}

// Ctrl+V 전용 — 클립보드를 직접 읽는다(⌘V 는 브라우저가 paste 이벤트로 알아서 처리).
// 이미지가 있으면 업로드해 경로를 찍고, 아니면 글자를 xterm paste 로(여러 줄도 한 덩어리).
async function pasteFromClipboard(pane = activePane) {
  // 재시도 계약: 휠클릭 캡처 직후엔 클립보드에 이미지가 실리기까지
  // 시차가 있다 — 바로 붙여넣으면 빈 클립보드를 읽고 조용히 실패했다. 최대 ~1.2초 기다려 잡는다.
  // (창 포커스 복귀 직전의 "Document is not focused" 도 같은 재시도로 넘어간다.)
  for (let attempt = 0; attempt < 4; attempt++) {
    if (attempt) await new Promise(r => setTimeout(r, 300));
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const imgType = item.types.find(t => t.startsWith("image/"));
        if (imgType) { pasteImage(await item.getType(imgType), imgType, null, pane); return; }
      }
      const text = await navigator.clipboard.readText();
      if (text) {
        pane?.mirror?.beforeExternalData(); // paste는 mirror를 안 거쳐 xterm onData로 가므로 문서 경계를 끊는다
        pane?.term?.paste(text);
        return;
      }
    } catch { /* 아직 못 읽는 순간 — 다음 시도로 */ }
  }
  pane?.term?.write("\r\n[frogterminal] 클립보드가 비어 있거나 읽지 못했습니다 — 권한을 허용하거나 ⌘V 를 쓰세요\r\n");
}
// 캡처 단계 필수 — xterm 의 paste 핸들러가 stopPropagation() 을 불러 버블 단계까지 안 온다
document.addEventListener("paste", e => {
  if (e.target?.closest?.("#browser-panel")) return;   // 주소칸에 붙여넣기는 그 칸의 몫 — 터미널로 새지 않게
  const files = [...(e.clipboardData?.files ?? [])];
  if (!files.length) {
    activePane?.mirror?.beforeExternalData(); // 웹 paste도 xterm이 직접 보내므로 직전 field와 어긋나지 않게 한다
    // 캡처 직후의 ⌘V — 클립보드에 이미지가 실리기 전이라 이벤트가 통째로 비어 오는 경우가 있다.
    // 그때만 재시도 경로(pasteFromClipboard)로 넘긴다. 글자가 실려 있으면 xterm 몫 그대로.
    if (!document.getElementById("term-view").hidden && !e.clipboardData?.items?.length)
      pasteFromClipboard();
    return;
  }
  if (document.getElementById("term-view").hidden) {
    // 목록·인박스 화면의 붙여넣기는 원래 무시였다 — 소리 없이 사라져 "안 된다"로 보였으니 말해 준다
    listHint("이미지·파일 붙여넣기는 터미널을 연 뒤에 하세요 — 목록 화면에서는 붙지 않습니다");
    return;
  }
  e.preventDefault();
  const pane = activePane;                   // 붙여넣기는 키보드가 있는 칸 = 활성 칸
  files.forEach(f => pasteAny(f, pane));
}, true);

// 붙여넣은 '파일' 한 개의 처리 순서 — 사본은 마지막 수단이다.
//   ① 원본 찾기(파인더에서 ⌘C 한 파일: 이름·크기·수정시각으로 특정)
//   ② 이미지면 캡처 원본(같은 픽셀 크기의 방금 찍은 캡처 파일)
//   ③ 그래도 못 찾으면 업로드 — 폰에서 고른 사진처럼 맥에 원본이 없는 경우다
async function pasteAny(file, pane) {
  const found = await locateOriginal(
    { kind: "file", name: file.name, size: file.size, mtimeMs: file.lastModified });
  if (found) { typeQuoted(found, pane); showToast("원본 경로 — 사본 안 만듦"); return; }
  if (file.type.startsWith("image/")) { pasteImage(file, file.type, file.name, pane); return; }
  uploadAndType(file, pane);
}

// 붙여넣은 이미지 — 사본을 만들기 전에 '캡처 원본이 파일로 남아 있나'부터 본다(캡처가
// 파일로 있으면 매번 사본을 만들지 말고 그 경로를 넘긴다). 맥 캡처는 설정된
// 폴더에 파일로도 남으므로, 붙여넣은 그림과 픽셀 크기가 같은 최신 캡처를 브릿지가 찾아 준다.
async function pasteImage(blob, type, name, pane = activePane) {
  try {
    const bmp = await createImageBitmap(blob);
    const res = await fetch(`${API}/api/capture?w=${bmp.width}&h=${bmp.height}`);
    if (res.ok) {
      const { path } = await res.json();
      if (path) { typeQuoted(path, pane); showToast("캡처 원본 경로 — 사본 안 만듦"); return; }
    }
  } catch { /* 크기를 못 읽거나 브릿지가 못 찾음 — 아래 업로드로 */ }
  const ext = (type.slice(6) || "png").replace("jpeg", "jpg").replace(/[^a-z0-9]/g, "") || "png";
  uploadAndType(new File([blob], name || `클립보드.${ext}`, { type }), pane);   // 이름이 있으면 살린다
}
// 드래그앤드롭은 전부 캡처 단계에서 우리가 먼저 받는다. 버블로 받으면 xterm·브라우저 기본
// 동작(드래그 텍스트를 포커스된 입력에 타이핑)이 먼저 먹어서, 경로 대신 글자 조각이 터미널로
// 새어 들어갔다(실측 — 화면에 엉뚱한 글자가 찍히고 정작 경로는 안 들어감).
document.addEventListener("dragover", e => {
  e.preventDefault();
  if (e.dataTransfer.types.includes("application/x-frog-path"))
    e.dataTransfer.dropEffect = "copy";      // 커서에 '복사' 표시 — 끌어도 되는 곳이라는 신호
}, true);
document.addEventListener("drop", e => {
  e.preventDefault();
  e.stopPropagation();                        // 기본 텍스트 드롭·다른 핸들러로 새지 않게
  if (document.getElementById("term-view").hidden) return;
  if (e.target?.closest?.("#browser-panel")) return;   // 패널 위에 떨어뜨린 것은 터미널 업로드가 아니다
  // 파일 패널에서 끌어온 것(files.js 가 심는 자체 타입) — 업로드가 아니라 그 경로를 입력한다.
  // 파일·폴더 모두 같은 길(VS Code·Zed 의 '트리에서 터미널로 드래그').
  const frogPath = e.dataTransfer.getData("application/x-frog-path");
  const pane = paneAt(e.target);             // 떨어뜨린 칸 — 칸 밖(머리줄 등)이면 활성 칸
  if (frogPath) { pane?.send(shellQuote(frogPath) + " "); pane?.term?.focus(); return; }
  // 파인더·탐색기 드래그 — dataTransfer 는 핸들러가 끝나면 무효가 되므로 여기서 동기로 집는다.
  const entries = [...(e.dataTransfer.items ?? [])]
    .map(it => it.kind === "file" ? it.webkitGetAsEntry?.() : null).filter(Boolean);
  if (entries.length) dropItems(entries, pane);
  else [...e.dataTransfer.files].forEach(f => uploadAndType(f, pane));   // entry API 없는 옛 브라우저 폴백
}, true);

// 드롭 처리의 최상위 — 항목마다 ①맥에서 원본을 찾으면 그 진짜 경로를 찍고(사본 없음),
// ②못 찾으면(폰에서 끌었거나 미색인 위치) 지금까지처럼 업로드 후 사본 경로를 찍는다.
// 배경: 전부 사본으로 가니 다른 폴더의 같은 이름이 뭉개지고 캐시만 늘었다.
async function dropItems(entries, pane) {
  for (const en of entries) {
    if (en.isDirectory) {
      const kids = await firstLevelNames(en);
      const found = await locateOriginal({ kind: "dir", name: en.name, children: kids });
      if (found) typeQuoted(found, pane);
      else await uploadEntries([en], pane);
    } else {
      let file;
      try { file = await new Promise((ok, no) => en.file(ok, no)); } catch { continue; }
      const found = await locateOriginal(
        { kind: "file", name: file.name, size: file.size, mtimeMs: file.lastModified });
      if (found) typeQuoted(found, pane);
      else await uploadAndType(file, pane);
    }
  }
}

async function firstLevelNames(entry) {      // 폴더 특정용 1단계 자식 이름 — 30개면 충분하다
  const rd = entry.createReader();
  const names = [];
  try {
    for (;;) {
      const batch = await new Promise((ok, no) => rd.readEntries(ok, no));
      if (!batch.length) break;
      for (const c of batch) names.push(c.name);
      if (names.length >= 30) break;
    }
  } catch { /* 못 읽으면 빈 목록 — 서버가 특정 실패로 답해 업로드로 물러선다 */ }
  return names.slice(0, 30);
}

async function locateOriginal(body) {
  try {
    const res = await fetch(`${API}/api/locate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    return (await res.json()).path ?? null;
  } catch { return null; }
}

function typeQuoted(path, pane = activePane) {   // 경로만 타이핑 — 실행은 절대 안 함. 연결 전이면 큐로.
  if (!pane) return;
  const line = shellQuote(path) + " ";
  pane.mirror?.beforeExternalData();         // 업로드·드롭 경로는 send() 직행이라 mirror 밖 입력이다
  if (pane.ws?.readyState === 1) pane.send(line);
  else pane.pendingPaste.push(line);
}

// 드롭된 entry 트리를 걸어 파일 목록을 모은다. 숨김(.DS_Store 류)은 걷어낸다.
// readEntries 는 한 번에 최대 100개만 주므로 빌 때까지 반복하는 것이 계약이다.
async function walkEntry(entry, dir, out) {
  if (entry.name.startsWith(".")) return;
  if (entry.isFile) {
    out.push({ file: await new Promise((ok, no) => entry.file(ok, no)), dir });
  } else if (entry.isDirectory) {
    const sub = dir ? `${dir}/${entry.name}` : entry.name;
    const rd = entry.createReader();
    for (;;) {
      const batch = await new Promise((ok, no) => rd.readEntries(ok, no));
      if (!batch.length) break;
      for (const child of batch) await walkEntry(child, sub, out);
    }
  }
}

async function uploadEntries(entries, pane = activePane) {
  const target = pane?.session?.name;
  if (!target) return;
  const out = [];
  try {
    for (const en of entries) await walkEntry(en, "", out);
  } catch {
    pane.term?.write("\r\n[frogterminal] 폴더를 읽지 못했습니다 — 다시 끌어보세요\r\n");
    return;
  }
  if (!out.length) { pane.term?.write("\r\n[frogterminal] 빈 폴더 — 올릴 파일이 없습니다\r\n"); return; }
  if (out.length > 300) {
    pane.term?.write(`\r\n[frogterminal] 파일 ${out.length}개 — 너무 많아 올리지 않습니다(상한 300)\r\n`);
    return;
  }
  let first = null, fail = 0;
  for (const { file, dir } of out) {          // 순차 업로드 — 서버·디스크에 부드럽게
    if (file.size > FROG.MAX_UPLOAD_BYTES) { fail++; continue; }
    const body = new FormData();
    body.append("file", file, file.name);
    try {
      const res = await fetch(`${API}/api/upload?project=${encodeURIComponent(baseOf(target))}` +
                              (dir ? `&dir=${encodeURIComponent(dir)}` : ""), { method: "POST", body });
      if (!res.ok) { fail++; continue; }
      const got = await res.json();
      first ??= got.top ?? got.path;          // 폴더면 꼭대기 경로 한 번만 찍는다
    } catch { fail++; }
  }
  if (pane.session?.name !== target) return;   // 그 사이 세션을 갈아탔다 — 엉뚱한 터미널 방지
  if (first) typeQuoted(first, pane);
  if (fail) pane.term?.write(`\r\n[frogterminal] 폴더 업로드 — ${out.length - fail}/${out.length}개 성공(일부 실패)\r\n`);
}

// ---------- 폰 특수키 바 ----------
function setCtrl(on) { ctrlArmed = on; document.getElementById("ctrl-key").classList.toggle("armed", on); }
document.querySelectorAll("#keybar [data-seq]").forEach(btn => {
  // Android mirror/비Android blur 보상 모두 조합 글자를 제어키보다 먼저 보낸다는 순서가 계약이다
  btn.onclick = () => {
    activePane?.mirror?.beforeExternalData();
    send(JSON.parse(`"${btn.dataset.seq}"`));
    term?.focus();
  };
});
document.getElementById("ctrl-key").onclick = () => { setCtrl(!ctrlArmed); term?.focus(); };

// 📎 사진·파일 넣기 — 폰에는 파인더 드래그도, 쓸 만한 클립보드 이미지도 없어서 여기가 유일한
// 길이다. 규칙은 드래그와 같게 둔다: 맥에 원본이 있으면 그 경로를 찍고(사본 없음),
// 폰 사진처럼 맥에 없는 것만 업로드한다. 그래야 같은 파일이 두 벌로 갈리지 않는다.
const filePick = document.getElementById("file-pick");
document.getElementById("pick-file").onclick = () => filePick.click();
filePick.onchange = async () => {
  for (const f of filePick.files) {
    const found = await locateOriginal(
      { kind: "file", name: f.name, size: f.size, mtimeMs: f.lastModified });
    if (found) typeQuoted(found);
    else await uploadAndType(f);
  }
  filePick.value = "";                       // 같은 파일을 다시 골라도 change 가 오게 비운다
  term?.focus();
};

document.getElementById("back").onclick = () => { closeTerminal(); showList(); };

// ---------- 상단 요약·인박스 ----------
document.getElementById("sum-pending").onclick = () => {
  if (document.getElementById("inbox-view").hidden) openInbox();
  else closeInbox();                          // 같은 버튼이 토글 — 열고 닫는 길이 하나 더 생긴다
};
document.getElementById("inbox-back").onclick = () => closeInbox();

if (!REDIRECTING) {                          // 문패 화면에선 부르지 않는다 (실패 안내만 깜빡이므로)
  // 첫 목록이 와야 칸을 채울 수 있다 — 기억한 칸 수로 연다(grid.js). 사무실 화면은 그 뒤에 되살린다
  // (칸을 되살리며 터미널을 열면 사무실이 닫히므로 순서가 거꾸로면 켜 둔 사무실이 사라진다).
  loadSessions().then(() => gridBoot()).then(() => window.officeBoot?.());
  refreshSummary();
  refreshStatusMeta();
  setInterval(poll, 7000);                   // 7초 폴링 — 요약·대시보드는 항상, 목록·인박스는 보일 때만
  setInterval(refreshStatusMeta, 60000);     // 계정·사용량 — 서버 캐시(60초·5분)에 맞춘 긴 주기
  // 오래 비워 둔 창으로 돌아왔을 때(한참 뒤에 들어가면 옛 화면이 몇 초 남던 문제).
  // 백그라운드에서 브라우저가 타이머를 조여 두면 화면 값이 낡은 채로 멈춰 있고, 잠들었다 깬
  // 뒤엔 소켓이 죽은 줄도 모른 채 백오프(최대 15초)를 기다린다. 돌아오는 즉시 둘 다 되살린다.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    poll();                                  // 낡은 목록·결재 수를 곧바로 새로
    for (const p of panes) {
      if (p.term && p.ws?.readyState !== 1) {  // 소켓이 죽었거나 아직 붙는 중 — 백오프를 건너뛴다
        clearTimeout(p.reconnectTimer);
        p.connect(0);
      } else if (p.term) p.ping();             // '열림'이라도 반쯤 죽었을 수 있다 — 곧바로 생존 확인
    }
  });
}

// 폴링 한 판 — 겹치지 않게 한 번에 하나만 돈다(single-flight). 응답이 느린 동안 7초마다 새 요청이
// 쌓이면, 창을 다시 열었을 때 밀린 요청이 한꺼번에 몰려 화면이 굳는다(코덱스 분석 지적).
let polling = false;
async function poll() {
  if (polling || document.hidden) return;    // 안 보이는 동안은 쉰다 — 돌아올 때 한 번에 새로 읽는다
  polling = true;
  try {
    const jobs = [refreshSummary()];
    if (!document.getElementById("list-view").classList.contains("offscreen")) jobs.push(loadSessions());
    // 결재는 상단 대시보드가 늘 떠 있으므로 매번 다시 읽는다. 인박스가 열려 있으면 그 한 번이
    // 대시보드까지 그린다(loadInbox → renderDash) — 같은 응답이라 두 화면의 수가 어긋날 수 없다.
    jobs.push(document.getElementById("inbox-view").hidden ? loadDash() : loadInbox());
    if (typeof fpRefresh === "function") jobs.push(fpRefresh());   // 파일 패널 — 펼친 폴더만 다시 읽는다(files.js)
    await Promise.allSettled(jobs);
  } finally {
    polling = false;
  }
}
