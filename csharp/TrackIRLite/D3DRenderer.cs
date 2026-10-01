using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using OpenCvSharp;
using Vortice.D3DCompiler;
using Vortice.Direct3D;
using Vortice.Direct3D11;
using Vortice.Direct3D9;
using Vortice.DXGI;
using Vortice.Mathematics;

namespace TrackIRLite;

[StructLayout(LayoutKind.Sequential)]
public struct ViewParams
{
    public float Yaw;
    public float Pitch;
    public float Focal;
    public float Fisheye;
    public float OutW;
    public float OutH;
    public float SrcW;
    public float SrcH;
    public float Range;
    public float Mirror;
    public float Pad1;
    public float Pad2;
}

public sealed class D3DRenderer : IDisposable
{
    private readonly ID3D11Device device;
    private readonly ID3D11DeviceContext context;
    private readonly ID3D11VertexShader vertexShader;
    private readonly ID3D11PixelShader pixelShader;
    private readonly ID3D11Buffer constants;
    private readonly ID3D11SamplerState wrapSampler;
    private readonly ID3D11SamplerState clampSampler;
    private readonly IDirect3D9Ex d3d9;
    private readonly IDirect3DDevice9Ex device9;
    private readonly RenderTarget mainTarget;
    private readonly RenderTarget mirrorTarget;

    private ID3D11Texture2D? sourceTexture;
    private ID3D11ShaderResourceView? sourceView;

    public D3DImage Image => mainTarget.Image;
    public D3DImage MirrorImage => mirrorTarget.Image;
    public bool HasSource => sourceTexture != null;
    public int SourceWidth { get; private set; }
    public int SourceHeight { get; private set; }

    public D3DRenderer(IntPtr hwnd)
    {
        D3D11.D3D11CreateDevice(IntPtr.Zero, Vortice.Direct3D.DriverType.Hardware, DeviceCreationFlags.BgraSupport,
            new[] { Vortice.Direct3D.FeatureLevel.Level_11_0, Vortice.Direct3D.FeatureLevel.Level_10_1, Vortice.Direct3D.FeatureLevel.Level_10_0 },
            out device, out context).CheckError();

        string source = LoadShaderSource();
        vertexShader = device.CreateVertexShader(Compiler.Compile(source, "VSMain", "Projection.hlsl", "vs_4_0", ShaderFlags.OptimizationLevel3, EffectFlags.None).Span);
        pixelShader = device.CreatePixelShader(Compiler.Compile(source, "PSMain", "Projection.hlsl", "ps_4_0", ShaderFlags.OptimizationLevel3, EffectFlags.None).Span);

        constants = device.CreateBuffer((uint)Marshal.SizeOf<ViewParams>(), BindFlags.ConstantBuffer, ResourceUsage.Default, CpuAccessFlags.None, ResourceOptionFlags.None, 0);
        wrapSampler = CreateSampler(TextureAddressMode.Wrap);
        clampSampler = CreateSampler(TextureAddressMode.Clamp);

        d3d9 = D3D9.Direct3DCreate9Ex();
        var present = new Vortice.Direct3D9.PresentParameters
        {
            Windowed = true,
            SwapEffect = Vortice.Direct3D9.SwapEffect.Discard,
            DeviceWindowHandle = hwnd,
            PresentationInterval = PresentInterval.Immediate,
            BackBufferWidth = 1,
            BackBufferHeight = 1,
            BackBufferFormat = Vortice.Direct3D9.Format.Unknown
        };
        device9 = d3d9.CreateDeviceEx(0, Vortice.Direct3D9.DeviceType.Hardware, hwnd,
            CreateFlags.HardwareVertexProcessing | CreateFlags.Multithreaded | CreateFlags.FpuPreserve, present);

        mainTarget = new RenderTarget(device, device9);
        mirrorTarget = new RenderTarget(device, device9);
    }

    private static string LoadShaderSource()
    {
        using Stream stream = typeof(D3DRenderer).Assembly.GetManifestResourceStream("TrackIRLite.Projection.hlsl")!;
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    private ID3D11SamplerState CreateSampler(TextureAddressMode addressU)
    {
        return device.CreateSamplerState(new SamplerDescription
        {
            Filter = Filter.MinMagMipLinear,
            AddressU = addressU,
            AddressV = TextureAddressMode.Clamp,
            AddressW = TextureAddressMode.Clamp,
            ComparisonFunc = ComparisonFunction.Never,
            MinLOD = 0,
            MaxLOD = float.MaxValue
        });
    }

    public void UploadFrame(Mat frame)
    {
        if (sourceTexture == null || SourceWidth != frame.Width || SourceHeight != frame.Height)
        {
            sourceView?.Dispose();
            sourceTexture?.Dispose();
            SourceWidth = frame.Width;
            SourceHeight = frame.Height;
            sourceTexture = device.CreateTexture2D(new Texture2DDescription
            {
                Width = (uint)SourceWidth,
                Height = (uint)SourceHeight,
                MipLevels = 1,
                ArraySize = 1,
                Format = Vortice.DXGI.Format.B8G8R8A8_UNorm,
                SampleDescription = new SampleDescription(1, 0),
                Usage = ResourceUsage.Default,
                BindFlags = BindFlags.ShaderResource
            });
            sourceView = device.CreateShaderResourceView(sourceTexture);
        }
        context.UpdateSubresource(sourceTexture, 0, null, frame.Data, (uint)frame.Step(), 0);
    }

    public void Render(int width, int height, ViewParams view) => Render(mainTarget, width, height, view);

    public void RenderMirror(int width, int height, ViewParams view) => Render(mirrorTarget, width, height, view);

    private void Render(RenderTarget target, int width, int height, ViewParams view)
    {
        if (sourceView == null || width < 1 || height < 1 || !target.Image.IsFrontBufferAvailable) return;
        target.Ensure(width, height);

        view.OutW = width;
        view.OutH = height;
        view.SrcW = SourceWidth;
        view.SrcH = SourceHeight;

        target.Image.Lock();
        context.UpdateSubresource(in view, constants);
        context.OMSetRenderTargets(target.View!);
        context.RSSetViewport(0, 0, width, height, 0, 1);
        context.IASetPrimitiveTopology(PrimitiveTopology.TriangleList);
        context.VSSetShader(vertexShader);
        context.PSSetShader(pixelShader);
        context.PSSetConstantBuffer(0, constants);
        context.PSSetShaderResource(0, sourceView);
        context.PSSetSampler(0, view.Fisheye < 0.5f && view.Range > 6.28f ? wrapSampler : clampSampler);
        context.Draw(3, 0);
        context.Flush();
        target.Image.AddDirtyRect(new Int32Rect(0, 0, width, height));
        target.Image.Unlock();
    }

    public void Dispose()
    {
        mainTarget.Dispose();
        mirrorTarget.Dispose();
        sourceView?.Dispose();
        sourceTexture?.Dispose();
        clampSampler.Dispose();
        wrapSampler.Dispose();
        constants.Dispose();
        pixelShader.Dispose();
        vertexShader.Dispose();
        context.Dispose();
        device.Dispose();
        device9.Dispose();
        d3d9.Dispose();
    }

    private sealed class RenderTarget : IDisposable
    {
        private readonly ID3D11Device device;
        private readonly IDirect3DDevice9Ex device9;
        private ID3D11Texture2D? texture;
        private IDirect3DTexture9? sharedTexture9;
        private IDirect3DSurface9? surface9;
        private int width;
        private int height;

        public D3DImage Image { get; } = new();
        public ID3D11RenderTargetView? View { get; private set; }

        public RenderTarget(ID3D11Device device, IDirect3DDevice9Ex device9)
        {
            this.device = device;
            this.device9 = device9;
            Image.IsFrontBufferAvailableChanged += (_, _) =>
            {
                if (Image.IsFrontBufferAvailable) AttachBackBuffer();
            };
        }

        public void Ensure(int newWidth, int newHeight)
        {
            if (texture != null && newWidth == width && newHeight == height) return;

            Release();
            width = newWidth;
            height = newHeight;
            texture = device.CreateTexture2D(new Texture2DDescription
            {
                Width = (uint)width,
                Height = (uint)height,
                MipLevels = 1,
                ArraySize = 1,
                Format = Vortice.DXGI.Format.B8G8R8A8_UNorm,
                SampleDescription = new SampleDescription(1, 0),
                Usage = ResourceUsage.Default,
                BindFlags = BindFlags.RenderTarget | BindFlags.ShaderResource,
                MiscFlags = ResourceOptionFlags.Shared
            });
            View = device.CreateRenderTargetView(texture);

            IntPtr handle;
            using (var resource = texture.QueryInterface<IDXGIResource>())
                handle = resource.SharedHandle;
            sharedTexture9 = device9.CreateTexture((uint)width, (uint)height, 1, Vortice.Direct3D9.Usage.RenderTarget,
                Vortice.Direct3D9.Format.A8R8G8B8, Pool.Default, ref handle);
            surface9 = sharedTexture9.GetSurfaceLevel(0);
            AttachBackBuffer();
        }

        private void AttachBackBuffer()
        {
            if (surface9 == null) return;
            Image.Lock();
            Image.SetBackBuffer(D3DResourceType.IDirect3DSurface9, surface9.NativePointer, true);
            Image.Unlock();
        }

        private void Release()
        {
            if (surface9 != null)
            {
                Image.Lock();
                Image.SetBackBuffer(D3DResourceType.IDirect3DSurface9, IntPtr.Zero);
                Image.Unlock();
            }
            surface9?.Dispose();
            sharedTexture9?.Dispose();
            View?.Dispose();
            texture?.Dispose();
            surface9 = null;
            sharedTexture9 = null;
            View = null;
            texture = null;
        }

        public void Dispose() => Release();
    }
}
