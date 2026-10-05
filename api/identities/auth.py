import hashlib
import re
import time

import nacl.exceptions
import nacl.signing
from django.core.cache import cache
from django.db import IntegrityError
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.throttling import BaseThrottle

from .models import Identity

MAX_CLOCK_SKEW_SECONDS = 300
EMPTY_BODY_HASH = hashlib.sha256(b"").hexdigest()
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX128 = re.compile(r"[0-9a-f]{128}")
# Authentication runs before any view throttle, so without this a script
# could mint unlimited keypairs and insert an Identity row per request.
NEW_IDENTITIES_PER_IP_PER_HOUR = 10


class SignatureAuthentication(BaseAuthentication):
    """Passwordless auth: the client generates an ed25519 keypair on-device,
    keeps the private key there forever, and signs each authenticated request
    with it. The server only ever sees the public key — there is nothing to
    leak, phish, or reset.

    Required headers:
      X-Public-Key : hex-encoded 32-byte ed25519 public key
      X-Signature  : hex-encoded signature over "{method}|{path}|{timestamp}|{body_sha256_hex}"
      X-Timestamp  : unix seconds, must be within 5 minutes of server time

    The body hash matters: signing only method+path+timestamp would let
    anyone who ever observes one valid signed request replay those same
    headers with an entirely different body — a different title, password,
    coordinates, whatever — and have it verify successfully, since nothing
    would tie the signature to the actual content. Binding the hash in closes
    that.

    Requests with none of these headers are simply anonymous (request.user
    becomes Django's AnonymousUser) — most endpoints in this API allow that.
    """

    def authenticate(self, request):
        pub = request.headers.get("X-Public-Key")
        sig = request.headers.get("X-Signature")
        ts = request.headers.get("X-Timestamp")
        if not (pub and sig and ts):
            return None
        # Canonical lowercase hex only. bytes.fromhex() also accepts
        # uppercase and spaces, so one keypair could register many
        # identities, and a spaced key overflowed max_length -> 500.
        if not (HEX64.fullmatch(pub) and HEX128.fullmatch(sig)):
            raise AuthenticationFailed("Malformed key or signature.")

        try:
            if abs(time.time() - int(ts)) > MAX_CLOCK_SKEW_SECONDS:
                raise AuthenticationFailed("Stale timestamp.")
            # Multipart bodies (photo upload) aren't hashed — the browser
            # generates the exact multipart encoding internally and there's
            # no practical way to pre-compute those exact bytes from JS
            # without reimplementing multipart encoding by hand. A fixed
            # empty-body hash is used for those instead (matching the
            # client — see web/src/lib/api.ts), and CanManageDrop
            # independently re-verifies ownership server-side regardless, so
            # the residual risk is bounded to "wrong photo uploaded to a
            # drop you already own", never someone else's drop.
            if request.content_type.startswith("multipart/form-data"):
                body_hash = EMPTY_BODY_HASH
            else:
                body_hash = hashlib.sha256(request.body).hexdigest()
            verify_key = nacl.signing.VerifyKey(bytes.fromhex(pub))
            message = f"{request.method}|{request.path}|{ts}|{body_hash}".encode()
            verify_key.verify(message, bytes.fromhex(sig))
            # Reject a replayed signature on writes. A captured signed
            # POST/PATCH/DELETE was previously reusable for the whole clock-
            # skew window. GETs are exempt: identical reads in the same second
            # legitimately produce identical (deterministic ed25519)
            # signatures, and replaying a read gains nothing. Fails open if
            # Redis is down, like every other cache-backed check here.
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                try:
                    fresh = cache.add(f"sig_seen:{sig}", 1, timeout=2 * MAX_CLOCK_SKEW_SECONDS)
                except Exception:
                    fresh = True
                if not fresh:
                    raise AuthenticationFailed("Replayed request.")
        except (ValueError, nacl.exceptions.CryptoError):
            # CryptoError, not just BadSignatureError specifically — that's
            # the base of PyNaCl's whole exception hierarchy. A malformed
            # key (wrong length, wrong format) is plausibly a different
            # subtype than a merely-invalid signature, and there's no
            # PyNaCl available in this environment to verify that
            # empirically either way — catching the base class closes the
            # uncertainty regardless of which specific subtype it turns out
            # to be, at zero cost.
            raise AuthenticationFailed("Invalid signature.")

        identity, _ = self._get_or_create_identity(request, pub)
        return (identity, None)

    def _get_or_create_identity(self, request, pub):
        # get_or_create() itself has a well-documented race: two near-
        # simultaneous first-ever requests with the same brand-new key
        # (the same browser tab firing two requests right after
        # generating an identity) could both see no existing row and
        # both attempt an insert — one succeeds, the other hits
        # IntegrityError on the unique constraint. Django's own
        # recommended fix for exactly this: catch it and fall back to a
        # plain get(), since by the time the exception is caught, the
        # other request's insert has already committed. get_or_create()
        # wraps its own internal create() in a savepoint specifically to
        # make this safe — the failed insert only rolls back to that
        # savepoint, not any enclosing transaction.
        try:
            return Identity.objects.get(public_key=pub), False
        except Identity.DoesNotExist:
            pass
        ip_key = f"new_identity:{BaseThrottle().get_ident(request)}"
        created_this_hour = cache.get(ip_key, 0)
        if created_this_hour >= NEW_IDENTITIES_PER_IP_PER_HOUR:
            raise Throttled(detail="Too many new identities from this network.")
        cache.set(ip_key, created_this_hour + 1, 3600)
        try:
            return Identity.objects.get_or_create(public_key=pub)
        except IntegrityError:
            return Identity.objects.get(public_key=pub), False
