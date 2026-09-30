namespace TrackIRLite;

public static class NetProtocol
{
    public const int DefaultPort = 5600;
    public const int HeaderSize = 5;
    public const int MaxPacketSize = 32 * 1024 * 1024;

    public const byte MainStream = 0;
    public const byte PipStream = 1;

    public const byte PipOff = 0;
    public const byte PipOn = 1;
}
