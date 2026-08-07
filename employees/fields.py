from django.db import models


class MysqlEnumField(models.CharField):
    """
    CharField that stores as MySQL ENUM so DB tools (phpMyAdmin, Workbench)
    show a dropdown. Falls back to VARCHAR on SQLite and other backends.
    """

    def __init__(self, *args, enum_values=None, **kwargs):
        self.enum_values = list(enum_values or [])
        if self.enum_values and "max_length" not in kwargs:
            kwargs["max_length"] = max(len(value) for value in self.enum_values)
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        if connection.vendor == "mysql" and self.enum_values:
            members = ", ".join("'%s'" % value.replace("'", "''") for value in self.enum_values)
            return f"enum({members})"
        return super().db_type(connection)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["enum_values"] = self.enum_values
        return name, path, args, kwargs

    def clone(self):
        name, path, args, kwargs = self.deconstruct()
        return self.__class__(*args, **kwargs)
