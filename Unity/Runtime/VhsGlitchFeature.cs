using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

public class VhsGlitchFeature : ScriptableRendererFeature
{
    [System.Serializable]
    public class Settings
    {
        public RenderPassEvent renderPassEvent = RenderPassEvent.AfterRenderingPostProcessing;

        [Range(0f, 1f)] public float intensity = 1f;

        [Header("Scanlines")]
        [Range(0f, 1f)] public float scanlineAmount = 0.35f;
        [Range(50f, 1200f)] public float scanlineCount = 480f;
        [Range(-5f, 5f)] public float scanlineSpeed = 0.6f;

        [Header("Noise")]
        [Range(0f, 1f)] public float noiseAmount = 0.25f;

        [Header("RGB Split")]
        [Range(0f, 10f)] public float rgbSplit = 1.5f;

        [Header("Vertical Roll")]
        [Range(0f, 1f)] public float rollAmount = 0.12f;
        [Range(0f, 5f)] public float rollSpeed = 0.15f;

        [Header("Flicker")]
        [Range(0f, 1f)] public float flicker = 0.12f;
    }

    public Settings settings = new Settings();

    class VhsGlitchPass : ScriptableRenderPass
    {
        static readonly int IntensityId = Shader.PropertyToID("_Intensity");
        static readonly int ScanlineAmountId = Shader.PropertyToID("_ScanlineAmount");
        static readonly int ScanlineCountId = Shader.PropertyToID("_ScanlineCount");
        static readonly int ScanlineSpeedId = Shader.PropertyToID("_ScanlineSpeed");
        static readonly int NoiseAmountId = Shader.PropertyToID("_NoiseAmount");
        static readonly int RgbSplitId = Shader.PropertyToID("_RgbSplit");
        static readonly int RollAmountId = Shader.PropertyToID("_RollAmount");
        static readonly int RollSpeedId = Shader.PropertyToID("_RollSpeed");
        static readonly int FlickerId = Shader.PropertyToID("_Flicker");
        static readonly int TempTexId = Shader.PropertyToID("_VhsGlitchTemp");

        readonly Material material;
        readonly Settings settings;
        readonly string profilerTag;
        RenderTargetIdentifier source;
        RenderTargetHandle tempTexture;

        public VhsGlitchPass(Material material, Settings settings, string profilerTag)
        {
            this.material = material;
            this.settings = settings;
            this.profilerTag = profilerTag;
            renderPassEvent = settings.renderPassEvent;
            tempTexture.Init("_VhsGlitchTemp");
        }

        public void SetSource(RenderTargetIdentifier source)
        {
            this.source = source;
        }

        public override void OnCameraSetup(CommandBuffer cmd, ref RenderingData renderingData)
        {
            RenderTextureDescriptor descriptor = renderingData.cameraData.cameraTargetDescriptor;
            descriptor.depthBufferBits = 0;
            descriptor.msaaSamples = 1;
            cmd.GetTemporaryRT(TempTexId, descriptor, FilterMode.Bilinear);
        }

        public override void Execute(ScriptableRenderContext context, ref RenderingData renderingData)
        {
            if (material == null)
                return;

            if (renderingData.cameraData.cameraType != CameraType.Game)
                return;

            CommandBuffer cmd = CommandBufferPool.Get(profilerTag);

            material.SetFloat(IntensityId, settings.intensity);
            material.SetFloat(ScanlineAmountId, settings.scanlineAmount);
            material.SetFloat(ScanlineCountId, settings.scanlineCount);
            material.SetFloat(ScanlineSpeedId, settings.scanlineSpeed);
            material.SetFloat(NoiseAmountId, settings.noiseAmount);
            material.SetFloat(RgbSplitId, settings.rgbSplit);
            material.SetFloat(RollAmountId, settings.rollAmount);
            material.SetFloat(RollSpeedId, settings.rollSpeed);
            material.SetFloat(FlickerId, settings.flicker);

            Blit(cmd, source, tempTexture.Identifier(), material, 0);
            Blit(cmd, tempTexture.Identifier(), source);

            context.ExecuteCommandBuffer(cmd);
            CommandBufferPool.Release(cmd);
        }

        public override void OnCameraCleanup(CommandBuffer cmd)
        {
            cmd.ReleaseTemporaryRT(TempTexId);
        }
    }

    Material material;
    VhsGlitchPass pass;

    public override void Create()
    {
        Shader shader = Shader.Find("Hidden/VhsGlitch");
        if (shader == null)
        {
            Debug.LogWarning("VhsGlitchFeature: shader Hidden/VhsGlitch not found");
            return;
        }

        material = CoreUtils.CreateEngineMaterial(shader);
        pass = new VhsGlitchPass(material, settings, name);
    }

    public override void AddRenderPasses(ScriptableRenderer renderer, ref RenderingData renderingData)
    {
        if (pass == null || settings.intensity <= 0f)
            return;

        pass.renderPassEvent = settings.renderPassEvent;
        renderer.EnqueuePass(pass);
    }

    public override void SetupRenderPasses(ScriptableRenderer renderer, in RenderingData renderingData)
    {
        if (pass == null || settings.intensity <= 0f)
            return;

        pass.SetSource(renderer.cameraColorTarget);
    }

    protected override void Dispose(bool disposing)
    {
        CoreUtils.Destroy(material);
    }
}
