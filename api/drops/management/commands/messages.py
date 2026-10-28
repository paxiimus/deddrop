from django.core.cache import cache
from django.core.management.base import BaseCommand

from deaddrop.middleware import CACHE_KEY
from drops.models import SiteStatus


class Command(BaseCommand):
    help = "Turn drop messages on or off site-wide, or show the current state."

    def add_arguments(self, parser):
        parser.add_argument("state", nargs="?", choices=["on", "off", "status"], default="status")

    def handle(self, state, **options):
        s = SiteStatus.get()
        if state != "status":
            s.messages_enabled = state == "on"
            s.save()
            cache.delete(CACHE_KEY)
        self.stdout.write(f"Messages are {'on' if s.messages_enabled else 'off'}.")
