# Ícone do Elo na bandeja (perto do relógio): Abrir, Ver log, Pausar/Retomar pré-geração e Desligar.
# Sobe pelo abrir_elo.ps1 (atalho "Elo"), sem janela. Um ícone só: se já houver um, este sai.
# O ícone some sozinho quando o servidor sai do ar (desligado por Configurações › Servidor, por
# exemplo). A pausa é o arquivo logs\pregeracao_pausada, o mesmo que Configurações usa.
#
# O menu usa as cores da tela do Elo (tokens de static/index.html) e segue o tema claro/escuro do
# Windows, como a tela segue o do navegador.
#
# Prévia do menu sem mexer no ícone que está rodando (abre o menu, salva um print e sai):
#   powershell -STA -File scripts\bandeja_elo.ps1 -Previa C:\caminho\menu.png [-Tema claro|escuro]
param([string]$Previa, [ValidateSet("", "claro", "escuro")][string]$Tema = "")

$raiz = Split-Path -Parent $PSScriptRoot
$endereco = "http://127.0.0.1:8000"
$log = Join-Path $raiz "logs\elo_servidor.log"
$pausa = Join-Path $raiz "logs\pregeracao_pausada"

if (-not $Previa) {
    $criado = $false
    $trava = New-Object System.Threading.Mutex($true, "Local\EloBandeja", [ref]$criado)
    if (-not $criado) { exit }
}

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class EloRenderer : ToolStripRenderer {
    public Color Papel, Tinta, Linha, DestaqueFundo, DestaqueTexto, Perigo, PerigoFundo;
    public Color Inicio = ColorTranslator.FromHtml("#6a10f3"), Fim = ColorTranslator.FromHtml("#f24479");

    protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e) {
        using (SolidBrush b = new SolidBrush(Papel)) e.Graphics.FillRectangle(b, e.AffectedBounds);
    }
    protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e) {
        using (Pen p = new Pen(Linha)) e.Graphics.DrawRectangle(p, 0, 0, e.ToolStrip.Width - 1, e.ToolStrip.Height - 1);
    }
    protected override void OnRenderImageMargin(ToolStripRenderEventArgs e) { }
    protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e) {
        if (!e.Item.Selected || !e.Item.Enabled) return;
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        Rectangle r = new Rectangle(4, 1, e.Item.Width - 8, e.Item.Height - 2);
        using (GraphicsPath caminho = Arredondado(r, 6))
        using (SolidBrush b = new SolidBrush(EhPerigo(e.Item) ? PerigoFundo : DestaqueFundo))
            e.Graphics.FillPath(b, caminho);
    }
    protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e) {
        if (e.Item is ToolStripMenuItem)
            e.TextColor = EhPerigo(e.Item) ? Perigo : (e.Item.Selected ? DestaqueTexto : Tinta);
        else
            e.TextColor = e.Item.ForeColor;
        base.OnRenderItemText(e);
    }
    protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e) {
        int y = e.Item.Height / 2;
        if ("degrade".Equals(e.Item.Tag)) {  // a faixa da marca, embaixo do cabeçalho
            Rectangle r = new Rectangle(12, y - 1, e.Item.Width - 24, 2);
            using (LinearGradientBrush b = new LinearGradientBrush(r, Inicio, Fim, LinearGradientMode.Horizontal))
                e.Graphics.FillRectangle(b, r);
        } else {
            using (Pen p = new Pen(Linha)) e.Graphics.DrawLine(p, 12, y, e.Item.Width - 12, y);
        }
    }

    static bool EhPerigo(ToolStripItem item) { return "perigo".Equals(item.Tag); }

    static GraphicsPath Arredondado(Rectangle r, int raio) {
        int d = raio * 2;
        GraphicsPath caminho = new GraphicsPath();
        caminho.AddArc(r.X, r.Y, d, d, 180, 90);
        caminho.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        caminho.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        caminho.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        caminho.CloseFigure();
        return caminho;
    }

    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr janela, int atributo, ref int valor, int tamanho);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();

    // Cantos arredondados do Windows 11 (no Windows 10 não faz nada).
    public static void Arredondar(IntPtr janela) {
        int arredondado = 2;
        try { DwmSetWindowAttribute(janela, 33, ref arredondado, 4); } catch (Exception) { }
    }
}
"@

[EloRenderer]::SetProcessDPIAware() | Out-Null  # sem isso, o Windows estica o menu e ele fica borrado
[System.Windows.Forms.Application]::EnableVisualStyles()
$escala = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero).DpiX / 96

# Paleta da tela (static/index.html): tema claro e tema escuro.
$temas = @{
    claro  = @{ papel = "#ffffff"; tinta = "#1a1625"; suave = "#6b6578"; linha = "#e6e2ec"; violetaClaro = "#f1e9fe"
                violetaTexto = "#5a0bd6"; rosa = "#f24479"; rosaClaro = "#fde7ee"; verde = "#0f7b5f"; ambar = "#8a5a00" }
    escuro = @{ papel = "#1e1929"; tinta = "#ece9f2"; suave = "#a59fb3"; linha = "#2c2638"; violetaClaro = "#2a1d45"
                violetaTexto = "#c4a3ff"; rosa = "#f24479"; rosaClaro = "#3d1a27"; verde = "#4fd1a5"; ambar = "#ffd27a" }
}
function Cor([string]$hex) { [System.Drawing.ColorTranslator]::FromHtml($hex) }
function Tema-Atual {
    if ($Tema) { return $temas[$Tema] }  # forçado na prévia
    $claro = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" -ErrorAction SilentlyContinue).AppsUseLightTheme
    if ($claro -eq 0) { $temas.escuro } else { $temas.claro }
}

# Ícones de linha da fonte do Windows 11 (Segoe Fluent Icons; no Windows 10, Segoe MDL2 Assets).
$familias = (New-Object System.Drawing.Text.InstalledFontCollection).Families.Name
$fonteIcones = if ($familias -contains "Segoe Fluent Icons") { "Segoe Fluent Icons" } else { "Segoe MDL2 Assets" }
$fonteTexto = if ($familias -contains "Barlow") { "Barlow" } else { "Segoe UI" }  # a mesma ordem da tela
function Glifo([int]$codigo, [string]$hex) {
    $lado = [int](16 * $escala)
    $imagem = New-Object System.Drawing.Bitmap($lado, $lado)
    $g = [System.Drawing.Graphics]::FromImage($imagem)
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $fonte = New-Object System.Drawing.Font($fonteIcones, (12 * $escala), [System.Drawing.GraphicsUnit]::Pixel)
    $formato = New-Object System.Drawing.StringFormat
    $formato.Alignment = [System.Drawing.StringAlignment]::Center
    $formato.LineAlignment = [System.Drawing.StringAlignment]::Center
    $pincel = New-Object System.Drawing.SolidBrush(Cor $hex)
    $g.DrawString([string][char]$codigo, $fonte, $pincel, (New-Object System.Drawing.RectangleF(0, 0, $lado, $lado)), $formato)
    $g.Dispose(); $fonte.Dispose(); $pincel.Dispose()
    $imagem
}

function No-Ar {
    try { Invoke-WebRequest -Uri "$endereco/saude" -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true }
    catch { return [bool]$_.Exception.Response }  # respondeu, mesmo que com erro: está no ar
}

function Abrir {
    $chrome = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($chrome) { Start-Process $chrome $endereco } else { Start-Process $endereco }
}

$arquivoIcone = Join-Path $PSScriptRoot "elo.ico"
$icone = New-Object System.Windows.Forms.NotifyIcon
$icone.Icon = if (Test-Path $arquivoIcone) { New-Object System.Drawing.Icon($arquivoIcone) } else { [System.Drawing.SystemIcons]::Application }
$icone.Text = "Elo"

$renderizador = New-Object EloRenderer
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menu.Renderer = $renderizador
$menu.ShowCheckMargin = $false
$menu.ShowImageMargin = $true
$menu.ImageScalingSize = New-Object System.Drawing.Size([int](16 * $escala), [int](16 * $escala))
$menu.Font = New-Object System.Drawing.Font($fonteTexto, 10)
$menu.Padding = New-Object System.Windows.Forms.Padding(4, 8, 4, 8)
$menu.MinimumSize = New-Object System.Drawing.Size([int](230 * $escala), 0)

# Cabeçalho: logo + nome, a situação do servidor e a faixa em degradê da marca.
$cabecalho = New-Object System.Windows.Forms.ToolStripLabel("Elo")
$cabecalho.Font = New-Object System.Drawing.Font($fonteTexto, 11.5, [System.Drawing.FontStyle]::Bold)
# O logo vem do PNG do app: o .ico guarda os tamanhos grandes em PNG, e Icon.ToBitmap os desenha como ruído.
$arquivoLogo = Join-Path $raiz "src\copilot_suporte\static\elo-icone-app-192.png"
if (Test-Path $arquivoLogo) {
    $original = [System.Drawing.Image]::FromFile($arquivoLogo)
    $ladoLogo = [int](22 * $escala)
    $logo = New-Object System.Drawing.Bitmap($ladoLogo, $ladoLogo)
    $g = [System.Drawing.Graphics]::FromImage($logo)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.DrawImage($original, 0, 0, $ladoLogo, $ladoLogo)
    $g.Dispose(); $original.Dispose()
    $cabecalho.Image = $logo
}
$cabecalho.ImageScaling = [System.Windows.Forms.ToolStripItemImageScaling]::None
$cabecalho.Padding = New-Object System.Windows.Forms.Padding(6, 2, 0, 0)
$situacao = New-Object System.Windows.Forms.ToolStripLabel("")
$situacao.Font = New-Object System.Drawing.Font($fonteTexto, 9)
$situacao.Padding = New-Object System.Windows.Forms.Padding(6, 0, 0, 2)
$faixa = New-Object System.Windows.Forms.ToolStripSeparator
$faixa.Tag = "degrade"
$faixa.AutoSize = $false
$faixa.Height = [int](14 * $escala)

$itemAbrir = New-Object System.Windows.Forms.ToolStripMenuItem("Abrir o Elo")
$itemAbrir.Font = New-Object System.Drawing.Font($fonteTexto, 10, [System.Drawing.FontStyle]::Bold)
$itemLog = New-Object System.Windows.Forms.ToolStripMenuItem("Ver log")
$itemPausa = New-Object System.Windows.Forms.ToolStripMenuItem("Pausar pré-geração")
$divisao = New-Object System.Windows.Forms.ToolStripSeparator
$itemDesligar = New-Object System.Windows.Forms.ToolStripMenuItem("Desligar")
$itemDesligar.Tag = "perigo"
foreach ($item in $itemAbrir, $itemLog, $itemPausa, $itemDesligar) {
    $item.Padding = New-Object System.Windows.Forms.Padding(2, [int](5 * $escala), 2, [int](5 * $escala))
}
$menu.Items.AddRange(@($cabecalho, $situacao, $faixa, $itemAbrir, $itemLog, $itemPausa, $divisao, $itemDesligar))
$icone.ContextMenuStrip = $menu

$script:noAr = $true
function Atualizar-Menu {  # cores do tema de agora, situação e o texto do que cada clique vai fazer
    $t = Tema-Atual
    $renderizador.Papel = Cor $t.papel; $renderizador.Tinta = Cor $t.tinta; $renderizador.Linha = Cor $t.linha
    $renderizador.DestaqueFundo = Cor $t.violetaClaro; $renderizador.DestaqueTexto = Cor $t.violetaTexto
    $renderizador.Perigo = Cor $t.rosa; $renderizador.PerigoFundo = Cor $t.rosaClaro
    $cabecalho.ForeColor = Cor $t.tinta
    $pausada = Test-Path $pausa
    if (-not $script:noAr) { $situacao.Text = "●  Fora do ar"; $situacao.ForeColor = Cor $t.rosa }
    elseif ($pausada) { $situacao.Text = "●  No ar  ·  pré-geração pausada"; $situacao.ForeColor = Cor $t.ambar }
    else { $situacao.Text = "●  No ar  ·  pré-geração ativa"; $situacao.ForeColor = Cor $t.verde }
    $itemPausa.Text = if ($pausada) { "Retomar pré-geração" } else { "Pausar pré-geração" }
    $itemAbrir.Image = Glifo 0xE8A7 $t.tinta      # abrir em nova janela
    $itemLog.Image = Glifo 0xE8A5 $t.tinta        # documento
    $itemPausa.Image = if ($pausada) { Glifo 0xE768 $t.tinta } else { Glifo 0xE769 $t.tinta }  # retomar / pausar
    $itemDesligar.Image = Glifo 0xE7E8 $t.rosa    # liga/desliga
}

function Encerrar {
    $relogio.Stop()
    $icone.Visible = $false
    $icone.Dispose()
    [System.Windows.Forms.Application]::Exit()
}

$menu.add_Opening({ Atualizar-Menu })
$menu.add_Opened({ [EloRenderer]::Arredondar($menu.Handle) })
$itemAbrir.add_Click({ Abrir })
$icone.add_DoubleClick({ Abrir })
# No Docker: janela com os logs do container ao vivo (fechar a janela não para o Elo). Sem Docker: o arquivo de log.
$itemLog.add_Click({
    $docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
    $noDocker = $docker -and (& $docker compose --project-directory $raiz ps -q elo 2>$null)
    if ($noDocker) {
        Start-Process powershell.exe -WorkingDirectory $raiz -ArgumentList "-NoExit", "-NoProfile", "-Command",
            "`$Host.UI.RawUI.WindowTitle = 'Elo - logs'; docker compose logs -f --tail 200 elo agenda | Select-String -NotMatch 'GET /'"
    } elseif (Test-Path $log) { Start-Process notepad.exe $log }
})
$itemPausa.add_Click({
    if (Test-Path $pausa) {
        Remove-Item $pausa -Force
        $icone.ShowBalloonTip(3000, "Elo", "Pré-geração retomada.", [System.Windows.Forms.ToolTipIcon]::Info)
    } else {
        New-Item -ItemType File -Force $pausa | Out-Null
        $icone.ShowBalloonTip(3000, "Elo", "Pré-geração pausada até ser retomada.", [System.Windows.Forms.ToolTipIcon]::Info)
    }
})
$itemDesligar.add_Click({
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "parar_elo.ps1") | Out-Null
    Encerrar
})

# Confere a cada 10 s; depois de 3 falhas seguidas (30 s fora do ar), o ícone sai.
$script:falhas = 0
$relogio = New-Object System.Windows.Forms.Timer
$relogio.Interval = 10000
$relogio.add_Tick({
    $script:noAr = No-Ar
    if ($script:noAr) { $script:falhas = 0 } else { $script:falhas++ }
    if ($script:falhas -ge 3) { Encerrar }
})

if ($Previa) {
    $script:noAr = No-Ar
    Atualizar-Menu
    $menu.Show((New-Object System.Drawing.Point(300, 300)))
    $itemAbrir.Select()  # mostra o destaque do item sob o mouse
    $espera = New-Object System.Windows.Forms.Timer
    $espera.Interval = 700
    $espera.add_Tick({
        $espera.Stop()
        $area = $menu.Bounds
        $largura = $area.Width + 24
        $altura = $area.Height + 24
        $print = New-Object System.Drawing.Bitmap($largura, $altura)
        $g = [System.Drawing.Graphics]::FromImage($print)
        $g.CopyFromScreen($area.X - 12, $area.Y - 12, 0, 0, $print.Size)
        $print.Save($Previa, [System.Drawing.Imaging.ImageFormat]::Png)
        $menu.Close()
        [System.Windows.Forms.Application]::Exit()
    })
    $espera.Start()
    [System.Windows.Forms.Application]::Run()
    exit
}

Atualizar-Menu
$icone.Visible = $true
$relogio.Start()
[System.Windows.Forms.Application]::Run()
if ($trava) { $trava.ReleaseMutex() }
