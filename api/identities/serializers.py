from rest_framework import serializers

from .models import Identity


class IdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Identity
        fields = ["id", "display_name", "created_at"]
        read_only_fields = ["id", "created_at"]
