from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("communications", "0004_outbound_delivery_status"),
        ("employees", "0008_rename_communications_to_whatsapp_perms"),
        ("shops", "0025_company_communications_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhatsAppGroup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("invite_link", models.CharField(blank=True, default="", max_length=500)),
                (
                    "source",
                    models.CharField(
                        choices=[("created", "Created"), ("joined", "Joined")],
                        db_index=True,
                        default="created",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="whatsapp_groups_created",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "members",
                    models.ManyToManyField(
                        blank=True,
                        related_name="whatsapp_groups",
                        to="shops.client",
                    ),
                ),
            ],
            options={
                "ordering": ["name", "id"],
            },
        ),
    ]
