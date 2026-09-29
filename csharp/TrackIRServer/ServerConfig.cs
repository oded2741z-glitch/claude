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
    public int BitrateKbps { get; set; } = 10000;

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
