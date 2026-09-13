[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'UTM DnD Guest'
$installDirectory = Join-Path $env:LOCALAPPDATA 'Programs\UTM DnD Guest'
$installedBinary = Join-Path $installDirectory 'utm-dnd-guest.exe'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Start-Sleep -Seconds 1
}
Get-Process -Name utm-dnd-guest -ErrorAction SilentlyContinue |
    Where-Object Path -eq $installedBinary |
    Stop-Process -Force

$expectedParent = Join-Path $env:LOCALAPPDATA 'Programs'
if ((Test-Path -LiteralPath $installDirectory) -and
    [IO.Path]::GetFullPath($installDirectory).StartsWith([IO.Path]::GetFullPath($expectedParent) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $installDirectory -Recurse -Force
}

Write-Output 'Removed the UTM DnD Guest scheduled task and installed executable.'
Write-Output 'SPICE/UTM Guest Tools, VirtIO drivers, received files, and LocalAppData logs were left untouched.'
