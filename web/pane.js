"use strict";
// 터미널 칸(Pane) — app.js 다음에 로드한다(panes·activePane 선언과 입력 헬퍼들이 app.js 에 있다).
// 여기 함수·클래스는 전부 실행 시점에 다른 파일의 함수를 부르므로 로드 순서는 이 한 가지만 지키면 된다.

// ---------- 칸(Pane) — 터미널 하나의 모든 상태 (멀티 칸 설계 2026-10-09) ----------
// 예전엔 전역 하나(term·ws·타이머들)가 '그 터미널'이었다. 칸을 여럿 띄우려면 xterm·소켓·재접속·핑·
// 크기·붙여넣기 대기열·IME 거울·터치 스크롤이 칸마다 따로 돌아야 한다 — 그래서 전부 이 객체가 갖는다.
// 동작 자체(아래 주석의 사고 이력들)는 단일 터미널 시절 그대로다. 바뀐 것은 '누구의 것이냐'뿐이다.
const PING_EVERY = 15000, PONG_WAIT = 6000;

class Pane {
  constructor(host) {
    this.host = host;                        // xterm 을 여는 자리(칸의 터미널 영역)
    this.root = host.parentElement;          // 칸 전체(.pane) — 머리줄(grid.js)과 활성 테두리가 붙는다
    this.session = null;                     // {name, path, alive, agent?} — 이 칸이 보는 세션
    this.term = null; this.fit = null; this.ws = null; this.resizeObs = null;
    this.mirror = null;                      // 안드로이드에서만 xterm 입력 대신 textarea 문서를 소유한다
    this.reconnectTimer = null; this.pingTimer = null; this.pongTimer = null;
    this.wsFails = 0; this.wsHinted = false; // 조용한 무한 재접속 대신 이유를 알려주기 위한 카운터
    this.focusOnOpen = false;                // 소켓이 열리면 터미널에 포커스를 줄까 — 세션당 첫 접속만
    this.sentSize = "";                      // 마지막으로 보낸 "cols x rows" — 같은 값은 다시 안 보낸다
    this.waitingFirstByte = false;           // 접속 후 첫 바이트를 기다리는 중인가
    this.connectHintTimer = null;
    this.pendingPaste = [];                  // WS 가 열리기 전에 끝난 업로드 경로 — onopen 때 찍는다
    this.scrolledSinceKey = false;           // 마지막 키 이후 휠·드래그가 있었나 — 복사 모드 의심 표시
    this.touchY = null; this.touchLeft = 0;  // 폰 터치 스크롤
  }

  // this.session 을 정한 뒤 부른다 — xterm 을 만들고 소켓을 붙인다.
  start() {
    this.wsFails = 0; this.wsHinted = false; // 세션을 새로 열면 실패 카운터도 새로
    this.everOpen = false;                   // 한 번이라도 붙었나 — 그 뒤의 접속은 '저절로 다시 붙기'(re=1)다
    this.focusOnOpen = true;                 // 이 세션의 첫 접속에는 터미널로 포커스

    // 폰트·크기·색은 설정(settings.js 의 termOptions — 기본 One Dark + D2Coding)에서 온다.
    // xterm 은 CSS 가 아니라 이 옵션으로 폰트를 잡는다. 글자폭이 바뀌므로 fit() 이 뒤따라야 한다.
    const term = this.term = new Terminal({ scrollback: 5000, ...termOptions() });
    this.fit = new FitAddon.FitAddon();
    term.loadAddon(this.fit);
    // 링크는 Ctrl(⌘도 — OS 에서 두 키를 바꿔 쓰는 경우까지)+클릭으로만 연다(Zed·VS Code 방식).
    // 수정자 없는 클릭은 터미널 몫(tmux 마우스·커서)이라 실수로 안 열린다.
    // 폰은 수정키가 없다 — 탭 한 번으로 연다(터치 기기만. 마우스 기기는 지금처럼 수정키 필수).
    term.loadAddon(new WebLinksAddon.WebLinksAddon((e, uri) => {
      if (e.ctrlKey || e.metaKey || isTouchPointer()) window.open(reachableUrl(uri), "_blank", "noopener");
    }));
    // 파일 경로도 같은 조작으로 — Ctrl/⌘+클릭하면 파일 패널에서 그 파일이 열린다.
    term.registerLinkProvider({ provideLinks: (y, cb) => filePathLinks(this, y, cb) });
    term.open(this.host);
    this.connect(0);

    const send = d => this.send(d);
    this.mirror = installTerminalLiveMirror(term, send);
    const consumeCommittedIME = this.mirror ? null : guardComposition(term.textarea, send);
    term.onData(d => {                        // 키입력·xterm의 bracketed paste 포함
      this.mirror?.beforeExternalData();      // 미러 밖 입력은 끝 커서 전제를 깨므로 먼저 확정·리셋한다
      if (ctrlArmed && d.length === 1) {
        // ASCII(@·A~Z·[\]^_)만 제어문자로 조합한다 — 한글 같은 비ASCII 를 & 0x1f 로 접으면
        // 엉뚱한 제어문자가 되어 터미널을 헤집는다(실사고 2026-08-15). 비ASCII 는 글자 그대로.
        const c = d.toUpperCase().charCodeAt(0);
        if (c >= 0x40 && c <= 0x5f) d = String.fromCharCode(c & 0x1f);
        setCtrl(false);
      }
      // Ctrl+Z 는 일시중단(클로드가 멈추고 fg 로만 복구)이라 그대로 보내지 않는다. 대신 사용자가
      // 기대하는 '실행 취소'로 옮긴다 — 클로드 코드도 zsh 도 실행취소가 Ctrl+_(0x1f)다(실측).
      if (d === "\x1a") { this.send("\x1f"); return; }
      // Ctrl+D(EOF)는 세션을 통째로 닫는다 — 오타 한 번에 날아가지 않게 잠근다(끄기는 사이드바 🗑).
      if (d === "\x04") { showToast("Ctrl+D(종료)는 잠겨 있습니다 — 끄려면 사이드바의 🗑"); return; }
      imeLog("→보냄", d);                      // 진단(?imedebug=1) — 평소엔 아무 일도 하지 않는다
      // 휠·드래그 뒤 첫 '키' — tmux 복사 모드부터 빠져나온다. 마우스 리포트(휠 한 칸·드래그도
      // 이 onData 로 "\x1b[<…M" 꼴로 나간다)는 키가 아니다 — 여기서 걸면 휠이 자기 스크롤을 스스로
      // 취소해 아예 안 올라간다(2026-09-09 실사고). 그래서 리포트는 그대로 흘려보낸다.
      if (!isMouseReport(d)) this.leaveCopyModeIfScrolled();
      if (this.send(d)) consumeCommittedIME?.(d);  // 비안드로이드 blur 보관분에서 실제 전송 접두부를 차감한다
    });
    // 휠로 위를 보거나 드래그하면 tmux 가 복사 모드에 들어가고, 그 뒤 타이핑은 클로드가 아니라
    // 화면 안을 돌아다닌다(Esc 로 나와야 했다). "스크롤은 조회, 커서는
    // 입력창"이 맞는 그림이므로 스크롤·드래그가 있었으면 다음 첫 키 앞에 unmode 를 먼저 보낸다.
    // 판단은 tmux 가 한다(모드 밖이면 아무 일도 없다) — 여기서는 '있었다'는 표시만 세운다.
    term.element.addEventListener("wheel", () => { this.scrolledSinceKey = true; }, { passive: true });
    term.element.addEventListener("mousedown", e => { if (e.button === 0) this.scrolledSinceKey = true; });
    term.attachCustomKeyEventHandler(e => macEditKeys(e, this));
    // term.element 는 dispose 때 DOM 에서 통째로 제거되므로 이 핸들러들도 같이 사라진다
    term.element.addEventListener("touchstart", e => this.onTouchStart(e), { passive: true });
    term.element.addEventListener("touchmove", e => this.onTouchMove(e), { passive: false });
    // 우클릭 — 브라우저 메뉴와 tmux 메뉴(Kill·Respawn 같은 위험 항목 포함)가 겹쳐 떴다.
    // 둘 다 막는다: 브라우저 메뉴는 contextmenu 에서, tmux 메뉴는
    // 우클릭이 xterm(마우스 리포트)에 아예 닿지 않게 캡처 단계에서 끊어서.
    // 휠클릭(가운데)도 같이 끊는다 — 휠클릭을 화면 캡처에 쓰는 환경에선, 터미널 위에서 누르면 캡처와
    // 동시에 tmux 의 가운데버튼 붙여넣기(옛 버퍼)가 끼어드는 사고가 났다. 휠 '스크롤'은 별개라 산다.
    term.element.addEventListener("contextmenu", e => termMenu(e, this));   // 브라우저·tmux 메뉴 대신 우리 메뉴
    for (const type of ["mousedown", "mouseup"])
      term.element.addEventListener(type, e => {
        if (e.button === 1 || e.button === 2) { e.preventDefault(); e.stopPropagation(); }
      }, true);
    // ★드래그 복사를 '이 기기' 클립보드로.
    // tmux 가 mouse on 이라 드래그는 브라우저 선택이 아니라 tmux 선택이고, 이 맥의 tmux.conf 는
    // 결과를 pbcopy 로 보낸다 — 폰에서 드래그하면 복사물이 폰이 아니라 맥으로 가 붙여넣기가
    // 영영 안 됐다. 드래그가 끝나면 tmux 버퍼를 읽어 이 브라우저 클립보드에도 넣는다.
    let dragAt = null;
    term.element.addEventListener("mousedown", e => {
      if (e.button === 0) dragAt = [e.clientX, e.clientY, performance.now()];
    });
    term.element.addEventListener("mouseup", e => {
      if (e.button !== 0 || !dragAt) return;
      const moved = Math.abs(e.clientX - dragAt[0]) + Math.abs(e.clientY - dragAt[1]) > 4;
      const pressedAt = dragAt[2];
      dragAt = null;
      if (moved) setTimeout(() => syncCopyToClipboard(this, pressedAt), 120);   // tmux 가 버퍼에 넣을 틈을 준다
    });
    this.resizeObs = new ResizeObserver(() => this.resize());   // close 에서 끊는다 (세션마다 쌓이지 않게)
    this.resizeObs.observe(this.host);
    // 브라우저 자동완성 끄기 — xterm 은 autocorrect/autocapitalize/spellcheck 만 꺼 준다.
    // 다만 이건 폼 자동완성용이라 삼성키보드 예측입력을 막지는 못하고(막을 표준 수단이 없다),
    // 한글은 애초에 조합 입력이라 못 막는다. 안드로이드는 mirror, 나머지는 guardComposition 으로 지킨다.
    term.textarea.setAttribute("autocomplete", "off");
  }

  // 세션 전환·목록 복귀의 공통 뒷정리. 소켓이 둘 열려 있으면 두 세션의 글자가 한 화면에 섞인다.
  close() {
    clearTimeout(this.reconnectTimer);       // 재접속 예약 취소 — 나간 뒤 몰래 붙는 것 방지
    clearInterval(this.pingTimer); clearTimeout(this.pongTimer); this.pongTimer = null;
    clearTimeout(this.connectHintTimer);
    this.resizeObs?.disconnect();            // 낡은 관찰자가 새 소켓에 resize 를 겹쳐 쏘지 않게
    this.mirror?.dispose(); this.mirror = null;
    this.ws?.close(); this.term?.dispose();
    this.ws = null; this.term = null; this.fit = null; this.resizeObs = null; this.session = null;
    this.pendingPaste = [];                  // 못 찍은 경로가 다음 세션 터미널에 새지 않게
  }

  send(data) {                               // 이 칸 전송의 단일 출구 — 소켓이 열렸을 때만 나간다
    if (this.ws?.readyState !== 1) return false;
    this.ws.send(enc.encode(data));
    return true;                             // IME 가 "실제로 보냈다"와 blur 재전송을 가르는 근거
  }

  leaveCopyModeIfScrolled() {
    if (!this.scrolledSinceKey || this.ws?.readyState !== 1) return;
    this.scrolledSinceKey = false;
    this.ws.send(JSON.stringify({ type: "unmode" }));   // 같은 소켓이라 뒤따르는 키보다 먼저 처리된다
  }

  connect(retry) {
    // 접속 순간의 실제 크기를 서버에 함께 보낸다 — PTY 가 처음부터 이 크기로 열려야
    // tmux 가 80×24 화면을 한 번 그렸다 다시 그리는 잔상(배너 중복)이 안 생긴다.
    const term = this.term;
    this.fit?.fit();
    const size = term ? `?cols=${term.cols}&rows=${term.rows}` : "";
    const agent = this.session.agent ? `${size ? "&" : "?"}agent=${this.session.agent}` : "";
    // 다시 붙기 표시 — 브릿지는 끄기 버튼으로 끈 세션이면 새로 켜지 않고 4410 으로 닫는다(server.py KILLED).
    // 처음 여는 접속(사용자가 고른 것)에는 안 붙인다 — 그건 꺼진 세션을 켜려는 뜻이다.
    const re = this.everOpen ? `${size || agent ? "&" : "?"}re=1` : "";
    // 프로토콜은 페이지를 따른다 — 실사용(테일스케일 https)은 wss, 로컬 http 개발은 ws.
    // wss 고정이던 시절엔 http 로 열면 터미널만 말없이 죽었다.
    const wsProto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(
      `${wsProto}://${location.host}/ws/${encodeURIComponent(this.session.name)}${size}${agent}${re}`);
    this.ws = sock;                          // sock !== this.ws 면 낡은 세대 — 아래 핸들러들이 스스로 물러난다
    sock.binaryType = "arraybuffer";
    // 위 query 로 이미 이 크기를 알렸다 → onopen 의 resize 가 '같은 크기'를 또 보내면 tmux 가
    // SIGWINCH 를 한 번 더 쏴 앱(클로드 TUI)이 화면을 통째로 다시 그린다(실측: 여분 덤프 발생).
    this.sentSize = term ? `${term.cols}x${term.rows}` : "";
    this.waitingFirstByte = true;
    clearTimeout(this.connectHintTimer);      // 붙는 데 오래 걸리면 '연결 중'을 눈에 보이게 알린다 —
    this.connectHintTimer = setTimeout(() => {  // 옛 화면만 남아 멈춘 것처럼 보이던 것
      if (this.waitingFirstByte && sock === this.ws) showToast("터미널 연결 중…");
    }, 400);
    sock.onopen = () => { if (sock !== this.ws) return; this.everOpen = true; this.wsFails = 0; this.wsHinted = false; this.resize();
      this.pendingPaste.splice(0).forEach(d => this.send(d));   // 연결 전에 끝난 업로드 경로를 이제 찍는다
      clearInterval(this.pingTimer); this.pingTimer = setInterval(() => this.ping(), PING_EVERY);
      this.sendCompact();                    // 붙을 때마다 — 여러 칸이면 HUD 를 첫 줄만(브릿지 재시작 뒤에도 다시 단다)
      // 포커스는 처음 붙을 때만 — 백그라운드 재접속(핑 실패·절전 복귀)이 브라우저 패널·주소칸에서 치던 손을
      // 터미널로 끌어오면 다음 키가 엉뚱한 곳에 들어간다(코덱스 재검증 2026-09-20 지적).
      // 여러 칸이 한꺼번에 붙을 때 포커스는 활성 칸만 — 마지막에 붙은 칸이 키보드를 채가지 않게.
      if (this.focusOnOpen) { this.focusOnOpen = false; if (this === activePane) this.term?.focus(); } };
    sock.onmessage = e => {
      if (sock !== this.ws) return;
      clearTimeout(this.pongTimer);          // 무엇이든 왔다 = 살아 있다(핑 답도 빈 바이트로 온다)
      if (this.waitingFirstByte) { this.waitingFirstByte = false; clearTimeout(this.connectHintTimer); }
      this.term.write(new Uint8Array(e.data));
    };
    sock.onclose = e => {                    // 자동 재접속 (지수 백오프, 최대 15초)
      clearInterval(this.pingTimer); clearTimeout(this.pongTimer);   // 세대 검사보다 먼저 — 나간 뒤 타이머가 남지 않게
      if (sock !== this.ws) return;          // 낡은 소켓의 close 는 재접속을 트리거하지 않는다
      if (e.code === 4410) { paneKilled(this); return; }   // 다른 곳에서 끈 세션 — 되살리지 않고 칸을 비운다
      if (++this.wsFails >= 3 && !this.wsHinted) {   // 3회 연속 실패 = 뭔가 잘못됐다. 말없이 돌지 않는다.
        this.wsHinted = true;
        this.term?.write(`\r\n[frogterminal] 연결이 계속 끊깁니다 — 세션이 뜨자마자 죽으면 ⚙ 설정의 실행 명령을, 아니면 주소(${location.origin}/)를 확인하세요\r\n`);
      }
      if (!document.getElementById("term-view").hidden)
        this.reconnectTimer = setTimeout(() => this.connect(retry + 1), Math.min(15000, 500 * 2 ** retry));
    };
  }

  // 여러 칸으로 보는 중인지 브릿지에 알린다 — 브릿지가 그 tmux 세션에 표시를 달면 클로드 HUD
  // (~/.claude/scripts/statusline.py)가 첫 줄만 찍는다. 계정 줄은 웹 하단 공통 HUD(grid.js)가 한 세트.
  sendCompact() {
    if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ type: "compact", on: isGrid() }));
  }

  // ---------- 생존 확인(핑) — 반쯤 죽은 소켓을 잡는다 ----------
  // 윈도우 PC 가 절전·와이파이 전환을 겪으면 서버 쪽 연결은 끊기는데 브라우저는 여전히 '열림'으로
  // 안다. 그러면 화면이 옛 그림에 멈추고, 키를 쳐야(전송이 실패해야) 그제야 끊긴 걸 알고 다시
  // 붙었다. visibilitychange 의 readyState 검사도 이 상태는 못 잡는다(열림이니까).
  // 그래서 브라우저가 주기적으로 핑을 보내고, 답(무슨 메시지든)이 제때 없으면 스스로 다시 붙는다.
  ping() {
    const sock = this.ws;
    if (!sock || sock.readyState !== 1 || document.hidden || this.pongTimer) return;   // 숨은 탭은 쉰다(돌아올 때 즉시)
    sock.send(JSON.stringify({ type: "ping" }));
    this.pongTimer = setTimeout(() => {
      this.pongTimer = null;
      if (sock !== this.ws) return;
      sock.close();                          // 반쯤 죽은 소켓은 close 도 늦게 끝난다 — 기다리지 않고
      clearTimeout(this.reconnectTimer);     // 새 세대로 곧바로 붙는다(낡은 소켓 핸들러는 물러난다)
      this.connect(0);
    }, PONG_WAIT);
  }

  resize() {
    if (!this.fit || this.ws?.readyState !== 1) return;
    this.fit.fit();                          // 창 크기·폰 회전 → PTY 크기 동기화 (피커 상자 안 깨지게)
    const size = `${this.term.cols}x${this.term.rows}`;
    if (size === this.sentSize) return;      // 크기가 그대로면 침묵 — resize 마다 tmux 가 전체 화면을
    this.sentSize = size;                    // 다시 그려 스크롤백에 잔상이 쌓인다(배너 중복의 두 번째 원인)
    this.ws.send(JSON.stringify({ type: "resize", cols: this.term.cols, rows: this.term.rows }));
  }

  // ---------- 폰 터치 스크롤 ----------
  // xterm 5.5 는 마우스리포트가 켜진 터미널에서 자기 touchstart/touchmove 를 통째로 건너뛴다
  // (조건: !coreMouseService.areMouseEventsActive). tmux 는 mouse on 이라 항상 켜져 있으니
  // 폰에서 손가락 스와이프가 아무 일도 안 하는 상태였다. 게다가 스크롤백은 xterm 이 아니라
  // tmux 안에 있어서, xterm 뷰포트를 밀어봐야 애초에 움직일 것이 없다.
  // → 세로 스와이프를 휠 이벤트로 바꿔 xterm 의 기존 휠 경로에 그대로 태운다.
  //   그러면 SGR 휠 리포트가 tmux 로 가고, tmux 가 copy-mode 로 스크롤한다(데스크톱 휠과 같은 길).
  onTouchStart(e) {
    this.touchY = e.touches.length === 1 ? e.touches[0].clientY : null;   // 두 손가락(확대 등)은 무시
    this.touchLeft = 0;
  }

  onTouchMove(e) {
    const term = this.term;
    if (this.touchY === null || e.touches.length !== 1) return;
    // 마우스리포트가 꺼져 있으면 손대지 않는다 — 그 경우 xterm 의 휠 경로는 스크롤백 없는 화면에서
    // 휠을 방향키(↑↓)로 바꿔 입력으로 보내버려, claude 입력 이력이 멋대로 넘어간다.
    // 이때는 xterm 자체 터치 핸들러가 살아 있으니 그쪽에 맡긴다.
    if (term.modes.mouseTrackingMode === "none") return;
    const touch = e.touches[0];
    this.touchLeft += this.touchY - touch.clientY;   // 아래로 쓸면 음수 = 휠 위 = 과거 화면
    this.touchY = touch.clientY;
    const rowHeight = term.element.clientHeight / term.rows || 17;
    const rows = Math.trunc(this.touchLeft / rowHeight);
    if (!rows) return;                       // 한 줄이 안 되는 미세한 움직임(=탭)은 그냥 둔다
    this.touchLeft -= rows * rowHeight;      // 남은 자투리는 다음 이동에 이어서 센다
    if (e.cancelable) e.preventDefault();    // 화면이 같이 끌려가거나 탭(클릭)으로 오인되지 않게
    for (let i = 0; i < Math.abs(rows); i++)
      term.element.dispatchEvent(new WheelEvent("wheel", {
        deltaY: Math.sign(rows),
        deltaMode: WheelEvent.DOM_DELTA_LINE,  // 픽셀 대신 줄 단위 — 한 번에 정확히 1줄
        clientX: touch.clientX, clientY: touch.clientY,
        bubbles: true, cancelable: true,
      }));
  }
}

// 첫 칸은 index.html 에 이미 있는 #terminal — 설정(settings.js)이 로드 때 그 배경을 칠한다.
// 나머지 칸은 격자(grid.js)가 만든다.
activePane = new Pane(document.getElementById("terminal"));
panes.push(activePane);

const isGrid = () => panes.length > 1;      // 여러 칸 모드 — 파일·브라우저 패널을 저절로 열지 않는다

// 이벤트가 난 자리의 칸(파일 끌어넣기는 '떨어뜨린 칸'으로). 칸 밖이면 활성 칸.
const paneAt = target => panes.find(p => target && p.root.contains(target)) ?? activePane;

// 창 크기·분할선·글자 크기 변화 — 모든 칸의 PTY 크기를 다시 맞춘다(split.js·settings.js 가 부른다)
function sendResize() { for (const p of panes) p.resize(); }
