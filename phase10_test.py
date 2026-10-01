"""Тесты Фазы 10: итоговые оценки, рейтинг, свод, экспорт, печать.

Проверяется не «страница открылась», а смысл: рекомендация по среднему,
обязательное обоснование при ручном итоге, права на чужой предмет,
совпадение выгрузки с тем, что показано на экране, и отсутствие кнопок в
печатной версии.

База восстанавливается из снимка, поэтому тест можно гонять много раз подряд
и на рабочих данных.

Запуск: python phase10_test.py
"""

import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Group, Student, Subject, Grade, AcademicPeriod,
                        ScheduleItem, LessonDate, AttendanceRecord,
                        PeriodResult)
from app.forms import to_int
from app.utils import recommend_final_grade, grade_quality_stats

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
TEACHER_PASSWORD = 'teacher123'
TEST_TEACHER = 't10_teacher'
TEST_SUBJECT = 'Тест Ф10'

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


def snapshot():
    with app.app_context():
        return {
            'results': [{'id': r.id, 'student': r.student_id,
                         'subject': r.subject_id, 'period': r.period_id,
                         'final': r.final_value,
                         'justification': r.justification, 'by': r.created_by}
                        for r in PeriodResult.query.all()],
            'grades': [{'id': g.id} for g in Grade.query.all()],
            'attendance': [{'id': a.id} for a in AttendanceRecord.query.all()],
            'users': {u.id for u in User.query.all()},
            'subjects': {s.id for s in Subject.query.all()},
            'subject_teachers': {s.id: s.teacher_id
                                 for s in Subject.query.all()},
            'students': {s.id: s.user_id for s in Student.query.all()},
            'schedule': {i.id for i in ScheduleItem.query.all()},
            'lessons': {(row.id, row.schedule_item_id)
                        for row in LessonDate.query.all()},
        }


def restore(state):
    """Возврат базы к снимку.

    Порядок обязателен: итоги, оценки и пропуски ссылаются на предметы,
    студентов и пользователей, часть из которых тест создаёт временно.
    Сначала освобождаем ссылки, потом удаляем временные записи расписания и
    предметов, и только после этого — учётные записи.
    """
    with app.app_context():
        for row in PeriodResult.query.all():
            db.session.delete(row)
        db.session.flush()

        for grade in Grade.query.all():
            if grade.id not in {g['id'] for g in state['grades']}:
                db.session.delete(grade)
        db.session.flush()

        for record in AttendanceRecord.query.all():
            if record.id not in {a['id'] for a in state['attendance']}:
                db.session.delete(record)
        db.session.flush()

        for row in LessonDate.query.all():
            if (row.id, row.schedule_item_id) not in state['lessons']:
                db.session.delete(row)
        db.session.flush()

        for item in ScheduleItem.query.all():
            if item.id not in state['schedule']:
                db.session.delete(item)
        db.session.flush()

        for subject in Subject.query.all():
            if subject.id not in state['subjects']:
                db.session.delete(subject)
        db.session.flush()

        for user in User.query.all():
            if user.id not in state['users']:
                db.session.delete(user)
        db.session.flush()
        for student_id, user_id in state['students'].items():
            student = db.session.get(Student, student_id)
            if student is not None:
                student.user_id = user_id
        for subject_id, teacher_id in state['subject_teachers'].items():
            subject = db.session.get(Subject, subject_id)
            if subject is not None:
                subject.teacher_id = teacher_id
        db.session.commit()

        for row in state['results']:
            db.session.add(PeriodResult(
                id=row['id'], student_id=row['student'],
                subject_id=row['subject'], period_id=row['period'],
                final_value=row['final'], justification=row['justification'],
                created_by=row['by']))
        db.session.commit()


def pick_case():
    """Группа, предмет, студент и период, где есть хотя бы одна оценка.

    Именно пара «студент + предмет» с оценками, а не просто первая
    подходящая: без оценок рекомендация пустая, и проверки «итог совпадает с
    рекомендацией» и «ручной итог отличается от рекомендации» теряли бы
    смысл — сравнивать не с чем.
    """
    with app.app_context():
        period = (AcademicPeriod.query
                  .order_by(AcademicPeriod.academic_year.desc(),
                            AcademicPeriod.sort_order)
                  .first())
        if period is None:
            return None
        graded = (Grade.query
                  .filter(Grade.period_id == period.id,
                          Grade.subject_id.isnot(None))
                  .order_by(Grade.id).first())
        if graded is None:
            return None
        student = db.session.get(Student, graded.student_id)
        subject = db.session.get(Subject, graded.subject_id)
        if student is None or subject is None:
            return None
        return {'group': db.session.get(Group, student.group_id),
                'subject': subject, 'student': student, 'period': period}


def send_final(client, case, value, justification='', recommended=None,
               subject=None, student=None):
    """POST итога с теми же полями, что шлёт модальное окно."""
    subject = subject or case['subject']
    student = student or case['student']
    data = {
        'student_id': student.id,
        'subject_id': subject.id,
        'period_id': case['period'].id,
        'group_id': student.group_id,
        'final_value': value,
        'recommended': recommended or '',
        'justification': justification,
        'csrf_token': csrf(client, '/results'),
    }
    return client.post('/results/final', data=data, follow_redirects=True)


def run_checks():
    case = pick_case()
    if case is None:
        check('подготовка данных (группа, предмет, студент, период)', False,
              'в базе нет расписания или активных студентов')
        return
    check('подготовка данных (группа, предмет, студент, период)', True,
          f'{case["group"].name} · {case["subject"].name}')

    # --- 10.1 расчётные функции -----------------------------------------
    check('рекомендация: без оценок итог не назначается',
          recommend_final_grade([]) == '',
          repr(recommend_final_grade([])))
    check('рекомендация: средний 4,67 → «5»',
          recommend_final_grade(['5', '4', '5']) == '5')
    check('рекомендация: средний 4,33 → «4»',
          recommend_final_grade(['5', '4', '4']) == '4')
    check('рекомендация: средний 3,67 → «3»',
          recommend_final_grade(['4', '4', '3']) == '3')
    check('рекомендация: одна «2» → «2»',
          recommend_final_grade(['2']) == '2')
    check('рекомендация: зачёты остаются зачётами',
          recommend_final_grade(['зачет', 'зачет']) == 'зачет',
          repr(recommend_final_grade(['зачет', 'зачет'])))
    check('рекомендация: незачёт среди зачётов не теряется',
          recommend_final_grade(['зачет', 'незачет']) == 'незачет')

    stats = grade_quality_stats(['5', '4', '3', '2'])
    check('качество: 4 и 5 из четырёх → 50 %',
          stats['quality'] == 50.0, str(stats))
    check('успеваемость: без двоек → 75 %',
          stats['progress'] == 75.0, str(stats))
    check('качество при пустом наборе не делит на ноль',
          grade_quality_stats([]) == {'count': 0, 'quality': 0, 'progress': 0})

    client = app.test_client()
    login(client, 'admin', ADMIN_PASSWORD)

    # --- 10.3 свод и 10.2 рейтинг ----------------------------------------
    response = client.get('/results')
    check('страница свода открывается', response.status_code == 200,
          str(response.status_code))
    html = response.get_data(as_text=True)
    text = page_text(html)
    check('на своде есть рейтинг группы', 'Рейтинг группы' in text)
    check('в своде есть колонка «Итого»', 'Итого' in text)
    check('студент из снимка попал в свод',
          f"{case['student'].last_name} {case['student'].first_name}" in text)
    check('на своде показан рекомендованный итог',
          'recommend' in html.lower() or 'Рекомендация' in text)

    # --- 10.1 постановка и снятие итога ---------------------------------
    with app.app_context():
        grades = [grade.grade_value for grade in
                  Grade.query.filter_by(student_id=case['student'].id,
                                        subject_id=case['subject'].id,
                                        period_id=case['period'].id).all()]
        db.session.remove()
    recommended = recommend_final_grade(grades)
    check('у тестового студента есть оценки для рекомендации',
          bool(grades), f'{len(grades)} оценок, рекомендация {recommended!r}')

    response = send_final(client, case, recommended,
                          recommended=recommended)
    messages = flash_of(response.get_data(as_text=True))
    check('итог, совпадающий с рекомендацией, ставится без обоснования',
          any('итог 4' in m.lower() for m in messages),
          ' | '.join(messages)[:90])
    with app.app_context():
        stored = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=case['subject'].id,
            period_id=case['period'].id).first()
        ok = stored is not None and stored.final_value == recommended
        check('итог сохранён в базе', ok,
              f'{stored.final_value if stored else "нет"}')
        check('обоснование не требуется и не пишется',
              stored is not None and stored.justification is None)
        db.session.remove()

    # Повторная постановка не плодит дубликаты
    send_final(client, case, recommended, recommended=recommended)
    with app.app_context():
        count = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=case['subject'].id,
            period_id=case['period'].id).count()
        check('повторная постановка не создаёт второй итог', count == 1,
              f'записей {count}')
        db.session.remove()

    other = recommended
    for value in ('5', '4', '3', '2', 'зачет', 'незачет'):
        if value != recommended:
            other = value
            break

    response = send_final(client, case, other)
    messages = flash_of(response.get_data(as_text=True))
    check('ручной итог без обоснования отклонён',
          any('обоснование' in m.lower() for m in messages),
          '; '.join(messages)[:90])
    with app.app_context():
        stored = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=case['subject'].id,
            period_id=case['period'].id).first()
        check('в базе осталась прежняя оценка, а не ручная',
              stored is not None and stored.final_value == recommended,
              f'{stored.final_value if stored else "нет"}')
        db.session.remove()

    # Подделка рекомендации в форме не отключает требование обоснования
    response = send_final(client, case, other, recommended=other)
    messages = flash_of(response.get_data(as_text=True))
    check('подсунутая в форму рекомендация не обходит проверку',
          any('обоснование' in m.lower() for m in messages),
          '; '.join(messages)[:90])
    with app.app_context():
        stored = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=case['subject'].id,
            period_id=case['period'].id).first()
        check('после подделки значение не изменилось',
              stored is not None and stored.final_value == recommended,
              f'{stored.final_value if stored else "нет"}')
        db.session.remove()

    response = send_final(client, case, other, justification='Более высокая '
                                                             'оценка за работу')
    messages = flash_of(response.get_data(as_text=True))
    check('ручной итог с обоснованием принимается',
          any(f'итог {other}'.lower() in m.lower() for m in messages),
          ' | '.join(messages)[:90])
    with app.app_context():
        stored = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=case['subject'].id,
            period_id=case['period'].id).first()
        check('ручной итог записан вместе с обоснованием',
              stored is not None and stored.final_value == other
              and bool(stored.justification),
              f'{stored.final_value if stored else "нет"}')
        db.session.remove()

    response = send_final(client, case, '')
    messages = flash_of(response.get_data(as_text=True))
    check('итог можно снять', any('снят' in m.lower() for m in messages),
          '; '.join(messages)[:90])
    with app.app_context():
        count = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=case['subject'].id,
            period_id=case['period'].id).count()
        check('после снятия записи не осталось', count == 0, f'записей {count}')
        db.session.remove()

    # --- права: чужой предмет --------------------------------------------
    # Преподаватель создаётся временно: пароль существующего сотрудника
    # неизвестен, а проверять права нужно на заведомо рабочей учётке.
    with app.app_context():
        stale = User.query.filter_by(username=TEST_TEACHER).first()
        if stale:
            db.session.delete(stale)
            db.session.flush()
        outsider = User(username=TEST_TEACHER, full_name='Проверка Ф10 Преподаватель',
                        role=User.ROLE_TEACHER, is_active=True)
        outsider.set_password(TEACHER_PASSWORD)
        db.session.add(outsider)
        db.session.commit()
        outsider_id = outsider.id
        db.session.remove()

    other_client = app.test_client()
    login(other_client, TEST_TEACHER, TEACHER_PASSWORD)
    check('преподаватель вошёл в систему',
          other_client.get('/results').status_code == 200)

    response = other_client.post('/results/final', data={
        'student_id': case['student'].id,
        'subject_id': case['subject'].id,
        'period_id': case['period'].id,
        'group_id': case['group'].id,
        'final_value': '5', 'recommended': '5',
        'csrf_token': csrf(other_client, '/results')},
        follow_redirects=False)
    check('преподаватель не ставит итог по чужому предмету',
          response.status_code == 403, str(response.status_code))
    with app.app_context():
        count = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=case['subject'].id,
            period_id=case['period'].id).count()
        db.session.remove()
    check('после чужой попытки итог не записан', count == 0,
          f'итогов по паре {count}')

    # Свой предмет преподаватель оформить может — иначе проверка выше была бы
    # успешной просто потому, что кнопка не работает ни у кого.
    with app.app_context():
        # Свободный слот обязателен: у группы уже есть расписание, и пара
        # «понедельник, первая» почти наверняка занята. Ищем тот день и
        # номер, которых ещё нет, иначе нарушится уникальность расписания.
        taken = {(row[0], row[1]) for row in
                 (db.session.query(ScheduleItem.day_of_week,
                                   ScheduleItem.lesson_number)
                  .filter_by(group_id=case['group'].id).all())}
        slot = next((day, number)
                    for day in range(1, 6) for number in range(1, 8)
                    if (day, number) not in taken)

        own_subject = Subject(name=TEST_SUBJECT, teacher_id=outsider_id)
        db.session.add(own_subject)
        # flush нужен до обращения к id: без него в schedule_item попадёт
        # NULL, и внешний ключ отклонит вставку
        db.session.flush()
        db.session.add(ScheduleItem(
            group_id=case['group'].id, subject_id=own_subject.id,
            day_of_week=slot[0], lesson_number=slot[1]))
        db.session.commit()
        own_id = own_subject.id
        db.session.remove()

    # По предмету без единой оценки рекомендации нет, поэтому «5» — это
    # ручной итог и обоснование обязательно. Иначе можно было бы поставить
    # «отлично» студенту, которому ничего не выставляли.
    response = other_client.post('/results/final', data={
        'student_id': case['student'].id,
        'subject_id': own_id,
        'period_id': case['period'].id,
        'group_id': case['group'].id,
        'final_value': '5', 'recommended': '',
        'csrf_token': csrf(other_client, '/results')},
        follow_redirects=True)
    messages = flash_of(response.get_data(as_text=True))
    check('по предмету без оценок «5» без обоснования не проходит',
          any('обоснование' in m.lower() for m in messages),
          ' | '.join(messages)[:90])

    with app.app_context():
        db.session.add(Grade(student_id=case['student'].id,
                             subject_id=own_id, grade_value='5',
                             date=case['period'].start_date,
                             period_id=case['period'].id))
        db.session.commit()
        db.session.remove()

    response = other_client.post('/results/final', data={
        'student_id': case['student'].id,
        'subject_id': own_id,
        'period_id': case['period'].id,
        'group_id': case['group'].id,
        'final_value': '5', 'recommended': '5',
        'csrf_token': csrf(other_client, '/results')},
        follow_redirects=True)
    messages = flash_of(response.get_data(as_text=True))
    check('преподаватель ставит итог по своему предмету',
          any('итог 5' in m.lower() for m in messages),
          ' | '.join(messages)[:90])
    with app.app_context():
        count = PeriodResult.query.filter_by(
            student_id=case['student'].id, subject_id=own_id).count()
        db.session.remove()
    check('итог по своему предмету сохранён', count == 1, f'записей {count}')

    # --- 10.4 / 10.5 экспорт ---------------------------------------------
    import openpyxl

    response = client.get('/journal/export?fmt=xlsx')
    check('выгрузка журнала в Excel отвечает файлом', response.status_code == 200,
          str(response.status_code))
    if response.status_code == 200:
        book = openpyxl.load_workbook(io.BytesIO(response.data))
        check('в книге три листа: журнал, сводная, пропуски',
              book.sheetnames == ['Журнал', 'Сводная', 'Пропуски'],
              str(book.sheetnames))
        header = [cell.value for cell in book['Журнал'][1]]
        check('первый лист начинается со строки «Студент»',
              header and header[0] == 'Студент', str(header[:3]))
        check('в шапке есть итоговый столбец среднего',
              header and header[-1] == 'Средний', str(header[-2:]))
        absence_header = [cell.value for cell in book['Пропуски'][1]]
        check('лист пропусков содержит причину и примечание',
              absence_header == ['Студент', 'Дата', 'Предмет', 'Причина',
                                 'Примечание'], str(absence_header))

    response = client.get('/journal/export?fmt=csv')
    check('выгрузка журнала в CSV отвечает файлом', response.status_code == 200,
          str(response.status_code))
    if response.status_code == 200:
        text = response.data.decode('utf-8-sig')
        lines = [line for line in text.splitlines() if line.strip()]
        check('CSV начинается с кода 1С-шапки',
              lines[0] == 'Студент,Группа,Предмет,Дата,Оценка,Пропуск',
              lines[0][:70])
        check('CSV без BOM-мусора в кириллице', 'Группа' in lines[0])

    response = client.get('/journal/export?mode=subjects&fmt=csv')
    if response.status_code == 200:
        text = response.data.decode('utf-8-sig')
        dates = [line.split(',')[3] for line in text.splitlines()[1:] if line]
        check('CSV из сводного режима всё равно содержит даты',
              all(re.match(r'^\d{4}-\d{2}-\d{2}$', value) for value in dates),
              f'пример: {dates[:3]}')

    response = client.get('/results/export?fmt=xlsx')
    check('выгрузка свода в Excel отвечает файлом', response.status_code == 200,
          str(response.status_code))
    if response.status_code == 200:
        book = openpyxl.load_workbook(io.BytesIO(response.data))
        check('в книге свода два листа: свод и рейтинг',
              len(book.sheetnames) == 2, str(book.sheetnames))
        header = [cell.value for cell in book[book.sheetnames[0]][1]]
        check('содержание свода выгружено по парам «студент + предмет»',
              'Рекомендованный итог' in header, str(header[:9]))
        rating_header = [cell.value for cell in book[book.sheetnames[1]][1]]
        check('рейтинг выгружен с местами и процентами',
              rating_header[:2] == ['Место', 'Студент']
              and 'Качество, %' in rating_header, str(rating_header))

    response = client.get('/results/export?fmt=csv')
    check('выгрузка свода в CSV отвечает файлом', response.status_code == 200,
          str(response.status_code))

    response = client.get('/journal/export?fmt=pdf', follow_redirects=False)
    check('неизвестный формат выгрузки не отдаёт файл',
          response.status_code == 302, str(response.status_code))

    # --- 10.6 печать ------------------------------------------------------
    html = client.get('/journal').get_data(as_text=True)
    check('в журнале есть кнопка печати', 'window.print()' in html)
    check('меню и верхняя панель помечены no-print',
          'no-print' in client.get('/results').get_data(as_text=True))

    with app.app_context():
        css = open(os.path.join(app.root_path, 'static', 'css',
                                'journal.css'), encoding='utf-8').read()
    print_block = re.search(r'@media print\s*\{(.*?)\n\}', css, re.DOTALL)
    check('в стилях есть блок @media print', print_block is not None)
    if print_block:
        block = print_block.group(1)
        check('печать скрывает элементы с классом no-print',
              '.no-print' in block)
        check('печать скрывает боковое меню', '.sidebar' in block)
        check('печать убирает кнопки правки ячеек',
              '.result-edit' in block and '.absence-add' in block)
        check('печать повторяет шапку таблицы на каждой странице',
              'table-header-group' in block)

    # --- печать не ломает саму страницу -----------------------------------
    with app.app_context():
        blocks = client.get('/results').get_data(as_text=True)
    check('страница свода остаётся доступной после добавления печати',
          'Рейтинг группы' in page_text(blocks))


def main():
    state = snapshot()
    try:
        run_checks()
    finally:
        restore(state)
        print('\nБаза восстановлена из снимка.')

    print()
    if failures:
        print(f'ПРОВАЛЕНО {len(failures)} из {checks}:')
        for item in failures:
            print(' -', item)
        return 1
    print(f'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({checks}/{checks})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
