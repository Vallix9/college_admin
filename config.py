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

# Ключ, на котором подписаны сессии. Подставной ключ из исходников известен
# кому угодно: кто им подписал cookie — тот и может подделать сессию
# администратора. Поэтому в production ключ обязателен, а в разработке
# допускается запасной — иначе не запустится ничего, даже чтобы посмотреть
# страницу.
DEV_SECRET_KEY = 'dev-secret-key-123'

# 32 байта энтропии (hex от secrets.token_hex(32)) — минимум, при котором
# перебор ключа офлайн бессмыслен
MIN_SECRET_KEY_LENGTH = 32

# Помеченные как «production» режимы: контейнер, waitress без отладки,
# явное APP_ENV/FLASK_ENV
PRODUCTION_ENV = {'production', 'prod'}

# Адреса, с которых сервер доступен только самому себе. Слушающий на
# любом другом интерфейсе отладочный сервер доступен из сети
LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def is_production():
    """Работает ли приложение не в режиме разработки.

    Отдельная функция, а не флаг в Config: проверка нужна до создания
    приложения, чтобы не стартовать вовсе с небезопасным ключом.
    """
    env = (os.environ.get('APP_ENV') or os.environ.get('FLASK_ENV') or '').strip().lower()
    if env in PRODUCTION_ENV:
        return True
    # WAITRESS=1 — это запуск сервиса, а не локальная отладка
    return os.environ.get('WAITRESS') == '1'


def secret_key_error(key):
    """Почему ключ не годится, либо None, если всё в порядке.

    Отдельная функция ради тестов: правило проверки должно проверяться, а не
    выглядеть правильным.
    """
    if not key:
        return ('SECRET_KEY не задан. Сгенерируйте ключ командой '
                'python -c "import secrets; print(secrets.token_hex(32))" '
                'и запишите его в .env')
    if key == DEV_SECRET_KEY:
        return ('SECRET_KEY остался запасным (dev-secret-key-123). Он есть в '
                'исходниках, и им можно подписать поддельную сессию '
                'администратора. Задайте случайный ключ в .env')
    if len(key) < MIN_SECRET_KEY_LENGTH:
        return (f'SECRET_KEY слишком короткий ({len(key)} символов, нужно '
                f'минимум {MIN_SECRET_KEY_LENGTH}). Подобрать короткий ключ '
                'перебором проще, чем длинный')
    return None


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or DEV_SECRET_KEY
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
        # Небезопасный SECRET_KEY — повод не стартовать, а не повод
        # предупредить: приложение, поднятое с ключом из исходников,
        # выглядит снаружи совершенно рабочим, и в продакшн его так и
        # уедут. В разработке запасной ключ допустим.
        if is_production():
            error = secret_key_error(app.config['SECRET_KEY'])
            if error:
                raise RuntimeError(
                    'Запуск в production невозможен: ' + error)
            # Отладочный Werkzeug на внешнем интерфейсе — это выполнение
            # произвольного кода по PIN-коду: отладочная консоль доступна
            # каждому, кто дотянулся до порта
            if app.config['DEBUG'] and app.config['HOST'] not in LOOPBACK_HOSTS:
                raise RuntimeError(
                    'Запуск в production невозможен: FLASK_DEBUG=1 при HOST='
                    f'{app.config["HOST"]}. Отладочная консоль Werkzeug '
                    'доступна из сети. Либо отключите отладку, либо '
                    'верните HOST=127.0.0.1')
            # FLASK_DEV_SERVER=1 — явная просьба поднять отладочный сервер.
            # В production такой просьбы быть не должно: её выполнение
            # означало бы, что в сервис попал Werkzeug с его консолью.
            if os.environ.get('FLASK_DEV_SERVER') == '1':
                raise RuntimeError(
                    'Запуск в production невозможен: FLASK_DEV_SERVER=1 '
                    'требует отладочный сервер Werkzeug. Уберите переменную '
                    'либо поставьте waitress (pip install waitress)')

        # Создаем необходимые директории
        for folder in (app.config['UPLOAD_FOLDER'],
                       app.config['BACKUP_FOLDER'],
                       app.config['EXPORT_FOLDER'],
                       app.config['LOG_FOLDER']):
            os.makedirs(folder, exist_ok=True)