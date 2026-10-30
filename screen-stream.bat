<# :
@echo off
title Screen Stream
powershell -NoProfile -ExecutionPolicy Bypass -Command "$BatPath = '%~f0'; iex ([IO.File]::ReadAllText($BatPath))"
exit /b
#>
$Port = 8080
$Fps = 2
$Width = 1280
$Quality = 60

Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type -Name Native -Namespace W -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); [DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int h); [DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out int m); [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, int m); [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern IntPtr GetSystemMenu(IntPtr w, bool r); [DllImport("user32.dll")] public static extern bool DeleteMenu(IntPtr m, uint p, uint f); [DllImport("user32.dll")] public static extern bool DrawMenuBar(IntPtr w);'
[W.Native]::SetProcessDPIAware() | Out-Null
$hwnd = [W.Native]::GetConsoleWindow()
[W.Native]::DeleteMenu([W.Native]::GetSystemMenu($hwnd, $false), 0xF060, 0) | Out-Null
[W.Native]::DrawMenuBar($hwnd) | Out-Null
$con = [W.Native]::GetStdHandle(-11)
$mode = 0
[W.Native]::GetConsoleMode($con, [ref]$mode) | Out-Null
[W.Native]::SetConsoleMode($con, $mode -bor 4) | Out-Null
$conIn = [W.Native]::GetStdHandle(-10)
$inMode = 0
[W.Native]::GetConsoleMode($conIn, [ref]$inMode) | Out-Null
[W.Native]::SetConsoleMode($conIn, ($inMode -bor 0x80) -band -bnot 0x40) | Out-Null
$esc = [char]27
$orange = "$esc[38;2;255;102;0m"
$white = "$esc[38;2;255;255;255m"
$green = "$esc[38;2;0;255;0m"
$reset = "$esc[0m"

$ConfigPath = [IO.Path]::ChangeExtension($BatPath, '.ini')
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    @(
        '; Screen Stream settings. Restart Screen Stream after changing them.'
        '; Port: 1-65535, Fps: 1-30, Width: 160-7680, Quality: 1-100'
        "Port=$Port"
        "Fps=$Fps"
        "Width=$Width"
        "Quality=$Quality"
    ) | Set-Content -LiteralPath $ConfigPath -Encoding ASCII
}
$Limits = @{ Port = 1, 65535; Fps = 1, 30; Width = 160, 7680; Quality = 1, 100 }
foreach ($l in Get-Content -LiteralPath $ConfigPath) {
    if ($l -match '^\s*(\w+)\s*=\s*(.*?)\s*$' -and $Limits.ContainsKey($matches[1])) {
        $name = $matches[1]
        $value = 0
        if ([int]::TryParse($matches[2], [ref]$value) -and $value -ge $Limits[$name][0] -and $value -le $Limits[$name][1]) {
            Set-Variable -Name $name -Value $value
        } else {
            Write-Host "${orange}Invalid $name in $(Split-Path -Leaf $ConfigPath), using $(Get-Variable -Name $name -ValueOnly)$reset"
        }
    }
}

$codec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$encParams = New-Object Drawing.Imaging.EncoderParameters 1
$encParams.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality), ([long]$Quality)
$md5 = [Security.Cryptography.MD5]::Create()

$b = [Windows.Forms.Screen]::PrimaryScreen.Bounds
if ($Width -ge $b.Width) { $Width = $b.Width }
$Height = [int]($b.Height * $Width / $b.Width)
$src = New-Object Drawing.Bitmap $b.Width, $b.Height
$gs = [Drawing.Graphics]::FromImage($src)
$dst = New-Object Drawing.Bitmap $Width, $Height
$gd = [Drawing.Graphics]::FromImage($dst)
$gd.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::Bilinear
$gd.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$wmText = 'oT'
$wmFont = New-Object Drawing.Font 'Segoe UI', ([float][Math]::Max(10, $Height / 36)), ([Drawing.FontStyle]::Bold), ([Drawing.GraphicsUnit]::Pixel)
$wmSize = $gd.MeasureString($wmText, $wmFont)
$wmX = [float]($Width - $wmSize.Width - 10)
$wmY = [float]($Height - $wmSize.Height - 8)
$wmShadow = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(110, 0, 0, 0))
$wmBrush = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(140, 255, 255, 255))

$interval = [int](1000 / $Fps)
$frame = $null
$etag = ''
$frameTime = [DateTime]::MinValue
$empty = New-Object byte[] 0

$html = @'
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Screen Stream</title>
<style>
html,body{margin:0;height:100%;background:#000}
img{display:block;width:100%;height:100%;object-fit:contain}
</style>
</head>
<body>
<img id="i" alt="">
<script>
var img = document.getElementById('i'), tag = '', delay = INTERVAL;
async function tick() {
  if (document.hidden) { setTimeout(tick, 1000); return; }
  try {
    var r = await fetch('/frame', { cache: 'no-store', headers: tag ? { 'If-None-Match': tag } : {} });
    if (r.status === 200) {
      tag = r.headers.get('ETag') || '';
      var u = URL.createObjectURL(await r.blob());
      img.onload = function () { URL.revokeObjectURL(u); };
      img.src = u;
    }
  } catch (e) {}
  setTimeout(tick, delay);
}
tick();
</script>
</body>
</html>
'@
$htmlBytes = [Text.Encoding]::UTF8.GetBytes(($html -replace 'INTERVAL', $interval))

function Get-Frame {
    $gs.CopyFromScreen($b.Left, $b.Top, 0, 0, $b.Size)
    $gd.DrawImage($src, 0, 0, $Width, $Height)
    $gd.DrawString($wmText, $wmFont, $wmShadow, $wmX + 1, $wmY + 1)
    $gd.DrawString($wmText, $wmFont, $wmBrush, $wmX, $wmY)
    $ms = New-Object IO.MemoryStream
    $dst.Save($ms, $codec, $encParams)
    $ms.ToArray()
}

function Send-Response($stream, $status, $type, [byte[]]$body, $extra) {
    $head = "HTTP/1.1 $status`r`nContent-Type: $type`r`nContent-Length: $($body.Length)`r`nCache-Control: no-store`r`nConnection: close`r`n$extra`r`n"
    $hb = [Text.Encoding]::ASCII.GetBytes($head)
    $stream.Write($hb, 0, $hb.Length)
    if ($body.Length) { $stream.Write($body, 0, $body.Length) }
}

$listener = New-Object Net.Sockets.TcpListener ([Net.IPAddress]::Any), $Port
try {
    $listener.Start()
} catch {
    Write-Host "$esc[38;2;255;0;0mCannot start on port ${Port}: $($_.Exception.InnerException.Message)$reset"
    Write-Host "Another Screen Stream window may still be open. Close it, or change Port in $(Split-Path -Leaf $ConfigPath)."
    Read-Host "Press Enter to exit" | Out-Null
    exit 1
}

Write-Host "${orange}Screen Stream is running. Open in a browser on the other computer:$reset"
$urls = @([Net.Dns]::GetHostAddresses($env:COMPUTERNAME) | Where-Object { $_.AddressFamily -eq 'InterNetwork' } | ForEach-Object { "  http://$($_.IPAddressToString):$Port" })
$arrowY = [Console]::CursorTop
$arrowX = $urls[0].Length + 1
$urls | ForEach-Object { Write-Host "${white}$_$reset" }
Write-Host "Press Ctrl+C to stop."
$endY = [Console]::CursorTop
[Console]::CursorVisible = $false
[Console]::TreatControlCAsInput = $true
$step = 0
$lastServe = [DateTime]::MinValue

while ($true) {
    while (-not $listener.Pending()) {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq 'C' -and ($key.Modifiers -band [ConsoleModifiers]::Control)) {
                $listener.Stop()
                exit
            }
        }
        try {
            [Console]::SetCursorPosition($arrowX, $arrowY)
            $color = if (([DateTime]::UtcNow - $lastServe).TotalSeconds -lt 3) { $green } else { $orange }
            [Console]::Write($color + (' ' * $step) + '-->' + (' ' * (6 - $step)) + $reset)
            [Console]::SetCursorPosition(0, $endY)
        } catch {}
        $step = ($step + 1) % 7
        Start-Sleep -Milliseconds 300
    }
    $client = $listener.AcceptTcpClient()
    try {
        $client.ReceiveTimeout = 3000
        $client.SendTimeout = 5000
        $stream = $client.GetStream()
        $reader = New-Object IO.StreamReader $stream
        $line = $reader.ReadLine()
        $inm = ''
        while (($hl = $reader.ReadLine())) {
            if ($hl -match '^If-None-Match:\s*(.+)$') { $inm = $matches[1].Trim() }
        }
        $path = if ($line -match '^GET (\S+)') { $matches[1] } else { '' }

        if ($path -like '/frame*') {
            $lastServe = [DateTime]::UtcNow
            $now = [DateTime]::UtcNow
            if (($now - $frameTime).TotalMilliseconds -ge $interval) {
                try {
                    $frame = Get-Frame
                    $etag = '"' + [BitConverter]::ToString($md5.ComputeHash($frame)).Replace('-', '') + '"'
                } catch {}
                $frameTime = $now
            }
            if (-not $frame) {
                Send-Response $stream '503 Service Unavailable' 'text/plain' $empty ''
            } elseif ($inm -eq $etag) {
                Send-Response $stream '304 Not Modified' 'image/jpeg' $empty "ETag: $etag`r`n"
            } else {
                Send-Response $stream '200 OK' 'image/jpeg' $frame "ETag: $etag`r`n"
            }
        } elseif ($path -eq '/' -or $path -like '/[?]*') {
            Send-Response $stream '200 OK' 'text/html; charset=utf-8' $htmlBytes ''
        } else {
            Send-Response $stream '404 Not Found' 'text/plain' $empty ''
        }
    } catch {
    } finally {
        $client.Close()
    }
}
