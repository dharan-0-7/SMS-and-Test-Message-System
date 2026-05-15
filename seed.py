import os
from pymongo import MongoClient
from flask_bcrypt import Bcrypt
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
bcrypt = Bcrypt(app)

from database import db

def seed_db():
    print("Clearing existing users and messages...")
    db.users.delete_many({})
    db.messages.delete_many({})
    db.campaigns.delete_many({})

    print("Creating admin user...")
    admin_pw = bcrypt.generate_password_hash('admin123').decode('utf-8')
    db.users.insert_one({
        'username': 'admin',
        'password_hash': admin_pw,
        'phone_number': '1234567890',
        'role': 'admin'
    })

    print("Creating test user...")
    test_pw = bcrypt.generate_password_hash('user123').decode('utf-8')
    db.users.insert_one({
        'username': 'tester',
        'password_hash': test_pw,
        'phone_number': '0987654321',
        'role': 'user'
    })

    print("Seeding complete!")
    print("Admin: username='admin', password='admin123'")
    print("User: username='tester', password='user123'")

if __name__ == '__main__':
    seed_db()
