using System.IO;
using System.Text.Json;
using TrackIRLite;

namespace TrackIRServer;

public class ServerConfig
{
    public static readonly string FilePath = Path.Combine(AppContext.BaseDirectory, "server.json");
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public int Camera { get; set; } = 0;
    public int Port { get; set; } = NetProtocol.DefaultPort;
    public int Width { get; set; } = 0;
    public int Height { get; set; } = 0;
    public int MaxBitrateKbps { get; set; } = 10000;
    public int PipCamera { get; set; } = 1;
    public int PipWidth { get; set; } = 1280;
    public int PipHeight { get; set; } = 720;
    public int PipMaxBitrateKbps { get; set; } = 3000;

    public static ServerConfig Load()
    {
        ServerConfig config = new();
        try
        {
            if (File.Exists(FilePath))
                config = JsonSerializer.Deserialize<ServerConfig>(File.ReadAllText(FilePath)) ?? new ServerConfig();
        }
        catch { }
        try { File.WriteAllText(FilePath, JsonSerializer.Serialize(config, JsonOptions)); }
        catch { }
        return config;
    }
}
