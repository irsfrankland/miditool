# Draws assets/rc10r-pedal.png, the background of the rehearsal window.
# The ring lights and the screen text are drawn live by pedal_sim.py, so their
# positions here must match the constants in PedalWindow.
Add-Type -AssemblyName System.Drawing

$W = 900
$H = 600
$out = Join-Path (Split-Path $PSScriptRoot -Parent) "assets\rc10r-pedal.png"

$bmp = New-Object System.Drawing.Bitmap $W, $H
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

function C([string]$hex, [int]$a = 255) {
    $c = [System.Drawing.ColorTranslator]::FromHtml($hex)
    [System.Drawing.Color]::FromArgb($a, $c.R, $c.G, $c.B)
}

function RoundRect([float]$x, [float]$y, [float]$w, [float]$h, [float]$r) {
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $r * 2
    $p.AddArc($x, $y, $d, $d, 180, 90)
    $p.AddArc($x + $w - $d, $y, $d, $d, 270, 90)
    $p.AddArc($x + $w - $d, $y + $h - $d, $d, $d, 0, 90)
    $p.AddArc($x, $y + $h - $d, $d, $d, 90, 90)
    $p.CloseFigure()
    $p
}

function Vertical([float]$x, [float]$y, [float]$w, [float]$h, [string]$top, [string]$bottom) {
    $rect = New-Object System.Drawing.RectangleF $x, $y, $w, $h
    New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, (C $top), (C $bottom), 90.0
}

function Text([string]$s, [string]$family, [float]$size, [System.Drawing.FontStyle]$style, [string]$color, [float]$x, [float]$y, [string]$align = "center") {
    $font = New-Object System.Drawing.Font $family, $size, $style, ([System.Drawing.GraphicsUnit]::Pixel)
    $brush = New-Object System.Drawing.SolidBrush (C $color)
    $fmt = New-Object System.Drawing.StringFormat
    $fmt.Alignment = switch ($align) { "left" { "Near" } "right" { "Far" } default { "Center" } }
    $fmt.LineAlignment = "Center"
    $g.DrawString($s, $font, $brush, $x, $y, $fmt)
    $font.Dispose(); $brush.Dispose()
}

function Knob([float]$cx, [float]$cy, [float]$r, [float]$angle) {
    $shadow = New-Object System.Drawing.SolidBrush (C "#000000" 120)
    $g.FillEllipse($shadow, $cx - $r - 2, $cy - $r + 3, ($r + 2) * 2, ($r + 2) * 2)
    $edge = Vertical ($cx - $r) ($cy - $r) ($r * 2) ($r * 2) "#3a3a3e" "#0c0c0d"
    $g.FillEllipse($edge, $cx - $r, $cy - $r, $r * 2, $r * 2)
    $pen = New-Object System.Drawing.Pen (C "#1a1a1c"), 1.2
    for ($i = 0; $i -lt 72; $i++) {
        $t = $i * [Math]::PI * 2 / 72
        $g.DrawLine($pen, $cx + [Math]::Cos($t) * ($r - 5), $cy + [Math]::Sin($t) * ($r - 5), $cx + [Math]::Cos($t) * $r, $cy + [Math]::Sin($t) * $r)
    }
    $ir = $r - 6
    $cap = Vertical ($cx - $ir) ($cy - $ir) ($ir * 2) ($ir * 2) "#38383c" "#141416"
    $g.FillEllipse($cap, $cx - $ir, $cy - $ir, $ir * 2, $ir * 2)
    $shine = New-Object System.Drawing.Pen (C "#ffffff" 40), 1.5
    $g.DrawArc($shine, $cx - $ir + 2, $cy - $ir + 2, ($ir - 2) * 2, ($ir - 2) * 2, 200, 120)
    $mark = New-Object System.Drawing.Pen (C "#f2f2f2"), 3
    $mark.StartCap = "Round"; $mark.EndCap = "Round"
    $t = $angle * [Math]::PI / 180
    $g.DrawLine($mark, $cx + [Math]::Cos($t) * ($ir * 0.35), $cy + [Math]::Sin($t) * ($ir * 0.35), $cx + [Math]::Cos($t) * ($ir - 3), $cy + [Math]::Sin($t) * ($ir - 3))
    $shadow.Dispose(); $edge.Dispose(); $pen.Dispose(); $cap.Dispose(); $shine.Dispose(); $mark.Dispose()
}

function Ring([float]$cx, [float]$cy) {
    $well = New-Object System.Drawing.SolidBrush (C "#0b0b0c")
    $g.FillEllipse($well, $cx - 90, $cy - 90, 180, 180)
    $lip = New-Object System.Drawing.Pen (C "#3c3c41"), 2
    $g.DrawEllipse($lip, $cx - 90, $cy - 90, 180, 180)
    $track = New-Object System.Drawing.Pen (C "#1d1d20"), 12
    $g.DrawEllipse($track, $cx - 76, $cy - 76, 152, 152)
    $well.Dispose(); $lip.Dispose(); $track.Dispose()
}

function Button([float]$cx, [float]$cy, [string]$label) {
    $path = RoundRect ($cx - 26) ($cy - 11) 52 22 6
    $fill = Vertical ($cx - 26) ($cy - 11) 52 22 "#3a3a3f" "#1c1c1f"
    $g.FillPath($fill, $path)
    $pen = New-Object System.Drawing.Pen (C "#4a4a50"), 1
    $g.DrawPath($pen, $path)
    Text $label "Segoe UI" 11 ([System.Drawing.FontStyle]::Bold) "#cfcfd4" $cx ($cy + 24)
    $fill.Dispose(); $pen.Dispose(); $path.Dispose()
}

function Pad([float]$x, [float]$y, [float]$w, [float]$h) {
    $shadow = RoundRect ($x - 2) ($y + 6) ($w + 4) ($h + 2) 18
    $sb = New-Object System.Drawing.SolidBrush (C "#000000" 150)
    $g.FillPath($sb, $shadow)
    $path = RoundRect $x $y $w $h 16
    $fill = Vertical $x $y $w $h "#2c2c2f" "#151516"
    $g.FillPath($fill, $path)
    $g.SetClip($path)
    $ridge = New-Object System.Drawing.Pen (C "#000000" 90), 2
    $light = New-Object System.Drawing.Pen (C "#ffffff" 14), 1
    for ($ry = $y + 18; $ry -lt $y + $h - 10; $ry += 9) {
        $g.DrawLine($ridge, $x + 18, $ry, $x + $w - 18, $ry)
        $g.DrawLine($light, $x + 18, $ry + 2, $x + $w - 18, $ry + 2)
    }
    $g.ResetClip()
    $edge = New-Object System.Drawing.Pen (C "#47474d"), 1.5
    $g.DrawPath($edge, $path)
    $top = New-Object System.Drawing.Pen (C "#ffffff" 35), 1.5
    $g.DrawLine($top, $x + 16, $y + 1.5, $x + $w - 16, $y + 1.5)
    $shadow.Dispose(); $sb.Dispose(); $path.Dispose(); $fill.Dispose(); $ridge.Dispose(); $light.Dispose(); $edge.Dispose(); $top.Dispose()
}

# Floor
$floor = Vertical 0 0 $W $H "#18181b" "#09090a"
$g.FillRectangle($floor, 0, 0, $W, $H)

# Body with a soft shadow
for ($i = 6; $i -ge 1; $i--) {
    $sh = RoundRect (40 - $i * 2) (44 - $i + 6) (820 + $i * 4) (530 + $i * 3) (28 + $i)
    $b = New-Object System.Drawing.SolidBrush (C "#000000" 22)
    $g.FillPath($b, $sh); $b.Dispose(); $sh.Dispose()
}
$body = RoundRect 40 40 820 530 28
$bodyFill = Vertical 40 40 820 530 "#c8171e" "#7a0b10"
$g.FillPath($bodyFill, $body)
$g.DrawPath((New-Object System.Drawing.Pen (C "#5a0609"), 2), $body)

# Sloped top panel
$panel = RoundRect 56 54 788 262 20
$panelFill = Vertical 56 54 788 262 "#d01b22" "#9e1016"
$g.FillPath($panelFill, $panel)
$g.DrawPath((New-Object System.Drawing.Pen (C "#e4545a"), 1.5), $panel)
$g.DrawLine((New-Object System.Drawing.Pen (C "#ffffff" 70), 1), 76, 55.5, 824, 55.5)

# Branding
Text "BOSS" "Arial Black" 26 ([System.Drawing.FontStyle]::Italic) "#f4f4f4" 80 80 "left"
Text "RC-10R" "Arial" 22 ([System.Drawing.FontStyle]::Bold) "#f4f4f4" 444 84 "right"
Text "Rhythm Loop Station" "Segoe UI" 13 ([System.Drawing.FontStyle]::Regular) "#ffd9db" 452 86 "left"

# Loop ring and rhythm ring, each with its level knob
Ring 185 190
Ring 715 190
Knob 185 190 48 -130
Knob 715 190 48 -50
Text "LOOP" "Segoe UI" 12 ([System.Drawing.FontStyle]::Bold) "#ffffff" 185 292
Text "RHYTHM" "Segoe UI" 12 ([System.Drawing.FontStyle]::Bold) "#ffffff" 715 292
Text "LEVEL" "Segoe UI" 9 ([System.Drawing.FontStyle]::Regular) "#ffd0d2" 118 292
Text "LEVEL" "Segoe UI" 9 ([System.Drawing.FontStyle]::Regular) "#ffd0d2" 782 292

# 16 x 2 character screen
$bezel = RoundRect 300 118 300 92 10
$g.FillPath((New-Object System.Drawing.SolidBrush (C "#0a0a0b")), $bezel)
$g.DrawPath((New-Object System.Drawing.Pen (C "#4a4a50"), 1.5), $bezel)
$lcd = RoundRect 312 128 276 72 4
$lcdFill = Vertical 312 128 276 72 "#3a6cf0" "#1f45c2"
$g.FillPath($lcdFill, $lcd)

# MENU, EXIT and the push dial
Button 345 238 "MENU"
Button 415 238 "EXIT"
Knob 545 244 24 -90
Text "MEMORY / VALUE" "Segoe UI" 9 ([System.Drawing.FontStyle]::Regular) "#ffd0d2" 545 282

# Footswitch pads
Pad 82 340 348 170
Pad 470 340 348 170
Text "LOOP" "Segoe UI" 14 ([System.Drawing.FontStyle]::Bold) "#ffffff" 256 530
Text "REC / PLAY / DUB    STOP: tap twice    UNDO: hold" "Segoe UI" 10 ([System.Drawing.FontStyle]::Regular) "#ffd9db" 256 551
Text "RHYTHM" "Segoe UI" 14 ([System.Drawing.FontStyle]::Bold) "#ffffff" 644 530
Text "START / FILL    PART: hold    END: tap twice" "Segoe UI" 10 ([System.Drawing.FontStyle]::Regular) "#ffd9db" 644 551

$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "Saved $out"
