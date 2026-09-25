using System.Windows;
using System.Windows.Input;

namespace TrackIRLite;

public partial class CameraIndexDialog : Window
{
    public int? Index { get; private set; }

    public CameraIndexDialog(string title)
    {
        InitializeComponent();
        TitleText.Text = title;
    }

    private void Window_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => DragMove();

    private void Connect_Click(object sender, RoutedEventArgs e)
    {
        if (!int.TryParse(IndexBox.Text, out int value)) return;
        Index = value;
        Close();
    }

    private void Cancel_Click(object sender, RoutedEventArgs e) => Close();
}
