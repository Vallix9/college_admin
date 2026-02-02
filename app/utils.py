# app/utils.py
import pandas as pd
from datetime import datetime
import os
import re

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
    
    exports_dir = os.path.join(os.path.dirname(__file__), '..', 'exports')
    os.makedirs(exports_dir, exist_ok=True)
    
    filepath = os.path.join(exports_dir, filename)
    
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
    backup_dir = 'backups'
    os.makedirs(backup_dir, exist_ok=True)
    
    backup_filename = f'backup_{timestamp}_{backup_type}.backup'
    backup_path = os.path.join(backup_dir, backup_filename)
    
    import zipfile
    import json
    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        if os.path.exists('college.db'):
            zipf.write('college.db', 'college.db')
        metadata = {
            'backup_type': backup_type,
            'created_at': datetime.now().isoformat(),
            'description': description,
            'include_files': include_files
        }
        metadata_str = json.dumps(metadata, indent=2, ensure_ascii=False)
        zipf.writestr('metadata.json', metadata_str)
    
    return backup_filename

def restore_backup(backup_path):
    """Восстановление из резервной копии"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    current_backup = f'restore_backup_{timestamp}.db'
    
    if os.path.exists('college.db'):
        import shutil
        shutil.copy2('college.db', current_backup)
    
    import zipfile
    with zipfile.ZipFile(backup_path, 'r') as zipf:
        zipf.extractall('.')
    
    return True

def import_from_file(file, import_type, import_mode='append'):
    """Импорт данных из файла"""
    filename = file.filename
    
    if filename.endswith('.xlsx') or filename.endswith('.xls'):
        df = pd.read_excel(file)
    elif filename.endswith('.csv'):
        df = pd.read_csv(file)
    else:
        raise ValueError('Неподдерживаемый формат файла')
    
    if import_type == 'students':
        return f'Импортировано {len(df)} студентов'
    elif import_type == 'grades':
        return f'Импортировано {len(df)} оценок'
    elif import_type == 'groups':
        return f'Импортировано {len(df)} групп'
    
    return f'Импортировано {len(df)} записей'

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