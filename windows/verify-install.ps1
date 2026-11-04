# Run only after installing helper.exe. Does not install or change startup settings.
$ErrorActionPreference='Stop'
$base='http://127.0.0.1:17843/v1'
$headers=@{Origin='https://downloader.setotower.com'}
$health=Invoke-RestMethod "$base/health" -Headers $headers
$headers['X-Setotower-Token']=$health.token
Write-Output "Helper state: $($health.state)"
if($health.state -ne 'ready'){throw "Tools not ready: $($health.error)"}
foreach($request in @(@{origin='https://example.com';token=$health.token},@{origin='https://downloader.setotower.com';token='wrong'})){
    try{Invoke-RestMethod "$base/jobs/nonexistent" -Headers @{Origin=$request.origin;'X-Setotower-Token'=$request.token} | Out-Null;throw 'Unexpected authorization success'}
    catch{if([int]$_.Exception.Response.StatusCode -ne 403){throw};Write-Output 'Unauthorized request correctly rejected (403)'}
}
$metadata=Invoke-RestMethod "$base/info" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"url":"https://youtu.be/22ZfLQ9iVNo"}' -TimeoutSec 150
Write-Output "Video: $($metadata.title) [$($metadata.duration)]"
Get-Process helper | Select-Object Id,@{N='WorkingSetMB';E={[math]::Round($_.WorkingSet64/1MB,1)}},@{N='PrivateMB';E={[math]::Round($_.PrivateMemorySize64/1MB,1)}}
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name SetotowerDownloader | Select-Object SetotowerDownloader
