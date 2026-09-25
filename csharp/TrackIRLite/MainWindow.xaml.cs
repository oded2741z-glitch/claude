using System.Windows;
using System.Windows.Input;

namespace TrackIRLite;

public partial class MainWindow : Window
{
    private readonly Settings settings;
    private bool isFullscreen;
    private Rect savedBounds;

    public MainWindow()
    {
        InitializeComponent();
        settings = Settings.Load();
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
