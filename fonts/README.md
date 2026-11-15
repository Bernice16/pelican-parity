# Pinned fonts

Renders must be reproducible, so `resvg` never uses the fonts installed on the
machine (`--skip-system-fonts`) and loads only this folder (`--use-fonts-dir`).
Any `font-family` an SVG asks for is drawn with one of these faces, identically
on every OS.

- Source: DejaVu Fonts 2.37, https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37
  (`dejavu-fonts-ttf-2.37.zip`, sha256 `7576310b219e04159d35ff61dd4a4ec4cdba4f35c00e002a136f00e96a908b0a`)
- License: free (Bitstream Vera / Arev derived), see `LICENSE-DejaVu.txt`
- Faces: DejaVu Sans (regular, bold, oblique, bold oblique) and DejaVu Sans Mono.
- Generic families: sans-serif, serif, cursive, fantasy -> DejaVu Sans; monospace -> DejaVu Sans Mono.
- Unknown families (Arial, Comic Sans...) fall back to DejaVu Sans. Serif was left out on purpose:
  with it loaded, resvg 0.49 picked DejaVu Serif as the fallback for unknown sans fonts like Arial.
