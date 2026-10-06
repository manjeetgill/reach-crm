from sqlalchemy import select
from sqlalchemy.orm import Session
from test_workflows import client, engine, org
from app.models import PlatformOwner, User, AuditLog


def platform_account(name):
    org_id, staff = org(name)
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.organization_id == org_id))
        user_id = user.id
        db.add(PlatformOwner(user_id=user_id))
        db.commit()
    r = client.post('/api/v1/platform/auth/login', json={'email': name.lower()+'@example.com', 'password': 'test-password-123'})
    assert r.status_code == 200
    return org_id, staff, {'Authorization': 'Bearer '+r.json()['token']}, user_id


def test_customer_roles_cannot_access_platform():
    _, owner = org('CustomerOnly')
    assert client.get('/api/v1/platform/workspaces', headers=owner).status_code == 403
    assert client.get('/api/v1/platform/summary', headers=owner).status_code == 403
    assert client.post('/api/v1/platform/auth/login', json={'email': 'customeronly@example.com', 'password': 'test-password-123'}).status_code == 401
    assert client.get('/api/v1/platform/workspaces').status_code == 401
    assert client.post('/api/v1/users', headers=owner, json={'name':'Intruder','email':'intruder@example.com','password':'test-password-123','role':'platform_owner'}).status_code == 422


def test_platform_management_scope_filters_and_audit():
    _, staff, admin, _ = platform_account('PlatformManager')
    customer_id, customer = org('ManagedCustomer')
    assert client.get('/api/v1/platform/auth/me', headers=admin).status_code == 200
    # Staff and platform sessions are not interchangeable, even for the same user.
    assert client.get('/api/v1/platform/workspaces', headers=staff).status_code == 403
    assert client.get('/api/v1/contacts', headers=admin).status_code == 401
    assert client.post('/api/v1/whatsapp-agent/details', headers=customer, json={'phone': '+918219613387'}).status_code == 200
    listing = client.get('/api/v1/platform/workspaces?q=ManagedCustomer', headers=admin).json()
    assert listing['total'] == 1 and listing['items'][0]['whatsapp_status'] == 'requested'
    pending = client.get('/api/v1/platform/workspaces?whatsapp=pending&q=ManagedCustomer', headers=admin).json()
    assert pending['total'] == 1 and pending['items'][0]['id'] == customer_id
    assert 'password_hash' not in str(listing) and 'settings' not in listing['items'][0]
    assert client.get('/api/v1/platform/summary', headers=admin).json()['whatsapp_pending'] >= 1
    path = '/api/v1/platform/workspaces/'+customer_id
    assert client.patch(path, headers=customer, json={'name':'Unwanted','stage':'live','notes':''}).status_code == 403
    updated = client.patch(path, headers=admin, json={'name':'Managed Customer Updated','stage':'onboarding','notes':'Private setup note'})
    assert updated.status_code == 200
    assert updated.json()['notes'] == 'Private setup note'
    assert len(updated.json()['history']) == 1
    assert client.get('/api/v1/platform/workspaces?q=Managed%20Customer&stage=new', headers=admin).json()['total'] == 0
    assert client.get('/api/v1/platform/workspaces?q=managedcustomer@example.com&stage=onboarding', headers=admin).json()['total'] == 1
    page = client.get('/api/v1/platform/workspaces?limit=1&offset=0', headers=admin).json()
    second = client.get('/api/v1/platform/workspaces?limit=1&offset=1', headers=admin).json()
    assert page['items'][0]['id'] != second['items'][0]['id']
    assert client.patch(path, headers=admin, json={'name':'  ','stage':'live','notes':''}).status_code == 422
    assert client.patch(path, headers=admin, json={'name':'Valid','stage':'unknown','notes':''}).status_code == 422
    assert client.get('/api/v1/platform/workspaces/missing', headers=admin).status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(AuditLog).where(AuditLog.organization_id == customer_id, AuditLog.action == 'platform.workspace_updated'))


def test_revoked_platform_grant_invalidates_session():
    _, _, admin, user_id = platform_account('RevokedPlatform')
    with Session(engine) as db:
        db.delete(db.get(PlatformOwner, user_id))
        db.commit()
    assert client.get('/api/v1/platform/workspaces', headers=admin).status_code == 401
    assert client.post('/api/v1/platform/auth/login', json={'email':'revokedplatform@example.com','password':'test-password-123'}).status_code == 401
