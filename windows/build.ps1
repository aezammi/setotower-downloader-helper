$ErrorActionPreference='Stop'
$project=Split-Path $PSScriptRoot -Parent
$pack=Join-Path $PSScriptRoot 'toolpack'
$release=Join-Path $PSScriptRoot 'release'
New-Item -ItemType Directory -Force $pack,$release | Out-Null
Copy-Item -LiteralPath (Get-Command yt-dlp.exe).Source -Destination (Join-Path $pack 'yt-dlp.exe') -Force
Copy-Item -LiteralPath (Get-Command ffmpeg.exe).Source -Destination (Join-Path $pack 'ffmpeg.exe') -Force
Copy-Item -LiteralPath (Get-Command node.exe).Source -Destination (Join-Path $pack 'node.exe') -Force
Copy-Item -LiteralPath (Join-Path $project 'engine/cineb-hls.mjs') -Destination (Join-Path $pack 'cineb-hls.mjs') -Force
$ffmpegRoot=Split-Path (Split-Path (Get-Command ffmpeg.exe).Source)
Copy-Item -LiteralPath (Join-Path $ffmpegRoot 'LICENSE.txt') -Destination (Join-Path $pack 'FFMPEG-LICENSE.txt') -Force
Invoke-WebRequest 'https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/LICENSE' -OutFile (Join-Path $pack 'YTDLP-LICENSE.txt')
Invoke-WebRequest 'https://raw.githubusercontent.com/nodejs/node/main/LICENSE' -OutFile (Join-Path $pack 'NODE-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $pack 'SOURCES.md') -Force
Compress-Archive -Path (Join-Path $pack '*') -DestinationPath (Join-Path $release 'tools-v1.zip') -CompressionLevel Optimal -Force
$hash=(Get-FileHash -Algorithm SHA256 (Join-Path $release 'tools-v1.zip')).Hash.ToLowerInvariant()
$config=Get-Content -Raw (Join-Path $PSScriptRoot 'BuildConfig.cs')
$config=[regex]::Replace($config,'ToolsSha256="[0-9a-f]+"','ToolsSha256="'+$hash+'"')
Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot 'BuildConfig.cs') $config
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /optimize+ ('/out:'+ (Join-Path $release 'helper.exe')) /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll (Join-Path $PSScriptRoot 'Helper.cs') (Join-Path $PSScriptRoot 'BuildConfig.cs')
if($LASTEXITCODE -ne 0){throw 'Helper compilation failed'}
Get-FileHash -Algorithm SHA256 (Join-Path $release 'helper.exe'),(Join-Path $release 'tools-v1.zip')
