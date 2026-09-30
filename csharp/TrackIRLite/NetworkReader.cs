using System.Buffers.Binary;
using System.Net.Sockets;
using FFmpeg.AutoGen;
using OpenCvSharp;

namespace TrackIRLite;

public sealed class NetworkReader : IFrameSource
{
    public const string Scheme = "net://";
    private const int ConnectTimeoutMs = 2000;

    private static bool ffmpegLoaded;

    private readonly string host;
    private readonly int port;
    private readonly bool hardware;
    private readonly Thread? thread;
    private readonly object writeLock = new();
    private readonly FrameSlot[] slots = { new(), new() };
    private volatile bool running;
    private volatile bool pipWanted;
    private TcpClient? client;

    public bool IsOpened { get; }

    public NetworkReader(string source, bool hardware)
    {
        this.hardware = hardware;
        (host, port) = Parse(source);
        if (!LoadFFmpeg()) return;
        client = Connect();
        IsOpened = client != null;
        if (!IsOpened) return;

        running = true;
        thread = new Thread(Run) { IsBackground = true };
        thread.Start();
    }

    public static bool IsNetworkSource(string source) => source.StartsWith(Scheme, StringComparison.OrdinalIgnoreCase);

    public static string ToSource(string address) => IsNetworkSource(address) ? address : Scheme + address;

    private static (string Host, int Port) Parse(string source)
    {
        string address = source[Scheme.Length..].Trim().TrimEnd('/');
        int colon = address.LastIndexOf(':');
        if (colon > 0 && int.TryParse(address[(colon + 1)..], out int port)) return (address[..colon], port);
        return (address, NetProtocol.DefaultPort);
    }

    private static bool LoadFFmpeg()
    {
        if (ffmpegLoaded) return true;
        try
        {
            ffmpeg.RootPath = AppContext.BaseDirectory;
            DynamicallyLoadedBindings.Initialize();
            ffmpeg.av_log_set_level(ffmpeg.AV_LOG_QUIET);
            ffmpegLoaded = true;
        }
        catch { }
        return ffmpegLoaded;
    }

    private TcpClient? Connect()
    {
        var tcp = new TcpClient { NoDelay = true, ReceiveTimeout = 3000 };
        try
        {
            if (tcp.ConnectAsync(host, port).Wait(ConnectTimeoutMs)) return tcp;
        }
        catch { }
        tcp.Dispose();
        return null;
    }

    public PipSource OpenPip()
    {
        pipWanted = true;
        SendPipState(client);
        return new PipSource(this);
    }

    private void ClosePip()
    {
        pipWanted = false;
        SendPipState(client);
    }

    private void SendPipState(TcpClient? tcp)
    {
        if (tcp == null) return;
        lock (writeLock)
        {
            try { tcp.GetStream().WriteByte(pipWanted ? NetProtocol.PipOn : NetProtocol.PipOff); }
            catch { }
        }
    }

    private void Run()
    {
        while (running)
        {
            TcpClient? tcp = client ?? Connect();
            client = tcp;
            if (tcp == null)
            {
                Thread.Sleep(500);
                continue;
            }
            SendPipState(tcp);
            try { Receive(tcp.GetStream()); }
            catch { }
            tcp.Dispose();
            client = null;
        }
        foreach (FrameSlot slot in slots) slot.Dispose();
    }

    private void Receive(NetworkStream stream)
    {
        var decoders = new H264Decoder?[slots.Length];
        var header = new byte[NetProtocol.HeaderSize];
        var payload = new byte[1 << 20];

        try
        {
            while (running)
            {
                stream.ReadExactly(header);
                int size = BinaryPrimitives.ReadInt32LittleEndian(header);
                if (size <= 0 || size > NetProtocol.MaxPacketSize) return;
                if (payload.Length < size) payload = new byte[size];
                stream.ReadExactly(payload, 0, size);

                int index = header[4];
                if (index >= slots.Length) continue;
                H264Decoder decoder = decoders[index] ??= new H264Decoder(hardware);
                if (decoder.Decode(payload, size, slots[index].Back)) slots[index].Publish();
            }
        }
        finally
        {
            foreach (H264Decoder? decoder in decoders) decoder?.Dispose();
        }
    }

    public bool TryRead(ref long lastId, Action<Mat> use) => slots[NetProtocol.MainStream].TryRead(ref lastId, use);

    public void Dispose()
    {
        running = false;
        client?.Dispose();
        if (thread != null)
        {
            thread.Join(1000);
            return;
        }
        foreach (FrameSlot slot in slots) slot.Dispose();
    }

    public sealed class PipSource : IFrameSource
    {
        private readonly NetworkReader owner;

        internal PipSource(NetworkReader owner)
        {
            this.owner = owner;
        }

        public bool IsOpened => true;

        public bool TryRead(ref long lastId, Action<Mat> use) => owner.slots[NetProtocol.PipStream].TryRead(ref lastId, use);

        public void Dispose() => owner.ClosePip();
    }

    private sealed class FrameSlot
    {
        private readonly object sync = new();
        private Mat front = new();
        private Mat back = new();
        private long frameId;

        public Mat Back => back;

        public void Publish()
        {
            lock (sync)
            {
                (front, back) = (back, front);
                frameId++;
            }
        }

        public bool TryRead(ref long lastId, Action<Mat> use)
        {
            lock (sync)
            {
                if (frameId == lastId || front.IsDisposed || front.Empty()) return false;
                lastId = frameId;
                use(front);
                return true;
            }
        }

        public void Dispose()
        {
            lock (sync)
            {
                front.Dispose();
                back.Dispose();
            }
        }
    }
}
