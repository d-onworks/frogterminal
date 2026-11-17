"""게임패드 신호 — 패드 상주 프로그램(별도 설치)이 "칸 N / 프로젝트 X 로 가라"를 보낸다.

패드 상주 프로그램이 POST /api/pad 로 누른 버튼을 알리고, 웹(pad.js)은 GET /api/pad/wait 롱폴로
기다렸다 받는다. 주기 폴링이 아니라 롱폴이라 버튼을 안 누르면 오가는 게 없다. 마지막 신호 하나만
메모리에 둔다 — 지난 버튼을 나중에 다시 틀 일은 없다(브릿지 재시작 시 id 는 0 부터).
인증은 다른 /api/* 와 같은 auth_middleware(Tailscale 신원 + 토큰 쿠키)를 그대로 탄다.
"""
from __future__ import annotations

import asyncio

from aiohttp import web

WAIT_SECONDS = 25.0                   # 롱폴 한 번의 최대 대기 — 프록시·브라우저가 끊기 전에 돌려준다
PROJECT_MAX = 100


class PadState:
    def __init__(self) -> None:
        self.last: dict = {"id": 0}
        self.changed = asyncio.Condition()

    async def push(self, slot: int, project: str) -> int:
        async with self.changed:
            self.last = {"id": self.last["id"] + 1, "slot": slot, "project": project}
            self.changed.notify_all()
            return self.last["id"]

    async def wait(self, after: int, timeout: float) -> dict:
        # 브릿지가 재시작해 id 가 줄었으면(after 가 더 큼) 지금 것부터 다시 받게 한다
        newer = lambda: self.last["id"] > after or (after > self.last["id"] and self.last["id"] > 0)
        async with self.changed:
            try:
                await asyncio.wait_for(self.changed.wait_for(newer), timeout)
            except asyncio.TimeoutError:
                return {"id": after}
            return dict(self.last)


PAD = web.AppKey("pad", PadState)


def parse(body: object) -> tuple[int, str] | None:
    """{"slot": 1~4 정수, "project": 짧은 문자열(빈 값 허용)} → (slot, project). 아니면 None."""
    if not isinstance(body, dict):
        return None
    slot, project = body.get("slot"), body.get("project", "")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 4:
        return None
    if project is None:
        project = ""
    if not isinstance(project, str) or len(project) > PROJECT_MAX or any(c in project for c in "\r\n\0/"):
        return None
    return slot, project.strip()


async def api_pad(request: web.Request) -> web.Response:
    try:
        parsed = parse(await request.json())
    except ValueError:
        parsed = None
    if parsed is None:
        return web.json_response({"error": "slot 1~4, project 짧은 문자열"}, status=400)
    return web.json_response({"ok": True, "id": await request.app[PAD].push(*parsed)})


async def api_pad_wait(request: web.Request) -> web.Response:
    if "after" not in request.query:            # 처음 붙을 때 — 기다리지 않고 지금 id 만 알려 준다
        return web.json_response({"id": request.app[PAD].last["id"]})
    try:
        after = max(0, int(request.query.get("after", "0")))
    except ValueError:
        return web.json_response({"error": "after"}, status=400)
    return web.json_response(await request.app[PAD].wait(after, WAIT_SECONDS))


# ── 지금 보는 세션 — 웹이 칸·세션을 옮길 때 알리고, 게임기 frog-agent(맥 서버)가 읽어 따라간다 ──
# (2026-11-07 요청: 맥 화면에서 세션을 옮기면 게임기 선택도 같이 따라가게)
# 마지막 하나만 메모리에. 같은 세션을 또 알리면 id 를 올리지 않는다(읽는 쪽이 '바뀜'만 보게).
FOCUS = web.AppKey("pad_focus", dict)


async def api_focus_set(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        body = None
    project = body.get("project") if isinstance(body, dict) else None
    if (not isinstance(project, str) or not project.strip() or len(project) > PROJECT_MAX
            or any(c in project for c in "\r\n\0/")):
        return web.json_response({"error": "project 짧은 문자열"}, status=400)
    focus = request.app[FOCUS]
    if focus["project"] != project.strip():
        focus.update(id=focus["id"] + 1, project=project.strip())
    return web.json_response({"ok": True, "id": focus["id"]})


async def api_focus_get(request: web.Request) -> web.Response:
    return web.json_response(dict(request.app[FOCUS]))


def add_routes(app: web.Application) -> None:
    app[PAD] = PadState()
    app[FOCUS] = {"id": 0, "project": ""}
    app.router.add_post("/api/pad", api_pad)
    app.router.add_get("/api/pad/wait", api_pad_wait)
    app.router.add_post("/api/pad/focus", api_focus_set)
    app.router.add_get("/api/pad/focus", api_focus_get)
