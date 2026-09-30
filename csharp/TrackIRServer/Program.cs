using System.Net;
using System.Net.Sockets;
using FFmpeg.AutoGen;
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

            new CameraStreamer("Main", NetProtocol.MainStream, config.Camera, config.Width, config.Height,
                config.MaxBitrateKbps, server).Start();
            if (config.PipCamera >= 0)
            {
                new CameraStreamer("PiP", NetProtocol.PipStream, config.PipCamera, config.PipWidth, config.PipHeight,
                    config.PipMaxBitrateKbps, server).Start();
            }
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
