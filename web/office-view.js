"use strict";
// 3D 모션 모드 — 사무실 화면 그리기·오른쪽 활동 패널·켜고 끄기.
// 켜진 프로젝트 하나 = 캐릭터 하나(개구리는 모자 색, 카피바라는 가디건 색으로 프로젝트를 가른다).
// 누가 무엇을 하는지는 office-life.js(심즈식 하루), 좌표·대사는 office-world.js, 칸 규격은
// office-atlas.js(그림 만들 때 자동 생성). 여기는 그것을 캔버스 하나에 30fps 로 그린다 — 화면이
// 안 보이면(꺼짐·탭 숨김) 그리기도 데이터 읽기도 멈춘다. 캐릭터를 누르면 사무실이 가운데로 쏙
// 빨려 들어가고(맥 독 최소화처럼) 그 프로젝트 터미널이 펼쳐진다.

(function () {
  const A = OFFICE_ATLAS;
  // 자세 이름 → [시트 꼬리, 칸, 원래 보는 쪽(-1 왼쪽·1 오른쪽·0 정면)]
  const POSES = {
    stand: ["", 0, -1], sitBack: ["", 3, 0], sitFront: ["", 4, 0], mug: ["", 5, 0],
    talk: ["2", 1, 1], laugh: ["2", 2, 0], sip: ["2", 3, 0], wave: ["2", 4, 0], think: ["2", 5, 0],
    phone: ["2", 6, 0], stretch: ["2", 7, 0],
    // 등 돌리고 서기(화이트보드·커피·랙) — 예전 GPT 뒤로 걷기 그림. 진짜 걷기는 아래 stepF·stepB
    walkB: ["3", 3, 1],
    cheer: ["3", 5, 0], scratch: ["3", 6, 0], sofaTalk: ["3", 7, 0],
    mStand: ["mini", 0, 0], mWalk: ["walk", 16, -1], mWalk2: ["walk", 17, -1], mBye: ["mini", 3, 0],
    mJump: ["mini", 4, 0], mSide: ["mini", 5, 1], mBow: ["mini", 6, 0], mBack: ["walk", 18, 1], mBack2: ["walk", 19, 1],
    // 휴대폰 만지작 — 서서 스크롤·문자·킥킥, 앉아서(소파 정면) 스크롤·문자·킥킥, 책상 등지고
    phScroll: ["phone", 1, 0], phText: ["phone", 2, 0], phLol: ["phone", 3, 0],
    sitScroll: ["phone", 4, 0], sitText: ["phone", 5, 0], sitBackPhone: ["phone", 6, 0], sitLol: ["phone", 7, 0],
  };
  // 걷기(요청 2026-10-22: "저벅저벅") — 블렌더로 뽑은 한 걸음 8장, walk 시트 앞 0~7·뒤 8~15
  for (let n = 0; n < 8; n++) { POSES["stepF" + n] = ["walk", n, -1]; POSES["stepB" + n] = ["walk", 8 + n, 1]; }
  const SHEETS = ["frog", "frog2", "frog3", "frogmini", "frogwalk", "frogphone",
                  "capy", "capy2", "capy3", "capymini", "capywalk", "capyphone"];
  const TINT = 0.6;              // 색칠한 칸 축척 — 쓰는 자세만 그때그때 칠해 메모리를 아낀다
  const STAND = 96;              // 보통 캐릭터 키(사무실 그림 픽셀) — 1차보다 작게(10%↓·넓게)
  const KID = 0.7;               // 커밋이 적은 저장소 = 꼬마(70%)
  const MINI = 0.5;              // 서브에이전트 = 주인 키의 50%
  const FRAME_MS = 1000 / 30, POLL_MS = 3000;
  const BG = "#EDEFF1";
  const FONT = "'Pretendard Variable', Pretendard, -apple-system, sans-serif";
  const DEBUG = new URLSearchParams(location.search).has("officedebug");
  const L = OfficeLife.L;
  const $ = id => document.getElementById(id);
  const st = { on: false, imgs: null, loaded: false, failed: "", cells: new Map(), raf: 0, last: 0, poll: 0,
               data: null, view: { s: 1, ox: 0, oy: 0, dpr: 1 }, parts: [], logSeen: new Set(), logQ: [], drip: 0 };
  const rnd = (a, b) => a + Math.random() * (b - a);
  const pick = arr => arr[Math.floor(Math.random() * arr.length)];

  // ---------- 그림 ----------
  function loadImages() {
    if (st.imgs) return;
    st.imgs = {};
    const names = ["floor", "chairs", ...SHEETS.flatMap(s => [s, s + "-key"])];
    let left = names.length;
    for (const n of names) {
      const img = new Image();
      img.onload = () => { if (--left === 0) { st.loaded = true; bar(); } };
      img.onerror = () => { st.failed = "사무실 그림을 불러오지 못했습니다 — 새로고침해 보세요"; bar(); };
      img.src = `office/${n}.webp`;
      st.imgs[n] = img;
    }
  }
  function canvasOf(w, h) { const c = document.createElement("canvas"); c.width = w; c.height = h; return c; }
  // 자세 한 칸을 프로젝트 색으로 — 마스크에 색을 채워 본판에 곱한다(명암·니트 결은 살고 색만 바뀐다)
  function cell(sheet, i, color) {
    const key = `${sheet}|${i}|${color}`;
    let c = st.cells.get(key);
    if (c) return c;
    const w = Math.round(A.cellW * TINT), h = Math.round(A.cellH * TINT);
    const m = canvasOf(w, h), mc = m.getContext("2d");
    mc.drawImage(st.imgs[sheet + "-key"], i * A.cellW, 0, A.cellW, A.cellH, 0, 0, w, h);
    mc.globalCompositeOperation = "source-in";
    mc.fillStyle = color;
    mc.fillRect(0, 0, w, h);
    c = canvasOf(w, h);
    const cc = c.getContext("2d");
    cc.drawImage(st.imgs[sheet], i * A.cellW, 0, A.cellW, A.cellH, 0, 0, w, h);
    cc.globalCompositeOperation = "multiply";
    cc.drawImage(m, 0, 0);
    st.cells.set(key, c);
    return c;
  }

  // 자세 하나를 발끝(x,y) 기준으로 — 돌려주는 값 = 눌림 판정·글씨 자리용 상자(그림 좌표)
  function sprite(ctx, species, color, pose, x, y, stand, face, o = {}) {
    const [tail, i, native] = POSES[pose] || POSES.stand;
    const sheet = species + tail;
    const c = cell(sheet, i, color);
    const k = stand * (o.k || 1) / (A.stand * TINT);
    const dw = c.width * k, dh = c.height * k;
    const mirror = native ? face !== native : (!pose.startsWith("sitBack") && face > 0);
    ctx.save();
    ctx.globalAlpha = o.alpha ?? 1;
    ctx.translate(x, y);
    if (o.tilt) ctx.rotate(o.tilt);
    ctx.scale((mirror ? -1 : 1) * (o.sx || 1), o.sy || 1);
    ctx.drawImage(c, -dw / 2, -dh + 2 * TINT * k, dw, dh);
    ctx.restore();
    const h = stand * (o.k || 1) * (A.heights[sheet]?.[i] ?? 1) * (o.sy || 1);
    return { x: x - h * 0.34, y: y - h, w: h * 0.68, h, top: y - h };
  }
  function shadow(ctx, x, y, w) {
    ctx.fillStyle = "rgba(20, 30, 25, .15)";
    ctx.beginPath();
    ctx.ellipse(x, y - 1, w, w * 0.32, 0, 0, Math.PI * 2);
    ctx.fill();
  }

  // ---------- 꼬물거림 — 같은 자세라도 늘 조금씩 움직인다 ----------
  const jelly = (t, speed, amt) => { const w = Math.sin(t * speed); return { sx: 1 - w * amt * 0.7, sy: 1 + w * amt }; };
  function hopFx(since, h) {                 // 폴짝 한 번: 웅크림 → 늘어나며 뜀 → 찌그러지며 착지
    if (since < 0 || since > 0.62) return null;
    if (since < 0.08) return { dy: 0, sx: 1.1, sy: 0.88 };
    if (since < 0.5) { const u = (since - 0.08) / 0.42; return { dy: -Math.sin(u * Math.PI) * h, sx: 0.94, sy: 1.08 }; }
    return { dy: 0, sx: 1.12, sy: 0.86 };
  }
  const standOf = p => STAND * (p.kid ? KID : 1);

  // 방향을 바꾸면 '뿅' — 가로가 잠깐 좁아졌다 돌아온다(휙 뒤집히는 대신)
  function turn(e, face, t) { if (e.face !== face) { e.face = face; e.turnT = t; } }
  const turnSx = (e, t) => (e.turnT && t - e.turnT < 0.14 ? 0.62 + (t - e.turnT) / 0.14 * 0.38 : 1);
  function poseOf(p, t) {
    if (p.over && t < p.over.until) return p.over.pose;
    if (p.path.length) {
      const up = (p.vy || 0) < -Math.abs(p.vx || 0) * 0.25;
      if (Math.abs(p.vx || 0) > 0.5) turn(p, p.vx > 0 ? 1 : -1, t);
      // 걸은 거리로 장을 넘긴다 — 한 장 = 그림 보폭(strides, 칸 픽셀)을 화면 키로 줄인 만큼. 발이 안 미끄러진다
      const n = Math.floor((p.dist || 0) / (A.strides[p.species] * standOf(p) / A.stand)) % 8;
      return (up ? "stepB" : "stepF") + n;
    }
    return p.act?.pose || "stand";
  }

  function drawPerson(ctx, p) {
    const t = L.t, stand = standOf(p), ph = p.phase, pose = poseOf(p, t);
    let dy = 0, dx = 0, tilt = 0, { sx, sy } = jelly(t + ph, 3.2, 0.03);
    if (p.path.length && !(p.over && t < p.over.until)) {            // 걸음 — 저벅저벅, 몸의 오르내림은 그림 8장에 들어 있다
      sx = sy = 1;
    } else if (pose === "sitBack" && p.act?.work) {                   // 빡 코딩 — 어깨 들썩 + 잔떨림
      dx = Math.sin(t * 38 + ph) * 0.6; dy = -Math.abs(Math.sin(t * 17 + ph)) * 1.6;
      tilt = Math.sin(t * 7 + ph) * 0.03; ({ sx, sy } = jelly(t + ph, 17, 0.02));
    } else if (p.act?.hop && pose === "stand") {                      // 결재 대기 — 쉬지 않고 폴짝폴짝
      ({ dy, sx, sy } = hopFx(((t + ph) * 1.15) % 0.9, 18) || { dy: 0, sx: 1, sy: 1 });
      tilt = Math.sin(t * 6 + ph) * 0.06;
    } else if (pose === "sitLol" || pose === "phLol") {              // 휴대폰 보다 킥킥 — 작게 들썩
      dy = -Math.abs(Math.sin(t * 13 + ph)) * 1.8; tilt = Math.sin(t * 6.5 + ph) * 0.04;
    } else if (pose.startsWith("sit")) {                              // 앉아서(휴대폰 포함) — 느릿한 숨 + 갸웃
      ({ sx, sy } = jelly(t + ph, 1.8, 0.025)); tilt = Math.sin(t * 0.9 + ph) * 0.035;
    } else if (pose === "phScroll" || pose === "phText") {            // 서서 휴대폰 — 고개 까딱, 엄지 톡톡
      tilt = Math.sin(t * 1.3 + ph) * 0.025; dy = -Math.abs(Math.sin(t * 5 + ph)) * 0.6;
    } else if (pose === "laugh") {                                    // 깔깔 — 빠르게 들썩
      dy = -Math.abs(Math.sin(t * 16)) * 3; tilt = Math.sin(t * 8) * 0.05;
    } else if (pose === "talk" || pose === "sofaTalk" || pose === "phone") {
      dy = -Math.abs(Math.sin(t * 7 + ph)) * 2; tilt = Math.sin(t * 3.5 + ph) * 0.05;
    } else if (pose === "cheer") {
      ({ dy, sx, sy } = hopFx((t * 1.6) % 0.62, 8) || { dy: 0, sx: 1, sy: 1 });
    } else if (pose === "wave") {
      tilt = Math.sin(t * 9) * 0.07;
    } else if (pose === "sip" || pose === "mug") {
      tilt = Math.sin(t * 1.4 + ph) * 0.04;
    } else if (pose === "stretch") {
      sy = 1 + Math.abs(Math.sin(t * 2.5)) * 0.05; sx = 1 - (sy - 1) * 0.6;
    } else {
      tilt = Math.sin(t * 1.7 + ph) * 0.035;
    }
    if (!pose.startsWith("sit")) shadow(ctx, p.x, p.y, stand * 0.21 * (1 + dy / 60));
    // 의자의 꼬마는 방석을 깔고 앉은 듯 올린다 — 안 그러면 등받이에 모자만 보인다. 앞줄 의자일수록 커서(원근)
    // 의자마다 올리는 양(chairs[].kid)을 그림 만들 때 재 둔다: 어느 줄에서나 머리가 비슷하게 나온다.
    const chair = p.kid && !p.path.length && p.act?.chair >= 0 ? A.chairs?.[p.act.chair] : null;
    if (chair) dy -= chair.kid || 0;
    p.box = sprite(ctx, p.species, p.color, pose, p.x + dx, p.y + dy, stand, p.face,
                   { alpha: p.alpha, sx: sx * turnSx(p, t), sy, tilt });
    p.feet = p.y;
  }

  function drawMini(ctx, m) {
    const p = L.people.get(m.owner);
    const species = p?.species || "frog", color = p?.color || "#888", stand = p ? standOf(p) : STAND;
    const t = L.t, ph = m.phase;
    let pose = "mStand", face = m.face, dy = 0, dx = 0, tilt, sx, sy;
    if (m.over && t < m.over.until) pose = m.over.pose;
    if (m.path.length) {                                              // 종종걸음 — 빠른 걸음, 발 딛을 때 통
      const up = (m.vy || 0) < -Math.abs(m.vx || 0) * 0.3;
      if (Math.abs(m.vx || 0) > 0.5) turn(m, m.vx > 0 ? 1 : -1, t);
      face = m.face;
      const f = Math.floor((m.wp || 0) / Math.PI) % 2;
      pose = up ? (f ? "mBack2" : "mBack") : (f ? "mWalk2" : "mWalk");
      const w = Math.sin(m.wp || 0), c = 1 - Math.abs(w);
      tilt = w * 0.08; dy = -Math.abs(w) * 2.6;
      sy = 1 - 0.06 * c * c; sx = 1 + 0.05 * c * c;
    } else {                                                          // 줄 서서 꼼지락 — 한시도 가만히 안 있는다
      const w = Math.sin(t * 6 + ph);
      tilt = w * 0.12; dx = Math.sin(t * 2.3 + ph) * 2; dy = -Math.abs(w) * 1.6;
      ({ sx, sy } = jelly(t + ph, 6, 0.05));
      if (pose === "mJump") ({ dy, sx, sy } = hopFx(0.6 - (m.over.until - t), 9) || { dy, sx, sy });
      if (m.flipT && t - m.flipT < 0.15) sx *= 0.7 + (t - m.flipT) / 0.15 * 0.3;   // 고개 돌릴 때 '뿅'
    }
    shadow(ctx, m.x, m.y, stand * MINI * 0.2);
    m.box = sprite(ctx, species, color, pose, m.x + dx, m.y + dy, stand, face,
                   { alpha: m.alpha, k: MINI, sx: sx * turnSx(m, t), sy, tilt });
  }

  // ---------- 글씨 — 이름표(발밑, 작게)·말풍선(머리 위) ----------
  function roundBox(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x + 0.5, y + 0.5, w, h, r); }
  function nameTags(ctx) {
    const tags = [];
    for (const p of L.people.values()) {
      if (!p.box || p.alpha < 0.3) continue;
      const [x, y] = scr(p.x, p.feet);
      ctx.font = `600 10px ${FONT}`;
      tags.push({ p, x, y: y + 3, w: ctx.measureText(p.name).width + 16, h: 14 });
    }
    tags.sort((a, b) => a.y - b.y);
    const placed = [];
    for (const g of tags) {                    // 겹치면 아래로 비켜 쌓는다
      for (let k = 0; k < 6; k++) {
        const o = placed.find(q => Math.abs(q.x - g.x) < (q.w + g.w) / 2 + 2 && g.y < q.y + q.h + 1 && g.y + g.h > q.y - 1);
        if (!o) break;
        g.y = o.y + o.h + 2;
      }
      placed.push(g);
      const lx = Math.round(g.x - g.w / 2);
      ctx.fillStyle = "rgba(255, 255, 255, .86)";
      ctx.strokeStyle = "rgba(20, 30, 25, .12)";
      ctx.lineWidth = 1;
      roundBox(ctx, lx, g.y, g.w, g.h, 3); ctx.fill(); ctx.stroke();
      ctx.fillStyle = g.p.color;
      ctx.beginPath(); ctx.arc(lx + 7, g.y + g.h / 2 + 0.5, 3, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#26302B"; ctx.textBaseline = "middle"; ctx.font = `600 10px ${FONT}`;
      ctx.fillText(g.p.name, lx + 12, g.y + g.h / 2 + 1);
    }
  }
  function bubbles(ctx) {
    const items = [];
    const add = (e, text, kind, color) => {
      if (!e.box || e.alpha < 0.3) return;
      const [x, top] = scr(e.x, e.box.top);
      ctx.font = kind === "ask" ? `700 12px ${FONT}` : `${kind === "work" ? 600 : 500} 12px ${FONT}`;
      const w = ctx.measureText(text).width + (kind === "ask" ? 8 : kind === "work" ? 22 : 16);
      items.push({ x, bottom: top - 4, w, h: kind === "ask" ? 17 : 22, text, kind, color });
    };
    for (const p of L.people.values()) {
      if (p.status === "decision") add(p, OfficeLife.cut(p.ask || "결재 대기", 20), "ask", p.color);
      if (p.bubble) add(p, p.bubble.text, p.bubble.kind, p.color);
    }
    for (const m of L.minis.values()) if (m.bubble) add(m, m.bubble.text, m.bubble.kind, L.people.get(m.owner)?.color);
    items.sort((a, b) => b.bottom - a.bottom);
    const placed = [];
    for (const b of items) {                  // 겹치면 위로 비켜 쌓는다
      for (let k = 0; k < 8; k++) {
        const o = placed.find(q => Math.abs(q.x - b.x) < (q.w + b.w) / 2 + 3 && b.bottom > q.bottom - q.h - 3 && b.bottom - b.h < q.bottom + 3);
        if (!o) break;
        b.bottom = o.bottom - o.h - 4;
      }
      placed.push(b);
      drawBubble(ctx, b);
    }
  }
  function drawBubble(ctx, b) {
    const lx = Math.round(b.x - b.w / 2), ty = Math.round(b.bottom - b.h);
    ctx.textBaseline = "middle";
    if (b.kind === "ask") {                    // 결재 대기 — 빨간 글씨(흰 테두리로 어디서나 읽히게)
      ctx.font = `700 12px ${FONT}`; ctx.textAlign = "center"; ctx.lineJoin = "round";
      ctx.lineWidth = 4; ctx.strokeStyle = "rgba(255, 255, 255, .95)";
      ctx.strokeText(b.text, b.x, ty + b.h / 2);
      ctx.fillStyle = "#D7263D"; ctx.fillText(b.text, b.x, ty + b.h / 2);
      ctx.textAlign = "start";
      return;
    }
    const think = b.kind === "think";
    ctx.fillStyle = think ? "#F2F4F6" : "#FFFFFF";
    ctx.strokeStyle = "rgba(20, 30, 25, .2)"; ctx.lineWidth = 1;
    roundBox(ctx, lx, ty, b.w, b.h, think ? 11 : 6); ctx.fill(); ctx.stroke();
    if (think) {                               // 생각 = 동글동글 꼬리
      ctx.beginPath(); ctx.arc(b.x - 2, ty + b.h + 4, 2.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    } else {
      ctx.beginPath(); ctx.moveTo(b.x - 5, ty + b.h); ctx.lineTo(b.x, ty + b.h + 6); ctx.lineTo(b.x + 5, ty + b.h); ctx.fill();
    }
    if (b.kind === "work") { ctx.fillStyle = b.color || "#4ADE80"; ctx.fillRect(lx + 4, ty + 5, 3, b.h - 10); }
    ctx.fillStyle = think ? "#4B5563" : "#1E2622";
    ctx.font = `${b.kind === "work" ? 600 : 500} 12px ${FONT}`;
    ctx.fillText(b.text, lx + (b.kind === "work" ? 12 : 8), ty + b.h / 2 + 1);
  }

  // ---------- 한 장 그리기 ----------
  function layout() {
    const cv = $("office-canvas"), box = $("office-stage");
    const cw = box.clientWidth, ch = box.clientHeight;
    if (!cw || !ch) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.width = Math.round(cw * dpr); cv.height = Math.round(ch * dpr);
    cv.style.width = cw + "px"; cv.style.height = ch + "px";
    const s = Math.min(cw / (OFFICE_W - 40), ch / (OFFICE_H - 30));    // 벽 바깥 여백은 조금 잘라도 된다
    st.view = { s, ox: cw / 2 - OFFICE_W / 2 * s, oy: ch / 2 - (OFFICE_H / 2 + 4) * s, dpr };
  }
  const scr = (x, y) => [st.view.ox + x * st.view.s, st.view.oy + y * st.view.s];

  function draw() {
    const cv = $("office-canvas"), ctx = cv.getContext("2d"), v = st.view;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, cv.width, cv.height);
    if (!st.loaded) return;
    ctx.setTransform(v.s * v.dpr, 0, 0, v.s * v.dpr, v.ox * v.dpr, v.oy * v.dpr);
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(st.imgs.floor, 0, 0, OFFICE_W, OFFICE_H);
    // 깊이 순서(발끝 y)대로 — 책상·회의실 의자 등받이도 끼워 넣는다: 앉은 사람(발끝 = 등받이 아래끝 - 2)보다 나중,
    // 의자 앞 통로를 지나는 사람보다 먼저. 그래서 앉은 사람은 허리 아래가 등받이에 가려진다.
    const all = [...L.people.values(), ...L.minis.values(), ...CHAIRS].sort((a, b) => a.y - b.y);
    for (const e of all) (e.chair ? drawChair : e.owner ? drawMini : drawPerson)(ctx, e);
    if (DEBUG) debugMarks(ctx);
    ctx.setTransform(v.dpr, 0, 0, v.dpr, 0, 0);    // 글씨는 화면 좌표로 — 사무실을 줄여도 크기 그대로
    for (const q of st.parts) {
      const a = 1 - (L.t - q.born) / q.life;
      const [x, y] = scr(q.x, q.y - (L.t - q.born) * 22);
      ctx.globalAlpha = Math.max(0, a);
      ctx.font = "700 12px ui-monospace, Menlo, monospace";
      ctx.fillStyle = q.color;
      ctx.fillText(q.text, x - 8, y);
    }
    ctx.globalAlpha = 1;
    nameTags(ctx);
    bubbles(ctx);
    for (const p of L.people.values()) {
      if (p.extra > 0 && p.box) {
        const [mx, my] = scr(p.x + 30 + 6 * 21, p.y + 34);
        ctx.font = `700 12px ${FONT}`; ctx.fillStyle = "#1E2622"; ctx.fillText(`+${p.extra}`, mx, my);
      }
    }
  }
  const CHAIRS = (A.chairs || []).map(c => ({ chair: c.box, y: c.seat[1] + OfficeLife.SIT_DESK + 1 }));
  function drawChair(ctx, c) {
    const [x, y, w, h] = c.chair;
    ctx.drawImage(st.imgs.chairs, x, y, w, h, x, y, w, h);
  }
  function particles() {
    const t = L.t;
    for (const p of L.people.values()) {
      if (st.parts.length > 60 || t < p.nextPart) continue;
      if (p.act?.work && !p.path.length) {
        p.nextPart = t + rnd(0.5, 1.1);
        const side = Math.random() < 0.5 ? -1 : 1;
        st.parts.push({ x: p.x + side * rnd(14, 26), y: p.y - standOf(p) * rnd(0.55, 0.8), text: pick(OFFICE_CODE),
                        color: p.color, born: t, life: 1.6 });
      } else if (p.act?.sleepy && !p.path.length && t - p.arrivedAt > 8 && !(p.over && t < p.over.until)) {
        p.nextPart = t + rnd(1.6, 2.6);
        st.parts.push({ x: p.x + 12, y: p.y - standOf(p) * 0.75, text: "z", color: "#7A8A99", born: t, life: 2.2 });
      }
    }
    st.parts = st.parts.filter(q => t - q.born < q.life);
  }
  function debugMarks(ctx) {
    ctx.font = "11px monospace";
    ctx.strokeStyle = "rgba(220,0,0,.8)"; ctx.lineWidth = 3;
    for (const [a, b] of OFFICE_WALLS) { ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke(); }
    ctx.strokeStyle = "rgba(230,120,0,.9)"; ctx.lineWidth = 2;
    for (const poly of OFFICE_BLOCKS) { ctx.beginPath(); poly.forEach((p, i) => (i ? ctx.lineTo(...p) : ctx.moveTo(...p))); ctx.closePath(); ctx.stroke(); }
    ctx.strokeStyle = "rgba(0,150,0,.6)";
    ctx.beginPath(); OFFICE_FLOOR.forEach((p, i) => (i ? ctx.lineTo(...p) : ctx.moveTo(...p))); ctx.closePath(); ctx.stroke();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(0,0,170,.35)";
    for (const [a, b] of OFFICE_EDGES) { ctx.beginPath(); ctx.moveTo(...OFFICE_NODES[a]); ctx.lineTo(...OFFICE_NODES[b]); ctx.stroke(); }
    for (const [k, [x, y]] of Object.entries(OFFICE_NODES)) { ctx.fillStyle = "#00A"; ctx.fillRect(x - 3, y - 3, 6, 6); ctx.fillText(k, x + 4, y - 4); }
    ctx.fillStyle = "#C00";
    for (const d of OFFICE_DESKS) ctx.fillRect(d.seat[0] - 4, d.seat[1] - 4, 8, 8);
    ctx.fillStyle = "#0A0";
    for (const list of Object.values(OFFICE_SPOTS)) for (const s of list) ctx.fillRect(s.at[0] - 4, s.at[1] - 4, 8, 8);
    for (const p of L.people.values()) {                  // 가는 길
      if (!p.path.length) continue;
      ctx.strokeStyle = p.color; ctx.beginPath(); ctx.moveTo(p.x, p.y);
      for (const q of p.path) ctx.lineTo(...q);
      ctx.stroke();
    }
  }

  function frame(now) {
    if (!st.on) return;
    st.raf = requestAnimationFrame(frame);
    if (document.hidden || now - st.last < FRAME_MS) return;
    const dt = Math.min(0.1, (now - (st.last || now)) / 1000);
    st.last = now;
    OfficeLife.update(dt);
    particles();
    draw();
  }

  // ---------- 오른쪽 패널 — 사무실 사람들 + 살아 움직이는 활동 로그 ----------
  const STATUS = { decision: ["결재 대기", "ask"], working: ["일하는 중", "work"] };
  // 화면의 상태 = 결재 > 일(본체가 쉬어도 꼬마가 일하면 일하는 중) > 쉼 — office-life.js 와 같은 잣대
  const modeOf = d => (d.status === "decision" ? "decision" : d.status === "working" || d.subCount > 0 ? "working" : "rest");
  const hhmmss = ms => new Date(ms).toLocaleTimeString("ko-KR", { hour12: false, timeZone: "Asia/Seoul" });
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function dot(color) { const d = el("i", "o-dot"); d.style.background = color; return d; }
  function renderPeople() {
    const list = $("office-people");
    if (!list || !st.data) return;
    const order = { decision: 0, working: 1 };
    const people = [...st.data].sort((a, b) => (order[modeOf(a)] ?? 2) - (order[modeOf(b)] ?? 2) || a.name.localeCompare(b.name));
    $("office-count").textContent = `${people.length}명 · 일하는 중 ${people.filter(p => modeOf(p) === "working").length}`;
    list.replaceChildren(...people.map(d => {
      const li = el("li", "o-person");
      const [label, cls] = STATUS[modeOf(d)] || ["쉬는 중", "rest"];
      const head = el("div", "o-head");
      head.append(dot(OfficeLife.colorOf(d.name)), el("b", null, d.name), el("span", `o-chip ${cls}`, label));
      if (d.subCount) head.append(el("span", "o-chip sub", `꼬마 ${d.subCount}`));
      li.append(head);
      const what = modeOf(d) === "decision" ? OfficeLife.cut(d.ask || "결재 대기", 40)
        : modeOf(d) === "working" ? (d.doing || (d.subCount ? "꼬마들 일 맡겨 둠" : "일하는 중…")) : "";
      if (what) li.append(el("p", `o-doing ${cls}`, what));
      for (const s of (d.subs || []).slice(0, 3)) li.append(el("p", "o-sub", `🐣 ${s.desc || s.type || "꼬마"}${s.doing ? " — " + s.doing : ""}`));
      li.onclick = () => goTerminal(d.name, null);
      li.title = `${d.name} 터미널로 가기`;
      return li;
    }));
  }
  // 로그는 한꺼번에 쏟지 않고 0.45초마다 한 줄씩 흘려 넣는다 — 보고 있으면 계속 움직인다(라이브 느낌)
  function collectLog() {
    for (const d of st.data || []) {
      for (const e of d.log || []) {
        const ms = Date.parse(e.at);
        const key = `${d.name}|${e.at}|${e.text}`;
        if (!ms || st.logSeen.has(key)) continue;
        st.logSeen.add(key);
        if (st.primed) st.logQ.push({ at: ms, name: d.name, color: OfficeLife.colorOf(d.name), text: e.text });
        else addLog({ at: ms, name: d.name, color: OfficeLife.colorOf(d.name), text: e.text }, true);
      }
    }
    for (const e of OfficeLife.drainEvents()) st.logQ.push({ ...e, mine: true });
    st.logQ.sort((a, b) => a.at - b.at);
    st.primed = true;
  }
  function addLog(e, quiet) {
    const box = $("office-log");
    if (!box) return;
    const li = el("li", quiet ? "o-log" : "o-log fresh");
    li.append(el("time", null, hhmmss(e.at)), dot(e.color), el("b", null, e.name), el("span", e.mine ? "o-ev" : null, e.text));
    // 시간순 자리에 끼운다(맨 위가 최신) — 서버 기록은 조금 늦게 와도 제 시각 자리로
    const after = [...box.children].find(c => Number(c.dataset.at) <= e.at);
    li.dataset.at = e.at;
    box.insertBefore(li, after || null);
    while (box.children.length > 60) box.lastChild.remove();
  }
  function drip() {
    if (!st.on) return;
    const e = st.logQ.shift();
    if (e) addLog(e, false);
  }

  // ---------- 데이터 ----------
  async function load() {
    if (!st.on || document.hidden) return;
    try {
      const res = await fetch("/api/office");
      if (!res.ok) throw new Error(res.status);
      const { people } = await res.json();
      st.data = people;
      if (!st.failed.startsWith("사무실 그림")) st.failed = "";
      OfficeLife.sync(people);
      collectLog();
      renderPeople();
    } catch {
      st.failed = "맥에 연결하지 못했습니다 — 잠시 뒤 다시 읽습니다";
    }
    bar();
  }
  function bar() {
    const e = $("office-bar");
    if (!e) return;
    if (st.failed) { e.textContent = st.failed; return; }
    if (!st.data) { e.textContent = "사무실 불 켜는 중…"; return; }
    if (!st.data.length) { e.textContent = "켜진 프로젝트가 없습니다 — 왼쪽에서 프로젝트를 켜면 사무실로 출근합니다"; return; }
    const c = s => st.data.filter(p => modeOf(p) === s).length;
    const subs = st.data.reduce((n, p) => n + (p.subCount || 0), 0);
    e.textContent = `일하는 중 ${c("working")} · 결재 대기 ${c("decision")} · 쉬는 중 ${st.data.length - c("working") - c("decision")}`
      + (subs ? ` · 서브에이전트 ${subs}` : "") + " — 캐릭터를 누르면 그 터미널로 갑니다";
  }

  // ---------- 누르기 — 캐릭터 → 쏙 빨려 들어가며 그 프로젝트 터미널로 ----------
  function hit(ev) {
    const r = $("office-canvas").getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    const all = [...L.people.values(), ...L.minis.values()].filter(e => e.box).sort((a, b) => b.y - a.y);
    for (const e of all) {
      const [bx, by] = scr(e.box.x, e.box.y), w = e.box.w * st.view.s, h = e.box.h * st.view.s;
      if (x >= bx && x <= bx + w && y >= by && y <= by + h) return e.owner || e.name;
    }
    return null;
  }
  // 맥 독 최소화(지니)처럼 — 사무실 한 장을 가로 띠로 잘라, 띠마다 가운데로 좁아지며 빨려 들어간다.
  // 가운데에서 먼 띠일수록 늦게 출발해 깔때기 모양이 된다. 깜빡임 없애기: ①빨려 드는 동안 바탕을
  // 터미널 바탕색으로 서서히 바꾸고 오른쪽 패널도 같이 흐려, 끝나는 순간 밝음→어둠이 툭 바뀌지 않게 하고
  // ②다 빨려 든 다음에 터미널을 열어 가운데서 펼친다 ③덮개는 터미널의 첫 프레임이 그려진 뒤에 걷는다.
  function genie(done) {
    const src = $("office-canvas"), view = $("office-view");
    if (matchMedia("(prefers-reduced-motion: reduce)").matches || !src.width) { done(); return; }
    const stage = $("office-stage").getBoundingClientRect(), rect = view.getBoundingClientRect();
    const dpr = st.view.dpr;
    const snap = canvasOf(src.width, src.height);
    snap.getContext("2d").drawImage(src, 0, 0);
    const fx = canvasOf(Math.round(rect.width * dpr), Math.round(rect.height * dpr));
    fx.id = "office-fx";
    Object.assign(fx.style, { position: "fixed", left: rect.left + "px", top: rect.top + "px",
                              width: rect.width + "px", height: rect.height + "px", zIndex: 30, pointerEvents: "none" });
    document.body.append(fx);
    $("office-side").classList.add("o-fade");
    const dark = getComputedStyle(document.documentElement).getPropertyValue("--void").trim() || "#0B0D0C";
    const hex = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16));
    const [r0, g0, b0] = hex(BG), [r1, g1, b1] = /^#[0-9a-f]{6}$/i.test(dark) ? hex(dark) : [11, 13, 12];
    const ctx = fx.getContext("2d"), W = fx.width, H = fx.height;
    const ox = (stage.left - rect.left) * dpr, oy = (stage.top - rect.top) * dpr, sw = snap.width, sh = snap.height;
    const cx = ox + sw / 2, cy = oy + sh / 2, band = Math.max(4, Math.round(5 * dpr)), dur = 560;
    const ease = u => u * u * u;
    const t0 = performance.now();
    (function tick(now) {
      const u = Math.min(1, (now - t0) / dur), m = u * u * (3 - 2 * u);
      ctx.globalAlpha = 1;
      ctx.fillStyle = `rgb(${Math.round(r0 + (r1 - r0) * m)},${Math.round(g0 + (g1 - g0) * m)},${Math.round(b0 + (b1 - b0) * m)})`;
      ctx.fillRect(0, 0, W, H);
      const prog = y => ease(Math.max(0, Math.min(1, u * 1.45 - Math.abs(y - sh / 2) / (sh / 2) * 0.45)));
      const at = y => oy + y + (sh / 2 - y) * prog(y);
      for (let y = 0; y < sh; y += band) {
        const e = prog(y + band / 2), w = sw * (1 - e);
        if (w < 1) continue;
        const top = at(y), bottom = at(y + band);                      // 이웃 띠와 틈 없이 이어 붙인다
        ctx.globalAlpha = 1 - e * 0.7;
        ctx.drawImage(snap, 0, y, sw, band, cx - w / 2, top, w, Math.max(0.6, bottom - top + 0.6));
      }
      if (u < 1) { requestAnimationFrame(tick); return; }
      const tv = $("term-view");
      tv.classList.remove("genie-in");
      done();                                  // 다 빨려 들어갔다 — 이제 터미널을 열어 가운데서 펼친다
      void tv.offsetWidth;
      tv.classList.add("genie-in");
      $("office-side").classList.remove("o-fade");
      // 터미널 첫 프레임이 그려진 다음에 덮개를 천천히 걷는다 — 여기서 툭 걷으면 한 번 깜빡인다
      requestAnimationFrame(() => requestAnimationFrame(() => {
        fx.style.transition = "opacity .22s ease-out";
        fx.style.opacity = "0";
        setTimeout(() => fx.remove(), 260);
        setTimeout(() => tv.classList.remove("genie-in"), 600);
      }));
    })(t0);
  }
  function goTerminal(name) {
    const d = (st.data || []).find(p => p.name === name);
    const s = (typeof lastSessions !== "undefined" && lastSessions.find(x => x.name === name))
      || { name, alive: true, path: d?.path ?? null, host: d?.host ?? null };
    genie(() => {
      officeClose(false);
      showView("term");                       // 그 세션이 이미 다른 칸에 있으면 openTerminal 은 칸만 고른다
      openTerminal(s);
    });
  }

  // ---------- 켜고 끄기 ----------
  function remember(on) {
    try { localStorage.setItem("frog.office", on ? "1" : ""); } catch { /* 저장 못 해도 화면은 그대로 */ }
  }
  // 3D 모션 동안은 화면 전체를 흰 테마로 — 사무실 그림이 밝아 검은 목록과 안 어울린다.
  // 설정(frog.prefs)은 건드리지 않고 <html> 클래스만 잠깐 바꿨다가, 끄면 그대로 되돌린다.
  function lightOn() {
    const root = document.documentElement;
    if (st.theme === undefined) st.theme = root.className;
    root.classList.remove("onedark");
    root.classList.add("light");
  }
  function lightOff() {
    if (st.theme === undefined) return;
    document.documentElement.className = st.theme;
    st.theme = undefined;
  }
  function officeOpen() {
    if (st.on) return;
    st.on = true;
    remember(true);
    lightOn();
    const main = $("main");
    main.classList.add("office");
    main.classList.remove("idle");
    $("term-view").hidden = true;
    if (!isDesktop()) $("list-view").classList.add("offscreen");
    $("office-view").hidden = false;
    $("btn-office").classList.add("on");
    $("btn-office").setAttribute("aria-pressed", "true");
    loadImages();
    layout();
    bar();
    load();
    st.poll = setInterval(load, POLL_MS);
    st.drip = setInterval(drip, 450);
    st.last = 0;
    st.raf = requestAnimationFrame(frame);
  }
  // restore = 사무실이 비운 가운데를 원래대로(열린 터미널 또는 빈 화면). false = 부른 쪽이 화면을 정한다.
  function officeClose(restore = true) {
    if (!st.on) return;
    st.on = false;
    remember(false);
    lightOff();
    cancelAnimationFrame(st.raf);
    clearInterval(st.poll);
    clearInterval(st.drip);
    $("office-view").hidden = true;
    $("main").classList.remove("office");
    $("btn-office").classList.remove("on");
    $("btn-office").setAttribute("aria-pressed", "false");
    if (!restore) return;
    if (panes.some(p => p.session)) showView("term");
    else showList();
  }

  window.officeClose = officeClose;
  window.officeShown = () => st.on;
  window.officeBoot = () => {                  // 새로고침 뒤에도 켜 둔 채로 — 칸 되살리기(gridBoot) 다음에
    let on = false;
    try { on = localStorage.getItem("frog.office") === "1"; } catch { /* 기억 없음 = 끔 */ }
    if (on) officeOpen();
  };
  // 시험·점검용 — 지금 사무실에 누가 어디서 무엇을 하는지(화면 좌표 포함)
  window.officeDebug = () => [...L.people.values()].map(p => ({
    name: p.name, species: p.species, color: p.color, kid: !!p.kid, status: p.status,
    act: p.path.length ? "walk" : p.act?.work ? "desk" : p.act?.hop ? "ask" : (p.act?.pose || "idle"),
    pose: p.box ? poseOf(p, L.t) : null, bubble: p.bubble?.text || "",
    minis: [...L.minis.values()].filter(m => m.owner === p.name && (m.state === "wait" || m.state === "in")).length,
    at: p.box ? scr(p.x, p.box.top + p.box.h * 0.55) : null,
    minisAt: [...L.minis.values()].filter(m => m.owner === p.name && m.state === "wait" && m.box)
      .map(m => scr(m.x, m.box.top + m.box.h * 0.6)),
  }));

  window.officeDebug.scr = scr;                    // 테스트가 사무실 좌표 → 캔버스 좌표를 바꿀 때
  $("btn-office").onclick = () => (st.on ? officeClose() : officeOpen());
  const cv = $("office-canvas");
  cv.addEventListener("click", ev => { const name = hit(ev); if (name) goTerminal(name); });
  cv.addEventListener("mousemove", ev => { cv.style.cursor = hit(ev) ? "pointer" : "default"; });
  new ResizeObserver(() => { if (st.on) layout(); }).observe($("office-stage"));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
})();
