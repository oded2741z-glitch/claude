using System.Diagnostics;
using OpenCvSharp;

namespace TrackIRLite;

public sealed class FrameReader : IDisposable
{
    private const int MaxWidth = 2500;

    private readonly VideoCapture cap;
    private readonly Thread? thread;
    private readonly object frameLock = new();
    private readonly bool isFile;
    private readonly double frameTime;
    private volatile bool running;
    private Mat front = new();
    private Mat back = new();
    private long frameId;

    public bool IsOpened { get; }

    public FrameReader(string source)
    {
        cap = int.TryParse(source, out int index) ? new VideoCapture(index) : new VideoCapture(source);
        IsOpened = cap.IsOpened();
        if (!IsOpened) return;

        cap.Set(VideoCaptureProperties.BufferSize, 1);
        isFile = cap.Get(VideoCaptureProperties.FrameCount) > 0;
        double fps = cap.Get(VideoCaptureProperties.Fps);
        frameTime = isFile && fps > 0 && fps < 240 ? 1.0 / fps : 0;

        running = true;
        thread = new Thread(Run) { IsBackground = true };
        thread.Start();
    }

    private void Run()
    {
        using var raw = new Mat();
        using var scaled = new Mat();
        var clock = Stopwatch.StartNew();

        while (running)
        {
            double start = clock.Elapsed.TotalSeconds;
            try
            {
                bool ok = cap.Read(raw);
                if (!ok && isFile)
                {
                    cap.Set(VideoCaptureProperties.PosFrames, 0);
                    ok = cap.Read(raw);
                }

                if (ok && !raw.Empty())
                {
                    Mat src = raw;
                    if (raw.Width > MaxWidth)
                    {
                        double scale = (double)MaxWidth / raw.Width;
                        Cv2.Resize(raw, scaled, new Size(0, 0), scale, scale);
                        src = scaled;
                    }
                    Cv2.CvtColor(src, back, ColorConversionCodes.BGR2BGRA);
                    lock (frameLock)
                    {
                        (front, back) = (back, front);
                        frameId++;
                    }
                }
                else
                {
                    Thread.Sleep(10);
                }
            }
            catch
            {
                Thread.Sleep(10);
            }

            if (frameTime > 0)
            {
                double wait = frameTime - (clock.Elapsed.TotalSeconds - start);
                if (wait > 0) Thread.Sleep(TimeSpan.FromSeconds(wait));
            }
        }
        cap.Release();
        cap.Dispose();
        lock (frameLock)
        {
            front.Dispose();
            back.Dispose();
        }
    }

    public bool TryRead(ref long lastId, Action<Mat> use)
    {
        lock (frameLock)
        {
            if (frameId == lastId || front.Empty()) return false;
            lastId = frameId;
            use(front);
            return true;
        }
    }

    public void Dispose()
    {
        if (thread != null)
        {
            running = false;
            thread.Join(1000);
            return;
        }
        cap.Dispose();
        front.Dispose();
        back.Dispose();
    }
}
