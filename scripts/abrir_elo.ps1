# Abre o Elo: sobe o Ollama e o servidor (se ainda não estiverem no ar), põe o ícone na bandeja
# e abre a aba no Chrome. É o que o atalho "Elo" da área de trabalho executa (sem janela, via
# conhost --headless). Recriar o atalho: powershell -ExecutionPolicy Bypass -File scripts\criar_atalho.ps1

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$endereco = "http://127.0.0.1:8000"
$log = Join-Path $raiz "logs\elo_servidor.log"

function No-Ar([string]$url) {
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true }
    catch { return [bool]$_.Exception.Response }  # respondeu, mesmo que com erro: está no ar
}

function Abrir-Chrome {
    $chrome = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($chrome) { Start-Process $chrome $endereco } else { Start-Process $endereco }  # sem Chrome: navegador padrão
}

function Abrir-Bandeja {  # o ícone perto do relógio; se já houver um, o próprio script sai
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", "`"$PSScriptRoot\bandeja_elo.ps1`""
}

# Já está no ar: só abre a aba.
if (No-Ar "$endereco/saude") { Abrir-Bandeja; Abrir-Chrome; exit }

# Janela pequena de "iniciando", para não parecer que nada aconteceu.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$janela = New-Object System.Windows.Forms.Form
$janela.Text = "Elo"
$janela.Size = New-Object System.Drawing.Size(360, 130)
$janela.StartPosition = "CenterScreen"
$janela.FormBorderStyle = "FixedDialog"
$janela.MaximizeBox = $false
$janela.MinimizeBox = $false
$janela.TopMost = $true
$icone = Join-Path $PSScriptRoot "elo.ico"
if (Test-Path $icone) { $janela.Icon = New-Object System.Drawing.Icon($icone) }
$texto = New-Object System.Windows.Forms.Label
$texto.Text = "Iniciando o Elo..."
$texto.Font = New-Object System.Drawing.Font("Segoe UI", 11)
$texto.AutoSize = $false
$texto.Size = New-Object System.Drawing.Size(320, 60)
$texto.Location = New-Object System.Drawing.Point(16, 18)
$janela.Controls.Add($texto)
$janela.Show()
[System.Windows.Forms.Application]::DoEvents()

function Avisar([string]$msg) { $texto.Text = $msg; [System.Windows.Forms.Application]::DoEvents() }

# 1. Ollama (o LLM local). Sem ele o Elo abre, só os rascunhos e o Pergunte ficam parados.
if (-not (No-Ar "http://127.0.0.1:11434/api/tags")) {
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
    if (Test-Path $ollama) { Avisar "Iniciando o Ollama..."; Start-Process $ollama -WindowStyle Hidden }
}

# 2. O servidor do Elo. Com Docker: os containers do compose.yaml (Elo + agenda diária).
#    Sem Docker: direto no Windows, sem janela; a saída vai para logs\elo_servidor.log.
New-Item -ItemType Directory -Force (Join-Path $raiz "logs") | Out-Null
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if ($docker -and (Test-Path (Join-Path $raiz "compose.yaml"))) {
    $log = "docker compose logs elo"
    & $docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Avisar "Iniciando o Docker..."
        Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe" -WindowStyle Hidden
        $ate = (Get-Date).AddSeconds(150)
        do { Start-Sleep -Seconds 3; & $docker info *> $null } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $ate)
    }
    Avisar "Iniciando o servidor do Elo (Docker)..."
    Start-Process $docker -ArgumentList "compose", "up", "-d" -WorkingDirectory $raiz -WindowStyle Hidden -Wait
} else {
    Avisar "Iniciando o servidor do Elo..."
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
    $comando = "set PYTHONUTF8=1&& set PYTHONUNBUFFERED=1&& `"$uv`" run python -m copilot_suporte.api >> `"$log`" 2>&1"
    Start-Process cmd.exe -ArgumentList "/c", $comando -WorkingDirectory $raiz -WindowStyle Hidden
}

# 3. Espera responder (o modelo de embeddings leva uns segundos para carregar).
$limite = (Get-Date).AddSeconds(240)  # 1ª subida do container: o modelo de busca ainda baixa
while (-not (No-Ar "$endereco/saude")) {
    if ((Get-Date) -gt $limite) {
        $janela.Close()
        [System.Windows.Forms.MessageBox]::Show("O Elo não subiu em 2 minutos. Veja o arquivo:`n$log", "Elo", "OK", "Error") | Out-Null
        exit 1
    }
    $s = [int]($limite - (Get-Date)).TotalSeconds
    Avisar "Iniciando o servidor do Elo... (até $s s)"
    Start-Sleep -Milliseconds 700
}

$janela.Close()
Abrir-Bandeja
Abrir-Chrome
