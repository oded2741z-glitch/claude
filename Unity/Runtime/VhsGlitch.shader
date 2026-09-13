Shader "Hidden/VhsGlitch"
{
    SubShader
    {
        Tags { "RenderPipeline" = "UniversalPipeline" }
        Cull Off ZWrite Off ZTest Always

        Pass
        {
            Name "VhsGlitch"

            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            TEXTURE2D_X(_MainTex);
            SAMPLER(sampler_MainTex);

            float4 _MainTex_TexelSize;

            float _Intensity;
            float _ScanlineAmount;
            float _ScanlineCount;
            float _ScanlineSpeed;
            float _NoiseAmount;
            float _RgbSplit;
            float _RollAmount;
            float _RollSpeed;
            float _Flicker;

            struct Attributes
            {
                float4 positionOS : POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            Varyings Vert(Attributes input)
            {
                Varyings output;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(output);
                output.positionCS = TransformObjectToHClip(input.positionOS.xyz);
                output.uv = input.uv;
                return output;
            }

            float Hash21(float2 p)
            {
                p = frac(p * float2(123.34, 456.21));
                p += dot(p, p + 45.32);
                return frac(p.x * p.y);
            }

            float4 Frag(Varyings input) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);

                float2 uv = input.uv;
                float time = _Time.y;

                float roll = sin(uv.y * 2.0 + time * _RollSpeed * 6.2831) * 0.5 + 0.5;
                roll = pow(roll, 8.0);
                float2 rolledUv = uv;
                rolledUv.x += roll * _RollAmount * 0.05 * _Intensity;

                float lineNoise = Hash21(float2(floor(rolledUv.y * _ScanlineCount), floor(time * 12.0)));
                rolledUv.x += (lineNoise - 0.5) * _NoiseAmount * 0.012 * _Intensity;
                rolledUv.x = frac(rolledUv.x);

                float split = _RgbSplit * _Intensity * _MainTex_TexelSize.x;
                float edge = abs(uv.x - 0.5) * 2.0;
                split *= 0.4 + edge;

                float4 color;
                color.r = SAMPLE_TEXTURE2D_X(_MainTex, sampler_MainTex, float2(rolledUv.x + split, rolledUv.y)).r;
                color.g = SAMPLE_TEXTURE2D_X(_MainTex, sampler_MainTex, rolledUv).g;
                color.b = SAMPLE_TEXTURE2D_X(_MainTex, sampler_MainTex, float2(rolledUv.x - split, rolledUv.y)).b;
                color.a = 1.0;

                float scan = sin(uv.y * _ScanlineCount * 3.14159 + time * _ScanlineSpeed * 10.0) * 0.5 + 0.5;
                scan = lerp(1.0, scan, _ScanlineAmount * _Intensity);
                color.rgb *= scan;

                float grain = Hash21(uv * _ScreenParams.xy + frac(time) * 137.0) - 0.5;
                color.rgb += grain * _NoiseAmount * 0.15 * _Intensity;

                float flick = 1.0 - _Flicker * _Intensity * (Hash21(float2(floor(time * 24.0), 7.3)) * 0.5);
                color.rgb *= flick;

                return float4(max(color.rgb, 0.0), 1.0);
            }
            ENDHLSL
        }
    }
    Fallback Off
}
