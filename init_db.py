"""Создание базы данных, схемы и начальных данных.

Запуск:  python init_db.py

Порядок работы:
  1. создаются таблицы (db.create_all)
  2. недостающие таблицы и колонки добавляются миграцией (для существующей базы)
  3. создаётся администратор из ADMIN_USERNAME / ADMIN_PASSWORD в .env
  4. создаются системные настройки
  5. заливается демонстрационный набор: группы, предметы, студенты, оценки

Скрипт идемпотентен: повторный запуск не дублирует данные.
"""

import os
import sys
import random
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Консоль Windows по умолчанию cp1251 и не печатает эмодзи
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import User, Group, Student, Subject, Grade, SystemSettings

from config import load_env_file

load_env_file()

app = create_app()

DEFAULT_ADMIN_USERNAME = 'admin'
DEFAULT_ADMIN_PASSWORD = 'admin123'

GROUPS = [
    ('ИСП-204', 'Информационные системы и программирование', 2023),
    ('ПКС-101', 'Программное обеспечение', 2024),
    ('СПО-302', 'Сетевое и системное администрирование', 2022),
]

SUBJECTS = [
    ('Математика', 144),
    ('Физика', 108),
    ('Информатика', 180),
    ('Программирование', 216),
    ('Базы данных', 144),
    ('Веб-разработка', 180),
    ('Английский язык', 144),
    ('Экономика', 72),
]

MALE_FIRST = ['Иван', 'Алексей', 'Дмитрий', 'Максим', 'Артём', 'Кирилл', 'Никита', 'Егор']
FEMALE_FIRST = ['Мария', 'Анна', 'Ольга', 'Елена', 'Дарья', 'Полина', 'Ксения', 'Ирина']
MALE_PATRONYMIC = ['Иванович', 'Петрович', 'Сергеевич', 'Андреевич', 'Алексеевич']
FEMALE_PATRONYMIC = ['Ивановна', 'Петровна', 'Сергеевна', 'Андреевна', 'Алексеевна']
MALE_LAST = ['Иванов', 'Петров', 'Сидоров', 'Кузнецов', 'Смирнов', 'Волков', 'Морозов', 'Лебедев']
FEMALE_LAST = ['Иванова', 'Петрова', 'Сидорова', 'Кузнецова', 'Смирнова', 'Волкова', 'Морозова', 'Лебедева']


def add_column_if_missing(table, column, ddl):
    """Добавляет колонку в SQLite, если её ещё нет."""
    existing = db.session.execute(
        db.text(f"PRAGMA table_info({table})")
    ).fetchall()
    if not existing:
        return False
    if any(row[1] == column for row in existing):
        return False
    db.session.execute(db.text(f'ALTER TABLE {table} ADD COLUMN {ddl}'))
    return True


def ensure_admin():
    username = os.environ.get('ADMIN_USERNAME') or DEFAULT_ADMIN_USERNAME
    password = os.environ.get('ADMIN_PASSWORD') or DEFAULT_ADMIN_PASSWORD

    user = User.query.filter_by(username=username).first()
    if user:
        print(f'ℹ️  Администратор «{username}» уже существует')
        return

    user = User(username=username, role='admin')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    print(f'✅ Администратор создан: {username}')


def ensure_settings():
    if SystemSettings.query.first():
        print('ℹ️  Системные настройки уже созданы')
        return
    db.session.add(SystemSettings())
    db.session.commit()
    print('✅ Системные настройки созданы')


def ensure_groups():
    if Group.query.first():
        print('ℹ️  Учебные группы уже созданы')
        return []
    groups = [Group(name=n, specialty=s, year=y) for n, s, y in GROUPS]
    db.session.add_all(groups)
    db.session.commit()
    print(f'✅ Создано групп: {len(groups)}')
    return groups


def ensure_subjects():
    if Subject.query.first():
        print('ℹ️  Предметы уже созданы')
        return []
    subjects = [Subject(name=n, hours=h) for n, h in SUBJECTS]
    db.session.add_all(subjects)
    db.session.commit()
    print(f'✅ Создано предметов: {len(subjects)}')
    return subjects


def ensure_students():
    if Student.query.first():
        print('ℹ️  Студенты уже созданы')
        return []
    groups = Group.query.order_by(Group.id).all()
    if not groups:
        print('⚠️  Нет групп — студенты не созданы')
        return []

    random.seed(42)
    students = []
    number = 1
    for group in groups:
        for _ in range(8):
            is_male = random.random() < 0.5
            last = random.choice(MALE_LAST if is_male else FEMALE_LAST)
            first = random.choice(MALE_FIRST if is_male else FEMALE_FIRST)
            patronymic = random.choice(MALE_PATRONYMIC if is_male else FEMALE_PATRONYMIC)
            birth = date(2002 + random.randint(0, 3), random.randint(1, 12), random.randint(1, 28))
            students.append(Student(
                student_id=f'STD{number:04d}',
                last_name=last,
                first_name=first,
                patronymic=patronymic,
                gender='M' if is_male else 'F',
                birth_date=birth,
                email=f'student{number}@college.edu',
                phone=f'+7999{random.randint(1000000, 9999999)}',
                group_id=group.id,
                status='active',
                enrollment_date=date(group.year, 9, 1),
            ))
            number += 1

    db.session.add_all(students)
    db.session.commit()
    print(f'✅ Создано студентов: {len(students)}')
    return students


def ensure_grades():
    if Grade.query.first():
        print('ℹ️  Оценки уже созданы')
        return
    students = Student.query.all()
    subjects = Subject.query.all()
    if not students or not subjects:
        print('⚠️  Нет студентов или предметов — оценки не созданы')
        return

    random.seed(7)
    today = date.today()
    grades = []
    for student in students:
        for subject in random.sample(subjects, k=random.randint(4, 7)):
            for _ in range(random.randint(1, 4)):
                weights = ['5'] * 3 + ['4'] * 4 + ['3'] * 2 + ['2']
                grades.append(Grade(
                    student_id=student.id,
                    subject_id=subject.id,
                    grade_value=random.choice(weights),
                    grade_type=random.choice(['exam', 'test', 'lab', 'practice', 'homework']),
                    date=today - timedelta(days=random.randint(0, 90)),
                ))

    db.session.add_all(grades)
    db.session.commit()
    print(f'✅ Создано оценок: {len(grades)}')


def main():
    with app.app_context():
        print('--- Создание базы данных ---')
        db.create_all()
        print('✅ Таблицы созданы')

        added = []
        for table, column, ddl in [
            ('grade', 'period_id', 'period_id INTEGER'),
            ('student', 'user_id', 'user_id INTEGER'),
            ('user', 'password_changed_at', 'password_changed_at DATETIME'),
            ('user', 'last_login_at', 'last_login_at DATETIME'),
            ('user', 'is_active', 'is_active BOOLEAN DEFAULT 1'),
        ]:
            if add_column_if_missing(table, column, ddl):
                added.append(f'{table}.{column}')
        if added:
            db.session.commit()
            print(f'✅ Миграция: добавлены колонки {", ".join(added)}')

        ensure_admin()
        ensure_settings()
        ensure_groups()
        ensure_subjects()
        ensure_students()
        ensure_grades()

        print('\n--- Готово ---')
        print(f'Студентов: {Student.query.count()}, групп: {Group.query.count()}, '
              f'предметов: {Subject.query.count()}, оценок: {Grade.query.count()}')
        username = os.environ.get('ADMIN_USERNAME') or DEFAULT_ADMIN_USERNAME
        print(f'Вход: логин «{username}», пароль — из ADMIN_PASSWORD в .env')


if __name__ == '__main__':
    main()
