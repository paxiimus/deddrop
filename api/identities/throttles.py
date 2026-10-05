from drops.throttles import _IPRateThrottle

# _IPRateThrottle is generic IP-keyed throttling logic that happens to live
# in drops/throttles.py — imported here rather than duplicated, since it's
# not actually drops-specific in what it does, just in where it was first
# written.


class IdentityThrottle(_IPRateThrottle):
    """MeView (GET/PATCH /api/identities/me/) had zero rate limiting — a
    genuine gap, not a deliberate exemption. It requires a valid ed25519
    signature, so this isn't fully anonymous access, but signature
    verification is real per-request CPU work (see identities/auth.py),
    and nothing was stopping it being repeated without limit. Kept as its
    own scope rather than sharing DropReadThrottle's bucket — an identity
    doing a lot of legitimate drop-browsing shouldn't burn down the same
    allowance as its own, unrelated identity-management calls."""

    scope = "identity_me"
