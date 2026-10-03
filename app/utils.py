# app/utils.py
import logging
import os
import re
import sys
import json
import shutil
import zipfile
from logging.handlers import RotatingFileHandler

import pandas as pd
from datetime import date, datetime

BASEDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Каталоги берутся из переменных окружения, как и в config.py. Раньше здесь
# были жёстко прописаны пути рядом с BASEDIR, а config.py объявлял свои
# BACKUP_FOLDER/EXPORT_FOLDER: две независимые правды, и настройка папки
# молча ни на что не влияла — файлы всё равно оказывались в exports/.
BACKUP_DIR = os.environ.get('BACKUP_FOLDER') or \
    os.path.join(BASEDIR, 'backups')
EXPORT_DIR = os.environ.get('EXPORT_FOLDER') or \
    os.path.join(BASEDIR, 'exports')
LOG_DIR = os.environ.get('LOG_FOLDER') or os.path.join(BASEDIR, 'logs')
DB_PATH = os.path.join(BASEDIR, 'college.db')

LOGGER_NAME = 'college'
LOG_FILENAME = 'app.log'

# Значения оценок, которые не являются числами, но имеют числовой эквивалент.
# Раньше эта таблица существовала в двух местах с разными значениями и разным
# поведением на мусорных данных, из-за чего средние баллы расходились.
NON_NUMERIC_GRADES = {
    'зачет': 5.0,
    'зачёт': 5.0,
    'незачет': 2.0,
    'незачёт': 2.0,
}


def grade_to_points(grade_value):
    """Числовой эквивалент оценки или None, если оценку в баллы не перевести.

    'зачет' = 5, 'незачет' = 2, числа и их строковые записи — как есть.
    Всё остальное (пусто, 'отлично', мусор) возвращает None и в среднем
    балле не участвует.
    """
    if grade_value is None:
        return None

    if isinstance(grade_value, bool):
        return None

    if isinstance(grade_value, (int, float)):
        return float(grade_value)

    text = str(grade_value).strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in NON_NUMERIC_GRADES:
        return NON_NUMERIC_GRADES[lowered]

    try:
        return float(text)
    except ValueError:
        return None


def average_grade(grades):
    """Средний балл по списку оценок (моделей Grade, кортежей или значений).

    Некорректные значения пропускаются, пустой набор даёт 0.
    """
    total = 0.0
    count = 0

    for item in grades or ():
        value = getattr(item, 'grade_value', item)
        points = grade_to_points(value)
        if points is None:
            continue
        total += points
        count += 1

    return round(total / count, 2) if count else 0


def setup_logging(log_dir=None, level=logging.INFO, max_bytes=2 * 1024 * 1024, backup_count=5):
    """Настраивает логирование в logs/app.log и в консоль.

    Хендлеры вешаются на именованный логгер, а не на root: иначе туда же
    попадут сообщения werkzeug в его собственном формате, и парсер
    в шаблоне журнала не сможет вытащить метку времени.
    """
    log_dir = log_dir or LOG_DIR
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, OSError):
            pass

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, LOG_FILENAME),
        maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    return logger


def get_logger(name=None):
    return logging.getLogger(name or LOGGER_NAME)


# ===================== АУДИТ (13.2) =====================
# Поля, значения которых нельзя писать в журнал: пароль, его хеш и любые
# поля, названные этими словами. Список используется в scrub_audit_details
# перед записью, а не при просмотре — «забыть удалить» невозможно.
AUDIT_FORBIDDEN_KEYS = {
    'password', 'new_password', 'old_password', 'current_password',
    'confirm_password', 'password_hash', 'password_temporary',
    'pass', 'pwd', 'passwd', 'secret', 'token', 'csrf_token',
    'hash', 'password_plain', 'temporary_password',
}


def scrub_audit_details(details):
    """Убирает пароли и хеши из details перед записью в аудит.

    Принимает строку, dict или None. Для dict удаляет опасные ключи
    полностью и заменяет их на «***», чтобы было видно, что значение
    было. Для строки удаляет всё, что похоже на хеш пароля
    (pbkdf2:salt...), и пароль=... в свободном тексте.
    """
    if details is None:
        return None

    if isinstance(details, dict):
        clean = {}
        for key, value in details.items():
            if str(key).lower() in AUDIT_FORBIDDEN_KEYS:
                clean[key] = '***'
            else:
                clean[key] = scrub_audit_details(value)
        return clean

    text = str(details)
    # Хеш вида pbkdf2:sha256:260000$... — не должен попасть в журнал
    text = re.sub(r'pbkdf2:[^\s,;"]+', '***', text)
    # Пароль в свободном тексте: password=X или пароль=X
    text = re.sub(
        r'(?i)\b(парол[ья]|password)\s*[:=]\s*\S+',
        lambda m: m.group(0).split(':')[0].split('=')[0] + '=***', text)
    return text


def format_audit_details(details):
    """Готовит details к записи в колонку Text.

    dict хранится в JSON: в Python repr от словаря значения теряют тип
    («5» и 5 неразличимы, кавычки путаются), а по JSON в журнале потом
    можно искать по конкретному полю. Строка пишется как есть.
    """
    if details is None:
        return None
    clean = scrub_audit_details(details)
    if isinstance(clean, dict):
        try:
            return json.dumps(clean, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(clean)
    return str(clean)


def log_audit(action, user=None, entity_type=None, entity_id=None,
              details=None, ip_address=None, user_agent=None):
    """Записывает действие в таблицу audit_log.

    user — объект User (flask_login current_user) или None (аноним).
    IP и user-agent берутся из Flask-запроса, если не переданы явно.
    details — строка или dict; пароли и хеши вычищаются из них всегда.

    Запись идёт своей транзакцией, поэтому вызывать её надо ПОСЛЕ
    db.session.commit() бизнес-операции: иначе аудит закоммитит ещё не
    сохранённые изменения вместе с собой. Ошибка записи аудита не должна
    ломать основную операцию — исключение глушится и уходит в файловый
    журнал, но сессию откатываем: после неудачного commit() она не
    пригодна для дальнейшей работы.
    """
    try:
        from app.init_ import db as _db
        from app.models import AuditLog

        # Flask-запрос доступен только в контексте запроса
        if ip_address is None or user_agent is None:
            try:
                from flask import request
                if ip_address is None:
                    ip_address = request.remote_addr
                if user_agent is None:
                    user_agent = (request.user_agent.string or '')[:255]
            except Exception:
                # Вне контекста запроса (скрипты, тесты) — пусто
                pass

        # Логин копируется в запись: user_id обнулится при удалении
        # сотрудника, а кто именно действовал — должно остаться видно
        username = getattr(user, 'username', None)

        entry = AuditLog(
            user_id=getattr(user, 'id', None),
            username=(username or '')[:150] or None,
            role=getattr(user, 'role', None),
            action=str(action)[:100],
            entity_type=(str(entity_type) if entity_type else None),
            details=format_audit_details(details),
            ip_address=(ip_address or '')[:64] or None,
            user_agent=(user_agent or '')[:255] or None,
        )
        if entity_type:
            entry.entity_type = entry.entity_type[:50] or None
        if entity_id is not None:
            entry.entity_id = str(entity_id)[:64]
        _db.session.add(entry)
        _db.session.commit()
    except Exception as exc:
        # Сбой аудита не должен откатывать бизнес-операцию, но оставлять
        # сессию в сломанном состоянии тоже нельзя
        get_logger().error('Не удалось записать в аудит «%s»: %s', action, exc)
        try:
            from app.init_ import db as _db
            _db.session.rollback()
        except Exception:
            pass


def get_log_file_path():
    return os.path.join(LOG_DIR, LOG_FILENAME)


def read_log_lines(limit=1000, level=None, search=None):
    """Читает последние строки журнала. Новые сверху.

    level — один уровень ('ERROR') или их набор (['INFO', 'WARNING']).
    Всё равно читается только текущий app.log, ротированные app.log.N
    не показываются.
    """
    path = get_log_file_path()
    if not os.path.isfile(path):
        return []

    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = [line.rstrip('\n') for line in f if line.strip()]
    except OSError:
        return []

    if level:
        wanted = [level] if isinstance(level, str) else list(level)
        needles = [f'[{name}]' for name in wanted]
        lines = [line for line in lines
                 if any(needle in line for needle in needles)]
    if search:
        needle = search.lower()
        lines = [line for line in lines if needle in line.lower()]

    lines.reverse()
    return lines[:limit] if limit else lines


def clear_log_file():
    """Очищает журнал, сохраняя сами обработчики работоспособными.

    Файл не удаляется, а обрезается до нуля: иначе RotatingFileHandler
    продолжил бы писать в удалённый дескриптор.
    """
    path = get_log_file_path()
    if not os.path.isfile(path):
        return False
    with open(path, 'w', encoding='utf-8'):
        pass
    return True


def count_logs_by_level(level):
    """Сколько записей уровня level в журнале. Используется в шаблоне
    view_logs.html, который зовёт функцию без аргументов."""
    return sum(1 for line in read_log_lines(limit=0) if f'[{level}]' in line)


def get_log_file_size():
    """Размер журнала в КБ."""
    path = get_log_file_path()
    if not os.path.isfile(path):
        return 0
    return round(os.path.getsize(path) / 1024, 2)


def get_log_file_mtime():
    """Когда журнал последний раз изменялся."""
    path = get_log_file_path()
    if not os.path.isfile(path):
        return 'Неизвестно'
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S')


TIMESTAMP_RE = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
MESSAGE_RE = re.compile(
    r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},?\d*\s*\[(INFO|WARNING|ERROR|DEBUG|CRITICAL)\]\s*')


def get_log_level(log_text):
    """Уровень записи журнала."""
    if not log_text:
        return 'INFO'
    if 'CRITICAL' in log_text:
        return 'CRITICAL'
    if 'ERROR' in log_text:
        return 'ERROR'
    if 'WARNING' in log_text:
        return 'WARNING'
    if 'DEBUG' in log_text:
        return 'DEBUG'
    return 'INFO'


def extract_timestamp(log_text):
    """Метка времени из строки журнала."""
    match = TIMESTAMP_RE.search(log_text or '')
    return match.group() if match else 'Неизвестно'


def extract_message(log_text):
    """Текст сообщения без метки времени и уровня."""
    return MESSAGE_RE.sub('', log_text or '').strip()


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

def export_to_excel(data, filename_prefix, fmt=None):
    """Экспорт данных в Excel или CSV.

    Формат берётся из настройки «Формат экспорта», если не задан явно.
    Раньше выбор в настройках ни на что не влиял: всегда создавался .xlsx.
    """
    df = pd.DataFrame(data)

    if fmt is None:
        from app.models import SystemSettings
        fmt = SystemSettings.get_settings().export_format or 'excel'
    fmt = str(fmt).strip().lower()

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_prefix = sanitize_filename(filename_prefix)
    extension = 'csv' if fmt == 'csv' else 'xlsx'
    filename = f'{safe_prefix}_{timestamp}.{extension}'

    os.makedirs(EXPORT_DIR, exist_ok=True)
    filepath = os.path.join(EXPORT_DIR, filename)

    if extension == 'csv':
        # utf-8-sig: без BOM Excel открывает кириллицу как нечитаемый текст
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
    else:
        df.to_excel(filepath, index=False, engine='openpyxl')

    return filepath

def credentials_sheets(issued):
    """Листы ведомости с выданными логинами и паролями.

    Таблица идёт на раздачу: её печатают и режут, поэтому нумерация сквозная,
    а пароль стоит в отдельной колонке с зазором — иначе при переносе строки
    на следующую страницу логин отрывается от своего пароля.
    """
    header = ['№', 'Зачётка (логин)', 'ФИО', 'Группа', 'Временный пароль']
    rows = [header]
    for index, row in enumerate(issued, start=1):
        rows.append([index, row.get('login', ''), row.get('name', ''),
                     row.get('group', ''), row.get('password', '')])
    return [('Учётные записи', rows)]


def format_date(date_obj):
    """Форматирование даты"""
    if date_obj:
        return date_obj.strftime('%d.%m.%Y')
    return ''

IMPORT_TEMPLATES = {
    'students': {
        'columns': ['Номер зачетки', 'ФИО', 'Группа', 'Дата рождения',
                    'Email', 'Телефон', 'Пол', 'Статус'],
        'sample': [['2023001', 'Иванов Иван Иванович', 'Группа 1', '01.01.2005',
                    'ivanov@example.com', '+79991234567', 'M', 'active']],
        'notes': [
            '«Номер зачетки» обязателен и должен быть уникальным.',
            '«ФИО» разбивается на фамилию, имя и отчество автоматически.',
            '«Группа»: если группы ещё нет, она будет создана автоматически.',
            '«Пол»: M или F, любой регистр.',
            '«Дата рождения»: 01.01.2005 или 2005-01-01.',
            'В режиме «Дополнить» записи с уже существующим номером зачётки '
            'обновляются, в режиме «Заменить» все студенты удаляются перед импортом.',
        ],
    },
    'groups': {
        'columns': ['Название', 'Специальность', 'Год'],
        'sample': [['Группа 1', '09.02.06 Программирование', 2023]],
        'notes': [
            '«Название» обязательно и должно быть уникальным.',
            '«Год» — только число, например 2023.',
        ],
    },
    'grades': {
        'columns': ['Номер зачетки', 'Студент', 'Предмет', 'Оценка',
                    'Тип оценки', 'Дата', 'Комментарий'],
        'sample': [['2023001', 'Иванов Иван Иванович', 'Математика', '5',
                    'exam', '01.09.2026', '']],
        'notes': [
            'Студент ищется по «Номеру зачетки», а если он пуст — по «ФИО».',
            'Студент и предмет должны уже существовать в базе, иначе строка '
            'пропускается с ошибкой.',
            '«Оценка»: 5, 4, 3, 2, 4.5, зачет, незачет.',
            'Дата: 01.09.2026 или 2026-09-01.',
        ],
    },
    'settings': {
        'columns': ['Название колледжа', 'Учебный год',
                    'Максимум студентов в группе', 'Элементов на странице',
                    'Цветовая тема', 'Формат экспорта'],
        'sample': [['Технический колледж', '2024-2025', 25, 20, 'purple', 'excel']],
        'notes': [
            'Импортируется только первая строка файла.',
            '«Цветовая тема»: purple, blue, green, orange, red.',
            '«Формат экспорта»: excel или csv.',
        ],
    },
}


def build_import_template(import_type):
    """Собирает .xlsx-шаблон для импорта: заголовки, пример строки,
    лист с пояснениями. Возвращает (путь, имя файла)."""
    spec = IMPORT_TEMPLATES.get(import_type)
    if spec is None:
        raise ValueError(f'Шаблон для типа «{import_type}» не поддерживается')

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'shablon_import_{import_type}_{timestamp}.xlsx'
    filepath = os.path.join(EXPORT_DIR, filename)
    os.makedirs(EXPORT_DIR, exist_ok=True)

    sheet_name = {'students': 'Студенты', 'groups': 'Группы',
                  'grades': 'Оценки', 'settings': 'Настройки'}[import_type]

    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        pd.DataFrame(spec['sample'], columns=spec['columns']).to_excel(
            writer, sheet_name=sheet_name, index=False)
        pd.DataFrame({'Пояснение': spec['notes']}).to_excel(
            writer, sheet_name='Инструкция', index=False)

        book = writer.book
        data_sheet = book[sheet_name]
        for index, column in enumerate(spec['columns'], start=1):
            width = max(len(str(column)) + 2,
                        *(len(str(row[index - 1])) + 2 for row in spec['sample']))
            data_sheet.column_dimensions[
                data_sheet.cell(row=1, column=index).column_letter].width = min(width, 45)
        book['Инструкция'].column_dimensions['A'].width = 80

    return filepath, filename

def parse_academic_year(academic_year):
    """'2026-2027' -> (2026, 2027). None, если строка не разобралась.

    Учебный год всегда записан через дефис: «2026-2027», «2026/2027».
    Взять только первые четыре цифры нельзя — тогда «2025-2026» и «2026-2025»
    выглядели бы одинаково, а это разные годы.
    """
    match = re.search(r'(\d{4})\D+(\d{4})', str(academic_year or ''))
    if not match:
        return None
    first, second = int(match.group(1)), int(match.group(2))
    if second < first:
        return None
    return first, second


# Границы периодов учебного года как (месяц, день) от начала года.
# Учебный год 2026-2027 начинается 1 сентября 2026 года, поэтому периоды
# с месяцем меньше сентября относятся к следующему календарному году.
PERIOD_TEMPLATES = {
    'quarter': [
        ('I четверть', (9, 1), (12, 25)),
        ('II четверть', (12, 26), (3, 15)),
        ('III четверть', (3, 16), (6, 5)),
        ('IV четверть', (6, 6), (8, 31)),
    ],
    'semester': [
        ('I семестр', (9, 1), (1, 15)),
        ('II семестр', (1, 16), (6, 30)),
    ],
}


def build_periods(academic_year, kind='quarter'):
    """Периоды учебного года по шаблону: [(name, start, end), ...].

    kind — 'quarter' (четверти) или 'semester' (семестры). Неизвестный вид
    трактуется как четверти, чтобы автосоздание никогда не падало.
    """
    years = parse_academic_year(academic_year)
    if not years:
        raise ValueError(
            f'Не удалось разобрать учебный год «{academic_year}». '
            'Ожидается формат 2026-2027.')
    first_year, _ = years
    template = PERIOD_TEMPLATES.get(kind, PERIOD_TEMPLATES['quarter'])

    def resolve(month_day, base_year):
        month, day = month_day
        # Дата до сентября относится к следующему календарному году:
        # II четверть заканчивается в марте 2027-го, а не 2026-го.
        year = base_year if month >= 9 else base_year + 1
        return date(year, month, day)

    return [(name,
             resolve(start, first_year),
             resolve(end, first_year))
            for name, start, end in template]


# Значения, которые принимает ячейка журнала. Зачёт/незачёт лежат в одной
# строке с числами осознанно: вводить их можно вручную, а в среднем балле
# grade_to_points() переводит их в 5 и 2 — так они учитываются во всех
# отчётах проекта одинаково.
GRADE_VALUES = ('2', '3', '4', '5', 'зачет', 'незачет')

# Потолок на количество оценок в одной ячейке. Обычная ячейка — одна-две
# оценки, десять — уже предел осмысленного; без лимита строка из 300 цифр
# создала бы 300 Grade за одно занятие.
GRADE_VALUES_MAX_COUNT = 10


def parse_grade_values(raw):
    """Разбирает строку оценок из журнала: '5, 4, 4' -> ['5', '4', '4'].

    Разделители — запятая, точка с запятой, пробел, дефис. Ведущие нули
    отбрасываются: «05» -> «5». Синонимы приводятся к значениям из БД:
    «зачёт/з» -> «зачет», «незачёт/н» -> «незачет».

    Дробных значений нет намеренно: запятая уже занята разделителем, и
    «4,5» неизбежно распалось бы на «4» и «5» — две оценки вместо одной.

    Неизвестные значения молча выбрасывать нельзя: преподаватель вводит
    «5, 6, 4», получает две сохранённые оценки и не понимает, что третью
    потерял. Поэтому поднимается ValueError с перечнем мусора — это
    требование 8.6. Больше GRADE_VALUES_MAX_COUNT значений подряд тоже
    ошибка, иначе один промах мыши превращается в сотни оценок за занятие.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(part).strip() for part in raw]
    else:
        parts = re.split(r'[,;\s\-–—]+', str(raw).strip())

    values = []
    invalid = []
    for part in parts:
        if not part:
            continue
        if re.fullmatch(r'\d+', part):
            part = str(int(part))
        else:
            lowered = part.lower()
            part = {'зачёт': 'зачет', 'з': 'зачет',
                    'н': 'незачет', 'незачёт': 'незачет'}.get(lowered, lowered)
        if part in GRADE_VALUES:
            values.append(part)
        else:
            invalid.append(part)

    if invalid:
        raise ValueError(
            f'не распознано: {", ".join(invalid)}. Допустимо: 2, 3, 4, 5, '
            f'зачет, незачет')
    if len(values) > GRADE_VALUES_MAX_COUNT:
        raise ValueError(
            f'слишком много оценок: {len(values)}, максимум '
            f'{GRADE_VALUES_MAX_COUNT} за одно занятие')
    return values


def grade_distribution(grades):
    """Сколько оценок каждого вида: {'5': 3, '4': 7, '2': 1, ...}.

    Зачёт и незачёт считаются отдельными значениями, а не приводятся к
    баллам: в сводке преподавателю нужно видеть, сколько было именно
    зачётов, а не сколько из них «пятёрок».
    """
    counts = {}
    for item in grades or ():
        value = str(getattr(item, 'grade_value', item) or '').strip().lower()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def journal_totals(cells_by_student):
    """Средний балл и число оценок по каждому студенту.

    cells_by_student: {student_id: [Grade, ...]} — ровно та структура,
    которую journal отдаёт в ctx['cells'] после разворачивания в строки.
    """
    totals = {}
    for student_id, grades in (cells_by_student or {}).items():
        totals[student_id] = {
            'average': average_grade(grades),
            'count': len(grades or []),
            'distribution': grade_distribution(grades),
        }
    return totals


# Пороги рекомендации итоговой оценки. Правило округления среднего балла
# задаётся учебным заведением, и «правильного» варианта здесь нет —
# важно, чтобы оно было записано в одном месте и не менялось по дороге.
# По умолчанию: 4,5 → «5», 3,75 → «4», 2,5 → «3», ниже → «2».
FINAL_GRADE_THRESHOLDS = ((4.5, '5'), (3.75, '4'), (2.5, '3'))


def recommend_final_grade(grades):
    """Рекомендованная итоговая оценка по списку оценок.

    Возвращает '' , если оценок нет: без оценок итог не рекомендуется, иначе
    показатель «3» у студента, которому ещё ничего не поставили, выглядел бы
    как недопуск.

    Если все оценки — зачёт/незачёт, рекомендуется та же шкала, а не числа:
    студент, у которого три зачёта, должен получить «зачёт», а не «5».
    """
    values = [str(getattr(item, 'grade_value', item) or '').strip().lower()
              for item in grades or ()]
    values = [value for value in values if value]
    if not values:
        return ''

    if all(value in NON_NUMERIC_GRADES for value in values):
        # В этой шкале больше низкой оценки нет: любой незачёт — это «незачёт»
        return 'незачет' if 'незачет' in values else 'зачет'

    average = average_grade(values)
    if not average:
        return ''
    for threshold, value in FINAL_GRADE_THRESHOLDS:
        if average >= threshold:
            return value
    return '2'


def grade_quality_stats(grades):
    """Качество и успеваемость по списку оценок.

    Проценты считаются по принятой в РФ терминологии:
      * качество — доля оценок 4 и 5 (знания усвоены);
      * успеваемость — доля оценок 3, 4 и 5 (студент не отстаёт).
    Двойка портит успеваемость, но не качество.
    """
    points = [grade_to_points(getattr(item, 'grade_value', item))
              for item in grades or ()]
    points = [value for value in points if value is not None]
    total = len(points)
    if not total:
        return {'count': 0, 'quality': 0, 'progress': 0}
    quality = sum(1 for value in points if value >= 4)
    progress = sum(1 for value in points if value >= 3)
    return {
        'count': total,
        'quality': round(quality / total * 100, 1),
        'progress': round(progress / total * 100, 1),
    }


def teacher_choices():
    """Список сотрудников, которым можно назначить предмет.

    Живёт здесь, а не в маршрутах, потому что нужна и формам: иначе forms
    пришлось бы импортировать routes, а тот, в свою очередь, forms.
    """
    from app.models import User
    query = User.query.filter(User.role.in_([User.ROLE_ADMIN, User.ROLE_TEACHER]))
    return [(0, '— не назначен —')] + [
        (user.id, f'{user.display_name} ({user.role_label})')
        for user in query.order_by(User.username).all()]


def group_choices():
    """Группы для выпадающих списков: [(id, название), ...]."""
    from app.models import Group
    return [(g.id, g.name) for g in Group.query.order_by(Group.name).all()]


def subject_choices():
    """Предметы для выпадающих списков: [(id, название), ...]."""
    from app.models import Subject
    return [(s.id, s.name) for s in Subject.query.order_by(Subject.name).all()]


def calculate_age(birth_date):
    """Вычисление возраста"""
    if birth_date:
        today = datetime.now().date()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        return age
    return None

# Остальные функции (без изменений)
def create_backup(description=''):
    """Создание полной резервной копии: база + метаданные.

    Параметры backup_type и include_files убраны. Тип копии попадал только
    в имя файла — внутри архива всегда была вся база, поэтому «частичную»
    копию нельзя было восстановить (restore_backup возвращает только
    college.db). include_files ссылался на папку uploads, которую никто
    никогда не заполнял.
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = get_backup_dir()

    backup_filename = f'backup_{timestamp}_full.backup'
    backup_path = os.path.join(backup_dir, backup_filename)
    
    db_path = get_db_path()
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f'Файл базы данных не найден: {db_path}')

    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(db_path, 'college.db')
        metadata = {
            'backup_type': 'full',
            'created_at': datetime.now().isoformat(),
            'description': description,
            'app_version': '1.0'
        }
        zipf.writestr('metadata.json',
                      json.dumps(metadata, indent=2, ensure_ascii=False))
    
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
            'size': os.path.getsize(full_path),
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
        # Пишем именно в тот файл, который открыт приложением (get_db_path),
        # а не в «college.db» рядом с ним: при DATABASE_URL, указывающем на
        # другое имя, восстановление уходило в соседний файл, и данные
        # «восстанавливались» в никуда.
        db_dir = os.path.dirname(db_path)
        target = os.path.abspath(db_path)
        if os.path.commonpath([target, db_dir]) != db_dir:
            raise ValueError('Некорректный путь внутри архива')

        with zipf.open('college.db') as src, open(target, 'wb') as dst:
            shutil.copyfileobj(src, dst)

    # Файл базы подменён, а SQLite продолжает держать старый открытый файл
    # и кеш страниц: без переоткрытия соединения приложение до перезапуска
    # читало бы прежние данные и «восстановление» выглядело бы сломанным.
    try:
        from app.models import db
        db.session.remove()
        db.engine.dispose()
    except Exception as exc:  # pragma: no cover - только при отладке
        print(f'Не удалось переоткрыть соединение с базой: {exc}')

    return True

def _resolve_columns(columns, aliases):
    """Сопоставляет колонки файла с полями по синонимам.

    Возвращает {поле: индекс}. Колонки сопоставляются без учёта регистра и
    лишних пробелов, чтобы файл, выгруженный самим приложением, и файл,
    заполненный вручную, читались одинаково.
    """
    lookup = {str(c).strip().lower(): i for i, c in enumerate(columns)}
    resolved = {}
    for field, names in aliases.items():
        for name in names:
            if name in lookup:
                resolved[field] = lookup[name]
                break
    return resolved


def _cell(row, index, default=''):
    """Значение ячейки как строка.

    Важно: pandas читает колонку, где почти все значения — числа, а одна
    ячейка пуста, как float. Тогда номер зачётки '9900001' превратился бы в
    '9900001.0'. Целые float приводим к int, чтобы идентификаторы и годы
    сохранялись в том виде, в каком их вводили.

    Обращение через iloc, а не row[index]: целочисленный ключ в Series
    pandas трактует как имя метки, и в новых версиях вместо ячейки вернулось
    бы пустое значение — молчаливый импорт пустых записей.
    """
    value = row.iloc[index]
    if value is None:
        return default
    if isinstance(value, float):
        if pd.isna(value):
            return default
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    return text or default


def _parse_date(value):
    """Дата из '25.09.2026', '2026-09-25' или datetime/date. None если не разобрать."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, 'year') and hasattr(value, 'day'):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(text, dayfirst=True, errors='raise')
        return parsed.date()
    except (ValueError, TypeError):
        return None


def _split_full_name(full_name):
    """'Иванов Иван Иванович' -> ('Иванов', 'Иван', 'Иванович')."""
    parts = (full_name or '').split()
    if not parts:
        return '', '', ''
    if len(parts) == 1:
        return parts[0], '', ''
    return parts[0], parts[1], ' '.join(parts[2:])


STUDENT_ALIASES = {
    'student_id': ('номер зачетки', 'номер зачётки', '№ зачетки', 'student_id',
                   'номер студента', 'зачетка', 'зачётка'),
    'last_name': ('фамилия', 'last_name'),
    'first_name': ('имя', 'first_name'),
    'patronymic': ('отчество', 'patronymic'),
    'full_name': ('фио', 'фио студента', 'full_name', 'студент'),
    'group': ('группа', 'group'),
    'birth_date': ('дата рождения', 'birth_date', 'рождение'),
    'email': ('email', 'почта', 'эл. почта', 'электронная почта'),
    'phone': ('телефон', 'phone'),
    'gender': ('пол', 'gender'),
    'status': ('статус', 'status'),
}

GROUP_ALIASES = {
    'name': ('название', 'имя группы', 'name', 'группа'),
    'specialty': ('специальность', 'специалитет', 'specialty'),
    'year': ('год', 'год поступления', 'year', 'курс'),
}

GRADE_ALIASES = {
    'student_id': ('номер зачетки', 'номер зачётки', 'student_id', 'зачетка', 'зачётка'),
    'student': ('студент', 'фио', 'фио студента'),
    'subject': ('предмет', 'дисциплина', 'subject'),
    'grade_value': ('оценка', 'балл', 'grade_value', 'значение'),
    'grade_type': ('тип оценки', 'тип', 'grade_type'),
    'date': ('дата', 'дата оценки', 'date'),
    'comments': ('комментарий', 'комментарии', 'примечание', 'comments'),
}

SETTINGS_ALIASES = {
    'college_name': ('название колледжа', 'колледж', 'college_name'),
    'academic_year': ('учебный год', 'год', 'academic_year'),
    'max_students_per_group': ('максимум студентов в группе', 'max_students_per_group'),
    'items_per_page': ('элементов на странице', 'items_per_page'),
    'theme_color': ('цветовая тема', 'тема', 'theme_color'),
    'export_format': ('формат экспорта', 'export_format'),
}


def import_students(df, import_mode, errors):
    """Импорт студентов. Возвращает число добавленных/обновлённых записей."""
    from app.models import Student, Group, db

    cols = _resolve_columns(df.columns, STUDENT_ALIASES)
    if 'student_id' not in cols and 'full_name' not in cols:
        raise ValueError('Не найдена колонка с номером зачётки или ФИО. '
                         'Ожидаются «Номер зачетки» и/или «ФИО».')

    if import_mode == 'replace':
        # Оценки удаляются каскадом вместе со студентом
        # (Student.grades — cascade='all, delete-orphan'). Явное удаление
        # оценок сверх каскада давало повторный DELETE по уже пустым строкам
        # и предупреждение SQLAlchemy о несовпадении числа строк.
        for s in Student.query.all():
            db.session.delete(s)
        db.session.flush()

    groups_cache = {g.name.strip().lower(): g for g in Group.query.all()}
    touched = 0

    for number, row in df.iterrows():
        line = int(number) + 2  # +1 — заголовок, +1 — нумерация с единицы
        try:
            student_id = _cell(row, cols['student_id']) if 'student_id' in cols else ''
            full_name = _cell(row, cols['full_name']) if 'full_name' in cols else ''
            last_name = _cell(row, cols['last_name']) if 'last_name' in cols else ''
            first_name = _cell(row, cols['first_name']) if 'first_name' in cols else ''
            patronymic = _cell(row, cols['patronymic']) if 'patronymic' in cols else ''

            if not last_name and full_name:
                last_name, first_name, patronymic = _split_full_name(full_name)
            if not last_name:
                raise ValueError('не указана фамилия')
            if not student_id:
                raise ValueError('не указан номер зачётки')

            group = None
            if 'group' in cols and _cell(row, cols['group']):
                group_name = _cell(row, cols['group'])
                group = groups_cache.get(group_name.lower())
                if group is None:
                    group = Group(name=group_name)
                    db.session.add(group)
                    db.session.flush()
                    groups_cache[group_name.lower()] = group

            gender = _cell(row, cols['gender']) if 'gender' in cols else ''
            if gender:
                gender = gender[0].upper()
                if gender not in ('M', 'F'):
                    gender = 'M'
            else:
                gender = 'M'

            values = {
                'last_name': last_name,
                'first_name': first_name or '-',
                'patronymic': patronymic,
                'group': group,
                'birth_date': _parse_date(_cell(row, cols['birth_date']))
                if 'birth_date' in cols else None,
                'email': _cell(row, cols['email']) if 'email' in cols else '',
                'phone': _cell(row, cols['phone']) if 'phone' in cols else '',
                'gender': gender,
                'status': (_cell(row, cols['status']) if 'status' in cols else '') or 'active',
            }

            student = Student.query.filter_by(student_id=str(student_id).strip()).first()
            if student is None:
                student = Student(student_id=str(student_id).strip())
                db.session.add(student)
            for field, value in values.items():
                setattr(student, field, value)
            touched += 1
        except ValueError as e:
            errors.append(f'Строка {line}: {e}')
        except Exception as e:
            db.session.rollback()
            errors.append(f'Строка {line}: {type(e).__name__}: {e}')

    return touched


def import_groups(df, import_mode, errors):
    """Импорт групп."""
    from app.models import Group, db

    cols = _resolve_columns(df.columns, GROUP_ALIASES)
    if 'name' not in cols:
        raise ValueError('Не найдена колонка с названием группы.')

    if import_mode == 'replace':
        for g in Group.query.all():
            db.session.delete(g)
        db.session.flush()

    touched = 0
    for number, row in df.iterrows():
        line = int(number) + 2
        try:
            name = _cell(row, cols['name'])
            if not name:
                raise ValueError('не указано название группы')
            year_text = _cell(row, cols['year']) if 'year' in cols else ''
            year = int(re.sub(r'\D', '', year_text)[:4]) if re.sub(r'\D', '', year_text) else None

            group = Group.query.filter_by(name=name).first()
            if group is None:
                group = Group(name=name)
                db.session.add(group)
            group.specialty = (_cell(row, cols['specialty'])
                              if 'specialty' in cols else '') or None
            if year:
                group.year = year
            touched += 1
        except ValueError as e:
            errors.append(f'Строка {line}: {e}')
        except Exception as e:
            db.session.rollback()
            errors.append(f'Строка {line}: {type(e).__name__}: {e}')

    return touched


def import_grades(df, import_mode, errors):
    """Импорт оценок. Студент ищется по номеру зачётки, иначе по ФИО."""
    from app.models import Grade, Student, Subject, db

    cols = _resolve_columns(df.columns, GRADE_ALIASES)
    if 'grade_value' not in cols or 'subject' not in cols:
        raise ValueError('Нужны колонки «Предмет» и «Оценка» (или «Номер зачётки»).')

    if import_mode == 'replace':
        for g in Grade.query.all():
            db.session.delete(g)
        db.session.flush()

    students_cache = {}
    for s in Student.query.all():
        students_cache[s.student_id] = s
        students_cache[s.full_name.lower()] = s
        students_cache[' '.join(s.full_name.split()).lower()] = s

    subjects_cache = {sub.name.strip().lower(): sub for sub in Subject.query.all()}
    touched = 0

    for number, row in df.iterrows():
        line = int(number) + 2
        try:
            subject_name = _cell(row, cols['subject'])
            if not subject_name:
                raise ValueError('не указан предмет')
            subject = subjects_cache.get(subject_name.lower())
            if subject is None:
                raise ValueError(f'предмет «{subject_name}» не найден в базе')

            key = ''
            if 'student_id' in cols and _cell(row, cols['student_id']):
                key = _cell(row, cols['student_id'])
            elif 'student' in cols and _cell(row, cols['student']):
                key = _cell(row, cols['student'])
            if not key:
                raise ValueError('не указан студент')

            student = students_cache.get(key) or students_cache.get(key.lower())
            if student is None:
                raise ValueError(f'студент «{key}» не найден в базе')

            raw_value = _cell(row, cols['grade_value'])
            if grade_to_points(raw_value) is None:
                raise ValueError(f'некорректное значение оценки «{raw_value}»')

            grade_type = (_cell(row, cols['grade_type'])
                          if 'grade_type' in cols else '') or 'exam'

            grade = Grade(
                student_id=student.id,
                subject_id=subject.id,
                grade_value=str(raw_value),
                grade_type=grade_type,
                date=_parse_date(_cell(row, cols['date'])) if 'date' in cols else None,
                comments=(_cell(row, cols['comments'])
                          if 'comments' in cols else '') or None
            )
            db.session.add(grade)
            touched += 1
        except ValueError as e:
            errors.append(f'Строка {line}: {e}')
        except Exception as e:
            db.session.rollback()
            errors.append(f'Строка {line}: {type(e).__name__}: {e}')

    return touched


def import_settings(df, import_mode, errors):
    """Импорт настроек: берётся первая строка файла."""
    from app.models import SystemSettings

    if df.empty:
        raise ValueError('Файл пуст.')

    cols = _resolve_columns(df.columns, SETTINGS_ALIASES)
    if not cols:
        raise ValueError('Не найдено ни одного поля настроек. Ожидаются '
                         '«Название колледжа», «Учебный год» и другие.')

    row = df.iloc[0]
    settings = SystemSettings.get_settings()
    if import_mode == 'replace':
        settings = SystemSettings.reset_to_default()

    for field, index in cols.items():
        raw = _cell(row, index)
        current = getattr(settings, field, None)
        try:
            if isinstance(current, bool):
                setattr(settings, field, raw.lower() in ('1', 'true', 'да', 'yes', 'on'))
            elif isinstance(current, int):
                setattr(settings, field, int(re.sub(r'\D', '', raw) or current))
            elif raw:
                setattr(settings, field, raw)
        except (TypeError, ValueError):
            errors.append(f'Поле «{field}»: значение «{raw}» не подходит, пропущено')

    return 1


IMPORTERS = {
    'students': import_students,
    'groups': import_groups,
    'grades': import_grades,
    'settings': import_settings,
}


def _count_valid_students(df):
    cols = _resolve_columns(df.columns, STUDENT_ALIASES)
    if 'student_id' not in cols and 'full_name' not in cols:
        raise ValueError('Не найдена колонка с номером зачётки или ФИО. '
                         'Ожидаются «Номер зачетки» и/или «ФИО».')
    valid = 0
    for _, row in df.iterrows():
        student_id = _cell(row, cols['student_id']) if 'student_id' in cols else ''
        last_name = _cell(row, cols['last_name']) if 'last_name' in cols else ''
        full_name = _cell(row, cols['full_name']) if 'full_name' in cols else ''
        if not last_name and full_name:
            last_name = _split_full_name(full_name)[0]
        if student_id and last_name:
            valid += 1
    return valid


def _count_valid_groups(df):
    cols = _resolve_columns(df.columns, GROUP_ALIASES)
    if 'name' not in cols:
        raise ValueError('Не найдена колонка с названием группы.')
    return sum(1 for _, row in df.iterrows() if _cell(row, cols['name']))


def _count_valid_grades(df):
    cols = _resolve_columns(df.columns, GRADE_ALIASES)
    if 'grade_value' not in cols or 'subject' not in cols:
        raise ValueError('Нужны колонки «Предмет» и «Оценка» (или «Номер зачётки»).')

    from app.models import Student, Subject

    students = set()
    for s in Student.query.all():
        students.add(s.student_id)
        students.add(s.full_name.lower())
    subjects = {sub.name.strip().lower() for sub in Subject.query.all()}

    valid = 0
    for _, row in df.iterrows():
        subject = _cell(row, cols['subject'])
        value = _cell(row, cols['grade_value'])
        key = _cell(row, cols['student_id']) if 'student_id' in cols else ''
        if not key and 'student' in cols:
            key = _cell(row, cols['student'])
        if (subject and subject.lower() in subjects and key
                and (key in students or key.lower() in students)
                and grade_to_points(value) is not None):
            valid += 1
    return valid


PRECHECKS = {
    'students': _count_valid_students,
    'groups': _count_valid_groups,
    'grades': _count_valid_grades,
}

IMPORT_TITLES = {
    'students': 'студенты',
    'groups': 'группы',
    'grades': 'оценки',
    'settings': 'настройки',
}


def _read_import_table(file, filename):
    """Читает загруженный файл в таблицу.

    CSV разбирается с автоопределением разделителя и кодировки. Русский
    Excel по умолчанию сохраняет CSV с точкой с запятой и в кодировке
    cp1251, а pandas с настройками по умолчанию такой файл читал как одну
    колонку, и импорт отвечал «не найдена колонка с номером зачётки» —
    то есть на нормальном файле из Excel.
    """
    lower = filename.lower()
    if lower.endswith(('.xlsx', '.xls')):
        return pd.read_excel(file)
    if not lower.endswith('.csv'):
        raise ValueError('Неподдерживаемый формат. Допустимы .xlsx, .xls, .csv')

    last_error = None
    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            if hasattr(file, 'stream'):
                file.stream.seek(0)
            return pd.read_csv(file, sep=None, engine='python',
                               encoding=encoding)
        except UnicodeDecodeError as error:
            last_error = error
    raise ValueError('Файл не читается: не удалось определить кодировку '
                     f'({last_error})')


def import_from_file(file, import_type='students', import_mode='append', **kwargs):
    """Импорт данных из Excel/CSV в базу.

    Ищет колонки по синонимам, проверяет каждую строку и возвращает отчёт
    с числом импортированных записей и списком ошибок. Часть строк может
    не пройти — валидные всё равно импортируются.

    Раньше функция только считала строки файла и ничего не записывала.
    """
    from app.init_ import db

    if import_type not in IMPORTERS:
        raise ValueError(f'Неизвестный тип импорта: {import_type}')

    filename = file.filename or ''
    try:
        df = _read_import_table(file, filename)
    finally:
        if hasattr(file, 'stream') and hasattr(file.stream, 'seek'):
            file.stream.seek(0)

    if df.empty:
        raise ValueError('Файл не содержит строк с данными.')

    errors = []
    touched = 0

    # В режиме замены сначала проверяем файл. Без этой проверки совершенно
    # неверный файл (перепутанные столбцы, чужие студенты) удалял бы текущие
    # данные и не импортировал бы ничего взамен.
    if import_mode == 'replace':
        precheck = PRECHECKS.get(import_type)
        if precheck is not None and precheck(df) == 0:
            raise ValueError(
                'В файле нет ни одной корректной строки, поэтому текущие данные '
                'не тронуты. Проверьте названия столбцов и значения.')

    try:
        touched = IMPORTERS[import_type](df, import_mode, errors)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    title = IMPORT_TITLES.get(import_type, 'данные')
    if not errors:
        message = f'Импортировано записей: {touched} ({title})'
    else:
        message = (f'Импортировано записей: {touched} ({title}), '
                   f'пропущено строк с ошибками: {len(errors)}')

    return {
        'count': touched,
        'skipped': len(errors),
        'type': import_type,
        'mode': import_mode,
        'columns': list(df.columns),
        'errors': errors[:100],
        'errors_total': len(errors),
        'message': message,
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


# Буквы и цифры, которые не путаются при чтении с распечатанной ведомости.
# «O» и «0», «l» и «1» выглядят одинаково, и студент с распечатки вводит не тот
# пароль — а потом идёт к администратору с ложным «пароль не подходит».
READABLE_PASSWORD_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'


def generate_temporary_password(length=10):
    """Пароль для выдачи на бумаге.

    Только читаемые символы, без 0/O и 1/l/I. Из generate_password не годится:
    там русский студент раз за разом путает O с нулём.
    """
    import random
    return ''.join(random.choice(READABLE_PASSWORD_CHARS) for _ in range(length))


def normalize_username(username):
    """Логин в виде, по которому его сравнивают.

    Регистр не различает: вход «ADMIN» и «admin» — одна и та же учётная
    запись, иначе в списке появятся два неотличимых пользователя.
    """
    return (username or '').strip()


def find_user_by_login(login):
    """Найти учётную запись по логину без учёта регистра.

    Сначала точное совпадение, затем перебор на Python. Так не приходится
    полагаться на lower() в SQLite: он умеет только латиницу, а Python
    lower() ещё и кириллицу, поэтому запрос lower(логин) = 'дб-01' не нашёл бы
    запись «ДБ-01» — и человек с верным паролем получал бы «неверный логин».
    Перебор дешёв: записей единицы, и он случается только когда точного
    совпадения нет.
    """
    from app.models import User
    name = normalize_username(login)
    if not name:
        return None
    user = User.query.filter(User.username == name).first()
    if user is not None:
        return user
    folded = name.casefold()
    for candidate in User.query.all():
        if normalize_username(candidate.username).casefold() == folded:
            return candidate
    return None


def username_taken(username, exclude_id=None):
    """Занят ли логин кем-то ещё, без учёта регистра.

    Проверка идёт по всей таблице User, а не только среди студентов: номер
    зачётки «admin» столкнётся с логином сотрудника, и такой студент
    останется без входа, хотя его карточка сохранилась. exclude_id нужен
    при правке существующей записи — иначе запись занимает свой же логин.
    """
    user = find_user_by_login(username)
    if user is None:
        return False
    return exclude_id is None or user.id != exclude_id

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