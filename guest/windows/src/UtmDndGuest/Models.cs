using System.Text.Json.Serialization;

namespace UtmDndGuest;

internal sealed record ExpectedFile(string Name, long Size);

internal sealed record DropRequest(
    Guid TransferId,
    int Display,
    double X,
    double Y,
    double FramebufferWidth,
    double FramebufferHeight,
    IReadOnlyList<ExpectedFile> Files);

internal sealed record TargetResult(
    string Kind,
    string Confidence,
    string Destination,
    string? Reason = null,
    TargetDiagnostic? Diagnostic = null);

internal sealed record TargetDiagnostic(
    long? Hwnd = null,
    string? WindowClass = null,
    int? MatchingShellWindows = null,
    int? Display = null,
    int? ScreenX = null,
    int? ScreenY = null);

internal sealed record MovedFile(string Expected, string Received, string FinalName, long Size);

internal sealed class TargetWire
{
    [JsonPropertyName("kind")]
    public required string Kind { get; init; }

    [JsonPropertyName("confidence")]
    public required string Confidence { get; init; }

    [JsonPropertyName("reason")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Reason { get; init; }

    [JsonPropertyName("diagnostic")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public TargetDiagnostic? Diagnostic { get; init; }

    internal static TargetWire From(TargetResult target, bool debug) => new()
    {
        Kind = target.Kind,
        Confidence = target.Confidence,
        Reason = target.Reason,
        Diagnostic = debug ? target.Diagnostic : null,
    };
}

internal sealed class ReadyResponse
{
    [JsonPropertyName("type")]
    public string Type => "ready";

    [JsonPropertyName("version")]
    public int Version => Protocol.Version;

    [JsonPropertyName("transferId")]
    public required string TransferId { get; init; }

    [JsonPropertyName("target")]
    public required TargetWire Target { get; init; }
}

internal sealed class ErrorResponse
{
    [JsonPropertyName("type")]
    public string Type => "error";

    [JsonPropertyName("version")]
    public int Version => Protocol.Version;

    [JsonPropertyName("transferId")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? TransferId { get; init; }

    [JsonPropertyName("error")]
    public required string Error { get; init; }
}

internal sealed class CompleteResponse
{
    [JsonPropertyName("type")]
    public string Type => "complete";

    [JsonPropertyName("version")]
    public int Version => Protocol.Version;

    [JsonPropertyName("transferId")]
    public required string TransferId { get; init; }

    [JsonPropertyName("target")]
    public required TargetWire Target { get; init; }

    [JsonPropertyName("files")]
    public required IReadOnlyList<MovedFile> Files { get; init; }
}
