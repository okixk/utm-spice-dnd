namespace UtmDndGuest;

internal sealed class Log
{
    private readonly object _gate = new();
    private readonly string _path;

    internal Log(string? path = null)
    {
        _path = path ?? System.IO.Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "UTM DnD Guest",
            "Logs",
            "utm-dnd-guest.log");
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(_path)!);
    }

    internal string FilePath => _path;

    internal void Write(string message)
    {
        string line = $"{DateTimeOffset.Now:O} utm-dnd-guest: {message}{Environment.NewLine}";
        lock (_gate)
        {
            try
            {
                File.AppendAllText(_path, line);
            }
            catch (IOException)
            {
                // Logging must never turn a safe fallback into a failed transfer.
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
    }
}
