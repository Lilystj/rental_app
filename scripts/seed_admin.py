import os, sys
from werkzeug.security import generate_password_hash

# Make sure project root is on sys.path so imports like `from app import app` work
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app
from extensions import db

with app.app_context():
    # import models inside app context to avoid circular import issues
    from models import User

    username = 'admin'
    password = 'admin123'
    email = 'admin@wheelshare.local'

    existing = User.query.filter_by(username=username).first()
    if existing:
        print(f"Admin user '{username}' already exists.")
    else:
        admin = User(username=username, password=generate_password_hash(password), email=email, role='admin')
        db.session.add(admin)
        db.session.commit()
        print(f"Created admin user: {username} / {password}")
