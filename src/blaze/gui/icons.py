"""Small line icons drawn from inline SVG, tinted to any color."""

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 24x24 stroke paths, in the spirit of Lucide/Feather.
PATHS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
    "camera": '<path d="M3 8a2 2 0 0 1 2-2h2.5l1.5-2h6l1.5 2H19a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
              '<circle cx="12" cy="13" r="3.5"/>',
    "memory": '<path d="M12 4a3 3 0 0 0-3-1 3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 1 5 3 3 0 0 0 4 3 3 3 0 0 0 3-1z"/>'
              '<path d="M12 4a3 3 0 0 1 3-1 3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-1 5 3 3 0 0 1-4 3 3 3 0 0 1-3-1z"/>'
              '<path d="M12 4v15"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1'
                'a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0'
                '-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1'
                'a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9'
                'a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1'
                ' 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    "mic-off": '<path d="M3 3l18 18"/><path d="M9 9v2a3 3 0 0 0 5.1 2.1"/><path d="M15 9.3V6a3 3 0 0 0-5.9-.7"/>'
               '<path d="M17 16.9A7 7 0 0 1 5 11"/><path d="M19 11a7 7 0 0 1-.2 1.7"/><path d="M12 18v3"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "power": '<path d="M12 3v9"/><path d="M6.4 6.4a8 8 0 1 0 11.2 0"/>',
    "send": '<path d="M5 12h13"/><path d="M12 5l7 7-7 7"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/>'
             '<path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "face": '<circle cx="12" cy="12" r="9"/><path d="M9 10h.01"/><path d="M15 10h.01"/>'
            '<path d="M8.5 14.5a4.5 4.5 0 0 0 7 0"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
    "volume": '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/>',
}


@cache
def pixmap(name: str, color: str, size: int = 18) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ratio = 2  # render crisp on HiDPI
    pix = QPixmap(size * ratio, size * ratio)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    renderer.render(p, QRectF(0, 0, size * ratio, size * ratio))
    p.end()
    pix.setDevicePixelRatio(ratio)
    return pix


def icon(name: str, color: str, selected_color: str | None = None, size: int = 18) -> QIcon:
    ic = QIcon(pixmap(name, color, size))
    if selected_color:
        ic.addPixmap(pixmap(name, selected_color, size), QIcon.Mode.Selected)
        ic.addPixmap(pixmap(name, selected_color, size), QIcon.Mode.Active)
    return ic
