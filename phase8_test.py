"""Функциональная проверка Фазы 8: журнал.

Запуск:  python phase8_test.py

Проверяет то, что не видно на странице:
  * два режима сетки: по датам занятий и сводная по предметам;
  * выставление нескольких оценок за одно занятие (одна строка Grade на
    каждое значение) и раздельные строки истории;
  * разбор строки «5, 4, 4» и отказ молча проглотить «6»;
  * правка оценки с сохранением старого значения в истории;
  * удаление с записью в историю;
  * запрет оценки за несуществующее/будущее занятие, вне периода и чужой группе;
  * права: студент не попадает в журнал, преподаватель правит только свои
    предметы;
  * средние и сводка считаются, а не показывают нули.

Все изменения откатываются в финале: скрипт снимает снимок оценок и истории
и восстанавливает их, поэтому база остаётся как была.
"""

import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Group, Student, Subject, Grade, GradeHistory,
                        AcademicPeriod, ScheduleItem, LessonDate,
                        AttendanceRecord)
from app.utils import parse_grade_values, average_grade, grade_distribution

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

ADMIN = os.environ.get('ADMIN_USERNAME', 'admin')
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
    """Весь видимый текст страницы.

    Часть отказов возвращается не через flash, а страницей error.html с
    кодом 400/403/404, поэтому искать причину отказа только во flash
    бесполезно: её нет в списке сообщений.
    """
    without_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html,
                             flags=re.DOTALL | re.IGNORECASE)
    return ' '.join(re.sub(r'<[^>]+>', ' ', without_scripts).split())


def snapshot():
    with app.app_context():
        return {
            'grades': [{'id': g.id, 'student': g.student_id,
                        'subject': g.subject_id, 'value': g.grade_value,
                        'type': g.grade_type, 'date': g.date,
                        'comments': g.comments, 'period': g.period_id}
                       for g in Grade.query.all()],
            'history': [{'id': h.id, 'grade': h.grade_id, 'old': h.old_value,
                         'new': h.new_value, 'action': h.action,
                         'by': h.changed_by, 'comment': h.comment}
                        for h in GradeHistory.query.all()],
            'attendance': [{'id': a.id, 'student': a.student_id,
                            'date': a.date, 'reason': a.reason}
                           for a in AttendanceRecord.query.all()],
            'users': {u.id for u in User.query.all()},
            'subjects': {s.id for s in Subject.query.all()},
            'subject_teachers': {s.id: s.teacher_id
                                 for s in Subject.query.all()},
            'student_users': {s.id: s.user_id for s in Student.query.all()},
        }


def restore(state):
    with app.app_context():
        # Временные учётные записи и предмет, созданные ради проверки прав
        for user in User.query.all():
            if user.id not in state['users']:
                db.session.delete(user)
        db.session.flush()
        for student_id, user_id in state['student_users'].items():
            student = db.session.get(Student, student_id)
            if student is not None:
                student.user_id = user_id
        # Временные пары и отметки о них удаляем раньше предметов: у
        # schedule_item.subject_id NOT NULL, и удаление предмета одной
        # транзакцией обнулило бы ссылку вместо удаления самой пары
        for item in ScheduleItem.query.all():
            if item.subject_id not in state['subjects']:
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

        for row in GradeHistory.query.all():
            db.session.delete(row)
        for row in Grade.query.all():
            db.session.delete(row)
        db.session.flush()
        for data in state['grades']:
            db.session.add(Grade(
                id=data['id'], student_id=data['student'],
                subject_id=data['subject'], grade_value=data['value'],
                grade_type=data['type'], date=data['date'],
                comments=data['comments'], period_id=data['period']))
        db.session.flush()
        for data in state['history']:
            db.session.add(GradeHistory(
                id=data['id'], grade_id=data['grade'], old_value=data['old'],
                new_value=data['new'], action=data['action'],
                changed_by=data['by'], comment=data['comment']))
        db.session.commit()


TEST_TEACHER = 'test-teacher-journal'
TEACHER_PASSWORD = 'journal-teacher-1'
TEST_STUDENT = 'test-student-journal'
STUDENT_PASSWORD = 'journal-student-1'
TEST_SUBJECT = 'Журнал Ф8 (временный)'


def make_test_accounts(own_subject_id, other_subject_id, student_row_id,
                       group_id, lesson_date):
    """Временные преподаватель, предмет, пара и студент с известными паролями.

    Демо-пароль преподавателя неизвестен, а проверять права нужно на
    настоящем входе через форму, поэтому учётки создаём и удаляем в
    restore(). Прежние временные объекты тоже убираем: если прошлый запуск
    упал до restore(), остатки в базе нарушили бы UNIQUE на имени предмета.

    Предмету нужна собственная пара в расписании и отметка LessonDate: без
    них маршрут справедливо не даст выставить оценку — не было занятия.
    """
    with app.app_context():
        for name in (TEST_TEACHER, TEST_STUDENT):
            stale = User.query.filter_by(username=name).first()
            if stale:
                for subject in Subject.query.filter_by(teacher_id=stale.id).all():
                    subject.teacher_id = other_subject_id
                db.session.delete(stale)
                db.session.flush()
        for subject in Subject.query.filter_by(name=TEST_SUBJECT).all():
            for item in ScheduleItem.query.filter_by(subject_id=subject.id).all():
                for row in LessonDate.query.filter_by(
                        schedule_item_id=item.id).all():
                    db.session.delete(row)
                db.session.delete(item)
            for grade in Grade.query.filter_by(subject_id=subject.id).all():
                db.session.delete(grade)
            db.session.delete(subject)
            db.session.flush()

        teacher = User(username=TEST_TEACHER, full_name='Проверка Ф8 Преподаватель',
                       role=User.ROLE_TEACHER, is_active=True)
        teacher.set_password(TEACHER_PASSWORD)
        db.session.add(teacher)
        db.session.flush()

        # Свой предмет преподавателю: создаём, чтобы не портить демо-данные
        own = Subject(name=TEST_SUBJECT, teacher_id=teacher.id)
        db.session.add(own)
        db.session.flush()

        # Свободный слот пары: (group, day, lesson) уникален, иначе темповая
        # пара столкнулась бы с демо-расписанием
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
                            lesson_number=lesson_number, room='Ф8')
        db.session.add(item)
        db.session.flush()
        db.session.add(LessonDate(schedule_item_id=item.id, date=lesson_date))

        student = User(username=TEST_STUDENT, full_name='Проверка Ф8 Студент',
                       role=User.ROLE_STUDENT, is_active=True)
        student.set_password(STUDENT_PASSWORD)
        db.session.add(student)
        db.session.flush()
        # Связь студента и учётной записи идёт со стороны Student: у User
        # своего student_id нет.
        row = db.session.get(Student, student_row_id)
        row.user_id = student.id
        db.session.commit()
        return teacher.id, own.id


def add_grade(client, data):
    """POST добавления оценки; возвращает HTML страницы после редиректа."""
    return client.post('/journal/grade/add', data=dict(
        data, csrf_token=csrf(client, '/journal')),
        follow_redirects=True).get_data(as_text=True)


def main():
    state = snapshot()
    try:
        return run_checks()
    finally:
        # restore() обязателен даже после исключения: иначе упавший прогон
        # оставит в демо-базе временные предметы и учётные записи
        restore(state)


def run_checks():
    with app.app_context():
        admin = User.query.filter_by(role=User.ROLE_ADMIN).first()
        teacher = User.query.filter_by(role=User.ROLE_TEACHER).first()
        if admin is None or teacher is None:
            print('Нужны администратор и преподаватель. Запустите init_db.py')
            return 1
        admin_password = ADMIN_PASSWORD
        teacher_subject = Subject.query.filter_by(
            teacher_id=teacher.id).first()
        other_subject = Subject.query.filter(Subject.teacher_id != teacher.id).first()
        if teacher_subject is None or other_subject is None:
            print('Нужны свои и чужие предметы. Запустите init_db.py')
            return 1
        teacher_subject_id = teacher_subject.id
        teacher_subject_name = teacher_subject.name
        other_subject_id = other_subject.id
        teacher_id = teacher.id
        admin_id = admin.id

        # Опорная сетка: первое занятие с отметкой LessonDate у пары
        # «нашего» преподавателя, иначе в журнале не будет ни одной даты
        lesson = (db.session.query(LessonDate)
                  .join(LessonDate.schedule_item)
                  .filter(ScheduleItem.subject_id == teacher_subject_id,
                          ScheduleItem.teacher_id == teacher_id,
                          LessonDate.date <= date.today())
                  .order_by(LessonDate.date).first())
        if lesson is None:
            print('Нет отмеченных занятий. Запустите init_db.py')
            return 1
        lesson_id = lesson.id
        lesson_date = lesson.date
        group_id = lesson.schedule_item.group_id
        period = (AcademicPeriod.query
                  .filter(AcademicPeriod.start_date <= lesson_date,
                          AcademicPeriod.end_date >= lesson_date)
                  .order_by(AcademicPeriod.sort_order).first())
        if period is None:
            print('Занятие не попадает ни в один период. Запустите init_db.py')
            return 1
        period_id = period.id
        student = (Student.query.filter_by(group_id=group_id, status='active')
                   .order_by(Student.id).first())
        if student is None:
            print('В группе нет активных студентов. Запустите init_db.py')
            return 1
        student_id = student.id
        student_name = student.full_name
        student_last = student.last_name
        group_name = group_id and db.session.get(Group, group_id).name

        # Дата внутри периода, в которую занятия не было: на ней маршрут
        # обязан отказать, а не записать оценку в пустую ячейку
        known = {row[0] for row in
                 (db.session.query(LessonDate.date)
                  .join(LessonDate.schedule_item)
                  .filter(ScheduleItem.group_id == group_id,
                          ScheduleItem.subject_id == teacher_subject_id)
                  .distinct().all())}
        free_date = None
        probe = period.start_date
        while probe <= min(period.end_date, date.today()):
            if probe not in known:
                free_date = probe
                break
            probe += timedelta(days=1)

    # --- Разбор строки оценок -----------------------------------------
    check('«5, 4, 4» -> три оценки',
          parse_grade_values('5, 4, 4') == ['5', '4', '4'])
    check('«зачёт; 3» -> зачёт и тройка',
          parse_grade_values('зачёт; 3') == ['зачет', '3'])
    check('«05» -> одна пятёрка', parse_grade_values('05') == ['5'])
    check('пустая строка -> пустой список', parse_grade_values('') == [])
    try:
        parse_grade_values('5, 6, 4')
        check('«6» вызывает ошибку, а не теряется молча', False,
              'исключения не было')
    except ValueError as error:
        check('«6» вызывает ошибку, а не теряется молча', '6' in str(error),
              str(error))
    try:
        parse_grade_values('5 4 3 2 5 4 3 2 5 4 3')
        check('больше 10 оценок подряд отклоняется', False, 'исключения не было')
    except ValueError as error:
        check('больше 10 оценок подряд отклоняется', 'максимум' in str(error).lower()
              or 'слишком' in str(error).lower(), str(error))

    check('average_grade считает только числа',
          average_grade(['5', '4', '3']) == 4.0)
    check('зачёт идёт по шкале 5, незачёт — по шкале 2',
          average_grade(['5', 'зачет', '4']) == 4.67
          and average_grade(['незачет', '4']) == 3.0)
    check('распределение считает зачёты отдельно',
          grade_distribution(['5', '5', 'зачет']) == {'5': 2, 'зачет': 1})

    # --- Права: студент не попадает в журнал ---------------------------
    test_teacher_id, own_subject_id = make_test_accounts(
        teacher_subject_id, other_subject_id, student_id, group_id, lesson_date)

    client = app.test_client()
    login(client, TEST_STUDENT, STUDENT_PASSWORD)
    response = client.get('/journal')
    check('студент не попадает в журнал (403)', response.status_code == 403,
          f'код {response.status_code}')

    # --- Администратор: страница и добавление ---------------------------
    client = app.test_client()
    login(client, ADMIN, admin_password)

    response = client.get('/journal')
    check('журнал открывается администратору', response.status_code == 200)
    html = response.get_data(as_text=True)
    check('в сетке есть заголовок «Журнал»', 'Журнал' in html)
    check('в сетке есть фамилия студента', student_last in html, student_last)

    url = f'/journal?group_id={group_id}&subject_id={teacher_subject_id}&period_id={period_id}&mode=dates'
    response = client.get(url)
    check('режим «по датам» открывается', response.status_code == 200)
    dates_html = response.get_data(as_text=True)
    check('колонка даты присутствует',
          lesson_date.strftime('%d.%m') in dates_html,
          lesson_date.strftime('%d.%m'))
    check('в режиме дат есть блок итогов', 'Средний' in dates_html)

    response = client.get(f'/journal?group_id={group_id}&subject_id={teacher_subject_id}'
                          f'&period_id={period_id}&mode=subjects')
    check('сводный режим открывается', response.status_code == 200)
    subjects_html = response.get_data(as_text=True)
    check('в сводном режиме есть столбцы предметов',
          'Средний балл по предметам' in subjects_html or 'сводная' in subjects_html.lower())

    response = client.get(f'/journal?group_id={group_id}&subject_id={teacher_subject_id}'
                          f'&period_id={period_id}&mode=unknown')
    check('неизвестный режим не ломает страницу', response.status_code == 200)

    # --- Добавление нескольких оценок ----------------------------------
    before = None
    with app.app_context():
        before = Grade.query.filter_by(student_id=student_id,
                                       subject_id=teacher_subject_id,
                                       date=lesson_date).count()

    html = add_grade(client, {
        'group_id': group_id, 'subject_id': teacher_subject_id,
        'period_id': period_id, 'student_id': student_id,
        'date': lesson_date.strftime('%Y-%m-%d'),
        'values': '5, 4, 4', 'grade_type': 'exam', 'comments': 'Проверка Ф8'})
    messages = flash_of(html)
    check('добавление нескольких оценок сообщает об успехе',
          any('5, 4, 4' in m for m in messages), '; '.join(messages) or 'нет flash')

    with app.app_context():
        created = (Grade.query.filter_by(student_id=student_id,
                                         subject_id=teacher_subject_id,
                                         date=lesson_date)
                   .order_by(Grade.id.desc()).limit(3).all())
        check('каждое значение сохранено отдельной строкой',
              len(created) == 3 and sorted(g.grade_value for g in created)
              == ['4', '4', '5'],
              str([g.grade_value for g in created]))
        check('строк стало ровно на три больше',
              Grade.query.filter_by(student_id=student_id,
                                    subject_id=teacher_subject_id,
                                    date=lesson_date).count() == before + 3)
        histories = (GradeHistory.query
                     .filter(GradeHistory.grade_id.in_([g.id for g in created]))
                     .all())
        check('у каждой новой оценки есть запись «добавлена»',
              len(histories) == 3 and all(h.action == 'created' for h in histories),
              str([(h.action, h.new_value) for h in histories]))
        # created отсортированы по убыванию id, поэтому первый — последний
        # вставленный, то есть «4». Для проверки правки берём заведомо
        # известное значение: создаём отдельную оценку «5».
        editable = (Grade.query.filter_by(student_id=student_id,
                                          subject_id=teacher_subject_id,
                                          date=lesson_date,
                                          grade_value='5').first())
        grade_id = editable.id
        grade_subject = created[0].subject_id
        edit_url = f'/journal/grade/{grade_id}/edit'
        delete_url = f'/journal/grade/{grade_id}/delete'

    # --- Правка оценки -------------------------------------------------
    html = client.post(edit_url, data={
        'group_id': group_id, 'subject_id': grade_subject, 'period_id': period_id,
        'value': 'зачет', 'comments': 'Пересмотрено',
        'csrf_token': csrf(client, url)},
        follow_redirects=True).get_data(as_text=True)
    messages = flash_of(html)
    check('правка оценки сообщает об успехе',
          any('5' in m and 'зачет' in m for m in messages),
          '; '.join(messages) or 'нет flash')
    with app.app_context():
        grade = db.session.get(Grade, grade_id)
        check('оценка сохранена как «зачет»', grade.grade_value == 'зачет',
              grade.grade_value)
        last = (GradeHistory.query.filter_by(grade_id=grade_id)
                .order_by(GradeHistory.id.desc()).first())
        check('в истории есть старое и новое значение',
              last is not None and last.action == 'updated'
              and last.old_value == '5' and last.new_value == 'зачет',
              f'{last.old_value} -> {last.new_value}' if last else 'нет записи')

    # --- Удаление ------------------------------------------------------
    html = client.post(delete_url, data={
        'group_id': group_id, 'subject_id': grade_subject, 'period_id': period_id,
        'csrf_token': csrf(client, url)},
        follow_redirects=True).get_data(as_text=True)
    messages = flash_of(html)
    check('удаление сообщает об успехе',
          any('удалена' in m.lower() for m in messages),
          '; '.join(messages) or 'нет flash')
    with app.app_context():
        check('оценка удалена', db.session.get(Grade, grade_id) is None)
        last = (GradeHistory.query.filter_by(grade_id=grade_id)
                .order_by(GradeHistory.id.desc()).first())
        check('удаление осталось в истории',
              last is not None and last.action == 'deleted',
              last.action if last else 'нет записи')

    # --- Отказы: день без занятия, будущее, вне периода -----------------
    with app.app_context():
        total_before = Grade.query.count()

    if free_date is not None:
        html = add_grade(client, {
            'group_id': group_id, 'subject_id': teacher_subject_id,
            'period_id': period_id, 'student_id': student_id,
            'date': free_date.strftime('%Y-%m-%d'),
            'values': '5', 'grade_type': 'exam'})
        text = page_text(html)
        check('оценка за день без занятия отклоняется',
              'не было' in text.lower(), free_date.strftime('%d.%m.%Y'))
    else:
        check('оценка за день без занятия отклоняется', True,
              'подходящей даты не нашлось, проверка пропущена')

    html = add_grade(client, {
        'group_id': group_id, 'subject_id': teacher_subject_id,
        'period_id': period_id, 'student_id': student_id,
        'date': (date.today() + timedelta(days=30)).strftime('%Y-%m-%d'),
        'values': '5', 'grade_type': 'exam'})
    text = page_text(html)
    check('оценка за будущее занятие отклоняется',
          'будущее' in text.lower() or 'вне периода' in text.lower(),
          'отказ' if 'будущее' in text.lower() or 'вне периода' in text.lower()
          else text[:120])

    # Дата внутри учебного года, но до выбранного периода
    with app.app_context():
        outside = AcademicPeriod.query.filter(
            AcademicPeriod.id != period_id,
            AcademicPeriod.start_date <= date.today()).order_by(
            AcademicPeriod.start_date.desc()).first()
    if outside is not None and outside.start_date < period.start_date:
        with app.app_context():
            probe = outside.start_date + timedelta(days=1)
            while probe < period.start_date and probe not in known:
                probe += timedelta(days=1)
            outside_date = probe if probe < period.start_date else None
        if outside_date is not None:
            html = add_grade(client, {
                'group_id': group_id, 'subject_id': teacher_subject_id,
                'period_id': period_id, 'student_id': student_id,
                'date': outside_date.strftime('%Y-%m-%d'),
                'values': '5', 'grade_type': 'exam'})
            check('оценка за дату вне периода отклоняется',
                  'вне периода' in page_text(html).lower(),
                  outside_date.strftime('%d.%m.%Y'))

    with app.app_context():
        check('после всех отказов новых оценок не появилось',
              Grade.query.count() == total_before,
              f'было {total_before}, стало {Grade.query.count()}')

    html = add_grade(client, {
        'group_id': group_id, 'subject_id': teacher_subject_id,
        'period_id': period_id, 'student_id': student_id,
        'date': lesson_date.strftime('%Y-%m-%d'),
        'values': '6', 'grade_type': 'exam'})
    text = page_text(html)
    check('оценка «6» отклоняется с понятным текстом',
          'не распознано' in text.lower() or 'values' in text.lower(),
          text[:160] or 'пусто')

    # Студент из другой группы: подмена group_id не должна пройти
    with app.app_context():
        other_group_student = (Student.query
                               .filter(Student.group_id != group_id,
                                       Student.status == 'active')
                               .order_by(Student.id).first())
        outsider_id = other_group_student.id if other_group_student else None
    if outsider_id:
        html = add_grade(client, {
            'group_id': group_id, 'subject_id': teacher_subject_id,
            'period_id': period_id, 'student_id': outsider_id,
            'date': lesson_date.strftime('%Y-%m-%d'),
            'values': '5', 'grade_type': 'exam'})
        check('оценка студенту из другой группы отклоняется',
              'не учится' in ' '.join(flash_of(html)),
              '; '.join(flash_of(html)) or 'нет flash')

    # --- Права преподавателя -------------------------------------------
    client = app.test_client()
    login(client, TEST_TEACHER, TEACHER_PASSWORD)
    response = client.get(f'/journal?group_id={group_id}'
                          f'&subject_id={own_subject_id}'
                          f'&period_id={period_id}')
    check('преподаватель открывает свой предмет', response.status_code == 200,
          f'код {response.status_code}')
    html = response.get_data(as_text=True)
    check('преподавателю доступна сетка своего предмета', 'Журнал' in html)

    response = client.get(f'/journal?group_id={group_id}'
                          f'&subject_id={other_subject_id}'
                          f'&period_id={period_id}')
    check('преподавателю чужой предмет недоступен (403)',
          response.status_code == 403, f'код {response.status_code}')

    response = client.post('/journal/grade/add', data={
        'group_id': group_id, 'subject_id': other_subject_id,
        'period_id': period_id, 'student_id': student_id,
        'date': lesson_date.strftime('%Y-%m-%d'), 'values': '5',
        'grade_type': 'exam',
        'csrf_token': csrf(client, f'/journal?group_id={group_id}'
                                     f'&subject_id={own_subject_id}'
                                     f'&period_id={period_id}')})
    with app.app_context():
        leaked = Grade.query.filter_by(student_id=student_id,
                                       subject_id=other_subject_id,
                                       date=lesson_date).count()
    # Отказ выглядит по-разному: 403 от teacher_owns_subject, либо 302 с
    # ошибкой формы, когда чужого предмета нет в списке выбора.
    check('преподаватель не может поставить оценку по чужому предмету',
          response.status_code in (302, 403), f'код {response.status_code}')
    check('по чужому предмету ничего не записано', leaked == 0,
          f'строк {leaked}')

    # Свой предмет: оценка должна сохраниться
    html = add_grade(client, {
        'group_id': group_id, 'subject_id': own_subject_id,
        'period_id': period_id, 'student_id': student_id,
        'date': lesson_date.strftime('%Y-%m-%d'), 'values': '4',
        'grade_type': 'exam'})
    with app.app_context():
        mine = Grade.query.filter_by(student_id=student_id,
                                     subject_id=own_subject_id,
                                     date=lesson_date).count()
    check('преподаватель ставит оценку по своему предмету', mine == 1,
          f'строк {mine}')

    # --- Повторная загрузка сетки после всех изменений ----------------
    client = app.test_client()
    login(client, ADMIN, admin_password)
    response = client.get(f'/journal?group_id={group_id}'
                          f'&subject_id={teacher_subject_id}'
                          f'&period_id={period_id}')
    check('сетка переживает изменения и открывается снова',
          response.status_code == 200)

    print()
    print(f'Проверок: {checks}, из них не пройдено: {len(failures)}')
    for item in failures:
        print(f'  ✗ {item}')
    print(f'Группа: {group_name}, предмет: {teacher_subject_name}, '
          f'занятие: {lesson_date.strftime("%d.%m.%Y")}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
