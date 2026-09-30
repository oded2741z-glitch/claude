using System.Net;
using System.Net.Sockets;

namespace TrackIRServer;

public sealed class StreamServer : IDisposable
{
    private readonly TcpListener listener;
    private readonly List<ClientConnection> clients = new();

    public StreamServer(int port)
    {
        listener = new TcpListener(IPAddress.Any, port);
        listener.Start();
        new Thread(AcceptLoop) { IsBackground = true }.Start();
    }

    private void AcceptLoop()
    {
        while (true)
        {
            TcpClient tcp;
            try { tcp = listener.AcceptTcpClient(); }
            catch { return; }

            var client = new ClientConnection(tcp);
            lock (clients) clients.Add(client);
            Program.Log($"Client connected: {client.Name}");
        }
    }

    public int ViewerCount(byte stream)
    {
        lock (clients)
        {
            for (int i = clients.Count - 1; i >= 0; i--)
            {
                if (clients[i].Alive) continue;
                Program.Log($"Client disconnected: {clients[i].Name}");
                clients[i].Dispose();
                clients.RemoveAt(i);
            }
            return clients.Count(c => c.Receives(stream));
        }
    }

    public bool NeedsKeyframe(byte stream)
    {
        lock (clients) return clients.Any(c => c.NeedsKeyframe(stream));
    }

    public void Broadcast(byte stream, byte[] message, bool keyframe)
    {
        lock (clients)
        {
            foreach (ClientConnection client in clients) client.Send(stream, message, keyframe);
        }
    }

    public void Dispose()
    {
        listener.Stop();
        lock (clients)
        {
            foreach (ClientConnection client in clients) client.Dispose();
            clients.Clear();
        }
    }
}
