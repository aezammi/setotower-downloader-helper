using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

internal static class Helper {
    internal const int Port=17843;
    static readonly string Origin="https://downloader.setotower.com";
    static readonly string Home=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"SetotowerDownloader");
    static readonly string Tools=Path.Combine(Home,"tools");
    static readonly string Destination=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),"Downloads","Setotower");
    static readonly string Token=Guid.NewGuid().ToString("N")+Guid.NewGuid().ToString("N");
    static readonly object Gate=new object();
    static readonly Dictionary<string,Job> Jobs=new Dictionary<string,Job>();
    static string toolState="preparing",toolError="";
    static int active=0;
    static TcpListener listener;
    static Mutex singleton;
    static JavaScriptSerializer Json(){return new JavaScriptSerializer{MaxJsonLength=32000000};}
    static void RegisterProtocol(bool enabled){using(var k=Registry.CurrentUser.CreateSubKey(@"Software\Classes\setotower-helper")){if(!enabled){Registry.CurrentUser.DeleteSubKeyTree(@"Software\Classes\setotower-helper",false);return;}k.SetValue(null,"URL:Setotower Helper");k.SetValue("URL Protocol","");using(var shell=k.CreateSubKey(@"shell\open\command"))shell.SetValue(null,"\""+Path.Combine(Home,"helper.exe")+"\" --start");}}
    static long lastActivity=DateTime.UtcNow.Ticks;
    static void Touch(){Interlocked.Exchange(ref lastActivity,DateTime.UtcNow.Ticks);}
    [STAThread] static void Main(string[] args){
        Application.EnableVisualStyles();
        string exe=Assembly.GetExecutingAssembly().Location;
        if(args.Contains("--uninstall")){RegisterProtocol(false);return;}
        if(!exe.Equals(Path.Combine(Home,"helper.exe"),StringComparison.OrdinalIgnoreCase)){
            Directory.CreateDirectory(Home);File.Copy(exe,Path.Combine(Home,"helper.exe"),true);RegisterProtocol(true);
            Process.Start(new ProcessStartInfo(Path.Combine(Home,"helper.exe"),"--start"){UseShellExecute=false,CreateNoWindow=true});return;
        }
        bool created;singleton=new Mutex(true,"Local\\SetotowerDownloaderHelper",out created);if(!created)return;
        try{listener=new TcpListener(IPAddress.Loopback,Port);listener.Start(8);}catch{return;}
        Task.Run(()=>Serve());Task.Run(()=>{EnsureTools();GC.Collect();});Task.Run(()=>IdleExit());
        Application.ApplicationExit+=(s,e)=>{listener.Stop();singleton.ReleaseMutex();};
        Application.Run();
    }
    static void IdleExit(){while(true){Thread.Sleep(30000);if(Volatile.Read(ref active)==0&&(DateTime.UtcNow.Ticks-Interlocked.Read(ref lastActivity))/TimeSpan.TicksPerSecond>300){try{listener.Stop();}catch{}Application.Exit();return;}}}
    static readonly object ToolGate=new object();
    static void EnsureTools(){lock(ToolGate){
        try{
            if(new[]{"yt-dlp.exe","ffmpeg.exe","node.exe","cineb-hls.mjs"}.All(n=>File.Exists(Path.Combine(Tools,n)))){toolState="ready";return;}
            toolState="preparing";toolError="";Directory.CreateDirectory(Home);
            string pack=Path.Combine(Home,"tools.download.zip");ServicePointManager.SecurityProtocol=SecurityProtocolType.Tls12;
            using(var web=new WebClient()){web.Headers["User-Agent"]="SetotowerHelper/1.0";web.DownloadFile(Origin+"/helper/tools-v1.zip",pack);}
            string hash;using(var sha=SHA256.Create())using(var f=File.OpenRead(pack))hash=BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();
            if(hash!=BuildConfig.ToolsSha256)throw new Exception("Tool package verification failed. Please reinstall the helper.");
            Directory.CreateDirectory(Tools);
            using(var archive=ZipFile.OpenRead(pack)){foreach(var entry in archive.Entries){if(entry.FullName.Contains("/")||entry.FullName.Contains("\\"))continue;entry.ExtractToFile(Path.Combine(Tools,entry.Name),true);}}
            File.Delete(pack);toolState="ready";
        }catch(Exception e){toolState="error";toolError=e.Message;}
    }}
    static async Task Serve(){while(true){TcpClient c;try{c=await listener.AcceptTcpClientAsync();}catch{return;}Task.Run(()=>Handle(c));}}
    static void Reply(NetworkStream stream,int status,object payload,bool cors){
        byte[] body=Encoding.UTF8.GetBytes(Json().Serialize(payload));
        string h="HTTP/1.1 "+status+" Response\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: "+body.Length+"\r\nCache-Control: no-store\r\nConnection: close\r\n";
        if(cors)h+="Access-Control-Allow-Origin: "+Origin+"\r\nVary: Origin\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type, X-Setotower-Token\r\nAccess-Control-Allow-Private-Network: true\r\n";
        byte[] headers=Encoding.ASCII.GetBytes(h+"\r\n");stream.Write(headers,0,headers.Length);stream.Write(body,0,body.Length);
    }
    static void Handle(TcpClient client){Touch();using(client){client.ReceiveTimeout=10000;client.SendTimeout=10000;using(var stream=client.GetStream()){
        bool cors=false;
        try{
            var bytes=new List<byte>();int b;while(bytes.Count<16384&&(b=stream.ReadByte())!=-1){bytes.Add((byte)b);int n=bytes.Count;if(n>=4&&bytes[n-4]==13&&bytes[n-3]==10&&bytes[n-2]==13&&bytes[n-1]==10)break;}
            var lines=Encoding.ASCII.GetString(bytes.ToArray()).Split(new[]{"\r\n"},StringSplitOptions.None);var request=lines[0].Split(' ');
            if(request.Length!=3){Reply(stream,400,new{error="Invalid request"},false);return;}
            var headers=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);foreach(var l in lines.Skip(1)){int i=l.IndexOf(':');if(i>0)headers[l.Substring(0,i)]=l.Substring(i+1).Trim();}
            string host,origin;headers.TryGetValue("Host",out host);headers.TryGetValue("Origin",out origin);cors=origin==Origin;
            if(host!="127.0.0.1:"+Port||!cors){Reply(stream,403,new{error="Only the Setotower website may use this helper"},false);return;}
            if(request[0]=="OPTIONS"){Reply(stream,200,new{ok=true},true);return;}
            string route=request[1];
            if(route=="/v1/health"&&request[0]=="GET"){Reply(stream,200,new{app="setotower-helper",version="1.0.0",state=toolState,error=toolError,token=Token},true);return;}
            string supplied;if(!headers.TryGetValue("X-Setotower-Token",out supplied)||supplied!=Token){Reply(stream,403,new{error="Reconnect the helper from the website"},true);return;}
            if(route.StartsWith("/v1/jobs/")&&request[0]=="GET"){
                lock(Gate){Job j;if(!Jobs.TryGetValue(route.Substring(9),out j)){Reply(stream,404,new{error="Download not found"},true);return;}Reply(stream,200,new{id=j.Id,state=j.State,progress=j.Progress,file=j.File,error=j.Error},true);}return;
            }
            if(toolState!="ready"){Reply(stream,503,new{error=toolState=="error"?toolError:"Preparing download tools. Please wait."},true);return;}
            if(request[0]!="POST"||(route!="/v1/info"&&route!="/v1/download")){Reply(stream,404,new{error="Unknown endpoint"},true);return;}
            string cl;int length;if(!headers.TryGetValue("Content-Length",out cl)||!int.TryParse(cl,out length)||length<1||length>8192){Reply(stream,400,new{error="Invalid request size"},true);return;}
            byte[] content=new byte[length];int offset=0;while(offset<length){int read=stream.Read(content,offset,length-offset);if(read==0)throw new IOException("Incomplete request");offset+=read;}
            var input=Json().Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(content));string url=Convert.ToString(input["url"]);
            ValidateUrl(url);
            if(Interlocked.CompareExchange(ref active,1,0)!=0){Reply(stream,429,new{error="Another download or lookup is running. Please wait."},true);return;}
            if(route=="/v1/info"){
                try{Reply(stream,200,Info(url),true);}finally{Interlocked.Exchange(ref active,0);GC.Collect();}return;
            }
            string type=input.ContainsKey("type")?Convert.ToString(input["type"]):"mp4";
            string quality=input.ContainsKey("quality")?Convert.ToString(input["quality"]):"best";
            if((type!="mp4"&&type!="mp3")||!Regex.IsMatch(quality,@"^(best|\d{2,4})$")){Interlocked.Exchange(ref active,0);throw new Exception("Invalid format or quality");}
            var job=new Job{Id=Guid.NewGuid().ToString("N"),State="downloading",Progress="Starting download…"};
            lock(Gate){if(Jobs.Count>20)Jobs.Clear();Jobs[job.Id]=job;}
            Task.Run(()=>Download(job,url,type,quality));Reply(stream,202,new{id=job.Id},true);
        }catch(Exception e){try{Reply(stream,400,new{error=Friendly(e.Message)},cors);}catch{}}
    }}}
    static bool IsHls(Uri u){return (u.AbsolutePath.EndsWith(".m3u8",StringComparison.OrdinalIgnoreCase)||u.Query.IndexOf(".m3u8",StringComparison.OrdinalIgnoreCase)>=0)&&String.IsNullOrEmpty(u.UserInfo);}
    static void ValidateUrl(string url){Uri u;if(!Uri.TryCreate(url,UriKind.Absolute,out u)||(u.Scheme!="https"&&u.Scheme!="http")||!String.IsNullOrEmpty(u.UserInfo))throw new Exception("Paste a valid media link.");if(IsHls(u))return;if(!new[]{"youtube.com","www.youtube.com","m.youtube.com","music.youtube.com","youtu.be","www.youtu.be"}.Contains(u.Host.ToLowerInvariant()))throw new Exception("This source is not supported yet. Use a supported video link or a prepared HLS source.");}
    static string Quote(string s){
        var result=new StringBuilder();result.Append('"');int slashes=0;
        foreach(char c in s){if(c=='\\'){slashes++;continue;}if(c=='"'){result.Append('\\',slashes*2+1).Append('"');}else{result.Append('\\',slashes).Append(c);}slashes=0;}
        result.Append('\\',slashes*2).Append('"');return result.ToString();
    }
    static ProcessStartInfo Command(List<string> args){return new ProcessStartInfo(Path.Combine(Tools,"yt-dlp.exe"),String.Join(" ",args.Select(Quote))){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8,WorkingDirectory=Home};}
    static ProcessStartInfo HlsCommand(List<string> args){return new ProcessStartInfo(Path.Combine(Tools,"node.exe"),String.Join(" ",args.Select(Quote))){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8,WorkingDirectory=Home};}
    static List<string> BaseArgs(){return new List<string>{"--ignore-config","--no-playlist","--no-cache-dir","--js-runtimes","node:"+Path.Combine(Tools,"node.exe"),"--ffmpeg-location",Tools,"--encoding","utf-8","--socket-timeout","25","--retries","2"};}
    static object Info(string url){Uri u;Uri.TryCreate(url,UriKind.Absolute,out u);if(IsHls(u)){var title=Path.GetFileNameWithoutExtension(u.AbsolutePath);return new{title=String.IsNullOrEmpty(title)?"Cineb video":title,thumbnail=(object)null,uploader="Cineb",duration=(object)null,engine="cineb-hls",video=new[]{new{value="best",label="Highest available MP4"}},audio=new[]{new{value="best",label="Audio embedded in video"}}};}var args=BaseArgs();args.AddRange(new[]{"--dump-single-json","--skip-download","--",url});
        using(var p=Process.Start(Command(args))){var output=p.StandardOutput.ReadToEndAsync();var err=p.StandardError.ReadToEndAsync();if(!p.WaitForExit(120000)){p.Kill();throw new Exception("YouTube took too long to respond. Retry shortly.");}if(p.ExitCode!=0)throw new Exception(err.Result);
            var data=Json().Deserialize<Dictionary<string,object>>(output.Result);var formats=(System.Collections.IEnumerable)data["formats"];var heights=new SortedSet<int>();
            foreach(Dictionary<string,object> f in formats){if(f.ContainsKey("height")&&f["height"]!=null&&f.ContainsKey("ext")&&new[]{"mp4","webm"}.Contains(Convert.ToString(f["ext"]))&&f.ContainsKey("vcodec")&&Convert.ToString(f["vcodec"])!="none")heights.Add(Convert.ToInt32(f["height"]));}
            return new{title=data["title"],thumbnail=data.ContainsKey("thumbnail")?data["thumbnail"]:null,uploader=data.ContainsKey("uploader")?data["uploader"]:null,duration=data.ContainsKey("duration_string")?data["duration_string"]:null,video=heights.Reverse().Select(h=>new{value=h.ToString(),label=h+"p MP4"}).ToArray(),audio=new[]{64,128,192,256,320}.Select(k=>new{value=k.ToString(),label=k+" kbps MP3"}).ToArray()};
        }
    }
    static void Download(Job job,string url,string type,string quality){try{
        Directory.CreateDirectory(Destination);Uri uri;Uri.TryCreate(url,UriKind.Absolute,out uri);
        if(IsHls(uri)){if(type!="mp4")throw new Exception("Prepared HLS sources currently support MP4 video only.");var output=Path.Combine(Destination,"setotower-download.mp4");var hlsArgs=new List<string>{Path.Combine(Tools,"cineb-hls.mjs"),"--url",url,"--output",output,"--ffmpeg",Path.Combine(Tools,"ffmpeg.exe"),"--connections","8"};using(var p=new Process{StartInfo=HlsCommand(hlsArgs)}){var errors=new StringBuilder();p.OutputDataReceived+=(s,e)=>{if(e.Data==null)return;lock(Gate){if(e.Data.StartsWith("SETOTOWER_FILE:"))job.File=e.Data.Substring(15);else if(e.Data.StartsWith("SETOTOWER_PROGRESS:"))job.Progress=e.Data.Substring(19);}};p.ErrorDataReceived+=(s,e)=>{if(e.Data==null)return;lock(errors){if(errors.Length>6000)errors.Clear();errors.AppendLine(e.Data);}};p.Start();p.BeginOutputReadLine();p.BeginErrorReadLine();p.WaitForExit();if(p.ExitCode!=0)throw new Exception(errors.ToString());if(String.IsNullOrEmpty(job.File)||!File.Exists(job.File)||new FileInfo(job.File).Length==0)throw new Exception("The HLS video could not be saved. Please retry.");lock(Gate){job.State="complete";job.Progress="Saved to Downloads\\Setotower";}return;}
        }
        var args=BaseArgs();
        args.AddRange(new[]{"--newline","--progress","--no-simulate","--print","after_move:SETOTOWER_FILE:%(filepath)s","-o",Path.Combine(Destination,"%(title).150B [%(id)s].%(ext)s")});
        if(type=="mp3")args.AddRange(new[]{"-f","bestaudio/best","-x","--audio-format","mp3","--audio-quality",quality=="best"?"0":quality+"K"});
        else{string height=quality=="best"?"":"[height<="+quality+"]";args.AddRange(new[]{"-f","bv*"+height+"[vcodec^=avc1]+ba[ext=m4a]/bv*"+height+"[ext=mp4]+ba/b"+height+"[ext=mp4]/b"+height,"--merge-output-format","mp4"});}
        args.AddRange(new[]{"--",url});
        using(var p=new Process{StartInfo=Command(args)}){var errors=new StringBuilder();
            p.OutputDataReceived+=(s,e)=>{if(e.Data==null)return;lock(Gate){if(e.Data.StartsWith("SETOTOWER_FILE:"))job.File=e.Data.Substring(15);else if(e.Data.Contains("[download]")||e.Data.Contains("[Merger]")||e.Data.Contains("[ExtractAudio]"))job.Progress=e.Data;}};
            p.ErrorDataReceived+=(s,e)=>{if(e.Data==null)return;lock(errors){if(errors.Length>6000)errors.Clear();errors.AppendLine(e.Data);}};
            p.Start();p.BeginOutputReadLine();p.BeginErrorReadLine();p.WaitForExit();
            if(p.ExitCode!=0)throw new Exception(errors.ToString());if(String.IsNullOrEmpty(job.File)||!System.IO.File.Exists(job.File)||new FileInfo(job.File).Length==0)throw new Exception("The video could not be saved. Please retry.");
            lock(Gate){job.State="complete";job.Progress="Saved to Downloads\\Setotower";}
        }
    }catch(Exception e){lock(Gate){job.State="error";job.Error=Friendly(e.Message);}}finally{Interlocked.Exchange(ref active,0);}}
    static string Friendly(string message){if(message.Contains("not a bot"))return "YouTube requested verification on this connection. Open this video on YouTube and try again later.";if(message.Contains("private video"))return "This video is private.";if(message.Length>500)return "YouTube could not complete the request. Try another quality or retry later.";return message;}
    class Job {public string Id,State,Progress,File="",Error="";}
}
