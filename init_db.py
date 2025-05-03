from app import app, db

with app.app_context():
    db.drop_all()  # This will drop all existing tables
    db.create_all()  # This will create new tables with the correct schema
    print("Database created successfully!")