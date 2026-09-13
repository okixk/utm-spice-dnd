using Microsoft.Win32.SafeHandles;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace UtmDndGuest;

internal sealed class PortConnection : IAsyncDisposable
{
    internal const string DefaultPath = @"\\.\Global\com.utmapp.dnd.0";
    private static readonly TimeSpan WriteTimeout = TimeSpan.FromSeconds(3);
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly SafeFileHandle _handle;
    private readonly FileStream _stream;
    private readonly SemaphoreSlim _writeGate = new(1, 1);

    private PortConnection(SafeFileHandle handle)
    {
        _handle = handle;
        // A one-byte managed buffer keeps character-device writes direct. In
        // particular, do not issue FlushFileBuffers against vioserial.
        _stream = new FileStream(handle, FileAccess.ReadWrite, 1, isAsync: true);
    }

    internal static PortConnection Open(string path)
    {
        SafeFileHandle handle = NativeMethods.CreateFile(
            path,
            NativeMethods.GenericRead | NativeMethods.GenericWrite,
            0,
            0,
            NativeMethods.OpenExisting,
            NativeMethods.FileFlagOverlapped,
            0);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error, $"cannot open control port {path}");
        }
        return new PortConnection(handle);
    }

    internal async Task ReadMessagesAsync(
        Func<ReadOnlyMemory<byte>, CancellationToken, Task> receive,
        CancellationToken cancellationToken)
    {
        byte[] buffer = new byte[16 * 1024];
        var line = new List<byte>(1024);
        while (!cancellationToken.IsCancellationRequested)
        {
            int count = await _stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            if (count == 0)
            {
                throw new IOException("control port disconnected");
            }
            for (int index = 0; index < count; index++)
            {
                byte value = buffer[index];
                if (value == (byte)'\n')
                {
                    if (line.Count > 0)
                    {
                        await receive(line.ToArray(), cancellationToken).ConfigureAwait(false);
                        line.Clear();
                    }
                    continue;
                }
                if (line.Count + 1 >= Protocol.MaximumMessageBytes)
                {
                    throw new ProtocolException("oversized unterminated control message");
                }
                line.Add(value);
            }
        }
    }

    internal async Task SendAsync(object message, CancellationToken cancellationToken)
    {
        byte[] json = JsonSerializer.SerializeToUtf8Bytes(message, message.GetType(), JsonOptions);
        if (json.Length + 1 > Protocol.MaximumMessageBytes)
        {
            throw new ProtocolException("refusing oversized response");
        }
        byte[] framed = new byte[json.Length + 1];
        Buffer.BlockCopy(json, 0, framed, 0, json.Length);
        framed[^1] = (byte)'\n';

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(WriteTimeout);
        await _writeGate.WaitAsync(timeout.Token).ConfigureAwait(false);
        try
        {
            await _stream.WriteAsync(framed, timeout.Token).ConfigureAwait(false);
        }
        finally
        {
            _writeGate.Release();
        }
    }

    // Used only by the live fault-injection test peer. Production messages go
    // through SendAsync so they are always size checked and valid JSON.
    internal async Task SendRawForTestAsync(ReadOnlyMemory<byte> framed, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(WriteTimeout);
        await _writeGate.WaitAsync(timeout.Token).ConfigureAwait(false);
        try
        {
            await _stream.WriteAsync(framed, timeout.Token).ConfigureAwait(false);
        }
        finally
        {
            _writeGate.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        try
        {
            NativeMethods.CancelIoEx(_handle, 0);
        }
        catch
        {
        }
        await _stream.DisposeAsync().ConfigureAwait(false);
        _writeGate.Dispose();
    }
}
