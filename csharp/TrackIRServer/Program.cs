using System.Net;
using System.Net.Sockets;
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
            Cv2.SetLogLevel(LogLevel.SILENT);
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

            bool manual = config.Width > 0 && config.Height > 0;
            var output = new StreamOutput("Main", manual ? config.Width : 0, config.MaxBitrateKbps, server);
            new CameraStreamer(config.Camera, config.Width, config.Height, server, output).Start();
            Thread.Sleep(Timeout.Infinite);
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
}
