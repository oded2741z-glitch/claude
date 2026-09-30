using TrackIRLite;

namespace TrackIRServer;

public sealed class CameraStreamer
{
    private readonly int camera;
    private readonly int width;
    private readonly int height;
    private readonly StreamServer server;
    private readonly StreamOutput[] outputs;
    private readonly string label;
    private bool missingReported;
    private bool requestReported;

    public CameraStreamer(int camera, int width, int height, StreamServer server, params StreamOutput[] outputs)
    {
        this.camera = camera;
        this.width = width;
        this.height = height;
        this.server = server;
        this.outputs = outputs;
        label = string.Join("/", outputs.Select(o => o.Name));
    }

    public void Start()
    {
        new Thread(Run) { IsBackground = true }.Start();
    }

    private void Run()
    {
        while (true)
        {
            try
            {
                RunCamera();
            }
            catch (Exception ex)
            {
                Program.Log($"{label}: {ex.Message}");
                Thread.Sleep(3000);
            }
        }
    }

    private void RunCamera()
    {
        bool manual = width > 0 && height > 0;
        using var reader = new FrameReader(camera.ToString(), true, manual ? width : 10000, manual ? height : 10000);
        if (!reader.IsOpened)
        {
            bool requested = outputs.Any(o => server.ViewerCount(o.Stream) > 0);
            if (!missingReported || (requested && !requestReported))
                Program.Log($"{label}: camera {camera} not found, retrying...");
            missingReported = true;
            requestReported = requested;
            Thread.Sleep(3000);
            return;
        }
        missingReported = false;
        requestReported = false;

        int fps = reader.Fps is > 0 and < 240 ? (int)Math.Round(reader.Fps) : 30;
        foreach (StreamOutput output in outputs) output.Open(camera, fps);
        long lastId = 0;

        try
        {
            while (reader.WaitForFrame(5000))
            {
                foreach (StreamOutput output in outputs) output.CheckViewers();
                reader.TryRead(ref lastId, frame =>
                {
                    foreach (StreamOutput output in outputs) output.Prepare(frame);
                });
                foreach (StreamOutput output in outputs) output.Encode();
            }
            Program.Log($"{label}: camera {camera} stopped, reopening...");
        }
        finally
        {
            foreach (StreamOutput output in outputs) output.Close();
        }
    }
}
