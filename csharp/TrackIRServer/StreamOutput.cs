using System.Buffers.Binary;
using System.Diagnostics;
using System.Runtime.InteropServices;
using OpenCvSharp;
using TrackIRLite;

namespace TrackIRServer;

public sealed class StreamOutput
{
    private readonly int limit;
    private readonly int maxBitrateKbps;
    private readonly StreamServer server;
    private readonly Mat scaled = new();
    private readonly Mat yuv = new();
    private readonly Stopwatch stats = new();
    private H264Encoder? encoder;
    private int camera;
    private int fps;
    private int width;
    private int height;
    private int frames;
    private long bytes;
    private bool sending;
    private bool prepared;

    public string Name { get; }
    public byte Stream { get; }

    public StreamOutput(string name, byte stream, int limit, int maxBitrateKbps, StreamServer server)
    {
        Name = name;
        Stream = stream;
        this.limit = limit;
        this.maxBitrateKbps = maxBitrateKbps;
        this.server = server;
    }

    public void Open(int camera, int fps)
    {
        Close();
        this.camera = camera;
        this.fps = fps;
        ResetStats();
    }

    public void CheckViewers()
    {
        sending = server.ViewerCount(Stream) > 0;
    }

    public void Prepare(Mat frame)
    {
        width = frame.Width;
        height = frame.Height;
        if (limit > 0 && width > limit)
        {
            height = height * limit / width;
            width = limit;
        }
        width &= ~1;
        height &= ~1;
        prepared = true;
        if (!sending) return;

        if (width < frame.Width - 1)
        {
            Cv2.Resize(frame, scaled, new Size(width, height), 0, 0, InterpolationFlags.Area);
            Cv2.CvtColor(scaled, yuv, ColorConversionCodes.BGRA2YUV_I420);
        }
        else
        {
            using var even = new Mat(frame, new OpenCvSharp.Rect(0, 0, width, height));
            Cv2.CvtColor(even, yuv, ColorConversionCodes.BGRA2YUV_I420);
        }
    }

    public void Encode()
    {
        if (!prepared) return;
        prepared = false;

        if (encoder == null || encoder.Width != width || encoder.Height != height)
        {
            encoder?.Dispose();
            encoder = new H264Encoder(width, height, fps, maxBitrateKbps);
            Program.Log($"{Name}: camera {camera}, {width}x{height} @ {fps} fps, encoder {encoder.Name}, max {maxBitrateKbps} kbit/s");
        }
        if (!sending)
        {
            ResetStats();
            return;
        }

        encoder.Encode(yuv, server.NeedsKeyframe(Stream), (data, size, keyframe) =>
        {
            var message = new byte[NetProtocol.HeaderSize + size];
            BinaryPrimitives.WriteInt32LittleEndian(message, size);
            message[4] = Stream;
            Marshal.Copy(data, message, NetProtocol.HeaderSize, size);
            server.Broadcast(Stream, message, keyframe);
            bytes += size;
        });
        frames++;

        if (stats.Elapsed.TotalSeconds >= 5)
        {
            double seconds = stats.Elapsed.TotalSeconds;
            Program.Log($"{Name}: {frames / seconds:0} fps, {bytes * 8 / seconds / 1e6:0.0} Mbit/s, clients: {server.ViewerCount(Stream)}");
            ResetStats();
        }
    }

    public void Close()
    {
        encoder?.Dispose();
        encoder = null;
        prepared = false;
    }

    private void ResetStats()
    {
        frames = 0;
        bytes = 0;
        stats.Restart();
    }
}
