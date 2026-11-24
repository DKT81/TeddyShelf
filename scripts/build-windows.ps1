<# Build and verify a standalone Windows-x64 EXE; no application user data is read. #>
[CmdletBinding()]
param(
    [string]$PythonExe = "python",
    [string]$OutputDirectory = "dist/windows-release",
    [switch]$UseExistingEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    function Invoke-BuildPython {
        # Pass Python switches as array values so PowerShell never binds them as
        # common parameters (for example pip -e versus -ErrorAction).
        param([string[]]$Arguments)
        & $script:buildPython @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Python step failed ($LASTEXITCODE): $($Arguments -join ' ')" }
    }

    $script:buildPython = $PythonExe
    Invoke-BuildPython -Arguments @("-c", "import sys,struct; assert sys.platform=='win32' and struct.calcsize('P')==8, 'Windows-x64 Python required'")
    if (-not $UseExistingEnvironment) {
        $environmentPath = Join-Path $projectRoot ".venv-build-windows"
        Invoke-BuildPython -Arguments @("-m", "venv", $environmentPath)
        $script:buildPython = Join-Path $environmentPath "Scripts/python.exe"
        Invoke-BuildPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")
        Invoke-BuildPython -Arguments @("-m", "pip", "install", "-r", "requirements-build-windows.txt")
        Invoke-BuildPython -Arguments @("-m", "pip", "install", "--no-deps", "-e", ".")
    }

    $artifactRoot = Join-Path $projectRoot "artifacts/windows-build"
    New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
    $env:TEDDYSHELF_DATA = Join-Path $artifactRoot "test-data"
    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-BuildPython -Arguments @("scripts/prepare_windows_tools.py")
    Invoke-BuildPython -Arguments @("scripts/extract_translations.py", "--check")
    Invoke-BuildPython -Arguments @("-m", "pytest", "-q", "--junitxml=$artifactRoot/test-results.xml")
    Invoke-BuildPython -Arguments @("scripts/build_windows.py", "--onefile", "--output-dir", $OutputDirectory)

    $outputPath = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
        [System.IO.Path]::GetFullPath($OutputDirectory)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
    }
    $version = (& $script:buildPython -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot determine application version" }
    $exeName = "TeddyShelf-$version-Windows-x64.exe"
    $executable = Join-Path $outputPath $exeName
    Invoke-BuildPython -Arguments @("scripts/verify_windows_exe.py", $executable, "--output", (Join-Path $artifactRoot "standalone-check"))
    Copy-Item -LiteralPath "artifacts/build-provenance.json" -Destination (Join-Path $outputPath "build-provenance.json")
    Compress-Archive -Path "artifacts/licenses/*" -DestinationPath (Join-Path $outputPath "TeddyShelf-$version-Licenses.zip") -Force
    if ($env:GITHUB_OUTPUT) {
        "version=$version" | Out-File -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8 -Append
        "exe=$exeName" | Out-File -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8 -Append
    }
    Write-Host "Verified Windows-x64 executable: $executable"
} finally {
    Pop-Location
}
