import os
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))

def _read_env_fallback(env_path):
    """Разбирает .env вручную в cp1251 — на случай, если редактор на Windows
    сохранил файл в однобайтовой кодировке, а комментарии на русском
    ломают UnicodeDecodeError при чтении в utf-8."""
    with open(env_path, encoding='cp1251', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))

def load_env_file():
    env_path = os.path.join(basedir, '.env')
    if not os.path.isfile(env_path):
        return
    try:
        load_dotenv(env_path, encoding='utf-8')
    except UnicodeDecodeError:
        _read_env_fallback(env_path)

load_env_file()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-123'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'college.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Настройки сессии
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    
    # Настройки загрузки файлов
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    
    # Настройки резервного копирования
    BACKUP_FOLDER = os.path.join(basedir, 'backups')
    
    # Каталоги экспорта и логов (вынесены из init_app, чтобы к ним
    # могли обращаться utils.py и хелперы журнала логов)
    EXPORT_FOLDER = os.path.join(basedir, 'exports')
    LOG_FOLDER = os.path.join(basedir, 'logs')
    
    # Сетевые настройки сервера
    HOST = os.environ.get('HOST', '127.0.0.1')
    PORT = int(os.environ.get('PORT', 5000))
    
    # Режим отладки. Включать только для разработки — при debug=True
    # Werkzeug показывает трассировки и выполняет произвольный код из консоли
    DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1'
    
    @staticmethod
    def init_app(app):
        # Создаем необходимые директории
        for folder in (app.config['UPLOAD_FOLDER'],
                       app.config['BACKUP_FOLDER'],
                       app.config['EXPORT_FOLDER'],
                       app.config['LOG_FOLDER']):
            os.makedirs(folder, exist_ok=True)