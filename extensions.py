from flask_socketio import SocketIO
from flask_bcrypt import Bcrypt
from flask_login import LoginManager

socketio = SocketIO(cors_allowed_origins="*", async_mode='eventlet')
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
