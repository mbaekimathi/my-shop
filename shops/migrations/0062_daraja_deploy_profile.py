from django.db import migrations, models


def seed_deploy_profiles(apps, schema_editor):
    CompanyDarajaSettings = apps.get_model("shops", "CompanyDarajaSettings")
    rows = list(CompanyDarajaSettings.objects.order_by("pk"))
    if not rows:
        CompanyDarajaSettings.objects.create(deploy_profile="local")
        CompanyDarajaSettings.objects.create(deploy_profile="hosted")
        return
    first = rows[0]
    if not getattr(first, "deploy_profile", None) or first.deploy_profile == "local":
        CompanyDarajaSettings.objects.filter(pk=first.pk).update(deploy_profile="local")
    for extra in rows[1:]:
        extra.delete()
    if not CompanyDarajaSettings.objects.filter(deploy_profile="hosted").exists():
        CompanyDarajaSettings.objects.create(deploy_profile="hosted")


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0061_stk_provider_nexus_generic"),
    ]

    operations = [
        migrations.AddField(
            model_name="companydarajasettings",
            name="deploy_profile",
            field=models.CharField(
                choices=[
                    ("local", "Local development"),
                    ("hosted", "Hosted production"),
                ],
                default="local",
                max_length=16,
            ),
        ),
        migrations.RunPython(seed_deploy_profiles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="companydarajasettings",
            name="deploy_profile",
            field=models.CharField(
                choices=[
                    ("local", "Local development"),
                    ("hosted", "Hosted production"),
                ],
                default="local",
                max_length=16,
                unique=True,
            ),
        ),
    ]
