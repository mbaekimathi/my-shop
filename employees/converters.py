from django.urls import register_converter

from .access import ROLE_URL_SEGMENTS


class RoleSegmentConverter:
    regex = "|".join(
        sorted(ROLE_URL_SEGMENTS.values(), key=len, reverse=True)
    )

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(RoleSegmentConverter, "role_segment")
