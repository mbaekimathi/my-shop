"""One-shot repair: rename legacy item images from name_ext to name.ext."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
django.setup()

from django.conf import settings
from items.models import Item

LEGACY_RE = re.compile(r"^(?P<stem>.+)_(?P<ext>jpe?g|png|webp|gif)$", re.IGNORECASE)


def repair_name(name: str) -> str | None:
    name = (name or "").replace("\\", "/")
    base = name.rsplit("/", 1)[-1]
    match = LEGACY_RE.match(base)
    if not match:
        return None
    prefix = name[: -len(base)] if "/" in name else ""
    return f"{prefix}{match.group('stem')}.{match.group('ext').lower()}"


def main() -> None:
    media_root = Path(settings.MEDIA_ROOT)
    updated = 0
    renamed = 0
    for item in Item.objects.exclude(image="").exclude(image=None).iterator():
        old_name = item.image.name
        new_name = repair_name(old_name)
        if not new_name or new_name == old_name:
            continue
        old_path = media_root / old_name
        new_path = media_root / new_name
        if old_path.is_file() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)
            renamed += 1
        item.image.name = new_name
        item.save(update_fields=["image"])
        updated += 1
        print(f"{item.pk}: {old_name} -> {new_name}")
    print(f"updated={updated} renamed={renamed}")


if __name__ == "__main__":
    main()
