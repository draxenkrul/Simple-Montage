"""Generate the app icon (icon.png + multi-size icon.ico). Requires Pillow.

Run once; the generated files are committed:  python packaging/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # draw large, downscale for smooth edges
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill="#2b2d31")
# timeline blocks: normal / faster / slower
y0, y1 = 400, 640
d.rounded_rectangle([130, y0, 380, y1], radius=40, fill="#3d7bd9")
d.rounded_rectangle([420, y0, 600, y1], radius=40, fill="#d98a3d")
d.rounded_rectangle([640, y0, 894, y1], radius=40, fill="#3dbf8a")
# playhead
d.rectangle([496, 230, 528, 800], fill="#ff4d4d")
d.polygon([(432, 200), (592, 200), (512, 300)], fill="#ff4d4d")

here = Path(__file__).resolve().parent
res = here.parent / "src" / "simple_montage" / "resources"
res.mkdir(parents=True, exist_ok=True)
img.resize((256, 256), Image.LANCZOS).save(res / "icon.png")
img.resize((256, 256), Image.LANCZOS).save(
    here / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
)
print("wrote", res / "icon.png", "and", here / "icon.ico")
