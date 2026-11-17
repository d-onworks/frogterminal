"""게임패드 신호 — POST /api/pad 로 받은 버튼을 /api/pad/wait 롱폴이 넘겨준다."""
import asyncio

import pytest

from bridge import pad_api, server

GOOD = {"Tailscale-User-Login": "tester@example.com"}


@pytest.fixture
async def client(aiohttp_client, monkeypatch):
    monkeypatch.setattr(pad_api, "WAIT_SECONDS", 0.3)
    return await aiohttp_client(server.create_app())


async def test_인증_없이는_못_보낸다(client):
    assert (await client.post("/api/pad", json={"slot": 1, "project": "alpha"})).status == 403
    assert (await client.get("/api/pad/wait")).status == 403


@pytest.mark.parametrize("body", [
    {"slot": 0}, {"slot": 5}, {"slot": "1"}, {"slot": True}, {"slot": 1.0}, {},
    {"slot": 1, "project": 3}, {"slot": 1, "project": "a" * 101}, {"slot": 1, "project": "a\nb"},
    {"slot": 1, "project": "../x"}, [1],
])
async def test_틀린_값은_400(client, body):
    assert (await client.post("/api/pad", json=body, headers=GOOD)).status == 400


async def test_보낸_신호를_기다리던_쪽이_바로_받는다(client):
    first = await (await client.get("/api/pad/wait", headers=GOOD)).json()
    assert first == {"id": 0}                                   # 처음 붙을 땐 기다리지 않고 지금 id 만
    waiting = asyncio.ensure_future(client.get("/api/pad/wait?after=0", headers=GOOD))
    await asyncio.sleep(0.05)
    sent = await (await client.post("/api/pad", json={"slot": 3, "project": "beta"},
                                    headers=GOOD)).json()
    assert sent == {"ok": True, "id": 1}
    assert await (await waiting).json() == {"id": 1, "slot": 3, "project": "beta"}


async def test_새_신호가_없으면_시간이_끝나고_같은_id(client):
    await client.post("/api/pad", json={"slot": 2}, headers=GOOD)   # 프로젝트 없음도 허용
    got = await (await client.get("/api/pad/wait?after=1", headers=GOOD)).json()
    assert got == {"id": 1}


async def test_지난_신호는_바로_받는다(client):
    await client.post("/api/pad", json={"slot": 1, "project": "alpha"}, headers=GOOD)
    await client.post("/api/pad", json={"slot": 4, "project": "blender"}, headers=GOOD)
    got = await (await client.get("/api/pad/wait?after=1", headers=GOOD)).json()
    assert got == {"id": 2, "slot": 4, "project": "blender"}       # 마지막 것 하나만 보관


async def test_브릿지가_다시_켜져_id_가_줄어도_다음_신호를_받는다(client):
    await client.post("/api/pad", json={"slot": 2, "project": "x"}, headers=GOOD)
    got = await (await client.get("/api/pad/wait?after=57", headers=GOOD)).json()
    assert got["id"] == 1 and got["slot"] == 2


# ── 지금 보는 세션(focus) — 웹이 칸을 옮기면 알리고, 게임기 frog-agent 가 읽어 따라간다 (2026-11-07) ──
async def test_focus_인증_없이는_못_쓴다(client):
    assert (await client.post("/api/pad/focus", json={"project": "alpha"})).status == 403
    assert (await client.get("/api/pad/focus")).status == 403


async def test_focus_처음엔_비어_있다(client):
    assert await (await client.get("/api/pad/focus", headers=GOOD)).json() == {"id": 0, "project": ""}


async def test_focus_마지막_세션을_돌려준다(client):
    await client.post("/api/pad/focus", json={"project": "kakao"}, headers=GOOD)
    r = await client.post("/api/pad/focus", json={"project": "alpha"}, headers=GOOD)
    assert await r.json() == {"ok": True, "id": 2}
    assert await (await client.get("/api/pad/focus", headers=GOOD)).json() == {"id": 2, "project": "alpha"}


async def test_focus_같은_세션을_또_알리면_id_가_안_는다(client):
    await client.post("/api/pad/focus", json={"project": "kakao"}, headers=GOOD)
    r = await client.post("/api/pad/focus", json={"project": "kakao"}, headers=GOOD)
    assert (await r.json())["id"] == 1


@pytest.mark.parametrize("body", [{}, {"project": 3}, {"project": "a" * 101}, {"project": "a\nb"},
                                  {"project": "../x"}, ["x"]])
async def test_focus_틀린_값은_400(client, body):
    assert (await client.post("/api/pad/focus", json=body, headers=GOOD)).status == 400
