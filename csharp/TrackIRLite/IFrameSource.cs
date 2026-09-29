using OpenCvSharp;

namespace TrackIRLite;

public interface IFrameSource : IDisposable
{
    bool IsOpened { get; }
    bool TryRead(ref long lastId, Action<Mat> use);
}
