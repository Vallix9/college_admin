"""Обновление схемы существующей базы данных без затрагивания данных.

Запуск:  python migrations.py

В отличие от init_db.py ничего не заливает: только создаёт новые таблицы
и добавляет недостающие колонки. Скрипт идемпотентен — можно запускать повторно.

Схема описана здесь в одном месте, и init_db.py вызывает ту же
apply_schema(), чтобы списки колонок не разъезжались между скриптами.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db

from config import load_env_file

load_env_file()

# (таблица, колонка, DDL для SQLite)
COLUMNS = [
    ('grade', 'period_id', 'period_id INTEGER'),
    ('student', 'user_id', 'user_id INTEGER'),
    ('user', 'password_changed_at', 'password_changed_at DATETIME'),
    ('user', 'last_login_at', 'last_login_at DATETIME'),
    ('user', 'is_active', 'is_active BOOLEAN DEFAULT 1'),
    # Фаза 5: роли, учётные записи сотрудников и назначение предметов
    ('user', 'created_by', 'created_by INTEGER'),
    ('user', 'full_name', 'full_name VARCHAR(200)'),
    ('user', 'email', 'email VARCHAR(100)'),
    ('subject', 'teacher_id', 'teacher_id INTEGER'),
    # Фаза 7: вид периодов (четверти или семестры) и отметки о занятиях
    ('system_settings', 'period_kind',
     "period_kind VARCHAR(20) DEFAULT 'quarter' NOT NULL"),
]

# Индексы и ограничения, которые create_all() не добавит к уже существующей
# таблице: он такие таблицы пропускает целиком. Для grade это единственный
# способ получить индексы — таблица появилась в Фазе 0 без них.
INDEXES = [
    ('ix_grade_subject_date', 'grade', '(subject_id, date)'),
    ('ix_grade_student_subject', 'grade', '(student_id, subject_id)'),
    ('ix_attendance_student_date', 'attendance_record', '(student_id, date)'),
    ('ix_grade_history_grade', 'grade_history', '(grade_id)'),
    # Журнал выбирает рабочие даты одним запросом и сортирует их
    ('ix_lesson_date_date', 'lesson_date', '(date)'),
]

UNIQUE_INDEXES = [
    # Расписание: в одной ячейке (группа, день, пара) не может быть двух занятий
    ('uq_schedule_slot', 'schedule_item',
     '(group_id, day_of_week, lesson_number)'),
    # В учебном году порядок периодов не повторяется
    ('uq_period_year_order', 'academic_period', '(academic_year, sort_order)'),
    # Одно и то же занятие за одну дату отмечается только раз
    ('uq_lesson_date', 'lesson_date', '(schedule_item_id, date)'),
]


def table_exists(name):
    return bool(db.session.execute(
        db.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {'n': name}).fetchone())


def index_exists(name):
    return bool(db.session.execute(
        db.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"),
        {'n': name}).fetchone())


def apply_schema(verbose=True):
    """Приводит схему базы к актуальной. Работает в текущем app_context.

    Идемпотентна: повторный вызов ничего не меняет. Её же вызывает
    init_db.py, поэтому описание схемы живёт только здесь.
    """
    def say(message):
        if verbose:
            print(message)

    db.create_all()
    say('✅ Таблицы проверены/созданы')

    added = []
    for table, column, ddl in COLUMNS:
        info = db.session.execute(db.text(f'PRAGMA table_info({table})')).fetchall()
        if not info:
            say(f'⚠️  Таблица {table} отсутствует — пропускаю {column}')
            continue
        if any(row[1] == column for row in info):
            continue
        db.session.execute(db.text(f'ALTER TABLE {table} ADD COLUMN {ddl}'))
        added.append(f'{table}.{column}')

    if added:
        db.session.commit()
        say(f'✅ Добавлены колонки: {", ".join(added)}')
    else:
        say('ℹ️  Все колонки на месте')

    # Уникальные индексы. Сначала проверяем дубликаты: если строки уже
    # нарушают уникальность, создание индекса упадёт и вся миграция следом.
    unique_created = []
    for name, table, columns in UNIQUE_INDEXES:
        if not table_exists(table):
            say(f'⚠️  Таблица {table} отсутствует — пропускаю {name}')
            continue
        if index_exists(name):
            continue
        col_list = columns.strip('()').replace(' ', '').split(',')
        key = ', '.join(f'"{col}"' for col in col_list)
        duplicates = db.session.execute(db.text(
            f'SELECT {key}, COUNT(*) FROM "{table}" '
            f'GROUP BY {key} HAVING COUNT(*) > 1')).fetchall()
        if duplicates:
            say(f'⚠️  {table}: дубликаты в ({key}) — групп {len(duplicates)}. '
                f'Ограничение пропущено, разберитесь вручную.')
            continue
        db.session.execute(
            db.text(f'CREATE UNIQUE INDEX IF NOT EXISTS {name} '
                    f'ON "{table}" {columns}'))
        unique_created.append(name)

    created = []
    for name, table, columns in INDEXES:
        if not table_exists(table):
            say(f'⚠️  Таблица {table} отсутствует — пропускаю {name}')
            continue
        if index_exists(name):
            continue
        db.session.execute(
            db.text(f'CREATE INDEX IF NOT EXISTS {name} '
                    f'ON "{table}" {columns}'))
        created.append(name)

    if created or unique_created:
        db.session.commit()
    if created:
        say(f'✅ Созданы индексы: {", ".join(created)}')
    if unique_created:
        say(f'✅ Созданы уникальные индексы: {", ".join(unique_created)}')
    if not created and not unique_created:
        say('ℹ️  Индексы уже на месте')


def main():
    app = create_app()
    with app.app_context():
        print('--- Обновление схемы ---')
        apply_schema()
        print('--- Готово ---')


if __name__ == '__main__':
    main()
