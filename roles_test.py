"""Проверка разграничения прав Фазы 5.

Запуск:  python roles_test.py

Скрипт заводит преподавателя, назначает ему предметы, выдаёт одну учётную
запись студенту и проверяет, что роли видят ровно то, что им положено:

  * администратор — все разделы, включая управление сотрудниками;
  * преподаватель — учебные разделы, но не админские, и только свои предметы;
  * студент — только личный кабинет.

Данные, созданные для проверки, удаляются в конце. Скрипт работает с
настоящей базой: перед началом делается резервная копия, а в конце все
тестовые записи снимаются. Если проверка упадёт посередине, копия остаётся
в backups/ и базу можно вернуть вручную.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import User, Student, Subject, Grade
from app.utils import create_backup

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

TEST_TEACHER = 'test-teacher-zz'
TEST_STUDENT_MARK = 9999999
TEACHER_PASSWORD = 'teacher-pass-1'
STUDENT_PASSWORD = 'student-pass-1'

# (URL, что ожидаем у администратора, у преподавателя, у студента)
# 200 — открывается, 403 — запрещено, 302 — редирект (обычно на вход)
CHECKS = [
    ('/dashboard',            200, 200,   403),
    ('/students',             200, 200,   403),
    ('/groups',               200, 200,   403),
    ('/subjects',             200, 200,   403),
    ('/grades',               200, 200,   403),
    ('/grades/add',           200, 200,   403),
    ('/reports',              200, 200,   403),
    ('/staff',                200, 403,   403),
    ('/staff/add',            200, 403,   403),
    ('/settings',             200, 403,   403),
    ('/settings/backup',      200, 403,   403),
    ('/settings/import',      200, 403,   403),
    ('/settings/logs',        200, 403,   403),
    ('/my/account',           302, 302,   200),
    ('/my/password',          200, 200,   200),
]


def csrf_of(client, url='/login'):
    """Достаёт csrf_token со страницы входа."""
    from smoke_test import extract_csrf
    return extract_csrf(client.get(url).get_data(as_text=True))


def login(client, username, password):
    token = csrf_of(client)
    resp = client.post('/login', data={
        'username': username, 'password': password, 'csrf_token': token,
    }, follow_redirects=False)
    return resp.status_code in (200, 302)


def logout(client):
    client.get('/logout')


def prepare():
    """Готовит тестовые данные и возвращает id созданного."""
    with app.app_context():
        # Чистим возможные остатки прошлого прогона
        stale = User.query.filter_by(username=TEST_TEACHER).first()
        if stale:
            for subject in Subject.query.filter_by(teacher_id=stale.id).all():
                db.session.delete(subject)
            db.session.delete(stale)
            db.session.commit()

        teacher = User(username=TEST_TEACHER, full_name='Тестовый Преподаватель',
                       role=User.ROLE_TEACHER, is_active=True)
        teacher.set_password(TEACHER_PASSWORD)
        db.session.add(teacher)
        db.session.commit()

        # Тестовые предметы — свои, а не чужие. Сид раздаёт все 8 предметов
        # демо-преподавателям; если бы тест отбирал существующие и не сумел
        # вернуть их из-за падения, база осталась бы с чужими данными.
        subjects = []
        for index in (1, 2):
            subject = Subject(name=f'Тестовый предмет {index}', hours=2)
            subject.teacher_id = teacher.id
            db.session.add(subject)
            subjects.append(subject)
        db.session.commit()

        # Учётная запись студента: берём того, у кого её ещё нет.
        # Часть аккаунтов выдаёт init_db.py, поэтому «просто первого»
        # здесь означало бы попытку создать логин, который уже занят.
        student = (Student.query
                   .filter_by(status='active', user_id=None)
                   .order_by(Student.id).first())
        if student is None:
            raise RuntimeError('Нет студента без учётной записи — нечего проверять')
        user = User(username=student.student_id, full_name=student.full_name,
                    role=User.ROLE_STUDENT, is_active=True)
        user.set_password(STUDENT_PASSWORD)
        db.session.add(user)
        db.session.commit()
        student.user_id = user.id
        db.session.commit()

        # Чужой предмет — тот, что ведёт не наш преподаватель. Раньше искали
        # предмет без преподавателя, но после сида таких не осталось.
        foreign = (Subject.query
                   .filter(Subject.teacher_id.isnot(None),
                           Subject.teacher_id != teacher.id)
                   .order_by(Subject.id).first())
        if foreign is None:
            raise RuntimeError('Нет чужого предмета — нечего проверять 403')

        return {'teacher_id': teacher.id,
                'teacher_name': teacher.username,
                'student_login': student.student_id,
                'subject_ids': [s.id for s in subjects],
                'foreign_subject': foreign.id}


def cleanup(ids):
    """Возвращает базу к исходному состоянию."""
    with app.app_context():
        user = User.query.filter_by(username=ids['teacher_name']).first()
        if user:
            for subject in Subject.query.filter_by(teacher_id=user.id).all():
                db.session.delete(subject)
            db.session.delete(user)
        student = Student.query.filter_by(
            student_id=ids['student_login']).first()
        if student:
            student.user_id = None
            account = User.query.filter_by(
                username=ids['student_login'], role=User.ROLE_STUDENT).first()
            if account:
                db.session.delete(account)
        db.session.commit()


def check(url, expected, label, failures):
    got = results[label].get(url)
    if got != expected:
        failures.append(f'{label} {url}: ожидали {expected}, получили {got}')
    return got


def main():
    admin_pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
    admin_name = os.environ.get('ADMIN_USERNAME', 'admin')

    print('--- Разграничение прав (Фаза 5) ---')
    backup = create_backup('перед проверкой ролей')
    print(f'ℹ️  Резервная копия: {backup if isinstance(backup, str) else "не создана"}')

    ids = prepare()
    print(f'ℹ️  Преподаватель: {ids["teacher_name"]}, предметов: {len(ids["subject_ids"])}')
    print(f'ℹ️  Студент: {ids["student_login"]}\n')

    global results
    results = {}
    failures = []

    try:
        for label, username, password in [
                ('админ', admin_name, admin_pw),
                ('преподаватель', ids['teacher_name'], TEACHER_PASSWORD),
                ('студент', ids['student_login'], STUDENT_PASSWORD)]:
            with app.test_client() as client:
                if not login(client, username, password):
                    failures.append(f'{label}: не удалось войти как {username}')
                    results[label] = {}
                    continue
                results[label] = {}
                for url, admin_code, teacher_code, student_code in CHECKS:
                    expected = {'админ': admin_code,
                                'преподаватель': teacher_code,
                                'студент': student_code}[label]
                    resp = client.get(url, follow_redirects=False)
                    results[label][url] = resp.status_code
                    mark = '✓' if resp.status_code == expected else '✗'
                    if resp.status_code != expected:
                        failures.append(
                            f'{label} {url}: ожидали {expected}, '
                            f'получили {resp.status_code}')
                    print(f'{mark} {label:13} {url:20} → {resp.status_code} '
                          f'(ожидали {expected})')
                logout(client)

        # Преподаватель не должен видеть чужие предметы и оценивать по ним
        print()
        foreign = ids.get('foreign_subject')
        own = ids['subject_ids'][0]
        with app.test_client() as client:
            if login(client, ids['teacher_name'], TEACHER_PASSWORD):
                resp = client.get(f'/subjects/{own}/edit', follow_redirects=False)
                ok = resp.status_code == 200
                print(f'{"✓" if ok else "✗"} преподаватель правит свой предмет '
                      f'/subjects/{own}/edit → {resp.status_code}')
                if not ok:
                    failures.append(
                        f'преподаватель /subjects/{own}/edit → {resp.status_code}')

                if foreign:
                    resp = client.get(f'/subjects/{foreign}/edit',
                                      follow_redirects=False)
                    ok = resp.status_code == 403
                    print(f'{"✓" if ok else "✗"} чужой предмет закрыт '
                          f'/subjects/{foreign}/edit → {resp.status_code}')
                    if not ok:
                        failures.append(
                            f'преподаватель /subjects/{foreign}/edit → '
                            f'{resp.status_code}, ожидали 403')

                    resp = client.post(f'/subjects/{foreign}/delete',
                                       follow_redirects=False)
                    ok = resp.status_code == 403
                    print(f'{"✓" if ok else "✗"} удаление чужого предмета закрыто '
                          f'→ {resp.status_code}')
                    if not ok:
                        failures.append(
                            f'преподаватель удаляет чужой предмет → {resp.status_code}')

        # Преподаватель не должен ставить оценку по чужому предмету
        if foreign:
            with app.app_context():
                grade = (Grade.query.filter_by(subject_id=foreign)
                         .order_by(Grade.id).first())
            if grade:
                with app.test_client() as client:
                    if login(client, ids['teacher_name'], TEACHER_PASSWORD):
                        token = csrf_of(client)
                        resp = client.post(
                            f'/grades/{grade.id}/delete',
                            data={'csrf_token': token},
                            follow_redirects=False)
                        ok = resp.status_code == 403
                        print(f'{"✓" if ok else "✗"} удаление чужой оценки закрыто '
                              f'/grades/{grade.id}/delete → {resp.status_code}')
                        if not ok:
                            failures.append(
                                f'преподаватель удаляет чужую оценку → {resp.status_code}')

        # Студент не видит чужого кабинета
        with app.app_context():
            other_student = (Student.query
                             .filter(Student.user_id.is_(None))
                             .order_by(Student.id).first())
        if other_student:
            with app.test_client() as client:
                if login(client, ids['student_login'], STUDENT_PASSWORD):
                    resp = client.get(f'/student/{other_student.id}',
                                      follow_redirects=False)
                    ok = resp.status_code == 403
                    print(f'{"✓" if ok else "✗"} студент не открывает чужую карточку '
                          f'/student/{other_student.id} → {resp.status_code}')
                    if not ok:
                        failures.append(
                            f'студент /student/{other_student.id} → {resp.status_code}')

    finally:
        cleanup(ids)

    print()
    if failures:
        print(f'❌ Провалено: {len(failures)}')
        for item in failures:
            print(f'   - {item}')
        return 1
    print('✅ Разграничение прав работает: админ / преподаватель / студент')
    return 0


if __name__ == '__main__':
    sys.exit(main())
