from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from database import db
from bson import ObjectId

campaigns_bp = Blueprint('campaigns', __name__)

@campaigns_bp.route('/admin/campaigns', methods=['GET', 'POST'])
@login_required
def manage_campaigns():
    if current_user.role != 'admin':
        flash('Permission denied.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        title = request.form.get('title')
        message_content = request.form.get('message')
        segment = request.form.get('segment', 'all_users')  # all_users | group | individual
        target_group_id = request.form.get('target_group')
        target_user = request.form.get('target_user')
        schedule_time = request.form.get('schedule_time')

        from datetime import datetime

        campaign = {
            'title': title,
            'message': message_content,
            'segment': segment,
            'sent_by': current_user.username,
            'timestamp': datetime.utcnow(),
            'scheduled_for': datetime.fromisoformat(schedule_time) if schedule_time else None,
            'status': 'scheduled' if schedule_time else 'sent',
            'target_count': 0
        }

        if not schedule_time:
            # --- Determine target users based on segment ---
            if segment == 'individual' and target_user:
                target_users = list(db.users.find({'username': target_user}))
            elif segment == 'group' and target_group_id:
                group = db.groups.find_one({'_id': ObjectId(target_group_id)})
                member_names = group['members'] if group else []
                target_users = list(db.users.find({'username': {'$in': member_names}}))
            else:  # all_users
                target_users = list(db.users.find())

            campaign['target_count'] = len(target_users)
            db.campaigns.insert_one(campaign)

            from extensions import socketio
            for user in target_users:
                msg_id = db.messages.insert_one({
                    'sender': 'Admin Campaign',
                    'recipient': user['username'],
                    'content': message_content,
                    'timestamp': datetime.utcnow(),
                    'status': 'sent'
                }).inserted_id

                socketio.emit('new_notification', {
                    '_id': str(msg_id),
                    'sender': 'Admin Campaign',
                    'content': message_content,
                    'timestamp': datetime.utcnow().isoformat(),
                    'status': 'sent'
                }, to=f"user_{user['username']}")

            flash(f'Campaign "{title}" sent to {len(target_users)} user(s)!', 'success')
        else:
            db.campaigns.insert_one(campaign)
            flash(f'Campaign "{title}" scheduled for {schedule_time}', 'info')

        return redirect(url_for('campaigns.manage_campaigns'))

    # --- Analytics ---
    total_campaigns = db.campaigns.count_documents({})
    total_messages = db.messages.count_documents({'sender': 'Admin Campaign'})
    total_users = db.users.count_documents({'role': {'$ne': 'admin'}})

    campaigns = list(db.campaigns.find().sort('timestamp', -1))
    all_users = list(db.users.find())
    all_groups = list(db.groups.find())
    for g in all_groups:
        g['_id'] = str(g['_id'])

    return render_template('admin.html',
                           campaigns=campaigns,
                           users=all_users,
                           groups=all_groups,
                           analytics={'total': total_campaigns, 'msgs': total_messages, 'total_users': total_users})


@campaigns_bp.route('/admin/users/add', methods=['POST'])
@login_required
def add_user():
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'user')

    from extensions import bcrypt
    from models import User

    if User.get_by_username(db, username):
        flash('Username already exists.', 'danger')
    else:
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        User.create_user(db, username, hashed_pw, role=role)
        flash(f'User "{username}" added successfully!', 'success')
    return redirect(url_for('campaigns.manage_campaigns'))


@campaigns_bp.route('/admin/users/delete/<username>', methods=['POST'])
@login_required
def delete_user(username):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    if username == current_user.username:
        flash("Cannot delete yourself.", "danger")
        return redirect(url_for('campaigns.manage_campaigns'))

    db.users.delete_one({'username': username})
    db.messages.delete_many({'recipient': username})
    flash(f'User "{username}" deleted.', 'info')
    return redirect(url_for('campaigns.manage_campaigns'))
