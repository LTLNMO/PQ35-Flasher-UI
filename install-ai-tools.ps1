# Installs the AI command-line tools PQ35 Flasher can use with your own
# subscription instead of an API key:
#   Claude Code  (Claude Pro/Max)     @anthropic-ai/claude-code
#   Gemini CLI   (Google account)     @google/gemini-cli
#   Codex CLI    (ChatGPT account)    @openai/codex
#
# install.ps1 runs this; you can also run it on its own to install or update
# them. Everything goes into PQ35 Flasher's own folder: no admin, nothing
# system-wide. Uses your Node.js if it's new enough, else downloads a private
# copy of the current Node.js LTS (checksum-verified).

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$Base = Join-Path $env:LOCALAPPDATA "PQ35-Flasher-UI"
$NodeDir = Join-Path $Base "node"
$ToolsDir = Join-Path $Base "ai-tools"
$Packages = @("@anthropic-ai/claude-code", "@google/gemini-cli", "@openai/codex")
$NodeMin = 20

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function NodeMajor($exe) {
    try { return [int](& $exe -p 'process.versions.node.split(".")[0]') } catch { return 0 }
}

$node = $null
$private = Join-Path $NodeDir "node.exe"
if (Test-Path $private) {
    $node = $private
} else {
    $system = Get-Command node -ErrorAction SilentlyContinue
    if ($system -and (NodeMajor $system.Source) -ge $NodeMin) { $node = $system.Source }
}

if (-not $node) {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
    Step "Finding the current Node.js LTS"
    $index = Invoke-RestMethod "https://nodejs.org/dist/index.json"
    $version = ($index | Where-Object { $_.lts } | Select-Object -First 1).version
    $file = "node-$version-win-$arch.zip"
    $tmp = Join-Path $env:TEMP ("pq35-node-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Force $tmp | Out-Null
    Step "Downloading Node.js $version (private copy for the AI tools)"
    Invoke-WebRequest "https://nodejs.org/dist/$version/$file" -OutFile "$tmp\$file"
    $sums = (Invoke-WebRequest "https://nodejs.org/dist/$version/SHASUMS256.txt" -UseBasicParsing).Content
    $expected = ($sums -split "`n" | Where-Object { $_ -match " $([regex]::Escape($file))$" }) -replace '\s.*$', ''
    $actual = (Get-FileHash "$tmp\$file" -Algorithm SHA256).Hash.ToLower()
    if (-not $expected -or $expected.Trim().ToLower() -ne $actual) { throw "Node.js download failed its checksum; not installing it." }
    Expand-Archive "$tmp\$file" "$tmp\x"
    if (Test-Path $NodeDir) { Remove-Item -Recurse -Force $NodeDir }
    Move-Item (Get-ChildItem "$tmp\x")[0].FullName $NodeDir
    Remove-Item -Recurse -Force $tmp
    $node = $private
}

$env:Path = "$(Split-Path $node);$env:Path"
$npm = Join-Path (Split-Path $node) "npm.cmd"
if (-not (Test-Path $npm)) { $npm = (Get-Command npm -ErrorAction Stop).Source }
Step "Installing/updating Claude Code, Gemini CLI and Codex CLI (Node.js $(& $node --version))"
New-Item -ItemType Directory -Force $ToolsDir | Out-Null
& $npm install --global --prefix $ToolsDir --no-fund --no-audit --loglevel=error @Packages
if ($LASTEXITCODE -ne 0) { throw "npm install failed" }

Write-Host "AI tools are in $ToolsDir. PQ35 Flasher finds them by itself."
Write-Host "Sign in once to the ones you'll use from Settings > AI > Sign in... in PQ35 Flasher."
