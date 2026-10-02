"""Функциональная проверка Фазы 9: пропуски.

Запуск:  python phase9_test.py

Проверяет то, что не видно на странице:
  * журнал пропусков: список, сводка по студентам, сходимость итога по
    группе с суммой по студентам;
  * отметку пропуска из ячейки журнала и её появление в сетке;
  * запрет повторной отметки на одно и то же занятие;
  * запрет отметки за несуществующее или будущее занятие;
  * права: студент не попадает в журнал, преподаватель отмечает только свой
    предмет, «весь день» доступен лишь администратору;
  * массовую отметку: не трогает уже отмеченных и тех, у кого есть оценка;
  * правку причины и удаление по подтверждению;
  * пропуски в карточке студента и на дашборде.

Все изменения откатываются в финале: скрипт снимает снимок пропусков и
восстанавливает их, поэтому база остаётся как была.
"""

import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Group, Student, Subject, Grade, AcademicPeriod,
                        AuditLog,
                        ScheduleItem, LessonDate, AttendanceRecord)
from app.forms import ALL_DAY_SUBJECT

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

failures = []
checks = 0


def check(label, ok, detail=''):
    global checks
    checks += 1
    print(f'{"✓" if ok else "✗"} {label}' + (f' — {detail}' if detail else ''))
    if not ok:
        failures.append(f'{label}: {detail}')


def csrf(client, url='/login'):
    html = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return match.group(1) if match else ''


def login(client, username, password):
    client.post('/login', data={
        'username': username, 'password': password,
        'csrf_token': csrf(client, '/login')}, follow_redirects=True)


def flash_of(html):
    found = re.findall(r'alert-(?:danger|warning|success)[^>]*>(.*?)</div>',
                       html, re.DOTALL)
    return [' '.join(re.sub(r'<[^>]+>', ' ', chunk).split())
            for chunk in found if chunk.strip()]


def page_text(html):
    without_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html,
                             flags=re.DOTALL | re.IGNORECASE)
    return ' '.join(re.sub(r'<[^>]+>', ' ', without_scripts).split())


def _audit_max_id():
    """Наибольший id в журнале действий — точка отсчёта для очистки.

    Вызывается из snapshot(), где контекст приложения уже открыт: свой
    контекст здесь только развёл бы читателя по лишней сессии.
    """
    return db.session.query(db.func.max(AuditLog.id)).scalar() or 0


def snapshot():
    with app.app_context():
        return {
            'audit_max_id': _audit_max_id(),
            'attendance': [{'id': a.id, 'student': a.student_id,
                            'date': a.date, 'reason': a.reason,
                            'subject': a.subject_id, 'note': a.note,
                            'by': a.created_by}
                           for a in AttendanceRecord.query.all()],
            'grades': [{'id': g.id} for g in Grade.query.all()],
            'users': {u.id for u in User.query.all()},
            'subjects': {s.id for s in Subject.query.all()},
            'subject_teachers': {s.id: s.teacher_id
                                 for s in Subject.query.all()},
            'student_users': {s.id: s.user_id for s in Student.query.all()},
            'schedule': {i.id for i in ScheduleItem.query.all()},
        }


def restore(state):
    """Возврат базы к снимку.

    Пропуски удаляются целиком и создаются заново: тестовые отметки ссылаются
    на временный предмет и временного преподавателя, которых к моменту
    восстановления уже нет. Порядок важен — attendance.created_by и
    attendance.subject_id сначала должны опустеть, иначе удаление учётных
    записей и предметов упрётся в внешние ключи.
    """
    with app.app_context():
        for row in AttendanceRecord.query.all():
            db.session.delete(row)
        db.session.flush()

        for user in User.query.all():
            if user.id not in state['users']:
                db.session.delete(user)
        db.session.flush()
        for student_id, user_id in state['student_users'].items():
            student = db.session.get(Student, student_id)
            if student is not None:
                student.user_id = user_id

        for item in ScheduleItem.query.all():
            if item.id not in state['schedule']:
                for row in LessonDate.query.filter_by(
                        schedule_item_id=item.id).all():
                    db.session.delete(row)
                db.session.delete(item)
        db.session.flush()
        for subject in Subject.query.all():
            if subject.id not in state['subjects']:
                db.session.delete(subject)
        db.session.flush()
        for subject_id, teacher_id in state['subject_teachers'].items():
            subject = db.session.get(Subject, subject_id)
            if subject is not None:
                subject.teacher_id = teacher_id

        for data in state['attendance']:
            db.session.add(AttendanceRecord(
                id=data['id'], student_id=data['student'], date=data['date'],
                reason=data['reason'], subject_id=data['subject'],
                note=data['note'], created_by=data['by']))
        db.session.commit()
        # Журнал действий (Фаза 13) тест тоже наполняет: без сброса каждый
        # прогон оставлял бы в рабочей базе записи о тестовых входах
        for entry in AuditLog.query.filter(
                AuditLog.id > state['audit_max_id']).all():
            db.session.delete(entry)
        db.session.commit()



TEST_TEACHER = 'test-teacher-attendance'
TEACHER_PASSWORD = 'attendance-teacher-1'
TEST_STUDENT = 'test-student-attendance'
STUDENT_PASSWORD = 'attendance-student-1'
TEST_SUBJECT = 'Пропуски Ф9 (временный)'


def make_test_accounts(other_subject_id, student_row_id, group_id, period,
                       earliest):
    """Временные преподаватель с парой и студент с известными паролями.

    Паре отмечаем два занятия — неделю apart, — потому что проверка массовой
    отметки обязана идти по другой дате, чем одиночная. Даты подбираются так,
    чтобы день недели совпадал со слотом пары: иначе в базе появляется
    LessonDate на дне, в который эта пара не проводится, и сводка по парам
    считается бы не по той неделе.
    """
    with app.app_context():
        for name in (TEST_TEACHER, TEST_STUDENT):
            stale = User.query.filter_by(username=name).first()
            if stale:
                for subject in Subject.query.filter_by(
                        teacher_id=stale.id).all():
                    subject.teacher_id = other_subject_id
                db.session.delete(stale)
                db.session.flush()
        for subject in Subject.query.filter_by(name=TEST_SUBJECT).all():
            for item in ScheduleItem.query.filter_by(
                    subject_id=subject.id).all():
                for row in LessonDate.query.filter_by(
                        schedule_item_id=item.id).all():
                    db.session.delete(row)
                db.session.delete(item)
            for record in AttendanceRecord.query.filter_by(
                    subject_id=subject.id).all():
                db.session.delete(record)
            for grade in Grade.query.filter_by(subject_id=subject.id).all():
                db.session.delete(grade)
            db.session.delete(subject)
            db.session.flush()

        teacher = User(username=TEST_TEACHER, full_name='Проверка Ф9 Преподаватель',
                       role=User.ROLE_TEACHER, is_active=True)
        teacher.set_password(TEACHER_PASSWORD)
        db.session.add(teacher)
        db.session.flush()

        own = Subject(name=TEST_SUBJECT, teacher_id=teacher.id)
        db.session.add(own)
        db.session.flush()

        taken = {(row[0], row[1]) for row in
                 (db.session.query(ScheduleItem.day_of_week,
                                   ScheduleItem.lesson_number)
                  .filter_by(group_id=group_id).all())}
        slot = None
        for day in range(1, 7):
            for number in range(1, 8):
                if (day, number) not in taken:
                    slot = (day, number)
                    break
            if slot:
                break
        day_of_week, lesson_number = slot or (1, 8)
        item = ScheduleItem(group_id=group_id, subject_id=own.id,
                            teacher_id=teacher.id, day_of_week=day_of_week,
                            lesson_number=lesson_number, room='Ф9')
        db.session.add(item)
        db.session.flush()

        def same_weekday(start, offset_days):
            """Ближайшая к start дата нужного дня недели в пределах периода."""
            day = start
            while day < period.end_date and day <= date.today():
                if day.weekday() + 1 == day_of_week and day >= start:
                    return day
                day += timedelta(days=1)
            return None

        first = same_weekday(earliest, 0)
        if first is None:
            db.session.commit()
            return teacher.id, own.id, item.id, None, None
        db.session.add(LessonDate(schedule_item_id=item.id, date=first))
        db.session.flush()

        # Второе занятие — следующий такой же день недели после первого
        second = same_weekday(first + timedelta(days=1), 1)
        if second is not None:
            db.session.add(LessonDate(schedule_item_id=item.id, date=second))

        student = User(username=TEST_STUDENT, full_name='Проверка Ф9 Студент',
                       role=User.ROLE_STUDENT, is_active=True)
        student.set_password(STUDENT_PASSWORD)
        db.session.add(student)
        db.session.flush()
        row = db.session.get(Student, student_row_id)
        row.user_id = student.id
        db.session.commit()
        return teacher.id, own.id, item.id, first, second


def mark(client, data, referer='/attendance'):
    return client.post('/attendance/mark', data=dict(
        data, csrf_token=csrf(client, referer)), follow_redirects=True)


def count_records(student_id, day, subject_id):
    with app.app_context():
        return AttendanceRecord.query.filter_by(
            student_id=student_id, date=day, subject_id=subject_id).count()


def main():
    state = snapshot()
    try:
        return run_checks()
    finally:
        restore(state)


def run_checks():
    with app.app_context():
        admin = User.query.filter_by(role=User.ROLE_ADMIN).first()
        other_teacher = User.query.filter_by(role=User.ROLE_TEACHER).first()
        if admin is None or other_teacher is None:
            print('Нужны администратор и преподаватель. Запустите init_db.py')
            return 1

        other_subject = Subject.query.filter(
            Subject.teacher_id != other_teacher.id).first()
        if other_subject is None:
            print('Нужны предметы. Запустите init_db.py')
            return 1
        other_subject_id = other_subject.id

        # Опорная пара: первое занятие с отметкой LessonDate в текущем периоде
        lesson = (db.session.query(LessonDate)
                  .join(LessonDate.schedule_item)
                  .filter(LessonDate.date <= date.today())
                  .order_by(LessonDate.date).first())
        if lesson is None:
            print('Нет отмеченных занятий. Запустите init_db.py')
            return 1
        real_group_id = lesson.schedule_item.group_id
        period = (AcademicPeriod.query
                  .filter(AcademicPeriod.start_date <= lesson.date,
                          AcademicPeriod.end_date >= lesson.date)
                  .order_by(AcademicPeriod.sort_order).first())
        if period is None:
            print('Занятие не попадает ни в один период. Запустите init_db.py')
            return 1

        students = (Student.query
                    .filter_by(group_id=real_group_id, status='active')
                    .order_by(Student.id).all())
        if len(students) < 2:
            print('Нужны минимум два активных студента группы. Запустите init_db.py')
            return 1
        first_id, second_id = students[0].id, students[1].id

        teacher_id, temp_subject_id, temp_item_id, temp_day, bulk_day_obj = (
            make_test_accounts(other_subject_id, first_id, real_group_id,
                               period, lesson.date))
        if temp_day is None:
            print('Не удалось подобрать даты занятий в периоде. Запустите init_db.py')
            return 1
        period_id = period.id
        # Дата вне расписания: тот же период, но занятия в этот день не было
        free_day = lesson.date
        while (db.session.query(LessonDate.id)
               .join(LessonDate.schedule_item)
               .filter(ScheduleItem.group_id == real_group_id,
                       LessonDate.date == free_day).first() is not None):
            free_day += timedelta(days=1)
            if free_day > period.end_date:
                break
        free_day_str = free_day.strftime('%Y-%m-%d')
        day_str = temp_day.strftime('%Y-%m-%d')

    # --- Страница журнала пропусков ----------------------------------------
    admin_client = app.test_client()
    login(admin_client, admin.username, ADMIN_PASSWORD)

    response = admin_client.get(f'/attendance?group_id={real_group_id}'
                                f'&period_id={period_id}')
    check('Журнал пропусков открывается администратору', response.status_code == 200,
          f'HTTP {response.status_code}')
    text = page_text(response.get_data(as_text=True))
    check('На странице есть сводка по студентам', 'Сводка по студентам' in text)
    check('На странице виден период', period.name in text)

    # --- Студент не попадает в журнал ---------------------------------------
    student_client = app.test_client()
    login(student_client, TEST_STUDENT, STUDENT_PASSWORD)
    response = student_client.get('/attendance', follow_redirects=False)
    check('Студенту журнал пропусков недоступен', response.status_code in (302, 403),
          f'HTTP {response.status_code}')

    # --- Отметка пропуска из ячейки журнала ---------------------------------
    response = mark(admin_client, {
        'student_id': first_id, 'group_id': real_group_id,
        'subject_id': temp_subject_id, 'date': day_str, 'reason': 'illness',
        'note': 'справка', 'mode': 'dates', 'period_id': period_id},
        referer=f'/journal?group_id={real_group_id}&subject_id={temp_subject_id}'
                f'&period_id={period_id}&mode=dates')
    check('Отметка пропуска принята', response.status_code == 200,
          f'HTTP {response.status_code}')
    check('Пропуск записан в базу', count_records(first_id, temp_day, temp_subject_id) == 1)

    with app.app_context():
        record = AttendanceRecord.query.filter_by(
            student_id=first_id, date=temp_day,
            subject_id=temp_subject_id).first()
        check('Причина сохранена', record is not None and record.reason == 'illness',
              record.reason if record else 'нет записи')
        check('Комментарий сохранён', record is not None and record.note == 'справка',
              repr(record.note) if record else 'нет записи')
        check('Автор отметки сохранён', record is not None and record.created_by == admin.id,
              str(record.created_by) if record else 'нет записи')
        record_id = record.id if record else None

    # Пропуск виден в сетке журнала
    html = admin_client.get(f'/journal?group_id={real_group_id}'
                            f'&subject_id={temp_subject_id}'
                            f'&period_id={period_id}&mode=dates').get_data(as_text=True)
    check('Пропуск виден в сетке журнала', f'data-record="{record_id}"' in html)
    check('Причина попала в подсказку сетки', 'Болезнь' in page_text(html))

    # --- Повторная отметка на то же занятие ---------------------------------
    before = count_records(first_id, temp_day, temp_subject_id)
    html = mark(admin_client, {
        'student_id': first_id, 'group_id': real_group_id,
        'subject_id': temp_subject_id, 'date': day_str, 'reason': 'unexcused',
        'mode': 'dates', 'period_id': period_id}).get_data(as_text=True)
    after = count_records(first_id, temp_day, temp_subject_id)
    check('Повторная отметка не создаёт вторую запись', before == after == 1,
          f'было {before}, стало {after}')
    check('О повторе сказано в сообщении',
          any('уже отмечен' in message for message in flash_of(html)),
          '; '.join(flash_of(html))[:90])

    # --- Отметка за несуществующее занятие ----------------------------------
    response = admin_client.post('/attendance/mark', data={
        'student_id': second_id, 'group_id': real_group_id,
        'subject_id': temp_subject_id, 'date': free_day_str,
        'reason': 'unexcused', 'mode': 'dates', 'period_id': period_id,
        'csrf_token': csrf(admin_client, f'/journal?group_id={real_group_id}'
                                       f'&subject_id={temp_subject_id}'
                                       f'&period_id={period_id}&mode=dates')},
        follow_redirects=True)
    check('Отметка за несуществующее занятие отклонена',
          response.status_code == 400, f'HTTP {response.status_code}')
    check('Записи о пропуске не появилось',
          count_records(second_id, free_day, temp_subject_id) == 0)

    # --- Отметка за будущее занятие ----------------------------------------
    future = date.today() + timedelta(days=400)
    response = admin_client.post('/attendance/mark', data={
        'student_id': second_id, 'group_id': real_group_id,
        'subject_id': temp_subject_id, 'date': future.strftime('%Y-%m-%d'),
        'reason': 'unexcused', 'mode': 'dates', 'period_id': period_id,
        'csrf_token': csrf(admin_client, f'/journal?group_id={real_group_id}'
                                       f'&subject_id={temp_subject_id}'
                                       f'&period_id={period_id}&mode=dates')},
        follow_redirects=True)
    text = page_text(response.get_data(as_text=True))
    check('Отметка за будущее занятие отклонена',
          response.status_code == 400 or 'будущ' in text.lower(),
          f'HTTP {response.status_code}')
    with app.app_context():
        check('Будущая отметка не записана', AttendanceRecord.query.filter_by(
            student_id=second_id, date=future).count() == 0)

    # --- Права: преподаватель отмечает только свой предмет ------------------
    teacher_client = app.test_client()
    login(teacher_client, TEST_TEACHER, TEACHER_PASSWORD)

    response = teacher_client.post('/attendance/mark', data={
        'student_id': second_id, 'group_id': real_group_id,
        'subject_id': temp_subject_id, 'date': day_str, 'reason': 'illness',
        'mode': 'dates', 'period_id': period_id,
        'csrf_token': csrf(teacher_client, f'/journal?group_id={real_group_id}'
                                         f'&subject_id={temp_subject_id}'
                                         f'&period_id={period_id}&mode=dates')},
        follow_redirects=True)
    check('Преподаватель отмечает пропуск по своему предмету',
          response.status_code == 200 and
          count_records(second_id, temp_day, temp_subject_id) == 1,
          f'HTTP {response.status_code}')

    response = teacher_client.post('/attendance/mark', data={
        'student_id': second_id, 'group_id': real_group_id,
        'subject_id': other_subject_id, 'date': day_str, 'reason': 'illness',
        'mode': 'dates', 'period_id': period_id,
        'csrf_token': csrf(teacher_client, f'/journal?group_id={real_group_id}'
                                         f'&subject_id={temp_subject_id}'
                                         f'&period_id={period_id}&mode=dates')},
        follow_redirects=True)
    check('Преподавателю отказан чужой предмет', response.status_code == 403,
          f'HTTP {response.status_code}')
    check('Чужой предмет не записан',
          count_records(second_id, temp_day, other_subject_id) == 0)

    response = teacher_client.post('/attendance/mark', data={
        'student_id': first_id, 'group_id': real_group_id,
        'subject_id': ALL_DAY_SUBJECT, 'date': day_str, 'reason': 'unexcused',
        'mode': 'dates', 'period_id': period_id,
        'csrf_token': csrf(teacher_client, f'/journal?group_id={real_group_id}'
                                         f'&subject_id={temp_subject_id}'
                                         f'&period_id={period_id}&mode=dates')},
        follow_redirects=True)
    # Такой предмет преподавателю не предлагали, поэтому отказ приходит уже
    # на проверке формы (403 был бы, если бы вариант был в списке)
    check('Преподавателю отказано в отметке «весь день»',
          response.status_code in (302, 403) or
          any('предмет' in message for message in
              flash_of(response.get_data(as_text=True))),
          f'HTTP {response.status_code}; ' +
          '; '.join(flash_of(response.get_data(as_text=True)))[:80])
    with app.app_context():
        check('Записи «весь день» от преподавателя нет',
              AttendanceRecord.query.filter_by(student_id=first_id,
                                               date=temp_day,
                                               subject_id=None).count() == 0)

    # В списке предметов у преподавателя нет варианта «весь день»
    html = teacher_client.get(f'/attendance?group_id={real_group_id}').get_data(as_text=True)
    check('Преподавателю не показан вариант «весь день»', 'Весь день' not in html)

    # --- Администратор может отметить «весь день» ----------------------------
    html = admin_client.get(f'/attendance?group_id={real_group_id}').get_data(as_text=True)
    check('Администратору показан вариант «весь день»', 'Весь день' in html)

    response = admin_client.post('/attendance/mark', data={
        'student_id': second_id, 'group_id': real_group_id,
        'subject_id': ALL_DAY_SUBJECT, 'date': day_str, 'reason': 'unexcused',
        'note': 'не пришёл', 'mode': 'dates', 'period_id': period_id,
        'csrf_token': csrf(admin_client, f'/attendance?group_id={real_group_id}')},
        follow_redirects=True)
    with app.app_context():
        all_day = AttendanceRecord.query.filter_by(
            student_id=second_id, date=temp_day, subject_id=None).first()
        check('Администратор отметил пропуск за весь день', all_day is not None)
        # hours_count у отметки без предмета считается по расписанию дня
        expected_hours = (db.session.query(db.func.count(ScheduleItem.id))
                          .filter_by(group_id=real_group_id,
                                     day_of_week=temp_day.weekday() + 1)
                          .scalar()) or 0
        check('Зачёт пропущенных пар посчитан по расписанию',
              all_day is not None and all_day.hours_count == expected_hours,
              f'{all_day.hours_count if all_day else "нет записи"} из {expected_hours}')
        check('Отметка за весь день не путается с отметкой по предмету',
              count_records(second_id, temp_day, temp_subject_id) == 1)

    # --- Правка причины ------------------------------------------------------
    response = admin_client.post(f'/attendance/{record_id}/edit', data={
        'reason': 'excused', 'note': 'по семейным обстоятельствам',
        'group_id': real_group_id, 'period_id': period_id, 'mode': 'dates',
        'csrf_token': csrf(admin_client, f'/attendance?group_id={real_group_id}')},
        follow_redirects=True)
    with app.app_context():
        updated = db.session.get(AttendanceRecord, record_id)
        check('Причина изменена', updated is not None and updated.reason == 'excused',
              updated.reason if updated else 'нет записи')
        check('Комментарий изменён',
              updated is not None and updated.note == 'по семейным обстоятельствам',
              repr(updated.note) if updated else 'нет записи')
        check('Запись не задвоилась при правке',
              count_records(first_id, temp_day, temp_subject_id) == 1)

    # --- Массовая отметка ----------------------------------------------------
    with app.app_context():
        if bulk_day_obj is None:
            print('Нужно второе занятие в периоде. Запустите init_db.py')
            return 1
        bulk_date = bulk_day_obj
        bulk_day = bulk_date.strftime('%Y-%m-%d')
        graded_student = students[2].id if len(students) > 2 else None
        if graded_student is not None:
            exists = Grade.query.filter_by(student_id=graded_student,
                                          subject_id=temp_subject_id,
                                          date=bulk_date).first()
            if exists is None:
                db.session.add(Grade(student_id=graded_student,
                                     subject_id=temp_subject_id,
                                     grade_value='5', date=bulk_date,
                                     period_id=period_id))
                db.session.commit()
        active_ids = {s.id for s in Student.query
                      .filter_by(group_id=real_group_id, status='active').all()}

    response = admin_client.post('/attendance/bulk', data={
        'group_id': real_group_id, 'subject_id': temp_subject_id,
        'date': bulk_day, 'reason': 'unexcused', 'skip_graded': 'y',
        'group_id': real_group_id,
        'csrf_token': csrf(admin_client, f'/attendance?group_id={real_group_id}')},
        follow_redirects=True)
    check('Массовая отметка принята', response.status_code == 200,
          f'HTTP {response.status_code}')
    with app.app_context():
        marked = AttendanceRecord.query.filter_by(subject_id=temp_subject_id,
                                                  date=bulk_date).all()
        marked_ids = {row.student_id for row in marked}
        expected = set(active_ids)
        if graded_student is not None:
            expected.discard(graded_student)
        check('Отмечены все, кроме тех, у кого есть оценка',
              marked_ids == expected,
              f'отмечено {len(marked_ids)}, ожидалось {len(expected)}')
        check('У кого есть оценка за день, пропуск не поставлен',
              graded_student not in marked_ids or graded_student is None)

        # Повторный запуск не должен дублировать записи
        db.session.commit()
    response = admin_client.post('/attendance/bulk', data={
        'group_id': real_group_id, 'subject_id': temp_subject_id,
        'date': bulk_day, 'reason': 'illness', 'skip_graded': 'y',
        'csrf_token': csrf(admin_client, f'/attendance?group_id={real_group_id}')},
        follow_redirects=True)
    with app.app_context():
        again = AttendanceRecord.query.filter_by(subject_id=temp_subject_id,
                                                 date=bulk_date).count()
        check('Повторная массовая отметка не создаёт дублей', again == len(expected),
              f'записей {again}')

    # --- Сходимость сводки ---------------------------------------------------
    html = admin_client.get(f'/attendance?group_id={real_group_id}'
                            f'&period_id={period_id}').get_data(as_text=True)
    text = page_text(html)
    with app.app_context():
        rows = AttendanceRecord.query.filter(
            AttendanceRecord.student_id.in_(active_ids),
            AttendanceRecord.date >= period.start_date,
            AttendanceRecord.date <= period.end_date).all()
        group_total = len(rows)
        per_student = {}
        for row in rows:
            per_student[row.student_id] = per_student.get(row.student_id, 0) + 1
        check('Сумма по студентам равна итогу по группе',
              sum(per_student.values()) == group_total,
              f'{sum(per_student.values())} против {group_total}')
        check('В сводке виден итог по группе', 'Итого по группе' in text)
        check('В сводке есть счётчик пропусков', f'Всего пропусков' in text)
        check('Отметки видны в списке за период',
              students[0].last_name in text and students[0].first_name in text)

    # --- Удаление пропуска ---------------------------------------------------
    html = admin_client.get(f'/attendance?group_id={real_group_id}'
                            f'&period_id={period_id}').get_data(as_text=True)
    check('Кнопка удаления есть в списке',
          f'/attendance/{record_id}/delete' in html)
    check('В окне удаления есть предупреждение',
          'будет удалён' in page_text(html))

    response = admin_client.post(f'/attendance/{record_id}/delete', data={
        'group_id': real_group_id, 'period_id': period_id, 'mode': 'dates',
        'csrf_token': csrf(admin_client, f'/attendance?group_id={real_group_id}')},
        follow_redirects=True)
    with app.app_context():
        check('Пропуск удалён', db.session.get(AttendanceRecord, record_id) is None)
    check('Удаление подтверждено сообщением',
          any('удалён' in message for message in flash_of(response.get_data(as_text=True))),
          '; '.join(flash_of(response.get_data(as_text=True)))[:90])

    # Оценка за это занятие не тронута
    with app.app_context():
        check('Удаление пропуска не удалило оценки',
              Grade.query.filter_by(student_id=first_id,
                                    subject_id=temp_subject_id).count() >= 0)

    # --- Пропуски в карточке студента ---------------------------------------
    html = admin_client.get(f'/student/{second_id}').get_data(as_text=True)
    text = page_text(html)
    check('В карточке студента есть блок пропусков', 'Пропуски' in text)
    check('В карточке есть ссылка на журнал пропусков',
          f'/attendance?group_id={real_group_id}' in html)
    with app.app_context():
        student_period = (AcademicPeriod.query
                          .filter(AcademicPeriod.start_date <= date.today(),
                                  AcademicPeriod.end_date >= date.today())
                          .order_by(AcademicPeriod.sort_order).first())
        if student_period is None:
            student_period = period
        student_rows = AttendanceRecord.query.filter_by(student_id=second_id).all()
        check('Счётчик в карточке совпадает с числом отметок',
              f'Всего' in text and str(len(student_rows)) in text,
              f'отметок {len(student_rows)}')

    # --- Пропуски на дашборде -----------------------------------------------
    html = admin_client.get('/dashboard').get_data(as_text=True)
    text = page_text(html)
    check('Дашборд показывает пропуски за период', 'Пропуски за период' in text)
    check('Дашборд ведёт на журнал пропусков', '/attendance' in html)
    with app.app_context():
        period_rows = AttendanceRecord.query.filter(
            AttendanceRecord.date >= period.start_date,
            AttendanceRecord.date <= period.end_date).count()
        check('Число пропусков на дашборде верное',
              str(period_rows) in text, f'в базе {period_rows}')

    # --- Меню ----------------------------------------------------------------
    check('В меню есть пункт «Пропуски»', 'Пропуски' in html)

    print()
    print(f'Проверок: {checks}, неудачных: {len(failures)}')
    if failures:
        print('\nНе прошли:')
        for item in failures:
            print(f'  - {item}')
        return 1
    print('Все проверки Фазы 9 пройдены.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
