using System.Buffers.Binary;
using System.Diagnostics;
using System.Runtime.InteropServices;
using OpenCvSharp;
using TrackIRLite;

namespace TrackIRServer;

public sealed class CameraStreamer
{
    private readonly string name;
    private readonly byte stream;
    private readonly int camera;
    private readonly int width;
    private readonly int height;
    private readonly int maxBitrateKbps;
    private readonly StreamServer server;
    private bool missingReported;

    public CameraStreamer(string name, byte stream, int camera, int width, int height, int maxBitrateKbps, StreamServer server)
    {
        this.name = name;
        this.stream = stream;
        this.camera = camera;
        this.width = width;
        this.height = height;
        this.maxBitrateKbps = maxBitrateKbps;
        this.server = server;
    }

    public void Start()
    {
        new Thread(Run) { IsBackground = true }.Start();
    }

    private void Run()
    {
        while (true)
        {
            try
            {
                RunCamera();
            }
            catch (Exception ex)
            {
                Program.Log($"{name}: {ex.Message}");
                Thread.Sleep(3000);
            }
        }
    }

    private void RunCamera()
    {
        bool manual = width > 0 && height > 0;
        using var reader = new FrameReader(camera.ToString(), true, manual ? width : 10000, manual ? height : 10000);
        if (!reader.IsOpened)
        {
            if (!missingReported) Program.Log($"{name}: camera {camera} not found, retrying...");
            missingReported = true;
            Thread.Sleep(3000);
            return;
        }
        missingReported = false;

        int limit = manual ? width : 0;
        int fps = reader.Fps is > 0 and < 240 ? (int)Math.Round(reader.Fps) : 30;
        H264Encoder? encoder = null;
        using var scaled = new Mat();
        using var yuv = new Mat();
        long lastId = 0;
        int frames = 0;
        long bytes = 0;
        var stats = Stopwatch.StartNew();

        try
        {
            while (reader.WaitForFrame(5000))
            {
                int w = 0, h = 0;
                bool sending = server.ViewerCount(stream) > 0;
                reader.TryRead(ref lastId, frame =>
                {
                    w = frame.Width;
                    h = frame.Height;
                    if (limit > 0 && w > limit)
                    {
                        h = h * limit / w;
                        w = limit;
                    }
                    w &= ~1;
                    h &= ~1;
                    if (!sending) return;

                    if (w < frame.Width - 1)
                    {
                        Cv2.Resize(frame, scaled, new Size(w, h), 0, 0, InterpolationFlags.Area);
                        Cv2.CvtColor(scaled, yuv, ColorConversionCodes.BGRA2YUV_I420);
                    }
                    else
                    {
                        using var even = new Mat(frame, new OpenCvSharp.Rect(0, 0, w, h));
                        Cv2.CvtColor(even, yuv, ColorConversionCodes.BGRA2YUV_I420);
                    }
                });
                if (w == 0) continue;

                if (encoder == null || encoder.Width != w || encoder.Height != h)
                {
                    encoder?.Dispose();
                    encoder = new H264Encoder(w, h, fps, maxBitrateKbps);
                    Program.Log($"{name}: camera {camera}, {w}x{h} @ {fps} fps, encoder {encoder.Name}, max {maxBitrateKbps} kbit/s");
                }
                if (!sending)
                {
                    frames = 0;
                    bytes = 0;
                    stats.Restart();
                    continue;
                }

                encoder.Encode(yuv, server.NeedsKeyframe(stream), (data, size, keyframe) =>
                {
                    var message = new byte[NetProtocol.HeaderSize + size];
                    BinaryPrimitives.WriteInt32LittleEndian(message, size);
                    message[4] = stream;
                    Marshal.Copy(data, message, NetProtocol.HeaderSize, size);
                    server.Broadcast(stream, message, keyframe);
                    bytes += size;
                });
                frames++;

                if (stats.Elapsed.TotalSeconds >= 5)
                {
                    double seconds = stats.Elapsed.TotalSeconds;
                    Program.Log($"{name}: {frames / seconds:0} fps, {bytes * 8 / seconds / 1e6:0.0} Mbit/s, clients: {server.ViewerCount(stream)}");
                    frames = 0;
                    bytes = 0;
                    stats.Restart();
                }
            }
            Program.Log($"{name}: camera {camera} stopped, reopening...");
        }
        finally
        {
            encoder?.Dispose();
        }
    }
}
