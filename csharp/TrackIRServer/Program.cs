using System.Buffers.Binary;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using FFmpeg.AutoGen;
using OpenCvSharp;
using TrackIRLite;

namespace TrackIRServer;

internal static class Program
{
    private static void Main()
    {
        Console.Title = "TrackIR Server";
        Console.WriteLine("TRACKIR SERVER");
        Console.WriteLine();

        try
        {
            ServerConfig config = ServerConfig.Load();
            Log($"Settings: {ServerConfig.FilePath}");

            ffmpeg.RootPath = AppContext.BaseDirectory;
            DynamicallyLoadedBindings.Initialize();
            ffmpeg.av_log_set_level(ffmpeg.AV_LOG_QUIET);

            using var server = new StreamServer(config.Port);
            Log($"Listening on port {config.Port}");
            foreach (IPAddress address in Dns.GetHostAddresses(Dns.GetHostName()))
            {
                if (address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(address))
                    Log($"Server address: {address}");
            }

            while (true) RunCamera(config, server);
        }
        catch (Exception ex)
        {
            Log($"Error: {ex.Message}");
            Console.WriteLine("Press any key to exit.");
            Console.ReadKey(true);
        }
    }

    public static void Log(string message)
    {
        Console.WriteLine($"{DateTime.Now:HH:mm:ss}  {message}");
    }

    private static void RunCamera(ServerConfig config, StreamServer server)
    {
        bool manual = config.Width > 0 && config.Height > 0;
        using var camera = new FrameReader(config.Camera.ToString(), true,
            manual ? config.Width : 10000, manual ? config.Height : 10000);
        if (!camera.IsOpened)
        {
            Log($"Camera {config.Camera} not found, retrying...");
            Thread.Sleep(3000);
            return;
        }

        int fps = camera.Fps is > 0 and < 240 ? (int)Math.Round(camera.Fps) : 30;
        H264Encoder? encoder = null;
        using var yuv = new Mat();
        long lastId = 0;
        int frames = 0;
        long bytes = 0;
        var stats = Stopwatch.StartNew();

        try
        {
            while (camera.WaitForFrame(5000))
            {
                int width = 0, height = 0;
                bool sending = server.ClientCount > 0;
                camera.TryRead(ref lastId, frame =>
                {
                    width = frame.Width & ~1;
                    height = frame.Height & ~1;
                    if (!sending) return;
                    using var even = new Mat(frame, new OpenCvSharp.Rect(0, 0, width, height));
                    Cv2.CvtColor(even, yuv, ColorConversionCodes.BGRA2YUV_I420);
                });
                if (width == 0) continue;

                if (encoder == null || encoder.Width != width || encoder.Height != height)
                {
                    encoder?.Dispose();
                    encoder = new H264Encoder(width, height, fps, config.MaxBitrateKbps);
                    Log($"Camera {config.Camera}: {width}x{height} @ {fps} fps, encoder {encoder.Name}, max {config.MaxBitrateKbps} kbit/s");
                }
                if (!sending)
                {
                    frames = 0;
                    bytes = 0;
                    stats.Restart();
                    continue;
                }

                encoder.Encode(yuv, server.NeedsKeyframe, (data, size, keyframe) =>
                {
                    var message = new byte[NetProtocol.HeaderSize + size];
                    BinaryPrimitives.WriteInt32LittleEndian(message, size);
                    message[4] = 0;
                    Marshal.Copy(data, message, NetProtocol.HeaderSize, size);
                    server.Broadcast(message, keyframe);
                    bytes += size;
                });
                frames++;

                if (stats.Elapsed.TotalSeconds >= 5)
                {
                    double seconds = stats.Elapsed.TotalSeconds;
                    Log($"{frames / seconds:0} fps, {bytes * 8 / seconds / 1e6:0.0} Mbit/s, clients: {server.ClientCount}");
                    frames = 0;
                    bytes = 0;
                    stats.Restart();
                }
            }
            Log($"Camera {config.Camera} stopped, reopening...");
        }
        finally
        {
            encoder?.Dispose();
        }
    }
}
