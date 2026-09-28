"""Обновление схемы существующей базы данных без затрагивания данных.

Запуск:  python migrations.py

В отличие от init_db.py ничего не заливает: только создаёт новые таблицы
и добавляет недостающие колонки. Скрипт идемпотентен — можно запускать повторно.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db

from config import load_env_file

load_env_file()

app = create_app()

# (таблица, колонка, DDL для SQLite)
COLUMNS = [
    ('grade', 'period_id', 'period_id INTEGER'),
    ('student', 'user_id', 'user_id INTEGER'),
    ('user', 'password_changed_at', 'password_changed_at DATETIME'),
    ('user', 'last_login_at', 'last_login_at DATETIME'),
    ('user', 'is_active', 'is_active BOOLEAN DEFAULT 1'),
]


def main():
    with app.app_context():
        print('--- Обновление схемы ---')
        db.create_all()
        print('✅ Новые таблицы созданы')

        added = []
        for table, column, ddl in COLUMNS:
            info = db.session.execute(db.text(f'PRAGMA table_info({table})')).fetchall()
            if not info:
                print(f'⚠️  Таблица {table} отсутствует — пропускаю {column}')
                continue
            if any(row[1] == column for row in info):
                continue
            db.session.execute(db.text(f'ALTER TABLE {table} ADD COLUMN {ddl}'))
            added.append(f'{table}.{column}')

        if added:
            db.session.commit()
            print(f'✅ Добавлены колонки: {", ".join(added)}')
        else:
            print('ℹ️  Схема уже актуальна, изменения не потребовались')

        print('--- Готово ---')


if __name__ == '__main__':
    main()
