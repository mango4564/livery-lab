param([switch]$ResolveOnly)

$ErrorActionPreference = 'Stop'
$statusWindow = $null
try {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    }
    $taskRoot = Join-Path $PSScriptRoot ('.local\tasks\' + (Get-Date -Format 'yyyyMMdd') + '_launch_latest')
    New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
    $runId = (Get-Date -Format 'HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $resultPath = Join-Path $taskRoot ($runId + '.path.txt')
    $logPath = Join-Path $taskRoot ($runId + '.log')
    $buildScript = Join-Path $PSScriptRoot 'scripts\build_windows.py'
    $process = Start-Process -FilePath $python -ArgumentList @('-X', 'utf8', ('"' + $buildScript + '"'), '--ensure-latest') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $resultPath -RedirectStandardError $logPath -PassThru
    # Retain the native handle so Windows PowerShell can read ExitCode after exit.
    $process.Handle | Out-Null
    $started = Get-Date
    while (-not $process.WaitForExit(100)) {
        if (-not $ResolveOnly -and -not $statusWindow -and ((Get-Date) - $started).TotalSeconds -gt 2) {
            Add-Type -AssemblyName System.Windows.Forms
            $statusWindow = New-Object System.Windows.Forms.Form
            $statusWindow.Text = '彩条工作台 · 检查更新'
            $statusWindow.ClientSize = New-Object System.Drawing.Size(440, 85)
            $statusWindow.StartPosition = 'CenterScreen'
            $statusWindow.ControlBox = $false
            $label = New-Object System.Windows.Forms.Label
            $label.Text = "正在检查版本；需要更新时会自动打包。`n完成后自动打开工作台，请稍候…"
            $label.AutoSize = $true
            $label.Location = New-Object System.Drawing.Point(20, 20)
            $statusWindow.Controls.Add($label)
            $statusWindow.Show()
        }
        if ($statusWindow) { [System.Windows.Forms.Application]::DoEvents() }
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        $detail = Get-Content -LiteralPath $logPath -Encoding UTF8 -Tail 12 | Out-String
        throw "检查或自动打包失败。`n$detail`n启动日志：$logPath"
    }
    $executable = (Get-Content -LiteralPath $resultPath -Encoding UTF8 -Raw).Trim()
    if (-not $executable -or -not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "自动打包未返回有效的程序路径。日志：$logPath"
    }
    if ($ResolveOnly) {
        Write-Output $executable
        exit 0
    }
    Start-Process -FilePath $executable -WorkingDirectory (Split-Path -Parent $executable)
} catch {
    if ($ResolveOnly) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '工作台启动失败') | Out-Null
    exit 1
} finally {
    if ($statusWindow) { $statusWindow.Dispose() }
}
