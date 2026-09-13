using System.Runtime.InteropServices;

namespace UtmDndGuest;

internal static class KnownFolders
{
    // Values from KnownFolders.h.
    internal static readonly Guid Desktop = new("B4BFCC3A-DB2C-424C-B029-7FE99A87C641");
    internal static readonly Guid Documents = new("FDD39AD0-238F-46AF-ADB4-6C85480369C7");
    internal static readonly Guid Downloads = new("374DE290-123F-4565-9164-39C4925E467B");

    internal static string Get(Guid folderId)
    {
        int result = NativeMethods.SHGetKnownFolderPath(folderId, 0, 0, out nint path);
        if (result != 0)
        {
            Marshal.ThrowExceptionForHR(result);
        }
        try
        {
            return Marshal.PtrToStringUni(path) ?? throw new IOException("known folder returned an empty path");
        }
        finally
        {
            Marshal.FreeCoTaskMem(path);
        }
    }
}
