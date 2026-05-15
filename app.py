import os
from flask import Flask, render_template, redirect, url_for, request, jsonify
from flask_socketio import SocketIO
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from pymongo import MongoClient
from dotenv import load_dotenv

from extensions import socketio, bcrypt, login_manager
from models import User

import threading
import time
from datetime import datetime

# Background scheduler for campaigns
def run_scheduler(app):
    with app.app_context():
        while True:
            now = datetime.utcnow()
            pending = list(db.campaigns.find({
                'status': 'scheduled',
                'scheduled_for': {'$lte': now}
            }))
            
            if pending:
                users = list(db.users.find())
                for campaign in pending:
                    for user in users:
                        msg_id = db.messages.insert_one({
                            'sender': 'Admin Campaign',
                            'recipient': user['username'],
                            'content': campaign['message'],
                            'timestamp': datetime.utcnow(),
                            'status': 'sent'
                        }).inserted_id
                        
                        socketio.emit('new_notification', {
                            '_id': str(msg_id),
                            'sender': 'Admin Campaign',
                            'content': campaign['message'],
                            'timestamp': datetime.utcnow().isoformat(),
                            'status': 'sent'
                        }, to=f"user_{user['username']}")
                        
                    db.campaigns.update_one({'_id': campaign['_id']}, {'$set': {'status': 'sent', 'target_count': len(users)}})
            
            time.sleep(10) # Check every 10 seconds

# Load environment variables
load_dotenv()

from werkzeug.utils import secure_filename

# Upload config
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 # 5MB limit
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev_secret')

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        return {'error': 'No file part'}, 400
    file = request.files['file']
    if file.filename == '':
        return {'error': 'No selected file'}, 400
    if file and allowed_file(file.filename):
        filename = secure_filename(f"{int(time.time())}_{file.filename}")
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return {'url': url_for('static', filename=f'uploads/{filename}')}
    return {'error': 'Invalid file type'}, 400

# Initialize extensions
socketio.init_app(app)
bcrypt.init_app(app)
login_manager.init_app(app)

# MongoDB connection
from database import db

from bson import ObjectId
from datetime import datetime

@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(db, user_id)

# Import and register blueprints
from routes.auth import auth_bp
from routes.campaigns import campaigns_bp
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(campaigns_bp)

@app.context_processor
def inject_unread_context():
    if current_user.is_authenticated:
        unread_msgs = list(db.messages.find({
            'recipient': current_user.username,
            'status': {'$ne': 'read'}
        }).sort('timestamp', -1).limit(10))
        count = db.messages.count_documents({
            'recipient': current_user.username,
            'status': {'$ne': 'read'}
        })
        return dict(global_unread_count=count, global_unread_messages=unread_msgs)
    return dict(global_unread_count=0, global_unread_messages=[])

@app.route('/welcome')
def welcome():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('campaigns.manage_campaigns'))
        return redirect(url_for('index'))
    return render_template('welcome.html')

@app.route('/')
@login_required
def index():
    # All users (including admin) can access messaging
    users = list(db.users.find())
    # Inbox: messages received from Admin campaigns
    inbox = list(db.messages.find({
        'recipient': current_user.username,
        'sender': 'Admin Campaign'
    }).sort('timestamp', -1))
    
    # Calculate unread messages per sender for the sidebar persistent dots
    unread_sender_docs = db.messages.aggregate([
        {'$match': {'recipient': current_user.username, 'status': {'$ne': 'read'}, 'sender': {'$ne': 'Admin Campaign'}}},
        {'$group': {'_id': '$sender', 'count': {'$sum': 1}}}
    ])
    unread_senders = {doc['_id']: doc['count'] for doc in unread_sender_docs}
    
    return render_template('index.html', users=users, inbox=inbox, unread_senders=unread_senders)

@login_manager.unauthorized_handler
def unauthorized():
    return redirect(url_for('welcome'))

from flask_socketio import join_room, leave_room, emit

# Global state for online users
online_users = {}

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        online_users[current_user.username] = True
        emit('user_status', {'username': current_user.username, 'status': 'online'}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        if current_user.username in online_users:
            del online_users[current_user.username]
        emit('user_status', {'username': current_user.username, 'status': 'offline'}, broadcast=True)

@socketio.on('join')
def on_join(data):
    username = current_user.username
    room = f"user_{username}"
    join_room(room)
    emit('all_statuses', {'online_users': list(online_users.keys())})

@socketio.on('typing')
def handle_typing(data):
    recipient_room = f"user_{data['recipient']}"
    emit('user_typing', {'username': current_user.username}, to=recipient_room)

@socketio.on('stop_typing')
def handle_stop_typing(data):
    recipient_room = f"user_{data['recipient']}"
    emit('user_stopped_typing', {'username': current_user.username}, to=recipient_room)

@socketio.on('send_message')
def handle_message(data):
    recipient_username = data.get('recipient')
    group_id = data.get('group_id')
    content = data.get('content', '')[:160] # Enforce 160 char limit
    attachment = data.get('attachment') # For MMS
    
    sender_username = current_user.username
    
    message = {
        'sender': sender_username,
        'content': content,
        'timestamp': datetime.utcnow(),
        'status': 'sent'
    }
    
    if attachment:
        message['attachment'] = attachment
        message['type'] = 'mms'
    else:
        message['type'] = 'text'

    if group_id:
        message['group_id'] = group_id
        db.messages.insert_one(message)
        emit('new_group_message', {
            'sender': sender_username,
            'content': content,
            'attachment': attachment,
            'group_id': group_id,
            'timestamp': message['timestamp'].isoformat()
        }, to=f"group_{group_id}")
    else:
        message['recipient'] = recipient_username
        msg_id = db.messages.insert_one(message).inserted_id
        
        emit('new_message', {
            '_id': str(msg_id),
            'sender': sender_username,
            'content': content,
            'attachment': attachment,
            'timestamp': message['timestamp'].isoformat()
        }, to=f"user_{recipient_username}")
        
        emit('new_message', {
            '_id': str(msg_id),
            'sender': sender_username,
            'content': content,
            'attachment': attachment,
            'timestamp': message['timestamp'].isoformat()
        }, to=f"user_{sender_username}")

@socketio.on('get_history')
def handle_history(data):
    recipient = data.get('recipient')
    group_id = data.get('group_id')
    
    if group_id:
        messages = list(db.messages.find({'group_id': group_id}).sort('timestamp', 1))
    else:
        messages = list(db.messages.find({
            '$or': [
                {'sender': current_user.username, 'recipient': recipient},
                {'sender': recipient, 'recipient': current_user.username}
            ]
        }).sort('timestamp', 1))
    
    for m in messages:
        m['_id'] = str(m['_id'])
        m['timestamp'] = m['timestamp'].isoformat()
    
    emit('load_history', {'messages': messages})

@socketio.on('mark_delivered')
def handle_mark_delivered(data):
    message_id = data.get('message_id')
    if message_id:
        msg = db.messages.find_one({'_id': ObjectId(message_id)})
        if msg and msg.get('status') == 'sent':
            db.messages.update_one({'_id': ObjectId(message_id)}, {'$set': {'status': 'delivered'}})
            emit('message_status_update', {
                'message_id': message_id,
                'status': 'delivered'
            }, to=f"user_{msg['sender']}")

@socketio.on('mark_read')
def handle_mark_read(data):
    message_id = data.get('message_id')
    if message_id:
        msg = db.messages.find_one({'_id': ObjectId(message_id)})
        if msg:
            db.messages.update_one({'_id': ObjectId(message_id)}, {'$set': {'status': 'read'}})
            emit('message_status_update', {
                'message_id': message_id,
                'status': 'read'
            }, to=f"user_{msg['sender']}")

# Group Routes
@app.route('/groups/create', methods=['POST'])
@login_required
def create_group():
    name = request.form.get('name')
    members = request.form.getlist('members')
    members.append(current_user.username)
    
    group = {
        'name': name,
        'creator': current_user.username,
        'members': list(set(members)),
        'created_at': datetime.utcnow()
    }
    db.groups.insert_one(group)
    return redirect(url_for('index'))

@app.route('/groups/members/add', methods=['POST'])
@login_required
def add_group_member():
    group_id = request.form.get('group_id')
    username = request.form.get('username')
    
    group = db.groups.find_one({'_id': ObjectId(group_id)})
    if not group or group['creator'] != current_user.username:
        return {'error': 'Unauthorized'}, 403
    
    if username not in group['members']:
        db.groups.update_one({'_id': ObjectId(group_id)}, {'$push': {'members': username}})
        return jsonify({'success': True})
    return jsonify({'error': 'Member already exists'}), 400

@app.route('/groups/members/remove', methods=['POST'])
@login_required
def remove_group_member():
    group_id = request.form.get('group_id')
    username = request.form.get('username')
    
    group = db.groups.find_one({'_id': ObjectId(group_id)})
    if not group or group['creator'] != current_user.username:
        return {'error': 'Unauthorized'}, 403
        
    if username == group['creator']:
        return jsonify({'error': 'Cannot remove creator'}), 400
        
    db.groups.update_one({'_id': ObjectId(group_id)}, {'$pull': {'members': username}})
    return jsonify({'success': True})

@app.route('/api/groups')
@login_required
def get_groups():
    groups = list(db.groups.find({'members': current_user.username}))
    
    # Get user's last read timestamps for groups
    user_data = db.users.find_one({'username': current_user.username})
    last_reads = user_data.get('last_group_reads', {})

    for g in groups:
        g_id = str(g['_id'])
        g['_id'] = g_id
        
        # Calculate unread count
        last_read_ts = last_reads.get(g_id)
        if last_read_ts:
            if isinstance(last_read_ts, str): # Handle possible string format from previous versions
                from datetime import datetime
                last_read_ts = datetime.fromisoformat(last_read_ts)
            
            unread_count = db.messages.count_documents({
                'group_id': g_id,
                'sender': {'$ne': current_user.username},
                'timestamp': {'$gt': last_read_ts}
            })
        else:
            # If no last_read_ts, count all messages not sent by user
            unread_count = db.messages.count_documents({
                'group_id': g_id,
                'sender': {'$ne': current_user.username}
            })
        
        g['unread_count'] = unread_count
        
    return jsonify({'groups': groups})

@app.route('/api/groups/<group_id>/members')
@login_required
def get_group_members(group_id):
    group = db.groups.find_one({'_id': ObjectId(group_id)})
    if group:
        return jsonify({'members': group['members'], 'creator': group['creator']})
    return jsonify({'error': 'Not found'}), 404

@socketio.on('join_group')
def on_join_group(data):
    group_id = data['group_id']
    join_room(f"group_{group_id}")
    # Also update last read timestamp when joining
    db.users.update_one(
        {'username': current_user.username},
        {'$set': {f'last_group_reads.{group_id}': datetime.utcnow()}}
    )

@socketio.on('mark_group_read')
def handle_mark_group_read(data):
    group_id = data.get('group_id')
    if group_id:
        db.users.update_one(
            {'username': current_user.username},
            {'$set': {f'last_group_reads.{group_id}': datetime.utcnow()}}
        )

@app.route('/api/messages/mark_read', methods=['POST'])
@login_required
def api_mark_read():
    message_id = request.json.get('message_id')
    if message_id:
        db.messages.update_one(
            {'_id': ObjectId(message_id), 'recipient': current_user.username},
            {'$set': {'status': 'read'}}
        )
        return jsonify({'success': True})
    return jsonify({'error': 'Message ID required'}), 400

@app.route('/api/messages/mark_all_read', methods=['POST'])
@login_required
def api_mark_all_read():
    db.messages.update_many(
        {'recipient': current_user.username, 'status': {'$ne': 'read'}},
        {'$set': {'status': 'read'}}
    )
    return jsonify({'success': True})

if __name__ == '__main__':
    # Start scheduler thread
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        threading.Thread(target=run_scheduler, args=(app,), daemon=True).start()
    socketio.run(app, debug=True, port=int(os.getenv('PORT', 5000)))
