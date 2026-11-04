param([switch]$BuildOnly)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'The CampusInbox tray requires Windows.' }
$compiler = @(
    (Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    (Join-Path $env:WINDIR 'Microsoft.NET/Framework/v4.0.30319/csc.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) { throw 'Windows .NET Framework compiler was not found.' }
$source = Join-Path $PSScriptRoot 'windows-tray.cs'
$build = Join-Path $env:LOCALAPPDATA 'CampusInbox/tray/build'
New-Item -ItemType Directory -Path $build -Force | Out-Null
$version = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.Substring(0, 16)
$executable = Join-Path $build "CampusInbox-Tray-$version.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    $temporary = Join-Path $build ("Tray-" + [Guid]::NewGuid().ToString('N') + '.exe')
    try {
        & $compiler /nologo /codepage:65001 /target:winexe /r:System.dll /r:System.Core.dll `
            /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Net.Http.dll `
            /r:System.Web.Extensions.dll "/out:$temporary" $source
        if ($LASTEXITCODE -ne 0) { throw 'CampusInbox tray compilation failed.' }
        if (-not (Test-Path -LiteralPath $executable)) {
            Move-Item -LiteralPath $temporary -Destination $executable
        }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}
if ($BuildOnly) {
    Write-Output $executable
} else {
    Start-Process -FilePath $executable -WindowStyle Hidden
}
