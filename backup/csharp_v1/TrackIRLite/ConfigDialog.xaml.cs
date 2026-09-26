using System.Globalization;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;

namespace TrackIRLite;

public partial class ConfigDialog : Window
{
    public bool Applied { get; private set; }
    public string LensMode { get; private set; }
    public string InputMode { get; private set; }
    public double BaseFov => FovSlider.Value;
    public int TargetFps => (int)FpsSlider.Value;
    public double SensX => Math.Round(SensXSlider.Value, 3);
    public double SensZ => Math.Round(SensZSlider.Value, 3);
    public double Deadzone => Math.Round(DeadzoneSlider.Value, 1);
    public double Curve => Math.Round(CurveSlider.Value, 1);
    public double Gain => Math.Round(GainSlider.Value, 1);
    public bool AlwaysOnTop => TopmostBox.IsChecked == true;

    public ConfigDialog(string lensMode, double baseFov, int fps, string inputMode, double sensX, double sensZ,
        double deadzone, double curve, double gain, bool topmost)
    {
        InitializeComponent();
        LensMode = lensMode;
        InputMode = inputMode;
        FovSlider.Value = baseFov;
        FpsSlider.Value = fps;
        SensXSlider.Value = sensX;
        SensZSlider.Value = sensZ;
        DeadzoneSlider.Value = deadzone;
        CurveSlider.Value = curve;
        GainSlider.Value = gain;
        TopmostBox.IsChecked = topmost;
        UpdateChoices();
        UpdateCurveLabels();
    }

    private void UpdateChoices()
    {
        Brush accent = (Brush)FindResource("AccentBrush");
        Brush text = (Brush)FindResource("TextBrush");
        BtnStandard.Foreground = LensMode == "Standard" ? accent : text;
        BtnFisheye.Foreground = LensMode == "Fisheye" ? accent : text;
        BtnMouse.Foreground = InputMode == "MOUSE" ? accent : text;
        BtnTrackIR.Foreground = InputMode == "TRACKIR" ? accent : text;
    }

    private void UpdateCurveLabels()
    {
        if (DeadzoneLabel == null || CurveLabel == null || GainLabel == null) return;
        DeadzoneLabel.Text = $"Deadzone: {Format(Deadzone)}";
        CurveLabel.Text = $"Curve: {Format(Curve)}";
        GainLabel.Text = $"Gain: {Format(Gain)}";
    }

    private static string Format(double value) => value.ToString("0.###", CultureInfo.InvariantCulture);

    private void Curve_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e) => UpdateCurveLabels();

    private void Lens_Click(object sender, RoutedEventArgs e)
    {
        LensMode = sender == BtnFisheye ? "Fisheye" : "Standard";
        UpdateChoices();
    }

    private void Input_Click(object sender, RoutedEventArgs e)
    {
        InputMode = sender == BtnTrackIR ? "TRACKIR" : "MOUSE";
        UpdateChoices();
    }

    private void Window_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => DragMove();

    private void Apply_Click(object sender, RoutedEventArgs e)
    {
        Applied = true;
        Close();
    }

    private void Cancel_Click(object sender, RoutedEventArgs e) => Close();
}
