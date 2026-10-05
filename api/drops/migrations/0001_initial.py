import uuid

import django.db.models.deletion
from django.db import migrations, models

import drops.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("identities", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Drop",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("owner_secret_hash", models.CharField(blank=True, default="", max_length=128)),
                ("title", models.CharField(blank=True, default="", max_length=200)),
                ("description", models.TextField(blank=True, default="")),
                ("latitude", models.FloatField()),
                ("longitude", models.FloatField()),
                ("password_hash", models.CharField(blank=True, default="", max_length=128)),
                ("is_anonymous", models.BooleanField(default=True)),
                ("is_public", models.BooleanField(default=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("expired", "Expired"), ("collected", "Collected")],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "creator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="drops",
                        to="identities.identity",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["latitude", "longitude"], name="drops_latlng_idx"),
                    models.Index(fields=["status", "-created_at"], name="drops_status_created_idx"),
                    models.Index(fields=["expires_at"], name="drops_expires_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="DropPhoto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.ImageField(upload_to=drops.models.photo_upload_to)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "drop",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="photos", to="drops.drop"),
                ),
            ],
            options={
                "ordering": ["order"],
            },
        ),
        migrations.CreateModel(
            name="DropVideo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("video", models.FileField(upload_to=drops.models.video_upload_to)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "drop",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="videos", to="drops.drop"),
                ),
            ],
            options={
                "ordering": ["order"],
            },
        ),
        migrations.CreateModel(
            name="SiteStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cooling_until", models.DateTimeField(blank=True, null=True)),
                ("cooling_message", models.CharField(blank=True, default="", max_length=200)),
            ],
            options={
                "verbose_name_plural": "Site status",
            },
        ),
        migrations.CreateModel(
            name="Message",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("ciphertext", models.TextField(blank=True, default="")),
                ("content", models.CharField(blank=True, default="", max_length=200)),
                ("is_system", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "drop",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="drops.drop"),
                ),
                (
                    "sender",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="identities.identity",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.CreateModel(
            name="Report",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "reason",
                    models.CharField(
                        choices=[
                            ("illegal", "Illegal content"),
                            ("spam", "Spam / abuse"),
                            ("unsafe", "Unsafe location"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("details", models.CharField(blank=True, default="", max_length=500)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("reviewed", "Reviewed"), ("actioned", "Actioned")],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "drop",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reports",
                        to="drops.drop",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
