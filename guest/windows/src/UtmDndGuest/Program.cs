using System.Runtime.InteropServices;
using System.Text.Json;

namespace UtmDndGuest;

internal static class Program
{
    [STAThread]
    private static async Task<int> Main(string[] args)
    {
        try
        {
            NativeMethods.SetProcessDpiAwarenessContext(NativeMethods.DpiAwarenessContextPerMonitorAwareV2);
        }
        catch (EntryPointNotFoundException)
        {
        }

        Options options;
        try
        {
            options = Options.Parse(args);
        }
        catch (ArgumentException)
        {
            return 64;
        }

        if (options.ProbePort)
        {
            try
            {
                await using PortConnection _ = PortConnection.Open(options.PortPath);
                return 0;
            }
            catch (System.ComponentModel.Win32Exception error)
            {
                return error.NativeErrorCode == 5 ? 5 : 2;
            }
        }

        var log = new Log(options.LogPath);
        if (options.DiagnoseTarget is not null)
        {
            try
            {
                DiagnosticTarget diagnostic = options.DiagnoseTarget;
                var request = new DropRequest(
                    Guid.NewGuid(),
                    diagnostic.Display,
                    diagnostic.X,
                    diagnostic.Y,
                    diagnostic.Width,
                    diagnostic.Height,
                    [new ExpectedFile("diagnostic.txt", 0)]);
                TargetResult result = new TargetResolver(true, log.Write).Resolve(request);
                File.WriteAllText(
                    diagnostic.OutputPath,
                    JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
                return 0;
            }
            catch (Exception error)
            {
                log.Write($"diagnostic target failed: {error}");
                return 1;
            }
        }

        using var instance = new Mutex(initiallyOwned: true, @"Local\com.utmapp.dnd.helper", out bool createdNew);
        if (!createdNew)
        {
            return 4;
        }

        using var stop = new CancellationTokenSource();
        AppDomain.CurrentDomain.ProcessExit += (_, _) => stop.Cancel();
        try
        {
            await new GuestApplication(options.PortPath, options.Debug, log).RunAsync(stop.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
        }
        return 0;
    }
}

internal sealed record DiagnosticTarget(
    int Display,
    double X,
    double Y,
    double Width,
    double Height,
    string OutputPath);

internal sealed record Options(
    string PortPath,
    bool Debug,
    bool ProbePort,
    string? LogPath,
    DiagnosticTarget? DiagnoseTarget)
{
    internal static Options Parse(string[] args)
    {
        string port = PortConnection.DefaultPath;
        string? log = null;
        bool debug = false;
        bool probe = false;
        DiagnosticTarget? diagnostic = null;
        for (int index = 0; index < args.Length; index++)
        {
            switch (args[index])
            {
                case "--debug":
                    debug = true;
                    break;
                case "--probe-port":
                    probe = true;
                    break;
                case "--port" when index + 1 < args.Length:
                    port = args[++index];
                    break;
                case "--log" when index + 1 < args.Length:
                    log = args[++index];
                    break;
                case "--diagnose-target" when index + 6 < args.Length:
                    diagnostic = new DiagnosticTarget(
                        int.Parse(args[++index], System.Globalization.CultureInfo.InvariantCulture),
                        double.Parse(args[++index], System.Globalization.CultureInfo.InvariantCulture),
                        double.Parse(args[++index], System.Globalization.CultureInfo.InvariantCulture),
                        double.Parse(args[++index], System.Globalization.CultureInfo.InvariantCulture),
                        double.Parse(args[++index], System.Globalization.CultureInfo.InvariantCulture),
                        args[++index]);
                    break;
                default:
                    throw new ArgumentException("invalid command line");
            }
        }
        if (probe && diagnostic is not null)
        {
            throw new ArgumentException("only one diagnostic mode can be selected");
        }
        return new Options(port, debug, probe, log, diagnostic);
    }
}
