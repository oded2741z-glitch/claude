using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using OpenCvSharp;
using Rect = System.Windows.Rect;
using Window = System.Windows.Window;

namespace TrackIRLite;

public partial class MainWindow : Window
{
    private readonly Settings settings;
    private bool isFullscreen;
    private Rect savedBounds;

    private FrameReader? cap;
    private long lastFrameId;
    private WriteableBitmap? videoBitmap;

    public MainWindow()
    {
        InitializeComponent();
        settings = Settings.Load();
    }

    private void Window_Loaded(object sender, RoutedEventArgs e)
    {
        if (settings.LastMainSource != null) LoadSource(settings.LastMainSource, silentFail: true);
        CompositionTarget.Rendering += OnRendering;
    }

    private void Window_Closed(object? sender, EventArgs e)
    {
        CompositionTarget.Rendering -= OnRendering;
        cap?.Dispose();
    }

    private void OnRendering(object? sender, EventArgs e)
    {
        cap?.TryRead(ref lastFrameId, ShowFrame);
    }

    private void ShowFrame(Mat frame)
    {
        if (videoBitmap == null || videoBitmap.PixelWidth != frame.Width || videoBitmap.PixelHeight != frame.Height)
        {
            videoBitmap = new WriteableBitmap(frame.Width, frame.Height, 96, 96, PixelFormats.Bgra32, null);
            VideoImage.Source = videoBitmap;
        }
        int stride = (int)frame.Step();
        videoBitmap.WritePixels(new Int32Rect(0, 0, frame.Width, frame.Height), frame.Data, stride * frame.Height, stride);
    }

    private void LoadSource(string source, bool silentFail)
    {
        cap?.Dispose();
        cap = new FrameReader(source);
        if (cap.IsOpened)
        {
            lastFrameId = 0;
            settings.LastMainSource = source;
            settings.Save();
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
