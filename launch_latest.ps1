param([switch]$ResolveOnly)

$ErrorActionPreference = 'Stop'
try {
    $releaseRoot = Join-Path $PSScriptRoot 'release'
    $appName = '彩条提取工作台.exe'
    $candidates = foreach ($directory in Get-ChildItem -LiteralPath $releaseRoot -Directory) {
        foreach ($relative in @($appName, (Join-Path '彩条提取工作台' $appName))) {
            $candidate = Join-Path $directory.FullName $relative
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                Get-Item -LiteralPath $candidate
            }
        }
    }
    $latest = $candidates | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if (-not $latest) {
        throw '没有找到已生成的工作台版本。请先打包程序。'
    }
    if ($ResolveOnly) {
        Write-Output $latest.FullName
        exit 0
    }
    Start-Process -FilePath $latest.FullName -WorkingDirectory $latest.DirectoryName
} catch {
    if ($ResolveOnly) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '工作台启动失败') | Out-Null
    exit 1
}
