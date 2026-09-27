using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace TrackIRLite;

public enum StreamChoice { Cancel, Url, MainCam, PipCam }

public partial class StreamDialog : Window
{
    public StreamChoice Choice { get; private set; } = StreamChoice.Cancel;
    public string Url => UrlBox.Text.Trim();

    public StreamDialog(IReadOnlyList<string> history)
    {
        InitializeComponent();
        foreach (string url in history.Skip(Math.Max(0, history.Count - 3)).Reverse())
        {
            var button = new Button
            {
                Content = url.Length <= 40 ? url : url[..37] + "...",
                Style = (Style)FindResource("HistoryButton")
            };
            button.Click += (_, _) => UrlBox.Text = url;
            HistoryPanel.Children.Add(button);
        }
    }

    private void Window_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => DragMove();

    private void Paste_Click(object sender, RoutedEventArgs e)
    {
        try { UrlBox.Text = Clipboard.GetText(); }
        catch { }
    }

    private void Url_Click(object sender, RoutedEventArgs e) => Finish(StreamChoice.Url);
    private void MainCam_Click(object sender, RoutedEventArgs e) => Finish(StreamChoice.MainCam);
    private void PipCam_Click(object sender, RoutedEventArgs e) => Finish(StreamChoice.PipCam);
    private void Cancel_Click(object sender, RoutedEventArgs e) => Finish(StreamChoice.Cancel);

    private void Finish(StreamChoice choice)
    {
        Choice = choice;
        Close();
    }
}
