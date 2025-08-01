# create_tables.py
from app import app, db    # adjust import to match your app’s module name

with app.app_context():
    db.create_all()
    print("✅ Tables created!")
