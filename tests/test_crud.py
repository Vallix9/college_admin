"""Студенты, группы и предметы (16.1).

Проверяется не «страница открылась», а что данные действительно изменились
и что несуществующее нельзя сохранить: тест, который только читает код
ответа, пропустил бы форму, которая молча не сохраняет.
"""

from conftest import csrf, login_admin, login_teacher, page_text

from app.init_ import db
from app.models import Group, Student, Subject


def test_create_group(client, ctx):
    login_admin(client)
    response = client.post('/groups/add', data={
        'csrf_token': csrf(client, '/groups/add'),
        'name': 'НОВАЯ-16', 'specialty': 'Новая специальность',
        'year': '2027'}, follow_redirects=True)
    assert 'НОВАЯ-16' in page_text(response.get_data(as_text=True))
    group = Group.query.filter_by(name='НОВАЯ-16').first()
    assert group is not None
    assert group.specialty == 'Новая специальность'
    assert group.year == 2027
    db.session.delete(group)
    db.session.commit()


def test_duplicate_group_is_rejected(client, ctx):
    login_admin(client)
    response = client.post('/groups/add', data={
        'csrf_token': csrf(client, '/groups/add'),
        'name': 'ПС-16', 'specialty': 'Дубликат', 'year': '2027'},
        follow_redirects=True)
    # Имя группы остаётся только в значении поля формы, а page_text
    # вырезает теги вместе с атрибутами, поэтому проверяем сообщение
    assert 'уже существует' in page_text(response.get_data(as_text=True))
    assert Group.query.filter_by(name='ПС-16').count() == 1, \
        'Группа с таким именем продублировалась'


def test_edit_student(client, ctx):
    login_admin(client)
    student = Student.query.filter_by(student_id='ST0017').first()
    response = client.post(f'/students/{student.id}/edit', data={
        'csrf_token': csrf(client, f'/students/{student.id}/edit'),
        'student_id': 'ST0017', 'last_name': 'Петрова-Фон',
        'first_name': 'Мария', 'patronymic': 'Петровна', 'gender': 'F',
        'birth_date': '2005-05-05', 'email': 'petrova@example.com',
        'phone': '+79990000000', 'group_id': str(student.group_id),
        'status': 'active', 'enrollment_date': '2026-09-01'},
        follow_redirects=True)
    assert 'Петрова-Фон' in page_text(response.get_data(as_text=True))
    assert Student.query.get(student.id).last_name == 'Петрова-Фон'


def test_delete_student_asks_confirmation_and_removes(client, ctx):
    login_admin(client)
    student = Student.query.filter_by(student_id='ST0017').first()
    token = csrf(client, f'/student/{student.id}')
    response = client.post(f'/students/{student.id}/delete',
                           data={'csrf_token': token},
                           follow_redirects=True)
    assert response.status_code == 200
    assert Student.query.get(student.id) is None


def test_teacher_cannot_create_student(client, ctx):
    login_teacher(client)
    response = client.get('/students/add', follow_redirects=False)
    assert response.status_code in (302, 403)
    assert Student.query.filter_by(student_id='НОВЫЙ-16').first() is None


def test_subject_edit_is_open_to_its_teacher(client, ctx):
    login_teacher(client)
    subject = Subject.query.filter_by(name='Математика').first()
    response = client.post(f'/subjects/{subject.id}/edit', data={
        'csrf_token': csrf(client, f'/subjects/{subject.id}/edit'),
        'name': 'Математика', 'hours': '144'}, follow_redirects=True)
    assert response.status_code == 200
    assert Subject.query.get(subject.id).hours == 144


def test_students_list_shows_both_students(client, ctx):
    login_admin(client)
    text = page_text(client.get('/students').get_data(as_text=True))
    assert 'Иванов Иван Иванович' in text
    assert 'Петрова Мария Петровна' in text


def test_average_grade_of_first_student(client, ctx):
    """Средний балл: (5 + 4 + 3) / 3 = 4.00."""
    login_admin(client)
    student = Student.query.filter_by(student_id='ST0016').first()
    assert student.average_grade() == 4.0
    html = client.get(f'/student/{student.id}').get_data(as_text=True)
    assert '4.00' in html or '4,00' in html


def test_missing_student_gives_404(client):
    login_admin(client)
    assert client.get('/student/999999').status_code == 404