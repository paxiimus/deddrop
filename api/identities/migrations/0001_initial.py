import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Identity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("public_key", models.CharField(max_length=64, unique=True)),
                ("display_name", models.CharField(blank=True, default="", max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
