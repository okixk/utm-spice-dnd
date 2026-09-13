using System.Text;
using UtmDndGuest;

if (args.Length != 1 || args[0] is not ("malformed" or "timeout"))
{
    Console.Error.WriteLine("usage: UtmDndGuest.PortFaultPeer malformed|timeout");
    return 2;
}

await using PortConnection port = PortConnection.Open(PortConnection.DefaultPath);
using var done = new CancellationTokenSource(TimeSpan.FromSeconds(30));

await port.ReadMessagesAsync(async (_, cancellationToken) =>
{
    if (args[0] == "malformed")
    {
        await port.SendRawForTestAsync(Encoding.UTF8.GetBytes("{malformed-json}\n"), cancellationToken);
    }
    else
    {
        await Task.Delay(TimeSpan.FromSeconds(6), cancellationToken);
    }
    done.Cancel();
}, done.Token);

return 0;
