"use strict";
// 설정 화면 — ① 새 세션(시작 에이전트·bypass·RC·실행 별칭) ② 화면(테마·터미널 글자) ③ 에이전트 연결.
// 렌더는 전부 textContent/createElement(이 repo 의 절대 계약 — 버전·계정 문자열이 그대로 들어온다).
// 로그인 자체는 각 CLI 의 OAuth 가 터미널에서 한다 — 이 화면은 비밀번호·키를 받지 않는다.
//
// 저장 위치가 둘로 갈린다:
//   서버(settings.json) = 세션을 '어떻게 시작하나' — 폰·맥 어디서 열어도 같아야 한다.
//   기기(localStorage)  = 화면을 '어떻게 보이나' — 폰과 맥은 다른 크기·테마를 원한다.

// ---------- 기기 표시 설정 ----------
const PREFS_KEY = "frog.prefs";
const PREF_DEFAULTS = { ui: "dark", termTheme: "onedark", fontSize: 14, font: "d2", fpFontSize: 13,
                        fpOpen: true,         // 파일 패널 기본 열림 — 📁·✕ 로 닫으면 false 로 남는다
                        fpTheme: "" };        // 파일 편집 색 — ""(화면과 같게)가 기본

function getPrefs() {
  try { return { ...PREF_DEFAULTS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; }
  catch { return { ...PREF_DEFAULTS }; }
}

function setPrefs(patch) {                      // 테마 세트처럼 두 키를 한 번에 — 저장·반영은 한 번만
  localStorage.setItem(PREFS_KEY, JSON.stringify({ ...getPrefs(), ...patch }));
  applyPrefs();
  renderDisplay();
}
const setPref = (key, value) => setPrefs({ [key]: value });

// "JetBrains Mono" 순정 이름만 적으면 Nerd Font 판("JetBrainsMono Nerd Font")만 깔린 기기에서
// 못 찾고 D2Coding 으로 떨어져 두 선택이 똑같아 보인다(실측 — 이 맥이 그랬다). 두 이름 다 적는다.
const TERM_FONTS = {
  d2: '"D2Coding", "JetBrains Mono", "JetBrainsMono Nerd Font", ui-monospace, Menlo, Consolas, monospace',
  jb: '"JetBrains Mono", "JetBrainsMono Nerd Font", "JetBrainsMono Nerd Font Mono", "D2Coding", ui-monospace, Menlo, Consolas, monospace',
  system: "ui-monospace, Menlo, Consolas, monospace",
};

// 터미널 색 — 기본은 One Dark Pro(VS Code 최다 설치 테마). "글자가 온통 흰색이라 눈이 아프다"
//의 답: 본문 #ABB2BF 로 낮추고 ANSI 16색을 제대로 채워 클로드 출력이 구별되게 한다.
const TERM_THEMES = {
  onedark: {
    background: "#23272E", foreground: "#ABB2BF", cursor: "#528BFF", cursorAccent: "#23272E",
    selectionBackground: "#404859",
    black: "#3F4451", red: "#E05561", green: "#8CC265", yellow: "#D18F52",
    blue: "#4AA5F0", magenta: "#C162DE", cyan: "#42B3C2", white: "#D7DAE0",
    brightBlack: "#4F5666", brightRed: "#FF616E", brightGreen: "#A5E075",
    brightYellow: "#F0A45D", brightBlue: "#4DC4FF", brightMagenta: "#DE73FF",
    brightCyan: "#4CD1E0", brightWhite: "#E6E6E6",
  },
  frog: {                                       // 앱과 같은 HOLOGRID 다크 — 개구리 그린 커서
    background: "#0B0D0C", foreground: "#C9D1CB", cursor: "#4ADE80", cursorAccent: "#0B0D0C",
    selectionBackground: "#2E3833",
    black: "#1E2320", red: "#FF6B6B", green: "#4ADE80", yellow: "#FFB800",
    blue: "#6CB6FF", magenta: "#FF7AB6", cyan: "#56D4DD", white: "#C9D1CB",
    brightBlack: "#4E5852", brightRed: "#FF8F8F", brightGreen: "#7FE8A5",
    brightYellow: "#FFD34D", brightBlue: "#93CBFF", brightMagenta: "#FF9FC8",
    brightCyan: "#83E3EA", brightWhite: "#E8ECE9",
  },
  // One Light 를 진하게 — 프로젝터에서 회색 글자가 날아갔다. 본문은 거의 검정,
  // 색 글자도 한 단계씩 어둡게. 클로드가 직접 찍는 연회색(트루컬러)은 아래 minimumContrastRatio 가 끌어내린다.
  light: {
    background: "#FFFFFF", foreground: "#111111", cursor: "#15803D", cursorAccent: "#FFFFFF",
    selectionBackground: "#C9DDF0",
    black: "#111111", red: "#C4302B", green: "#2E7D32", yellow: "#8A5A00",
    blue: "#005F99", magenta: "#8A1C88", cyan: "#00707F", white: "#FAFAFA",
    brightBlack: "#4A4D55", brightRed: "#C4302B", brightGreen: "#2E7D32",
    brightYellow: "#8A5A00", brightBlue: "#005F99", brightMagenta: "#8A1C88",
    brightCyan: "#00707F", brightWhite: "#FFFFFF",
  },
};

// 터미널을 새로 열 때(app.js)와 이미 열려 있을 때(applyPrefs) 둘 다 이 한 곳에서 읽는다.
function termOptions() {
  const p = getPrefs();
  return {
    fontSize: Math.max(10, Math.min(22, p.fontSize | 0 || 14)),
    fontFamily: TERM_FONTS[p.font] ?? TERM_FONTS.d2,
    theme: TERM_THEMES[p.termTheme] ?? TERM_THEMES.onedark,
    // 밝은 바탕에선 글자 대비를 최소 7:1 로 강제한다(WCAG AAA) — 테마 밖 트루컬러 회색(클로드 힌트·상태줄)까지
    // xterm 이 자동으로 어둡게 그린다. 어두운 테마는 원래 색 그대로(1).
    minimumContrastRatio: p.termTheme === "light" ? 7 : 1,
    // ⌥+드래그 = 브라우저 자체 선택. tmux 가 mouse on 이라 맨 드래그는 tmux 가 가져가는데,
    // 화면 밖(스크롤백)이나 여러 pane 을 가로질러 긁고 싶을 때의 탈출구다(맥 터미널 관례).
    macOptionClickForcesSelection: true,
  };
}

function applyPrefs() {
  const p = getPrefs();
  document.documentElement.classList.toggle("light", p.ui === "light");
  document.documentElement.classList.toggle("onedark", p.ui === "onedark");
  // 파일 패널 글자 크기 — 패널 머리의 −/+ 가 바꾼다(files.js). CSS 는 이 변수 하나만 읽는다.
  // 숫자를 −/+ 사이에 같이 보여준다 — 1px 변화는 눈에 안 띄어 "버튼이 안 된다"로 읽혔다(실측).
  const fpFs = Math.max(10, Math.min(22, p.fpFontSize | 0 || 13));
  document.documentElement.style.setProperty("--fp-fs", `${fpFs}px`);
  document.getElementById("fp-fs-val").textContent = fpFs;
  // 파일 편집 색 — 파일 패널 subtree 에만 팔레트를 덮는다.
  // 터미널 색을 따로 고르는 것과 같은 결: 코드를 읽는 창과 앱 껍데기의 밝기를 따로 잡고 싶을 때 쓴다.
  const panel = document.getElementById("file-panel");
  panel.classList.remove("pal-frog", "pal-onedark", "pal-light");
  if (p.fpTheme) panel.classList.add(`pal-${p.fpTheme}`);
  const opts = termOptions();
  // 모든 칸(멀티 칸)에 즉시 반영 — 칸마다 xterm 이 따로라 하나씩 옵션을 건다
  for (const host of document.querySelectorAll(".pane-term")) host.style.background = opts.theme.background;
  if (typeof panes !== "undefined" && panes.some(p => p.term)) {
    for (const p of panes) if (p.term) Object.assign(p.term.options, opts);
    sendResize();                               // 글자 크기가 바뀌면 칸수가 달라진다
  }
}

// ---------- 서버 설정 (세션 시작) ----------
const AGENT_LABEL = { claude: "클로드", codex: "코덱스", anti: "안티그래비티", shell: "셸만" };
const AGENT_DESC = {
  claude: "Claude Code를 바로 시작",
  codex: "Codex CLI를 바로 시작",
  anti: "Antigravity CLI(agy)를 바로 시작",
  shell: "빈 셸 — 직접 입력",
};

async function loadSettings() {
  const box = document.getElementById("set-agent");
  try {
    const res = await fetch(`${API}/api/settings`);
    if (!res.ok) throw new Error(res.status);
    const { settings, agents, home } = await res.json();
    if (home) FROG.HOME = home;                // app.js 의 `~/` 펼치기가 쓴다
    box.replaceChildren(...agents.map(a => choiceBtn(
      AGENT_LABEL[a] ?? a, AGENT_DESC[a] ?? "", a === settings.agent,
      () => saveSettings({ agent: a }))));
    renderFlags(settings);
    renderRoot(settings);
    renderCmds(settings);
  } catch {
    box.replaceChildren(el("p", "설정을 불러오지 못했습니다 — 맥에 연결됐는지 확인하세요"));
  }
  renderDisplay();
  loadAgentStatus();
}

// 프로젝트 루트 — 서버 설정 한 곳에 저장한다. 별칭과 같은 입력+명시적 저장 문법을 쓴다.
function renderRoot(settings) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = settings.projectsRoot ?? "";
  input.placeholder = "/Users/이름/projects";
  input.autocapitalize = "off"; input.autocomplete = "off"; input.spellcheck = false;
  const save = () => saveSettings({ projectsRoot: input.value });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
  });
  const button = el("button", "저장");
  button.type = "button";
  button.className = "set-cmd-save";
  button.onclick = save;
  const row = document.createElement("div");
  row.className = "set-root-row";
  row.append(input, button);
  document.getElementById("set-root").replaceChildren(row);
}

function choiceBtn(label, desc, on, run) {
  const btn = document.createElement("button");
  btn.className = "set-choice" + (on ? " on" : "");
  btn.append(el("b", label));
  if (desc) btn.append(el("small", desc));
  btn.onclick = run;
  return btn;
}

// 클로드 시작 스위치 — Orca 와 같은 기본값(전권한·이어서 시작).
// RC 는 claude.ai·폰 앱에서 이 세션을 잇는 기능.
function renderFlags(settings) {
  const bypass = choiceBtn("바이패스 퍼미션", "권한 확인 없이 실행 (클로드·코덱스 --yolo)", settings.bypass,
                           () => saveSettings({ bypass: !settings.bypass }));
  bypass.classList.add("risk");   // 위험 옵션이 켜졌을 때 '권장'의 그린이 아니라 주의의 amber 로
  document.getElementById("set-flags").replaceChildren(
    bypass,
    // 이어서 시작 — exit 로 나갔다 다시 떠도 그 세션이 하던 대화를 그대로 잇는다.
    // 세션마다 고정 ID 라 같은 폴더의 다른 탭·Zed 대화를 가로채지 않는다(bridge/settings.py).
    choiceBtn("이어서 시작", "나갔다 다시 떠도 하던 대화 그대로", settings.cont,
              () => saveSettings({ cont: !settings.cont })),
    choiceBtn("리모트 컨트롤(RC)", "claude.ai·폰 앱에서 이어받기", settings.rc,
              () => saveSettings({ rc: !settings.rc })),
  );
}

// 실행 별칭 — 이름만 받고 대상(claude·codex)은 서버가 고정한다. 저장 버튼은 두 칸을 함께 보내
// 기존 frogterminal 블록을 한 번에 갈아끼운다. Enter도 같은 저장 동작(사용자의 기존 습관)을 유지한다.
function renderCmds(settings) {
  const inputs = {};
  const row = (agent, label) => {
    const wrap = document.createElement("label");
    wrap.className = "set-cmd";
    const input = document.createElement("input");
    input.type = "text";
    input.value = settings.commands?.[agent] ?? "";
    input.placeholder = `비우면 ${agent} 기본 명령`;
    input.autocapitalize = "off"; input.autocomplete = "off"; input.spellcheck = false;
    inputs[agent] = input;
    wrap.append(el("span", label), input);
    return wrap;
  };
  const save = () => saveSettings({ commands: {
    claude: inputs.claude.value, codex: inputs.codex.value,
  }});
  const button = el("button", "저장");
  button.type = "button";
  button.className = "set-cmd-save";
  button.onclick = save;
  const rows = [row("claude", "클로드 실행 명령"), row("codex", "코덱스 실행 명령")];
  Object.values(inputs).forEach(input => input.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
  }));
  const hint = el("p", "영문 별칭만 입력하세요. 비우면 별칭을 지웁니다.");
  hint.className = "set-hint";
  document.getElementById("set-cmds").replaceChildren(...rows, hint, button);
}

async function saveSettings(patch) {
  settingsNote("");
  let saved = false;
  try {
    const res = await fetch(`${API}/api/settings`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      let body = null;
      try { body = await res.json(); } catch { /* 본문 없는 오류 — 아래 일반 문구로 */ }
      settingsNote(body?.error === "invalid alias"
        ? `별칭을 저장하지 않았습니다 — ${body.reason || "사용할 수 없는 이름입니다"}`
        : body?.error === "invalid projects root"
        ? `프로젝트 폴더를 저장하지 않았습니다 — ${body.reason || "경로를 확인하세요"}`
        : `저장하지 못했습니다 (서버 응답 ${res.status})`);
    } else if (patch.projectsRoot !== undefined) {
      saved = true;
      settingsNote("프로젝트 폴더를 저장했습니다. 사이드바를 새 작업공간으로 바꿨습니다.", true);
    } else if (patch.commands) {
      saved = true;
      const names = [patch.commands.claude, patch.commands.codex].filter(Boolean);
      settingsNote(names.length
        ? `별칭을 저장했습니다. 이제 새로 여는 터미널부터 ${names.join(" · ")}로 시작합니다.`
        : "별칭을 지웠습니다. 이제 새로 여는 터미널부터 기본 명령으로 시작합니다.", true);
    } else saved = true;
  } catch {
    settingsNote("맥에 연결되지 않아 저장하지 못했습니다 — Tailscale 연결을 확인하세요");
  }
  if (saved && patch.projectsRoot !== undefined) {
    await loadSessions();                       // 재시작 없이 새 루트를 즉시 사이드바에 반영
    refreshSummary();
  }
  loadSettings();                              // 성공·실패 모두 서버 값으로 다시 그린다(추측 금지)
}

// ---------- 화면 설정 (이 기기) ----------
// 테마는 3세트 — 패널과 터미널이 같은 이름·같은 계열로 묶인다.
// 테마를 고르면 짝이 되는 터미널 색까지 같이 바뀌고, 아래 '터미널 색' 줄에서 따로 바꿀 수도 있다.
const UI_LABEL = { dark: "개구리 다크", onedark: "One Dark", light: "라이트" };
const UI_TERM_PAIR = { dark: "frog", onedark: "onedark", light: "light" };
const THEME_LABEL = { onedark: "One Dark", frog: "개구리 다크", light: "라이트" };
const FONT_LABEL = { d2: "D2Coding", jb: "JetBrains Mono", system: "시스템 모노" };
// 파일 편집(오른쪽 패널) 색 — ""는 앱 테마를 그대로 따른다
const FP_THEME_LABEL = { "": "화면과 같게", frog: "개구리 다크", onedark: "One Dark", light: "라이트" };

function renderDisplay() {
  const p = getPrefs();
  const group = (pairs, key) => pairs.map(([value, label]) =>
    choiceBtn(label, "", p[key] === value, () => setPref(key, value)));
  document.getElementById("set-ui").replaceChildren(
    ...Object.entries(UI_LABEL).map(([value, label]) =>
      choiceBtn(label, "", p.ui === value,
                () => setPrefs({ ui: value, termTheme: UI_TERM_PAIR[value] }))));
  document.getElementById("set-term-theme").replaceChildren(
    ...group(Object.entries(THEME_LABEL), "termTheme"));
  document.getElementById("set-font").replaceChildren(
    ...group(Object.entries(FONT_LABEL), "font"));
  document.getElementById("set-fp-theme").replaceChildren(
    ...group(Object.entries(FP_THEME_LABEL), "fpTheme"));
  // 글자 크기 — 스텝퍼(− 14px +). 열려 있는 터미널에 즉시 반영된다.
  const minus = el("button", "−");
  const plus = el("button", "+");
  minus.className = plus.className = "set-step";
  minus.onclick = () => setPref("fontSize", Math.max(10, p.fontSize - 1));
  plus.onclick = () => setPref("fontSize", Math.min(22, p.fontSize + 1));
  const size = el("b", `${p.fontSize}px`);
  document.getElementById("set-font-size").replaceChildren(minus, size, plus);
}

// ---------- 에이전트 연결 상태 ----------
const agentUpdateResults = {};
const agentUpdateRunning = new Set();

function claudeAutoUpdateText(status) {
  if (!status || !["enabled", "disabled"].includes(status.autoUpdates)) return "";
  const parts = [status.autoUpdates === "enabled" ? "자동 업데이트 켜짐" : "자동 업데이트 꺼짐"];
  const last = status.lastAttempt;
  if (last && ["success", "failed", "failure"].includes(last.status)) {
    const date = /^\d{4}-(\d{2})-(\d{2})$/.exec(last.date ?? "");
    const when = date ? `${Number(date[1])}/${Number(date[2])} ` : "";
    parts.push(`최근 ${when}${last.status === "success" ? "성공" : "실패"}`);
  }
  return parts.join(" · ");
}

async function loadAgentStatus() {
  const box = document.getElementById("set-status");
  let agents;
  try {
    const res = await fetch(`${API}/api/agents`);
    if (!res.ok) throw new Error(res.status);
    ({ agents } = await res.json());
  } catch {
    box.replaceChildren(el("p", "연결 상태를 불러오지 못했습니다"));
    return;
  }
  const claude = agents?.claude ?? {};
  const codex = agents?.codex ?? {};
  // 조직명이 이메일의 반복이면 생략 — "ceo@… · ceo@…'s Organization" 같은 중복 표기 방지
  const org = claude.org && (!claude.email || !claude.org.startsWith(claude.email)) ? claude.org : "";
  box.replaceChildren(
    statusRow("claude", "Claude Code", claude.installed,
              claude.installed ? [claude.version, claude.email, org].filter(Boolean).join(" · ")
                                : "설치되지 않음",
              claude.installed && !claude.email ? "로그인 필요 — 터미널에서 claude" : "",
              claudeAutoUpdateText(claude.updateStatus), claude),
    statusRow("codex", "Codex CLI", codex.installed && codex.loggedIn,
              codex.installed
                ? [codex.version, codex.loggedIn ? "로그인됨" : ""].filter(Boolean).join(" · ")
                : "설치되지 않음",
              codex.installed && !codex.loggedIn ? "로그인 필요 — 터미널에서 codex login" : "",
              codex.installed ? "새 버전은 알려주지만 설치는 눌러야 합니다" : "", codex),
  );
}

function statusRow(agent, name, ok, meta, warn, detail, info) {
  const row = document.createElement("div");
  row.className = "set-row";
  const dot = el("em", "●");
  dot.className = ok ? "set-ok" : "set-off";
  const body = document.createElement("div");
  body.className = "set-agent-body";
  const head = document.createElement("div");
  head.className = "set-agent-head";
  head.append(el("b", name));
  const metaText = el("span", meta);
  metaText.className = "set-agent-meta";
  head.append(metaText);
  if (info.latestVersion) {
    const badge = el("span", info.updateAvailable ? `새 버전 ${info.latestVersion}` : "최신");
    badge.className = "set-version-badge" + (info.updateAvailable ? " new" : "");
    head.append(badge);
  }
  body.append(head);
  if (warn) {
    const w = el("span", warn);
    w.className = "set-warn";
    body.append(w);
  }
  if (detail) {
    const d = el("span", detail);
    d.className = "set-agent-detail";
    body.append(d);
  }
  const result = el("p", agentUpdateResults[agent] ?? "");
  result.id = `set-update-result-${agent}`;
  result.className = "set-update-result";
  result.hidden = !result.textContent;
  body.append(result);
  row.append(dot, body);
  if (info.installed) {
    const update = el("button", agentUpdateRunning.has(agent) ? "업데이트 중…" : "업데이트");
    update.type = "button";
    update.className = "set-update-btn";
    update.disabled = agentUpdateRunning.has(agent);
    update.onclick = () => runAgentUpdate(agent, update);
    row.append(update);
  }
  return row;
}

async function runAgentUpdate(agent, button) {
  // agent는 위 두 고정 행의 클로저에서만 온다. 별칭 입력이나 다른 사용자 문자열을 보내지 않는다.
  agentUpdateRunning.add(agent);
  button.disabled = true;
  button.textContent = "업데이트 중…";
  agentUpdateResults[agent] = "업데이트 명령을 실행하고 있습니다…";
  const showing = document.getElementById(`set-update-result-${agent}`);
  if (showing) { showing.textContent = agentUpdateResults[agent]; showing.hidden = false; }
  try {
    const res = await fetch(`${API}/api/agents/${agent}/update`, { method: "POST" });
    let data = null;
    try { data = await res.json(); } catch { /* 본문 없는 오류는 아래 일반 문구 */ }
    if (!res.ok) throw new Error(data?.error ?? res.status);
    const outcome = data.updated ? "새 버전을 설치했습니다"
      : data.success ? "이미 최신입니다" : "업데이트하지 못했습니다";
    agentUpdateResults[agent] = data.output ? `${outcome} — ${data.output}` : outcome;
    if (data.updated) {
      const count = Number.isInteger(data.activeSessions)
        ? ` 지금 켜져 있는 세션 ${data.activeSessions}개.` : "";
      settingsNote("새 버전이 설치됐습니다 — 지금 열려 있는 터미널은 끄고 다시 켜야 " +
                   `새 버전으로 돕니다.${count}`);
    } else {
      settingsNote(data.success ? "이미 최신 버전입니다." : "업데이트하지 못했습니다 — 실행 결과를 확인하세요",
                   data.success);
    }
  } catch {
    agentUpdateResults[agent] = "업데이트 요청에 실패했습니다 — 맥 연결을 확인하세요";
    settingsNote(agentUpdateResults[agent]);
  } finally {
    agentUpdateRunning.delete(agent);
    await loadAgentStatus();                   // 실제 전후 버전과 최신 배지를 서버 값으로 다시 그린다
  }
}

// alert() 금지 — 안내는 화면 안 한 줄로(#settings-body 바깥이라 다시 그려도 남는다).
function settingsNote(text, ok = false) {
  const note = document.getElementById("set-note");
  note.textContent = text;
  note.classList.toggle("ok", ok);
  note.hidden = !text;
}

// 설정은 오른쪽 팝업 패널 — 뒤(터미널)가 보여야 테마·글자 변경이 누르는 즉시 확인된다.
document.getElementById("btn-settings").onclick = () => {
  const panel = document.getElementById("settings-view");
  if (panel.hidden) {
    closeInbox(false);                         // 오른쪽 패널은 한 번에 하나
    panel.hidden = false;
    loadSettings();
    loadCacheUsage();
  } else {
    panel.hidden = true;                       // ⚙ 다시 누르면 닫힌다(토글)
  }
};
document.getElementById("settings-back").onclick = () => {
  document.getElementById("settings-view").hidden = true;
};
// Esc = 열린 오른쪽 패널 닫기. 단 터미널에 포커스가 있으면 Esc 는 tmux·클로드의 키다 — 안 가로챈다.
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (document.getElementById("term-view").contains(document.activeElement)) return;
  const settings = document.getElementById("settings-view");
  const inbox = document.getElementById("inbox-view");
  if (!settings.hidden) settings.hidden = true;
  else if (!inbox.hidden) closeInbox();
});
applyPrefs();                                  // 페이지 로드 즉시 — 테마·터미널 배경을 맞춘다


// ---------- 붙여넣기 캐시 — 얼마나 쌓였나 · 비우기 ----------
// 원본을 찾으면 사본을 안 만드는 쪽으로 고쳤지만(app.js), 폰에서 올린 사진처럼 맥에 원본이
// 없는 것은 여전히 사본으로 남는다. 그 자리를 사람이 직접 비울 수 있게 한다.
const cacheSizeText = b =>
  b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)}MB` : `${Math.ceil(b / 1024)}KB`;

async function loadCacheUsage() {
  const box = document.getElementById("set-cache-size");
  try {
    const res = await fetch(`${API}/api/uploads`);
    if (!res.ok) throw new Error(res.status);
    const { bytes, files } = await res.json();
    box.textContent = files ? `사본 ${files}개 · ${cacheSizeText(bytes)}` : "비어 있음";
    document.getElementById("set-cache-clear").disabled = !files;
  } catch {
    box.textContent = "확인 못 함 — 맥 연결을 확인하세요";
  }
}

// 되돌릴 수 없으니 한 번 되묻는다(사이드바 끄기와 같은 잣대 — 모달 대신 그 자리에서).
document.getElementById("set-cache-clear").onclick = function () {
  if (this.dataset.armed !== "1") {
    this.dataset.armed = "1";
    this.textContent = "정말 비우기(되돌릴 수 없음)";
    setTimeout(() => {                         // 5초 안에 다시 안 누르면 원래대로
      if (this.dataset.armed === "1") { this.dataset.armed = ""; this.textContent = "비우기"; }
    }, 5000);
    return;
  }
  this.dataset.armed = "";
  this.textContent = "비우는 중…";
  fetch(`${API}/api/uploads/clear`, { method: "POST" })
    .then(res => res.ok ? res.json() : Promise.reject(res.status))
    .then(({ bytes, files }) => {
      settingsNote(`캐시를 비웠습니다 — ${files}개 · ${cacheSizeText(bytes)} 확보`);
      loadCacheUsage();
    })
    .catch(() => settingsNote("캐시를 비우지 못했습니다 — 맥 연결을 확인하세요"))
    .finally(() => { this.textContent = "비우기"; });
};
