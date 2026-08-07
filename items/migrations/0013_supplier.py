from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0012_stock_line_supplier_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="Supplier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=160)),
                ("phone_country_code", models.CharField(db_index=True, max_length=8)),
                ("phone_country_iso", models.CharField(blank=True, default="KE", max_length=2)),
                ("phone_number", models.CharField(db_index=True, max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name", "phone_number"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("phone_country_code", "phone_number"),
                        name="uniq_supplier_phone",
                    )
                ],
            },
        ),
    ]
