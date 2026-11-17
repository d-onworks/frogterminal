"use strict";
// 3D 모션 모드 — 사무실 사람들의 하루(걷기·할 일·수다·꼬마 출퇴근). 그리기는 office-view.js.
// 요청(2026-10-22): "심즈4처럼 지들끼리 조잘거리고, 탕비실에서 커피도 마시고" — 그래서 쉬는 사람은
// 할 일 목록(커피·소파·카페 탁자·서재·통화·기지개·서버 점검·회의·산책·수다)에서 무게를 두고 고르고,
// 일하는 사람은 자기 책상에서 타닥타닥하다 가끔 만세·머리 긁기를 한다. 지나가다 마주치면 손 흔들어 인사.
// 좌표·대사는 office-world.js.

const OfficeLife = (() => {
  const SPEED = 36, MINI_SPEED = 78;        // 걷는 속도(그림 픽셀/초) — 어른은 고른 걸음, 꼬마는 종종걸음
  const SIT_DESK = 18, SIT_SOFA = 16;       // 의자 등받이·방석 점 → 앉은 그림의 발끝까지
  const L = {
    t: 0, people: new Map(), minis: new Map(), desks: new Map(), taken: new Map(), chats: new Set(),
    greeted: new Map(), events: [], synced: false,
  };

  // ---------- 작은 도구 ----------
  const rnd = (a, b) => a + Math.random() * (b - a);
  const pick = arr => arr[Math.floor(Math.random() * arr.length)];
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  function hash(s) {                       // FNV-1a — 이름이 같으면 색·종이 늘 같다
    let h = 2166136261;
    for (const ch of s) h = Math.imul(h ^ ch.codePointAt(0), 16777619);
    return h >>> 0;
  }
  const colorOf = name => OFFICE_COLORS[hash(name) % OFFICE_COLORS.length];
  const speciesOf = name => (hash("종:" + name) % 2 ? "capy" : "frog");
  const cut = (text, n) => { const a = Array.from(text || ""); return a.length > n ? a.slice(0, n).join("") + "…" : a.join(""); };
  const shuffle = arr => arr.map(v => [Math.random(), v]).sort((a, b) => a[0] - b[0]).map(v => v[1]);
  function event(name, color, text) {
    L.events.push({ at: Date.now(), name, color, text });
    if (L.events.length > 80) L.events.splice(0, L.events.length - 80);
  }

  // ---------- 벽·바닥 — 선분이 벽을 가로지르는가, 점이 바닥 안인가 ----------
  const orient = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  function cross(p1, p2, q1, q2) {
    const d1 = orient(q1, q2, p1), d2 = orient(q1, q2, p2), d3 = orient(p1, p2, q1), d4 = orient(p1, p2, q2);
    return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0)) && d1 !== 0 && d2 !== 0;
  }
  const EDGES = [...OFFICE_WALLS, ...OFFICE_BLOCKS.flatMap(poly => poly.map((pt, i) => [pt, poly[(i + 1) % poly.length]]))];
  const blocked = (a, b) => EDGES.some(([q1, q2]) => cross(a, b, q1, q2));
  function inPoly([x, y], poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  const onFloor = pt => inPoly(pt, OFFICE_FLOOR) && !OFFICE_BLOCKS.some(poly => inPoly(pt, poly));
  // 한 걸음(a→b)이 괜찮은가 — 바닥 안이고 벽을 안 넘는다
  const canMove = (a, b) => onFloor(b) && !blocked(a, b);

  // ---------- 길 찾기 — 통로 점을 잇는 최단 경로 ----------
  const ADJ = {};
  for (const [a, b] of OFFICE_EDGES) {
    const d = dist(OFFICE_NODES[a], OFFICE_NODES[b]);
    (ADJ[a] ||= []).push([b, d]);
    (ADJ[b] ||= []).push([a, d]);
  }
  function nearest(pt) {                    // 벽 너머의 점은 아무리 가까워도 고르지 않는다
    let best = null, bd = Infinity, any = null, ad = Infinity;
    for (const [k, p] of Object.entries(OFFICE_NODES)) {
      const d = dist(p, pt);
      if (d < ad) { ad = d; any = k; }
      if (d < bd && !blocked(pt, p)) { bd = d; best = k; }
    }
    return best || any;
  }
  function nodePath(a, b) {
    const d = { [a]: 0 }, prev = {}, todo = new Set(Object.keys(OFFICE_NODES));
    while (todo.size) {
      let u = null;
      for (const k of todo) if (d[k] !== undefined && (u === null || d[k] < d[u])) u = k;
      if (u === null || u === b) break;
      todo.delete(u);
      for (const [v, w] of ADJ[u] || []) if (d[u] + w < (d[v] ?? Infinity)) { d[v] = d[u] + w; prev[v] = u; }
    }
    const out = [];
    for (let k = b; k; k = prev[k]) { out.unshift(OFFICE_NODES[k]); if (k === a) break; }
    return out;
  }
  function route(from, to, via) {
    if (dist(from, to) < 60 && !blocked(from, to)) return [to];
    const pts = nodePath(nearest(from), via || nearest(to));
    while (pts.length > 1 && dist(from, pts[1]) < dist(pts[0], pts[1]) && !blocked(from, pts[1])) pts.shift();
    return [...pts, to];
  }

  // ---------- 말풍선 ----------
  function say(p, text, dur = 2.8, kind = "talk") {
    if (text) p.bubble = { text: cut(text, 26), until: L.t + dur, kind };
  }

  // ---------- 할 일 = 단계 목록 ----------
  // 단계: {go:[x,y], via} 걷기 | {pose, dur, face, say, mix, hop, work, chat, sleepy, chair} 자세 취하기
  // chair = 앉은 의자 번호(office-atlas.js chairs: 책상 11 + 회의실 앞줄 3) — 화면이 꼬마를 그만큼 올려 앉힌다
  function run(p, steps) { p.steps = steps; p.si = -1; advance(p); }
  function advance(p) {
    p.si++;
    const s = p.steps?.[p.si];
    if (!s) { p.steps = null; p.act = null; return; }
    if (s.go) {
      // 의자에서 일어나면 앉으러 들어온 통로 점으로 먼저 물러난다 — 바로 다음 곳으로 가면 등받이 뒤·책상 위를
      // 가로질러 걷는다(2026-10-22 빨리 감기 실측)
      const from = [p.x, p.y], exit = p.exit;
      p.path = exit && dist(from, exit) < 160 && !blocked(from, exit) ? [exit, ...route(exit, s.go, s.via)] : route(from, s.go, s.via);
      p.exit = null; p.act = null; return;
    }
    p.path = [];
    p.act = s;
    p.exit = s.chair >= 0 && p.steps[p.si - 1]?.via ? OFFICE_NODES[p.steps[p.si - 1].via] : null;
    p.until = L.t + (s.dur ?? 3);
    p.arrivedAt = L.t;
    p.mixAt = L.t + rnd(2, 5);
    if (s.face) p.face = s.face;
    if (s.chat && !p.chat) p.until = L.t + 0.5;      // 걸어오는 사이 수다가 끝났다 — 서 있지 말고 다음 일로
    if (s.say) say(p, typeof s.say === "function" ? s.say() : s.say, s.sayDur || 2.8, s.sayKind || "talk");
  }
  // 첫 화면 — 걷지 않고 그 자리에 바로 놓는다
  function snap(p) {
    while (p.steps && p.path.length) {
      const to = p.path[p.path.length - 1];
      p.x = to[0]; p.y = to[1]; p.path = [];
      advance(p);
    }
  }

  // ---------- 자리 ----------
  function release(p) {
    for (const [k, who] of L.taken) if (who === p.name) L.taken.delete(k);
    if (modeOf(p) === "rest") L.desks.delete(p.name);      // 쉬는 사람은 책상을 붙잡지 않는다(일할 사람 몫)
  }
  // 그 자리에 누가 앉아 있거나 지금 그리로 가는 중인가 — 같은 의자에 둘이 겹쳐 앉지 않게
  const goalOf = q => (q.path.length ? q.path[q.path.length - 1] : [q.x, q.y]);
  // 예약(L.taken — 커피 다음에 앉을 카페 의자처럼 나중에 갈 자리)까지 본다. 수다 자리와 카페 의자는 좌표가
  // 같아서, 예약을 안 보면 커피 마시러 간 사이 수다가 그 의자를 잡아 둘이 한 의자에 앉았다(2026-10-22 실측).
  const spotAt = k => { const [kind, i] = k.split(":"); return OFFICE_SPOTS[kind]?.[+i]?.at; };
  const occupied = (pt, me) => [...L.people.values()].some(q => q !== me && !q.leaving && dist(goalOf(q), pt) < 34)
    || [...L.taken].some(([k, who]) => who !== me?.name && spotAt(k) && dist(spotAt(k), pt) < 34)
    // 회의실 의자를 일자리로 배정받은 사람(결재로 잠깐 일어난 사이에도 그 의자는 그 사람 것)
    || [...L.desks].some(([n, k]) => k < 0 && n !== me?.name && dist(sitAt(OFFICE_OVERFLOW[-1 - k].seat, SIT_DESK), pt) < 34);
  function takeSpot(p, kind) {
    const list = OFFICE_SPOTS[kind] || [];
    const free = list.map((s, i) => i).filter(i => !L.taken.has(`${kind}:${i}`) && !occupied(list[i].at, p));
    if (!free.length) return null;
    const i = pick(free);
    L.taken.set(`${kind}:${i}`, p.name);
    return list[i];
  }
  // 책상 고르기 — 순서대로 채우지 않고 흩어 앉힌다. 꼬마가 줄 서는 쪽(오른쪽 옆자리)은
  // 비워 둔다: 꼬마가 있는 사람의 오른쪽 자리는 남에게 안 주고, 꼬마가 있는 사람은 오른쪽이 빈 자리만 고른다.
  const rightOf = i => (i >= 0 && i + 1 < OFFICE_DESKS.length && OFFICE_DESKS[i + 1].row === OFFICE_DESKS[i].row ? i + 1 : -1);
  const leftOf = i => (i > 0 && OFFICE_DESKS[i - 1].row === OFFICE_DESKS[i].row ? i - 1 : -1);
  function deskOk(i, p) {
    const owner = new Map([...L.desks].map(([n, k]) => [k, n]));
    if (owner.has(i) && owner.get(i) !== p.name) return false;
    const left = owner.get(leftOf(i));                       // 왼쪽 사람의 꼬마 줄 자리면 안 된다
    if (left && left !== p.name && L.people.get(left)?.subCount > 0) return false;
    if (p.subCount > 0) {                                     // 내 꼬마 줄 자리(오른쪽)가 비어 있어야
      const right = owner.get(rightOf(i));
      if (right && right !== p.name) return false;
    }
    return true;
  }
  function deskOf(p) {
    const cur = L.desks.get(p.name);
    const lost = cur < 0 && occupied(sitAt(OFFICE_OVERFLOW[-1 - cur].seat, SIT_DESK), p);   // 비운 사이 누가 앉았다
    if (cur === undefined || (cur >= 0 && !deskOk(cur, p)) || lost) {
      const taken = [...L.desks].filter(([n]) => n !== p.name).map(([, k]) => k);
      const seats = taken.filter(k => k >= 0).map(k => OFFICE_DESKS[k].seat);
      const cands = OFFICE_DESKS.map((_, i) => i).filter(i => !taken.includes(i) && deskOk(i, p))
        .map(i => ({ i, far: Math.min(260, ...seats.map(q => dist(q, OFFICE_DESKS[i].seat)))
                             - 0.35 * dist([p.x, p.y], OFFICE_DESKS[i].seat) }))   // 남과 떨어지되 너무 멀리 걷지 않게
        .sort((a, b) => b.far - a.far);
      let i = cands.length ? pick(cands.slice(0, 3)).i : -1;  // 그중 좋은 자리 셋 중 하나
      if (i < 0) {                                              // 책상이 다 찼다 — 비어 있는 회의실 의자로
        const free = OFFICE_OVERFLOW.map((_, k) => k)
          .filter(k => !taken.includes(-1 - k) && !occupied(sitAt(OFFICE_OVERFLOW[k].seat, SIT_DESK), p));
        if (!free.length) { L.desks.delete(p.name); return null; }   // 회의실도 다 찼다 — 한 의자에 둘이 앉지 않는다
        i = -1 - free[0];
      }
      L.desks.set(p.name, i);
    }
    const i = L.desks.get(p.name);
    return i >= 0 ? OFFICE_DESKS[i] : OFFICE_OVERFLOW[-1 - i];
  }
  // 지금 해야 할 몫 — 결재 > 일(본체가 쉬어도 꼬마가 일하면 본체도 책상에서 일하는 중이다, 요청) > 쉼
  const busy = p => p.status === "working" || (p.status !== "decision" && p.subCount > 0);
  const modeOf = p => (p.status === "decision" ? "ask" : busy(p) ? "work" : "rest");
  const sitAt = (pt, dy) => [pt[0], pt[1] + dy];
  const chairOf = d => d.chair ?? OFFICE_DESKS.indexOf(d);     // 책상이면 책상 번호, 회의실 자리면 그 의자 번호

  // ---------- 무엇을 할까 ----------
  const homeOf = p => OFFICE_HOMES[p.name.toLowerCase()] || null;
  function choose(p) {
    leaveChat(p);
    release(p);
    if (p.leaving) return;
    const home = homeOf(p);
    if (home && modeOf(p) === "work") {        // 서버실 랙 앞에 서서 일한다 — 가끔 생각·통화·만세
      const w = home.work;
      return run(p, [{ go: w.at, via: w.via },
                     { pose: "walkB", dur: Infinity, face: w.face, work: true, fixed: true,
                       mix: [["think", 0.12, 1.6], ["phone", 0.08, 2.6], ["cheer", 0.05, 1.3]] }]);
    }
    if (home && modeOf(p) === "rest" && Math.random() < home.rest) {
      const w = home.work;
      return run(p, [{ go: w.at, via: w.via },
                     { pose: pick(["walkB", "think", "phone", "phScroll"]), dur: rnd(14, 28), face: w.face, fixed: true,
                       say: Math.random() < 0.4 ? () => pick(["🖥️ 랙 불빛 이상 무", "서버 온도 좋고", "백업 확인"]) : null,
                       mix: [["think", 0.15, 1.6]] }]);
    }
    if (modeOf(p) === "work") {
      const d = deskOf(p);
      if (!d) {                                  // 앉을 데가 없다 — 빈 산책 자리에서 서서 일한다(등을 보이고)
        const s = takeSpot(p, "stroll");
        return run(p, [...(s ? [{ go: s.at, via: s.via }] : []),
                       { pose: "walkB", dur: Infinity, work: true, mix: [["think", 0.12, 1.6], ["cheer", 0.05, 1.3]] }]);
      }
      return run(p, [{ go: sitAt(d.seat, SIT_DESK), via: d.via },
                     { pose: "sitBack", dur: Infinity, work: true, chair: chairOf(d),
                       mix: [["cheer", 0.06, 1.3], ["scratch", 0.09, 1.6]] }]);
    }
    if (p.status === "decision" && home) {       // 서버실 사람은 결재도 서버실에서 폴짝
      return run(p, [{ go: home.work.at, via: home.work.via },
                     { pose: "stand", dur: Infinity, hop: true, mix: [["wave", 0.25, 1.1]] }]);
    }
    if (p.status === "decision") {               // 의자에서 일어나 책상 앞 통로에서 폴짝 — 머리 글씨가 안 묻히게
      const d = deskOf(p);
      if (!d) return run(p, [{ pose: "stand", dur: Infinity, hop: true, mix: [["wave", 0.25, 1.1]] }]);
      return run(p, [{ go: [d.seat[0] + 10, d.seat[1] + SIT_DESK + 40], via: d.via },
                     { pose: "stand", dur: Infinity, hop: true, mix: [["wave", 0.25, 1.1]] }]);
    }
    rest(p);
  }

  // 쉬는 사람 규칙: 기본은 앉아 있기(소파·탕비실·회의실·서재·빈 책상). 서서 돌아다니는
  // 사람은 한꺼번에 3명까지 — 이미 2명이 돌아다니면 나머지는 앉을 일만 고른다. 앉았다 일어났다는 무작위로.
  const SIT = [["sofa", 14], ["cafe", 12], ["chatSit", 14], ["meeting", 9], ["read", 7], ["deskrest", 9]];
  const MOVE = [["coffee", 12], ["chat", 8], ["phone", 5], ["stretch", 3], ["server", 3], ["stroll", 6]];
  const ROAM_MAX = 3;
  // 휴대폰 만지작(요청 2026-10-22: "쉴 때 모습이 하나뿐 — 앉아서·서서 가끔 휴대폰 보게"). 앉은 자리는
  // 처음부터 휴대폰을 보는 경우도 있고(PHONE_START), 앉아 있다가 가끔 꺼내 보기도 한다(섞기 표).
  const PHONE_START = 0.4;
  const PHONE_SIT = [["sitScroll", 0.22, 6], ["sitText", 0.1, 3.5], ["sitLol", 0.05, 1.8]];
  const PHONE_STAND = [["phScroll", 0.3, 5], ["phText", 0.12, 3], ["phLol", 0.05, 1.6]];
  const sofaPose = () => (Math.random() < PHONE_START ? "sitScroll" : "sitFront");
  // 이미 휴대폰을 보고 앉은 사람은 섞기에서 가끔 고개를 든다(sitFront), 아니면 가끔 휴대폰을 꺼낸다
  const sofaMix = (pose, extra) => (pose === "sitScroll"
    ? [["sitText", 0.14, 3.5], ["sitLol", 0.08, 1.8], ["sitFront", 0.1, 3], ...extra]
    : [...PHONE_SIT, ...extra]);
  const deskRestMix = pose => (pose === "sitBackPhone"
    ? [["sitBack", 0.12, 3], ["scratch", 0.06, 1.4]]
    : [["sitBackPhone", 0.28, 6], ["scratch", 0.08, 1.4]]);
  const roaming = except => [...L.people.values()]
    .filter(q => q !== except && !q.leaving && modeOf(q) === "rest" && !q.seated).length;
  function rest(p) {
    const pool = roaming(p) >= ROAM_MAX - 1 || Math.random() < 0.7 ? SIT : MOVE;
    const total = pool.reduce((n, r) => n + r[1], 0);
    for (let tries = 0; tries < 5; tries++) {
      let r = Math.random() * total, kind = pool[0][0];
      for (const [k, w] of pool) { if ((r -= w) < 0) { kind = k; break; } }
      p.seated = SIT.some(([k]) => k === kind);
      if (ACTS[kind](p)) return;
    }
    p.seated = true;                          // 앉을 데가 다 찼다 — 빈 책상, 그것도 없으면 그 자리에서 쉰다
    if (ACTS.deskrest(p)) return;
    run(p, [{ pose: "stand", dur: rnd(6, 12), mix: PHONE_STAND }]);
  }
  const ACTS = {
    chat: p => roaming(p) <= 1 && startChat(p, false),     // 서서 떠드는 건 둘이서, 돌아다니는 사람이 적을 때만
    chatSit: p => startChat(p, true),
    deskrest(p) {                            // 빈 책상에 앉아 쉬기 — 일하는 사람 자리는 안 뺏는다
      const free = OFFICE_DESKS.length - L.desks.size;
      if (!L.desks.has(p.name) && free < 3) return false;
      const d = deskOf(p);
      if (!d) return false;
      const pose = Math.random() < PHONE_START ? "sitBackPhone" : "sitBack";
      run(p, [{ go: sitAt(d.seat, SIT_DESK), via: d.via },
              { pose, dur: rnd(18, 36), chair: chairOf(d), mix: deskRestMix(pose) }]);
      return true;
    },
    coffee(p) {
      const s = takeSpot(p, "coffee");
      if (!s) return false;
      const steps = [{ go: s.at, via: s.via }, { pose: "walkB", dur: 2.4, face: s.face, say: "☕ 커피 내리는 중" },
                     { pose: "sip", dur: 1.6, say: () => pick(["캬~", "아 좋다", "☕ 후루룩"]) }, { pose: "mug", dur: rnd(2, 4) },
                     { pose: "sip", dur: 1.6 }, { pose: "mug", dur: rnd(1.5, 3) }];
      const seat = Math.random() < 0.5 && takeSpot(p, "cafe");
      const sp = sofaPose();
      if (seat) steps.push({ go: sitAt(seat.at, SIT_SOFA), via: seat.via },
                           { pose: sp, dur: rnd(8, 14), face: seat.face, mix: sofaMix(sp, [["sofaTalk", 0.12, 1.6]]) });
      run(p, steps);
      return true;
    },
    sofa(p) {
      const s = takeSpot(p, "sofa");
      if (!s) return false;
      const pose = sofaPose();
      run(p, [{ go: sitAt(s.at, SIT_SOFA), via: s.via },
              { pose, dur: rnd(20, 42), face: s.face, sleepy: pose === "sitFront",
                mix: sofaMix(pose, [["sofaTalk", 0.1, 1.6]]) }]);
      return true;
    },
    cafe(p) {
      const s = takeSpot(p, "cafe");
      if (!s) return false;
      const pose = sofaPose();
      run(p, [{ go: sitAt(s.at, SIT_SOFA), via: s.via },
              { pose, dur: rnd(16, 32), face: s.face, say: () => pick(OFFICE_THINK), sayKind: "think",
                mix: sofaMix(pose, [["sofaTalk", 0.08, 1.4]]) }]);
      return true;
    },
    phone(p) {
      const s = takeSpot(p, "stroll");
      if (!s) return false;
      const face = pick([1, -1]);
      if (Math.random() < 0.4) {               // 통화
        run(p, [{ go: s.at, via: s.via }, { pose: "phone", dur: rnd(3, 4), face, say: () => pick(OFFICE_PHONE) },
                { pose: "phone", dur: rnd(3, 4), say: () => pick(OFFICE_PHONE) }, { pose: "laugh", dur: 1.2 }]);
      } else {                                 // 서서 휴대폰 만지작 — 스크롤하다 답장하다 킥킥
        run(p, [{ go: s.at, via: s.via },
                { pose: "phScroll", dur: rnd(4, 7), face, say: Math.random() < 0.5 ? () => pick(OFFICE_SCROLL) : null, sayKind: "think" },
                { pose: "phText", dur: rnd(2.5, 4), say: Math.random() < 0.5 ? () => pick(OFFICE_TEXTING) : null, sayKind: "think" },
                { pose: "phLol", dur: 1.6, say: () => pick(OFFICE_LOL) }, { pose: "phScroll", dur: rnd(3, 6), mix: PHONE_STAND }]);
      }
      return true;
    },
    read(p) {
      const arm = takeSpot(p, "armchair");
      if (arm) {
        run(p, [{ go: sitAt(arm.at, SIT_SOFA), via: arm.via },
                { pose: "sitFront", dur: rnd(20, 38), face: arm.face, say: "📚 읽는 중", sayKind: "think", sleepy: true }]);
        return true;
      }
      return false;                           // 서재는 안락의자 둘뿐 — 서서 읽는 자리는 붐벼서 뺐다
    },
    stretch(p) {
      run(p, [{ pose: "stretch", dur: 2.2, say: "으아아~" }, { pose: "stand", dur: 1.2 }]);
      return true;
    },
    server(p) {
      const s = takeSpot(p, "server");
      if (!s) return false;
      run(p, [{ go: s.at, via: s.via }, { pose: "walkB", dur: 4, face: s.face, say: "🖥️ 랙 불빛 이상 무" },
              { pose: "think", dur: 1.6 }]);
      return true;
    },
    meeting(p) {
      const board = Math.random() < 0.4 && takeSpot(p, "board");
      if (board) {
        run(p, [{ go: board.at, via: board.via },
                { pose: "talk", dur: 5, face: -1, say: "자, 회의 시작합니다" }, { pose: "think", dur: 2 }]);
        return true;
      }
      const s = takeSpot(p, "meeting");
      if (!s) return false;
      run(p, [{ go: sitAt(s.at, SIT_DESK), via: s.via },
              { pose: "sitBack", dur: rnd(18, 34), chair: s.chair, mix: [["sitBackPhone", 0.25, 6], ["scratch", 0.1, 1.4]] }]);
      return true;
    },
    stroll(p) {
      const s = takeSpot(p, "stroll");
      const n = OFFICE_NODES[pick(Object.keys(OFFICE_NODES).filter(k => !["out", "door"].includes(k)))];
      const at = s ? s.at : [n[0] + rnd(-10, 10), n[1] + rnd(-6, 6)];
      const look = Math.random() < 0.35;      // 멈춰 서서 휴대폰 확인
      run(p, [{ go: at, via: s?.via },
              { pose: look ? "phScroll" : "stand", dur: look ? rnd(4, 7) : rnd(2, 4), face: pick([1, -1]),
                say: look && Math.random() < 0.4 ? () => pick(OFFICE_SCROLL) : null, sayKind: "think" },
              { pose: "think", dur: 1.6, say: Math.random() < 0.4 ? () => pick(OFFICE_THINK) : null, sayKind: "think" }]);
      return true;
    },
  };

  // ---------- 수다 — 두세 명이 둘러서서 번갈아 말한다 ----------
  function free(q) {
    return !q.leaving && !q.chat && modeOf(q) === "rest";
  }
  function startChat(p, sit) {
    const busy = new Set([...L.chats].map(c => c.spot));
    const spots = OFFICE_CHATS.filter(c => !busy.has(c) && !!c.sit === sit
      && !c.pts.some(pt => occupied(pt, p)));
    const mates = shuffle([...L.people.values()].filter(q => q !== p && free(q)));
    if (!spots.length || !mates.length) return false;
    const spot = pick(spots);
    const n = sit ? Math.min(spot.pts.length, 1 + mates.length) : 2;       // 서서 떠드는 건 둘까지
    const members = [p, ...mates.slice(0, n - 1)];
    const chat = { spot, members, start: 0, next: 0, said: 0, last: null, born: L.t,
                   lines: 4 + Math.floor(Math.random() * 4) + n };
    L.chats.add(chat);
    members.forEach((q, k) => {
      if (q !== p) { leaveChat(q); release(q); }
      q.chat = chat;
      q.seated = !!spot.sit;
      const at = spot.sit ? sitAt(spot.pts[k], SIT_SOFA) : spot.pts[k];
      run(q, [{ go: at, via: spot.vias?.[k] || spot.via }, { pose: spot.sit ? "sitFront" : "stand", dur: Infinity, chat: true }]);
    });
    return true;
  }
  function leaveChat(p) {
    const c = p.chat;
    if (!c) return;
    p.chat = null;
    c.members = c.members.filter(q => q !== p);
    if (c.members.length < 2) endChat(c, false);
  }
  function endChat(c, bye) {
    L.chats.delete(c);
    for (const q of c.members) {
      q.chat = null;
      if (bye && Math.random() < 0.7) { q.over = { pose: "wave", until: L.t + 1 }; say(q, pick(["또 봐~", "고고!", "ㅂㅂ", "나중에 봐"]), 1.4); }
      if (q.act?.chat) q.until = L.t + (bye ? 1.1 : 0);
    }
  }
  function chatTick(c) {
    const here = c.members.filter(q => !q.path.length && q.act?.chat);
    if (!c.start) {
      if (here.length === c.members.length || (L.t - c.born > 22 && here.length >= 2)) { c.start = L.t; c.next = L.t + 0.4; }
      else if (L.t - c.born > 40) return endChat(c, false);
      return;
    }
    if (L.t < c.next) return;
    if (c.said >= c.lines || here.length < 2) return endChat(c, true);
    const speaker = pick(here.filter(q => q !== c.last)) || here[0];
    const line = pick(OFFICE_TALK);
    say(speaker, line, 2.3);
    speaker.over = { pose: c.spot.sit ? "sofaTalk" : (Math.random() < 0.75 ? "talk" : "wave"), until: L.t + 2.2 };
    const cx = here.reduce((n, q) => n + q.x, 0) / here.length;
    speaker.face = speaker.x < cx ? 1 : -1;
    const funny = /ㅋ|대박|천재|!/.test(line);
    for (const q of here) {
      if (q === speaker) continue;
      q.face = q.x < speaker.x ? 1 : -1;
      const r = Math.random();
      if (!c.spot.sit && (funny ? r < 0.65 : r < 0.25)) q.over = { pose: "laugh", until: L.t + rnd(1.2, 2) };
      else if (!c.spot.sit && r < 0.45) q.over = { pose: "think", until: L.t + rnd(1, 1.8) };
    }
    c.last = speaker;
    c.said++;
    c.next = L.t + rnd(2.3, 3);
  }

  // ---------- 서브에이전트 꼬마 ----------
  function slotOf(m) {
    const p = L.people.get(m.owner);
    if (!p) return [m.x, m.y];
    const line = [...L.minis.values()].filter(q => q.owner === m.owner && q.state !== "out" && q.state !== "bye");
    const k = Math.max(0, line.indexOf(m));
    const row = Math.floor(k / 6), col = k % 6, dir = homeOf(p)?.queue || 1;
    // 회의실 의자에서 일하면 옆이 의자 줄이다 — 의자 위가 아니라 앞 통로 쪽으로 비스듬히 선다
    if (!p.path.length && p.act?.chair >= OFFICE_DESKS.length)
      return [p.x + 20 + col * 26 - row * 14, p.y + 34 + col * 15 + row * 22];
    return [p.x + dir * (46 + col * 27 - row * 12), p.y + 20 + col * 4 + row * 26];
  }
  function miniLeave(m, delay) {
    if (m.state === "bye" || m.state === "out") return;
    m.state = "bye";
    m.byeAt = L.t + delay;
  }

  // ---------- 데이터가 오면 들이고 내보낸다 ----------
  function sync(list) {
    const seen = new Set();
    for (const d of list) {
      seen.add(d.name);
      let p = L.people.get(d.name);
      const fresh = !p;
      if (fresh) {
        p = { name: d.name, species: speciesOf(d.name), color: colorOf(d.name), x: 0, y: 0, path: [], steps: null,
              act: null, face: pick([1, -1]), phase: rnd(0, 6.28), alpha: 1, over: null, bubble: null,
              nextSay: L.t + rnd(3, 8), nextPart: 0 };
        L.people.set(d.name, p);
      }
      const was = p.status, wasDoing = p.doing, back = p.leaving, wasMode = fresh ? null : modeOf(p);
      const wasDesk = L.desks.get(p.name);
      Object.assign(p, { agent: d.agent, status: d.status, ask: d.ask || "", kid: !!d.kid, doing: d.doing || "",
                         subCount: d.subCount || 0, subs: d.subs || [], log: d.log || [], commits: d.commits,
                         path0: d.path, host: d.host });
      if (fresh) {
        if (L.synced) {                              // 나중에 켠 프로젝트 = 문으로 출근
          p.x = OFFICE_NODES.out[0]; p.y = OFFICE_NODES.out[1]; p.alpha = 0;
          event(p.name, p.color, "출근했습니다");
          choose(p);
        } else {                                     // 첫 화면 — 통로 점 하나에 세워 두고 할 일 자리로 바로 놓는다.
          // (0,0)에서 시작하면 걷기 없는 첫 할 일(기지개·빈자리 없음)일 때 그림 왼쪽 위 바깥에 서 있다가
          // 거기서 벽을 넘어 걸어 들어왔다(2026-10-22 60분 빨리 감기 실측)
          [p.x, p.y] = OFFICE_NODES[pick(START_NODES)];
          choose(p); snap(p);
        }
      } else if (back) {
        p.leaving = false; p.gone = false; p.alpha = 1;
        choose(p);
      } else if (was !== p.status || wasMode !== modeOf(p)) {
        if (p.status === "working" && was !== "working") event(p.name, p.color, "일을 시작했습니다");
        else if (p.status === "decision" && was !== "decision") event(p.name, p.color, `결재를 올렸습니다 — ${cut(p.ask || "확인 필요", 24)}`);
        else if (modeOf(p) === "work" && wasMode !== "work") event(p.name, p.color, "꼬마들과 일하는 중");
        else if (wasMode === "work" && modeOf(p) === "rest") { event(p.name, p.color, "일을 마쳤습니다"); p.over = { pose: "cheer", until: L.t + 1.4 }; }
        choose(p);
      } else if (modeOf(p) === "work" && wasDesk >= 0 && !deskOk(wasDesk, p)) {
        choose(p);                                 // 꼬마가 생겼는데 옆자리가 막혔다 — 넓은 자리로 옮겨 앉는다
      }
      if (modeOf(p) === "work" && p.doing && p.doing !== wasDoing) { say(p, p.doing, 4, "work"); p.nextSay = L.t + rnd(6, 10); }
      syncMinis(p, d);
    }
    for (const p of L.people.values()) {
      if (seen.has(p.name) || p.leaving) continue;
      p.leaving = true;
      leaveChat(p);
      release(p);
      L.desks.delete(p.name);
      event(p.name, p.color, "퇴근했습니다");
      run(p, [{ go: OFFICE_NODES.out }]);
      for (const m of L.minis.values()) if (m.owner === p.name) miniLeave(m, 0);
    }
    L.synced = true;
  }

  function syncMinis(p, d) {
    const ids = new Set((d.subs || []).map(s => s.id));
    let k = 0;
    for (const m of L.minis.values()) {
      if (m.owner !== p.name || m.state === "bye" || m.state === "out") continue;
      if (!ids.has(m.id)) { miniLeave(m, k++ * 1.1); event(p.name, p.color, `꼬마 퇴근 — ${cut(m.desc || m.type || "일 끝", 22)}`); }
    }
    for (const s of d.subs || []) {
      const old = L.minis.get(s.id);
      if (old) { if (s.doing && s.doing !== old.doing) { old.doing = s.doing; old.nextSay = Math.min(old.nextSay, L.t + 1); } continue; }
      const m = { id: s.id, owner: p.name, type: s.type, desc: s.desc, doing: s.doing || "", x: 0, y: 0, path: [],
                  face: pick([1, -1]), phase: rnd(0, 6.28), alpha: 1, over: null, bubble: null, state: "wait",
                  flipAt: L.t + rnd(1, 3), hopAt: L.t + rnd(2, 5), nextSay: L.t + rnd(4, 10) };
      L.minis.set(s.id, m);
      if (L.synced) {                                // 새 꼬마 = 문으로 출근해서 줄에 선다
        m.x = OFFICE_NODES.out[0]; m.y = OFFICE_NODES.out[1]; m.alpha = 0; m.state = "in";
        m.path = route([m.x, m.y], [p.x, p.y]).slice(0, -1);
        event(p.name, p.color, `꼬마 출근 — ${cut(s.desc || s.type || "일 받음", 22)}`);
      } else { [m.x, m.y] = slotOf(m); }
    }
    p.extra = Math.max(0, (d.subCount || 0) - (d.subs || []).length);
  }

  // ---------- 한 프레임 ----------
  // 막힘 풀기(2026-10-22 20분 빨리 감기 실측: 끼임 238건) — 통로 점 바로 옆에 앉은 사람·결재로 폴짝 뛰는 사람은
  // 안 밀린다(무게 0). 그러면 그 점을 지나야 하는 사람이 밀려났다 다시 다가가기를 끝없이 되풀이했다. 그래서
  // 같은 점에 1초 넘게 못 다가가면: 다음 점이 바로 보이면 이 점은 건너뛰고, 아니면 옆으로 비켜 도는 점을
  // 하나 끼운다(좌우 번갈아). 마지막 점(앉을 자리)인데 6초 넘게 못 가면 그 자리로 옮겨 놓는다.
  const UNSTICK_S = 1, UNSTICK_JUMP = 6, START_NODES = ["inner", "la1", "B1", "B2", "C1", "C2", "A2", "A3", "ra2", "ra3"];
  // 지름길이 남의 의자 곁(앉는 점 22픽셀 안)을 지나는가 — 의자는 벽이 아니라서 따로 본다(목적지 의자는 빼고)
  const SEATS = (OFFICE_ATLAS.chairs || []).map(c => [c.seat[0], c.seat[1] + SIT_DESK]);
  function segDist(pt, a, b) {
    const vx = b[0] - a[0], vy = b[1] - a[1], l2 = vx * vx + vy * vy || 1;
    const k = Math.max(0, Math.min(1, ((pt[0] - a[0]) * vx + (pt[1] - a[1]) * vy) / l2));
    return Math.hypot(pt[0] - a[0] - k * vx, pt[1] - a[1] - k * vy);
  }
  // 출발점 바로 곁(30픽셀 안)의 의자는 뺀다 — 방금 일어난 자기 의자에서 멀어지는 걸음까지 막으면 못 움직인다
  const nearChair = (a, b, goal) => SEATS.some(q => dist(q, goal) > 4 && dist(q, a) > 30 && segDist(q, a, b) < 22);
  function unstick(e, d) {
    const to = e.path[0], g = e.prog;
    if (!g || g.to !== to) { e.prog = { to, best: d, t: L.t, side: g?.side || 1, stuck: g?.stuck || 0 }; return; }
    if (d < g.best - 2) { g.best = d; g.t = L.t; g.stuck = 0; return; }
    if (L.t - g.t < UNSTICK_S) return;
    g.t = L.t; g.best = d; g.stuck++;
    const here = [e.x, e.y];
    const goal = e.path[e.path.length - 1];
    // 남의 의자 곁을 지나는 지름길은 3초 넘게 막혔을 때만(등받이 뒤로 걸어 다니지 않게)
    if (e.path.length > 1 && !blocked(here, e.path[1]) && (g.stuck >= 3 || !nearChair(here, e.path[1], goal))) { e.path.shift(); return; }
    if (g.stuck >= UNSTICK_JUMP && (d < 90 || g.stuck >= 10) && onFloor(to) && !blocked(here, to)) { e.x = to[0]; e.y = to[1]; return; }
    const vx = to[0] - e.x, vy = to[1] - e.y, vl = Math.hypot(vx, vy) || 1;
    for (const s of [g.side, -g.side]) {
      const side = [e.x - vy / vl * 26 * s + vx / vl * 8, e.y + vx / vl * 26 * s + vy / vl * 8];
      if (canMove(here, side) && !blocked(side, to) && !nearChair(here, side, goal) && !nearChair(side, to, goal)) {
        e.path.unshift(side); g.side = -s; return;
      }
    }
  }
  function step(e, speed, dt, rate) {
    if (!e.path.length) return false;
    if (rate) e.wp = (e.wp || 0) + dt * rate;             // 꼬마 걸음 위상 — π 마다 한 걸음(그리기가 이걸로 다리를 바꾼다)
    if (blocked([e.x, e.y], e.path[0])) {
      const goal = e.path[e.path.length - 1];
      e.path = route([e.x, e.y], goal);
      if (blocked([e.x, e.y], e.path[0])) e.path.unshift(OFFICE_NODES[nearest([e.x, e.y])]);
    }
    unstick(e, dist([e.x, e.y], e.path[0]));
    const [tx, ty] = e.path[0];
    const dx = tx - e.x, dy = ty - e.y, d = Math.hypot(dx, dy);
    const mv = rate ? speed * dt * (0.7 + 0.6 * Math.abs(Math.sin(e.wp))) : speed * dt;   // 어른은 고르게(저벅저벅)
    e.vx = dx; e.vy = dy;
    e.dist = (e.dist || 0) + Math.min(d, mv);            // 걸은 거리 — 어른 걷기 그림은 이걸로 장을 넘긴다
    if (d <= mv) { e.x = tx; e.y = ty; e.path.shift(); return !e.path.length; }
    e.x += dx / d * mv; e.y += dy / d * mv;
    return false;
  }

  function update(dt) {
    L.t += dt;
    const t = L.t;
    for (const p of [...L.people.values()]) {
      if (p.alpha < 1 && !p.gone) p.alpha = Math.min(1, p.alpha + dt * 2);
      const paused = p.over && t < p.over.until && p.over.stop;
      if (p.path.length && !paused) {
        if (step(p, SPEED, dt, 0)) { if (p.leaving) p.gone = true; else advance(p); }
      } else if (!p.path.length && p.act && t > p.until) advance(p);
      else if (!p.path.length && !p.act && !p.leaving) choose(p);
      if (p.gone) { p.alpha -= dt * 2.5; if (p.alpha <= 0) { L.people.delete(p.name); continue; } }
      if (p.over && t > p.over.until) p.over = null;
      // 자세 섞기 — 같은 자리에서도 가끔 다른 몸짓(만세·머리 긁기·손 흔들기·수다 손짓)
      if (p.act?.mix && t > p.mixAt && !p.over) {
        p.mixAt = t + rnd(3, 7);
        for (const [pose, prob, dur] of p.act.mix) {
          if (Math.random() < prob) {
            p.over = { pose, until: t + dur };
            if (pose === "cheer") say(p, pick(["됐다!", "통과!", "오예~"]), 1.4);
            if (pose === "sitLol" || pose === "phLol") say(p, pick(OFFICE_LOL), 1.6);
            else if (/Scroll|BackPhone/.test(pose) && Math.random() < 0.3) say(p, pick(OFFICE_SCROLL), 2.2, "think");
            else if (/Text$/.test(pose) && Math.random() < 0.35) say(p, pick(OFFICE_TEXTING), 2, "think");
            if (pose === "wave" && p.status === "decision") say(p, "저기요~!", 1.2);
            break;
          }
        }
      }
      // 말풍선 — 일하는 사람은 실제 하는 일, 쉬는 사람은 혼잣말
      if (t > p.nextSay && !p.chat) {
        if (p.act?.work) { say(p, p.doing || pick(OFFICE_WORK), 3.6, "work"); p.nextSay = t + rnd(6, 11); }
        else if (!p.act?.hop) { if (Math.random() < 0.5) say(p, pick(OFFICE_THINK), 2.2, "think"); p.nextSay = t + rnd(10, 22); }
        else p.nextSay = t + 5;
      }
      if (p.bubble && t > p.bubble.until) p.bubble = null;
    }
    for (const c of [...L.chats]) chatTick(c);
    greet(t);
    separate(dt);
    for (const m of [...L.minis.values()]) {
      const owner = L.people.get(m.owner);
      if (m.state === "in" && !m.path.length) {
        m.state = "wait";
        say(m, pick(["출근!", "넵!", "왔습니다"]), 1.4);
      }
      if (m.state === "bye" && t >= m.byeAt && !m.byeSaid) {
        m.byeSaid = true;
        m.byeEnd = t + 1.3;
        m.over = { pose: "mBye", until: m.byeEnd };
        say(m, "퇴근합니다! 👋", 1.6);
      }
      if (m.state === "bye" && m.byeSaid && t > m.byeEnd) {
        m.state = "out";
        m.path = route([m.x, m.y], OFFICE_NODES.out);
      }
      if (m.path.length) {
        if (step(m, MINI_SPEED, dt, 11) && m.state === "out") m.gone = true;
      } else if (m.state === "wait" && owner) {
        const [sx, sy] = slotOf(m);
        if (Math.hypot(sx - m.x, sy - m.y) > 2 && onFloor([sx, sy]))
          m.path = blocked([m.x, m.y], [sx, sy]) ? route([m.x, m.y], [sx, sy]) : [[sx, sy]];
        if (t > m.flipAt) { m.flipGap = rnd(1, 3); m.flipAt = t + m.flipGap; m.face = -m.face; m.flipT = t; }
        if (t > m.hopAt) {
          m.hopAt = t + rnd(2, 5);
          m.over = Math.random() < 0.35 ? { pose: "mSide", until: t + 1.2 } : { pose: "mJump", until: t + 0.6 };
        }
        if (t > m.nextSay) {
          m.nextSay = t + rnd(8, 16);
          if (Math.random() < 0.6) say(m, m.doing || pick(["넵!", "확인 중…", "보고드립니다", "뭐 하지?"]), 2.4, m.doing ? "work" : "talk");
        }
      } else if (!owner && m.state === "wait") miniLeave(m, 0);
      if (m.alpha < 1 && !m.gone) m.alpha = Math.min(1, m.alpha + dt * 2);
      if (m.gone) { m.alpha -= dt * 2.5; if (m.alpha <= 0) L.minis.delete(m.id); }
      if (m.over && t > m.over.until) m.over = null;
      if (m.bubble && t > m.bubble.until) m.bubble = null;
    }
  }

  // ---------- 안 겹치기 — 발밑 타원끼리 밀어낸다(요청: "물리 간섭 적용해서 무조건 안 겹치게") ----------
  // 아이소 바닥이라 가로로 넓고 세로로 얇은 타원. 앉은 사람·일하는 사람·결재 대기는 자리에 박혀 있어야
  // 가구와 맞으므로 고정(밀리지 않는다), 서 있는 사람은 조금, 걷는 사람·꼬마는 많이 밀린다. 걷는 사람이
  // 고정된 사람에 막히면 정면으로 밀려 멈추지 않게 진행 방향의 옆으로 비켜 돌아간다.
  const STAND_PX = 96;
  function sizeOf(e) {
    if (e.owner) return STAND_PX * 0.5 * (L.people.get(e.owner)?.kid ? 0.7 : 1);
    return STAND_PX * (e.kid ? 0.7 : 1);
  }
  function weightOf(e) {
    if (e.gone || e.alpha < 0.2) return -1;                       // 사라지는 중 — 부딪히지 않는다
    if (e.owner) return e.state === "wait" && !e.path.length ? 0.6 : 1;
    if (e.path.length) return 1;
    const a = e.act;
    if (!a || a.fixed || a.work || a.hop || /^sit/.test(a.pose)) return 0;
    return 0.5;
  }
  const seating = (e, sitter) => /^sit/.test(sitter.act?.pose || "") && e.path.length && !e.owner
    && /^sit/.test(e.steps?.[e.si + 1]?.pose || "") && dist([e.x, e.y], e.path[e.path.length - 1]) < 48;
  function separate(dt) {
    const ents = [...L.people.values(), ...L.minis.values()];
    const rate = Math.min(1, dt * 10);
    for (let i = 0; i < ents.length; i++) {
      const a = ents[i], wa = weightOf(a);
      if (wa < 0) continue;
      for (let j = i + 1; j < ents.length; j++) {
        const b = ents[j], wb = weightOf(b);
        if (wb < 0 || (wa === 0 && wb === 0)) continue;
        if (a.owner && b.owner && a.owner === b.owner) continue;   // 같은 줄 꼬마끼리는 줄 간격이 정한다
        if ((a.owner === b.name && !a.path.length) || (b.owner === a.name && !b.path.length)) continue;
        // 앉은 사람은 옆자리에 앉으러 들어오는 마지막 걸음을 밀어내지 않는다 — 회의실 의자는 46픽셀 간격이라
        // 양옆이 앉아 있으면 가운데 의자에 영영 못 다가갔다(2026-10-22 실측)
        if ((wa === 0 && seating(b, a)) || (wb === 0 && seating(a, b))) continue;
        const sa = sizeOf(a), sb = sizeOf(b);
        const both = a.path.length && b.path.length;                 // 둘 다 걷는 중 — 엇갈려 지나가게 작게
        const rx = (sa + sb) * (both ? 0.16 : 0.3), ry = (sa + sb) * (both ? 0.06 : 0.14);
        let dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx / rx, dy / ry);
        if (d >= 1) continue;
        if (d < 1e-3) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; }
        const n = Math.hypot(dx / rx, dy / ry) || 1;
        let px = (dx / rx) / n * rx * (1 - d), py = (dy / ry) / n * ry * (1 - d);
        const total = wa + wb, ka = wa / total, kb = wb / total;
        for (const [e, k, sign] of [[a, ka, -1], [b, kb, 1]]) {
          if (!k) continue;
          let mx = sign * px * k * rate, my = sign * py * k * rate;
          if (e.path.length && (sign < 0 ? wb : wa) === 0) {       // 막혔다 — 옆걸음으로 돌아간다
            const vx = e.vx || 0, vy = e.vy || 0, vl = Math.hypot(vx, vy) || 1;
            const side = (vx * my - vy * mx) >= 0 ? 1 : -1;
            mx += -vy / vl * side * 1.2 * rate * 4; my += vx / vl * side * 1.2 * rate * 4;
          }
          const from = [e.x, e.y];
          if (canMove(from, [e.x + mx, e.y + my])) { e.x += mx; e.y += my; }
          else if (canMove(from, [e.x + mx, e.y])) e.x += mx;                 // 벽을 따라 미끄러진다
          else if (canMove(from, [e.x, e.y + my])) e.y += my;
        }
      }
    }
  }

  // 지나가다 마주치면 손 흔들어 인사 — 둘 다 걷는 중이고 가까울 때, 같은 짝은 40초에 한 번
  function greet(t) {
    const walkers = [...L.people.values()].filter(p => p.path.length && !p.leaving && !p.over);
    for (let i = 0; i < walkers.length; i++) {
      for (let j = i + 1; j < walkers.length; j++) {
        const a = walkers[i], b = walkers[j];
        if (Math.hypot(a.x - b.x, a.y - b.y) > 42) continue;
        const key = [a.name, b.name].sort().join("|");
        if (t - (L.greeted.get(key) ?? -99) < 40) continue;
        L.greeted.set(key, t);
        if (Math.random() < 0.4) continue;
        for (const [p, q] of [[a, b], [b, a]]) {
          p.face = p.x < q.x ? 1 : -1;
          p.over = { pose: "wave", until: t + 0.9 };
          say(p, pick(OFFICE_GREET), 1.4);
        }
      }
    }
  }

  return { L, SIT_DESK, sync, update, cut, nearest, route, colorOf, speciesOf, modeOf, blocked, onFloor,
           drainEvents: () => L.events.splice(0, L.events.length) };
})();
