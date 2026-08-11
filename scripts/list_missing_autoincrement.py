import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")

import django

django.setup()

from django.db import connection

with connection.cursor() as c:
    c.execute(
        """
        SELECT TABLE_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND COLUMN_NAME = 'id'
          AND EXTRA NOT LIKE '%auto_increment%'
        ORDER BY TABLE_NAME
        """
    )
    for (table,) in c.fetchall():
        print(table)
