from flask import Blueprint, render_template, redirect, url_for, flash, request
from extensions import bcrypt, login_manager
from models import User
from flask_login import login_user, logout_user, login_required, current_user
from flask import current_app

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Redirect based on role
        if current_user.role == 'admin':
            return redirect(url_for('campaigns.manage_campaigns'))
        return redirect(url_for('index'))

    role_hint = request.args.get('role', 'user')  # 'user' or 'admin'

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        from database import db
        user_data = db.users.find_one({'username': username})

        if user_data and bcrypt.check_password_hash(user_data['password_hash'], password):
            user_actual_role = user_data.get('role', 'user')
            
            if role_hint == 'admin' and user_actual_role != 'admin':
                flash('Access denied: Admin privileges required to access this portal.', 'danger')
                return render_template('login.html', role_hint=role_hint)
                
            if role_hint == 'user' and user_actual_role == 'admin':
                flash('Admins must use the Admin Portal to log in.', 'info')
                return redirect(url_for('auth.login', role='admin'))

            user = User(user_data)
            login_user(user)
            # Redirect admin to dashboard, users to messaging
            if user.role == 'admin':
                return redirect(url_for('campaigns.manage_campaigns'))
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password. Please try again.', 'danger')

    return render_template('login.html', role_hint=role_hint)

@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('welcome'))
