"""Render a synthetic TV remote JPEG for locate tests and docs (no real photo needed)."""

import sys
from PIL import Image, ImageDraw, ImageFont

ROWS = [["POWER", "MUTE"], ["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"],
        ["EXIT", "0", "OK"], ["◀", "▲", "▼", "▶"]]


def render(path, finger=None):
    image = Image.new("RGB", (720, 1280), "#2b2f33")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    draw.rounded_rectangle((90, 40, 630, 1240), 60, fill="#15181b", outline="#555", width=4)
    centers = {}
    for r, row in enumerate(ROWS):
        width = 460 // len(row)
        for c, label in enumerate(row):
            x0 = 130 + c * (460 // len(row)) + 8
            y0 = 90 + r * 190
            box = (x0, y0, x0 + width - 16, y0 + 140)
            draw.rounded_rectangle(box, 30, fill="#d9dde1")
            w = draw.textlength(label, font=font)
            draw.text(((box[0] + box[2]) / 2 - w / 2, y0 + 52), label, fill="#111", font=font)
            centers[(r + 1, c + 1)] = ((box[0] + box[2]) // 2, y0 + 70)
    if finger:
        cx, cy = centers[finger]
        draw.ellipse((cx - 38, cy + 10, cx + 38, cy + 150), fill="#e0ac8a", outline="#b07a5a", width=3)
        draw.rounded_rectangle((cx - 38, cy + 90, cx + 38, cy + 300), 30, fill="#e0ac8a")
    image.save(path, "JPEG", quality=88)


if __name__ == "__main__":
    render(sys.argv[1], tuple(map(int, sys.argv[2].split(","))) if len(sys.argv) > 2 else None)
