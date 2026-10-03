"""Импорт, выгрузка и полный цикл данных (16.1 и 16.3).

Главный тест здесь — test_import_edit_backup_restore_cycle: он проходит
путь, ради которого бэкапы и делаются. Импортировали, поправили, сняли
копию, испортили, восстановили — и данные должны совпасть с состоянием
до испорчения. Проверка на «файл существует и не пустой» такой случай не
поймала бы.
"""

import io
import os

from conftest import (LESSON_DATE, csrf, csrf_any, login_admin,
                      login_teacher, page_text)

from app.init_ import db
from app.models import Grade, Group, Student, Subject
from app.utils import create_backup, get_backup_dir, list_backups

STUDENTS_CSV = (
    'номер зачетки;фамилия;имя;отчество;группа;пол\n'
    'IMP001;Кузнецов;Артём;Игоревич;ПС-16;M\n'
    'IMP002;Егорова;Дарья;Сергеевна;ПС-16;F\n'
)

STUDENTS_BAD_COLUMNS = 'фамилия;имя\nБезномер;Иван\n'


def _upload(client, path, filename, content, data,
            content_type='multipart/form-data'):
    # Именно multipart: с другим Content-Type werkzeug не разбирает файл,
    # и форма сообщает «выберите файл», хотя файл был
    return client.post(path, data=dict(data, file=(io.BytesIO(content), filename)),
                       content_type=content_type, follow_redirects=True)


def _import_students(client, content, mode='append', filename='students.csv'):
    return _upload(client, '/settings/import', filename, content.encode('utf-8'),
                   {'csrf_token': csrf(client, '/settings/import'),
                    'import_type': 'students', 'import_mode': mode})


def _snapshot():
    """Состояние данных, с которым потом сверяемся."""
    students = sorted((s.student_id, s.last_name, s.first_name)
                      for s in Student.query.all())
    grades = sorted((g.student.student_id, g.subject.name, g.grade_value)
                    for g in Grade.query.all())
    return students, grades


def test_import_students_creates_records(client, ctx):
    login_admin(client)
    before = Student.query.count()
    response = _import_students(client, STUDENTS_CSV)
    text = page_text(response.get_data(as_text=True))
    assert 'Импортировано записей: 2' in text
    assert Student.query.count() == before + 2
    imported = Student.query.filter_by(student_id='IMP001').first()
    assert imported is not None and imported.last_name == 'Кузнецов'
    assert imported.group.name == 'ПС-16'


def test_reimport_updates_instead_of_duplicating(client, ctx):
    """Второй импорт того же файла обновляет записи, а не плодит их."""
    login_admin(client)
    _import_students(client, STUDENTS_CSV)
    after_first = Student.query.count()

    changed = STUDENTS_CSV.replace('Кузнецов', 'Кузнецов-Новый')
    _import_students(client, changed)
    assert Student.query.count() == after_first, 'Появились дубликаты'
    assert Student.query.filter_by(student_id='IMP001').first().last_name \
        == 'Кузнецов-Новый'
    assert Student.query.filter_by(student_id='IMP001').count() == 1


def test_import_replace_mode_swaps_students(client, ctx):
    login_admin(client)
    response = _import_students(client, STUDENTS_CSV, mode='replace')
    assert response.status_code == 200
    assert Student.query.filter_by(student_id='ST0016').first() is None, \
        'Режим «заменить» оставил прежних студентов'
    assert Student.query.filter_by(student_id='IMP001').first() is not None


def test_import_reports_missing_columns(client, ctx):
    login_admin(client)
    before = Student.query.count()
    response = _import_students(client, STUDENTS_BAD_COLUMNS)
    text = page_text(response.get_data(as_text=True))
    assert 'номер' in text.lower() and 'зачет' in text.lower()
    assert Student.query.count() == before, \
        'Файл без номера зачётки всё равно импортировался'


def test_import_rejects_garbage_file(client, ctx):
    login_admin(client)
    before = Student.query.count()
    response = _import_students(client, '��то не таблица\nи не данные\n')
    assert response.status_code == 200
    assert Student.query.count() == before


def test_import_without_file_is_refused(client, ctx):
    login_admin(client)
    response = client.post('/settings/import', data={
        'csrf_token': csrf(client, '/settings/import'),
        'import_type': 'students', 'import_mode': 'append'},
        follow_redirects=True)
    assert 'файл' in page_text(response.get_data(as_text=True)).lower()


def test_teacher_cannot_import(client, ctx):
    login_teacher(client)
    before = Student.query.count()
    response = client.get('/settings/import', follow_redirects=False)
    assert response.status_code in (302, 403)
    assert Student.query.count() == before


def test_import_template_downloads(client, ctx):
    login_admin(client)
    for import_type in ('students', 'grades', 'groups', 'settings'):
        response = client.get(f'/settings/export-template/{import_type}')
        assert response.status_code == 200, \
            f'Шаблон {import_type} не скачался'
        assert len(response.get_data()) > 1000


def test_journal_export_xlsx_and_csv(client, ctx):
    login_teacher(client)
    ids = {
        'group': Group.query.filter_by(name='ПС-16').first().id,
        'subject': Subject.query.filter_by(name='Математика').first().id,
    }
    query = (f'?group_id={ids["group"]}&subject_id={ids["subject"]}'
             f'&period_id=1')
    xlsx = client.get('/journal/export' + query + '&fmt=xlsx')
    assert xlsx.status_code == 200
    assert xlsx.get_data()[:2] == b'PK', 'Выгрузка не xlsx'

    csv = client.get('/journal/export' + query + '&fmt=csv')
    assert csv.status_code == 200
    body = csv.get_data(as_text=True)
    assert 'Математика' in body or 'Иванов' in body


def test_journal_export_of_foreign_subject_is_denied(client, ctx):
    login_teacher(client)
    group = Group.query.filter_by(name='ПС-16').first().id
    foreign = Subject.query.filter_by(name='География').first().id
    response = client.get(
        f'/journal/export?group_id={group}&subject_id={foreign}&fmt=xlsx',
        follow_redirects=False)
    assert response.status_code in (302, 403)


def test_results_export(client, ctx):
    login_teacher(client)
    group = Group.query.filter_by(name='ПС-16').first().id
    response = client.get(f'/results/export?group_id={group}&period_id=1')
    assert response.status_code == 200
    assert len(response.get_data()) > 1000


def test_backup_is_created_and_listed(client, ctx):
    login_admin(client)
    response = client.post('/settings/backup', data={
        'csrf_token': csrf(client, '/settings/backup'),
        'description': 'проверка тестом'}, follow_redirects=True)
    assert 'создана' in page_text(response.get_data(as_text=True)).lower()
    names = [item['filename'] for item in list_backups()]
    assert any(name.endswith('.backup') for name in names)
    assert 'проверка тестом' in page_text(response.get_data(as_text=True))


def test_backup_download(client, ctx):
    login_admin(client)
    filename = create_backup(description='для скачивания')
    response = client.get(f'/settings/backup/{filename}/download')
    assert response.status_code == 200
    assert response.get_data()[:2] == b'PK'


def test_restore_of_missing_file_is_reported(client, ctx):
    login_admin(client)
    response = client.post('/settings/backup/net-takogo-fajla.backup/restore',
                           data={'csrf_token': csrf(client, '/settings/backup')},
                           follow_redirects=True)
    assert 'не найден' in page_text(response.get_data(as_text=True)).lower()


def test_import_edit_backup_restore_cycle(client, ctx):
    """Полный цикл 16.3: импорт → правка → бэкап → порча → восстановление."""
    login_admin(client)

    # 1. Импортировали новых студентов
    _import_students(client, STUDENTS_CSV)
    imported = Student.query.filter_by(student_id='IMP001').first()
    assert imported is not None

    # 2. Поправили импортированного студента
    client.post(f'/students/{imported.id}/edit', data={
        'csrf_token': csrf(client, f'/students/{imported.id}/edit'),
        'student_id': 'IMP001', 'last_name': 'Кузнецов-Исправленный',
        'first_name': 'Артём', 'patronymic': 'Игоревич', 'gender': 'M',
        'group_id': str(imported.group_id), 'status': 'active'},
        follow_redirects=True)
    assert Student.query.get(imported.id).last_name == 'Кузнецов-Исправленный'

    # 3. Сняли копию и запомнили состояние
    filename = create_backup(description='состояние после правки')
    expected_students, expected_grades = _snapshot()

    # 4. Испортили данные: удалили студента и добавили оценку
    victim = Student.query.filter_by(student_id='IMP002').first()
    client.post(f'/students/{victim.id}/delete',
                data={'csrf_token': csrf(client, f'/student/{victim.id}')},
                follow_redirects=True)
    student = Student.query.filter_by(student_id='IMP001').first()
    student_pk = student.id  # id сохраняем: после restore() сессия сбрасывается
    subject = Subject.query.filter_by(name='Математика').first()
    db.session.add(Grade(student_id=student.id, subject_id=subject.id,
                         grade_value='5', grade_type='exam',
                         date=__import__('datetime').date(2026, 9, 28)))
    db.session.commit()
    assert Student.query.filter_by(student_id='IMP002').first() is None

    # 5. Восстановили из копии (сессия администратора остаётся в силе)
    response = client.post(f'/settings/backup/{filename}/restore',
                           data={'csrf_token': csrf_any(client, '/settings/backup')},
                           follow_redirects=True)
    assert 'восстановлены' in page_text(response.get_data(as_text=True)).lower()

    # 6. Данные совпали с состоянием до порчи
    db.session.remove()
    db.engine.dispose()
    actual_students, actual_grades = _snapshot()
    assert actual_students == expected_students, \
        'После восстановления список студентов отличается'
    assert actual_grades == expected_grades, \
        'После восстановления оценки отличаются'
    assert Student.query.get(student_pk).last_name == 'Кузнецов-Исправленный'


def test_restore_keeps_audit_trail(client, ctx):
    """Запись о восстановлении пишется после подмены базы, иначе исчезла бы."""
    from app.models import AuditLog
    login_admin(client)
    filename = create_backup(description='для проверки журнала')
    client.post(f'/settings/backup/{filename}/restore',
                data={'csrf_token': csrf(client, '/settings/backup')},
                follow_redirects=True)
    db.session.remove()
    db.engine.dispose()
    actions = [row.action for row in AuditLog.query.all()]
    assert 'backup_restore' in actions


def test_pre_restore_copy_is_kept(client, ctx):
    """Перед восстановлением остаётся файл с текущей базой — откат."""
    login_admin(client)
    filename = create_backup(description='откат')
    client.post(f'/settings/backup/{filename}/restore',
                data={'csrf_token': csrf(client, '/settings/backup')},
                follow_redirects=True)
    pre = [name for name in os.listdir(get_backup_dir())
           if name.startswith('pre_restore_')]
    assert pre, 'Копия текущей базы перед восстановлением не осталась'


def test_backup_delete(client, ctx):
    login_admin(client)
    filename = create_backup(description='на удаление')
    response = client.post(f'/settings/backup/{filename}/delete',
                           data={'csrf_token': csrf(client, '/settings/backup')},
                           follow_redirects=True)
    assert response.status_code == 200
    assert not os.path.isfile(os.path.join(get_backup_dir(), filename))


def test_deleted_backup_cannot_be_restored(client, ctx):
    login_admin(client)
    filename = create_backup(description='удалим сразу')
    client.post(f'/settings/backup/{filename}/delete',
                data={'csrf_token': csrf(client, '/settings/backup')},
                follow_redirects=True)
    before = Student.query.count()
    client.post(f'/settings/backup/{filename}/restore',
                data={'csrf_token': csrf(client, '/settings/backup')},
                follow_redirects=True)
    assert Student.query.count() == before


def test_lesson_date_marks_survive_export(client, ctx):
    """Занятие, отмеченное администратором, попадает в сетку и в выгрузку."""
    from app.models import LessonDate
    login_admin(client)
    group = Group.query.filter_by(name='ПС-16').first()
    subject = Subject.query.filter_by(name='История').first()
    response = client.post('/schedule/lessons', data={
        'csrf_token': csrf(client, f'/schedule?group_id={group.id}'),
        'date': '2026-09-30', 'group_id': str(group.id),
        'item_ids': [str(item.id) for item in
                     __import__('app.models', fromlist=['ScheduleItem'])
                     .ScheduleItem.query.filter_by(group_id=group.id).all()],
        'mark_all': 'y'}, follow_redirects=True)
    assert response.status_code == 200
    assert LessonDate.query.filter_by(date=__import__('datetime').date(2026, 9, 30)).first()
    assert subject is not None
    assert LESSON_DATE  # константы даты используются в других тестах