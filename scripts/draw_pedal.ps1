# Draws assets/rc10r-pedal.png, the background of the rehearsal window.
# Shapes are laid out on a 600 x 833 face and saved at 90 %, so the picture
# is 540 x 750. The ring lights, the part names, the screen text, and the
# footswitch glow are drawn live by pedal_sim.py, so their positions there
# (PedalWindow constants, in saved pixels) must match the ones here.
Add-Type -AssemblyName System.Drawing

$S = 0.9
$FaceW = 600
$FaceH = 833
$W = [int]($FaceW * $S)
$H = [int]($FaceH * $S)
$out = Join-Path (Split-Path $PSScriptRoot -Parent) "assets\rc10r-pedal.png"

$bmp = New-Object System.Drawing.Bitmap $W, $H
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.ScaleTransform($S, $S)

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

function Linear([float]$x, [float]$y, [float]$w, [float]$h, [string]$from, [string]$to, [float]$angle = 90.0) {
    $rect = New-Object System.Drawing.RectangleF $x, $y, $w, $h
    New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, (C $from), (C $to), $angle
}

function Radial([float]$cx, [float]$cy, [float]$r, [string]$center, [string]$edge, [float]$dx = 0, [float]$dy = 0) {
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $p.AddEllipse($cx - $r, $cy - $r, $r * 2, $r * 2)
    $b = New-Object System.Drawing.Drawing2D.PathGradientBrush $p
    $b.CenterColor = C $center
    $b.SurroundColors = [System.Drawing.Color[]]@(C $edge)
    $b.CenterPoint = New-Object System.Drawing.PointF ($cx + $dx), ($cy + $dy)
    $g.FillEllipse($b, $cx - $r, $cy - $r, $r * 2, $r * 2)
    $b.Dispose(); $p.Dispose()
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

function Label([string]$s, [float]$size, [float]$x, [float]$y, [string]$align = "center") {
    Text $s "Arial" $size ([System.Drawing.FontStyle]::Bold) "#fbf4f2" $x $y $align
}

function Shadow([float]$cx, [float]$cy, [float]$r, [float]$drop) {
    for ($i = 4; $i -ge 1; $i--) {
        $b = New-Object System.Drawing.SolidBrush (C "#3a0004" 34)
        $rr = $r + $i * 1.6
        $g.FillEllipse($b, $cx - $rr, $cy - $rr + $drop, $rr * 2, $rr * 2)
        $b.Dispose()
    }
}

# A chrome knob seen from above: knurled skirt, brushed top, and a soft highlight.
function Chrome([float]$cx, [float]$cy, [float]$r, [bool]$dot = $false, [float]$dotAngle = -90) {
    Shadow $cx $cy $r 5
    $skirt = Linear ($cx - $r) ($cy - $r) ($r * 2) ($r * 2) "#f4f4f6" "#4b4b50" 60
    $g.FillEllipse($skirt, $cx - $r, $cy - $r, $r * 2, $r * 2)
    $dark = New-Object System.Drawing.Pen (C "#2c2c30" 150), 1.1
    $light = New-Object System.Drawing.Pen (C "#ffffff" 110), 1.1
    $count = [int]([Math]::Max(48, $r * 2.6))
    for ($i = 0; $i -lt $count; $i++) {
        $t = $i * [Math]::PI * 2 / $count
        $pen = if ($i % 2 -eq 0) { $dark } else { $light }
        $g.DrawLine($pen, $cx + [Math]::Cos($t) * ($r * 0.8), $cy + [Math]::Sin($t) * ($r * 0.8), $cx + [Math]::Cos($t) * ($r - 1), $cy + [Math]::Sin($t) * ($r - 1))
    }
    $face = $r * 0.74
    Radial $cx $cy $face "#ffffff" "#8e8e94" (-$face * 0.35) (-$face * 0.4)
    $ring = New-Object System.Drawing.Pen (C "#ffffff" 26), 1
    for ($k = 0.25; $k -lt 1.0; $k += 0.15) {
        $g.DrawEllipse($ring, $cx - $face * $k, $cy - $face * $k, $face * $k * 2, $face * $k * 2)
    }
    $rim = New-Object System.Drawing.Pen (C "#5a5a60"), 1.2
    $g.DrawEllipse($rim, $cx - $face, $cy - $face, $face * 2, $face * 2)
    $edge = New-Object System.Drawing.Pen (C "#2a2a2e"), 1.2
    $g.DrawEllipse($edge, $cx - $r, $cy - $r, $r * 2, $r * 2)
    if ($dot) {
        $t = $dotAngle * [Math]::PI / 180
        $dx = $cx + [Math]::Cos($t) * $face * 0.62
        $dy = $cy + [Math]::Sin($t) * $face * 0.62
        $b = New-Object System.Drawing.SolidBrush (C "#3a3a3e")
        $g.FillEllipse($b, $dx - 3, $dy - 3, 6, 6)
        $b.Dispose()
    }
    $skirt.Dispose(); $dark.Dispose(); $light.Dispose(); $ring.Dispose(); $rim.Dispose(); $edge.Dispose()
}

# A ring display: a black glass circle in a thin dark bezel.
function Ring([float]$cx, [float]$cy, [float]$r) {
    Shadow $cx $cy $r 3
    $bezel = Linear ($cx - $r) ($cy - $r) ($r * 2) ($r * 2) "#4a4a4f" "#121214" 70
    $g.FillEllipse($bezel, $cx - $r, $cy - $r, $r * 2, $r * 2)
    $ir = $r - 5
    Radial $cx $cy $ir "#1b1c21" "#040405" (-$ir * 0.25) (-$ir * 0.35)
    $gloss = New-Object System.Drawing.Pen (C "#ffffff" 22), 2
    $g.DrawArc($gloss, $cx - $ir + 8, $cy - $ir + 8, ($ir - 8) * 2, ($ir - 8) * 2, 200, 70)
    $bezel.Dispose(); $gloss.Dispose()
}

function Jack([float]$cx) {
    $nut = Linear ($cx - 20) 4 40 30 "#f0f0f2" "#6b6b70" 0
    $g.FillRectangle($nut, $cx - 20, 6, 40, 26)
    $pen = New-Object System.Drawing.Pen (C "#3e3e42" 170), 1
    for ($x = $cx - 17; $x -le $cx + 17; $x += 4) { $g.DrawLine($pen, $x, 7, $x, 31) }
    $cap = Linear ($cx - 13) 0 26 10 "#d9d9dc" "#8a8a8f" 90
    $g.FillRectangle($cap, $cx - 13, 0, 26, 8)
    $nut.Dispose(); $pen.Dispose(); $cap.Dispose()
}

function SmallButton([float]$x, [float]$y) {
    $path = RoundRect $x $y 56 26 4
    $fill = Linear $x $y 56 26 "#2a2a2e" "#060607"
    $g.FillPath($fill, $path)
    $pen = New-Object System.Drawing.Pen (C "#000000"), 1.5
    $g.DrawPath($pen, $path)
    $hi = New-Object System.Drawing.Pen (C "#ffffff" 40), 1
    $g.DrawLine($hi, $x + 5, $y + 2, $x + 51, $y + 2)
    $path.Dispose(); $fill.Dispose(); $pen.Dispose(); $hi.Dispose()
}

function Footswitch([float]$cx, [float]$cy) {
    Shadow $cx $cy 42 6
    $nut = Linear ($cx - 42) ($cy - 42) 84 84 "#d6d6d9" "#545459" 70
    $g.FillEllipse($nut, $cx - 42, $cy - 42, 84, 84)
    $edge = New-Object System.Drawing.Pen (C "#38383c"), 1.4
    $g.DrawEllipse($edge, $cx - 42, $cy - 42, 84, 84)
    Radial $cx $cy 31 "#ffffff" "#9b9ba0" -11 -12
    $g.DrawEllipse($edge, $cx - 31, $cy - 31, 62, 62)
    $ring = New-Object System.Drawing.Pen (C "#ffffff" 30), 1
    foreach ($k in 12, 18, 24) { $g.DrawEllipse($ring, $cx - $k, $cy - $k, $k * 2, $k * 2) }
    $nut.Dispose(); $edge.Dispose(); $ring.Dispose()
}

# Floor
$floor = Linear 0 0 $FaceW $FaceH "#17171a" "#0a0a0b"
$g.FillRectangle($floor, 0, 0, $FaceW, $FaceH)

# Jacks along the back edge, drawn first so the body overlaps their base
foreach ($x in 160, 250, 340, 430, 520) { Jack $x }
$dc = New-Object System.Drawing.SolidBrush (C "#0e0e10")
$g.FillRectangle($dc, 56, 12, 26, 20)
$g.FillRectangle($dc, 538, 14, 18, 18)

# Body
for ($i = 6; $i -ge 1; $i--) {
    $sh = RoundRect (12 - $i) (28 + $i) (576 + $i * 2) (790 + $i) (24 + $i)
    $b = New-Object System.Drawing.SolidBrush (C "#000000" 26)
    $g.FillPath($b, $sh); $b.Dispose(); $sh.Dispose()
}
$body = RoundRect 12 28 576 790 22
$bodyFill = Linear 12 28 576 790 "#dd2a2c" "#b3141b"
$g.FillPath($bodyFill, $body)
$g.SetClip($body)
$sheen = Linear 12 28 576 260 "#ffffff" "#ffffff" 90
$sheen.LinearColors = [System.Drawing.Color[]]@((C "#ffffff" 34), (C "#ffffff" 0))
$g.FillRectangle($sheen, 12, 28, 576, 260)
# The lower half, where the footswitches sit, is a separate slope
$lower = Linear 12 532 576 290 "#c51c22" "#a50f17"
$g.FillRectangle($lower, 12, 532, 576, 290)
$fold = New-Object System.Drawing.Pen (C "#7a080d" 200), 2
$g.DrawLine($fold, 12, 532, 588, 532)
$foldLight = New-Object System.Drawing.Pen (C "#ff8a8a" 70), 1.5
$g.DrawLine($foldLight, 12, 535, 588, 535)
$g.ResetClip()
$g.DrawPath((New-Object System.Drawing.Pen (C "#6e070c"), 2), $body)
$g.DrawLine((New-Object System.Drawing.Pen (C "#ffffff" 80), 1.2), 34, 30, 566, 30)

# Screen
$bezel = RoundRect 45 95 340 138 6
$g.FillPath((New-Object System.Drawing.SolidBrush (C "#08080a")), $bezel)
$g.DrawPath((New-Object System.Drawing.Pen (C "#5c0a0e"), 2), $bezel)
$screen = RoundRect 60 110 310 106 3
$g.FillPath((Linear 60 110 310 106 "#101116" "#040406"), $screen)
$g.DrawPath((New-Object System.Drawing.Pen (C "#000000"), 2), $screen)

# VALUE dial, MENU / WRITE / EXIT
Label "VALUE" 17 440 92
Chrome 440 160 44
Label "(ENTER: PUSH)" 11 440 236
Label "MENU" 16 535 92
SmallButton 507 108
Label "WRITE" 15 535 163
$tick = New-Object System.Drawing.Pen (C "#fbf4f2"), 1.6
$g.DrawLine($tick, 535, 139, 535, 152)
$g.DrawLine($tick, 535, 174, 535, 188)
SmallButton 507 190
Label "EXIT" 16 535 238

# Ring displays and the two LEVEL knobs between them
$accent = New-Object System.Drawing.Pen (C "#fbf4f2"), 2.4
$accent.StartCap = "Round"; $accent.EndCap = "Round"
$g.DrawBezier($accent, 338, 292, 362, 290, 374, 296, 394, 314)
$g.DrawBezier($accent, 272, 413, 250, 416, 238, 424, 220, 446)
$g.DrawBezier($accent, 224, 454, 214, 462, 204, 467, 192, 470)
$g.DrawBezier($accent, 384, 300, 400, 272, 430, 258, 456, 256)
Ring 145 355 98
Ring 470 355 98
$scale = New-Object System.Drawing.SolidBrush (C "#fbf4f2")
foreach ($a in -150, -90, -30) {
    $t = $a * [Math]::PI / 180
    $g.FillEllipse($scale, 305 + [Math]::Cos($t) * 40 - 2, 295 + [Math]::Sin($t) * 40 - 2, 4, 4)
}
foreach ($a in 150, 90, 30) {
    $t = $a * [Math]::PI / 180
    $g.FillEllipse($scale, 305 + [Math]::Cos($t) * 40 - 2, 410 + [Math]::Sin($t) * 40 - 2, 4, 4)
}
Chrome 305 295 31 $true -60
Chrome 305 410 31 $true -120
Label "LEVEL" 16 305 352
Label "LOOP" 19 125 466
Label "RHYTHM" 19 462 466

# Branding
$logo = RoundRect 46 489 22 22 3
$g.DrawPath((New-Object System.Drawing.Pen (C "#fbf4f2"), 2.2), $logo)
Text "B" "Arial Black" 15 ([System.Drawing.FontStyle]::Regular) "#fbf4f2" 57 501
Text "BOSS" "Arial Black" 27 ([System.Drawing.FontStyle]::Italic) "#fbf4f2" 70 501 "left"
Text "LOOP" "Arial" 13 ([System.Drawing.FontStyle]::Bold) "#fbf4f2" 280 507 "right"
Text "RHYTHM" "Arial" 13 ([System.Drawing.FontStyle]::Bold) "#fbf4f2" 284 494 "left"
Text "STATION" "Arial" 13 ([System.Drawing.FontStyle]::Bold) "#fbf4f2" 284 507 "left"
Text "RC-10R" "Arial Black" 30 ([System.Drawing.FontStyle]::Regular) "#fbf4f2" 572 500 "right"

# Footswitches
Label "LOOP" 20 115 614
Label "RHYTHM" 20 493 614
Footswitch 115 712
Footswitch 490 712
Label "UNDO/REDO:" 12 170 686 "left"
Label "HOLD" 12 170 702 "left"
Label "STOP:" 12 170 724 "left"
Label "PRESS TWICE" 12 170 740 "left"
Label "PTN 1/PTN 2:" 12 436 686 "right"
Label "HOLD" 12 436 702 "right"
Label "STOP:" 12 436 724 "right"
Label "PRESS TWICE" 12 436 740 "right"

$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "Saved $out ($W x $H)"
