from django.db import migrations


def rename_whatsapp_permission_slugs(apps, schema_editor):
    Permission = apps.get_model("employees", "EmployeeModulePermission")
    Permission.objects.filter(module_slug="communications").update(module_slug="whatsapp")
    Permission.objects.filter(
        module_slug="settings", submodule_slug="communications"
    ).update(submodule_slug="whatsapp")


def revert_whatsapp_permission_slugs(apps, schema_editor):
    Permission = apps.get_model("employees", "EmployeeModulePermission")
    Permission.objects.filter(module_slug="whatsapp").update(module_slug="communications")
    Permission.objects.filter(
        module_slug="settings", submodule_slug="whatsapp"
    ).update(submodule_slug="communications")


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0007_employeemodulepermission"),
    ]

    operations = [
        migrations.RunPython(
            rename_whatsapp_permission_slugs,
            revert_whatsapp_permission_slugs,
        ),
    ]
