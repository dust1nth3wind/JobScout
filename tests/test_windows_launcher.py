"""Exercise the Windows entrypoints with real processes and a harmless fake uv.

Run on Windows 10/11 with ``uv run pytest tests/test_windows_launcher.py``.
The fake executable uses only the .NET Framework bundled with PowerShell 5.1;
tests neither collect jobs nor open the user's browser.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell 5.1 and Windows process/shortcut APIs")

REPOSITORY = Path(__file__).resolve().parents[1]
READY_MESSAGE = "Dashboard bereit:"


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(scope="session")
def powershell() -> Path:
    path = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    assert path.is_file(), "Windows PowerShell 5.1 must be installed"
    return path


def run_powershell(powershell: Path, script: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        **kwargs,
    )


@pytest.fixture(scope="session")
def fake_uv(powershell: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    executable = tmp_path_factory.mktemp("windows-fake-uv") / "uv.exe"
    source = REPOSITORY / "tests/windows/FakeUv.cs"
    result = run_powershell(
        powershell,
        "$ErrorActionPreference = 'Stop'; "
        f"Add-Type -TypeDefinition ([IO.File]::ReadAllText({ps_quote(source)})) "
        f"-OutputAssembly {ps_quote(executable)} -OutputType ConsoleApplication",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert executable.is_file()
    return executable


def process_is_running(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        return False
    try:
        status = ctypes.c_uint32()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(status))) and status.value == 259
    finally:
        kernel.CloseHandle(handle)


class Launcher:
    def __init__(self, root: Path, powershell: Path, fake_uv: Path) -> None:
        self.root = root
        self.powershell = powershell
        self.log = root / "fake-uv.jsonl"
        self.browser_log = root / "browser.log"
        self.processes: list[subprocess.Popen[bytes]] = []
        self.outputs: dict[int, Path] = {}
        self.output_handles: list[Any] = []
        fake_bin = root / "fake bin"
        fake_bin.mkdir()
        shutil.copy2(fake_uv, fake_bin / "uv.exe")
        self.environment = os.environ.copy()
        # Exclude any installed real uv; a mistake must not touch private data.
        self.environment["PATH"] = os.pathsep.join([str(fake_bin), str(powershell.parent), str(Path(os.environ["SystemRoot"]) / "System32")])
        self.environment["JOBSCOUT_TEST_LOG"] = str(self.log)

    def start(self, person: str = "Xinning", port: int | None = None, capture_browser: bool = False,
              startup_timeout_seconds: int = 2, **settings: int) -> subprocess.Popen[bytes]:
        chosen_port = free_port() if port is None else port
        environment = self.environment.copy()
        environment.update({f"JOBSCOUT_TEST_{name}": str(value) for name, value in settings.items()})
        arguments = [str(self.powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
        starter = self.root / "scripts/windows/Start-JobScout.ps1"
        if capture_browser:
            # Shadow only the browser-opening cmdlet in this test runspace. The
            # starter's actual browser branch executes after HTTP readiness.
            environment["JOBSCOUT_TEST_BROWSER_LOG"] = str(self.browser_log)
            arguments += ["-Command",
                "function global:Start-Process { param([string]$FilePath) "
                "[IO.File]::AppendAllText($env:JOBSCOUT_TEST_BROWSER_LOG, $FilePath + [Environment]::NewLine) }; "
                f"& {ps_quote(starter)} -Person {ps_quote(person)} -Port {chosen_port} "
                f"-StartupTimeoutSeconds {startup_timeout_seconds} -NoPause; exit $LASTEXITCODE"]
        else:
            arguments += ["-File", str(starter), "-Person", person, "-Port", str(chosen_port),
                          "-StartupTimeoutSeconds", str(startup_timeout_seconds), "-NoPause", "-NoBrowser"]
        output = self.root / f"launcher-{len(self.processes)}.log"
        handle = output.open("wb")
        self.output_handles.append(handle)
        process = subprocess.Popen(
            arguments,
            cwd=self.root.parent,  # Starter must establish its own working directory.
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        self.processes.append(process)
        self.outputs[process.pid] = output
        return process

    def output(self, process: subprocess.Popen[bytes]) -> str:
        return self.outputs[process.pid].read_text(encoding="utf-8", errors="replace")

    def records(self, kind: str = "invocation") -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        result = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # A live writer can be between write and close.
            if record["kind"] == kind:
                result.append(record)
        return result

    def wait(self, predicate: Callable[[], bool], timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        diagnostic = "\n".join(self.output(process) for process in self.processes)
        pytest.fail(f"Launcher did not reach the expected state.\n{diagnostic}\n{self.records()}")

    def ready(self, process: subprocess.Popen[bytes], count: int = 1) -> None:
        self.wait(lambda: self.output(process).count(READY_MESSAGE) >= count)
        assert process.poll() is None, self.output(process)

    def close(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        # Fallback cleanup also makes a failing cleanup test harmless to the host.
        pids = {record["pid"] for kind in ("invocation", "descendant") for record in self.records(kind)}
        for pid in pids:
            if process_is_running(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10, env=self.environment)
        for handle in self.output_handles:
            handle.close()


@pytest.fixture
def launcher(tmp_path: Path, powershell: Path, fake_uv: Path) -> Iterator[Launcher]:
    root = tmp_path / "JobScout Test Ä Ö with spaces"
    root.mkdir()
    shutil.copytree(REPOSITORY / "scripts/windows", root / "scripts/windows")
    (root / "pyproject.toml").write_text('[project]\nname = "jobscout-test"\n', encoding="utf-8")
    (root / "config").mkdir()
    for person in ("xinning", "harish"):
        (root / f"config/{person}.local.toml").write_text("# Test fixture: parsed only by fake uv.\n", encoding="utf-8")
    instance = Launcher(root, powershell, fake_uv)
    try:
        yield instance
    finally:
        instance.close()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def command(record: dict[str, Any]) -> str:
    arguments = record["args"]
    return "sync" if arguments == ["sync"] else arguments[2]


def option(record: dict[str, Any], name: str) -> str:
    arguments = record["args"]
    return arguments[arguments.index(name) + 1]


@pytest.mark.parametrize(("person", "profile"), [("Xinning", "friend-a"), ("Harish", "friend-b")])
def test_personal_pipeline_and_http_dashboard(launcher: Launcher, person: str, profile: str) -> None:
    port = free_port()
    process = launcher.start(person, port)
    launcher.ready(process)
    records = launcher.records()
    assert [command(record) for record in records] == ["sync", "config", "scan", "serve"]
    assert records[1]["args"][:4] == ["run", "jobscout", "config", "check"]
    for record in records:
        assert Path(record["cwd"]).resolve() == launcher.root.resolve()
    for record in records[1:]:
        config = Path(option(record, "--config"))
        if not config.is_absolute():
            config = launcher.root / config
        assert config.resolve() == (launcher.root / f"config/{person.lower()}.local.toml").resolve()
    assert option(records[2], "--profile") == profile
    assert option(records[3], "--host") == "127.0.0.1"
    assert option(records[3], "--port") == str(port)
    assert f"http://127.0.0.1:{port}/?profile={profile}" in launcher.output(process)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2) as response:
        assert response.status == 200
        assert b"JobScout integration test" in response.read()


@pytest.mark.parametrize(("setting", "expected"), [("SYNC_EXIT", ["sync"]), ("CONFIG_EXIT", ["sync", "config"]), ("SCAN_EXIT", ["sync", "config", "scan"])])
def test_pipeline_stops_after_failed_step(launcher: Launcher, setting: str, expected: list[str]) -> None:
    process = launcher.start(**{setting: 1})
    assert process.wait(timeout=15) != 0, launcher.output(process)
    assert [command(record) for record in launcher.records()] == expected
    assert READY_MESSAGE not in launcher.output(process)


def test_partial_scan_still_serves_dashboard(launcher: Launcher) -> None:
    process = launcher.start(SCAN_EXIT=2)
    launcher.ready(process)
    assert [command(record) for record in launcher.records()] == ["sync", "config", "scan", "serve"]
    assert "WARN" in launcher.output(process).upper() or "TEIL" in launcher.output(process).upper()


def test_readiness_waits_for_http_response(launcher: Launcher) -> None:
    port = free_port()
    process = launcher.start(port=port, capture_browser=True, SERVER_DELAY_MS=1200)
    launcher.wait(lambda: any(command(record) == "serve" for record in launcher.records()))
    time.sleep(0.3)
    assert launcher.records("ready") == []
    assert READY_MESSAGE not in launcher.output(process)
    assert not launcher.browser_log.exists()
    launcher.ready(process)
    assert len(launcher.records("ready")) == 1
    launcher.wait(lambda: launcher.browser_log.exists())
    assert launcher.browser_log.read_text(encoding="utf-8").splitlines() == [f"http://127.0.0.1:{port}/?profile=friend-a"]


def test_failed_server_does_not_announce_dashboard(launcher: Launcher) -> None:
    process = launcher.start(SERVER_FAIL=1)
    launcher.wait(lambda: any(command(record) == "serve" for record in launcher.records()))
    observed_exit = time.monotonic()
    assert process.wait(timeout=15) != 0, launcher.output(process)
    assert time.monotonic() - observed_exit < 1.8, "A dead server should be detected before the 2-second startup timeout"
    assert [command(record) for record in launcher.records()] == ["sync", "config", "scan", "serve"]
    assert READY_MESSAGE not in launcher.output(process)


def test_startup_timeout_cleans_server_tree(launcher: Launcher) -> None:
    process = launcher.start(SERVER_DELAY_MS=10000)
    launcher.wait(lambda: bool(launcher.records("descendant")))
    server = next(record["pid"] for record in launcher.records() if command(record) == "serve")
    descendant = launcher.records("descendant")[0]["pid"]
    assert process.wait(timeout=15) != 0, launcher.output(process)
    assert READY_MESSAGE not in launcher.output(process)
    launcher.wait(lambda: not process_is_running(server) and not process_is_running(descendant), timeout=3)


def test_foreign_listener_is_preserved(launcher: Launcher) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        process = launcher.start(port=port)
        assert process.wait(timeout=15) != 0, launcher.output(process)
        assert "serve" not in [command(record) for record in launcher.records()]
        assert READY_MESSAGE not in launcher.output(process)
        assert listener.getsockname()[1] == port
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass


def test_second_click_restarts_existing_supervisor(launcher: Launcher) -> None:
    port = free_port()
    owner = launcher.start(port=port)
    launcher.ready(owner)
    launcher.wait(lambda: bool(launcher.records("descendant")))
    old_server = next(record["pid"] for record in launcher.records() if command(record) == "serve")
    old_descendant = launcher.records("descendant")[0]["pid"]
    duplicate = launcher.start(port=port)
    assert duplicate.wait(timeout=15) == 0, launcher.output(duplicate)
    launcher.ready(owner, count=2)
    assert [command(record) for record in launcher.records()] == ["sync", "config", "scan", "serve"] * 2
    assert not process_is_running(old_server)
    assert not process_is_running(old_descendant)
    assert owner.poll() is None


@pytest.mark.parametrize(("stage", "invocation_count"), [("SYNC", 1), ("CONFIG", 2), ("SCAN", 3), ("SERVER", 4)])
def test_parallel_clicks_while_busy_run_one_pipeline(launcher: Launcher, stage: str, invocation_count: int) -> None:
    port = free_port()
    owner = launcher.start(port=port, startup_timeout_seconds=5, **{f"{stage}_DELAY_MS": 2500})
    launcher.wait(lambda: len(launcher.records()) == invocation_count)
    duplicates = [launcher.start(port=port) for _ in range(3)]
    for duplicate in duplicates:
        assert duplicate.wait(timeout=15) == 0, launcher.output(duplicate)
        assert launcher.output(duplicate).strip()
    launcher.ready(owner)
    time.sleep(0.5)
    assert [command(record) for record in launcher.records()] == ["sync", "config", "scan", "serve"]


def test_forced_window_close_kills_server_and_descendant(launcher: Launcher) -> None:
    process = launcher.start()
    launcher.ready(process)
    launcher.wait(lambda: bool(launcher.records("descendant")))
    server = next(record["pid"] for record in launcher.records() if command(record) == "serve")
    descendant = launcher.records("descendant")[0]["pid"]
    assert process_is_running(server)
    assert process_is_running(descendant)
    process.kill()
    process.wait(timeout=10)
    launcher.wait(lambda: not process_is_running(server) and not process_is_running(descendant), timeout=3)


def test_persons_cannot_restart_each_others_dashboard(launcher: Launcher) -> None:
    port = free_port()
    xinning = launcher.start("Xinning", port)
    launcher.ready(xinning)
    harish = launcher.start("Harish", port)
    assert harish.wait(timeout=15) != 0, launcher.output(harish)
    time.sleep(0.3)
    assert launcher.output(xinning).count(READY_MESSAGE) == 1
    assert len([record for record in launcher.records() if command(record) == "serve"]) == 1
    assert xinning.poll() is None


@pytest.mark.parametrize("person", ["Xinning", "Harish"])
def test_installer_creates_idempotent_shortcut(launcher: Launcher, person: str) -> None:
    desktop = launcher.root / "Test Desktop Ä Ö"
    desktop.mkdir()
    installer = launcher.root / "scripts/windows/Install-JobScoutIcons.ps1"
    install_script = f"& {ps_quote(installer)} -Person {ps_quote(person)} -DesktopPath {ps_quote(desktop)}"
    for _ in range(2):
        result = run_powershell(launcher.powershell, install_script, cwd=launcher.root.parent, env=launcher.environment)
        assert result.returncode == 0, result.stdout + result.stderr
    shortcuts = list(desktop.glob("*.lnk"))
    assert len(shortcuts) == 1
    assert person in shortcuts[0].stem
    inspect = run_powershell(
        launcher.powershell,
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        f"$link = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(shortcuts[0])}); "
        "$link | Select-Object TargetPath, Arguments, WorkingDirectory, IconLocation | ConvertTo-Json -Compress",
    )
    assert inspect.returncode == 0, inspect.stdout + inspect.stderr
    shortcut = json.loads(inspect.stdout.strip())
    assert Path(shortcut["TargetPath"]).name.lower() == "powershell.exe"
    assert Path(shortcut["TargetPath"]).is_absolute()
    assert Path(shortcut["WorkingDirectory"]).resolve() == launcher.root.resolve()
    assert str(launcher.root / "scripts/windows/Start-JobScout.ps1") in shortcut["Arguments"]
    assert f"-Person {person}" in shortcut["Arguments"] or f'-Person "{person}"' in shortcut["Arguments"]
    icon = Path(shortcut["IconLocation"].rsplit(",", 1)[0])
    assert icon.is_absolute()
    assert icon.is_file() and icon.suffix.lower() == ".ico"
    assert person.lower() in icon.stem.lower()
