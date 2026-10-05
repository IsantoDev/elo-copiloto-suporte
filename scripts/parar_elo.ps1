# Para o servidor do Elo (o que escuta na porta 8000). O Ollama continua aberto.
# No Docker: para os containers (Elo e agenda). Senão: o processo do Windows na porta 8000.
$raiz = Split-Path -Parent $PSScriptRoot
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if ($docker) {
    $ids = & $docker compose --project-directory $raiz ps -q 2>$null
    if ($ids) { & $docker compose --project-directory $raiz stop *> $null; Write-Output "Elo parado."; exit }
}
$conexoes = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $conexoes) { Write-Output "O Elo não está rodando."; exit }
$conexoes | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
Write-Output "Elo parado."
