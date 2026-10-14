"""Generate the secret keys the reminders need. Run once:  python scripts/generate_keys.py

Prints three settings to paste into Vercel (Project Settings -> Environment Variables).
Keep VAPID_PRIVATE_KEY and CRON_SECRET secret: don't commit them or share them.
"""
import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
private_raw = key.private_numbers().private_value.to_bytes(32, "big")
public_raw = key.public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

print("VAPID_PUBLIC_KEY=" + b64url(public_raw))
print("VAPID_PRIVATE_KEY=" + b64url(private_raw))
print("CRON_SECRET=" + secrets.token_hex(32))
