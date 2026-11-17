import os,tempfile
os.environ['OPS_DATA_DIR']=tempfile.mkdtemp(prefix='ops-studio-tests-')
os.environ['OPS_MAX_CONCURRENCY']='3'
os.environ['OPS_PER_USER_CONCURRENCY']='2'
import pytest
from fastapi.testclient import TestClient
from ops.api import app, ATTEMPTS
from ops.db import Base,engine,Session,User,init_db
from ops.auth import password_hash,token

@pytest.fixture(autouse=True)
def clean():
    Base.metadata.drop_all(engine); init_db(); ATTEMPTS.clear()
    yield

@pytest.fixture
def client():
    with TestClient(app) as client: yield client

@pytest.fixture
def people():
    with Session() as db:
        values=[]
        for name,role in [('admin','admin'),('alice','developer'),('bob','developer')]:
            u=User(username=name,password=password_hash('test-password-123'),role=role);db.add(u);db.flush()
            values.append({'user':u,'headers':{'Authorization':'Bearer '+token(u)}})
        db.commit();return values
