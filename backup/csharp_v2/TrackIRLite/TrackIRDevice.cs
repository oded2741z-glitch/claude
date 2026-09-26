using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace TrackIRLite;

public sealed class TrackIRDevice : IDisposable
{
    private const float MaxRotation = 180f;
    private const float MaxValue = 16383f;
    private const float MaxTranslation = 50f;
    private const ushort NPPitch = 2, NPYaw = 4, NPRoll = 1, NPZ = 64;

    [StructLayout(LayoutKind.Sequential)]
    private struct TrackIRData
    {
        public ushort Status;
        public ushort FrameSignature;
        public uint IOData;
        public float Roll, Pitch, Yaw, X, Y, Z;
        public float Reserved1, Reserved2, Reserved3, Reserved4, Reserved5, Reserved6, Reserved7, Reserved8, Reserved9;
    }

    private delegate int HandleFn(IntPtr hwnd);
    private delegate int UShortFn(ushort value);
    private delegate int VoidFn();
    private delegate int GetDataFn(ref TrackIRData data);

    private readonly IntPtr library;
    private readonly GetDataFn? getData;
    private readonly VoidFn? stopCursor;
    private readonly VoidFn? stopDataTransmission;
    private readonly VoidFn? unregisterWindowHandle;

    public bool Connected { get; }

    public TrackIRDevice(IntPtr hwnd)
    {
        try
        {
            const string dllName = "NPClient64.dll";
            string local = Path.Combine(AppContext.BaseDirectory, dllName);
            string path = File.Exists(local) ? local : Path.Combine(ReadRegistryPath(), dllName);
            library = NativeLibrary.Load(path);

            Get<HandleFn>("NP_RegisterWindowHandle")(hwnd);
            Get<UShortFn>("NP_RequestData")((ushort)(NPPitch | NPYaw | NPRoll | NPZ));
            Get<UShortFn>("NP_RegisterProgramProfileID")(1000);
            getData = Get<GetDataFn>("NP_GetData");
            stopCursor = Get<VoidFn>("NP_StopCursor");
            stopDataTransmission = Get<VoidFn>("NP_StopDataTransmission");
            unregisterWindowHandle = Get<VoidFn>("NP_UnregisterWindowHandle");

            if (Get<VoidFn>("NP_StartDataTransmission")() == 0)
            {
                Get<VoidFn>("NP_StartCursor")();
                Connected = true;
            }
        }
        catch { }
    }

    private static string ReadRegistryPath()
    {
        using RegistryKey? key = Registry.CurrentUser.OpenSubKey(@"Software\NaturalPoint\NATURALPOINT\NPClient Location");
        return key?.GetValue("Path") as string ?? throw new FileNotFoundException();
    }

    private T Get<T>(string name) where T : Delegate
    {
        return Marshal.GetDelegateForFunctionPointer<T>(NativeLibrary.GetExport(library, name));
    }

    public (double Yaw, double Pitch, double Z) GetData()
    {
        if (!Connected || getData == null) return (0, 0, 0);
        try
        {
            var data = new TrackIRData();
            if (getData(ref data) == 0)
                return (data.Yaw / MaxValue * MaxRotation, data.Pitch / MaxValue * MaxRotation, data.Z / MaxValue * MaxTranslation);
        }
        catch { }
        return (0, 0, 0);
    }

    public void Dispose()
    {
        if (!Connected) return;
        try
        {
            stopCursor?.Invoke();
            stopDataTransmission?.Invoke();
            unregisterWindowHandle?.Invoke();
        }
        catch { }
    }
}
