import base64, hashlib, hmac, secrets, time
import jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from .config import JWT_KEY
from .db import Session, User

bearer = HTTPBearer(auto_error=False)
def password_hash(password):
    if len(password) < 10 or len(password) > 256:
        raise HTTPException(400, '密码长度应为 10–256 个字符')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + digest).decode()

def password_ok(password, value):
    try:
        raw = base64.b64decode(value)
        return hmac.compare_digest(raw[16:], hashlib.scrypt(password.encode(), salt=raw[:16], n=16384, r=8, p=1))
    except (ValueError, TypeError): return False

def token(user, typ='access', seconds=3600, **extra):
    return jwt.encode({'sub':str(user.id), 'ver':user.token_version, 'type':typ, 'iat':int(time.time()), 'exp':int(time.time())+seconds, 'iss':'ops-studio', 'aud':'ops-studio', **extra}, JWT_KEY, algorithm='HS256')

def decode(value, typ='access'):
    try:
        data = jwt.decode(value, JWT_KEY, algorithms=['HS256'], issuer='ops-studio', audience='ops-studio', options={'require':['sub','exp','iat','type','ver']})
        if data['type'] != typ: raise ValueError()
        with Session() as db:
            u = db.get(User, int(data['sub']))
            if not u or not u.active or u.token_version != data['ver']: raise ValueError()
            return u, data
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, '登录已过期或账号不可用')

def current(auth: HTTPAuthorizationCredentials = Depends(bearer)):
    if not auth: raise HTTPException(401, '请先登录')
    return decode(auth.credentials)[0]

def admin(user=Depends(current)):
    if user.role != 'admin': raise HTTPException(403, '需要管理员权限')
    return user

def can_read(user, resource):
    return resource and not resource.archived and (user.role=='admin' or resource.owner_id==user.id or resource.scope=='platform')

def can_write(user, resource):
    return resource and not resource.archived and (user.role=='admin' or (resource.owner_id==user.id and resource.scope=='private'))
