"""다른 PC 터미널의 파일 패널 — ssh 로 원격 폴더를 읽기 전용으로 본다.

ssh 는 전부 가짜(subprocess.run 치환)다. 보는 것: ①원격 명령이 PowerShell -EncodedCommand 로 가고 경로가
그 안에 들어가는가 ②`..`·절대경로·비밀파일·드라이브는 ssh 를 부르기도 전에 404 인가 ③base64 JSON 을 로컬과
같은 모양으로 푸는가 ④바이너리·CP949·크기 초과·ssh 실패가 각각의 예외로 갈리는가 ⑤로컬 폴더가 있으면 원격이
아니라 로컬인가.
"""
import base64
import json

import pytest

from bridge import config, files, remote_files, sessions

RM = {"host": "srvpc", "path": "C:\\rproj"}


def _b64json(obj) -> bytes:
    return base64.b64encode(json.dumps(obj).encode())


class FakeRun:
    """ssh 를 흉내 낸다 — 마지막 명령을 기억하고 정해진 (종료코드, stdout) 을 돌려준다."""
    def __init__(self, code=0, stdout=b""):
        self.code, self.stdout, self.calls = code, stdout, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        class Out: pass
        o = Out(); o.returncode = self.code; o.stdout = self.stdout; o.stderr = b"\xc3\xa7\xc0\xfa"   # CP949 잡음
        return o

    def script(self) -> str:
        cmd = self.calls[-1]
        return base64.b64decode(cmd[cmd.index("-EncodedCommand") + 1]).decode("utf-16-le")


# ---------- 경로 ----------

def test_remote_path_는_슬래시_상대경로를_윈도우_절대경로로():
    assert remote_files.remote_path("C:\\rproj", "") == "C:\\rproj"
    assert remote_files.remote_path("C:\\rproj\\", "docs/a b.md") == "C:\\rproj\\docs\\a b.md"
    assert remote_files.remote_path("C:\\rproj", "docs%2Fx.md") == "C:\\rproj\\docs\\x.md"   # 로컬과 같은 이중 디코딩


@pytest.mark.parametrize("rel", ["../Users", "docs/../../x", "/C:/Windows", "\\x", "C:/Windows",
                                 "docs\\..\\x", ".env", "docs/.env.local", "secrets/x.txt",
                                 "id_rsa", "a?b", "a*", "a:b", " a", "a|b", "..", "."])
def test_remote_path_는_밖_비밀_드라이브_금지글자를_전부_거부한다(rel):
    with pytest.raises(files.NotAllowed):
        remote_files.remote_path("C:\\rproj", rel)


def test_거부되는_경로는_ssh_를_부르지도_않는다(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(remote_files.subprocess, "run", run)
    with pytest.raises(files.NotAllowed):
        remote_files.list_dir(RM, "../Users")
    assert run.calls == []


# ---------- 목록 ----------

def test_list_dir_는_PowerShell_EncodedCommand_로_묻고_base64_JSON_을_로컬_모양으로_푼다(monkeypatch):
    run = FakeRun(0, _b64json([
        {"name": "README.md", "dir": False, "size": 1134},
        {"name": "docs", "dir": True, "size": None},
        {"name": ".env", "dir": False, "size": 10},          # 비밀파일 — 목록에서 뺀다
        {"name": "한글폴더", "dir": True, "size": None},
    ]))
    monkeypatch.setattr(remote_files.subprocess, "run", run)
    got = remote_files.list_dir(RM, "docs")
    assert got == [{"name": "docs", "dir": True, "size": None},
                   {"name": "한글폴더", "dir": True, "size": None},
                   {"name": "README.md", "dir": False, "size": 1134}]     # 폴더 먼저, 비밀 제외
    cmd = run.calls[-1]
    assert cmd[0] == "ssh" and "BatchMode=yes" in cmd and cmd[cmd.index("--") + 1] == "srvpc"
    assert cmd[cmd.index("srvpc") + 1:cmd.index("-EncodedCommand")] == ["powershell", "-NoProfile", "-NonInteractive"]
    assert "'C:\\rproj\\docs'" in run.script() and "Get-ChildItem" in run.script()


def test_list_dir_는_항목_하나짜리_객체도_배열로_받는다(monkeypatch):
    monkeypatch.setattr(remote_files.subprocess, "run",
                        FakeRun(0, _b64json({"name": "only.txt", "dir": False, "size": 3})))
    assert remote_files.list_dir(RM) == [{"name": "only.txt", "dir": False, "size": 3}]


def test_list_dir_는_원격_실패코드를_404_로_뭉갠다(monkeypatch):
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(3, b""))
    with pytest.raises(files.NotAllowed):
        remote_files.list_dir(RM, "nope")


def test_ssh_자체_실패는_RemoteUnavailable(monkeypatch):
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(255, b""))
    with pytest.raises(remote_files.RemoteUnavailable):
        remote_files.list_dir(RM)
    def boom(*a, **k):
        raise OSError("no ssh")
    monkeypatch.setattr(remote_files.subprocess, "run", boom)
    with pytest.raises(remote_files.RemoteUnavailable):
        remote_files.list_dir(RM)


def test_경로에_작은따옴표가_있어도_PowerShell_문자열이_안_깨진다(monkeypatch):
    run = FakeRun(0, _b64json([]))
    monkeypatch.setattr(remote_files.subprocess, "run", run)
    remote_files.list_dir(RM, "it's")
    assert "'C:\\rproj\\it''s'" in run.script()


# ---------- 읽기 ----------

def _file_out(data: bytes, mtime="639274661072399160"):
    return _b64json({"mtime": mtime, "size": len(data), "data": base64.b64encode(data).decode()})


def test_read_text_는_로컬과_같은_모양_BOM_은_벗긴다(monkeypatch):
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(0, _file_out("\ufeff# 자체 웹 MES\n".encode())))
    got = remote_files.read_text(RM, "README.md")
    assert got == {"content": "# 자체 웹 MES\n", "mtimeNs": "639274661072399160", "size": 20}   # size 는 BOM 포함 원본 바이트


def test_read_text_는_바이너리와_CP949_를_IsBinary_로(monkeypatch):
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(0, _file_out(b"PK\x00\x01zip")))
    with pytest.raises(files.IsBinary):
        remote_files.read_text(RM, "a.zip")
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(0, _file_out("한글".encode("cp949"))))
    with pytest.raises(files.IsBinary):
        remote_files.read_text(RM, "old.txt")


def test_read_text_는_크기_초과를_원격에서도_로컬에서도_TooLarge_로(monkeypatch):
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(4, b""))         # 원격이 먼저 거른 경우
    with pytest.raises(files.TooLarge):
        remote_files.read_text(RM, "big.log")
    monkeypatch.setattr(config, "MAX_TEXT_FILE_BYTES", 4)
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(0, _file_out(b"12345")))   # 그래도 넘어온 경우
    with pytest.raises(files.TooLarge):
        remote_files.read_text(RM, "big.log")


def test_read_text_는_깨진_출력을_RemoteUnavailable_로(monkeypatch):
    monkeypatch.setattr(remote_files.subprocess, "run", FakeRun(0, b"not base64!!"))
    with pytest.raises(remote_files.RemoteUnavailable):
        remote_files.read_text(RM, "x.txt")


# ---------- 어느 프로젝트가 원격인가 ----------

def test_remote_of_는_ssh_세션이고_원격폴더를_알_때만(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "session_remotes", lambda: {
        "rproj": {"host": "srvpc", "path": "C:\\rproj"},
        "bare": {"host": "winpc", "path": None},                  # 폴더를 모르면 원격 아님(404 그대로)
        "evil": {"host": "-oProxyCommand=calc", "path": "C:\\x"},  # 옵션처럼 생긴 호스트는 거부
    })
    assert remote_files.remote_of("rproj") == {"host": "srvpc", "path": "C:\\rproj"}
    assert remote_files.remote_of("bare") is None
    assert remote_files.remote_of("evil") is None
    assert remote_files.remote_of("nothere") is None
    assert remote_files.remote_of("../x") is None


def test_remote_of_는_같은_이름의_로컬_폴더가_있으면_로컬이_우선(tmp_path, monkeypatch):
    (tmp_path / "rproj").mkdir()
    monkeypatch.setattr(config, "projects_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "session_remotes", lambda: {"rproj": {"host": "srvpc", "path": "C:\\rproj"}})
    assert remote_files.remote_of("rproj") is None
