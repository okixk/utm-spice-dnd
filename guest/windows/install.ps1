[CmdletBinding()]
param(
    [string]$BinaryPath,
    [switch]$DebugLogging
)

$ErrorActionPreference = 'Stop'
$taskName = 'UTM DnD Guest'
$installDirectory = Join-Path $env:LOCALAPPDATA 'Programs\UTM DnD Guest'
$installedBinary = Join-Path $installDirectory 'utm-dnd-guest.exe'
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Fail([string]$Message) {
    throw "install.ps1: $Message"
}

$currentVersion = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
$rid = switch ($architecture) {
    'Arm64' { 'win-arm64' }
    'X64' { 'win-x64' }
    default { Fail "unsupported Windows architecture: $architecture" }
}
if ([Environment]::OSVersion.Version.Major -lt 10) {
    Fail 'Windows 10 or newer is required'
}

if (-not $BinaryPath) {
    $BinaryPath = Join-Path $PSScriptRoot "artifacts\$rid\utm-dnd-guest.exe"
}
$BinaryPath = [IO.Path]::GetFullPath($BinaryPath)
if (-not (Test-Path -LiteralPath $BinaryPath -PathType Leaf)) {
    Fail "published $rid helper was not found at $BinaryPath"
}

$vdservice = Get-Service -Name vdservice -ErrorAction SilentlyContinue
if (-not $vdservice) {
    Fail 'SPICE VDAgent service (vdservice) is not installed; install UTM Windows Guest Tools first'
}
$virtioSerial = Get-CimInstance Win32_PnPSignedDriver | Where-Object DeviceName -eq 'VirtIO Serial Driver' | Select-Object -First 1
if (-not $virtioSerial) {
    Fail 'the Red Hat VirtIO Serial Driver is not installed'
}
$vdagent = Get-Process -Name vdagent -ErrorAction SilentlyContinue | Where-Object SessionId -ne 0 | Select-Object -First 1
if (-not $vdagent) {
    Write-Warning 'vdagent.exe is not running in an interactive session; ordinary SPICE payload delivery is not currently available'
}

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Start-Sleep -Seconds 1
}
Get-Process -Name utm-dnd-guest -ErrorAction SilentlyContinue |
    Where-Object Path -eq $installedBinary |
    Stop-Process -Force
Start-Sleep -Milliseconds 300

New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Copy-Item -LiteralPath $BinaryPath -Destination $installedBinary -Force

$probe = Start-Process -FilePath $installedBinary -ArgumentList '--probe-port' -Wait -PassThru
$probeExit = $probe.ExitCode
if ($probeExit -eq 5) {
    Fail 'the current user cannot open \\.\Global\com.utmapp.dnd.0; no ACL changes were made'
}
if ($probeExit -ne 0) {
    Fail 'the dedicated port \\.\Global\com.utmapp.dnd.0 is absent or unavailable; start the patched UTM build first'
}

$action = if ($DebugLogging) {
    New-ScheduledTaskAction -Execute $installedBinary -Argument '--debug' -WorkingDirectory $installDirectory
} else {
    New-ScheduledTaskAction -Execute $installedBinary -WorkingDirectory $installDirectory
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Interactive target resolver for UTM SPICE host-to-guest drag and drop.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2

$running = Get-CimInstance Win32_Process -Filter "Name = 'utm-dnd-guest.exe'" -ErrorAction SilentlyContinue |
    Where-Object ExecutablePath -eq $installedBinary |
    Select-Object -First 1
if (-not $running) {
    Fail 'the scheduled helper did not remain running; inspect its LocalAppData log'
}

Write-Output "Installed UTM DnD Guest 0.1.0 for $($currentVersion.ProductName) $($currentVersion.DisplayVersion) build $($currentVersion.CurrentBuild).$($currentVersion.UBR) ($architecture)."
Write-Output "Binary: $installedBinary"
Write-Output "Scheduled task: \$taskName (interactive token, limited run level)"
Write-Output "VirtIO Serial: $($virtioSerial.DriverVersion); vdservice: $($vdservice.Status); helper PID/session: $($running.ProcessId)/$($running.SessionId)"
