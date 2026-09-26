cbuffer View : register(b0)
{
    float Yaw;
    float Pitch;
    float Focal;
    float Fisheye;
    float OutW;
    float OutH;
    float SrcW;
    float SrcH;
    float Range;
    float3 Padding;
};

Texture2D Source : register(t0);
SamplerState Sampler0 : register(s0);

static const float PI = 3.14159265;

float4 VSMain(uint id : SV_VertexID) : SV_Position
{
    float2 uv = float2((id << 1) & 2, id & 2);
    return float4(uv * float2(2, -2) + float2(-1, 1), 0, 1);
}

float4 PSMain(float4 pos : SV_Position) : SV_Target
{
    float3 v = normalize(float3(pos.x - 0.5 - OutW * 0.5, pos.y - 0.5 - OutH * 0.5, Focal));

    float cy = cos(Yaw), sy = sin(Yaw);
    float cp = cos(Pitch), sp = sin(Pitch);
    float3 u = float3(v.x, cp * v.y - sp * v.z, sp * v.y + cp * v.z);
    float3 r = float3(cy * u.x - sy * u.z, u.y, sy * u.x + cy * u.z);

    float2 m;
    if (Fisheye > 0.5)
    {
        float r3 = sqrt(r.x * r.x + r.y * r.y);
        float r2 = (SrcH / 3.14159) * atan2(r3, r.z);
        m = r3 > 0 ? float2(r2 * r.x / r3 + SrcW * 0.5, r2 * r.y / r3 + SrcH * 0.5)
                   : float2(SrcW * 0.5, SrcH * 0.5);
    }
    else
    {
        m = float2((atan2(r.x, r.z) / Range + 0.5) * SrcW,
                   (asin(clamp(r.y, -1, 1)) / PI + 0.5) * SrcH);
    }

    float2 uv = (m + 0.5) / float2(SrcW, SrcH);
    bool wraps = Fisheye < 0.5 && Range > 6.28;
    if (!wraps && (any(uv < 0) || any(uv > 1)))
        return float4(0, 0, 0, 1);

    return float4(Source.SampleLevel(Sampler0, uv, 0).rgb, 1);
}
