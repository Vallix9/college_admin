"""Создание базы данных, схемы и начальных данных.

Запуск:  python init_db.py

Порядок работы:
  1. создаются таблицы (db.create_all)
  2. недостающие таблицы и колонки добавляются миграцией (для существующей базы)
  3. создаётся администратор из ADMIN_USERNAME / ADMIN_PASSWORD в .env
  4. создаются системные настройки
  5. заливается демонстрационный набор: группы, предметы, преподаватели,
     учебные периоды, расписание, студенты (часть — с учётными записями),
     оценки с привязкой к периодам, пропуски

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
from app.models import (User, Group, Student, Subject, Grade, SystemSettings,
                        AcademicPeriod, ScheduleItem, AttendanceRecord,
                        LessonDate)
from migrations import apply_schema

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

# Преподаватели демонстрационной базы. Предметы распределяются между ними
# в ensure_subjects, иначе журнал нечем наполнять: у преподавателя не будет
# ни одного своего предмета.
TEACHERS = [
    ('teacher_smirnov', 'Смирнов Алексей Петрович'),
    ('teacher_kuznetsova', 'Кузнецова Мария Ивановна'),
    ('teacher_volkov', 'Волков Дмитрий Сергеевич'),
]

# Пароль демонстрационных учётных записей. Это сид, а не боевая база.
DEMO_STUDENT_PASSWORD = 'student123'

# Расписание: сколько пар в день у группы
LESSONS_PER_DAY = 6
SCHEDULE_DAYS = 5  # Пн–Пт
ROOMS = ['101', '102', '201', '203', '301', '305']

MALE_FIRST = ['Иван', 'Алексей', 'Дмитрий', 'Максим', 'Артём', 'Кирилл', 'Никита', 'Егор']
FEMALE_FIRST = ['Мария', 'Анна', 'Ольга', 'Елена', 'Дарья', 'Полина', 'Ксения', 'Ирина']
MALE_PATRONYMIC = ['Иванович', 'Петрович', 'Сергеевич', 'Андреевич', 'Алексеевич']
FEMALE_PATRONYMIC = ['Ивановна', 'Петровна', 'Сергеевна', 'Андреевна', 'Алексеевна']
MALE_LAST = ['Иванов', 'Петров', 'Сидоров', 'Кузнецов', 'Смирнов', 'Волков', 'Морозов', 'Лебедев']
FEMALE_LAST = ['Иванова', 'Петрова', 'Сидорова', 'Кузнецова', 'Смирнова', 'Волкова', 'Морозова', 'Лебедева']


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
    settings = SystemSettings.query.first()
    if settings:
        print('ℹ️  Системные настройки уже созданы')
        return settings
    settings = SystemSettings()
    db.session.add(settings)
    db.session.commit()
    print('✅ Системные настройки созданы')
    return settings


def ensure_groups():
    existing = Group.query.order_by(Group.id).all()
    if existing:
        print('ℹ️  Учебные группы уже созданы')
        return existing
    groups = [Group(name=n, specialty=s, year=y) for n, s, y in GROUPS]
    db.session.add_all(groups)
    db.session.commit()
    print(f'✅ Создано групп: {len(groups)}')
    return groups


def ensure_subjects():
    existing = Subject.query.order_by(Subject.id).all()
    if existing:
        print('ℹ️  Предметы уже созданы')
        return existing
    subjects = [Subject(name=n, hours=h) for n, h in SUBJECTS]
    db.session.add_all(subjects)
    db.session.commit()
    print(f'✅ Создано предметов: {len(subjects)}')
    return subjects


def ensure_students():
    existing = Student.query.order_by(Student.id).all()
    if existing:
        print('ℹ️  Студенты уже созданы')
        return existing
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


def current_academic_year():
    """Учебный год по текущей дате: с сентября начинается новый.

    В сентябре–январе год ещё текущий, с февраля начинается следующий.
    """
    today = date.today()
    start_year = today.year if today.month >= 9 else today.year - 1
    return f'{start_year}-{start_year + 1}'


def academic_year_start(year_label):
    return date(int(year_label.split('-')[0]), 9, 1)


def ensure_teachers():
    """Преподаватели демонстрационной базы."""
    created = 0
    for username, full_name in TEACHERS:
        if User.query.filter_by(username=username).first():
            continue
        teacher = User(username=username, full_name=full_name,
                       role=User.ROLE_TEACHER, is_active=True)
        teacher.set_password(DEMO_STUDENT_PASSWORD)
        db.session.add(teacher)
        created += 1
    if created:
        db.session.commit()
        print(f'✅ Создано преподавателей: {created}')
    else:
        print('ℹ️  Преподаватели уже созданы')
    return User.query.filter_by(role=User.ROLE_TEACHER).order_by(User.id).all()


def ensure_periods(year_label):
    """Четыре четверти учебного года.

    Границы считаются от начала года с равномерными 90-дневными отрезками,
    последняя четверть добирает остаток до 31 июля.
    """
    if AcademicPeriod.query.filter_by(academic_year=year_label).first():
        print('ℹ️  Учебные периоды уже созданы')
        return []

    start = academic_year_start(year_label)
    year_end = date(start.year + 1, 7, 31)
    names = ['I четверть', 'II четверть', 'III четверть', 'IV четверть']

    periods = []
    cursor = start
    for index, name in enumerate(names, start=1):
        end = min(cursor + timedelta(days=89), year_end)
        periods.append(AcademicPeriod(
            name=name, academic_year=year_label, start_date=cursor,
            end_date=end, sort_order=index, is_annual=False,
            kind=AcademicPeriod.KIND_QUARTER))
        cursor = end + timedelta(days=1)

    db.session.add_all(periods)
    db.session.commit()
    print(f'✅ Создано учебных периодов: {len(periods)} ({year_label})')
    return periods


def assign_subjects_to_teachers(teachers):
    """Распределяет предметы между преподавателями по кругу."""
    if not teachers:
        return
    subjects = Subject.query.order_by(Subject.id).all()
    assigned = 0
    for index, subject in enumerate(subjects):
        if subject.teacher_id:
            continue
        subject.teacher_id = teachers[index % len(teachers)].id
        assigned += 1
    if assigned:
        db.session.commit()
        print(f'✅ Закреплено предметов за преподавателями: {assigned}')
    else:
        print('ℹ️  Все предметы уже закреплены')


def ensure_lesson_dates():
    """Отмечает состоявшиеся занятия текущего периода.

    Без них сетка журнала Фазы 8 не знает, какие даты рабочие, и период
    выглядит пустым. Отмечаем будни от начала периода до сегодняшнего дня —
    это правдоподобно: учебный год в сентябре, до занятий ещё не все даты.
    """
    if LessonDate.query.first():
        print('ℹ️  Отметки о состоявшихся занятиях уже созданы')
        return

    period = AcademicPeriod.query.filter(
        AcademicPeriod.start_date <= date.today(),
        AcademicPeriod.end_date >= date.today()).order_by(
        AcademicPeriod.sort_order).first()
    if period is None:
        print('ℹ️  Текущего периода нет — отметки не созданы')
        return

    # Ограничиваем окно 45 днями: за год четверти набегает 600+ занятий на
    # 24 студента, и сид перестаёт быть обозримым. Журнал Фазы 8 всё равно
    # показывает месяц, а не весь период.
    start = max(period.start_date, date.today() - timedelta(days=45))
    items = ScheduleItem.query.all()
    marked = 0
    day = start
    while day <= min(period.end_date, date.today()):
        weekday = day.weekday() + 1
        for item in items:
            if item.day_of_week == weekday:
                db.session.add(LessonDate(schedule_item_id=item.id, date=day))
                marked += 1
        day += timedelta(days=1)

    db.session.commit()
    print(f'✅ Отмечено состоявшихся занятий: {marked} '
          f'({period.name}, с {start.strftime("%d.%m.%Y")})')


def ensure_schedule(groups, subjects):
    """Заполняет недельное расписание для каждой группы.

    Расписание строится по слотам (день, пара): в одном слоте у преподавателя
    может быть только одна группа. Иначе демонстрационная база была бы полна
    невозможных ситуаций — один преподаватель в три группы одновременно —
    и проверка конфликтов Фазы 7 не нашла бы, что проверять.
    """
    if ScheduleItem.query.first():
        print('ℹ️  Расписание уже создано')
        return

    random.seed(11)
    # Предметы сгруппированы по преподавателю: в одном слоте берём по
    # предмету от разных преподавателей
    by_teacher = {}
    for subject in subjects:
        if subject.teacher_id:
            by_teacher.setdefault(subject.teacher_id, []).append(subject)

    items = []
    conflicts = 0
    for day in range(1, SCHEDULE_DAYS + 1):
        for lesson in range(1, LESSONS_PER_DAY + 1):
            # Кто ведёт в этом слоте: перемешиваем и берём без повторов
            slot_teachers = list(by_teacher)
            random.shuffle(slot_teachers)

            # Группы, у которых в этот момент идёт пара
            in_session = [group for group in groups
                          if random.random() >= 0.15]
            random.shuffle(in_session)

            for group, teacher_id in zip(in_session, slot_teachers):
                options = by_teacher[teacher_id]
                subject = random.choice(options)
                items.append(ScheduleItem(
                    group_id=group.id, subject_id=subject.id,
                    day_of_week=day, lesson_number=lesson,
                    teacher_id=teacher_id,
                    room=random.choice(ROOMS)))

    # Проверяем результат: конфликтов быть не должно, иначе Фазе 7 не на
    # чем будет проверять свою проверку
    seen = {}
    for item in items:
        key = (item.teacher_id, item.day_of_week, item.lesson_number)
        if key in seen:
            conflicts += 1
        seen[key] = item.id

    db.session.add_all(items)
    db.session.commit()
    print(f'✅ Создано занятий в расписании: {len(items)}'
          f' (конфликтов преподавателей: {conflicts})')


def ensure_student_accounts(students, limit=6):
    """Выдаёт учётные записи части студентов.

    Логином служит номер зачётки, пароль у всех демонстрационный один.
    Остальные студенты остаются без аккаунта — как в реальной базе до
    выдачи логинов деканатом.
    """
    # limit — это сколько аккаунтов должно быть ВСЕГО, а не сколько за
    # один запуск. Иначе повторный запуск выдавал бы следующей шестерке
    # и демонстрационная база разрасталась бы.
    have = Student.query.filter(Student.user_id.isnot(None)).count()
    if have >= limit:
        print(f'ℹ️  Учётные записи уже созданы: {have}')
        return

    created = 0
    for student in students:
        if have + created >= limit:
            break
        if student.user_id:
            continue
        if User.query.filter_by(username=student.student_id).first():
            continue
        account = User(username=student.student_id, full_name=student.full_name,
                       role=User.ROLE_STUDENT, is_active=True)
        account.set_password(DEMO_STUDENT_PASSWORD)
        db.session.add(account)
        db.session.flush()
        student.user_id = account.id
        created += 1
    if created:
        db.session.commit()
        print(f'✅ Создано учётных записей студентов: {created} (всего {have + created})')
    else:
        print('ℹ️  Новые учётные записи не созданы')


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
    periods = (AcademicPeriod.query
               .order_by(AcademicPeriod.sort_order).all())
    # Даты берём из самих периодов, а не «последние 90 дней»: иначе часть
    # оценок оказалась бы до начала учебного года и осталась без периода,
    # а журнал Фазы 8 пришлось бы проверять на пустой сетке.
    past_periods = [p for p in periods if p.start_date <= today]
    if not past_periods:
        print('⚠️  Нет начавшихся периодов — оценки не созданы')
        return

    grades = []
    for student in students:
        for subject in random.sample(subjects, k=random.randint(4, 7)):
            for _ in range(random.randint(1, 4)):
                weights = ['5'] * 3 + ['4'] * 4 + ['3'] * 2 + ['2']
                # Текущий период берём чаще — на него смотрят чаще всего
                period = (random.choice(past_periods[-2:])
                          if random.random() < 0.7
                          else random.choice(past_periods))
                last_day = min(period.end_date, today)
                span = (last_day - period.start_date).days
                grade_date = (period.start_date
                              + timedelta(days=random.randint(0, max(span, 0))))
                grades.append(Grade(
                    student_id=student.id,
                    subject_id=subject.id,
                    grade_value=random.choice(weights),
                    grade_type=random.choice(['exam', 'test', 'lab', 'practice', 'homework']),
                    date=grade_date,
                    period_id=period.id,
                ))

    db.session.add_all(grades)
    db.session.commit()
    with_period = sum(1 for grade in grades if grade.period_id)
    print(f'✅ Создано оценок: {len(grades)} (с периодом: {with_period})')


def ensure_attendance(subjects):
    """Пропуски за текущий период. Нужны, чтобы отчёт Фазы 12 не был пустым."""
    if AttendanceRecord.query.first():
        print('ℹ️  Пропуски уже созданы')
        return

    random.seed(23)
    students = Student.query.filter_by(status='active').all()
    if not students:
        return

    # Окно пропусков — от начала текущего периода до сегодняшнего дня
    current = (AcademicPeriod.query.filter(AcademicPeriod.start_date <= date.today())
               .order_by(AcademicPeriod.sort_order.desc()).first())
    if current is None:
        print('⚠️  Нет начавшихся периодов — пропуски не созданы')
        return
    span = max((date.today() - current.start_date).days, 0)

    reasons = [AttendanceRecord.REASON_ILLNESS,
               AttendanceRecord.REASON_EXCUSED,
               AttendanceRecord.REASON_UNEXCUSED]
    records = []
    today = date.today()
    for student in students:
        for _ in range(random.randint(0, 4)):
            day = today - timedelta(days=random.randint(0, span))
            # Пропуск по конкретной паре или на весь день
            subject = random.choice(subjects) if random.random() < 0.7 else None
            records.append(AttendanceRecord(
                student_id=student.id, date=day,
                reason=random.choice(reasons),
                subject_id=subject.id if subject else None,
                note=None,
            ))

    db.session.add_all(records)
    db.session.commit()
    print(f'✅ Создано пропусков: {len(records)} ({current.name})')


def main():
    with app.app_context():
        print('--- Создание базы данных ---')
        # Схему обновляет migrations: список колонок и индексов живёт там,
        # здесь он бы со временем разошёлся с моделями
        apply_schema()

        ensure_admin()
        settings = ensure_settings()

        groups = ensure_groups()
        subjects = ensure_subjects()
        teachers = ensure_teachers()
        assign_subjects_to_teachers(teachers)

        year_label = current_academic_year()
        settings.academic_year = year_label
        db.session.commit()

        ensure_periods(year_label)
        ensure_schedule(groups, subjects)

        students = ensure_students()
        ensure_student_accounts(students)
        ensure_lesson_dates()
        ensure_grades()
        ensure_attendance(subjects)

        print('\n--- Готово ---')
        print(f'Групп: {Group.query.count()}, предметов: {Subject.query.count()}, '
              f'преподавателей: {len(teachers)}')
        print(f'Периодов: {AcademicPeriod.query.count()}, '
              f'занятий: {ScheduleItem.query.count()}')
        print(f'Студентов: {Student.query.count()}, оценок: {Grade.query.count()}, '
              f'пропусков: {AttendanceRecord.query.count()}, '
              f'отметок о занятиях: {LessonDate.query.count()}')
        with_accounts = Student.query.filter(Student.user_id.isnot(None)).count()
        print(f'С учётными записями: {with_accounts}')
        username = os.environ.get('ADMIN_USERNAME') or DEFAULT_ADMIN_USERNAME
        print(f'Вход: логин «{username}», пароль — из ADMIN_PASSWORD в .env')


if __name__ == '__main__':
    main()
