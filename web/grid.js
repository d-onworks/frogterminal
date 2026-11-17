"use strict";
// 멀티 칸 격자 — 상단 [1][4][6][8], 칸 머리줄의 프로젝트 고르기, 활성 칸 전환, 배치 기억
// (설계 docs/superpowers/specs/2026-10-09-multipane-grid-design.md).
// 칸 하나의 터미널 동작은 pane.js, 화면 공통부(위 머리줄·탭 바·파일/브라우저 패널)는 app.js 가 맡는다.
// 데스크톱 전용 — 폰은 한 화면씩 갈아끼우는 배치라 늘 1칸이다.

const GRID_KEY = "frog.grid";
const GRID_SIZES = [1, 4, 6, 8];
const GRID_DEFAULT = 4;                       // 기억값이 없으면 4칸

// 이 기기에 남긴 배치 — {n: 고른 칸 수, names: 칸별 세션 이름}. 기기별이라 폰·다른 맥은 따라오지 않는다.
function gridLoad() {
  try {
    const g = JSON.parse(localStorage.getItem(GRID_KEY) ?? "null");
    if (g && GRID_SIZES.includes(g.n))
      return { n: g.n, names: Array.isArray(g.names) ? g.names : [] };
  } catch { /* 깨진 값·막힌 저장소 — 기본값으로 */ }
  return { n: GRID_DEFAULT, names: [] };
}

let gridChoice = gridLoad().n;                // 사용자가 고른 칸 수 — 폰 폭에서 1칸으로 접혀도 이 값은 남는다

function gridSave() {
  if (!isDesktop()) return;                   // 폰의 강제 1칸이 데스크톱 배치를 덮지 않게
  try {
    localStorage.setItem(GRID_KEY, JSON.stringify({
      n: gridChoice, names: panes.map(p => p.session?.name ?? null) }));
  } catch { /* 저장 못 해도 지금 화면은 그대로 */ }
}

// 사이드바에 보이는 것만 고를 수 있다 — 숨긴 것·프로젝트 아닌 폴더는 사이드바와 같은 잣대로 뺀다.
const gridPickable = s => !s.hidden && s.project !== false;

// 이름 → 열 수 있는 세션. 탭(base@N)은 목록에 따로 없고 기본 세션의 tabs 에 번호로만 온다.
function gridSession(name) {
  if (!name) return null;
  const base = baseOf(name);
  const entry = lastSessions.find(s => s.name === base);
  if (!entry) return null;
  if (name === base) return entry;
  return entry.tabs?.includes(Number(name.slice(-1))) ? { name, path: entry.path, alive: true } : null;
}

// ---------- 칸 수 바꾸기 ----------
// names — 칸 자리별로 먼저 앉힐 세션 이름(이 기기 기억). 나머지 빈 칸은 사이드바 순서의 켜진 세션.
// 꺼진 프로젝트는 저절로 켜지 않는다 — 칸 수 하나 고를 때 에이전트가 여럿 동시에 뜨면 안 된다.
// save=false — 폰 폭 진입처럼 화면이 강제로 바꾼 것. 사용자가 고른 칸 수(gridChoice)는 그대로 둔다.
function setGrid(n, names = [], save = true) {
  if (!GRID_SIZES.includes(n)) return;
  if (save) gridChoice = n;
  clearMax();                                 // 칸 수를 바꾸면 크게 보기는 풀고 새 격자 그대로
  if (!isDesktop()) n = 1;
  const box = document.getElementById("panes");
  // 줄일 때 활성 칸은 남긴다 — 지금 치던 터미널이 사라지면 안 된다. 사라지는 칸은 세션을 끄지
  // 않고 붙어 있던 소켓만 뗀다(tmux 세션은 그대로 살아 있다).
  const ai = panes.indexOf(activePane);
  if (ai >= n) {
    panes.splice(ai, 1);
    panes.splice(n - 1, 0, activePane);
    for (const p of panes) box.append(p.root);   // 화면 순서도 배열 순서에 맞춘다
  }
  while (panes.length > n) {
    const p = panes.pop();
    p.close();
    p.root.remove();
  }
  while (panes.length < n) panes.push(makePane(box));
  box.dataset.n = String(n);
  if (n > 1) {
    showView("term");                         // 칸 크기가 잡힌 뒤에 터미널을 연다 — 첫 fit 이 제 크기로
    gridFoldPanels();
    setActive(activePane, true);
    gridFill(names);
  } else {
    setActive(activePane, true);
    if (!activePane.session) showList();       // 1칸인데 볼 것이 없다 — 처음 화면(빈 가운데)으로
    else { fpAutoOpen(); bpSync?.(); }         // 1칸으로 돌아왔다 — 패널은 원래 규칙대로
  }
  gridSave();
  renderGridBar();
  loadDash();                                 // 결재 대시보드 — 여러 칸이면 접힘이 기본(dash.js)
  for (const p of panes) p.sendCompact();     // 클로드 HUD 를 칸 수에 맞게(여러 칸 = 첫 줄만)
  refreshHud();
}

// 여러 칸에서는 파일·브라우저 패널을 접는다 — 칸마다 폭이 좁은데 패널까지 깔면 터미널이 짜부라진다.
// 접기만 한다(📁·🌐 기억은 안 건드린다). 저장 안 한 편집이 있으면 fpGo 가 되묻고, 그때는 안 접는다.
function gridFoldPanels() {
  if (!fpPanel().hidden) fpGo(fpClose);
  if (!bpPanel().hidden) bpClose();
}

function gridFill(names) {
  const taken = new Set(panes.map(p => p.session?.name).filter(Boolean));
  panes.forEach((p, i) => {                   // ① 이 기기에 기억해 둔 자리 그대로
    if (p.session) return;
    const s = gridSession(names[i]);
    if (s?.alive && !taken.has(s.name)) { taken.add(s.name); openIn(p, s); }
  });
  const queue = lastSessions.filter(s => gridPickable(s) && s.alive && !taken.has(s.name));
  for (const p of panes) {                    // ② 남은 빈 칸은 사이드바 순서(즐겨찾기→결재→진행중…)로
    if (p.session) continue;
    const s = queue.shift();
    if (!s) break;
    taken.add(s.name);
    openIn(p, s);
  }
  renderPicks();
}

function makePane(box) {
  const root = document.createElement("div");
  root.className = "pane";
  const host = document.createElement("div");
  host.className = "pane-term";
  host.style.background = termOptions().theme.background;   // 첫 칸(#terminal)은 settings.js 가 칠한다
  root.append(host);
  box.append(root);
  const pane = new Pane(host);
  attachHead(pane);
  return pane;
}

// ---------- 칸 머리줄 — [프로젝트 ▾] + 꺼진 것 켜기 되묻기 ----------
function attachHead(pane) {
  const head = document.createElement("div");
  head.className = "cell-head";   // .pane-head 는 오른쪽 패널들(인박스·설정·파일) 머리줄 이름이다 — 겹치면 안 된다
  const pick = document.createElement("select");
  pick.className = "pane-pick";
  pick.title = "이 칸에 띄울 프로젝트";
  pick.setAttribute("aria-label", "이 칸에 띄울 프로젝트");
  const ask = document.createElement("span");
  ask.className = "pane-ask";
  head.append(pick, ask);
  pane.root.prepend(head);
  pane.pick = pick; pane.ask = ask; pane.pickSig = "";
  pick.onchange = () => pickChanged(pane);
  // 칸 어디를 눌러도(머리줄·터미널) 그 칸이 활성 — 키바·붙여넣기·위 머리줄·파일 패널이 따라온다.
  // pointerdown(캡처)이라 xterm 이 포커스를 가져가기 전에, 패널들의 click 동기화(fpSync·bpSync)보다 먼저 선다.
  pane.root.addEventListener("pointerdown", () => setActive(pane), true);
  pane.host.addEventListener("focusin", () => setActive(pane));
  // 더블클릭 = 그 칸 크게 보기 / 다시 더블클릭 = 원래 격자로. 여러 칸일 때만 —
  // 1칸에서는 지금처럼 tmux 의 단어 선택으로 간다. 두 번째 누름(detail 2)을 캡처 단계에서 끊어 xterm·tmux 에
  // 안 보낸다: 그대로 두면 tmux 가 단어를 골라 복사 모드에 들어가 다음 타이핑이 화면 안을 돌아다닌다.
  pane.root.addEventListener("mousedown", e => {
    if (!isGrid() || e.button !== 0 || e.detail !== 2 || e.target.closest("select, button")) return;
    e.preventDefault();
    e.stopPropagation();
    toggleMax(pane);
  }, true);
}

// 크게 보기 — 격자는 그대로 두고 다른 칸만 감춘다(소켓은 붙어 있다). 감춘 칸은 크기가 0이라 fit 이
// 아무 일도 안 해 PTY 크기도 그대로다 — 되돌리면 예전 크기 그대로 다시 보인다.
function toggleMax(pane) {
  const box = document.getElementById("panes");
  const on = !pane.root.classList.contains("maxed");
  for (const p of panes) p.root.classList.toggle("maxed", on && p === pane);
  box.classList.toggle("max", on);
  setActive(pane);
  pane.term?.focus();
}

function clearMax() {
  document.getElementById("panes").classList.remove("max");
  for (const p of panes) p.root.classList.remove("maxed");
}

function setActive(pane, force = false) {
  if (!pane || (pane === activePane && !force)) return;
  activePane = pane;
  for (const p of panes) p.root.classList.toggle("active", p === pane);
  paintHeader();
  renderPicks();
}

function pickChanged(pane) {
  setActive(pane);
  pane.ask.replaceChildren();
  const name = pane.pick.value;
  if (!name) {                                // '— 프로젝트 선택 —' = 이 칸 비우기(세션은 끄지 않는다)
    pane.close();
    paintHeader();
    renderPicks(true);
    gridSave();
    return;
  }
  const s = gridSession(name);
  if (!s) { renderPicks(true); return; }      // 그 사이 목록에서 사라졌다
  if (s.alive) { openIn(pane, s); return; }
  // 꺼진 것은 사이드바와 같은 잣대로 되묻는다 — 고르기만 해도 tmux 세션과 에이전트가 새로 선다.
  const done = () => pane.ask.replaceChildren();
  pane.ask.append(el("span", "꺼져 있습니다 —"),
    actBtn("클로드로 켜기", "agent-claude", () => { done(); openIn(pane, s, "claude"); }),
    actBtn("코덱스로 켜기", "agent-codex", () => { done(); openIn(pane, s, "codex"); }, "codex --yolo 로 시작"),
    actBtn("안티로 켜기", "agent-anti", () => { done(); openIn(pane, s, "anti"); }, "Antigravity CLI(agy) 로 시작"),
    actBtn("취소", "cancel", () => { done(); renderPicks(true); }));
}

// 칸마다 고르기 목록을 다시 채운다 — 7초 폴링(loadSessions)도 부른다. 다른 칸에 떠 있는 세션은
// 고를 수 없게 막는다(같은 세션 두 칸 = 같은 tmux 에 두 번 붙어 크기를 서로 뺏는다).
function renderPicks(force = false) {
  if (!isGrid()) return;                      // 1칸은 머리줄이 숨는다(CSS) — 그릴 것이 없다
  const usedBy = new Map(panes.filter(p => p.session).map(p => [p.session.name, p]));
  const list = lastSessions.filter(gridPickable);
  for (const pane of panes) {
    if (pane.ask.childElementCount) continue;                         // 켜기를 묻는 중 — 고른 값을 지우지 않는다
    if (!force && document.activeElement === pane.pick) continue;     // 펼쳐 둔 목록을 폴링이 닫지 않게
    const cur = pane.session?.name ?? "";
    const opts = [["", "— 프로젝트 선택 —", false]];
    if (cur && !list.some(s => s.name === cur))                       // 탭(base@N)·숨긴 세션도 지금 값은 보인다
      opts.push([cur, cur.replace(/@(\d)$/, " · 탭 $1"), false]);
    for (const s of list) {
      const other = usedBy.get(s.name);
      const busy = !!other && other !== pane;
      // ssh 로 다른 PC 에 붙은 세션은 [host] 를 앞에 — 칸 머리줄에서도 어느 기계인지 읽힌다
      opts.push([s.name, (s.host ? `[${s.host}] ` : "") + s.name + (s.alive ? "" : " · 꺼짐")
                 + (busy ? " · 다른 칸" : ""), busy]);
    }
    const sig = JSON.stringify([cur, opts]);
    if (sig === pane.pickSig && !force) continue;
    pane.pickSig = sig;
    pane.pick.replaceChildren(...opts.map(([value, label, disabled]) => {
      const o = el("option", label);
      o.value = value;
      o.disabled = disabled;
      return o;
    }));
    pane.pick.value = cur;
  }
}

// ---------- 하단 공통 HUD — 여러 칸일 때만 ----------
// 칸마다 반복되던 계정 사용량 줄(digi·ceo·cdx·anti)을 상태바 위에 한 세트만 띄운다. 내용은 브릿지가
// 같은 statusline.py 를 --accounts 로 돌린 것(ANSI 색 포함) — 클로드 안의 HUD 와 글자까지 같다.
// 1칸은 클로드 HUD 가 원래대로 다 보이므로 여기는 접는다(같은 줄이 두 번 보이지 않게).
const HUD_SGR = { 1: "b", 2: "d", 31: "red", 32: "green", 33: "yellow", 36: "cyan", 94: "blue", 95: "magenta" };
const HUD_STYLE = new Set(["b", "d"]);

function hudLine(text) {
  const line = document.createElement("div");
  line.className = "hud-line";
  let on = new Set();
  for (const part of text.split(/(\x1b\[[0-9;]*m)/)) {
    const code = part.match(/^\x1b\[([0-9;]*)m$/);
    if (code) {
      for (const c of (code[1] || "0").split(";").map(Number)) {
        if (c === 0) on = new Set();
        else if (HUD_SGR[c]) {
          if (!HUD_STYLE.has(HUD_SGR[c]))     // 색은 하나만 — 새 색이 오면 옛 색을 뗀다(굵게·흐리게는 유지)
            on = new Set([...on].filter(k => HUD_STYLE.has(k)));
          on.add(HUD_SGR[c]);
        }
      }
      continue;
    }
    if (!part) continue;
    const span = el("span", part);           // textContent — 어떤 글자가 와도 HTML 로 해석되지 않는다
    if (on.size) span.className = [...on].map(k => `h-${k}`).join(" ");
    line.append(span);
  }
  return line;
}

async function refreshHud() {
  const box = document.getElementById("hud");
  if (!isGrid()) { box.hidden = true; return; }
  try {
    const res = await fetch(`${API}/api/hud`);
    if (!res.ok) throw new Error(res.status);
    const { lines = [] } = await res.json();
    if (!isGrid()) return;                    // 받는 사이 1칸으로 돌아왔다
    box.replaceChildren(...lines.map(hudLine));
    box.hidden = !lines.length;
  } catch { box.hidden = true; }              // 못 받으면 접는다 — 칸 안 HUD 1줄은 그대로 남는다
}
setInterval(() => { if (!document.hidden) refreshHud(); }, 10000);   // 클로드 HUD 와 같은 10초

// ---------- 상단 [1][4][6][8] ----------
function renderGridBar() {
  for (const btn of document.querySelectorAll("#grid-bar button"))
    btn.classList.toggle("on", Number(btn.dataset.n) === panes.length);
}

// 누른 뒤 키보드는 활성 칸으로 돌려준다 — 버튼에 포커스가 남으면 다음 타이핑이 아무 데도 안 간다.
for (const btn of document.querySelectorAll("#grid-bar button"))
  btn.onclick = () => { setGrid(Number(btn.dataset.n), gridLoad().names); activePane.term?.focus(); };

// 첫 목록이 온 뒤 app.js 가 부른다 — 기억한(또는 기본 4) 칸 수로 연다. 1칸이면 지금까지처럼 빈 가운데.
function gridBoot() {
  renderGridBar();
  if (!isDesktop() || gridChoice === 1 || panes.some(p => p.session)) return;
  setGrid(gridChoice, gridLoad().names, false);
}

// 폰↔데스크톱 경계를 넘으면 — 폰 폭은 1칸으로 접고, 데스크톱으로 돌아오면 고른 칸 수를 되살린다.
matchMedia("(min-width: 900px)").addEventListener("change", () => {
  if (!isDesktop() && panes.length > 1) setGrid(1, [], false);
  else if (isDesktop() && gridChoice > 1 && panes.length === 1) setGrid(gridChoice, gridLoad().names, false);
});

attachHead(panes[0]);                         // 첫 칸(index.html 의 #terminal)에도 머리줄을 단다
panes[0].root.classList.add("active");
