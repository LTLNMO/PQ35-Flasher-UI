"""The user's own module pictures on the Connect cards.

Images live in Documents/PQ35 Flasher/Module images, named by workshop address: 03.png for
ABS Brakes, 17.jpg for Instruments ("Module list.txt" in that folder lists every address).
PNG, JPG, WebP or SVG. A card without an image shows no picture. Demo mode uses the same
folder (the pictures aren't demo data).
"""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImageReader, QPainter, QPixmap

EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")
# The app's module families by name (their address can be missing without a gateway).
FAMILY_ADDRESS = {"engine": 0x01, "transmission": 0x02, "haldex-gen4": 0x22, "pq-eps": 0x44,
                  "pq-bcm": 0x09}

_cache = {}


def folder() -> Path:
    from .storage import user_dir
    base = user_dir()
    return (base.parent if base.name == "Demo" else base) / "Module images"


def _image(address):
    for ext in EXTENSIONS:
        path = folder() / f"{address:02X}{ext}"
        if path.is_file():
            return path
    return None


def pixmap(family, address, size, ratio=1.0):
    """The picture for a module (scaled to fit size x size, centred), or None."""
    family = family or {}
    address = FAMILY_ADDRESS.get(family.get("name"), family.get("address", address))
    if not isinstance(address, int):
        return None
    path = _image(address)
    if path is None:
        return None
    stat = path.stat()
    key = (str(path), stat.st_mtime, stat.st_size, size, ratio)
    if key not in _cache:
        side = int(size * ratio)
        canvas = QPixmap(side, side)
        canvas.fill(Qt.GlobalColor.transparent)
        if path.suffix.lower() == ".svg":
            from PySide6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(str(path))
            painter = QPainter(canvas)
            renderer.render(painter, QRectF(0, 0, side, side))
            painter.end()
        else:
            image = QImageReader(str(path)).read()
            if image.isNull():
                return None
            scaled = image.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            painter = QPainter(canvas)
            painter.drawImage((side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled)
            painter.end()
        canvas.setDevicePixelRatio(ratio)
        _cache[key] = canvas
    return _cache[key]
