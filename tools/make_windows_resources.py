"""Generate Windows resources for the Open Video Archiver portable executable."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

from ova_constants import APP_NAME, APP_VERSION, COPYRIGHT


def version_tuple() -> tuple[int, int, int, int]:
    parts = [int(part) for part in APP_VERSION.split(".") if part.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def write_icon(path: Path) -> None:
    size = 256
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1769aa"))
    painter.drawRoundedRect(QRectF(12, 12, 232, 232), 42, 42)

    painter.setBrush(QColor("white"))
    triangle = QPolygonF(
        [
            QPointF(103, 77),
            QPointF(103, 179),
            QPointF(183, 128),
        ]
    )
    painter.drawPolygon(triangle)
    painter.end()

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt could not encode the Open Video Archiver icon as PNG.")
    png = bytes(data)

    # ICO header + one PNG-compressed 256x256 image entry.
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entry + png)


def write_version_file(path: Path) -> None:
    version = version_tuple()
    dotted = ".".join(str(value) for value in version[:3])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version},
    prodvers={version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'John G. Haas'),
          StringStruct('FileDescription', '{APP_NAME}'),
          StringStruct('FileVersion', '{dotted}'),
          StringStruct('InternalName', 'OpenVideoArchiver'),
          StringStruct('LegalCopyright', '{COPYRIGHT}'),
          StringStruct('OriginalFilename', 'OpenVideoArchiver.exe'),
          StringStruct('ProductName', '{APP_NAME}'),
          StringStruct('ProductVersion', '{dotted}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--icon", required=True, type=Path)
    parser.add_argument("--version-file", required=True, type=Path)
    args = parser.parse_args()
    write_icon(args.icon)
    write_version_file(args.version_file)


if __name__ == "__main__":
    main()
