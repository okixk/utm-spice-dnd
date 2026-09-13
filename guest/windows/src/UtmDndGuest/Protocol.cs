using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace UtmDndGuest;

internal sealed class ProtocolException(string message) : Exception(message);

internal abstract record ControlMessage(Guid TransferId);
internal sealed record DropControlMessage(DropRequest Request) : ControlMessage(Request.TransferId);
internal sealed record CancelControlMessage(Guid Id) : ControlMessage(Id);

internal static partial class Protocol
{
    internal const int Version = 1;
    internal const int MaximumMessageBytes = 64 * 1024;
    internal const int MaximumFiles = 100;

    private static readonly HashSet<string> DropFields =
    [
        "type", "version", "transferId", "display", "x", "y",
        "framebufferWidth", "framebufferHeight", "files",
    ];

    private static readonly HashSet<string> CancelFields = ["type", "version", "transferId"];
    private static readonly HashSet<string> FileFields = ["name", "size"];

    internal static ControlMessage Parse(ReadOnlySpan<byte> utf8)
    {
        if (utf8.Length == 0 || utf8.Length + 1 > MaximumMessageBytes)
        {
            throw new ProtocolException("invalid message size");
        }

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(utf8.ToArray(), new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 8,
            });
        }
        catch (JsonException)
        {
            throw new ProtocolException("invalid JSON");
        }

        using (document)
        {
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                throw new ProtocolException("message must be an object");
            }

            string type = RequiredString(root, "type");
            return type switch
            {
                "drop" => ParseDrop(root),
                "cancel" => ParseCancel(root),
                _ => throw new ProtocolException("unsupported message type"),
            };
        }
    }

    private static DropControlMessage ParseDrop(JsonElement root)
    {
        ValidateFields(root, DropFields, "invalid drop message fields");
        ValidateVersion(root);
        Guid transferId = RequiredGuid(root, "transferId");
        int display = RequiredInt32(root, "display");
        if (display is < 0 or > 31)
        {
            throw new ProtocolException("invalid display");
        }

        double x = RequiredFiniteDouble(root, "x");
        double y = RequiredFiniteDouble(root, "y");
        double width = RequiredFiniteDouble(root, "framebufferWidth");
        double height = RequiredFiniteDouble(root, "framebufferHeight");
        if (width <= 0 || height <= 0 || x < 0 || y < 0 || x >= width || y >= height)
        {
            throw new ProtocolException("drop coordinates are outside the framebuffer");
        }

        if (!root.TryGetProperty("files", out JsonElement rawFiles) || rawFiles.ValueKind != JsonValueKind.Array)
        {
            throw new ProtocolException("invalid files list");
        }

        int count = rawFiles.GetArrayLength();
        if (count is < 1 or > MaximumFiles)
        {
            throw new ProtocolException("invalid files list");
        }

        var files = new List<ExpectedFile>(count);
        foreach (JsonElement rawFile in rawFiles.EnumerateArray())
        {
            if (rawFile.ValueKind != JsonValueKind.Object)
            {
                throw new ProtocolException("invalid file entry");
            }
            ValidateFields(rawFile, FileFields, "invalid file entry fields");
            string name = ValidateBasename(RequiredString(rawFile, "name"));
            if (!rawFile.TryGetProperty("size", out JsonElement sizeElement) ||
                sizeElement.ValueKind != JsonValueKind.Number ||
                !sizeElement.TryGetInt64(out long size) || size < 0)
            {
                throw new ProtocolException("invalid file size");
            }
            files.Add(new ExpectedFile(name, size));
        }

        return new DropControlMessage(new DropRequest(transferId, display, x, y, width, height, files));
    }

    private static CancelControlMessage ParseCancel(JsonElement root)
    {
        ValidateFields(root, CancelFields, "invalid cancel message fields");
        ValidateVersion(root);
        return new CancelControlMessage(RequiredGuid(root, "transferId"));
    }

    internal static string ValidateBasename(string name)
    {
        if (string.IsNullOrWhiteSpace(name) || name is "." or ".." || name[^1] is ' ' or '.')
        {
            throw new ProtocolException("invalid file name");
        }
        if (Encoding.UTF8.GetByteCount(name) > 255 || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
            name.Contains('/') || name.Contains('\\') || Path.GetFileName(name) != name)
        {
            throw new ProtocolException("file name must be a Windows basename");
        }

        string stem = Path.GetFileNameWithoutExtension(name).TrimEnd(' ', '.');
        string reservedCandidate = stem.Split('.')[0];
        if (ReservedDeviceName().IsMatch(reservedCandidate))
        {
            throw new ProtocolException("reserved Windows file name");
        }
        return name;
    }

    internal static bool NameMatchesReceived(string expected, string candidate)
    {
        string normalizedExpected = expected.Normalize(NormalizationForm.FormC);
        string normalizedCandidate = candidate.Normalize(NormalizationForm.FormC);
        if (string.Equals(normalizedExpected, normalizedCandidate, StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        string extension = Path.GetExtension(normalizedExpected);
        string stem = Path.GetFileNameWithoutExtension(normalizedExpected);
        return Regex.IsMatch(
            normalizedCandidate,
            $"^{Regex.Escape(stem)} \\([1-9][0-9]*\\){Regex.Escape(extension)}$",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    }

    internal static string DuplicateName(string name, int number)
    {
        string extension = Path.GetExtension(name);
        string stem = Path.GetFileNameWithoutExtension(name);
        return $"{stem} ({number}){extension}";
    }

    private static void ValidateVersion(JsonElement root)
    {
        if (!root.TryGetProperty("version", out JsonElement element) ||
            element.ValueKind != JsonValueKind.Number ||
            !element.TryGetInt32(out int version) || version != Version)
        {
            throw new ProtocolException("unsupported protocol version");
        }
    }

    private static void ValidateFields(JsonElement element, HashSet<string> expected, string error)
    {
        var actual = new HashSet<string>(StringComparer.Ordinal);
        foreach (JsonProperty property in element.EnumerateObject())
        {
            if (!actual.Add(property.Name))
            {
                throw new ProtocolException(error);
            }
        }
        if (!actual.SetEquals(expected))
        {
            throw new ProtocolException(error);
        }
    }

    private static string RequiredString(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.String)
        {
            throw new ProtocolException($"invalid {name}");
        }
        return value.GetString()!;
    }

    private static Guid RequiredGuid(JsonElement root, string name)
    {
        if (!Guid.TryParseExact(RequiredString(root, name), "D", out Guid value))
        {
            throw new ProtocolException("invalid transferId");
        }
        return value;
    }

    private static int RequiredInt32(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out JsonElement value) ||
            value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out int result))
        {
            throw new ProtocolException($"invalid {name}");
        }
        return result;
    }

    private static double RequiredFiniteDouble(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out JsonElement value) ||
            value.ValueKind != JsonValueKind.Number || !value.TryGetDouble(out double result) ||
            !double.IsFinite(result))
        {
            throw new ProtocolException($"invalid {name}");
        }
        return result;
    }

    [GeneratedRegex("^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex ReservedDeviceName();
}
