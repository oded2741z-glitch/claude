using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;

namespace TrackIRServer;

public sealed class ClientConnection : IDisposable
{
    private const int MaxQueue = 5;

    private readonly TcpClient tcp;
    private readonly BlockingCollection<byte[]> queue = new();
    private readonly object sync = new();
    private bool alive = true;
    private bool needsKeyframe = true;

    public string Name { get; }

    public bool Alive
    {
        get { lock (sync) return alive; }
    }

    public bool NeedsKeyframe
    {
        get { lock (sync) return alive && needsKeyframe; }
    }

    public ClientConnection(TcpClient tcp)
    {
        this.tcp = tcp;
        tcp.NoDelay = true;
        Name = (tcp.Client.RemoteEndPoint as IPEndPoint)?.Address.ToString() ?? "unknown";
        new Thread(SendLoop) { IsBackground = true }.Start();
        new Thread(ReceiveLoop) { IsBackground = true }.Start();
    }

    public void Send(byte[] message, bool keyframe)
    {
        lock (sync)
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
        lock (sync) alive = false;
    }

    private void ReceiveLoop()
    {
        try
        {
            NetworkStream stream = tcp.GetStream();
            while (stream.ReadByte() >= 0) { }
        }
        catch { }
        lock (sync) alive = false;
    }

    public void Dispose()
    {
        lock (sync)
        {
            alive = false;
            queue.CompleteAdding();
        }
        tcp.Dispose();
    }
}
