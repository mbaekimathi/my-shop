"""Company brand themes used across MY-SHOP (independent of light/dark mode)."""

DEFAULT_COMPANY_THEME = "harbor"

COMPANY_THEMES = (
    {
        "slug": "harbor",
        "label": "Harbor",
        "summary": "Teal and amber — calm, retail-ready default.",
        "swatches": ("#0f3a3f", "#0f8f82", "#f2b045"),
    },
    {
        "slug": "navy",
        "label": "Navy Bridge",
        "summary": "Deep navy with a clear corporate blue accent.",
        "swatches": ("#0b1f3a", "#0e7490", "#2563eb"),
    },
    {
        "slug": "forest",
        "label": "Forest Ledger",
        "summary": "Trustworthy greens with a soft gold highlight.",
        "swatches": ("#14352a", "#1f7a5c", "#c4a35a"),
    },
    {
        "slug": "graphite",
        "label": "Graphite Sky",
        "summary": "Modern charcoal surfaces with a sharp sky accent.",
        "swatches": ("#111827", "#0f766e", "#0284c7"),
    },
    {
        "slug": "ember",
        "label": "Ember Trade",
        "summary": "Warm espresso tones with a confident copper accent.",
        "swatches": ("#2a1f18", "#0f766e", "#c26d3a"),
    },
    {
        "slug": "arctic",
        "label": "Arctic Line",
        "summary": "Cool slate and cyan for a crisp professional look.",
        "swatches": ("#0f172a", "#0e7490", "#0891b2"),
    },
)

COMPANY_THEME_BY_SLUG = {theme["slug"]: theme for theme in COMPANY_THEMES}
COMPANY_THEME_CHOICES = tuple(
    (theme["slug"], theme["label"]) for theme in COMPANY_THEMES
)
COMPANY_THEME_SLUGS = frozenset(COMPANY_THEME_BY_SLUG)


def normalize_company_theme(value: str | None) -> str:
    slug = (value or "").strip().lower()
    if slug in COMPANY_THEME_SLUGS:
        return slug
    return DEFAULT_COMPANY_THEME


def company_theme_options(*, selected: str | None = None) -> list[dict]:
    active = normalize_company_theme(selected)
    rows = []
    for theme in COMPANY_THEMES:
        row = dict(theme)
        row["selected"] = theme["slug"] == active
        rows.append(row)
    return rows


COMPANY_DEFAULT_THEME_SLUG = ""


def normalize_shop_theme(value: str | None) -> str:
    """Return a concrete theme slug, or blank to mean company default."""
    slug = (value or "").strip().lower()
    if slug in ("", "company", "default", "company-default"):
        return COMPANY_DEFAULT_THEME_SLUG
    if slug in COMPANY_THEME_SLUGS:
        return slug
    return COMPANY_DEFAULT_THEME_SLUG


def shop_theme_options(
    *,
    selected: str | None = None,
    company_theme: str | None = None,
) -> list[dict]:
    """Theme cards for a shop, including a company-default option."""
    company = normalize_company_theme(company_theme)
    company_meta = COMPANY_THEME_BY_SLUG.get(company) or COMPANY_THEMES[0]
    shop_slug = normalize_shop_theme(selected)
    using_company = shop_slug == COMPANY_DEFAULT_THEME_SLUG

    rows = [
        {
            "slug": "company",
            "value": COMPANY_DEFAULT_THEME_SLUG,
            "label": "Company default",
            "summary": f"Follow the company theme ({company_meta['label']}).",
            "swatches": company_meta["swatches"],
            "selected": using_company,
            "effective_slug": company,
            "is_company_default": True,
        }
    ]
    for theme in COMPANY_THEMES:
        row = dict(theme)
        row["value"] = theme["slug"]
        row["selected"] = (not using_company) and theme["slug"] == shop_slug
        row["effective_slug"] = theme["slug"]
        row["is_company_default"] = False
        rows.append(row)
    return rows
