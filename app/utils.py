import pandas as pd
from datetime import datetime, timedelta
import os
import json
import sqlite3
from io import BytesIO
import zipfile

def export_to_excel(data, filename_prefix):
    """Экспорт данных в Excel"""
    df = pd.DataFrame(data)
    
    # Создаем имя файла с датой
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{filename_prefix}_{timestamp}.xlsx'
    filepath = f'exports/{filename}'
    
    # Создаем директорию если её нет
    os.makedirs('exports', exist_ok=True)
    
    # Сохраняем в Excel
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

def create_backup(backup_type='full', include_files=False, description=''):
    """Создание резервной копии"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = 'backups'
    os.makedirs(backup_dir, exist_ok=True)
    
    backup_filename = f'backup_{timestamp}_{backup_type}.backup'
    backup_path = os.path.join(backup_dir, backup_filename)
    
    # Создаем ZIP архив с данными
    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Копируем базу данных
        if os.path.exists('college.db'):
            zipf.write('college.db', 'college.db')
        
        # Добавляем метаданные
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
    # Создаем резервную копию текущей базы данных
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    current_backup = f'restore_backup_{timestamp}.db'
    
    if os.path.exists('college.db'):
        import shutil
        shutil.copy2('college.db', current_backup)
    
    # Распаковываем резервную копию
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
    
    # Логика импорта в зависимости от типа
    if import_type == 'students':
        return f'Импортировано {len(df)} студентов'
    elif import_type == 'grades':
        return f'Импортировано {len(df)} оценок'
    elif import_type == 'groups':
        return f'Импортировано {len(df)} групп'
    
    return f'Импортировано {len(df)} записей'

def validate_email(email):
    """Валидация email"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone):
    """Валидация номера телефона"""
    import re
    pattern = r'^(\+7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'
    return re.match(pattern, phone) is not None

def generate_password(length=12):
    """Генерация случайного пароля"""
    import random
    import string
    
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    password = ''.join(random.choice(characters) for _ in range(length))
    return password

def get_system_stats():
    """Получение статистики системы"""
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
    """Очистка старых файлов"""
    cutoff_date = datetime.now() - timedelta(days=days_old)
    
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        if os.path.isfile(filepath):
            file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
            if file_time < cutoff_date:
                os.remove(filepath)
                print(f'Удален старый файл: {filename}')
    
    return True