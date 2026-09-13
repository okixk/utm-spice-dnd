using System.Text.Json;

namespace UtmDndGuest;

internal sealed class GuestApplication(string portPath, bool debug, Log log)
{
    private static readonly TimeSpan ReconnectDelay = TimeSpan.FromSeconds(1);
    private readonly string _portPath = portPath;
    private readonly bool _debug = debug;
    private readonly Log _log = log;
    private readonly FileTransferCoordinator _transfers = new(log.Write);
    private readonly TargetResolver _resolver = new(debug, log.Write);

    internal async Task RunAsync(CancellationToken cancellationToken)
    {
        _log.Write($"starting pid={Environment.ProcessId} user={Environment.UserDomainName}\\{Environment.UserName} port={_portPath}");
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await using PortConnection port = PortConnection.Open(_portPath);
                _log.Write("control port connected");
                await port.ReadMessagesAsync(
                    (message, token) => HandleMessageAsync(port, message, token),
                    cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception error)
            {
                _log.Write($"control port unavailable/disconnected: {error.GetType().Name}: {error.Message}");
            }
            finally
            {
                _transfers.CancelActive("control-port-disconnected");
            }

            try
            {
                await Task.Delay(ReconnectDelay, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
        _log.Write("stopped");
    }

    private async Task HandleMessageAsync(
        PortConnection port,
        ReadOnlyMemory<byte> message,
        CancellationToken cancellationToken)
    {
        try
        {
            ControlMessage control = Protocol.Parse(message.Span);
            if (control is CancelControlMessage cancel)
            {
                _transfers.Cancel(cancel.TransferId, "host-cancel");
                return;
            }

            DropRequest request = ((DropControlMessage)control).Request;
            if (_transfers.IsActive)
            {
                throw new ProtocolException("another semantic transfer is active");
            }

            TargetResult target = _resolver.Resolve(request);
            TransferSession session = _transfers.Arm(request, target);
            string expected = string.Join(", ", request.Files.Select(file => $"{file.Name} ({file.Size})"));
            _log.Write($"drop version={Protocol.Version} id={request.TransferId:D} files=[{expected}]");
            _log.Write($"target id={request.TransferId:D} display={request.Display} guest=({request.X:F1},{request.Y:F1}) kind={target.Kind} confidence={target.Confidence} destination={target.Destination} reason={target.Reason}");

            await port.SendAsync(new ReadyResponse
            {
                TransferId = request.TransferId.ToString("D"),
                Target = TargetWire.From(target, _debug),
            }, cancellationToken).ConfigureAwait(false);
            _log.Write($"ready version={Protocol.Version} id={request.TransferId:D}");
            _transfers.Start(session, port.SendAsync, _debug);
        }
        catch (ProtocolException error)
        {
            string? transferId = TryReadTransferId(message.Span);
            _log.Write($"rejected control message: {error.Message}");
            await port.SendAsync(new ErrorResponse
            {
                TransferId = transferId,
                Error = error.Message,
            }, cancellationToken).ConfigureAwait(false);
        }
    }

    private static string? TryReadTransferId(ReadOnlySpan<byte> message)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(message.ToArray());
            if (document.RootElement.ValueKind == JsonValueKind.Object &&
                document.RootElement.TryGetProperty("transferId", out JsonElement element) &&
                element.ValueKind == JsonValueKind.String &&
                Guid.TryParseExact(element.GetString(), "D", out Guid transferId))
            {
                return transferId.ToString("D");
            }
        }
        catch (JsonException)
        {
        }
        return null;
    }
}
