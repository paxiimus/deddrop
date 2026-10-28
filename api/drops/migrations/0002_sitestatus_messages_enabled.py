from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("drops", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitestatus",
            name="messages_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Untick to turn off messages on every drop. Takes effect within a few seconds.",
            ),
        ),
    ]
