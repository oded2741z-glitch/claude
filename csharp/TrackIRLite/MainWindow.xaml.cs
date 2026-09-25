using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Interop;
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
    }

    private void Window_Closed(object? sender, EventArgs e)
    {
        CompositionTarget.Rendering -= OnRendering;
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
        cap.TryRead(ref lastFrameId, renderer.UploadFrame);
        if (!renderer.HasSource) return;

        view.Update(view.InputMode == "TRACKIR" ? ReadTrackIR() : (0, 0, 0));
        DpiScale dpi = VisualTreeHelper.GetDpi(this);
        int width = (int)Math.Round(VideoArea.ActualWidth * dpi.DpiScaleX);
        int height = (int)Math.Round(VideoArea.ActualHeight * dpi.DpiScaleY);
        renderer.Render(width, height, view.GetParams(width));
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
