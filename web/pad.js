// ---------- 게임패드 — 패드 상주 프로그램의 ABXY 버튼으로 칸·프로젝트 이동 ----------
// 패드 상주 프로그램(별도 설치)이 브릿지에 POST /api/pad {slot, project} 를 보내면,
// 여기서 /api/pad/wait 롱폴로 받아 움직인다. 롱폴이라 버튼을 안 누르면 오가는 게 없다.
// 여러 칸이면 slot 번째 칸으로, 한 칸이면 project 세션을 연다. 폰은 패드가 없으니 반응하지 않는다.
const PAD_RETRY_MS = 5000;                   // 브릿지가 안 될 때(재시작·끊김) 다시 붙는 간격

function padGo({ slot, project }) {
  // 여러 칸이면 그 프로젝트가 떠 있는 칸을 먼저 찾는다 — 게임기 frog-agent 의 X 버튼은 칸 번호를 모르고
  // 프로젝트만 보낸다(2026-11-07). 패드 상주 프로그램는 칸 번호와 그 칸의 프로젝트를 같이 보내니 결과가 같다.
  if (isGrid() && project) {
    const i = panes.findIndex(p => p.session && (p.session.name === project || baseOf(p.session.name) === project));
    if (i >= 0) slot = i + 1;
  }
  if (isGrid() && panes.length >= slot) {
    const pane = panes[slot - 1];
    showView("term");                        // 사무실 화면이 떠 있으면 터미널에 자리를 내준다
    setActive(pane);
    pane.term?.focus();
    showToast(`패드: ${slot}번 칸` + (pane.session ? ` · ${baseOf(pane.session.name)}` : ""));
    return;
  }
  if (!project) { showToast(`패드: ${slot}번 칸 없음`); return; }
  const s = lastSessions.find(x => x.name === project);
  if (!s) { showToast(`패드: ${project} 없음`); return; }
  if (s.alive) openTerminal(s);
  else document.querySelector(`#sessions li[data-name="${CSS.escape(project)}"]`)?.click();   // 켤지 묻기 — 사이드바 클릭과 같다
  showToast(`패드: ${slot}번 · ${project}`);
}

async function padLoop() {
  let after = null;                          // 처음엔 지금 id 만 받는다 — 페이지를 열기 전에 누른 버튼으로 움직이지 않게
  for (;;) {
    try {
      const res = await fetch(`${API}/api/pad/wait` + (after === null ? "" : `?after=${after}`));
      if (!res.ok) throw new Error(res.status);
      const ev = await res.json();
      if (after !== null && ev.id !== after && ev.slot) padGo(ev);
      after = ev.id;
    } catch {
      await new Promise(r => setTimeout(r, PAD_RETRY_MS));
    }
  }
}

// ---------- 지금 보는 세션 알리기 — 게임기 frog-agent 선택이 맥 화면을 따라오게 ----------
// 칸·세션이 바뀔 때(paintHeader) 활성 칸의 세션 이름을 브릿지에 한 번 알린다. 같은 이름이면 보내지 않는다.
// 폰·태블릿은 게임기 옆에 있는 화면이 아니라 보내지 않는다.
let focusSent = "";
function reportFocus() {
  const name = activePane?.session?.name;
  if (!name || name === focusSent || isTouchPointer() || !isDesktop()) return;
  focusSent = name;
  fetch(`${API}/api/pad/focus`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project: name }),
  }).catch(() => { focusSent = ""; });
}

if (!isTouchPointer() && isDesktop()) padLoop();
