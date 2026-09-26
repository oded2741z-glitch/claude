using System.IO;
using System.Text.Json;

namespace TrackIRLite;

public class Settings
{
    private static readonly string FilePath = Path.Combine(AppContext.BaseDirectory, "settings.json");
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public double HomeYaw { get; set; } = 0.0;
    public double HomePitch { get; set; } = 0.0;
    public double BaseFov { get; set; } = 70.0;

    public int PipIndex { get; set; } = -1;
    public int PipWidth { get; set; } = 400;
    public int PipX { get; set; } = -1;
    public int PipY { get; set; } = 10;

    public List<string> RtspHistory { get; set; } = new();
    public string? LastMainSource { get; set; } = null;

    public double TirDeadzone { get; set; } = 1.0;
    public double TirCurve { get; set; } = 1.5;
    public double TirGain { get; set; } = 2.0;

    public string VideoDecoding { get; set; } = "GPU";

    public static Settings Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<Settings>(File.ReadAllText(FilePath)) ?? new Settings();
        }
        catch { }
        return new Settings();
    }

    public void Save()
    {
        try { File.WriteAllText(FilePath, JsonSerializer.Serialize(this, JsonOptions)); }
        catch { }
    }
}
