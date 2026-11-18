import re
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from items.models import Item

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


class Command(BaseCommand):
    help = (
        "Link item images dropped into a folder (e.g. via cPanel file manager) "
        "to items. Filename stem = item id (e.g. 123.jpg) or item name "
        "(e.g. 'Coca Cola 500ml.png', matched ignoring case/spaces/dashes). "
        "Files already inside MEDIA_ROOT are linked in place; files elsewhere "
        "are stored under items/images/ and the source is removed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "folder",
            nargs="?",
            default="media/item-image-import",
            help="Folder to scan (relative to the project root or absolute).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show matches without changing anything.",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Keep source files that live outside MEDIA_ROOT after import.",
        )

    def _match_item(self, stem, by_id, by_name):
        if stem.isdigit():
            return by_id.get(int(stem)), None
        lead = re.match(r"^(\d+)\D", stem)
        if lead and int(lead.group(1)) in by_id:
            return by_id[int(lead.group(1))], None
        candidates = by_name.get(_slug(stem), [])
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            return None, f"ambiguous: matches {len(candidates)} items"
        return None, "no item with that id or name"

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.is_absolute():
            folder = Path(settings.BASE_DIR) / folder
        folder = folder.resolve()
        if not folder.is_dir():
            raise CommandError(f"Not a folder: {folder}")

        media_root = Path(settings.MEDIA_ROOT).resolve()
        files = sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )
        if not files:
            self.stdout.write(f"No image files found in {folder}")
            return

        items = list(Item.objects.filter(is_suspended=False))
        by_id = {item.pk: item for item in items}
        by_name = {}
        for item in items:
            by_name.setdefault(_slug(item.name), []).append(item)

        dry_run = options["dry_run"]
        keep = options["keep"]
        linked = skipped = 0
        for path in files:
            item, err = self._match_item(path.stem, by_id, by_name)
            if item is None:
                self.stdout.write(
                    self.style.WARNING(f"skip {path.name}: {err}")
                )
                skipped += 1
                continue

            try:
                rel = path.relative_to(media_root).as_posix()
            except ValueError:
                rel = None

            if dry_run:
                target = rel or f"items/images/…{path.suffix.lower()}"
                self.stdout.write(
                    f"would link {path.name} -> item {item.pk} "
                    f"{item.name!r} as {target}"
                )
                linked += 1
                continue

            old_name = (item.image.name if item.image else "") or ""
            if rel is not None:
                item.image = rel
                item.save(update_fields=["image", "updated_at"])
            else:
                item.image.save(
                    path.name, ContentFile(path.read_bytes()), save=True
                )
                if not keep:
                    path.unlink()
            if old_name and old_name != item.image.name:
                try:
                    item.image.storage.delete(old_name)
                except OSError:
                    pass
            self.stdout.write(
                f"linked {path.name} -> item {item.pk} {item.name!r}"
            )
            linked += 1

        summary = f"{'dry-run: ' if dry_run else ''}linked={linked} skipped={skipped}"
        self.stdout.write(
            self.style.SUCCESS(summary) if linked else self.style.WARNING(summary)
        )
