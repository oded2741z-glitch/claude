using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using TrackIRLite;

namespace TrackIRServer;

public sealed class ClientConnection : IDisposable
{
    private const int MaxQueue = 10;

    private readonly TcpClient tcp;
    private readonly BlockingCollection<byte[]> queue = new();
    private readonly object sync = new();
    private readonly bool[] needsKeyframe = { true, true };
    private bool alive = true;
    private bool wantsPip;

    public string Name { get; }

    public bool Alive
    {
        get { lock (sync) return alive; }
    }

    public ClientConnection(TcpClient tcp)
    {
        this.tcp = tcp;
        tcp.NoDelay = true;
        Name = (tcp.Client.RemoteEndPoint as IPEndPoint)?.Address.ToString() ?? "unknown";
        new Thread(SendLoop) { IsBackground = true }.Start();
        new Thread(ReceiveLoop) { IsBackground = true }.Start();
    }

    public bool Receives(byte stream)
    {
        lock (sync) return alive && (stream == NetProtocol.MainStream || wantsPip);
    }

    public bool NeedsKeyframe(byte stream)
    {
        lock (sync) return Receives(stream) && needsKeyframe[stream];
    }

    public void Send(byte stream, byte[] message, bool keyframe)
    {
        lock (sync)
        {
            if (!Receives(stream)) return;
            if (needsKeyframe[stream])
            {
                if (!keyframe) return;
                needsKeyframe[stream] = false;
            }
            if (queue.Count >= MaxQueue)
            {
                while (queue.TryTake(out _)) { }
                needsKeyframe[NetProtocol.MainStream] = true;
                needsKeyframe[NetProtocol.PipStream] = true;
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
            int command;
            while ((command = stream.ReadByte()) >= 0)
            {
                lock (sync)
                {
                    if (command == NetProtocol.PipOn && !wantsPip)
                    {
                        wantsPip = true;
                        needsKeyframe[NetProtocol.PipStream] = true;
                    }
                    else if (command == NetProtocol.PipOff)
                    {
                        wantsPip = false;
                    }
                }
            }
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
