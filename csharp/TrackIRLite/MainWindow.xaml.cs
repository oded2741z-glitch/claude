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
        cap.TryRead(ref lastFrameId, OnFrame);
        if (!renderer.HasSource) return;

        view.Update(view.InputMode == "TRACKIR" ? ReadTrackIR() : (0, 0, 0));
        DpiScale dpi = VisualTreeHelper.GetDpi(this);
        int width = (int)Math.Round(VideoArea.ActualWidth * dpi.DpiScaleX);
        int height = (int)Math.Round(VideoArea.ActualHeight * dpi.DpiScaleY);
        renderer.Render(width, height, view.GetParams(width));

        Hud.ShowPanorama = barsVisible;
        Hud.Strip = strip;
        Hud.InvalidateVisual();
    }

    private void OnFrame(Mat frame)
    {
        renderer!.UploadFrame(frame);
        if (barsVisible && view.LensMode != "Fisheye") UpdateStrip(frame);
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
        }
    }

    private void VideoArea_MouseMove(object sender, MouseEventArgs e)
    {
        Point p = e.GetPosition(VideoArea);
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

    private void LoadSource(string source, bool silentFail)
    {
        cap?.Dispose();
        cap = new FrameReader(source);
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
        }
    }

    private void BtnReloadCams_Click(object sender, RoutedEventArgs e)
    {
        cap?.Dispose();
        cap = null;
        if (settings.LastMainSource != null) LoadSource(settings.LastMainSource, silentFail: false);

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
