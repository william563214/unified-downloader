#requires -Version 5.1
<#
定期重啟 Windows 工作列
工作列由檔案總管負責 卡住不自動隱藏時把檔案總管關掉再拉起來
檔案總管視窗會一起關閉 桌面會閃一下
電腦鎖定或前景全螢幕時會跳過 若要強制執行加上 -Force

立即重啟一次
  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-taskbar.ps1

每 30 分鐘重啟並登錄排程 約一分鐘後第一次執行
  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-taskbar.ps1 -Install

改間隔為 60 分鐘
  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-taskbar.ps1 -Install -IntervalMinutes 60

只切換自動隱藏 不關閉檔案總管
  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-taskbar.ps1 -Install -Mode Unstick

這個視窗留著自己循環 不登錄排程
  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-taskbar.ps1 -Loop -IntervalMinutes 30

移除排程
  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-taskbar.ps1 -Uninstall

查看排程
  powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-taskbar.ps1 -Status
#>
[CmdletBinding()]
param(
    [ValidateSet("Restart", "Unstick")]
    [string]$Mode = "Restart",

    [int]$IntervalMinutes = 30,

    [string]$TaskName = "Restart-Taskbar",

    [switch]$Install,

    [switch]$Uninstall,

    [switch]$Status,

    [switch]$Loop,

    [switch]$Scheduled,

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$min_interval_minutes = 5
$max_interval_minutes = 1439
$explorer_wait_seconds = 8
$unstick_pause_ms = 400
$log_max_bytes = 262144
$mutex_name = "Local\RestartTaskbar"
$cooldown_name = "last-restart.txt"
$log_dir_name = "RestartTaskbar"
$log_file_name = "restart-taskbar.log"

$native_source = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class native_win {
    public const int abs_autohide = 0x1;
    public const int abs_alwaysontop = 0x2;
    public const uint abm_getstate = 0x4;
    public const uint abm_setstate = 0xA;
    public const uint monitor_defaulttonearest = 2;
    public const uint desktop_switchdesktop = 0x0100;
    public const int fullscreen_slack = 8;

    [StructLayout(LayoutKind.Sequential)]
    public struct rect {
        public int left;
        public int top;
        public int right;
        public int bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct point {
        public int x;
        public int y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct appbardata {
        public int cbSize;
        public IntPtr hWnd;
        public uint uCallbackMessage;
        public uint uEdge;
        public rect rc;
        public IntPtr lParam;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    public struct monitorinfo {
        public int cbSize;
        public rect rcMonitor;
        public rect rcWork;
        public uint dwFlags;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out rect lpRect);

    [DllImport("user32.dll")]
    public static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern bool GetMonitorInfo(IntPtr hMonitor, ref monitorinfo lpmi);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool CloseDesktop(IntPtr hDesktop);

    [DllImport("shell32.dll")]
    public static extern IntPtr SHAppBarMessage(uint dwMessage, ref appbardata pData);

    public static bool is_locked() {
        IntPtr desk = OpenInputDesktop(0, false, desktop_switchdesktop);
        if (desk == IntPtr.Zero) {
            return true;
        }
        CloseDesktop(desk);
        return false;
    }

    public static string window_class(IntPtr hwnd) {
        StringBuilder sb = new StringBuilder(256);
        GetClassName(hwnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static bool is_fullscreen() {
        IntPtr hwnd = GetForegroundWindow();
        if (hwnd == IntPtr.Zero) {
            return false;
        }
        string cls = window_class(hwnd);
        if (cls == "Progman" || cls == "WorkerW" || cls == "Shell_TrayWnd" || cls == "Shell_SecondaryTrayWnd") {
            return false;
        }
        rect window;
        if (!GetWindowRect(hwnd, out window)) {
            return false;
        }
        IntPtr monitor = MonitorFromWindow(hwnd, monitor_defaulttonearest);
        if (monitor == IntPtr.Zero) {
            return false;
        }
        monitorinfo info = new monitorinfo();
        info.cbSize = Marshal.SizeOf(typeof(monitorinfo));
        if (!GetMonitorInfo(monitor, ref info)) {
            return false;
        }
        return window.left <= info.rcMonitor.left + fullscreen_slack
            && window.top <= info.rcMonitor.top + fullscreen_slack
            && window.right >= info.rcMonitor.right - fullscreen_slack
            && window.bottom >= info.rcMonitor.bottom - fullscreen_slack;
    }

    static appbardata new_appbardata() {
        appbardata data = new appbardata();
        data.cbSize = Marshal.SizeOf(typeof(appbardata));
        data.hWnd = FindWindow("Shell_TrayWnd", null);
        return data;
    }

    public static bool is_autohide() {
        appbardata data = new_appbardata();
        IntPtr result = SHAppBarMessage(abm_getstate, ref data);
        return (result.ToInt32() & abs_autohide) != 0;
    }

    public static void set_autohide(bool enabled) {
        appbardata data = new_appbardata();
        int flags = abs_alwaysontop;
        if (enabled) {
            flags = flags | abs_autohide;
        }
        data.lParam = new IntPtr(flags);
        SHAppBarMessage(abm_setstate, ref data);
    }
}
'@

function Write-TaskbarLog {
    param([string]$Message)

    $dir = Join-Path $env:LOCALAPPDATA $log_dir_name
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $path = Join-Path $dir $log_file_name
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Length -gt $log_max_bytes)) {
        $kept = @(Get-Content -LiteralPath $path -Tail 200 -Encoding UTF8)
        $bom = New-Object System.Text.UTF8Encoding $true
        [System.IO.File]::WriteAllLines($path, $kept, $bom)
    }
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    if (-not (Test-Path -LiteralPath $path)) {
        $bom = New-Object System.Text.UTF8Encoding $true
        [System.IO.File]::WriteAllText($path, $line + [Environment]::NewLine, $bom)
    } else {
        $plain = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::AppendAllText($path, $line + [Environment]::NewLine, $plain)
    }
    Write-Host $line
}

function Get-ClampedInterval {
    param([int]$Minutes)

    if ($Minutes -lt $min_interval_minutes) {
        return $min_interval_minutes
    }
    if ($Minutes -gt $max_interval_minutes) {
        return $max_interval_minutes
    }
    return $Minutes
}

function Get-StampPath {
    $dir = Join-Path $env:LOCALAPPDATA $log_dir_name
    return (Join-Path $dir $cooldown_name)
}

function Test-RestartCooldown {
    $path = Get-StampPath
    if (-not (Test-Path -LiteralPath $path)) {
        return $false
    }
    $text = ([System.IO.File]::ReadAllText($path)).Trim()
    $parsed = [datetime]::MinValue
    if (-not [datetime]::TryParse($text, [ref]$parsed)) {
        return $false
    }
    $gap = [Math]::Max($IntervalMinutes - 1, 1)
    return (((Get-Date) - $parsed).TotalMinutes -lt $gap)
}

function Save-RestartStamp {
    $dir = Join-Path $env:LOCALAPPDATA $log_dir_name
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $plain = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText((Get-StampPath), (Get-Date).ToString("o"), $plain)
}

function Invoke-UnstickTaskbar {
    if (-not [native_win]::is_autohide()) {
        Write-TaskbarLog "自動隱藏目前是關閉的，沒有變更設定。若要重啟檔案總管，請改用 -Mode Restart。"
        return
    }
    Write-TaskbarLog "切換自動隱藏，解開卡住的工作列。"
    [native_win]::set_autohide($false)
    Start-Sleep -Milliseconds $unstick_pause_ms
    [native_win]::set_autohide($true)
    Write-TaskbarLog "已恢復自動隱藏。"
}

function Invoke-RestartExplorer {
    Write-TaskbarLog "開始重啟檔案總管。開啟中的檔案總管視窗會關閉，桌面會閃一下。"
    $explorer_exe = Join-Path $env:SystemRoot "explorer.exe"
    Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds($explorer_wait_seconds)
    $back = $null
    do {
        Start-Sleep -Milliseconds 400
        $back = Get-Process -Name explorer -ErrorAction SilentlyContinue | Select-Object -First 1
    } while (-not $back -and (Get-Date) -lt $deadline)
    if (-not $back) {
        Start-Process -FilePath $explorer_exe
        Start-Sleep -Seconds 1
        $back = Get-Process -Name explorer -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if ($back) {
        Write-TaskbarLog "工作列已重啟。"
    } else {
        Write-TaskbarLog "檔案總管沒有回來，請手動執行 explorer.exe。"
    }
}

function Invoke-TaskbarFix {
    if (-not $Force) {
        if ([native_win]::is_locked()) {
            Write-TaskbarLog "電腦已鎖定，這次略過。"
            return
        }
        if ([native_win]::is_fullscreen()) {
            Write-TaskbarLog "前景是全螢幕，這次略過。"
            return
        }
        if ($Scheduled -and (Test-RestartCooldown)) {
            Write-TaskbarLog "距離上次太近，這次略過。"
            return
        }
    }

    $owned = $false
    $mutex = New-Object System.Threading.Mutex($false, $mutex_name)
    try {
        try {
            $owned = $mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $owned = $true
        }
        if (-not $owned) {
            Write-TaskbarLog "已有另一個重啟在進行，這次略過。"
            return
        }
        if ($Mode -eq "Unstick") {
            Invoke-UnstickTaskbar
        } else {
            Invoke-RestartExplorer
        }
        Save-RestartStamp
    } finally {
        if ($owned) {
            try {
                [void]$mutex.ReleaseMutex()
            } catch {
                # mutex already released
            }
        }
        $mutex.Dispose()
    }
}

function New-TaskXml {
    param(
        [string]$PowerShellExe,
        [string]$Arguments,
        [string]$UserName,
        [int]$Minutes
    )

    $exe_xml = [System.Security.SecurityElement]::Escape($PowerShellExe)
    $args_xml = [System.Security.SecurityElement]::Escape($Arguments)
    $user_xml = [System.Security.SecurityElement]::Escape($UserName)
    $start = (Get-Date).AddMinutes(1).ToString("yyyy-MM-ddTHH:mm:ss")
    $interval = "PT${Minutes}M"
    $description = [System.Security.SecurityElement]::Escape("定期重啟工作列，讓自動隱藏恢復")

    @"
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$description</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>$interval</Interval>
        <Duration>P9999D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>$start</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$user_xml</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$exe_xml</Command>
      <Arguments>$args_xml</Arguments>
    </Exec>
  </Actions>
</Task>
"@
}

function Install-RestartTaskbarTask {
    if ([string]::IsNullOrWhiteSpace($TaskName) -or $TaskName -match '[\\/]') {
        throw "排程名稱不能空白，也不能含有斜線。"
    }
    if ($PSCommandPath -match '"') {
        throw "腳本路徑含有雙引號，請搬到一般資料夾再登錄。"
    }

    $minutes = Get-ClampedInterval -Minutes $IntervalMinutes
    if ($minutes -ne $IntervalMinutes) {
        Write-TaskbarLog "間隔需介於 $min_interval_minutes 到 $max_interval_minutes 分鐘，已改為 $minutes。"
    }

    $powershell_exe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Mode $Mode -IntervalMinutes $minutes -Scheduled"
    $user_name = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $xml = New-TaskXml -PowerShellExe $powershell_exe -Arguments $arguments -UserName $user_name -Minutes $minutes
    Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force | Out-Null
    $log_path = Join-Path (Join-Path $env:LOCALAPPDATA $log_dir_name) $log_file_name
    Write-TaskbarLog "已登錄排程「$TaskName」，每 $minutes 分鐘以 $Mode 處理工作列。約一分鐘後第一次執行。"
    Write-TaskbarLog "日誌在 $log_path。搬移這個腳本後要重新執行 -Install。"
}

function Uninstall-RestartTaskbarTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-TaskbarLog "沒有排程 $TaskName。"
        return
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-TaskbarLog "已移除排程 $TaskName。"
}

function Show-TaskStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-TaskbarLog "尚未登錄排程 $TaskName。"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-TaskbarLog "排程 $TaskName 狀態 $($task.State)。"
    if ($info.LastRunTime.Year -lt 2000) {
        Write-TaskbarLog "尚未執行過。"
    } else {
        $when = $info.LastRunTime.ToString("yyyy-MM-dd HH:mm:ss")
        Write-TaskbarLog "上次 $when，結果 $($info.LastTaskResult)。"
    }
    if ($info.NextRunTime.Year -ge 2000) {
        Write-TaskbarLog ("下次 " + $info.NextRunTime.ToString("yyyy-MM-dd HH:mm:ss") + "。")
    }
    $log_path = Join-Path (Join-Path $env:LOCALAPPDATA $log_dir_name) $log_file_name
    Write-TaskbarLog "日誌在 $log_path。"
}

if ($env:OS -ne "Windows_NT") {
    [Console]::Error.WriteLine("此腳本只能在 Windows 執行。")
    exit 1
}

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # console encoding stays as is
}

try {
    Add-Type -TypeDefinition $native_source -Language CSharp -ErrorAction Stop
} catch {
    if (-not ("native_win" -as [type])) {
        throw
    }
}

$action_count = 0
if ($Install) { $action_count += 1 }
if ($Uninstall) { $action_count += 1 }
if ($Status) { $action_count += 1 }
if ($Loop) { $action_count += 1 }
if ($action_count -gt 1) {
    Write-TaskbarLog "請只選一種動作：-Install、-Uninstall、-Status 或 -Loop。"
    exit 1
}

try {
    if ($Uninstall) {
        Uninstall-RestartTaskbarTask
        exit 0
    }
    if ($Status) {
        Show-TaskStatus
        exit 0
    }
    if ($Install) {
        Install-RestartTaskbarTask
        exit 0
    }
    if ($Loop) {
        $minutes = Get-ClampedInterval -Minutes $IntervalMinutes
        if ($minutes -ne $IntervalMinutes) {
            Write-TaskbarLog "間隔需介於 $min_interval_minutes 到 $max_interval_minutes 分鐘，已改為 $minutes。"
        }
        Write-TaskbarLog "開始循環，每 $minutes 分鐘處理一次。關閉這個視窗就會停止。"
        while ($true) {
            Invoke-TaskbarFix
            Start-Sleep -Seconds ($minutes * 60)
        }
    }
    Invoke-TaskbarFix
    exit 0
} catch {
    $text = $_.Exception.Message
    try {
        Write-TaskbarLog "執行失敗：$text"
    } catch {
        Write-Host "執行失敗：$text"
    }
    exit 1
}
