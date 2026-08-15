"""Build the deterministic 256 px HACS brand icon."""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
image = Image.new("RGBA", (SIZE, SIZE), "#111111")
draw = ImageDraw.Draw(image)
draw.rounded_rectangle(
    (42, 48, 214, 196), radius=14, fill="#f5f5f0", outline="white", width=8
)
draw.ellipse((59, 69, 89, 99), fill="#111111")
for start, end in (
    ((101, 83), (179, 83)),
    ((74, 119), (179, 119)),
    ((74, 151), (140, 151)),
):
    draw.line((start, end), fill="#111111", width=12)
    radius = 6
    for x, y in (start, end):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#111111")
draw.line(((98, 220), (158, 220)), fill="white", width=12)
for x in (98, 158):
    draw.ellipse((x - 6, 214, x + 6, 226), fill="white")

output = (
    Path(__file__).parents[1]
    / "custom_components"
    / "coolajz_epaper_display_hub"
    / "brand"
    / "icon.png"
)
image.save(output, format="PNG", optimize=True)
