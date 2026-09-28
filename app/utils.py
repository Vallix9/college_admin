# app/utils.py
import pandas as pd
from datetime import datetime
import os
import re
import json
import shutil
import zipfile

BASEDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BACKUP_DIR = os.path.join(BASEDIR, 'backups')
EXPORT_DIR = os.path.join(BASEDIR, 'exports')
DB_PATH = os.path.join(BASEDIR, 'college.db')


def get_db_path():
    """Путь к файлу БД. Учитывает DATABASE_URL, иначе college.db в корне."""
    uri = os.environ.get('DATABASE_URL') or ''
    if uri.startswith('sqlite:///') and ':memory:' not in uri:
        candidate = uri[len('sqlite:///'):]
        if candidate and not os.path.isabs(candidate):
            candidate = os.path.join(BASEDIR, candidate)
        return candidate
    return DB_PATH


def get_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def sanitize_filename(filename):
    """Удаляет недопустимые символы из имени файла для Windows"""
    filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
    filename = re.sub(r'\.{2,}', '.', filename)
    filename = filename.strip(' ._')
    return filename if filename else 'report'

def export_to_excel(data, filename_prefix):
    """Экспорт данных в Excel с корректным путём и безопасным именем"""
    df = pd.DataFrame(data)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_prefix = sanitize_filename(filename_prefix)
    filename = f'{safe_prefix}_{timestamp}.xlsx'
    
    os.makedirs(EXPORT_DIR, exist_ok=True)
    filepath = os.path.join(EXPORT_DIR, filename)
    
    df.to_excel(filepath, index=False, engine='openpyxl')
    
    return filepath

def format_date(date_obj):
    """Форматирование даты"""
    if date_obj:
        return date_obj.strftime('%d.%m.%Y')
    return ''

def calculate_age(birth_date):
    """Вычисление возраста"""
    if birth_date:
        today = datetime.now().date()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        return age
    return None

# Остальные функции (без изменений)
def create_backup(backup_type='full', include_files=False, description=''):
    """Создание резервной копии"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = get_backup_dir()
    
    backup_filename = f'backup_{timestamp}_{backup_type}.backup'
    backup_path = os.path.join(backup_dir, backup_filename)
    
    db_path = get_db_path()
    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        if os.path.exists(db_path):
            zipf.write(db_path, 'college.db')
        metadata = {
            'backup_type': backup_type,
            'created_at': datetime.now().isoformat(),
            'description': description,
            'include_files': include_files
        }
        metadata_str = json.dumps(metadata, indent=2, ensure_ascii=False)
        zipf.writestr('metadata.json', metadata_str)
    
    return backup_filename


def list_backups():
    """Список резервных копий: новые сверху, с разобранным metadata.json"""
    backup_dir = get_backup_dir()
    backups = []

    for filename in os.listdir(backup_dir):
        if not filename.endswith('.backup'):
            continue

        full_path = os.path.join(backup_dir, filename)
        size_mb = round(os.path.getsize(full_path) / 1024 / 1024, 3)

        backup_type, description, created = 'full', '', ''
        try:
            with zipfile.ZipFile(full_path, 'r') as zipf:
                if 'metadata.json' in zipf.namelist():
                    meta = json.loads(zipf.read('metadata.json').decode('utf-8'))
                    backup_type = meta.get('backup_type', 'full')
                    description = meta.get('description', '') or ''
                    created = meta.get('created_at', '') or ''
        except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError):
            pass

        if not created:
            created = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime(
                '%Y-%m-%d %H:%M:%S')

        backups.append({
            'filename': filename,
            'type': backup_type,
            'description': description,
            'date': created,
            'size_mb': size_mb,
            'path': full_path,
        })

    backups.sort(key=lambda b: b['date'], reverse=True)
    return backups


def delete_backup(filename):
    """Удаляет файл резервной копии. Возвращает True/False."""
    safe_name = os.path.basename(sanitize_filename(filename))
    if not safe_name.endswith('.backup'):
        return False

    path = os.path.join(get_backup_dir(), safe_name)
    if not os.path.isfile(path):
        return False

    os.remove(path)
    return True


def restore_backup(backup_path):
    """Восстановление из резервной копии.

    Перед восстановлением текущая база сохраняется отдельным файлом,
    чтобы операцию можно было откатить.
    """
    backup_path = os.path.abspath(backup_path)
    if not os.path.isfile(backup_path):
        raise FileNotFoundError('Файл резервной копии не найден')

    with zipfile.ZipFile(backup_path, 'r') as zipf:
        names = zipf.namelist()
        if 'college.db' not in names:
            raise ValueError('В архиве нет файла college.db')

        db_path = get_db_path()
        if os.path.exists(db_path):
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            current_backup = f'pre_restore_{timestamp}.db'
            shutil.copy2(db_path, os.path.join(get_backup_dir(), current_backup))

        # Извлекаем только college.db, защищаясь от подстановки путей
        # (zip-slip) внутри архива. Остальные файлы не трогаем — иначе
        # архив мог бы записать что угодно в каталог с базой.
        db_dir = os.path.dirname(db_path)
        target = os.path.abspath(os.path.join(db_dir, 'college.db'))
        if os.path.commonpath([target, db_dir]) != db_dir:
            raise ValueError('Некорректный путь внутри архива')

        with zipf.open('college.db') as src, open(target, 'wb') as dst:
            shutil.copyfileobj(src, dst)

    return True

def import_from_file(file, import_type, import_mode='append'):
    """Импорт данных из файла.

    Возвращает словарь с количеством прочитанных строк и текстом результата.
    Фактическая запись в базу — см. Фазу 4.3 плана (import_from_file_real).
    """
    filename = file.filename

    if filename.lower().endswith(('.xlsx', '.xls')):
        df = pd.read_excel(file)
    elif filename.lower().endswith('.csv'):
        df = pd.read_csv(file)
    else:
        raise ValueError('Неподдерживаемый формат файла. Допустимы .xlsx, .xls, .csv')

    titles = {
        'students': 'студентов',
        'grades': 'оценок',
        'groups': 'групп',
        'settings': 'настроек',
    }
    title = titles.get(import_type, 'записей')

    return {
        'count': len(df),
        'type': import_type,
        'mode': import_mode,
        'columns': list(df.columns),
        'message': f'Прочитано строк: {len(df)} ({title})',
    }

def validate_email(email):
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone):
    import re
    pattern = r'^(\+7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'
    return re.match(pattern, phone) is not None

def generate_password(length=12):
    import random
    import string
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(random.choice(characters) for _ in range(length))

def get_system_stats():
    import psutil
    import platform
    stats = {
        'cpu_percent': psutil.cpu_percent(interval=1),
        'memory_percent': psutil.virtual_memory().percent,
        'disk_usage': psutil.disk_usage('/').percent,
        'boot_time': datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S'),
        'python_version': platform.python_version(),
        'system': platform.system(),
        'processor': platform.processor()
    }
    return stats

def clean_old_files(directory, days_old=30):
    from datetime import timedelta
    cutoff_date = datetime.now() - timedelta(days=days_old)
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        if os.path.isfile(filepath):
            file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
            if file_time < cutoff_date:
                os.remove(filepath)
    return True