# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
#
# Build PyAmp for Windows (x86_64) with libmpv bundled: the app folder, a portable zip
# and an installer (Inno Setup).
#
#   pwsh packaging/windows/build.ps1 [-Out dist] [-Libmpv path\to\libmpv-2.dll]
#
# -Libmpv bundles that DLL instead of the pinned download (e.g. one just built by
# packaging/windows/libmpv/build.sh, to test it in the app).
#
# The pinned libmpv download is cached in build\libmpv-cache\ and checked against the
# SHA-256 in packaging\windows\libmpv.lock. Needs 7-Zip (7z) and Inno Setup 6 (ISCC).
param([string]$Out = "dist", [string]$Libmpv = "")
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Out = [IO.Path]::GetFullPath((Join-Path (Get-Location) $Out))
$Work = Join-Path ([IO.Path]::GetTempPath()) ("pyamp-build-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $Work, $Out | Out-Null

function Find-Tool([string]$Name, [string[]]$Candidates) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($path in $Candidates) { if (Test-Path $path) { return $path } }
    throw "$Name not found; install it first (see the comment at the top of this script)"
}

try {
    # -- libmpv ---------------------------------------------------------------- #
    New-Item -ItemType Directory -Force "$Work\libmpv" | Out-Null
    if ($Libmpv) {
        Copy-Item (Resolve-Path $Libmpv).Path "$Work\libmpv\libmpv-2.dll"
    } else {
        $entry = Get-Content "$Root\packaging\windows\libmpv.lock" |
            Where-Object { $_ -match '^x86_64\s' } | Select-Object -First 1
        if (-not $entry) { throw "no x86_64 libmpv pinned in packaging\windows\libmpv.lock" }
        $null, $sha, $url = $entry -split '\s+'
        $cache = "$Root\build\libmpv-cache"
        New-Item -ItemType Directory -Force $cache | Out-Null
        $archive = Join-Path $cache ([IO.Path]::GetFileName($url))
        if (-not (Test-Path $archive)) {
            Write-Host "Downloading $([IO.Path]::GetFileName($url))"
            Invoke-WebRequest $url -OutFile "$archive.part"
            Move-Item "$archive.part" $archive
        }
        $actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $sha) {
            Remove-Item $archive
            throw "checksum mismatch for $archive (deleted; run again to download it again)"
        }
        $sevenZip = Find-Tool "7z" @("$env:ProgramFiles\7-Zip\7z.exe")
        & $sevenZip x $archive "-o$Work\archive" -y | Out-Null
        Copy-Item "$Work\archive\libmpv-2.dll" "$Work\libmpv\"
    }

    # -- App -------------------------------------------------------------------- #
    Push-Location $Root
    try {
        $version = (uv run --group packaging python -c "import pyamp; print(pyamp.__version__)").Trim()
        Write-Host "Building PyAmp $version for Windows x86_64"
        $env:PYAMP_BUNDLE_LIBMPV = "$Work\libmpv"
        $env:PYAMP_NOTICES = "$Root\packaging\THIRD_PARTY_NOTICES.md"
        uv run --group packaging pyinstaller --noconfirm --clean --log-level WARN `
            --distpath "$Work\dist" --workpath "$Work\build" packaging\windows\pyamp.spec
    } finally {
        Pop-Location
    }
    $app = "$Work\dist\PyAmp"
    $name = "PyAmp-$version-windows-x86_64"
    if (Test-Path "$Out\PyAmp") { Remove-Item -Recurse -Force "$Out\PyAmp" }
    Copy-Item -Recurse $app "$Out\PyAmp"

    # -- Portable zip --------------------------------------------------------------- #
    $zip = "$Out\$name-portable.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal

    # -- Installer ------------------------------------------------------------------ #
    $iscc = Find-Tool "iscc" @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe")
    & $iscc /Q "/DAppVersion=$version" "/DSourceDir=$app" "/DOutputDir=$Out" "/DOutputName=$name-setup" `
        "/DRootDir=$Root" "$Root\packaging\windows\pyamp.iss"

    Write-Host "App:       $Out\PyAmp"
    Write-Host "Zip:       $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
    Write-Host "Installer: $Out\$name-setup.exe ($([math]::Round((Get-Item "$Out\$name-setup.exe").Length / 1MB)) MB)"
} finally {
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
