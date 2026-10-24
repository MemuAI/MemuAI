[CmdletBinding()]
param([string]$OutputDirectory = (Join-Path $PSScriptRoot 'audit-results'))

# A custom, read-only scan: Microsoft documents that DisableRemediation
# ignores file exclusions and reports detections without applying quarantine.
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$taskPlatform = Get-ChildItem -LiteralPath "$env:ProgramData\Microsoft\Windows Defender\Platform" -Directory |
    Sort-Object Name -Descending | Select-Object -First 1
$taskScanner = if ($taskPlatform) { Join-Path $taskPlatform.FullName 'MpCmdRun.exe' } else {
    Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
}
if (-not (Test-Path -LiteralPath $taskScanner -PathType Leaf)) { throw 'Microsoft Defender scanner not found' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$taskOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
$taskLog = Join-Path $taskOutput 'defender-final.log'
# The custom installer (installer\build_setup.py) names the Setup files after the
# bundle version from app\version.py: installer\MemuAI-Setup-<version>.exe.
$taskVersionMatch = Select-String -LiteralPath (Join-Path $taskRoot 'app\version.py') -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' |
    Select-Object -First 1
if (-not $taskVersionMatch) { throw 'APP_VERSION not found in app\version.py' }
$taskVersion = $taskVersionMatch.Matches[0].Groups[1].Value
$taskFiles = @('dist\MemuAI\MemuAI.exe', 'dist\MemuAI\MemuAI.pkg',
    'dist\MemuAIProxy\MemuAIProxy.exe', 'dist\MemuCode\MemuCode.exe',
    'dist\proxy-only\MemuAIProxy\MemuAIProxy.exe',
    "installer\MemuAI-Setup-$taskVersion.exe", "installer\MemuAIProxy-Setup-$taskVersion.exe")

function Get-ExclusionFingerprint {
    $taskPreferences = Get-MpPreference
    $taskText = (@{paths=@($taskPreferences.ExclusionPath | Sort-Object)
        processes=@($taskPreferences.ExclusionProcess | Sort-Object)
        extensions=@($taskPreferences.ExclusionExtension | Sort-Object)} | ConvertTo-Json -Compress)
    $taskHasher = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskText))).Replace('-', '') }
    finally { $taskHasher.Dispose() }
}

$taskBefore = Get-ExclusionFingerprint
$taskStatus = Get-MpComputerStatus
$taskReport = [ordered]@{time=(Get-Date -Format o); scanner=$taskScanner
    signature=$taskStatus.AntivirusSignatureVersion; engine=$taskStatus.AMEngineVersion
    realtime_protection=$taskStatus.RealTimeProtectionEnabled; disable_remediation=$true
    exclusions_before=$taskBefore; scans=@()}
"Custom scans with DisableRemediation; file exclusions ignored; no remediation requested." |
    Set-Content -LiteralPath $taskLog -Encoding utf8
$taskFailed = $false
foreach ($taskRelative in $taskFiles) {
    $taskPath = Join-Path $taskRoot $taskRelative
    if (-not (Test-Path -LiteralPath $taskPath -PathType Leaf)) { throw "Missing release payload: $taskRelative" }
    $taskChecksum = (Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash
    $taskLines = @(& $taskScanner -Scan -ScanType 3 -File $taskPath -DisableRemediation 2>&1)
    $taskCode = $LASTEXITCODE
    $taskText = $taskLines -join "`n"
    $taskClean = $taskCode -eq 0 -and $taskText -match 'found no threats'
    $taskUnchanged = (Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash -eq $taskChecksum
    $taskReport.scans += [ordered]@{path=$taskRelative; sha256=$taskChecksum
        returncode=$taskCode; clean=$taskClean; unchanged=$taskUnchanged; output=$taskText}
    "$taskRelative`n$taskText`n" | Add-Content -LiteralPath $taskLog -Encoding utf8
    Write-Output "$taskRelative : clean=$taskClean unchanged=$taskUnchanged exit=$taskCode"
    $taskFailed = $taskFailed -or -not $taskClean -or -not $taskUnchanged
}
$taskReport.exclusions_after = Get-ExclusionFingerprint
$taskReport.exclusions_unchanged = $taskReport.exclusions_after -eq $taskBefore
$taskReport | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskOutput 'defender-final.json') -Encoding utf8
if ($taskFailed -or -not $taskReport.exclusions_unchanged) { exit 1 }
