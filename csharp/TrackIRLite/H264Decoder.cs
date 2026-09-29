using FFmpeg.AutoGen;
using OpenCvSharp;

namespace TrackIRLite;

public sealed unsafe class H264Decoder : IDisposable
{
    private AVCodecContext* context;
    private AVFrame* frame;
    private AVFrame* transfer;
    private AVPacket* packet;
    private SwsContext* scaler;

    public H264Decoder(bool hardware)
    {
        AVCodec* codec = ffmpeg.avcodec_find_decoder(AVCodecID.AV_CODEC_ID_H264);
        context = ffmpeg.avcodec_alloc_context3(codec);
        context->flags |= ffmpeg.AV_CODEC_FLAG_LOW_DELAY;
        context->thread_type = ffmpeg.FF_THREAD_SLICE;

        if (hardware)
        {
            AVBufferRef* device = null;
            if (ffmpeg.av_hwdevice_ctx_create(&device, AVHWDeviceType.AV_HWDEVICE_TYPE_D3D11VA, null, null, 0) >= 0)
                context->hw_device_ctx = device;
        }

        if (ffmpeg.avcodec_open2(context, codec, null) < 0)
        {
            Dispose();
            throw new InvalidOperationException("Cannot open H.264 decoder.");
        }
        frame = ffmpeg.av_frame_alloc();
        transfer = ffmpeg.av_frame_alloc();
        packet = ffmpeg.av_packet_alloc();
    }

    public bool Decode(byte[] data, int size, Mat output)
    {
        int result;
        fixed (byte* p = data)
        {
            packet->data = p;
            packet->size = size;
            result = ffmpeg.avcodec_send_packet(context, packet);
            packet->data = null;
            packet->size = 0;
        }
        if (result < 0) return false;

        bool decoded = false;
        while (ffmpeg.avcodec_receive_frame(context, frame) == 0)
        {
            AVFrame* source = frame;
            if (frame->hw_frames_ctx != null)
                source = ffmpeg.av_hwframe_transfer_data(transfer, frame, 0) >= 0 ? transfer : null;
            if (source != null)
            {
                Convert(source, output);
                decoded = true;
            }
            ffmpeg.av_frame_unref(transfer);
            ffmpeg.av_frame_unref(frame);
        }
        return decoded;
    }

    private void Convert(AVFrame* source, Mat output)
    {
        int width = source->width, height = source->height;
        scaler = ffmpeg.sws_getCachedContext(scaler, width, height, (AVPixelFormat)source->format,
            width, height, AVPixelFormat.AV_PIX_FMT_BGRA, ffmpeg.SWS_BILINEAR, null, null, null);
        if (output.Width != width || output.Height != height || output.Type() != MatType.CV_8UC4)
            output.Create(height, width, MatType.CV_8UC4);

        var dst = new byte*[] { (byte*)output.Data, null, null, null };
        var dstStride = new[] { (int)output.Step(), 0, 0, 0 };
        ffmpeg.sws_scale(scaler, source->data, source->linesize, 0, height, dst, dstStride);
    }

    public void Dispose()
    {
        AVFrame* f = frame;
        ffmpeg.av_frame_free(&f);
        f = transfer;
        ffmpeg.av_frame_free(&f);
        AVPacket* p = packet;
        ffmpeg.av_packet_free(&p);
        AVCodecContext* c = context;
        ffmpeg.avcodec_free_context(&c);
        ffmpeg.sws_freeContext(scaler);
        frame = null;
        transfer = null;
        packet = null;
        context = null;
        scaler = null;
    }
}
