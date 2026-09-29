using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;

namespace TrackIRServer;

public sealed class ClientConnection : IDisposable
{
    private const int MaxQueue = 5;

    private readonly TcpClient tcp;
    private readonly BlockingCollection<byte[]> queue = new();
    private volatile bool alive = true;
    private volatile bool needsKeyframe = true;

    public string Name { get; }
    public bool Alive => alive;
    public bool NeedsKeyframe => needsKeyframe;

    public ClientConnection(TcpClient tcp)
    {
        this.tcp = tcp;
        tcp.NoDelay = true;
        Name = (tcp.Client.RemoteEndPoint as IPEndPoint)?.Address.ToString() ?? "unknown";
        new Thread(SendLoop) { IsBackground = true }.Start();
    }

    public void Send(byte[] message, bool keyframe)
    {
        if (!alive) return;
        if (needsKeyframe)
        {
            if (!keyframe) return;
            needsKeyframe = false;
        }
        if (queue.Count >= MaxQueue)
        {
            while (queue.TryTake(out _)) { }
            needsKeyframe = true;
            return;
        }
        queue.Add(message);
    }

    private void SendLoop()
    {
        try
        {
            NetworkStream stream = tcp.GetStream();
            foreach (byte[] message in queue.GetConsumingEnumerable())
                stream.Write(message);
        }
        catch { }
        alive = false;
    }

    public void Dispose()
    {
        alive = false;
        queue.CompleteAdding();
        tcp.Dispose();
    }
}
