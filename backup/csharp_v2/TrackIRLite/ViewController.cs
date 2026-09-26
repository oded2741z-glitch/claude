using System.Diagnostics;

namespace TrackIRLite;

public class ViewController
{
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private bool animating;
    private double animDYaw, animDPitch, animStart, animAppliedYaw, animAppliedPitch;

    public double Yaw { get; private set; }
    public double Pitch { get; private set; }
    public double OffsetYaw { get; set; }
    public double OffsetPitch { get; set; }
    public double HomeYaw { get; set; }
    public double HomePitch { get; set; }
    public double BaseFov { get; set; } = 70;
    public double CurrentFov { get; set; } = 70;
    public string ViewMode { get; private set; } = "360";
    public string LensMode { get; set; } = "Standard";
    public string InputMode { get; set; } = "MOUSE";
    public double SensX { get; set; } = 0.01;
    public double SensZ { get; set; } = 0.01;
    public double TirDeadzone { get; set; } = 1.0;
    public double TirCurve { get; set; } = 1.5;
    public double TirGain { get; set; } = 2.0;

    public void Update((double Yaw, double Pitch, double Z) tir)
    {
        StepAnimation();

        if (InputMode == "TRACKIR")
        {
            Yaw = ApplyCurve(tir.Yaw) + OffsetYaw;
            Pitch = ApplyCurve(tir.Pitch) + OffsetPitch;
            CurrentFov = Math.Clamp(BaseFov + tir.Z * 1.5, 30, 130);
        }
        else
        {
            Yaw = OffsetYaw;
            Pitch = OffsetPitch;
        }

        double half = ViewMode switch { "180" => 90, "120" => 60, _ => 0 };
        if (half > 0)
        {
            double limit = Math.Max(0, half - CurrentFov / 2);
            Yaw = Math.Clamp(Yaw, -limit, limit);
            if (InputMode == "MOUSE") OffsetYaw = Yaw;
        }
    }

    public double ApplyCurve(double angle)
    {
        const double reference = 45.0;
        double m = Math.Max(0.0, Math.Abs(angle) - TirDeadzone);
        return Math.CopySign(reference * TirGain * Math.Pow(m / (reference - TirDeadzone), TirCurve), angle);
    }

    public double WrapDelta(double d)
    {
        if (ViewMode != "360") return d;
        double r = (d + 180) % 360;
        if (r < 0) r += 360;
        return r - 180;
    }

    public void StartAnimation(double dYaw, double dPitch)
    {
        animating = true;
        animDYaw = dYaw;
        animDPitch = dPitch;
        animStart = clock.Elapsed.TotalSeconds;
        animAppliedYaw = 0;
        animAppliedPitch = 0;
    }

    private void StepAnimation()
    {
        if (!animating) return;
        double p = Math.Min(1.0, (clock.Elapsed.TotalSeconds - animStart) / 0.5);
        double e = p * p * (3 - 2 * p);
        OffsetYaw += animDYaw * e - animAppliedYaw;
        OffsetPitch += animDPitch * e - animAppliedPitch;
        animAppliedYaw = animDYaw * e;
        animAppliedPitch = animDPitch * e;
        if (p >= 1) animating = false;
    }

    public void SetHome()
    {
        HomeYaw = Yaw;
        HomePitch = Pitch;
    }

    public void ResetToHome((double Yaw, double Pitch, double Z) tir)
    {
        CurrentFov = BaseFov;
        double targetYaw, targetPitch;
        if (InputMode == "MOUSE")
        {
            targetYaw = HomeYaw;
            targetPitch = HomePitch;
        }
        else
        {
            targetYaw = HomeYaw - ApplyCurve(tir.Yaw);
            targetPitch = HomePitch - ApplyCurve(tir.Pitch);
        }
        StartAnimation(WrapDelta(targetYaw - OffsetYaw), targetPitch - OffsetPitch);
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
        if (InputMode != "MOUSE") return;
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
