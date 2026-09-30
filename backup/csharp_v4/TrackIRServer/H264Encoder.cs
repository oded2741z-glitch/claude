using FFmpeg.AutoGen;
using OpenCvSharp;

namespace TrackIRServer;

public sealed unsafe class H264Encoder : IDisposable
{
    private AVCodecContext* context;
    private AVFrame* frame;
    private AVPacket* packet;
    private long pts;

    public string Name { get; }
    public int Width { get; }
    public int Height { get; }

    public H264Encoder(int width, int height, int fps, int maxBitrateKbps)
    {
        Width = width;
        Height = height;
        Name = new[] { "h264_nvenc", "libx264" }.FirstOrDefault(name => TryOpen(name, fps, maxBitrateKbps))
            ?? throw new InvalidOperationException("No H.264 encoder available.");

        frame = ffmpeg.av_frame_alloc();
        frame->format = (int)AVPixelFormat.AV_PIX_FMT_YUV420P;
        frame->width = width;
        frame->height = height;
        packet = ffmpeg.av_packet_alloc();
    }

    private bool TryOpen(string name, int fps, int maxBitrateKbps)
    {
        AVCodec* codec = ffmpeg.avcodec_find_encoder_by_name(name);
        if (codec == null) return false;

        AVCodecContext* ctx = ffmpeg.avcodec_alloc_context3(codec);
        ctx->width = Width;
        ctx->height = Height;
        ctx->pix_fmt = AVPixelFormat.AV_PIX_FMT_YUV420P;
        ctx->time_base = new AVRational { num = 1, den = fps };
        ctx->framerate = new AVRational { num = fps, den = 1 };
        ctx->rc_max_rate = maxBitrateKbps * 1000L;
        ctx->rc_buffer_size = (int)(ctx->rc_max_rate / fps * 2);
        ctx->gop_size = fps * 5;
        ctx->max_b_frames = 0;

        if (name == "h264_nvenc")
        {
            SetOption(ctx, "preset", "p1");
            SetOption(ctx, "tune", "ull");
            SetOption(ctx, "rc", "vbr");
            SetOption(ctx, "cq", "23");
            SetOption(ctx, "zerolatency", "1");
            SetOption(ctx, "delay", "0");
        }
        else
        {
            SetOption(ctx, "preset", "veryfast");
            SetOption(ctx, "tune", "zerolatency");
            SetOption(ctx, "crf", "23");
        }
        SetOption(ctx, "forced-idr", "1");

        if (ffmpeg.avcodec_open2(ctx, codec, null) < 0)
        {
            ffmpeg.avcodec_free_context(&ctx);
            return false;
        }
        context = ctx;
        return true;
    }

    private static void SetOption(AVCodecContext* ctx, string key, string value)
    {
        ffmpeg.av_opt_set(ctx->priv_data, key, value, 0);
    }

    public void Encode(Mat i420, bool keyframe, Action<IntPtr, int, bool> output)
    {
        byte* data = (byte*)i420.Data;
        int ySize = Width * Height;
        frame->data[0] = data;
        frame->data[1] = data + ySize;
        frame->data[2] = data + ySize * 5 / 4;
        frame->linesize[0] = Width;
        frame->linesize[1] = Width / 2;
        frame->linesize[2] = Width / 2;
        frame->pts = pts++;
        frame->pict_type = keyframe ? AVPictureType.AV_PICTURE_TYPE_I : AVPictureType.AV_PICTURE_TYPE_NONE;

        if (ffmpeg.avcodec_send_frame(context, frame) < 0) return;
        while (ffmpeg.avcodec_receive_packet(context, packet) == 0)
        {
            output((IntPtr)packet->data, packet->size, (packet->flags & ffmpeg.AV_PKT_FLAG_KEY) != 0);
            ffmpeg.av_packet_unref(packet);
        }
    }

    public void Dispose()
    {
        AVFrame* f = frame;
        ffmpeg.av_frame_free(&f);
        AVPacket* p = packet;
        ffmpeg.av_packet_free(&p);
        AVCodecContext* c = context;
        ffmpeg.avcodec_free_context(&c);
        frame = null;
        packet = null;
        context = null;
    }
}
