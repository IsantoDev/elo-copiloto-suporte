# Cria (ou recria) o atalho "Elo" na área de trabalho, com a logo. Ele roda abrir_elo.ps1 sem abrir
# janela: o conhost --headless do Windows 11 esconde o console do PowerShell.
$desktop = [Environment]::GetFolderPath("Desktop")
$atalho = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "Elo.lnk"))
$atalho.TargetPath = "$env:SystemRoot\System32\conhost.exe"
$atalho.Arguments = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\abrir_elo.ps1`""
$atalho.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$atalho.IconLocation = "$PSScriptRoot\elo.ico,0"
$atalho.Description = "Elo - Copiloto de Suporte"
$atalho.Save()
Write-Output "Atalho criado em $desktop\Elo.lnk"
