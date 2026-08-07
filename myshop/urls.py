from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

employee_patterns = (
    [
        path("employees/", include("employees.urls")),
        path("", include("employees.portal_urls")),
    ],
    "employees",
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("", include(employee_patterns)),
    path("pos/", include("pos.urls")),
]

if settings.DEBUG or settings.SERVE_MEDIA_IN_PRODUCTION:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
