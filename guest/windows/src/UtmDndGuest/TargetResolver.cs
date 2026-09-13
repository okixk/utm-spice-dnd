using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace UtmDndGuest;

internal sealed class TargetResolver(bool debug, Action<string> log)
{
    private readonly bool _debug = debug;
    private readonly Action<string> _log = log;

    internal TargetResult Resolve(DropRequest request)
    {
        try
        {
            (NativeMethods.Point point, DisplayMonitor monitor) = MapFramebufferPoint(request);
            nint leaf = NativeMethods.WindowFromPoint(point);
            if (leaf == 0)
            {
                return Fallback("window-from-point-failed", request.Display, point);
            }

            List<string> classes = WindowClassChain(leaf);
            nint root = NativeMethods.GetAncestor(leaf, NativeMethods.GaRoot);
            string rootClass = ClassName(root);
            var diagnostic = new TargetDiagnostic(
                Hwnd: root.ToInt64(),
                WindowClass: string.Join(" > ", classes),
                Display: request.Display,
                ScreenX: point.X,
                ScreenY: point.Y);

            if (IsDesktop(classes, rootClass))
            {
                string path = ValidateDestination(KnownFolders.Get(KnownFolders.Desktop));
                return new TargetResult("desktop", "high", path, Diagnostic: diagnostic);
            }

            if (!string.Equals(rootClass, "CabinetWClass", StringComparison.Ordinal) &&
                !string.Equals(rootClass, "ExploreWClass", StringComparison.Ordinal))
            {
                return Fallback("unsupported-window", request.Display, point, diagnostic);
            }

            return ResolveExplorer(root, request.Display, point, diagnostic);
        }
        catch (Exception error) when (error is not OutOfMemoryException and not StackOverflowException)
        {
            _log($"target resolution failed: {error.GetType().Name}: {error.Message}");
            return Fallback("target-resolution-failed", request.Display, null);
        }
    }

    private TargetResult ResolveExplorer(
        nint root,
        int display,
        NativeMethods.Point point,
        TargetDiagnostic diagnostic)
    {
        List<string> paths = RunSta(() => FindExplorerPaths(root), TimeSpan.FromMilliseconds(1500));
        diagnostic = diagnostic with { MatchingShellWindows = paths.Count };
        if (paths.Count == 0)
        {
            return Fallback("explorer-window-not-found-in-shellwindows", display, point, diagnostic);
        }
        if (paths.Count != 1)
        {
            // Multiple ShellWindows for one frame can be inactive Windows 11 tabs.
            // Without an exact active-tab identity, guessing would misplace a file.
            return Fallback("explorer-tabs-ambiguous", display, point, diagnostic);
        }

        try
        {
            string path = ValidateDestination(paths[0]);
            return new TargetResult("explorer", "high", path, Diagnostic: diagnostic);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        {
            _log($"rejected Explorer target: {error.Message}");
            return Fallback("explorer-location-rejected", display, point, diagnostic);
        }
    }

    private static List<string> FindExplorerPaths(nint targetRoot)
    {
        Type shellType = Type.GetTypeFromProgID("Shell.Application", throwOnError: true)!;
        object shellObject = Activator.CreateInstance(shellType)!;
        object? windowsObject = null;
        var paths = new List<string>();
        try
        {
            dynamic shell = shellObject;
            windowsObject = shell.Windows();
            dynamic windows = windowsObject;
            int count = Convert.ToInt32(windows.Count);
            for (int index = 0; index < count; index++)
            {
                object? windowObject = null;
                try
                {
                    windowObject = windows.Item(index);
                    if (windowObject is null)
                    {
                        continue;
                    }
                    dynamic window = windowObject;
                    nint hwnd = new(Convert.ToInt64(window.HWND));
                    nint root = NativeMethods.GetAncestor(hwnd, NativeMethods.GaRoot);
                    if (root != targetRoot || !NativeMethods.IsWindowVisible(root))
                    {
                        continue;
                    }

                    string? path = Convert.ToString(window.Document.Folder.Self.Path);
                    if (!string.IsNullOrWhiteSpace(path))
                    {
                        paths.Add(path);
                    }
                }
                catch (COMException)
                {
                    // A ShellWindows entry may disappear while tabs/windows close.
                }
                finally
                {
                    ReleaseComObject(windowObject);
                }
            }
        }
        finally
        {
            ReleaseComObject(windowsObject);
            ReleaseComObject(shellObject);
        }
        return paths.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
    }

    internal static string ValidateDestination(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.StartsWith("\\\\", StringComparison.Ordinal) ||
            value.StartsWith("\\\\?\\", StringComparison.Ordinal) || value.StartsWith("\\\\.\\", StringComparison.Ordinal))
        {
            throw new IOException("network, device, and namespace paths are not supported");
        }

        string fullPath = Path.GetFullPath(value);
        if (!Path.IsPathFullyQualified(fullPath) || !Directory.Exists(fullPath))
        {
            throw new IOException("destination is not an existing local directory");
        }
        if ((File.GetAttributes(fullPath) & FileAttributes.ReparsePoint) != 0)
        {
            throw new IOException("reparse-point destinations are not supported");
        }

        string canonical = CanonicalDirectoryPath(fullPath);
        string? root = Path.GetPathRoot(canonical);
        if (string.IsNullOrEmpty(root))
        {
            throw new IOException("destination has no local drive root");
        }
        DriveType driveType = new DriveInfo(root).DriveType;
        if (driveType is not DriveType.Fixed and not DriveType.Removable)
        {
            throw new IOException("destination is not on a local fixed or removable drive");
        }

        foreach (string forbidden in ForbiddenSystemDirectories())
        {
            if (IsSameOrChild(canonical, forbidden))
            {
                throw new UnauthorizedAccessException("system directories are not valid drop targets");
            }
        }

        string probe = Path.Combine(canonical, $".utm-dnd-write-{Guid.NewGuid():N}.tmp");
        using (new FileStream(
            probe,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            1,
            FileOptions.DeleteOnClose | FileOptions.WriteThrough))
        {
        }
        return canonical;
    }

    private TargetResult Fallback(
        string reason,
        int display,
        NativeMethods.Point? point,
        TargetDiagnostic? diagnostic = null)
    {
        string downloads = ValidateDestination(KnownFolders.Get(KnownFolders.Downloads));
        diagnostic ??= new TargetDiagnostic(
            Display: display,
            ScreenX: point?.X,
            ScreenY: point?.Y);
        return new TargetResult("fallback", "low", downloads, reason, _debug ? diagnostic : null);
    }

    internal static (NativeMethods.Point Point, DisplayMonitor Monitor) MapFramebufferPoint(DropRequest request)
    {
        List<DisplayMonitor> monitors = EnumerateMonitors();
        if (request.Display < 0 || request.Display >= monitors.Count)
        {
            throw new IOException("requested display is unavailable");
        }
        DisplayMonitor monitor = monitors[request.Display];
        int x = monitor.Rect.Left + (int)Math.Floor(request.X * monitor.Rect.Width / request.FramebufferWidth);
        int y = monitor.Rect.Top + (int)Math.Floor(request.Y * monitor.Rect.Height / request.FramebufferHeight);
        x = Math.Clamp(x, monitor.Rect.Left, monitor.Rect.Right - 1);
        y = Math.Clamp(y, monitor.Rect.Top, monitor.Rect.Bottom - 1);
        return (new NativeMethods.Point(x, y), monitor);
    }

    private static List<DisplayMonitor> EnumerateMonitors()
    {
        var monitors = new List<DisplayMonitor>();
        NativeMethods.MonitorEnumProc callback = (nint handle, nint hdc, ref NativeMethods.Rect rect, nint data) =>
        {
            var info = new NativeMethods.MonitorInfoEx
            {
                Size = (uint)Marshal.SizeOf<NativeMethods.MonitorInfoEx>(),
                Device = string.Empty,
            };
            if (NativeMethods.GetMonitorInfo(handle, ref info))
            {
                monitors.Add(new DisplayMonitor(
                    info.Monitor,
                    (info.Flags & NativeMethods.MonitorInfoPrimary) != 0,
                    info.Device));
            }
            return true;
        };
        if (!NativeMethods.EnumDisplayMonitors(0, 0, callback, 0) || monitors.Count == 0)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "cannot enumerate displays");
        }
        return monitors
            .OrderByDescending(monitor => monitor.Primary)
            .ThenBy(monitor => monitor.Rect.Top)
            .ThenBy(monitor => monitor.Rect.Left)
            .ToList();
    }

    private static bool IsDesktop(IReadOnlyCollection<string> classes, string rootClass)
    {
        bool shellClass = classes.Any(value => value is "SysListView32" or "SHELLDLL_DefView" or "WorkerW" or "Progman");
        bool shellRoot = rootClass is "WorkerW" or "Progman" or "#32769";
        return shellClass && shellRoot;
    }

    private static List<string> WindowClassChain(nint start)
    {
        var classes = new List<string>();
        nint current = start;
        for (int count = 0; current != 0 && count < 32; count++)
        {
            classes.Add(ClassName(current));
            nint parent = NativeMethods.GetParent(current);
            if (parent == current)
            {
                break;
            }
            current = parent;
        }
        return classes;
    }

    private static string ClassName(nint hwnd)
    {
        var buffer = new StringBuilder(256);
        return NativeMethods.GetClassName(hwnd, buffer, buffer.Capacity) > 0 ? buffer.ToString() : "?";
    }

    private static string CanonicalDirectoryPath(string path)
    {
        using Microsoft.Win32.SafeHandles.SafeFileHandle handle = NativeMethods.CreateFile(
            path,
            0,
            NativeMethods.FileShareRead | NativeMethods.FileShareWrite | NativeMethods.FileShareDelete,
            0,
            NativeMethods.OpenExisting,
            NativeMethods.FileFlagBackupSemantics,
            0);
        if (handle.IsInvalid)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "cannot open destination directory");
        }
        var buffer = new StringBuilder(32768);
        uint length = NativeMethods.GetFinalPathNameByHandle(
            handle,
            buffer,
            (uint)buffer.Capacity,
            NativeMethods.FileNameNormalized | NativeMethods.VolumeNameDos);
        if (length == 0 || length >= buffer.Capacity)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "cannot canonicalize destination directory");
        }
        string result = buffer.ToString();
        return result.StartsWith("\\\\?\\", StringComparison.Ordinal) ? result[4..] : result;
    }

    private static IEnumerable<string> ForbiddenSystemDirectories()
    {
        string? windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        string? programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        string? programFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        string? commonData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
        return new[] { windows, programFiles, programFilesX86, commonData }
            .Where(path => !string.IsNullOrWhiteSpace(path))!;
    }

    private static bool IsSameOrChild(string candidate, string parent)
    {
        string normalizedCandidate = Path.TrimEndingDirectorySeparator(Path.GetFullPath(candidate));
        string normalizedParent = Path.TrimEndingDirectorySeparator(Path.GetFullPath(parent));
        return string.Equals(normalizedCandidate, normalizedParent, StringComparison.OrdinalIgnoreCase) ||
            normalizedCandidate.StartsWith(normalizedParent + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    private static T RunSta<T>(Func<T> action, TimeSpan timeout)
    {
        T? result = default;
        Exception? failure = null;
        using var completed = new ManualResetEventSlim();
        var thread = new Thread(() =>
        {
            try
            {
                result = action();
            }
            catch (Exception error)
            {
                failure = error;
            }
            finally
            {
                completed.Set();
            }
        })
        {
            IsBackground = true,
            Name = "UTM DnD Shell resolver",
        };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        if (!completed.Wait(timeout))
        {
            throw new TimeoutException("ShellWindows target lookup timed out");
        }
        if (failure is not null)
        {
            throw failure;
        }
        return result!;
    }

    private static void ReleaseComObject(object? value)
    {
        if (value is not null && Marshal.IsComObject(value))
        {
            Marshal.FinalReleaseComObject(value);
        }
    }
}
