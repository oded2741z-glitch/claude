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
    private readonly FrameSlot slot = new();
    private volatile bool running;
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
            try { Receive(tcp.GetStream()); }
            catch { }
            tcp.Dispose();
            client = null;
        }
        slot.Dispose();
    }

    private void Receive(NetworkStream stream)
    {
        using var decoder = new H264Decoder(hardware);
        var header = new byte[NetProtocol.HeaderSize];
        var payload = new byte[1 << 20];

        while (running)
        {
            stream.ReadExactly(header);
            int size = BinaryPrimitives.ReadInt32LittleEndian(header);
            if (size <= 0 || size > NetProtocol.MaxPacketSize) return;
            if (payload.Length < size) payload = new byte[size];
            stream.ReadExactly(payload, 0, size);
            if (header[4] != NetProtocol.MainStream) continue;

            if (decoder.Decode(payload, size, slot.Back)) slot.Publish();
        }
    }

    public bool TryRead(ref long lastId, Action<Mat> use) => slot.TryRead(ref lastId, use);

    public void Dispose()
    {
        running = false;
        client?.Dispose();
        if (thread != null)
        {
            thread.Join(1000);
            return;
        }
        slot.Dispose();
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
