"""Burn the narration captions into a band under the recorded video; also write an .srt file."""

import json
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

src_dir, out_mp4 = Path(sys.argv[1]), Path(sys.argv[2])
data = json.loads((src_dir / "captions.json").read_text())
video = data["video"]
caps = data["captions"]

BAND = 132
FONT_PATHS = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def font(size):
    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT, SMALL = font(27), font(17)


def wrap(draw, text, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=FONT) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


reader = imageio_ffmpeg.read_frames(video)
meta = next(reader)
w, h = meta["size"]
fps = meta["fps"] or 25
bands: dict[int, np.ndarray] = {}


def band_for(idx):
    if idx in bands:
        return bands[idx]
    img = Image.new("RGB", (w, BAND), (22, 22, 22))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 3], fill=(15, 98, 254))
    text = caps[idx][1] if idx >= 0 else ""
    if text:
        d.text(
            (28, 12), f"NARRATION  {idx + 1}/{sum(1 for _, t in caps if t)}", font=SMALL, fill=(141, 141, 141)
        )
        lines = wrap(d, text, w - 56)[:3]
        y = 38 if len(lines) <= 2 else 34
        for line in lines:
            d.text((28, y), line, font=FONT, fill=(244, 244, 244))
            y += 33
    bands[idx] = np.asarray(img)
    return bands[idx]


def caption_index(t):
    idx = -1
    for i, (start, _) in enumerate(caps):
        if t >= start:
            idx = i
    return idx


writer = imageio_ffmpeg.write_frames(
    str(out_mp4),
    (w, h + BAND),
    fps=fps,
    codec="libx264",
    pix_fmt_out="yuv420p",
    quality=8,
    macro_block_size=16,
    output_params=["-movflags", "+faststart"],
)
writer.send(None)
n = 0
for raw in reader:
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)
    t = n / fps
    writer.send(np.ascontiguousarray(np.vstack([frame, band_for(caption_index(t))])))
    n += 1
writer.close()


def ts(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


srt, k = [], 0
for i, (start, text) in enumerate(caps):
    if not text:
        continue
    end = caps[i + 1][0] if i + 1 < len(caps) else n / fps
    k += 1
    srt.append(f"{k}\n{ts(start)} --> {ts(end)}\n{text}\n")
out_mp4.with_suffix(".srt").write_text("\n".join(srt))
print(f"frames={n} fps={fps} duration={n / fps:.1f}s size={w}x{h + BAND} -> {out_mp4}")
