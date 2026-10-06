#requires -Version 5.1
# Windows bootstrap: prefer PowerShell 7; 5.1 supports machines without it.
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [ValidateSet('codex', 'none')][string]$HostName = 'codex',
    [string]$ToolsDirectory = '',
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 1200
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stage = '检查源码包'

function Stop-InstallerProcessTree {
    param([System.Diagnostics.Process]$Process)
    if ($Process.HasExited) { return }
    try {
        $Process.Kill($true)
    } catch {
        # .NET Framework / Windows PowerShell 5.1 lacks Kill(bool).
        # Use the exact child PID and /T; killing only uv would leave its workers.
        $taskKill = Join-Path ([Environment]::GetFolderPath('System')) 'taskkill.exe'
        & $taskKill /PID $Process.Id /T /F 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0 -and -not $Process.HasExited) {
            throw '无法结束安装子进程，请检查进程状态后重试。'
        }
    }
    if (-not $Process.WaitForExit(10000)) { throw '安装子进程未及时结束。' }
}

function Invoke-InstallerProcess {
    param([string]$Executable, [string[]]$Arguments, [int]$Seconds = $TimeoutSeconds)
    # ProcessStartInfo avoids shell interpretation of paths containing & or spaces.
    $quoted = foreach ($argument in $Arguments) {
        '"' + [regex]::Replace([regex]::Replace($argument, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = $quoted -join ' '
    $start.WorkingDirectory = $projectRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Console]::OutputEncoding
    $start.StandardErrorEncoding = [Console]::OutputEncoding
    # An inherited override must not install into an unrelated environment.
    $start.EnvironmentVariables['UV_PROJECT_ENVIRONMENT'] = Join-Path $projectRoot '.venv'
    $start.EnvironmentVariables.Remove('VIRTUAL_ENV')
    # Let each PowerShell edition discover its own built-in modules.
    $start.EnvironmentVariables.Remove('PSModulePath')
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    $started = $false
    try {
        if (-not $process.Start()) { throw '无法启动程序。' }
        $started = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($Seconds * 1000)) {
            Stop-InstallerProcessTree -Process $process
            throw '操作超时，请检查网络后重新运行。'
        }
        $stdoutText = $stdoutTask.GetAwaiter().GetResult()
        $stderrText = $stderrTask.GetAwaiter().GetResult()
        if ($stdoutText) { Write-Host $stdoutText.TrimEnd() }
        if ($stderrText) { Write-Host $stderrText.TrimEnd() }
        if ($process.ExitCode -ne 0) { throw ('程序退出码：' + $process.ExitCode) }
    } finally {
        if ($started -and -not $process.HasExited) {
            Stop-InstallerProcessTree -Process $process
        }
        $process.Dispose()
    }
}

try {
    Write-Host 'CogniVault 安装向导'
    Write-Host '自动准备基础环境；已有资料配置将保留。首次安装需要联网。'
    foreach ($relative in @('pyproject.toml', 'uv.lock', 'scripts/setup_local.py',
                            '.codex/setup_mcp.py', '.codex/config.example.toml',
                            'src/cognivault/__init__.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relative) -PathType Leaf)) {
            throw '源码包不完整。请解压完整的源码包后双击 install.cmd。'
        }
    }

    $stage = '准备 uv'
    Write-Host "`n[1/3] 准备安装工具..."
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $toolsRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CogniVault/tools/uv/0.12.17'
    if ($ToolsDirectory) {
        if (-not [IO.Path]::IsPathRooted($ToolsDirectory)) { throw 'ToolsDirectory 必须是绝对路径。' }
        $toolsRoot = [IO.Path]::GetFullPath($ToolsDirectory)
    }
    $installedUv = Join-Path $toolsRoot 'uv.exe'
    if ($uvCommand) {
        $uvExecutable = $uvCommand.Source
    } elseif (Test-Path -LiteralPath $installedUv -PathType Leaf) {
        $uvExecutable = $installedUv
    } else {
        Write-Host '未找到 uv，正在从官方渠道安装到当前用户目录...'
        $temporaryInstaller = Join-Path ([IO.Path]::GetTempPath()) ('cognivault-uv-' + [guid]::NewGuid().ToString('N') + '.ps1')
        $oldInstallDir = $env:UV_INSTALL_DIR
        $oldNoModify = $env:UV_NO_MODIFY_PATH
        $oldUnmanaged = $env:UV_UNMANAGED_INSTALL
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.12.17/install.ps1' -OutFile $temporaryInstaller -TimeoutSec 60
            $env:UV_INSTALL_DIR = $toolsRoot
            $env:UV_NO_MODIFY_PATH = '1'
            $env:UV_UNMANAGED_INSTALL = $toolsRoot
            $currentShell = (Get-Process -Id $PID).Path
            Invoke-InstallerProcess -Executable $currentShell -Arguments @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $temporaryInstaller) -Seconds 300
        } finally {
            $env:UV_INSTALL_DIR = $oldInstallDir
            $env:UV_NO_MODIFY_PATH = $oldNoModify
            $env:UV_UNMANAGED_INSTALL = $oldUnmanaged
            if (Test-Path -LiteralPath $temporaryInstaller -PathType Leaf) {
                Remove-Item -LiteralPath $temporaryInstaller
            }
        }
        if (-not (Test-Path -LiteralPath $installedUv -PathType Leaf)) { throw 'uv 未安装成功，请检查网络后重试。' }
        $uvExecutable = $installedUv
    }
    Invoke-InstallerProcess -Executable $uvExecutable -Arguments @('--version') -Seconds 30

    $stage = '安装 Python 和项目依赖'
    Write-Host "`n[2/3] 准备 Python 3.11 和项目依赖，首次下载可能需要几分钟..."
    Invoke-InstallerProcess -Executable $uvExecutable -Arguments @('sync', '--locked', '--no-editable', '--python', '3.11', '--project', $projectRoot)
    $pythonExecutable = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) { throw '项目 Python 环境未创建。' }

    $stage = '生成本地与客户端配置'
    Write-Host "`n[3/3] 生成本地配置与客户端接入..."
    Invoke-InstallerProcess -Executable $pythonExecutable -Arguments @('-X', 'utf8', (Join-Path $projectRoot 'scripts/setup_local.py'), '--uv', $uvExecutable, '--host', $HostName) -Seconds 60
    exit 0
} catch {
    Write-Host "`n安装未完成：$stage" -ForegroundColor Red
    Write-Host $_.Exception.Message
    Write-Host '可以直接重新双击 install.cmd；已有资料和配置会保留。'
    Write-Host '更多帮助见 docs/installation.md。'
    exit 1
}
