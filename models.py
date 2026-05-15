from flask_login import UserMixin
from bson import ObjectId

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.password_hash = user_data['password_hash']
        self.phone_number = user_data.get('phone_number', '')
        self.role = user_data.get('role', 'user')

    @staticmethod
    def get_by_id(db, user_id):
        user_data = db.users.find_one({'_id': ObjectId(user_id)})
        return User(user_data) if user_data else None

    @staticmethod
    def get_by_username(db, username):
        user_data = db.users.find_one({'username': username})
        return User(user_data) if user_data else None

    @staticmethod
    def create_user(db, username, password_hash, phone_number=None, role='user'):
        user_data = {
            'username': username,
            'password_hash': password_hash,
            'phone_number': phone_number,
            'role': role
        }
        result = db.users.insert_one(user_data)
        return str(result.inserted_id)
