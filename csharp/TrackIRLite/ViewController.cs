namespace TrackIRLite;

public class ViewController
{
    public double Yaw { get; private set; }
    public double Pitch { get; private set; }
    public double OffsetYaw { get; set; }
    public double OffsetPitch { get; set; }
    public double BaseFov { get; set; } = 70;
    public double CurrentFov { get; set; } = 70;
    public string ViewMode { get; private set; } = "360";
    public string LensMode { get; set; } = "Standard";
    public double SensX { get; set; } = 0.01;
    public double SensZ { get; set; } = 0.01;

    public void Update()
    {
        Yaw = OffsetYaw;
        Pitch = OffsetPitch;

        double half = ViewMode switch { "180" => 90, "120" => 60, _ => 0 };
        if (half > 0)
        {
            double limit = Math.Max(0, half - CurrentFov / 2);
            Yaw = Math.Clamp(Yaw, -limit, limit);
            OffsetYaw = Yaw;
        }
    }

    public void ToggleViewMode()
    {
        ViewMode = ViewMode switch { "360" => "180", "180" => "120", _ => "360" };
        OffsetYaw = 0;
        Yaw = 0;
    }

    public void ChangePitch(double delta)
    {
        OffsetPitch = Math.Clamp(OffsetPitch + delta, -89, 89);
    }

    public void MouseMove(double dx, double dy)
    {
        OffsetYaw += dx * SensX * 5;
        CurrentFov = Math.Clamp(CurrentFov + dy * SensZ * 10, 20, 130);
    }

    public ViewParams GetParams(int outWidth)
    {
        double range = ViewMode switch { "180" => Math.PI, "120" => 2.0 / 3.0 * Math.PI, _ => 2 * Math.PI };
        return new ViewParams
        {
            Yaw = (float)(Yaw * Math.PI / 180),
            Pitch = (float)(-Pitch * Math.PI / 180),
            Focal = (float)(0.5 * outWidth / Math.Tan(0.5 * CurrentFov * Math.PI / 180)),
            Fisheye = LensMode == "Fisheye" ? 1f : 0f,
            Range = (float)range
        };
    }
}
