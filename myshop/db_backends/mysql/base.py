"""MySQL/MariaDB backend with relaxed minimum version for XAMPP (MariaDB 10.4)."""

from django.db.backends.mysql.base import DatabaseWrapper as MySQLDatabaseWrapper
from django.db.backends.mysql.features import DatabaseFeatures as MySQLDatabaseFeatures
from django.utils.functional import cached_property


class DatabaseFeatures(MySQLDatabaseFeatures):
    """
    Django 5 defaults to MariaDB 10.11+, but XAMPP/WAMP often ships 10.4.x.
    This backend allows MariaDB 10.4+, which is sufficient for this application.
    Upgrade to MariaDB 10.11+ when possible for full Django 5 feature support.
    """

    @cached_property
    def minimum_database_version(self):
        if self.connection.mysql_is_mariadb:
            return (10, 4)
        return (8, 0, 11)

    @cached_property
    def can_return_columns_from_insert(self):
        # MariaDB RETURNING requires 10.5+; XAMPP 10.4 lacks it
        if self.connection.mysql_is_mariadb:
            version = self.connection.mysql_version
            return version >= (10, 5)
        return super().can_return_columns_from_insert

    can_return_rows_from_bulk_insert = property(
        lambda self: self.can_return_columns_from_insert
    )


class DatabaseWrapper(MySQLDatabaseWrapper):
    features_class = DatabaseFeatures
