using System.Globalization;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace TrackIRLite;

public class HudOverlay : FrameworkElement
{
    private static readonly Brush Accent = Frozen(new SolidColorBrush(Color.FromRgb(0xFF, 0x66, 0x00)));
    private static readonly Pen AccentPen = Frozen(new Pen(Accent, 1));
    private static readonly Pen BoxPen = Frozen(new Pen(Accent, 2));
    private static readonly Pen FramePen = Frozen(new Pen(new SolidColorBrush(Color.FromRgb(0x33, 0x33, 0x33)), 1));
    private static readonly Typeface Font = new("Segoe UI");

    public ViewController? View { get; set; }
    public bool ShowHud { get; set; } = true;
    public bool ShowPanorama { get; set; }
    public WriteableBitmap? Strip { get; set; }
    public Rect? PanoRect { get; private set; }
    public double PanoRange { get; private set; }

    private object? lastState;

    private static T Frozen<T>(T f) where T : Freezable
    {
        f.Freeze();
        return f;
    }

    public (int Width, int Height)? GetStripSize()
    {
        double w = ActualWidth, h = ActualHeight;
        int sw = (int)Math.Min(640, w * 0.6);
        int sh = Math.Max(30, sw / 8);
        if (sw < 50 || h - 50 - sh < 0) return null;
        return (sw, sh);
    }

    public void Refresh()
    {
        if (View == null) return;
        var state = (View.Yaw, View.Pitch, View.CurrentFov, View.HomeYaw, View.ViewMode, View.LensMode,
            ShowHud, ShowPanorama, Strip, ActualWidth, ActualHeight);
        if (state.Equals(lastState)) return;
        lastState = state;
        InvalidateVisual();
    }

    protected override void OnRender(DrawingContext dc)
    {
        PanoRect = null;
        if (View == null) return;
        double w = ActualWidth, h = ActualHeight;
        if (ShowPanorama) DrawPanorama(dc, w, h);
        if (ShowHud) DrawHud(dc, w, h);
    }

    private void DrawPanorama(DrawingContext dc, double w, double h)
    {
        if (View!.LensMode == "Fisheye" || Strip == null || GetStripSize() is not (int sw, int sh)) return;

        double rng = View.ViewMode switch { "180" => 180, "120" => 120, _ => 360 };
        double x0 = Math.Floor((w - sw) / 2), y0 = Math.Floor(h - 50 - sh);
        var area = new Rect(x0, y0, sw, sh);

        dc.DrawImage(Strip, area);
        dc.PushClip(new RectangleGeometry(area));
        int bw = Math.Max(4, (int)(View.CurrentFov / rng * sw));
        int bc = (int)((0.5 - View.Yaw / rng) * sw);
        foreach (int off in rng == 360 ? new[] { -sw, 0, sw } : new[] { 0 })
        {
            double left = bc + off - bw / 2;
            dc.DrawRectangle(null, BoxPen, new Rect(x0 + left, y0, bw, sh - 1));
        }
        dc.Pop();
        dc.DrawRectangle(null, FramePen, new Rect(x0 - 0.5, y0 - 0.5, sw + 1, sh + 1));

        PanoRect = area;
        PanoRange = rng;
    }

    private void DrawHud(DrawingContext dc, double w, double h)
    {
        var v = View!;
        double heading = Mod(-(v.Yaw - v.HomeYaw), 360);
        double cx = Math.Floor(w / 2), ty = 50, half = Math.Floor(w * 0.3);
        double span = v.CurrentFov / 2 * (half / (w / 2));
        double tape = ty + 14;

        Line(dc, cx - half, tape, cx + half, tape);
        for (int d = (int)Math.Floor(heading - span); d <= (int)Math.Ceiling(heading + span); d++)
        {
            if (d % 5 != 0) continue;
            double x = Math.Floor(cx + (d - heading) / span * half);
            bool big = d % 10 == 0;
            Line(dc, x, tape, x, tape - (big ? 10 : 5));
            if (big && Math.Abs(x - cx) > 20)
                Text(dc, ((int)Mod(d, 360)).ToString(), 12.5, x, ty, 0.5);
        }
        Triangle(dc, new Point(cx, ty + 16), new Point(cx - 5, ty + 24), new Point(cx + 5, ty + 24));
        Text(dc, ((int)Mod(Math.Round(heading), 360)).ToString("000"), 16, cx, ty + 42, 0.5);

        double elev = -v.Pitch;
        double px = w - 50, pcy = Math.Floor(h / 2), phalf = 80, prange = 30;
        Line(dc, px, pcy - phalf, px, pcy + phalf);
        for (int d = (int)Math.Floor(elev - prange); d <= (int)Math.Ceiling(elev + prange); d++)
        {
            if (d % 10 != 0) continue;
            double y = Math.Floor(pcy - (d - elev) / prange * phalf);
            Line(dc, px, y, px + 8, y);
            Text(dc, d.ToString(), 11, px + 12, y + 4, 0);
        }
        Triangle(dc, new Point(px - 2, pcy), new Point(px - 10, pcy - 5), new Point(px - 10, pcy + 5));
        int elevRounded = (int)Math.Round(elev);
        Text(dc, elevRounded.ToString("+0;-0;+0"), 14, px - 14, pcy + 5, 1);

        Text(dc, $"FOV {v.CurrentFov:0}", 14, 15, ty + 18, 0);
    }

    private static double Mod(double a, double m) => ((a % m) + m) % m;

    private static void Line(DrawingContext dc, double x1, double y1, double x2, double y2)
    {
        dc.DrawLine(AccentPen, new Point(x1 + 0.5, y1 + 0.5), new Point(x2 + 0.5, y2 + 0.5));
    }

    private static void Triangle(DrawingContext dc, Point a, Point b, Point c)
    {
        var geometry = new StreamGeometry();
        using (StreamGeometryContext ctx = geometry.Open())
        {
            ctx.BeginFigure(a, true, true);
            ctx.PolyLineTo(new[] { b, c }, true, false);
        }
        geometry.Freeze();
        dc.DrawGeometry(Accent, null, geometry);
    }

    private void Text(DrawingContext dc, string text, double size, double x, double baseline, double align)
    {
        var ft = new FormattedText(text, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, Font, size, Accent,
            VisualTreeHelper.GetDpi(this).PixelsPerDip);
        dc.DrawText(ft, new Point(x - ft.Width * align, baseline - ft.Baseline));
    }
}
