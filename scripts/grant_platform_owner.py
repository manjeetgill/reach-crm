"""Explicitly grant platform access to an existing Reach account. Never grants on signup."""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api'))
from sqlalchemy import select
from app.db import SessionLocal
from app.models import AuditLog, PlatformOwner, User

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--organization-id', required=True)
parser.add_argument('--email', required=True)
args = parser.parse_args()
if not os.getenv('DATABASE_URL'):
    raise SystemExit('Set DATABASE_URL explicitly to the intended Reach database.')
with SessionLocal() as db:
    user = db.scalar(select(User).where(User.organization_id == args.organization_id, User.email == args.email.lower(), User.status == 'active'))
    if not user:
        raise SystemExit('Active account not found. No access granted.')
    if not db.get(PlatformOwner, user.id):
        db.add(PlatformOwner(user_id=user.id))
        db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, action='platform.owner_granted', resource_type='user', resource_id=user.id))
        db.commit()
    print('Platform access granted to the specified account. Sign in at /platform with its existing password.')
