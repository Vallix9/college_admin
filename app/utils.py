import pandas as pd
import os
from datetime import datetime
from io import BytesIO

def export_to_excel(data, filename_prefix):
    """
    Экспорт данных в Excel файл
    """
    if not data:
        return None
    
    df = pd.DataFrame(data)
    
    # Создание временного файла
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{filename_prefix}_{timestamp}.xlsx'
    filepath = os.path.join('temp', filename)
    
    # Создание папки temp если не существует
    os.makedirs('temp', exist_ok=True)
    
    # Сохранение в Excel
    df.to_excel(filepath, index=False, engine='openpyxl')
    
    return filepath

def format_date(date_obj):
    """Форматирование даты в строку"""
    if date_obj:
        return date_obj.strftime('%d.%m.%Y')
    return ''

def get_gender_display(gender_code):
    """Получение отображаемого значения для пола"""
    gender_map = {'M': 'Мужской', 'F': 'Женский'}
    return gender_map.get(gender_code, 'Не указан')

def get_status_display(status_code):
    """Получение отображаемого значения для статуса"""
    status_map = {
        'active': 'Активный',
        'academic_leave': 'Академический отпуск',
        'expelled': 'Отчислен',
        'graduated': 'Выпускник'
    }
    return status_map.get(status_code, 'Неизвестно')

def calculate_age(birth_date):
    """Расчет возраста по дате рождения"""
    if not birth_date:
        return None
    
    today = datetime.today()
    age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    return age