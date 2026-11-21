# Downloads ffmpeg.exe, ffprobe.exe and libmpv-2.dll into the project's bin\ folder.
# Files that are already there are kept.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
# Windows PowerShell 5.1 on older Windows 10 may not enable TLS 1.2 by default
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$bin = Join-Path (Split-Path -Parent $PSScriptRoot) "bin"
$tmp = Join-Path ([IO.Path]::GetTempPath()) "simple-montage-download"
New-Item -ItemType Directory -Force -Path $bin, $tmp | Out-Null

function Get-Url($url, $outFile) {
    Write-Host "  $url"
    Invoke-WebRequest -Uri $url -OutFile $outFile -UseBasicParsing -Headers @{ "User-Agent" = "simple-montage-build" }
}

# ---------------------------------------------------------------- FFmpeg
if (-not ((Test-Path "$bin\ffmpeg.exe") -and (Test-Path "$bin\ffprobe.exe"))) {
    Write-Host "Downloading FFmpeg..."
    $zip = Join-Path $tmp "ffmpeg.zip"
    $sources = @(
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    )
    $done = $false
    foreach ($url in $sources) {
        try { Get-Url $url $zip; $done = $true; break }
        catch { Write-Warning "Download failed: $($_.Exception.Message)" }
    }
    if (-not $done) { throw "Could not download FFmpeg." }
    $out = Join-Path $tmp "ffmpeg"
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $out -Force
    Get-ChildItem $out -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $bin -Force
}

# ---------------------------------------------------------------- libmpv
if (-not (Test-Path "$bin\libmpv-2.dll")) {
    Write-Host "Downloading libmpv..."
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/zhongfly/mpv-winbuild/releases/latest" `
        -Headers @{ "User-Agent" = "simple-montage-build" }
    $asset = $release.assets | Where-Object { $_.name -match '^mpv-dev-x86_64-\d{8}-git-[0-9a-f]+\.7z$' } | Select-Object -First 1
    if (-not $asset) { throw "No mpv-dev-x86_64 package found in the latest mpv-winbuild release." }
    $archive = Join-Path $tmp $asset.name
    Get-Url $asset.browser_download_url $archive
    $out = Join-Path $tmp "mpv"
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $sevenZip = Get-Command 7z -ErrorAction SilentlyContinue
    if (-not $sevenZip -and (Test-Path "$env:ProgramFiles\7-Zip\7z.exe")) { $sevenZip = "$env:ProgramFiles\7-Zip\7z.exe" }
    if ($sevenZip) { & $sevenZip x $archive "-o$out" -y | Out-Null }
    else { tar -xf $archive -C $out }   # Windows' built-in tar can read .7z on recent versions
    if ($LASTEXITCODE) { throw "Could not extract $archive (install 7-Zip and try again)." }
    $dll = Get-ChildItem $out -Recurse -Filter libmpv-2.dll | Select-Object -First 1
    if (-not $dll) { throw "libmpv-2.dll not found in $archive" }
    Copy-Item $dll.FullName -Destination $bin -Force
}

Get-ChildItem $bin -Include *.exe, *.dll -Recurse | ForEach-Object { Write-Host ("  {0,-14} {1,6:N1} MB" -f $_.Name, ($_.Length / 1MB)) }
