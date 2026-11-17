"use strict";
// 프로젝트 파일 패널 — 폴더를 VS Code 처럼 제자리에서 접었다 폈다 하고, 글자 파일은 그 자리에서 고쳐 저장한다.
//
// 이 파일의 계약은 셋이다.
// ① 화면에 넣는 글자는 전부 textContent — 파일 내용·파일명이 그대로 들어오는 자리라
//    innerHTML 을 쓰면 남이 써 둔 파일 한 줄이 스크립트로 실행된다.
// ② 파일 버전 토큰(mtimeNs)은 '문자열'로만 다룬다. st_mtime_ns 는 19자리(약 1.75e18)라
//    Number 로 바꾸는 순간 하위 자릿수가 뭉개지고(…456789 → …456800), 그 값을 되돌려 보내면
//    서버의 버전 비교가 영영 어긋나 저장이 매번 409 가 된다. 받은 문자열을 그대로 들고 있다가
//    그대로 돌려보낸다 — Number()·parseInt·산술 금지.
// ③ 저장하지 않은 수정은 어떤 경우에도 말없이 사라지지 않는다(아래 fpGo·fpSync·409 처리).

const fp = {
  project: null,   // 이 패널이 보고 있는 프로젝트 — currentSession 과 어긋나면 fpSync 가 잡는다
  root: null,      // 트리 뿌리(프로젝트 루트) 노드 — 프로젝트가 바뀌면 받아 둔 자식까지 통째로 버린다
  file: null,      // 열어 둔 파일 (null 이면 트리만 보는 중)
  mtimeNs: null,   // 열 때 받은 버전 토큰 — 문자열 그대로 저장에 되돌려 보낸다
  editor: null,    // 편집 중인 textarea (null 이면 보기 모드)
  loaded: "",      // 편집을 시작한 시점의 내용 — 이것과 다르면 '저장 안 한 수정'
  imageUrl: null,  // 이미지 Blob URL — 파일·프로젝트를 닫을 때 revoke해 메모리에 남기지 않는다
};

// 트리 한 칸. children 은 '아직 안 받음(null)' 과 '빈 폴더([])' 를 구분한다 — 이 구분이 곧 캐시다.
// 한 번 받은 자식은 접었다 펴도 다시 받지 않는다(못 읽은 줄만 다시 펼 때 한 번 더 받아 본다).
const fpNode = (name, path, dir) =>
  ({ name, path, dir, children: null, open: false, busy: false, err: false });

// 확장자 → 아이콘 클래스 조각. 영숫자 1~8자만 통과(파일명 유래 문자열이 클래스가 되는 자리).
function extOf(name) {
  const i = name.lastIndexOf(".");
  if (i <= 0) return "none";                  // 확장자 없음·숨김파일(.env 등)
  const ext = name.slice(i + 1).toLowerCase();
  return /^[a-z0-9]{1,8}$/.test(ext) ? ext : "none";
}

const FILE_ERRORS = {                         // 서버 응답 코드 → 사용자가 읽을 말
  404: "없거나 볼 수 없는 파일입니다",
  413: "너무 커서 열 수 없습니다 (2MB 넘음)",
  415: "글자 파일이 아니라 열 수 없습니다",
  405: "다른 PC 의 파일은 여기서 저장할 수 없습니다 — 터미널 안에서 고치세요",   // ssh 세션의 원격 폴더 = 읽기 전용
  502: "원격 PC 에 연결하지 못했습니다 — 켜져 있고 Tailscale 에 붙어 있는지 보세요",
};
// SVG는 스크립트를 품을 수 있어 서버가 막고, HEIC는 브라우저가 못 그린다. 둘도 이미지 전용
// 라우트로 보내야 텍스트 소스 대신 사람이 읽을 거절 사유를 보여 줄 수 있다.
const FP_IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "avif", "bmp",
                               "svg", "heic", "heif"]);

// 세대(generation) — "지금 화면에 그릴 자격이 있는 요청" 번호.
// 파일 요청은 왕복이라 답이 늦게 온다. 그 사이 사이드바·인박스로 세션을 갈아타면(fpSync) 늦게
// 도착한 옛 응답이 마지막에 이겨, 'mall' 이라 써 놓은 패널에 wiki 파일 내용이 그려진다 —
// 그대로 고쳐 저장하면 wiki 내용이 mall 파일로 들어간다(저장은 409 로 한 번 막히지만, 그때 뜨는
// 안내는 "다른 곳에서 먼저 바뀌었습니다"라는 거짓 설명이라 [덮어쓰기] 한 번이면 그대로 들어간다).
// 그래서 요청을 시작할 때 세대와 프로젝트를 붙잡아 두고, 모든 await 뒤에 '아직 내 세대인가'를
// 확인해 아니면 화면에 손대지 않고 물러난다. fetch 자체를 취소하지 않는 것은 의도다 — 저장(PUT)은
// 이미 나갔고, 여기서 막아야 하는 것은 '늦게 온 답이 화면을 덮는 것'뿐이다.
//
// 트리는 폴더마다 따로 요청하므로 번호를 둘로 나눈다.
//   fpGen(프로젝트 세대) — 프로젝트가 바뀌거나 패널을 열고 닫을 때만 오른다. 모든 요청이 확인한다.
//   fpFileSeq(파일 열기 순번) — 파일을 열 때마다 오른다. 파일 응답만 확인해 '마지막 클릭'이 이긴다.
// 폴더 펼치기는 둘 중 어느 것도 올리지 않는다: 폴더 요청이 서로를 무효로 만들면 A 를 펴고 B 를 편
// 순간 A 가 영영 안 나온다. 폴더끼리는 서로 다른 줄만 채우므로 겹칠 일이 없고, 여기서 막아야 하는
// 위험은 '늦게 온 답이 남의 프로젝트 트리를 채우는 것' 하나뿐이라 fpGen 확인으로 충분하다.
let fpGen = 0, fpFileSeq = 0;
const fpNewGen = () => ++fpGen;

const fpPanel = () => document.getElementById("file-panel");
const fpBody = () => document.getElementById("fp-body");
const fpDirty = () => !!fp.editor?.isConnected && fp.editor.value !== fp.loaded;
// project 를 인자로 받는다 — fp.project 를 여기서 읽으면 await 사이에 바뀐 값이 섞여 들어간다
const fpQuery = (project, path) => `project=${encodeURIComponent(project)}&path=${encodeURIComponent(path)}`;

// alert()·confirm() 금지(포커스·자동화를 망가뜨린다) — 안내는 패널 안 한 줄로.
// #fp-body 바깥에 두어 트리·본문을 다시 그려도 지워지지 않는다.
function fpNote(text, action) {
  const note = document.getElementById("fp-note");
  note.replaceChildren(text || "");           // 문자열은 텍스트 노드로 들어간다(HTML 해석 없음)
  if (text && action) {
    const btn = el("button", action.label);
    btn.id = "fp-note-act";
    btn.onclick = action.onClick;
    note.append(btn);
  }
  note.hidden = !text;
}

// 편집하다 다른 파일·크럼·✕ 를 누르면 고친 내용이 말없이 사라진다.
// 첫 누름은 막고 경고줄로 되묻는다 — 모달 대신 화면 안 한 줄 + [수정 버리고 이동].
// (폴더를 접었다 펴는 것은 아래 칸을 건드리지 않으므로 여기를 거치지 않는다 — fpRow 참조.)
function fpGo(run) {
  if (!fpDirty()) { fpNote(""); run(); return; }
  fpNote("✋ 저장하지 않은 수정이 있습니다 — 💾 저장을 누르거나",
         { label: "수정 버리고 이동", onClick: () => { fp.editor = null; fpNote(""); run(); } });
}

// ---------- 열기·닫기 ----------
// 📁 는 #term-header 안에 있어 터미널 화면에서만 보인다(인박스를 보는 동안엔 눌 수 없다).
// 손으로 닫는 길(📁 토글·✕)만 fpOpen 선택을 기기에 남긴다 — fpSync 의 자동 접힘(목록 복귀)은
// 선택이 아니므로 남기지 않는다. 더러운 편집이 있어 fpGo 가 막아선 동안에는 아직 안 닫힌 것이니
// 기록도 실제로 닫히는 순간으로 미룬다.
const fpUserClose = () => { setPref("fpOpen", false); fpClose(); };
document.getElementById("files-toggle").onclick = () => {
  if (!fpPanel().hidden) { fpGo(fpUserClose); return; }
  if (!currentSession) return;                // 열 프로젝트가 없으면 아무 일도 하지 않는다
  setPref("fpOpen", true);
  fpShow();
};
document.getElementById("fp-close").onclick = () => fpGo(fpUserClose);

function fpShow() {
  fpPanel().hidden = false;
  fp.project = baseOf(currentSession.name);   // 병렬 탭(base@N)에서도 파일은 그 프로젝트 것
  fpReset();
}

// 터미널의 파일 경로 링크(app.js — Ctrl/⌘+클릭)가 부른다: 패널을 열고 그 파일을 보여준다.
// 저장 안 한 수정이 있으면 fpGo 가 되묻는다 — 파일 패널의 "말없이 안 버린다" 계약 그대로.
function fpOpenFromTerminal(path) {
  if (!currentSession) return;
  fpGo(() => {
    if (fpPanel().hidden || fp.project !== baseOf(currentSession.name)) fpShow();
    fpOpen(path);
  });
}

// 터미널을 열 때마다 app.js(openTerminal)가 부른다 — 파일 패널은 '열림'이 기본값.
// 폰은 패널이 화면을 통째로 덮어 터미널이 안 보이므로 자동으로는 열지 않는다(📁 로만).
function fpAutoOpen() {
  if (!fpPanel().hidden || !currentSession) return;   // 이미 열려 있으면 fpSync 가 프로젝트를 맞춘다
  if (isGrid()) return;                       // 여러 칸 — 칸이 좁아 저절로는 안 연다(📁 로 직접 열 수는 있다)
  // 1100px 미만(폴드 펼침 ~904px 등)은 3열을 깔면 터미널이 40칸대로 짜부라진다 — 📁 손으로만.
  if (!matchMedia("(min-width: 1100px)").matches || getPrefs().fpOpen === false) return;
  fpShow();
}

// 패널 머리 도구 — 글자 크기(이 기기 설정에 저장, settings.js 의 --fp-fs 로 반영)와 새 폴더(루트에).
document.getElementById("fp-fs-minus").onclick = () => fpBumpFont(-1);
document.getElementById("fp-fs-plus").onclick = () => fpBumpFont(+1);
document.getElementById("fp-newdir").onclick = () => fpNewDirPrompt(fp.root);
function fpBumpFont(step) {
  const cur = getPrefs().fpFontSize | 0 || 13;
  setPref("fpFontSize", Math.max(10, Math.min(22, cur + step)));
}

function fpClose() {
  fpNewGen();                                 // 닫는 중에 도착할 응답이 접힌 패널을 되살리지 않게
  fpClearImageUrl();
  fpPanel().hidden = true;
  fpPanel().classList.remove("wide");
  fp.root = null; fp.file = null; fp.editor = null; fp.mkdir = null;
  fpNote("");
}

// 프로젝트 하나를 통째로 새로 그린다 — 펴 둔 자리도, 받아 둔 자식(캐시)도 여기서 버린다.
function fpReset() {
  fpNewGen();                                 // 진행 중이던 옛 프로젝트 요청의 답을 전부 무효로
  fpClearImageUrl();
  fp.root = fpNode(fp.project, "", true);
  fp.root.open = true;
  fp.file = null; fp.editor = null; fp.mkdir = null;
  fpPanel().classList.remove("wide");
  fpNote("");
  fpCrumb();
  // 크럼은 곧바로 새 프로젝트 이름으로 바뀐다 — 그 아래에 옛 프로젝트 트리를 남겨 두면
  // 루트가 오기까지의 몇백 ms 동안 화면이 또 두 프로젝트를 섞어 보여준다.
  fpBody().replaceChildren(el("p", "불러오는 중…"));
  fpFetchDir(fp.root);
}

// ---------- 지금 열어 둔 파일의 자리 ----------
// 어디에 무엇이 있나는 트리가 보여주므로, 크럼은 '열어 둔 파일이 어느 프로젝트의 어디인가'만 말한다.
function fpCrumb() {
  const nav = document.getElementById("fp-crumb");
  nav.replaceChildren();
  const parts = fp.file ? fp.file.split("/") : [];
  const dirs = parts.slice(0, -1);
  const crumb = (label, run) => {
    const btn = el("button", label);
    btn.onclick = () => fpGo(run);
    nav.append(btn, el("span", "/"));
  };
  crumb(fp.project ?? "…", fpCloseFile);      // 맨 앞은 프로젝트 이름 — 어느 프로젝트인지 항상 보이게
  dirs.forEach((name, i) => {
    const path = dirs.slice(0, i + 1).join("/");
    crumb(name, () => { fpRevealDir(path); fpCloseFile(); });
  });
  if (fp.file) nav.append(el("b", parts[parts.length - 1]));
}

// ---------- 트리 ----------
// #fp-body 는 두 칸이다 — 위는 트리(#fp-tree), 아래는 열어 둔 파일(#fp-view).
// 나눠 두는 까닭은 계약 ③ 이다: 폴더를 펴느라 트리를 다시 그려도 아래 칸의 textarea 는 살아 있어야
// 한다. 한 칸에 같이 두면 폴더 하나 펼 때마다 저장 안 한 수정이 말없이 사라진다.
function fpShell() {
  let tree = document.getElementById("fp-tree");
  if (!tree) {
    tree = document.createElement("ul");
    tree.id = "fp-tree";
    const view = document.createElement("div");
    view.id = "fp-view";
    fpBody().replaceChildren(tree, view);
  }
  return tree;
}
const fpView = () => (fpShell(), document.getElementById("fp-view"));

// 보이는 줄만 한 줄씩 늘어놓는다(중첩 ul 대신 평평한 목록 + --depth).
// 중첩하면 부모 줄 안에 자식 글자까지 들어가, '폴더를 눌렀다'와 '그 안의 파일을 눌렀다'가 섞인다.
function fpRenderTree() {
  const rows = [];
  if (fp.root) fpBranch(fp.root, 0, rows);
  fpShell().replaceChildren(...rows);
}

function fpBranch(node, depth, out) {
  if (fp.mkdir?.parent === node) out.push(fpMkdirRow(node, depth));   // 새 폴더 입력줄 — 그 폴더 맨 위
  if (node.err) { out.push(fpMsg("tree-err", "이 폴더를 열지 못했습니다 — 다시 눌러 보세요", depth)); return; }
  if (node.children === null) { out.push(fpMsg("tree-empty", "불러오는 중…", depth)); return; }
  if (!node.children.length) { out.push(fpMsg("tree-empty", "(빈 폴더)", depth)); return; }
  for (const child of node.children) {
    out.push(fpRow(child, depth));
    if (child.dir && child.open) fpBranch(child, depth + 1, out);   // 자식은 부모 '바로 아래' 줄로
  }
}

function fpMsg(cls, text, depth) {
  const li = el("li", text);
  li.className = cls;
  li.style.setProperty("--depth", depth);
  return li;
}

function fpRow(node, depth) {
  const li = document.createElement("li");
  li.className = `tree-row ${node.dir ? "dir" : "file"}`;
  li.style.setProperty("--depth", depth);     // 들여쓰기는 CSS 가 이 값으로만 계산한다(0 = 루트 바로 아래)
  if (!node.dir && node.path === fp.file) li.classList.add("sel");
  const chev = el("span", node.dir ? (node.open ? "▾" : "▸") : "");  // 파일 줄은 빈 칸 — 이름을 세로로 맞춘다
  chev.className = "tree-chev";
  // 아이콘은 CSS 가 그린다(확장자별 색 — VS Code 아이콘 테마 차용). 클래스는 화이트리스트를
  // 통과한 확장자만 — 파일명에서 온 문자열이 클래스로 들어가는 자리라 모양 검사가 곧 방어선이다.
  const icon = el("span", "");
  icon.className = `tree-icon ${node.dir ? (node.open ? "i-dir open" : "i-dir") : "i-" + extOf(node.name)}`;
  const name = el("span", node.name);         // 파일명도 textContent 로만
  name.className = "tree-name";
  li.append(chev, icon, name);
  // 폴더는 펴고 접을 뿐 아무것도 버리지 않는다 → 편집 중에도 fpGo 로 되묻지 않는다.
  // 파일은 아래 칸을 갈아치우므로 반드시 fpGo 를 거친다.
  li.onclick = () => (node.dir ? fpToggle(node) : fpGo(() => fpOpen(node.path)));
  // 우클릭 = 프로그램으로 열기(맥에서) — PDF·HTML·큰 이미지·SVG·HEIC에도 남겨 둔 길
  li.addEventListener("contextmenu", e => fpShowMenu(e, node));
  // 트리 → 터미널 드래그 = 그 경로를 입력(VS Code·Zed 와 같은 동작).
  // 절대경로는 세션의 path 로 만든다(사용자별 루트를 코드에 박지 않는다). 받는 쪽은 app.js drop.
  li.draggable = true;
  li.addEventListener("dragstart", e => {
    const base = currentSession?.path;
    if (!base) { e.preventDefault(); return; }         // 폴더 없는 tmux 세션 — 만들 경로가 없다
    const abs = node.path ? `${base}/${node.path}` : base;
    e.dataTransfer.setData("application/x-frog-path", abs);
    e.dataTransfer.setData("text/plain", abs);         // 밖(에디터 등)에 떨어뜨려도 경로 글자가 된다
    e.dataTransfer.effectAllowed = "copy";
  });
  return li;
}

function fpToggle(node) {
  // 폴더로 나가면 열어 둔 파일은 닫는다(안 그러면 계속 열려 있다) — 단 저장 안 한 수정은
  // 말없이 버리지 않는다: 편집 중이면 그대로 두고, ✕ 닫기·크럼이 fpGo 를 거쳐 닫는 길이 된다.
  if (fp.file && !fpDirty()) fpCloseFile();
  node.open = !node.open;
  if (node.open && !node.busy && (node.children === null || node.err)) fpFetchDir(node);
  fpRenderTree();
}

// 폴더 하나의 자식을 받아 그 줄 아래에 끼운다.
// 못 읽는 폴더가 있어도 트리 전체를 지우지 않는다 — 그 줄만 .tree-err 로 알리고 나머지는 그대로 둔다.
async function fpFetchDir(node) {
  const gen = fpGen, project = fp.project;    // 이 요청이 어느 프로젝트 것인지 붙잡아 둔다
  node.busy = true; node.err = false;
  let entries;
  try {
    const res = await fetch(`${API}/api/files?${fpQuery(project, node.path)}`);
    if (gen !== fpGen) return;                // 그 사이 프로젝트가 바뀌었다 — 새 트리에 손대지 않는다
    if (!res.ok) throw new Error(res.status);
    ({ entries = [] } = await res.json());    // 목록이 빠진 응답에도 화면이 멈추지 않게
    if (gen !== fpGen) return;
  } catch {
    if (gen !== fpGen) return;                // 옛 프로젝트의 실패를 새 화면에 적지 않는다
    node.busy = false; node.err = true;
    fpRenderTree();
    return;
  }
  node.busy = false;
  // 서버가 폴더 먼저·이름순으로 정렬하고 .env·.git·_uploads 같은 것은 빼고 준다(bridge/files.py) —
  // 화면이 다시 거르거나 다시 정렬하지 않는다.
  node.children = entries.map(e =>
    fpNode(e.name, node.path ? `${node.path}/${e.name}` : e.name, !!e.dir));
  fpRenderTree();
}

// ---------- 주기 갱신 — 터미널에서 생긴 폴더·파일이 저절로 나타난다 ----------
// children 은 '한 번 받으면 다시 안 받는' 캐시라 트리가 첫 읽기 그대로 멈춰 있었다(폴더가
// 생겨도 실시간으로 안 보였다). app.js 의 7초 폴링이 이걸 부른다 — 펼쳐 둔 폴더만
// 다시 읽고, 같은 이름의 노드는 객체를 이어받아 펼침·받아 둔 자식을 잃지 않는다. 서버 push(감시)는
// 브릿지 수정이 필요하고 이득이 작다 — 펼친 폴더 몇 개의 작은 JSON 이면 충분하다.
let fpRefreshing = false;
async function fpRefresh() {
  if (fpRefreshing || fpPanel().hidden || !fp.root || document.hidden) return;
  if (fp.mkdir) return;                       // 새 폴더 이름을 치는 중 — 다시 그리면 입력줄이 날아간다
  const dirs = [];
  (function collect(node) {
    if (!node.dir || !node.open || node.children === null || node.busy) return;
    dirs.push(node);
    for (const c of node.children) collect(c);
  })(fp.root);
  if (!dirs.length) return;
  fpRefreshing = true;
  const gen = fpGen, project = fp.project;
  let changed = false;
  try {
    await Promise.all(dirs.map(async node => {
      let entries;
      try {
        const res = await fetch(`${API}/api/files?${fpQuery(project, node.path)}`);
        if (!res.ok) return;                  // 잠깐 못 읽은 폴더는 다음 주기에 — 보던 것을 지우지 않는다
        ({ entries = [] } = await res.json());
      } catch { return; }
      if (gen !== fpGen || node.busy || node.children === null) return;   // 그 사이 프로젝트가 바뀌었거나 손으로 다시 받는 중
      const same = entries.length === node.children.length &&
        entries.every((e, i) => e.name === node.children[i].name && !!e.dir === node.children[i].dir);
      if (same) return;
      const old = new Map(node.children.map(c => [`${c.dir ? "d" : "f"}:${c.name}`, c]));
      node.children = entries.map(e =>
        old.get(`${e.dir ? "d" : "f"}:${e.name}`) ??
        fpNode(e.name, node.path ? `${node.path}/${e.name}` : e.name, !!e.dir));
      changed = true;
    }));
  } finally {
    fpRefreshing = false;
  }
  if (changed && gen === fpGen) fpRenderTree();
}

// 크럼의 폴더 이름 — 그 폴더를 트리에서 다시 펼친다(파일은 트리에서만 열리니 조상은 이미 받아 둔 상태다).
function fpRevealDir(path) {
  let node = fp.root;
  for (const name of path.split("/")) {
    node = (node?.children ?? []).find(c => c.dir && c.name === name);
    if (!node) return;
    node.open = true;
  }
}

// ---------- 파일 보기 ----------
// 열지 못한 경우엔 아래 칸을 갈아치우지 않는다 — 보던 것을 남겨 두고 이유만 한 줄로 알린다.
async function fpOpen(path, note = "") {
  if (FP_IMAGE_EXTS.has(extOf(path))) return fpOpenImage(path, note);
  const gen = fpGen, seq = ++fpFileSeq;       // 프로젝트 세대 + 이번 클릭 순번을 함께 붙잡는다
  const project = fp.project;
  let got;
  try {
    const res = await fetch(`${API}/api/file?${fpQuery(project, path)}`);
    if (gen !== fpGen || seq !== fpFileSeq) return;   // 프로젝트가 바뀌었거나 더 나중 클릭이 있다
    if (!res.ok) {
      fpNote(FILE_ERRORS[res.status] ?? `파일을 열지 못했습니다 (서버 응답 ${res.status})`);
      return;
    }
    got = await res.json();
    if (gen !== fpGen || seq !== fpFileSeq) return;
  } catch {
    if (gen !== fpGen || seq !== fpFileSeq) return;
    fpNote("맥에 연결되지 않아 파일을 열지 못했습니다 — Tailscale 연결을 확인하세요");
    return;
  }
  fpClearImageUrl();
  fp.file = path;
  fp.mtimeNs = got.mtimeNs;                   // 문자열 그대로 보관 — 저장할 때 이 값을 그대로 보낸다
  fp.editor = null;
  fpPanel().classList.add("wide");            // 파일을 열면 패널이 넓어진다(문서를 읽을 폭)
  fpCrumb();
  fpRenderTree();                             // 트리는 그대로 남고, 열어 둔 줄에 .sel 이 붙는다
  const edit = el("button", "✏️ 편집");
  edit.id = "fp-edit";
  edit.onclick = () => fpEditMode(got.content);
  const close = el("button", "✕ 닫기");      // 보던 파일을 손으로 닫는 길
  close.id = "fp-view-close";
  close.onclick = fpCloseFile;
  const pre = document.createElement("pre");
  pre.textContent = got.content;              // 파일 내용 — 반드시 textContent
  const view = fpView();
  view.replaceChildren(edit, close, pre);
  view.scrollIntoView({ block: "nearest" });  // 트리가 길면 본문이 화면 밖에서 열린다
  fpNote(note);
}

async function fpErrorReason(res, fallback) {
  try {
    const body = await res.json();
    return typeof body.reason === "string" && body.reason ? body.reason : fallback;
  } catch { return fallback; }
}

function fpFormatBytes(n) {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

async function fpOpenImage(path, note = "") {
  const gen = fpGen, seq = ++fpFileSeq, project = fp.project;
  fpNote("이미지를 불러오는 중입니다…");             // 큰 파일을 받는 동안 멈춘 화면으로 보이지 않게
  let res;
  try {
    res = await fetch(`${API}/api/file/image?${fpQuery(project, path)}`);
    if (gen !== fpGen || seq !== fpFileSeq) return;
    if (!res.ok) {
      const fallback = res.status === 413 ? "이미지가 너무 커서 열 수 없습니다"
        : res.status === 415 ? "이 형식은 파일 패널에서 이미지로 열 수 없습니다"
        : FILE_ERRORS[res.status] ?? `파일을 열지 못했습니다 (서버 응답 ${res.status})`;
      const reason = await fpErrorReason(res, fallback);
      if (gen !== fpGen || seq !== fpFileSeq) return;
      fpNote(reason);
      return;
    }
    const blob = await res.blob();
    if (gen !== fpGen || seq !== fpFileSeq) return;

    fpClearImageUrl();
    fp.imageUrl = URL.createObjectURL(blob);
    fp.file = path; fp.mtimeNs = null; fp.editor = null;
    fpPanel().classList.add("wide");
    fpCrumb(); fpRenderTree();

    const name = path.split("/").pop() || path;
    const close = el("button", "✕ 닫기");
    close.id = "fp-view-close";
    close.onclick = fpCloseFile;
    const meta = el("p", `${name} · ${fpFormatBytes(blob.size)}`);
    meta.className = "fp-image-meta";
    const loading = el("p", "그림을 표시하는 중입니다…");
    loading.className = "fp-image-loading";
    const img = document.createElement("img");
    img.className = "fp-image"; img.alt = name; img.hidden = true;
    img.onload = () => {
      if (gen !== fpGen || seq !== fpFileSeq || !img.isConnected) return;
      img.hidden = false; loading.remove(); fpNote(note);
    };
    img.onerror = () => {
      if (gen !== fpGen || seq !== fpFileSeq || !img.isConnected) return;
      loading.textContent = "이미지를 표시하지 못했습니다 — 맥에서 열기를 사용하세요";
      fpNote("");
    };
    fpView().replaceChildren(close, meta, loading, img);       // 편집·저장 버튼은 만들지 않는다
    img.src = fp.imageUrl;
    fpView().scrollIntoView({ block: "nearest" });
  } catch {
    if (gen !== fpGen || seq !== fpFileSeq) return;
    fpNote("맥에 연결되지 않아 이미지를 열지 못했습니다 — Tailscale 연결을 확인하세요");
  }
}

function fpClearImageUrl() {
  if (fp.imageUrl) URL.revokeObjectURL(fp.imageUrl);
  fp.imageUrl = null;
}

// 파일을 닫고 트리만 남긴다 — 크럼의 프로젝트 이름·폴더 이름이 부르는 자리.
function fpCloseFile() {
  fpClearImageUrl();
  fp.file = null; fp.editor = null;
  fpPanel().classList.remove("wide");
  fpNote("");
  fpCrumb();
  fpView().replaceChildren();
  fpRenderTree();                             // 열어 뒀던 줄의 .sel 을 거둔다
}

// ---------- 파일 고치기 ----------
function fpEditMode(content) {
  const ta = document.createElement("textarea");
  ta.value = content;
  const save = el("button", "💾 저장");
  save.id = "fp-save";
  save.onclick = () => fpSave(ta, save);
  const cancel = el("button", "취소");
  cancel.id = "fp-cancel";
  cancel.onclick = () => fpGo(() => fpOpen(fp.file));
  fp.editor = ta; fp.loaded = content;        // 여기서부터 '고쳤는지'를 이 값과 비교해 판단한다
  fpView().replaceChildren(save, cancel, ta);
  fpNote("");
}

async function fpSave(ta, btn) {
  // PUT 본문은 await 전에 굳지만(그래서 나가는 저장 자체는 늘 옳다) 응답 처리는 await 뒤다.
  // 고친 것이 없는 저장은 곧바로 이탈이 가능하므로(fpGo·fpSync 가 막지 않는다) 그 사이 세션이
  // 바뀔 수 있다 — 그때 응답을 그대로 처리하면 ① fpOpen(null) 로 거짓 오류를 띄우고
  // ② 409 라면 [덮어쓰기] 가 '새 프로젝트의' 파일을 옛 내용으로 덮으러 간다.
  const gen = fpGen, file = fp.file;
  btn.disabled = true;                        // 연타로 같은 저장이 두 번 나가지 않게
  fpNote("");
  let res;
  try {
    res = await fetch(`${API}/api/file`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: fp.project, path: file,
                             content: ta.value, baseMtimeNs: fp.mtimeNs }),   // 문자열 그대로 왕복
    });
  } catch {
    if (gen !== fpGen) return;
    btn.disabled = false;
    fpNote("맥에 연결되지 않아 저장하지 못했습니다 — 고친 내용은 아래 그대로 있습니다");
    return;
  }
  if (gen !== fpGen) return;                  // 다른 프로젝트로 옮겼다 — 이 답으로 화면을 만지지 않는다
  btn.disabled = false;
  if (res.status === 409) {
    // 열어본 뒤 다른 곳(클로드·에디터)에서 그 파일이 먼저 바뀌었다. 여기서 화면을 갈아치우면
    // 사용자가 방금 쓴 글이 사라진다 → 고친 내용은 textarea 에 그대로 두어 읽고 복사할 수 있게 하고,
    // 덮어쓸지 말지는 사용자가 고르게 한다(덮어쓰면 상대 수정은 사라진다 — 버튼에 그대로 적는다).
    fpNote("⚠️ 다른 곳에서 이 파일이 먼저 바뀌었습니다 — 아래 내 수정은 그대로 있습니다",
           { label: "덮어쓰기 (상대 수정은 사라집니다)", onClick: () => fpOverwrite(ta, btn) });
    return;
  }
  if (!res.ok) {
    fpNote(FILE_ERRORS[res.status] ?? `저장하지 못했습니다 (서버 응답 ${res.status})`);
    return;
  }
  // 새 버전 토큰(역시 문자열)을 확실히 받은 뒤에야 편집 상태를 푼다 — 먼저 풀면 응답이 깨졌을 때
  // 이탈 경고까지 같이 풀려, 고친 내용이 다음 클릭에 말없이 사라진다.
  let done;
  try {
    done = await res.json();
    if (gen !== fpGen) return;
  } catch {
    if (gen !== fpGen) return;
    fpNote("저장 결과를 읽지 못했습니다 — 파일을 다시 열어 확인하세요");
    return;
  }
  if (fp.file !== file) return;               // 그 사이 트리에서 다른 파일을 열었다 — 그 파일의 버전
  fp.mtimeNs = done.mtimeNs;                  // 토큰을 이 결과로 덮으면 다음 저장이 영영 409 가 된다
  fp.editor = null;
  fpOpen(file, "✅ 저장했습니다");              // 저장된 내용을 다시 읽어 보기 모드로
}

// 409 다음 한 걸음 — 최신 버전 토큰을 다시 받아 그 위에 내 내용을 올린다.
async function fpOverwrite(ta, btn) {
  const gen = fpGen, project = fp.project, file = fp.file;
  try {
    const res = await fetch(`${API}/api/file?${fpQuery(project, file)}`);
    if (gen !== fpGen) return;                // 프로젝트가 바뀌었다 — 남의 파일을 덮으러 가지 않는다
    if (!res.ok) throw new Error(res.status);
    fp.mtimeNs = (await res.json()).mtimeNs;
    if (gen !== fpGen) return;
  } catch {
    if (gen !== fpGen) return;
    fpNote("최신 내용을 확인하지 못했습니다 — 잠시 뒤 💾 저장을 다시 눌러 보세요");
    return;
  }
  fpSave(ta, btn);
}

// ---------- 새 폴더 ----------
// 모달·prompt() 금지(이 repo 계약) — 트리 안 인라인 입력줄로 받는다. Enter = 만들기, Esc = 취소.
// 위치는 둘: 머리의 [새 폴더] = 루트에, 폴더 우클릭 → '새 폴더 만들기' = 그 폴더 안에.
function fpNewDirPrompt(parent) {
  if (!parent) return;                        // 패널이 아직 루트를 못 받았다
  if (parent.dir && !parent.open) fpToggle(parent);   // 접힌 폴더면 펴서 입력줄이 보이게
  fp.mkdir = { parent };
  fpRenderTree();
}

function fpMkdirRow(parent, depth) {
  const li = document.createElement("li");
  li.className = "tree-row mkdir";
  li.style.setProperty("--depth", depth);
  const icon = el("span", "");
  icon.className = "tree-icon i-dir";
  // 입력칸은 한 번만 만들어 fp.mkdir 에 들고 있는다 — 접힌 폴더에 우클릭하면 로딩 응답이 곧
  // 트리를 다시 그리는데, 그때마다 새 입력칸으로 갈아치우면 치던 이름·포커스가 날아간다(실측).
  const input = fp.mkdir.input ??= fpMkdirInput();
  li.append(el("span", ""), icon, input);     // 셰브런 자리 빈 칸 — 폴더 줄과 세로를 맞춘다
  li.onclick = e => e.stopPropagation();
  if (document.activeElement !== input) requestAnimationFrame(() => input.focus());
  return li;
}

function fpMkdirInput() {
  const input = document.createElement("input");
  input.placeholder = "새 폴더 이름 — Enter 로 만들기";
  input.autocapitalize = "off"; input.autocomplete = "off"; input.spellcheck = false;
  input.onkeydown = e => {
    if (e.key === "Escape") { e.stopPropagation(); fp.mkdir = null; fpRenderTree(); }
    if (e.key === "Enter" && fp.mkdir) fpCreateDir(fp.mkdir.parent, input.value.trim());
  };
  return input;
}

async function fpCreateDir(parent, name) {
  if (!name) return;
  if (/[/\\]/.test(name)) { fpNote("폴더 이름에는 / 를 쓸 수 없습니다 — 한 번에 한 단계씩 만듭니다"); return; }
  const gen = fpGen;
  let res;
  try {
    res = await fetch(`${API}/api/mkdir`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: fp.project,
                             path: parent.path ? `${parent.path}/${name}` : name }),
    });
  } catch {
    if (gen === fpGen) fpNote("맥에 연결되지 않아 폴더를 만들지 못했습니다 — Tailscale 연결을 확인하세요");
    return;
  }
  if (gen !== fpGen) return;                  // 그 사이 프로젝트가 바뀌었다 — 아무것도 건드리지 않는다
  if (!res.ok) {
    fpNote(res.status === 409 ? `'${name}' — 같은 이름이 이미 있습니다`
         : res.status === 404 ? `'${name}' — 만들 수 없는 이름이거나 위치입니다`
         : `폴더를 만들지 못했습니다 (서버 응답 ${res.status})`);
    return;
  }
  fp.mkdir = null;
  fpNote("");
  fpFetchDir(parent);                         // 서버가 준 목록으로 다시 그린다 — 화면 추측 금지
}

// ---------- 우클릭 — 프로그램으로 열기 (맥에서 실행) ----------
// 지원 이미지도 여기서 맥 앱으로 열 수 있다. PDF·HTML·큰 이미지·SVG·HEIC에는 이 길이 필요하다.
// 앱 목록은 서버 화이트리스트와 1:1 — 임의 앱 문자열은 어느 쪽에서도 안 받는다.
const FP_OPENERS = [
  ["default", "기본 앱으로 열기"],
  ["preview", "미리보기로 열기"],
  ["chrome", "크롬으로 열기"],
  ["finder", "파인더에서 보기"],
];

function fpMenuEl() {
  let m = document.getElementById("fp-menu");
  if (!m) {
    m = document.createElement("div");
    m.id = "fp-menu";
    m.hidden = true;
    document.body.append(m);
  }
  return m;
}

function fpShowMenu(e, node) {
  e.preventDefault();                          // 브라우저 기본 우클릭 메뉴 대신 우리 메뉴
  const m = fpMenuEl();
  m.replaceChildren(...FP_OPENERS.map(([app, label]) => {
    const btn = el("button", label);
    btn.onclick = ev => { ev.stopPropagation(); fpHideMenu(); fpOpenWith(node, app); };
    return btn;
  }));
  if (node.dir) {                              // 폴더 안에 새 폴더 — 머리의 [새 폴더]는 루트 전용이라
    const mk = el("button", "새 폴더 만들기");
    mk.onclick = ev => { ev.stopPropagation(); fpHideMenu(); fpNewDirPrompt(node); };
    m.prepend(mk);
  }
  m.hidden = false;
  // 커서 자리에, 화면 밖으로 나가지 않게
  m.style.left = `${Math.min(e.clientX, innerWidth - m.offsetWidth - 8)}px`;
  m.style.top = `${Math.min(e.clientY, innerHeight - m.offsetHeight - 8)}px`;
}

const fpHideMenu = () => { fpMenuEl().hidden = true; };
document.addEventListener("click", fpHideMenu);
document.addEventListener("keydown", e => { if (e.key === "Escape") fpHideMenu(); });

async function fpOpenWith(node, app) {
  fpNote("");
  try {
    const res = await fetch(`${API}/api/open`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: fp.project, path: node.path, app }),
    });
    if (!res.ok) fpNote(`맥에서 열지 못했습니다 (서버 응답 ${res.status})`);
    // 성공 안내는 따로 없다 — 맥 화면에 앱이 뜨는 것이 피드백이다(폰 사용은 범위 밖)
  } catch {
    fpNote("맥에 연결되지 않아 열지 못했습니다 — Tailscale 연결을 확인하세요");
  }
}

// ---------- 패널은 늘 '지금 그 프로젝트' 를 봐야 한다 ----------
// currentSession 은 클릭으로만 바뀐다(사이드바 · ← 목록 · 인박스의 [터미널 열기]).
// 그래서 클릭이 끝난 뒤마다 패널이 아직 같은 프로젝트를 보고 있는지 확인한다 —
// 엉뚱한 프로젝트 파일을 그 프로젝트 것으로 착각하는 게 이 앱이 막으려는 사고다.
document.addEventListener("click", () => setTimeout(fpSync), true);

function fpSync() {
  const now = currentSession ? baseOf(currentSession.name) : null;   // 탭 전환은 같은 프로젝트다
  if (fpPanel().hidden || now === fp.project) return;
  if (fpDirty()) {                            // 편집 중 — 고친 내용을 말없이 버리지 않는다.
    fpNote(`✋ 터미널은 '${now ?? "목록"}' 으로 옮겼습니다 — 이 패널은 '${fp.project}' 의 저장 안 한 수정입니다`,
           { label: "수정 버리고 나가기", onClick: () => { fp.editor = null; fpSync(); } });
    return;
  }
  if (!now) { fpNewGen(); fpClose(); return; }  // 목록으로 나갔다 — 패널도 접는다
  fp.project = now;
  fpReset();                                   // 새 프로젝트의 루트부터 다시 (fpNewGen 이 들어 있다)
}
