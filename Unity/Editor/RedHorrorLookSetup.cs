using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;
using UnityEditor;
using UnityEditor.SceneManagement;

public static class RedHorrorLookSetup
{
    const string FolderPath = "Assets/Settings";
    const string ProfilePath = FolderPath + "/RedHorrorLook.asset";
    const string VolumeName = "Red Horror Volume";
    const string KeyLightName = "Red Key Light";

    [MenuItem("Tools/Red Horror Look/Create Volume Profile Only")]
    public static void CreateProfileOnly()
    {
        VolumeProfile profile = CreateProfile();
        Selection.activeObject = profile;
        EditorGUIUtility.PingObject(profile);
        Debug.Log("Red Horror Look: profile created at " + ProfilePath);
    }

    [MenuItem("Tools/Red Horror Look/Apply Full Look To Active Scene")]
    public static void ApplyFullLook()
    {
        VolumeProfile profile = CreateProfile();
        SetupVolume(profile);
        SetupLighting();
        SetupCamera();
        EnableHdr();
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Debug.Log("Red Horror Look: applied to active scene");
    }

    static VolumeProfile CreateProfile()
    {
        if (!AssetDatabase.IsValidFolder(FolderPath))
            AssetDatabase.CreateFolder("Assets", "Settings");

        VolumeProfile profile = AssetDatabase.LoadAssetAtPath<VolumeProfile>(ProfilePath);
        if (profile == null)
        {
            profile = ScriptableObject.CreateInstance<VolumeProfile>();
            AssetDatabase.CreateAsset(profile, ProfilePath);
        }
        else
        {
            foreach (VolumeComponent existing in profile.components)
                Object.DestroyImmediate(existing, true);
            profile.components.Clear();
        }

        ColorAdjustments color = AddComponent<ColorAdjustments>(profile);
        color.postExposure.Override(-0.5f);
        color.contrast.Override(45f);
        color.colorFilter.Override(Color.white);
        color.hueShift.Override(0f);
        color.saturation.Override(10f);

        ChannelMixer mixer = AddComponent<ChannelMixer>(profile);
        mixer.redOutRedIn.Override(70f);
        mixer.redOutGreenIn.Override(110f);
        mixer.redOutBlueIn.Override(20f);
        mixer.greenOutRedIn.Override(4f);
        mixer.greenOutGreenIn.Override(8f);
        mixer.greenOutBlueIn.Override(0f);
        mixer.blueOutRedIn.Override(3f);
        mixer.blueOutGreenIn.Override(3f);
        mixer.blueOutBlueIn.Override(3f);

        LiftGammaGain lgg = AddComponent<LiftGammaGain>(profile);
        lgg.lift.Override(new Vector4(1f, 0.8f, 0.8f, -0.18f));
        lgg.gamma.Override(new Vector4(1.1f, 0.85f, 0.85f, -0.05f));
        lgg.gain.Override(new Vector4(1.25f, 0.8f, 0.8f, 0.05f));

        Tonemapping tone = AddComponent<Tonemapping>(profile);
        tone.mode.Override(TonemappingMode.ACES);

        FilmGrain grain = AddComponent<FilmGrain>(profile);
        grain.type.Override(FilmGrainLookup.Large01);
        grain.intensity.Override(0.75f);
        grain.response.Override(0.6f);

        Vignette vignette = AddComponent<Vignette>(profile);
        vignette.color.Override(Color.black);
        vignette.center.Override(new Vector2(0.5f, 0.5f));
        vignette.intensity.Override(0.5f);
        vignette.smoothness.Override(0.7f);
        vignette.rounded.Override(false);

        Bloom bloom = AddComponent<Bloom>(profile);
        bloom.threshold.Override(0.6f);
        bloom.intensity.Override(0.8f);
        bloom.scatter.Override(0.75f);
        bloom.tint.Override(new Color(1f, 0.1f, 0.05f));
        bloom.highQualityFiltering.Override(true);

        profile.isDirty = true;
        EditorUtility.SetDirty(profile);
        AssetDatabase.SaveAssets();
        AssetDatabase.ImportAsset(ProfilePath);
        return profile;
    }

    static T AddComponent<T>(VolumeProfile profile) where T : VolumeComponent
    {
        T component = profile.Add<T>(true);
        component.name = typeof(T).Name;
        AssetDatabase.AddObjectToAsset(component, profile);
        return component;
    }

    static void SetupVolume(VolumeProfile profile)
    {
        GameObject go = GameObject.Find(VolumeName);
        if (go == null)
        {
            go = new GameObject(VolumeName);
            Undo.RegisterCreatedObjectUndo(go, "Create " + VolumeName);
        }

        Volume volume = go.GetComponent<Volume>();
        if (volume == null)
            volume = Undo.AddComponent<Volume>(go);

        Undo.RecordObject(volume, "Configure " + VolumeName);
        volume.isGlobal = true;
        volume.priority = 10f;
        volume.weight = 1f;
        volume.sharedProfile = profile;
    }

    static void SetupLighting()
    {
        foreach (Light light in Object.FindObjectsOfType<Light>())
        {
            if (light.type != LightType.Directional)
                continue;
            Undo.RecordObject(light, "Dim Directional Light");
            light.color = new Color(0.6f, 0.05f, 0.03f);
            light.intensity = 0.15f;
        }

        GameObject lightGo = GameObject.Find(KeyLightName);
        if (lightGo == null)
        {
            lightGo = new GameObject(KeyLightName);
            Undo.RegisterCreatedObjectUndo(lightGo, "Create " + KeyLightName);
        }

        Light key = lightGo.GetComponent<Light>();
        if (key == null)
            key = Undo.AddComponent<Light>(lightGo);

        Undo.RecordObject(key, "Configure " + KeyLightName);
        key.type = LightType.Point;
        key.color = new Color(1f, 0.1f, 0.05f);
        key.intensity = 12f;
        key.range = 20f;
        key.shadows = LightShadows.Soft;

        Camera cam = Camera.main;
        Vector3 position = cam != null
            ? cam.transform.position + cam.transform.forward * 3f + Vector3.up * 2f
            : new Vector3(0f, 3f, 0f);
        Undo.RecordObject(lightGo.transform, "Place " + KeyLightName);
        lightGo.transform.position = position;

        RenderSettings.ambientMode = AmbientMode.Flat;
        RenderSettings.ambientLight = new Color(0.06f, 0.005f, 0.005f);
        RenderSettings.fog = false;
    }

    static void SetupCamera()
    {
        Camera cam = Camera.main;
        if (cam == null)
        {
            Debug.LogWarning("Red Horror Look: no camera tagged MainCamera found, enable Post Processing on your camera manually");
            return;
        }

        UniversalAdditionalCameraData data = cam.GetUniversalAdditionalCameraData();
        Undo.RecordObject(data, "Enable Post Processing");
        data.renderPostProcessing = true;
    }

    static void EnableHdr()
    {
        UniversalRenderPipelineAsset urp = GraphicsSettings.currentRenderPipeline as UniversalRenderPipelineAsset;
        if (urp == null)
        {
            Debug.LogWarning("Red Horror Look: URP asset not found in Graphics Settings");
            return;
        }

        if (!urp.supportsHDR)
        {
            urp.supportsHDR = true;
            EditorUtility.SetDirty(urp);
            AssetDatabase.SaveAssets();
        }
    }
}
