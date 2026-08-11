"""Register bundled Manrope fonts for ReportLab PDF output."""

from functools import lru_cache
from pathlib import Path

from django.conf import settings

MANROPE_PDF = "Manrope"
MANROPE_PDF_BOLD = "Manrope-Bold"


@lru_cache(maxsize=1)
def register_manrope_pdf_fonts() -> tuple[str, str]:
    """Register Manrope TTF files once per process; return (regular, bold) names."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fonts_dir = Path(settings.BASE_DIR) / "static" / "fonts"
    regular_path = fonts_dir / "Manrope-Regular.ttf"
    bold_path = fonts_dir / "Manrope-Bold.ttf"

    if not regular_path.is_file():
        raise FileNotFoundError(f"Missing PDF font: {regular_path}")
    if not bold_path.is_file():
        raise FileNotFoundError(f"Missing PDF font: {bold_path}")

    registered = set(pdfmetrics.getRegisteredFontNames())
    if MANROPE_PDF not in registered:
        pdfmetrics.registerFont(TTFont(MANROPE_PDF, str(regular_path)))
    if MANROPE_PDF_BOLD not in registered:
        pdfmetrics.registerFont(TTFont(MANROPE_PDF_BOLD, str(bold_path)))

    return MANROPE_PDF, MANROPE_PDF_BOLD
