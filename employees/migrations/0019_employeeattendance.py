import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0018_employeeprofile_company_accountant_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeAttendance",
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
                ("work_date", models.DateField(db_index=True)),
                ("clock_in", models.DateTimeField()),
                ("clock_out", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attendance_entries",
                        to="employees.employeeprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["-clock_in"],
                "indexes": [
                    models.Index(
                        fields=["employee", "work_date"],
                        name="emp_att_emp_date_idx",
                    ),
                ],
            },
        ),
    ]
