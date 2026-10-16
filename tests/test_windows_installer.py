"""Run the Windows entrypoints; fake only the external uv installation boundary."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib

import pytest

from test_install_setup import clone_setup_files, REPOSITORY


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer entrypoint")


@pytest.fixture(scope="module")
def fake_uv(tmp_path_factory):
    directory = tmp_path_factory.mktemp("uv-program")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        pytest.skip("Windows .NET Framework compiler required for the external uv stub")
    source = directory / "uv.cs"
    source.write_text('''using System;
using System.IO;
using System.Text;
class Program {
  static int Main(string[] args) {
    if (args.Length == 1 && args[0] == "child") {
      System.Threading.Thread.Sleep(2500);
      File.WriteAllText(Environment.GetEnvironmentVariable("FAKE_UV_CHILD_MARKER"), "still running");
      return 0;
    }
    File.AppendAllText(Environment.GetEnvironmentVariable("FAKE_UV_TRACE"),
      String.Join("|", args) + "\\n", new UTF8Encoding(false));
    if (args.Length == 1 && args[0] == "--version") {
      System.Threading.Thread.Sleep(Int32.Parse(Environment.GetEnvironmentVariable("FAKE_UV_VERSION_DELAY_MS") ?? "0"));
      Console.WriteLine("uv fixture"); return 0;
    }
    if (args.Length != 7 || args[0] != "sync" || args[1] != "--locked" ||
        args[2] != "--no-editable" || args[3] != "--python" || args[4] != "3.11" ||
        args[5] != "--project" || !File.Exists(Path.Combine(args[6], "uv.lock"))) return 89;
    if (Environment.GetEnvironmentVariable("FAKE_UV_POLICY_CHECK") == "1") {
      string shell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
        "WindowsPowerShell/v1.0/powershell.exe");
      var check = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(
        shell, "-NoProfile -Command Get-ExecutionPolicy") { UseShellExecute = false, CreateNoWindow = true });
      check.WaitForExit();
      if (check.ExitCode != 0) return 90;
    }
    if (Environment.GetEnvironmentVariable("FAKE_UV_CHILD_MARKER") != null) {
      var child = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(
        System.Reflection.Assembly.GetExecutingAssembly().Location, "child") {
          UseShellExecute = false, CreateNoWindow = true });
      File.AppendAllText(Environment.GetEnvironmentVariable("FAKE_UV_TRACE"),
        "spawned-child|" + child.Id + "\\n", new UTF8Encoding(false));
      System.Threading.Thread.Sleep(5000);
    }
    if (Environment.GetEnvironmentVariable("FAKE_UV_SLEEP") == "1") System.Threading.Thread.Sleep(5000);
    return Int32.Parse(Environment.GetEnvironmentVariable("FAKE_UV_EXIT") ?? "0");
  }
}''', encoding="utf-8")
    executable = directory / "uv.exe"
    result = subprocess.run([str(compiler), "/nologo", f"/out:{executable}", str(source)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout
    return executable


def installer_checkout(tmp_path):
    root = clone_setup_files(tmp_path / "安装工具 with spaces & symbol")
    for name in ("install.cmd", "scripts/install.ps1", "uv.lock"):
        shutil.copyfile(REPOSITORY / name, root / name)
    (root / "src/cognivault").mkdir(parents=True)
    shutil.copyfile(REPOSITORY / "src/cognivault/__init__.py", root / "src/cognivault/__init__.py")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
                   check=True, capture_output=True, timeout=30)
    return root


def installer_environment(fake_uv, tmp_path, exit_code=0):
    env = os.environ.copy()
    env["PATH"] = str(fake_uv.parent) + os.pathsep + env["PATH"]
    env["FAKE_UV_TRACE"] = str(tmp_path / "uv-trace.txt")
    env["FAKE_UV_EXIT"] = str(exit_code)
    return env


@pytest.mark.parametrize("entry", ["powershell", "cmd"])
def test_windows_entrypoints_install_from_other_cwd_with_unicode_and_spaces(tmp_path, fake_uv, entry):
    root = installer_checkout(tmp_path)
    env = installer_environment(fake_uv, tmp_path)
    if entry == "cmd":
        # /s strips the outer pair; keep an inner pair around the batch path.
        command = f'"{env["COMSPEC"]}" /d /s /c ""{root / "install.cmd"}" -NonInteractive"'
    else:
        command = [shutil.which("pwsh"), "-NoProfile", "-File", str(root / "scripts/install.ps1"), "-NonInteractive"]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "基础安装完成" in result.stdout
    assert "health_report" in result.stdout
    raw = tomllib.loads((root / "config.local.toml").read_text(encoding="utf-8"))
    assert raw["study"] == {"backend": "not_configured"}
    server = tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["cognivault"]
    assert Path(server["command"]) == fake_uv
    trace = (tmp_path / "uv-trace.txt").read_text(encoding="utf-8").splitlines()
    assert trace == ["--version", f"sync|--locked|--no-editable|--python|3.11|--project|{root}"]


def test_failed_dependency_install_does_not_generate_configs(tmp_path, fake_uv):
    root = installer_checkout(tmp_path)
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-File", str(root / "scripts/install.ps1"), "-NonInteractive"],
        cwd=tmp_path, env=installer_environment(fake_uv, tmp_path, exit_code=23),
        capture_output=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode != 0
    assert not (root / "config.local.toml").exists()
    assert not (root / ".codex/config.toml").exists()
    assert "基础安装完成" not in result.stdout


def test_timed_out_install_stops_before_configuration(tmp_path, fake_uv):
    root = installer_checkout(tmp_path)
    env = installer_environment(fake_uv, tmp_path)
    env["FAKE_UV_SLEEP"] = "1"
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-File", str(root / "scripts/install.ps1"),
         "-NonInteractive", "-TimeoutSeconds", "1"],
        cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode != 0
    assert not (root / "config.local.toml").exists()
    assert "超时" in result.stdout


@pytest.mark.parametrize("version_delay_ms", [0, 16000], ids=["immediate-version", "slow-allowed-version"])
def test_builtin_powershell_timeout_stops_descendants(tmp_path, fake_uv, version_delay_ms):
    root = installer_checkout(tmp_path)
    env = installer_environment(fake_uv, tmp_path)
    marker = tmp_path / "child-still-running.txt"
    env["FAKE_UV_CHILD_MARKER"] = str(marker)
    env["FAKE_UV_VERSION_DELAY_MS"] = str(version_delay_ms)
    # Compatibility coverage specifically requires the Windows 5.1 runtime.
    shell = Path(env["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # This harness budget includes PS5.1 startup, the separately allowed 30s
    # version probe and up to 10s cleanup. The sync deadline stays at 1s.
    result = subprocess.run(
        [str(shell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts/install.ps1"), "-NonInteractive", "-TimeoutSeconds", "1"],
        cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=60,
    )
    trace = (tmp_path / "uv-trace.txt").read_text(encoding="utf-8")
    diagnostic = f"exit={result.returncode}; uv trace={trace}; stdout={result.stdout}; stderr={result.stderr}"
    assert result.returncode != 0, diagnostic
    assert "超时" in result.stdout, diagnostic
    assert "sync|--locked|--no-editable|--python|3.11|--project|" in trace, diagnostic
    assert "spawned-child|" in trace, diagnostic
    time.sleep(3)
    assert not marker.exists(), diagnostic
    assert not (root / "config.local.toml").exists(), diagnostic


def test_native_bootstrap_does_not_inherit_other_powershell_module_paths(tmp_path, fake_uv):
    root = installer_checkout(tmp_path)
    env = installer_environment(fake_uv, tmp_path)
    # The test is run by PS7: inheriting its modules breaks Security in PS5.1.
    env["PSModulePath"] = str(Path(shutil.which("pwsh")).parent / "Modules")
    env["FAKE_UV_POLICY_CHECK"] = "1"
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-File", str(root / "scripts/install.ps1"), "-NonInteractive"],
        cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / ".codex/config.toml").exists()


def test_incomplete_source_download_stops_before_network_or_uv(tmp_path, fake_uv):
    root = tmp_path / "incomplete"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(REPOSITORY / "scripts/install.ps1", root / "scripts/install.ps1")
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-File", str(root / "scripts/install.ps1"), "-NonInteractive"],
        cwd=tmp_path, env=installer_environment(fake_uv, tmp_path),
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode != 0
    assert not (tmp_path / "uv-trace.txt").exists()


def test_builtin_powershell_compatibility_when_pwsh_not_on_path(tmp_path, fake_uv):
    root = installer_checkout(tmp_path)
    env = installer_environment(fake_uv, tmp_path)
    env["PATH"] = str(fake_uv.parent) + os.pathsep + str(Path(env["WINDIR"]) / "System32")
    command = f'"{env["COMSPEC"]}" /d /s /c ""{root / "install.cmd"}" -NonInteractive"'
    result = subprocess.run(command,
                            cwd=tmp_path, env=env, capture_output=True, encoding="utf-8",
                            errors="replace", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / ".codex/config.toml").exists()
