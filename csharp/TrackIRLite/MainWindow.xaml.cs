using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using OpenCvSharp;
using Point = System.Windows.Point;
using Rect = System.Windows.Rect;
using Window = System.Windows.Window;

namespace TrackIRLite;

public partial class MainWindow : Window
{
    private readonly Settings settings;
    private bool isFullscreen;
    private Rect savedBounds;

    private readonly ViewController view = new();
    private D3DRenderer? renderer;
    private TrackIRDevice? trackir;
    private FrameReader? cap;
    private long lastFrameId;
    private Point? lastMouse;
    private readonly Mat stripMat = new();
    private WriteableBitmap? strip;
    private readonly DispatcherTimer barsTimer = new() { Interval = TimeSpan.FromMilliseconds(200) };
    private bool barsVisible = true;
    private DateTime lastBarActivity = DateTime.Now;
    private int updateDelay = 15;
    private readonly System.Diagnostics.Stopwatch renderClock = System.Diagnostics.Stopwatch.StartNew();
    private double lastRenderMs = double.MinValue;
    private double lastStripMs = double.MinValue;

    private FrameReader? pipCap;
    private bool pipEnabled;
    private long lastPipId;
    private WriteableBitmap? pipBitmap;
    private string? pipInteraction;
    private Point pipDragStart;
    private (int W, int X, int Y) pipOrig;

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint { public int X, Y; }

    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out NativePoint point);

    public MainWindow()
    {
        InitializeComponent();
        settings = Settings.Load();
        view.BaseFov = settings.BaseFov;
        view.CurrentFov = settings.BaseFov;
        view.HomeYaw = settings.HomeYaw;
        view.HomePitch = settings.HomePitch;
        view.OffsetYaw = settings.HomeYaw;
        view.OffsetPitch = settings.HomePitch;
        view.TirDeadzone = settings.TirDeadzone;
        view.TirCurve = settings.TirCurve;
        view.TirGain = settings.TirGain;
        Hud.View = view;
        barsTimer.Tick += BarsTick;
    }

    private void Window_Loaded(object sender, RoutedEventArgs e)
    {
        trackir = new TrackIRDevice(new WindowInteropHelper(this).Handle);
        view.InputMode = trackir.Connected ? "TRACKIR" : "MOUSE";
        UpdateControlsVisibility();

        if (settings.PipIndex != -1)
        {
            pipCap = new FrameReader(settings.PipIndex.ToString(), HardwareDecoding);
            if (pipCap.IsOpened) pipEnabled = true;
            else settings.PipIndex = -1;
        }
        UpdatePipButton();

        try
        {
            renderer = new D3DRenderer(new WindowInteropHelper(this).Handle);
            VideoImage.Source = renderer.Image;
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "GPU Error", MessageBoxButton.OK, MessageBoxImage.Error);
        }

        if (settings.LastMainSource != null) LoadSource(settings.LastMainSource, silentFail: true);
        CompositionTarget.Rendering += OnRendering;
        ShowBars();
        barsTimer.Start();
    }

    private void Window_Closed(object? sender, EventArgs e)
    {
        CompositionTarget.Rendering -= OnRendering;
        barsTimer.Stop();
        cap?.Dispose();
        pipCap?.Dispose();
        trackir?.Dispose();
        renderer?.Dispose();
    }

    private (double Yaw, double Pitch, double Z) ReadTrackIR()
    {
        return trackir?.GetData() ?? (0, 0, 0);
    }

    private void UpdateControlsVisibility()
    {
        PitchControls.Visibility = view.InputMode == "MOUSE" ? Visibility.Visible : Visibility.Collapsed;
    }

    private void Window_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.F5) ResetToHome();
        else if (e.Key == Key.F6) SetHome();
        else if (e.Key == Key.H) Hud.ShowHud = !Hud.ShowHud;
    }

    private void SetHome()
    {
        view.SetHome();
        settings.HomeYaw = view.HomeYaw;
        settings.HomePitch = view.HomePitch;
        settings.Save();
    }

    private void ResetToHome()
    {
        view.ResetToHome(ReadTrackIR());
    }

    private void BtnSetHome_Click(object sender, RoutedEventArgs e) => SetHome();
    private void BtnReset_Click(object sender, RoutedEventArgs e) => ResetToHome();

    private void OnRendering(object? sender, EventArgs e)
    {
        if (renderer == null || cap == null) return;
        double now = renderClock.Elapsed.TotalMilliseconds;
        if (now - lastRenderMs < updateDelay - 1) return;
        lastRenderMs = now;

        cap.TryRead(ref lastFrameId, OnFrame);
        if (!renderer.HasSource) return;

        view.Update(view.InputMode == "TRACKIR" ? ReadTrackIR() : (0, 0, 0));
        DpiScale dpi = VisualTreeHelper.GetDpi(this);
        int width = (int)Math.Round(VideoArea.ActualWidth * dpi.DpiScaleX);
        int height = (int)Math.Round(VideoArea.ActualHeight * dpi.DpiScaleY);
        renderer.Render(width, height, view.GetParams(width));
        UpdatePip();

        Hud.ShowPanorama = barsVisible;
        Hud.Strip = strip;
        Hud.Refresh();
    }

    private void OnFrame(Mat frame)
    {
        renderer!.UploadFrame(frame);
        double now = renderClock.Elapsed.TotalMilliseconds;
        if (barsVisible && view.LensMode != "Fisheye" && now - lastStripMs >= 100)
        {
            lastStripMs = now;
            UpdateStrip(frame);
        }
    }

    private void UpdatePip()
    {
        if (!pipEnabled || pipCap == null)
        {
            PipBox.Visibility = Visibility.Collapsed;
            return;
        }
        pipCap.TryRead(ref lastPipId, frame =>
        {
            if (pipBitmap == null || pipBitmap.PixelWidth != frame.Width || pipBitmap.PixelHeight != frame.Height)
            {
                pipBitmap = new WriteableBitmap(frame.Width, frame.Height, 96, 96, PixelFormats.Bgra32, null);
                PipImage.Source = pipBitmap;
            }
            int stride = (int)frame.Step();
            pipBitmap.WritePixels(new Int32Rect(0, 0, frame.Width, frame.Height), frame.Data, stride * frame.Height, stride);
        });
        if (pipBitmap == null) return;

        int winW = (int)VideoArea.ActualWidth, winH = (int)VideoArea.ActualHeight;
        int pw = settings.PipWidth, ph = (int)(pw * 9 / 16.0);
        if (settings.PipX == -1) settings.PipX = (winW - pw) / 2;
        settings.PipX = Math.Max(0, Math.Min(settings.PipX, winW - pw));
        settings.PipY = Math.Max(0, Math.Min(settings.PipY, winH - ph));

        bool fits = settings.PipX + pw <= winW && settings.PipY + ph <= winH;
        PipBox.Visibility = fits ? Visibility.Visible : Visibility.Collapsed;
        PipBox.Width = pw;
        PipBox.Height = ph;
        System.Windows.Controls.Canvas.SetLeft(PipBox, settings.PipX);
        System.Windows.Controls.Canvas.SetTop(PipBox, settings.PipY);
    }

    private void UpdatePipButton()
    {
        BtnPip.Content = pipEnabled ? "PiP: ON" : "PiP: OFF";
        BtnPip.Foreground = (Brush)FindResource(pipEnabled ? "AccentBrush" : "TextBrush");
    }

    private void OnPipSelected(int index)
    {
        pipCap?.Dispose();
        pipCap = new FrameReader(index.ToString(), HardwareDecoding);
        lastPipId = 0;
        if (pipCap.IsOpened)
        {
            pipEnabled = true;
            settings.PipIndex = index;
            UpdatePipButton();
            settings.Save();
        }
    }

    private void BtnPip_Click(object sender, RoutedEventArgs e)
    {
        if (!pipEnabled)
        {
            if (settings.PipIndex != -1)
            {
                pipCap?.Dispose();
                pipCap = new FrameReader(settings.PipIndex.ToString(), HardwareDecoding);
                lastPipId = 0;
                if (pipCap.IsOpened)
                {
                    pipEnabled = true;
                    UpdatePipButton();
                    return;
                }
            }
            int? index = AskCameraIndex("PIP USB CAMERA");
            if (index != null) OnPipSelected(index.Value);
        }
        else
        {
            pipEnabled = false;
            UpdatePipButton();
            pipCap?.Dispose();
            pipCap = null;
            settings.Save();
        }
    }

    private void BtnConfig_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new ConfigDialog(view.LensMode, view.BaseFov, Math.Clamp(1000 / updateDelay, 15, 60), view.InputMode,
            view.SensX, view.SensZ, view.TirDeadzone, view.TirCurve, view.TirGain, Topmost, settings.VideoDecoding) { Owner = this };
        dialog.ShowDialog();
        if (!dialog.Applied) return;

        view.LensMode = dialog.LensMode;
        view.BaseFov = dialog.BaseFov;
        view.CurrentFov = view.BaseFov;
        updateDelay = 1000 / dialog.TargetFps;

        string input = dialog.InputMode;
        if (input == "TRACKIR" && (trackir == null || !trackir.Connected))
        {
            MessageBox.Show(this, "TrackIR not connected.", "Error", MessageBoxButton.OK, MessageBoxImage.Error);
            input = "MOUSE";
        }
        view.InputMode = input;
        UpdateControlsVisibility();
        view.SensX = dialog.SensX;
        view.SensZ = dialog.SensZ;
        view.TirDeadzone = dialog.Deadzone;
        view.TirCurve = dialog.Curve;
        view.TirGain = dialog.Gain;
        Topmost = dialog.AlwaysOnTop;

        settings.BaseFov = view.BaseFov;
        settings.TirDeadzone = view.TirDeadzone;
        settings.TirCurve = view.TirCurve;
        settings.TirGain = view.TirGain;
        bool decodingChanged = dialog.Decoding != settings.VideoDecoding;
        settings.VideoDecoding = dialog.Decoding;
        settings.Save();
        if (decodingChanged) ReopenSources();
    }

    private void UpdateStrip(Mat frame)
    {
        if (Hud.GetStripSize() is not (int sw, int sh)) return;
        DpiScale dpi = VisualTreeHelper.GetDpi(this);
        int pw = (int)Math.Round(sw * dpi.DpiScaleX);
        int ph = (int)Math.Round(sh * dpi.DpiScaleY);

        int band = Math.Min(frame.Height, Math.Max(1, frame.Width * sh / sw));
        int top = (frame.Height - band) / 2;
        using (var roi = new Mat(frame, new OpenCvSharp.Rect(0, top, frame.Width, band)))
            Cv2.Resize(roi, stripMat, new OpenCvSharp.Size(pw, ph), 0, 0, InterpolationFlags.Area);

        if (strip == null || strip.PixelWidth != pw || strip.PixelHeight != ph)
            strip = new WriteableBitmap(pw, ph, 96 * dpi.DpiScaleX, 96 * dpi.DpiScaleY, PixelFormats.Bgra32, null);
        int stride = (int)stripMat.Step();
        strip.WritePixels(new Int32Rect(0, 0, pw, ph), stripMat.Data, stride * ph, stride);
    }

    private void ShowBars()
    {
        lastBarActivity = DateTime.Now;
        if (barsVisible) return;
        TopBar.Visibility = Visibility.Visible;
        BottomBar.Visibility = Visibility.Visible;
        barsVisible = true;
    }

    private void HideBars()
    {
        if (!barsVisible) return;
        TopBar.Visibility = Visibility.Collapsed;
        BottomBar.Visibility = Visibility.Collapsed;
        barsVisible = false;
    }

    private void BarsTick(object? sender, EventArgs e)
    {
        try
        {
            GetCursorPos(out NativePoint cursor);
            Point p = PointFromScreen(new Point(cursor.X, cursor.Y));
            double w = ActualWidth, h = ActualHeight;
            if (p.X >= 0 && p.X <= w && ((p.Y >= 0 && p.Y < 50) || (p.Y > h - 60 && p.Y <= h))) ShowBars();
            else if ((DateTime.Now - lastBarActivity).TotalSeconds > 2) HideBars();
        }
        catch { }
    }

    private void VideoArea_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        Point p = e.GetPosition(Hud);
        if (Hud.PanoRect is Rect pano && pano.Contains(p))
        {
            double target = (0.5 - (p.X - pano.X) / pano.Width) * Hud.PanoRange;
            view.StartAnimation(view.WrapDelta(target - view.Yaw), 0);
            return;
        }
        PipMouseDown(e.GetPosition(VideoArea));
    }

    private void PipMouseDown(Point p)
    {
        if (!pipEnabled) return;
        int pw = settings.PipWidth, px = settings.PipX, py = settings.PipY, ph = (int)(pw * 9 / 16.0);
        if (p.X < px || p.X > px + pw || p.Y < py || p.Y > py + ph) return;

        bool corner = p.X >= px + pw - 20 && p.Y >= py + ph - 20;
        pipInteraction = corner ? "resize" : "move";
        pipDragStart = p;
        pipOrig = (pw, px, py);
        VideoArea.CaptureMouse();
    }

    private void PipMouseDrag(Point p)
    {
        if (!pipEnabled || pipInteraction == null) return;
        int dx = (int)(p.X - pipDragStart.X), dy = (int)(p.Y - pipDragStart.Y);
        if (pipInteraction == "move")
        {
            settings.PipX = pipOrig.X + dx;
            settings.PipY = pipOrig.Y + dy;
        }
        else
        {
            int winW = (int)VideoArea.ActualWidth, winH = (int)VideoArea.ActualHeight;
            int newW = Math.Max(150, Math.Min(winW, pipOrig.W + dx));
            if ((int)(newW * 9 / 16.0) > winH) newW = (int)(winH * 16 / 9.0);
            settings.PipWidth = newW;
        }
    }

    private void VideoArea_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (pipInteraction == null) return;
        pipInteraction = null;
        VideoArea.ReleaseMouseCapture();
        settings.Save();
    }

    private void VideoArea_MouseMove(object sender, MouseEventArgs e)
    {
        Point p = e.GetPosition(VideoArea);
        if (e.LeftButton == MouseButtonState.Pressed)
        {
            PipMouseDrag(p);
            lastMouse = p;
            return;
        }
        if (lastMouse is Point last)
        {
            DpiScale dpi = VisualTreeHelper.GetDpi(this);
            view.MouseMove((p.X - last.X) * dpi.DpiScaleX, (p.Y - last.Y) * dpi.DpiScaleY);
        }
        lastMouse = p;
    }

    private void VideoArea_MouseLeave(object sender, MouseEventArgs e)
    {
        lastMouse = null;
    }

    private void BtnMode_Click(object sender, RoutedEventArgs e)
    {
        view.ToggleViewMode();
        BtnMode.Content = $"Mode: {view.ViewMode}";
    }

    private void BtnPitchDown_Click(object sender, RoutedEventArgs e) => view.ChangePitch(-5);
    private void BtnPitchUp_Click(object sender, RoutedEventArgs e) => view.ChangePitch(5);

    private bool HardwareDecoding => settings.VideoDecoding == "GPU";

    private void ReopenSources()
    {
        if (cap != null && settings.LastMainSource != null)
        {
            cap.Dispose();
            cap = new FrameReader(settings.LastMainSource, HardwareDecoding);
            lastFrameId = 0;
            if (!cap.IsOpened)
            {
                cap.Dispose();
                cap = null;
            }
        }
        if (pipEnabled && settings.PipIndex != -1)
        {
            pipCap?.Dispose();
            pipCap = new FrameReader(settings.PipIndex.ToString(), HardwareDecoding);
            lastPipId = 0;
        }
    }

    private void LoadSource(string source, bool silentFail)
    {
        cap?.Dispose();
        cap = new FrameReader(source, HardwareDecoding);
        if (cap.IsOpened)
        {
            lastFrameId = 0;
            settings.LastMainSource = source;
            settings.Save();
            ResetToHome();
        }
        else
        {
            cap.Dispose();
            cap = null;
            if (!silentFail)
                MessageBox.Show(this, "Could not open source.", "Error", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private int? AskCameraIndex(string title)
    {
        var dialog = new CameraIndexDialog(title) { Owner = this };
        dialog.ShowDialog();
        return dialog.Index;
    }

    private void BtnLoadStream_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new StreamDialog(settings.RtspHistory) { Owner = this };
        dialog.ShowDialog();

        switch (dialog.Choice)
        {
            case StreamChoice.Url:
                string url = dialog.Url;
                if (url.Length == 0) break;
                settings.RtspHistory.Remove(url);
                settings.RtspHistory.Add(url);
                if (settings.RtspHistory.Count > 5) settings.RtspHistory.RemoveAt(0);
                settings.Save();
                LoadSource(url, silentFail: false);
                break;

            case StreamChoice.MainCam:
                int? index = AskCameraIndex("MAIN USB CAMERA");
                if (index != null) LoadSource(index.Value.ToString(), silentFail: false);
                break;

            case StreamChoice.PipCam:
                int? pipIndex = AskCameraIndex("PIP USB CAMERA");
                if (pipIndex != null) OnPipSelected(pipIndex.Value);
                break;
        }
    }

    private void BtnReloadCams_Click(object sender, RoutedEventArgs e)
    {
        cap?.Dispose();
        cap = null;
        if (settings.LastMainSource != null) LoadSource(settings.LastMainSource, silentFail: false);

        if (pipEnabled && settings.PipIndex != -1)
        {
            pipCap?.Dispose();
            pipCap = new FrameReader(settings.PipIndex.ToString(), HardwareDecoding);
            lastPipId = 0;
        }

        if (trackir == null || !trackir.Connected)
        {
            trackir?.Dispose();
            trackir = new TrackIRDevice(new WindowInteropHelper(this).Handle);
            if (trackir.Connected)
            {
                view.InputMode = "TRACKIR";
                UpdateControlsVisibility();
            }
        }
    }

    private void TopBar_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (!isFullscreen) DragMove();
    }

    private void BtnFull_Click(object sender, RoutedEventArgs e)
    {
        isFullscreen = !isFullscreen;
        if (isFullscreen)
        {
            savedBounds = new Rect(Left, Top, Width, Height);
            Left = 0;
            Top = 0;
            Width = SystemParameters.PrimaryScreenWidth;
            Height = SystemParameters.PrimaryScreenHeight;
        }
        else
        {
            Left = savedBounds.Left;
            Top = savedBounds.Top;
            Width = savedBounds.Width;
            Height = savedBounds.Height;
        }
    }

    private void BtnQuit_Click(object sender, RoutedEventArgs e)
    {
        Close();
    }
}
