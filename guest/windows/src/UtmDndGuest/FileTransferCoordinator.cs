using Microsoft.Win32.SafeHandles;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace UtmDndGuest;

internal sealed class FileTransferCoordinator(Action<string> log)
{
    private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(200);
    private static readonly TimeSpan StableInterval = TimeSpan.FromMilliseconds(600);
    private static readonly TimeSpan TransferTimeout = TimeSpan.FromMinutes(3);

    private readonly object _gate = new();
    private readonly Action<string> _log = log;
    private TransferSession? _active;

    internal bool IsActive
    {
        get
        {
            lock (_gate)
            {
                return _active is not null;
            }
        }
    }

    internal TransferSession Arm(DropRequest request, TargetResult target)
    {
        string downloads = TargetResolver.ValidateDestination(KnownFolders.Get(KnownFolders.Downloads));
        HashSet<FileIdentity> baseline = Snapshot(downloads);
        var session = new TransferSession(
            request.TransferId,
            request.Files,
            downloads,
            target,
            baseline,
            DateTime.UtcNow,
            DateTime.UtcNow + TransferTimeout);
        lock (_gate)
        {
            if (_active is not null)
            {
                session.Cancellation.Dispose();
                throw new ProtocolException("another semantic transfer is active");
            }
            _active = session;
        }
        return session;
    }

    internal void Start(
        TransferSession session,
        Func<object, CancellationToken, Task> send,
        bool debug)
    {
        _ = Task.Run(() => MonitorAsync(session, send, debug));
    }

    internal void Cancel(Guid transferId, string reason)
    {
        TransferSession? session;
        lock (_gate)
        {
            session = _active?.TransferId == transferId ? _active : null;
            if (session is not null)
            {
                _active = null;
            }
        }
        if (session is not null)
        {
            session.Cancellation.Cancel();
            _log($"transfer {transferId:D} cancelled ({reason}); unmatched files remain in Downloads");
        }
    }

    internal void CancelActive(string reason)
    {
        Guid? transferId;
        lock (_gate)
        {
            transferId = _active?.TransferId;
        }
        if (transferId.HasValue)
        {
            Cancel(transferId.Value, reason);
        }
    }

    private async Task MonitorAsync(
        TransferSession session,
        Func<object, CancellationToken, Task> send,
        bool debug)
    {
        CancellationToken token = session.Cancellation.Token;
        try
        {
            while (!token.IsCancellationRequested && DateTime.UtcNow < session.DeadlineUtc)
            {
                if (session.Moved.Count == session.Files.Count)
                {
                    // Placement is authoritative. A diagnostic status write must
                    // never keep the serialized-transfer gate armed.
                    Finish(session);
                    await send(new CompleteResponse
                    {
                        TransferId = session.TransferId.ToString("D"),
                        Target = TargetWire.From(session.Target, debug),
                        Files = session.Moved,
                    }, token).ConfigureAwait(false);
                    _log($"transfer {session.TransferId:D} complete");
                    return;
                }

                ExpectedFile expected = session.Files[session.Moved.Count];
                IReadOnlyList<FileCandidate> candidates = ScanCandidates(session, expected);
                foreach (FileCandidate candidate in candidates)
                {
                    if (!CandidateIsStable(session, candidate))
                    {
                        continue;
                    }

                    string finalPath;
                    try
                    {
                        finalPath = MoveSafely(candidate.Path, session.Target.Destination, expected.Name);
                    }
                    catch (Exception error) when (error is IOException or UnauthorizedAccessException)
                    {
                        _log($"target move failed for {Path.GetFileName(candidate.Path)}: {error.Message}; leaving file in Downloads");
                        finalPath = candidate.Path;
                        session.Target = new TargetResult(
                            "fallback",
                            "low",
                            session.Downloads,
                            "target-disappeared");
                    }

                    session.Matched.Add(candidate.Identity);
                    session.Moved.Add(new MovedFile(
                        expected.Name,
                        Path.GetFileName(candidate.Path),
                        Path.GetFileName(finalPath),
                        candidate.Size));
                    _log($"transfer id={session.TransferId:D} received={Path.GetFileName(candidate.Path)} final={finalPath}");
                    break;
                }
                await Task.Delay(PollInterval, token).ConfigureAwait(false);
            }

            if (!token.IsCancellationRequested)
            {
                _log($"transfer {session.TransferId:D} timed out; unmatched files remain in Downloads");
                await send(new ErrorResponse
                {
                    TransferId = session.TransferId.ToString("D"),
                    Error = "timed out waiting for expected SPICE files",
                }, CancellationToken.None).ConfigureAwait(false);
                Finish(session);
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception error)
        {
            _log($"transfer monitor failed: {error.GetType().Name}: {error.Message}; unmatched files remain in Downloads");
            Finish(session);
        }
        finally
        {
            session.Cancellation.Dispose();
        }
    }

    private void Finish(TransferSession session)
    {
        lock (_gate)
        {
            if (ReferenceEquals(_active, session))
            {
                _active = null;
            }
        }
    }

    private static HashSet<FileIdentity> Snapshot(string directory)
    {
        var identities = new HashSet<FileIdentity>();
        foreach (string path in Directory.EnumerateFiles(directory))
        {
            if (TryGetFileIdentity(path, out FileIdentity identity))
            {
                identities.Add(identity);
            }
        }
        return identities;
    }

    private static IReadOnlyList<FileCandidate> ScanCandidates(TransferSession session, ExpectedFile expected)
    {
        var matches = new List<FileCandidate>();
        IEnumerable<string> files;
        try
        {
            files = Directory.EnumerateFiles(session.Downloads).ToArray();
        }
        catch (IOException)
        {
            return matches;
        }

        foreach (string path in files)
        {
            try
            {
                var info = new FileInfo(path);
                if ((info.Attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0 ||
                    info.Length != expected.Size ||
                    info.CreationTimeUtc + TimeSpan.FromSeconds(2) < session.StartedUtc ||
                    !Protocol.NameMatchesReceived(expected.Name, info.Name) ||
                    !TryGetFileIdentity(path, out FileIdentity identity) ||
                    session.Baseline.Contains(identity) || session.Matched.Contains(identity))
                {
                    continue;
                }
                matches.Add(new FileCandidate(path, identity, info.Length, info.LastWriteTimeUtc, info.CreationTimeUtc));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
            }
        }
        return matches.OrderBy(candidate => candidate.CreationUtc).ThenBy(candidate => candidate.Path, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    private static bool CandidateIsStable(TransferSession session, FileCandidate candidate)
    {
        DateTime now = DateTime.UtcNow;
        if (!session.Candidates.TryGetValue(candidate.Identity, out CandidateState? state) ||
            state.Size != candidate.Size || state.LastWriteUtc != candidate.LastWriteUtc)
        {
            session.Candidates[candidate.Identity] = new CandidateState(candidate.Size, candidate.LastWriteUtc, now);
            return false;
        }
        return now - state.StableSinceUtc >= StableInterval;
    }

    internal static string MoveSafely(string source, string destinationDirectory, string destinationName)
    {
        destinationDirectory = TargetResolver.ValidateDestination(destinationDirectory);
        string downloads = TargetResolver.ValidateDestination(KnownFolders.Get(KnownFolders.Downloads));
        if (string.Equals(
            Path.TrimEndingDirectorySeparator(destinationDirectory),
            Path.TrimEndingDirectorySeparator(downloads),
            StringComparison.OrdinalIgnoreCase))
        {
            return source;
        }

        FileInfo sourceInfo = new(source);
        if (!sourceInfo.Exists || (sourceInfo.Attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
        {
            throw new IOException("received source is no longer a normal regular file");
        }

        bool sameVolume = string.Equals(
            Path.GetPathRoot(source),
            Path.GetPathRoot(destinationDirectory),
            StringComparison.OrdinalIgnoreCase);
        for (int number = 0; number < 10_000; number++)
        {
            string name = number == 0 ? destinationName : Protocol.DuplicateName(destinationName, number);
            string destination = Path.Combine(destinationDirectory, name);
            if (File.Exists(destination) || Directory.Exists(destination))
            {
                continue;
            }
            try
            {
                if (sameVolume)
                {
                    File.Move(source, destination, overwrite: false);
                }
                else
                {
                    CopyExclusiveThenDelete(source, destination, sourceInfo.Length);
                }
                return destination;
            }
            catch (IOException) when (File.Exists(destination) || Directory.Exists(destination))
            {
                continue;
            }
        }
        throw new IOException("could not choose a non-colliding destination name");
    }

    private static void CopyExclusiveThenDelete(string source, string destination, long expectedLength)
    {
        try
        {
            using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, FileOptions.SequentialScan))
            using (var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1024 * 1024, FileOptions.SequentialScan | FileOptions.WriteThrough))
            {
                input.CopyTo(output, 1024 * 1024);
                output.Flush(flushToDisk: true);
                if (output.Length != expectedLength)
                {
                    throw new IOException("copied file length does not match source");
                }
            }
        }
        catch
        {
            try
            {
                File.Delete(destination);
            }
            catch
            {
            }
            throw;
        }
        File.Delete(source);
    }

    private static bool TryGetFileIdentity(string path, out FileIdentity identity)
    {
        using SafeFileHandle handle = NativeMethods.CreateFile(
            path,
            0,
            NativeMethods.FileShareRead | NativeMethods.FileShareWrite | NativeMethods.FileShareDelete,
            0,
            NativeMethods.OpenExisting,
            NativeMethods.FileFlagOpenReparsePoint,
            0);
        if (handle.IsInvalid || !NativeMethods.GetFileInformationByHandle(handle, out NativeMethods.ByHandleFileInformation info))
        {
            identity = default;
            return false;
        }
        identity = new FileIdentity(info.VolumeSerialNumber, ((ulong)info.FileIndexHigh << 32) | info.FileIndexLow);
        return true;
    }
}

internal sealed class TransferSession(
    Guid transferId,
    IReadOnlyList<ExpectedFile> files,
    string downloads,
    TargetResult target,
    HashSet<FileIdentity> baseline,
    DateTime startedUtc,
    DateTime deadlineUtc)
{
    internal Guid TransferId { get; } = transferId;
    internal IReadOnlyList<ExpectedFile> Files { get; } = files;
    internal string Downloads { get; } = downloads;
    internal TargetResult Target { get; set; } = target;
    internal HashSet<FileIdentity> Baseline { get; } = baseline;
    internal DateTime StartedUtc { get; } = startedUtc;
    internal DateTime DeadlineUtc { get; } = deadlineUtc;
    internal HashSet<FileIdentity> Matched { get; } = [];
    internal Dictionary<FileIdentity, CandidateState> Candidates { get; } = [];
    internal List<MovedFile> Moved { get; } = [];
    internal CancellationTokenSource Cancellation { get; } = new();
}

internal sealed record CandidateState(long Size, DateTime LastWriteUtc, DateTime StableSinceUtc);
internal sealed record FileCandidate(string Path, FileIdentity Identity, long Size, DateTime LastWriteUtc, DateTime CreationUtc);
