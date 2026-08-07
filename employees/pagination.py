from django.shortcuts import redirect
from django.urls import reverse


def page_url(view_name, page_number, *, url_kwargs=None, fragment=None):
    """Build a URL with the page number after the current path (page 1 omits it)."""
    url_kwargs = dict(url_kwargs or {})
    page_number = int(page_number or 1)
    if page_number > 1:
        url = reverse(
            f"employees:{view_name}_page",
            kwargs={**url_kwargs, "page": page_number},
        )
    else:
        url = reverse(f"employees:{view_name}", kwargs=url_kwargs)
    if fragment:
        url = f"{url}#{fragment}"
    return url


def pagination_links(page_obj, view_name, *, url_kwargs=None, fragment=None):
    """Previous and next URLs for a paginated page object."""
    url_kwargs = url_kwargs or {}
    links = {"previous_url": None, "next_url": None}
    if page_obj.has_previous():
        links["previous_url"] = page_url(
            view_name,
            page_obj.previous_page_number,
            url_kwargs=url_kwargs,
            fragment=fragment,
        )
    if page_obj.has_next():
        links["next_url"] = page_url(
            view_name,
            page_obj.next_page_number,
            url_kwargs=url_kwargs,
            fragment=fragment,
        )
    return links


def redirect_query_page(request, view_name, path_page, *, url_kwargs=None, fragment=None):
    """Redirect legacy ?page=N query strings to path-based pagination."""
    if path_page is not None or not request.GET.get("page"):
        return None
    try:
        page_number = int(request.GET.get("page"))
    except (TypeError, ValueError):
        return None
    if page_number < 1:
        return None
    return redirect(
        page_url(view_name, page_number, url_kwargs=url_kwargs, fragment=fragment)
    )
