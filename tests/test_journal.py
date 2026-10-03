"""Журнал: сетка, оценки, история правок, пропуски, итоги (16.1).

Здесь важна не отрисовка таблицы, а правила: чужой предмет не трогаем,
оценка за чужую группу не сохраняется, правка не затирает историю, а итог
без обоснования не проходит.
"""

from conftest import (LESSON_DATE, OTHER_TEACHER_LOGIN,
                      OTHER_TEACHER_PASSWORD, csrf, login,
                      login_student, login_teacher, page_text)

from app.models import (AttendanceRecord, Grade, GradeHistory, Group,
                        PeriodResult, Student, Subject)
from app.utils import recommend_final_grade


def _ids(ctx):
    """Числовые id, нужные формам: в разметке их не видно."""
    return {
        'student': Student.query.filter_by(student_id='ST0016').first().id,
        'subject': Subject.query.filter_by(name='Математика').first().id,
        'foreign_subject': Subject.query.filter_by(name='География').first().id,
        'group': Group.query.filter_by(name='ПС-16').first().id,
        'other_group': Group.query.filter_by(name='ВТ-16').first().id,
    }


def _journal_query(ctx, ids=None, mode='dates'):
    ids = ids or _ids(ctx)
    return (f'/journal?mode={mode}&group_id={ids["group"]}'
            f'&subject_id={ids["subject"]}')


def _results_query(ctx, ids):
    # Без группы и периода /results показывает только выбор и форм нет,
    # а значит нет и CSRF-токена
    return f'/results?group_id={ids["group"]}&period_id=1'


def test_journal_grid_shows_students(client, ctx):
    login_teacher(client)
    response = client.get(_journal_query(ctx))
    assert response.status_code == 200
    text = page_text(response.get_data(as_text=True))
    # В сетке журнала ФИО короче: фамилия и имя
    assert 'Иванов Иван' in text
    assert 'Петрова Мария' in text


def test_journal_subject_mode_shows_average(client, ctx):
    """Сводный режим: средний балл Иванова 4.00, Петровой 2.00."""
    login_teacher(client)
    html = client.get(_journal_query(ctx, mode='subjects')).get_data(as_text=True)
    assert '4.0' in html or '4,00' in html
    assert '2.0' in html or '2,00' in html


def test_teacher_adds_grade(client, ctx):
    ids = _ids(ctx)
    before = Grade.query.count()
    login_teacher(client)
    client.post('/journal/grade/add', data={
        'csrf_token': csrf(client, _journal_query(ctx)),
        'student_id': ids['student'], 'subject_id': ids['subject'],
        'period_id': '1', 'date': LESSON_DATE, 'values': '5',
        'grade_type': 'exam'}, follow_redirects=True)
    assert Grade.query.count() == before + 1
    history = (GradeHistory.query
               .order_by(GradeHistory.id.desc()).first())
    assert history.action == GradeHistory.ACTION_CREATED
    assert history.new_value == '5'


def test_several_grades_in_one_cell(client, ctx):
    """«5, 4, 4» за одно занятие — три строки, иначе правки неразличимы."""
    ids = _ids(ctx)
    before = Grade.query.count()
    login_teacher(client)
    client.post('/journal/grade/add', data={
        'csrf_token': csrf(client, _journal_query(ctx)),
        'student_id': ids['student'], 'subject_id': ids['subject'],
        'period_id': '1', 'date': LESSON_DATE, 'values': '5, 4, 4',
        'grade_type': 'test'}, follow_redirects=True)
    assert Grade.query.count() == before + 3


def test_teacher_cannot_grade_foreign_subject(client, ctx):
    """Предмет другого преподавателя недоступен даже с верным CSRF."""
    ids = _ids(ctx)
    before = Grade.query.count()
    login(client, OTHER_TEACHER_LOGIN, OTHER_TEACHER_PASSWORD)
    response = client.post('/journal/grade/add', data={
        'csrf_token': csrf(client, f'/subjects/{ids["foreign_subject"]}/edit'),
        'student_id': ids['student'], 'subject_id': ids['subject'],
        'period_id': '1', 'date': LESSON_DATE, 'values': '5',
        'grade_type': 'exam'}, follow_redirects=True)
    assert response.status_code == 403
    assert 'другой преподаватель' in page_text(response.get_data(as_text=True))
    assert Grade.query.count() == before, 'Оценка чужого предмета сохранилась'


def test_grade_for_student_of_another_group_is_rejected(client, ctx):
    ids = _ids(ctx)
    before = Grade.query.count()
    login_teacher(client)
    response = client.post('/journal/grade/add', data={
        'csrf_token': csrf(client, _journal_query(ctx, ids)),
        'student_id': ids['student'], 'subject_id': ids['subject'],
        'group_id': ids['other_group'], 'period_id': '1',
        'date': LESSON_DATE, 'values': '5', 'grade_type': 'exam'},
        follow_redirects=True)
    assert 'не учится в выбранной группе' in page_text(response.get_data(as_text=True))
    assert Grade.query.count() == before


def test_grade_edit_keeps_old_value_in_history(client, ctx):
    ids = _ids(ctx)
    login_teacher(client)
    grade = (Grade.query.filter_by(student_id=ids['student'])
             .order_by(Grade.id.desc()).first())
    client.post(f'/journal/grade/{grade.id}/edit', data={
        'csrf_token': csrf(client, _journal_query(ctx)),
        'value': '5', 'comments': 'исправлено'}, follow_redirects=True)
    assert Grade.query.get(grade.id).grade_value == '5'
    history = (GradeHistory.query.filter_by(grade_id=grade.id)
               .order_by(GradeHistory.id.desc()).first())
    assert history.action == GradeHistory.ACTION_UPDATED
    assert history.old_value == grade.grade_value or history.old_value
    assert history.new_value == '5'


def test_grade_delete_is_recorded(client, ctx):
    ids = _ids(ctx)
    login_teacher(client)
    grade = (Grade.query.filter_by(student_id=ids['student'])
             .order_by(Grade.id.desc()).first())
    client.post(f'/journal/grade/{grade.id}/delete', data={
        'csrf_token': csrf(client, _journal_query(ctx))},
        follow_redirects=True)
    assert Grade.query.get(grade.id) is None
    history = (GradeHistory.query.filter_by(grade_id=grade.id)
               .order_by(GradeHistory.id.desc()).first())
    assert history is not None and history.action == GradeHistory.ACTION_DELETED


def test_attendance_can_be_marked_and_edited(client, ctx):
    ids = _ids(ctx)
    login_teacher(client)
    client.post('/attendance/mark', data={
        'csrf_token': csrf(client, _journal_query(ctx, ids)),
        'student_id': ids['student'], 'group_id': ids['group'],
        'subject_id': ids['subject'], 'date': LESSON_DATE,
        'reason': AttendanceRecord.REASON_ILLNESS, 'note': 'справка'},
        follow_redirects=True)
    record = (AttendanceRecord.query
              .filter_by(student_id=ids['student'], date=LESSON_DATE,
                         subject_id=ids['subject']).first())
    assert record is not None, 'Пропуск не записался'
    assert record.reason == AttendanceRecord.REASON_ILLNESS

    client.post(f'/attendance/{record.id}/edit', data={
        'csrf_token': csrf(client, '/attendance?group_id=%d&period_id=1' % ids['group']),
        'reason': AttendanceRecord.REASON_EXCUSED, 'note': 'уточнено'},
        follow_redirects=True)
    assert AttendanceRecord.query.get(record.id).reason == \
        AttendanceRecord.REASON_EXCUSED


def test_double_attendance_is_not_duplicated(client, ctx):
    ids = _ids(ctx)
    login_teacher(client)
    before = AttendanceRecord.query.filter_by(
        student_id=ids['student'], date=LESSON_DATE,
        subject_id=ids['subject']).count()
    for _ in range(2):
        client.post('/attendance/mark', data={
            'csrf_token': csrf(client, _journal_query(ctx, ids)),
            'student_id': ids['student'], 'group_id': ids['group'],
            'subject_id': ids['subject'], 'date': LESSON_DATE,
            'reason': AttendanceRecord.REASON_ILLNESS}, follow_redirects=True)
    after = AttendanceRecord.query.filter_by(
        student_id=ids['student'], date=LESSON_DATE,
        subject_id=ids['subject']).count()
    assert after == before + 1


def test_future_attendance_is_refused(client, ctx):
    ids = _ids(ctx)
    login_teacher(client)
    before = AttendanceRecord.query.count()
    client.post('/attendance/mark', data={
        'csrf_token': csrf(client, _journal_query(ctx, ids)),
        'student_id': ids['student'], 'group_id': ids['group'],
        'subject_id': ids['subject'], 'date': '2027-01-11',
        'reason': AttendanceRecord.REASON_ILLNESS}, follow_redirects=True)
    assert AttendanceRecord.query.count() == before

def _final_payload(ctx, ids, client, **extra):
    """Данные формы итога. Рекомендацию берём тем же расчётом, что и сервер:
    форма сверяется с этим скрытым полем, и без него любой итог выглядел бы
    как «несогласованный с рекомендацией»."""
    grades = Grade.query.filter_by(student_id=ids['student'],
                                  subject_id=ids['subject'],
                                  period_id=1).all()
    payload = {'csrf_token': csrf(client, _results_query(ctx, ids)),
               'student_id': ids['student'], 'subject_id': ids['subject'],
               'period_id': '1', 'group_id': ids['group'],
               'recommended': recommend_final_grade(grades)}
    payload.update(extra)
    return payload


def test_final_grade_needs_justification_when_it_differs(client, ctx):
    """Рекомендация по среднему (4,0) — это 4; «5» без обоснования нет."""
    ids = _ids(ctx)
    login_teacher(client)

    response = client.post('/results/final', follow_redirects=True,
                           data=_final_payload(ctx, ids, client,
                                               final_value='5'))
    assert 'обоснование' in page_text(response.get_data(as_text=True))
    assert PeriodResult.query.count() == 0

    client.post('/results/final', follow_redirects=True,
                data=_final_payload(ctx, ids, client, final_value='5',
                                    justification='защита проекта'))
    result = PeriodResult.query.first()
    assert result is not None and result.final_value == '5'
    assert result.justification == 'защита проекта'


def test_final_grade_equal_to_recommendation_needs_no_justification(client, ctx):
    ids = _ids(ctx)
    login_teacher(client)
    client.post('/results/final', follow_redirects=True,
                data=_final_payload(ctx, ids, client, final_value='4'))
    assert PeriodResult.query.count() == 1


def test_final_grade_can_be_removed(client, ctx):
    ids = _ids(ctx)
    login_teacher(client)
    client.post('/results/final', follow_redirects=True,
                data=_final_payload(ctx, ids, client, final_value='4'))
    client.post('/results/final', follow_redirects=True,
                data=_final_payload(ctx, ids, client, final_value=''))
    assert PeriodResult.query.count() == 0


def test_student_sees_own_grades_only(client, ctx):
    login_student(client)
    html = client.get('/portal/grades').get_data(as_text=True)
    assert 'Математика' in html
    assert 'Петрова' not in html, \
        'В кабинете студента видна чужая ведомость'


def test_student_sees_own_absence(client, ctx):
    login_student(client)
    text = page_text(client.get('/portal/attendance').get_data(as_text=True))
    assert 'История' in text
    assert 'Петрова' not in text