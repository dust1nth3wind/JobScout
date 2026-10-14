// Real Windows executable used by test_windows_launcher.py in place of uv.
// It never imports JobScout or accesses private configuration/data.
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

public static class FakeUv
{
    private static readonly Encoding Utf8 = new UTF8Encoding(false);

    private static string Json(string value)
    {
        StringBuilder result = new StringBuilder("\"");
        foreach (char character in value)
        {
            switch (character)
            {
                case '\\': result.Append("\\\\"); break;
                case '"': result.Append("\\\""); break;
                case '\n': result.Append("\\n"); break;
                case '\r': result.Append("\\r"); break;
                case '\t': result.Append("\\t"); break;
                default:
                    if (character < 32) result.Append("\\u" + ((int)character).ToString("x4"));
                    else result.Append(character);
                    break;
            }
        }
        return result.Append('"').ToString();
    }

    private static void Log(string kind, string[] args)
    {
        StringBuilder line = new StringBuilder("{\"kind\":" + Json(kind));
        line.Append(",\"pid\":" + Process.GetCurrentProcess().Id);
        line.Append(",\"cwd\":" + Json(Environment.CurrentDirectory));
        line.Append(",\"time\":" + Json(DateTime.UtcNow.ToString("o")));
        line.Append(",\"args\":[");
        for (int index = 0; index < args.Length; index++)
        {
            if (index > 0) line.Append(',');
            line.Append(Json(args[index]));
        }
        line.Append("]}\n");
        byte[] bytes = Utf8.GetBytes(line.ToString());
        string path = Environment.GetEnvironmentVariable("JOBSCOUT_TEST_LOG");
        // Parent, server and descendant can write concurrently. Share only reads
        // and retry so each log entry is one complete UTF-8 line.
        for (int attempt = 0; attempt < 100; attempt++)
        {
            try
            {
                using (FileStream stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read))
                    stream.Write(bytes, 0, bytes.Length);
                return;
            }
            catch (IOException) { Thread.Sleep(10); }
        }
        throw new IOException("Cannot write integration-test log: " + path);
    }

    private static int Setting(string name, int defaultValue)
    {
        int value;
        return Int32.TryParse(Environment.GetEnvironmentVariable(name), out value) ? value : defaultValue;
    }

    private static void Delay(string stage)
    {
        Thread.Sleep(Setting("JOBSCOUT_TEST_" + stage.ToUpperInvariant() + "_DELAY_MS", 0));
    }

    private static string Argument(string[] args, string name, string defaultValue)
    {
        for (int index = 0; index < args.Length - 1; index++)
            if (args[index] == name) return args[index + 1];
        return defaultValue;
    }

    private static int Serve(string[] args)
    {
        if (Setting("JOBSCOUT_TEST_SERVER_FAIL", 0) != 0) return 3;
        ProcessStartInfo child = new ProcessStartInfo();
        child.FileName = Process.GetCurrentProcess().MainModule.FileName;
        child.Arguments = "--test-descendant";
        child.UseShellExecute = false;
        Process.Start(child);

        Delay("server");
        int port = Int32.Parse(Argument(args, "--port", "8000"));
        TcpListener listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        Log("ready", args);
        while (true)
        {
            using (TcpClient client = listener.AcceptTcpClient())
            {
                client.ReceiveTimeout = 2000;
                client.SendTimeout = 2000;
                using (NetworkStream stream = client.GetStream())
                {
                    // Read complete HTTP headers before returning a successful
                    // response; raw TCP port probes simply disconnect.
                    StringBuilder request = new StringBuilder();
                    try
                    {
                        while (request.Length < 8192 && !request.ToString().EndsWith("\r\n\r\n"))
                        {
                            int value = stream.ReadByte();
                            if (value < 0) break;
                            request.Append((char)value);
                        }
                        if (request.Length == 0) continue;
                        byte[] body = Utf8.GetBytes("<html><body>JobScout integration test</body></html>");
                        byte[] headers = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + body.Length + "\r\nConnection: close\r\n\r\n");
                        stream.Write(headers, 0, headers.Length);
                        stream.Write(body, 0, body.Length);
                    }
                    catch (IOException) { }
                }
            }
        }
    }

    public static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--test-descendant")
        {
            Log("descendant", args);
            Thread.Sleep(Timeout.Infinite);
            return 0;
        }
        Log("invocation", args);
        if (args.Length == 1 && args[0] == "sync")
        {
            Delay("sync");
            return Setting("JOBSCOUT_TEST_SYNC_EXIT", 0);
        }
        if (args.Length >= 4 && args[0] == "run" && args[1] == "jobscout" && args[2] == "config" && args[3] == "check")
        {
            Delay("config");
            return Setting("JOBSCOUT_TEST_CONFIG_EXIT", 0);
        }
        if (args.Length >= 3 && args[0] == "run" && args[1] == "jobscout" && args[2] == "scan")
        {
            Delay("scan");
            return Setting("JOBSCOUT_TEST_SCAN_EXIT", 0);
        }
        if (args.Length >= 3 && args[0] == "run" && args[1] == "jobscout" && args[2] == "serve")
            return Serve(args);
        Console.Error.WriteLine("Unexpected fake uv arguments: " + String.Join(" ", args));
        return 90;
    }
}
