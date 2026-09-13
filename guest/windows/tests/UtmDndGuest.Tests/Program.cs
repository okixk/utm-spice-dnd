using System.Text;
using UtmDndGuest;

var tests = new (string Name, Action Run)[]
{
    ("valid drop", ValidDrop),
    ("strict schema", StrictSchema),
    ("host destination rejected", HostDestinationRejected),
    ("coordinate bounds", CoordinateBounds),
    ("invalid file metadata", InvalidFileMetadata),
    ("cancel", Cancel),
    ("SPICE duplicate match", SpiceDuplicateMatch),
    ("Unicode normalization", UnicodeNormalization),
    ("Windows reserved basename", ReservedBasename),
    ("known folders", KnownFolderPaths),
    ("writable local target", WritableLocalTarget),
    ("collision-safe move", CollisionSafeMove),
    ("DPI-aware display mapping", DisplayMapping),
};

int failures = 0;
foreach ((string name, Action run) in tests)
{
    try
    {
        run();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception error)
    {
        failures++;
        Console.Error.WriteLine($"FAIL {name}: {error}");
    }
}
Console.WriteLine($"{tests.Length - failures}/{tests.Length} tests passed");
return failures == 0 ? 0 : 1;

static string ValidJson(string? id = null) => $$"""
{"type":"drop","version":1,"transferId":"{{id ?? Guid.NewGuid().ToString("D")}}","display":0,"x":100,"y":200,"framebufferWidth":1280,"framebufferHeight":800,"files":[{"name":"hello world.txt","size":12}]}
""";

static void ValidDrop()
{
    var parsed = (DropControlMessage)Protocol.Parse(Encoding.UTF8.GetBytes(ValidJson()));
    Equal("hello world.txt", parsed.Request.Files[0].Name);
    Equal(12L, parsed.Request.Files[0].Size);
}

static void StrictSchema()
{
    Throws<ProtocolException>(() => Protocol.Parse(Encoding.UTF8.GetBytes(ValidJson().Replace("\"files\"", "\"extra\":1,\"files\""))));
    Throws<ProtocolException>(() => Protocol.Parse(Encoding.UTF8.GetBytes(ValidJson().Replace("\"version\":1", "\"version\":2"))));
}

static void HostDestinationRejected()
{
    Throws<ProtocolException>(() => Protocol.Parse(Encoding.UTF8.GetBytes(ValidJson().Replace("\"files\"", "\"destination\":\"C:\\\\Windows\",\"files\""))));
}

static void CoordinateBounds()
{
    Throws<ProtocolException>(() => Protocol.Parse(Encoding.UTF8.GetBytes(ValidJson().Replace("\"x\":100", "\"x\":1280"))));
}

static void InvalidFileMetadata()
{
    Throws<ProtocolException>(() => Protocol.Parse(Encoding.UTF8.GetBytes(ValidJson().Replace("hello world.txt", "..\\\\bad.txt"))));
    Throws<ProtocolException>(() => Protocol.Parse(Encoding.UTF8.GetBytes(ValidJson().Replace("\"size\":12", "\"size\":-1"))));
}

static void Cancel()
{
    Guid id = Guid.NewGuid();
    var parsed = (CancelControlMessage)Protocol.Parse(Encoding.UTF8.GetBytes($"{{\"type\":\"cancel\",\"version\":1,\"transferId\":\"{id:D}\"}}"));
    Equal(id, parsed.TransferId);
}

static void SpiceDuplicateMatch()
{
    True(Protocol.NameMatchesReceived("test.txt", "test (1).txt"));
    True(Protocol.NameMatchesReceived("archive.tar.gz", "archive.tar (2).gz"));
    False(Protocol.NameMatchesReceived("test.txt", "other (1).txt"));
}

static void UnicodeNormalization()
{
    True(Protocol.NameMatchesReceived("caf\u00e9.txt", "cafe\u0301.txt"));
}

static void ReservedBasename()
{
    Throws<ProtocolException>(() => Protocol.ValidateBasename("CON.txt"));
    Throws<ProtocolException>(() => Protocol.ValidateBasename("trailing. "));
}

static void KnownFolderPaths()
{
    True(Directory.Exists(KnownFolders.Get(KnownFolders.Desktop)));
    True(Directory.Exists(KnownFolders.Get(KnownFolders.Documents)));
    True(Directory.Exists(KnownFolders.Get(KnownFolders.Downloads)));
}

static void WritableLocalTarget()
{
    string root = MakeTempDirectory();
    try
    {
        Equal(Path.TrimEndingDirectorySeparator(root), Path.TrimEndingDirectorySeparator(TargetResolver.ValidateDestination(root)));
        Throws<IOException>(() => TargetResolver.ValidateDestination(@"\\server\share"));
    }
    finally
    {
        Directory.Delete(root, recursive: true);
    }
}

static void CollisionSafeMove()
{
    string root = MakeTempDirectory();
    string source = Path.Combine(root, "incoming.tmp");
    string target = Path.Combine(root, "target");
    Directory.CreateDirectory(target);
    File.WriteAllText(source, "new");
    File.WriteAllText(Path.Combine(target, "test.txt"), "old");
    try
    {
        string moved = FileTransferCoordinator.MoveSafely(source, target, "test.txt");
        Equal("test (1).txt", Path.GetFileName(moved));
        Equal("old", File.ReadAllText(Path.Combine(target, "test.txt")));
        Equal("new", File.ReadAllText(moved));
        False(File.Exists(source));
    }
    finally
    {
        Directory.Delete(root, recursive: true);
    }
}

static void DisplayMapping()
{
    var request = new DropRequest(Guid.NewGuid(), 0, 50, 25, 100, 50, [new ExpectedFile("x", 0)]);
    (NativeMethods.Point point, DisplayMonitor monitor) = TargetResolver.MapFramebufferPoint(request);
    True(point.X >= monitor.Rect.Left && point.X < monitor.Rect.Right);
    True(point.Y >= monitor.Rect.Top && point.Y < monitor.Rect.Bottom);
}

static string MakeTempDirectory()
{
    string path = Path.Combine(Path.GetTempPath(), $"utm-dnd-tests-{Guid.NewGuid():N}");
    Directory.CreateDirectory(path);
    return path;
}

static void True(bool value)
{
    if (!value) throw new Exception("expected true");
}

static void False(bool value) => True(!value);

static void Equal<T>(T expected, T actual)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new Exception($"expected {expected}, got {actual}");
    }
}

static void Throws<T>(Action action) where T : Exception
{
    try
    {
        action();
    }
    catch (T)
    {
        return;
    }
    throw new Exception($"expected {typeof(T).Name}");
}
