from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file
from flask_login import login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from datetime import datetime, date
from sqlalchemy import or_, func, desc
import os
from app.init_ import db
from app.models import User, Student, Group, Subject, Grade, SystemSettings
from app.forms import LoginForm, StudentForm, GroupForm, GradeForm, SubjectForm, SettingsForm, BackupForm, ImportForm
from app.forms import ClearLogsForm
from app.utils import export_to_excel, format_date, create_backup, restore_backup, import_from_file
from app.utils import list_backups, delete_backup, get_backup_dir, sanitize_filename
from app.utils import get_logger, average_grade, build_import_template
from app.utils import read_log_lines, get_log_file_path, clear_log_file

log = get_logger()

main = Blueprint('main', __name__)

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def flash_msg(type, message):
    icons = {'success': '✅', 'error': '❌', 'warning': '⚠️', 'info': 'ℹ️'}
    flash(f"{icons.get(type, '')} {message}", type if type != 'error' else 'danger')

def get_settings():
    return SystemSettings.get_settings()

def safe_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def is_safe_redirect(target):
    """Проверяет, что цель редиректа ведёт внутри приложения.

    Отклоняем абсолютные URL, протокол-относительные ('//host', '/\\host') и
    любые цели без ведущего слэша — иначе получаем open redirect.
    """
    if not target:
        return False
    if not target.startswith('/'):
        return False
    if target.startswith('//') or target.startswith('/\\'):
        return False
    return urlparse(target).netloc == '' and not urlparse(target).scheme

def get_pagination_args():
    return {
        'page': request.args.get('page', 1, type=int),
        'per_page': get_settings().items_per_page or 20
    }

def apply_filters(query, model):
    """Применяет фильтры из строки запроса и возвращает их же для шаблона.

    Возвращённый словарь используется формами фильтрации, поэтому в него
    попадает и search — иначе поле поиска теряет введённое значение при
    переходе на следующую страницу.
    """
    filters = {}
    search = (request.args.get('search') or '').strip()
    if search:
        search_term = f'%{search}%'
        if model is Student:
            query = query.filter(or_(
                Student.last_name.ilike(search_term),
                Student.first_name.ilike(search_term),
                Student.patronymic.ilike(search_term),
                Student.student_id.ilike(search_term)
            ))
        elif model is Subject:
            query = query.filter(Subject.name.ilike(search_term))
        filters['search'] = search

    group_id = request.args.get('group')
    if group_id and group_id != 'all':
        query = query.filter_by(group_id=group_id)
        filters['group'] = group_id

    status = request.args.get('status')
    if status and status != 'all':
        query = query.filter_by(status=status)
        filters['status'] = status

    # Фильтр по полу есть в students.html, но бэкенд его не обрабатывал —
    # выбор молча ничего не менял
    gender = request.args.get('gender')
    if gender and gender != 'all':
        query = query.filter_by(gender=gender)
        filters['gender'] = gender

    return query, filters

def group_student_counts(groups_list, status='active'):
    """Число активных студентов по каждой группе одним запросом.

    group.student_count() дёргал отдельный COUNT на каждую групку, а
    шаблоны зовут его по два-три раза на группу.
    """
    if not groups_list:
        return {}

    rows = db.session.query(
        Student.group_id,
        db.func.count(Student.id)
    ).filter(
        Student.group_id.in_([g.id for g in groups_list]),
        Student.status == status
    ).group_by(Student.group_id).all()

    counts = {group_id: count for group_id, count in rows}
    return {g.id: counts.get(g.id, 0) for g in groups_list}

def student_average_grades():
    """Средние баллы всех студентов одним запросом.

    students.html звал student.average_grade() четыре раза на строку, а
    relationship grades ленивый — на каждого студента уходил отдельный
    SELECT. Считаем пачкой.
    """
    rows = db.session.query(Grade.student_id, Grade.grade_value).all()
    buckets = {}
    for student_id, value in rows:
        buckets.setdefault(student_id, []).append(value)
    return {student_id: average_grade(values) for student_id, values in buckets.items()}


def subject_grade_counts():
    """Число оценок по каждому предмету одним запросом (для subjects.html)."""
    rows = db.session.query(
        Grade.subject_id, db.func.count(Grade.id)
    ).group_by(Grade.subject_id).all()
    return dict(rows)

# ===================== АУТЕНТИФИКАЦИЯ =====================
@main.route('/')
@main.route('/dashboard')
@login_required
def dashboard():
    try:
        settings = get_settings()
        stats = {
            'total_students': Student.query.count(),
            'active_students': Student.query.filter_by(status='active').count(),
            'total_groups': Group.query.count(),
            'male_students': Student.query.filter_by(gender='M').count(),
            'female_students': Student.query.filter_by(gender='F').count(),
        }
        
        recent_students = Student.query.order_by(desc(Student.created_at)).limit(5).all()
        # joinedload, иначе обращение к grade.student в шаблоне даёт
        # отдельный SELECT на каждую из десяти оценок
        recent_grades = (Grade.query
                         .options(joinedload(Grade.student), joinedload(Grade.subject))
                         .order_by(desc(Grade.created_at)).limit(10).all())
        
        return render_template('dashboard.html', 
                             stats=stats, 
                             recent_students=recent_students, 
                             recent_grades=recent_grades,
                             settings=settings)
    except Exception as e:
        flash_msg('error', f'Ошибка загрузки дашборда: {str(e)}')
        return render_template('dashboard.html', stats={}, recent_students=[], recent_grades=[])

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            next_page = request.args.get('next')
            
            if not is_safe_redirect(next_page):
                next_page = url_for('main.dashboard')
            
            user.last_login_at = datetime.now()
            db.session.commit()
            log.info('Вход выполнен: %s (роль: %s) с %s',
                     user.username, user.role, request.remote_addr)
            flash_msg('success', f'Добро пожаловать, {user.username}!')
            return redirect(next_page)
        
        log.warning('Неудачная попытка входа: логин «%s» с %s',
                    form.username.data, request.remote_addr)
        flash_msg('error', 'Неверное имя пользователя или пароль')
    
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    log.info('Выход: %s', current_user.username)
    logout_user()
    flash_msg('success', 'Вы успешно вышли из системы')
    return redirect(url_for('main.login'))

# ===================== СТУДЕНТЫ =====================
@main.route('/students')
@login_required
def students():
    query = Student.query
    query, filters = apply_filters(query, Student)
    page_args = get_pagination_args()
    students_paginated = query.order_by(Student.last_name).paginate(
        page=page_args['page'], per_page=page_args['per_page'], error_out=False)
    
    return render_template('students.html', 
                         students=students_paginated,
                         groups=Group.query.all(),
                         current_filters=filters,
                         averages=student_average_grades())

@main.route('/students/add', methods=['GET', 'POST'])
@login_required
def add_student():
    form = StudentForm()
    form.group_id.choices = [(0, 'Без группы')] + [(g.id, g.name) for g in Group.query.all()]
    if form.validate_on_submit():
        try:
            student = Student(
                student_id=form.student_id.data,
                last_name=form.last_name.data,
                first_name=form.first_name.data,
                patronymic=form.patronymic.data,
                gender=form.gender.data,
                birth_date=form.birth_date.data,
                email=form.email.data,
                phone=form.phone.data,
                group_id=form.group_id.data if form.group_id.data != 0 else None,
                status=form.status.data,
                enrollment_date=form.enrollment_date.data
            )
            db.session.add(student)
            db.session.commit()
            log.info('Добавлен студент: %s (группа: %s, статус: %s) — %s',
                     student.full_name, student.group.name if student.group else '—',
                     student.status, current_user.username)
            flash_msg('success', f'Студент {student.full_name} успешно добавлен')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при добавлении студента: %s', form.student_id.data)
            flash_msg('error', 'Запись с такими данными уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка добавления студента: %s', e)
            flash_msg('error', f'Ошибка добавления студента: {str(e)}')
    
    return render_template('student_form.html', form=form, title='Добавить студента', student=None)

@main.route('/students/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_student(id):
    student = Student.query.get_or_404(id)
    form = StudentForm(obj=student)
    form.group_id.choices = [(0, 'Без группы')] + [(g.id, g.name) for g in Group.query.all()]
    if form.validate_on_submit():
        try:
            form.populate_obj(student)
            student.group_id = form.group_id.data if form.group_id.data != 0 else None
            db.session.commit()
            log.info('Изменён студент: %s — %s', student.student_id, current_user.username)
            flash_msg('success', f'Данные студента {student.full_name} обновлены')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при изменении студента: %s', form.student_id.data)
            flash_msg('error', 'Запись с такими данными уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка изменения студента: %s', e)
            flash_msg('error', f'Ошибка обновления студента: {str(e)}')
    
    return render_template('student_form.html', form=form, title='Редактировать студента', student=student)

@main.route('/students/<int:id>/delete', methods=['POST'])
@login_required
def delete_student(id):
    student = Student.query.get_or_404(id)
    full_name = student.full_name
    try:
        db.session.delete(student)
        db.session.commit()
        log.warning('Удалён студент: %s — %s', full_name, current_user.username)
        flash_msg('success', f'Студент {full_name} удален')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления студента %s: %s', full_name, e)
        flash_msg('error', f'Ошибка удаления студента: {str(e)}')
    return redirect(url_for('main.students'))

# ===================== СТРАНИЦА СТУДЕНТА =====================
@main.route('/student/<int:student_id>')
@login_required
def view_student(student_id):
    """Детальная страница студента с управлением оценками"""
    student = Student.query.get_or_404(student_id)
    # Получаем все оценки студента
    grades = Grade.query.filter_by(student_id=student_id).order_by(Grade.date.desc()).all()
    
    # Группируем оценки по предметам
    grades_by_subject = {}
    for grade in grades:
        subject_id = grade.subject_id
        if subject_id not in grades_by_subject:
            grades_by_subject[subject_id] = {
                'subject': grade.subject,
                'grades': [],
                'average': 0
            }
        grades_by_subject[subject_id]['grades'].append(grade)
    
    # Вычисляем средний балл по каждому предмету
    for data in grades_by_subject.values():
        data['average'] = average_grade(data['grades'])
    
    # Все доступные предметы
    all_subjects = Subject.query.order_by(Subject.name).all()
    
    # Создаем формы для шаблона
    grade_form = GradeForm()
    grade_form.subject_id.choices = [(s.id, s.name) for s in all_subjects]
    subject_form = SubjectForm()
    
    # Текущая дата для шаблона
    today = date.today()
    
    return render_template('student_detail.html',
                         student=student,
                         grades_by_subject=grades_by_subject,
                         all_subjects=all_subjects,
                         grade_form=grade_form,
                         subject_form=subject_form,
                         today=today)

# ===================== ДОБАВЛЕНИЕ И УДАЛЕНИЕ ОЦЕНОК СТУДЕНТА =====================
@main.route('/students/<int:student_id>/grades/add', methods=['POST'])
@login_required
def add_grade_to_student(student_id):
    """Добавление оценки конкретному студенту со страницы студента"""
    student = Student.query.get_or_404(student_id)
    
    # Получаем данные из формы
    subject_id = request.form.get('subject_id')
    grade_value = request.form.get('grade_value')
    grade_type = request.form.get('grade_type', 'exam')
    comments = request.form.get('comments', '')
    
    if not subject_id or not grade_value:
        flash_msg('error', 'Пожалуйста, заполните все обязательные поля')
        return redirect(url_for('main.view_student', student_id=student_id))
    
    try:
        # Проверяем, существует ли предмет
        subject = Subject.query.get(subject_id)
        if not subject:
            flash_msg('error', 'Выбранный предмет не найден')
            return redirect(url_for('main.view_student', student_id=student_id))
        
        # Создаем новую оценку
        grade = Grade(
            student_id=student_id,
            subject_id=subject_id,
            grade_value=str(grade_value),
            grade_type=grade_type,
            date=date.today(),
            comments=comments
        )
        db.session.add(grade)
        db.session.commit()
        log.info('Оценка добавлена: студент %s, предмет «%s», значение %s (%s) — %s',
                 student.full_name, subject.name, grade_value, grade_type,
                 current_user.username)
        flash_msg('success', f'Оценка по предмету "{subject.name}" успешно добавлена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка добавления оценки студенту %s: %s', student_id, e)
        flash_msg('error', f'Ошибка добавления оценки: {str(e)}')
    
    return redirect(url_for('main.view_student', student_id=student_id))

@main.route('/students/<int:student_id>/grades/<int:grade_id>/delete', methods=['POST'])
@login_required
def delete_student_grade(student_id, grade_id):
    """Удаление оценки студента (основной эндпоинт для шаблона)"""
    try:
        grade = Grade.query.get_or_404(grade_id)
        # Проверяем, что оценка принадлежит студенту
        if grade.student_id != student_id:
            flash_msg('error', 'Оценка не принадлежит данному студенту')
            log.warning('Отклонено удаление чужой оценки: оценка %d, студент %d — %s',
                        grade_id, student_id, current_user.username)
            return redirect(url_for('main.view_student', student_id=student_id))
        
        info = 'предмет %s, значение %s' % (grade.subject.name, grade.grade_value)
        db.session.delete(grade)
        db.session.commit()
        log.warning('Оценка удалена: %s — %s', info, current_user.username)
        flash_msg('success', 'Оценка удалена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления оценки %d: %s', grade_id, e)
        flash_msg('error', f'Ошибка удаления оценки: {str(e)}')
    
    return redirect(url_for('main.view_student', student_id=student_id))

# Альтернативное имя для совместимости
@main.route('/students/<int:student_id>/grades/<int:grade_id>/remove', methods=['POST'])
@login_required
def delete_grade_from_student(student_id, grade_id):
    """Альтернативный эндпоинт для удаления оценки"""
    return delete_student_grade(student_id, grade_id)

# ===================== ГРУППЫ =====================
@main.route('/groups')
@login_required
def groups():
    groups_list = Group.query.order_by(Group.name).all()
    # Один запрос вместо student_count() на каждую группу в шаблоне (N+1)
    return render_template('groups.html', groups=groups_list,
                           student_counts=group_student_counts(groups_list))

@main.route('/groups/add', methods=['GET', 'POST'])
@login_required
def add_group():
    form = GroupForm()
    if form.validate_on_submit():
        try:
            group = Group(
                name=form.name.data,
                specialty=form.specialty.data,
                year=form.year.data
            )
            db.session.add(group)
            db.session.commit()
            log.info('Добавлена группа: %s (%s) — %s',
                     group.name, group.specialty, current_user.username)
            flash_msg('success', f'Группа {group.name} успешно добавлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат группы: %s', form.name.data)
            flash_msg('error', 'Группа с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка добавления группы: %s', e)
            flash_msg('error', f'Ошибка добавления группы: {str(e)}')
    
    return render_template('group_form.html', form=form, title='Добавить группу', group=None)

@main.route('/groups/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_group(id):
    group = Group.query.get_or_404(id)
    form = GroupForm(obj=group)
    if form.validate_on_submit():
        try:
            form.populate_obj(group)
            db.session.commit()
            log.info('Изменена группа: %s — %s', group.name, current_user.username)
            flash_msg('success', f'Группа {group.name} обновлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при изменении группы: %s', form.name.data)
            flash_msg('error', 'Группа с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка изменения группы: %s', e)
            flash_msg('error', f'Ошибка обновления группы: {str(e)}')
    
    return render_template('group_form.html', form=form, title='Редактировать группу', group=group)

@main.route('/groups/<int:id>/delete', methods=['POST'])
@login_required
def delete_group(id):
    group = Group.query.get_or_404(id)
    name = group.name
    try:
        db.session.delete(group)
        db.session.commit()
        log.warning('Удалена группа: %s — %s', name, current_user.username)
        flash_msg('success', f'Группа {name} удалена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления группы %s: %s', name, e)
        flash_msg('error', f'Ошибка удаления группы: {str(e)}')
    return redirect(url_for('main.groups'))

# ===================== ПРЕДМЕТЫ =====================
@main.route('/subjects')
@login_required
def subjects():
    """Список всех предметов"""
    subjects_list = Subject.query.order_by(Subject.name).all()
    return render_template('subjects.html', subjects=subjects_list,
                           grade_counts=subject_grade_counts())

@main.route('/subjects/add', methods=['GET', 'POST'])
@login_required
def add_subject():
    """Добавление нового предмета (ОДНА ФУНКЦИЯ - НЕТ ДУБЛИРОВАНИЯ)"""
    if request.method == 'POST':
        # Обработка быстрого добавления из текстового поля
        subjects_text = request.form.get('subjects_text')
        if subjects_text:
            lines = subjects_text.strip().split('\n')
            added_count = 0
            for line in lines:
                line = line.strip()
                if line:
                    # Парсим строку: "Название Часы" или просто "Название"
                    parts = line.split()
                    if len(parts) >= 2 and parts[-1].isdigit():
                        name = ' '.join(parts[:-1])
                        hours = int(parts[-1])
                    else:
                        name = line
                        hours = 72
                    
                    # Проверяем, существует ли уже предмет
                    existing = Subject.query.filter_by(name=name).first()
                    if not existing:
                        try:
                            subject = Subject(name=name, hours=hours)
                            db.session.add(subject)
                            added_count += 1
                        except Exception as e:
                            log.error('Не удалось добавить предмет «%s»: %s', name, e)
                            continue
            
            if added_count > 0:
                db.session.commit()
                log.info('Массово добавлено предметов: %d — %s',
                         added_count, current_user.username)
                flash_msg('success', f'Добавлено {added_count} новых предметов')
            else:
                flash_msg('warning', 'Не удалось добавить ни одного предмета (возможно, они уже существуют)')
            
            return redirect(url_for('main.subjects'))
        else:
            # Обработка обычной формы
            name = request.form.get('name')
            hours = request.form.get('hours', 72, type=int)
            
            if not name:
                flash_msg('error', 'Введите название предмета')
                return redirect(url_for('main.subjects'))
            
            try:
                subject = Subject(
                    name=name,
                    hours=hours
                )
                db.session.add(subject)
                db.session.commit()
                log.info('Добавлен предмет: «%s», %d ч. — %s',
                         subject.name, hours, current_user.username)
                flash_msg('success', f'Предмет "{subject.name}" успешно добавлен')
                return redirect(url_for('main.subjects'))
            except IntegrityError:
                db.session.rollback()
                log.error('Дубликат предмета: %s', name)
                flash_msg('error', 'Предмет с таким названием уже существует')
            except Exception as e:
                db.session.rollback()
                log.exception('Ошибка добавления предмета: %s', e)
                flash_msg('error', f'Ошибка добавления предмета: {str(e)}')
    
    # GET запрос - показываем форму
    form = SubjectForm()
    return render_template('subject_form.html', form=form, title='Добавить предмет')

@main.route('/subjects/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_subject(id):
    """Редактирование предмета"""
    subject = Subject.query.get_or_404(id)
    form = SubjectForm(obj=subject)
    if form.validate_on_submit():
        try:
            form.populate_obj(subject)
            db.session.commit()
            log.info('Изменён предмет: «%s» — %s', subject.name, current_user.username)
            flash_msg('success', f'Предмет "{subject.name}" обновлен')
            return redirect(url_for('main.subjects'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при изменении предмета: %s', form.name.data)
            flash_msg('error', 'Предмет с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка изменения предмета: %s', e)
            flash_msg('error', f'Ошибка обновления предмета: {str(e)}')
    
    return render_template('subject_form.html', form=form, title='Редактировать предмет', subject=subject)

@main.route('/subjects/<int:id>/delete', methods=['POST'])
@login_required
def delete_subject(id):
    """Удаление предмета"""
    subject = Subject.query.get_or_404(id)
    try:
        # Проверяем, есть ли оценки по этому предмету
        grade_count = Grade.query.filter_by(subject_id=id).count()
        if grade_count > 0:
            log.warning('Удаление предмета «%s» отклонено: %d оценок',
                        subject.name, grade_count)
            flash_msg('error', f'Нельзя удалить предмет "{subject.name}", так как по нему уже есть {grade_count} оценок')
            return redirect(url_for('main.subjects'))
        name = subject.name
        db.session.delete(subject)
        db.session.commit()
        log.warning('Удалён предмет: «%s» — %s', name, current_user.username)
        flash_msg('success', f'Предмет "{name}" удален')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления предмета: %s', e)
        flash_msg('error', f'Ошибка удаления предмета: {str(e)}')
    
    return redirect(url_for('main.subjects'))

# ===================== ОЦЕНКИ =====================
@main.route('/grades')
@login_required
def grades():
    """Список всех оценок"""
    # Получаем параметры фильтрации
    group_id = request.args.get('group', type=int)
    student_id = request.args.get('student', type=int)
    # Создаем базовый запрос
    # options подгружают студента, группу и предмет вместе со строкой:
    # без них обращения в шаблоне дают отдельный SELECT на каждую оценку
    query = Grade.query.join(Student).join(Subject).options(
        joinedload(Grade.student).joinedload(Student.group),
        joinedload(Grade.subject)
    )
    
    # Применяем фильтры
    if group_id:
        query = query.filter(Student.group_id == group_id)
    if student_id:
        query = query.filter(Grade.student_id == student_id)
    
    # Пагинация
    page_args = get_pagination_args()
    grades_paginated = query.order_by(desc(Grade.date)).paginate(
        page=page_args['page'], per_page=page_args['per_page'], error_out=False)
    
    # Получаем данные для фильтров
    groups = Group.query.all()
    students = Student.query.order_by(Student.last_name).all()
    
    # Подготавливаем фильтры для отображения
    current_filters = {}
    if group_id:
        current_filters['group'] = group_id
    if student_id:
        current_filters['student'] = student_id
    
    return render_template('grades.html', 
                         grades=grades_paginated,
                         groups=groups,
                         students=students,
                         current_filters=current_filters)

@main.route('/grades/add', methods=['GET', 'POST'])
@login_required
def add_grade():
    """Добавление новой оценки"""
    form = GradeForm()
    if form.validate_on_submit():
        try:
            # Проверяем, существует ли студент и предмет
            student = Student.query.get(form.student_id.data)
            subject = Subject.query.get(form.subject_id.data)
            
            if not student:
                flash_msg('error', 'Выбранный студент не найден')
                return redirect(url_for('main.add_grade'))
            
            if not subject:
                flash_msg('error', 'Выбранный предмет не найден')
                return redirect(url_for('main.add_grade'))
            
            grade = Grade(
                student_id=form.student_id.data,
                subject_id=form.subject_id.data,
                grade_value=str(form.grade_value.data),
                grade_type=form.grade_type.data,
                date=form.date.data,
                comments=form.comments.data
            )
            db.session.add(grade)
            db.session.commit()
            log.info('Оценка добавлена: %s, предмет «%s», значение %s (%s) от %s — %s',
                     student.full_name, subject.name, form.grade_value.data,
                     form.grade_type.data, form.date.data, current_user.username)
            flash_msg('success', f'Оценка по предмету "{subject.name}" для студента {student.full_name} успешно добавлена')
            return redirect(url_for('main.grades'))
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка добавления оценки: %s', e)
            flash_msg('error', f'Ошибка добавления оценки: {str(e)}')
    
    return render_template('grade_form.html', form=form, title='Добавить оценку')

@main.route('/grades/<int:id>/delete', methods=['POST'])
@login_required
def delete_grade(id):
    """Удаление оценки"""
    grade = Grade.query.get_or_404(id)
    info = 'студент %s, предмет «%s», значение %s' % (
        grade.student.full_name, grade.subject.name, grade.grade_value)
    try:
        db.session.delete(grade)
        db.session.commit()
        log.warning('Оценка удалена: %s — %s', info, current_user.username)
        flash_msg('success', 'Оценка удалена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления оценки: %s', e)
        flash_msg('error', f'Ошибка удаления оценки: {str(e)}')
    return redirect(url_for('main.grades'))

# ===================== ОТЧЕТЫ =====================
@main.route('/reports')
@login_required
def reports():
    groups_list = Group.query.order_by(Group.name).all()
    return render_template('reports.html', groups=groups_list,
                           student_counts=group_student_counts(groups_list),
                           total_grades=db.session.query(
                               db.func.count(Grade.id)).scalar() or 0,
                           subject_count=Subject.query.count())

@main.route('/reports/generate', methods=['POST'])
@login_required
def generate_report():
    report_type = request.form.get('report_type')
    group_id = request.form.get('group_id')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    try:
        if report_type == 'students':
            query = Student.query
            if group_id:
                query = query.filter_by(group_id=group_id)
            
            data = [{
                'Номер зачетки': s.student_id,
                'ФИО': s.full_name,
                'Группа': s.group.name if s.group else '',
                'Дата рождения': format_date(s.birth_date),
                'Email': s.email or '',
                'Телефон': s.phone or '',
                'Статус': s.status
            } for s in query.all()]
            
            filepath = export_to_excel(data, 'students_report')
            log.info('Экспорт отчёта: студенты (%s строк) — %s',
                     len(data), current_user.username)
            return send_file(filepath, as_attachment=True)
        
        elif report_type == 'grades':
            query = Grade.query
            
            if group_id:
                query = query.join(Student).filter(Student.group_id == group_id)
            
            if start_date:
                query = query.filter(Grade.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
            
            if end_date:
                query = query.filter(Grade.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
            
            data = [{
                'Студент': g.student.full_name if g.student else '',
                'Группа': g.student.group.name if g.student and g.student.group else '',
                'Предмет': g.subject.name if g.subject else '',
                'Оценка': g.grade_value,
                'Тип оценки': g.grade_type,
                'Дата': format_date(g.date),
                'Комментарий': g.comments or ''
            } for g in query.all()]
            
            filepath = export_to_excel(data, 'grades_report')
            log.info('Экспорт отчёта: оценки (%s строк) — %s',
                     len(data), current_user.username)
            return send_file(filepath, as_attachment=True)
        
        flash_msg('error', 'Неверный тип отчета')
        return redirect(url_for('main.reports'))
        
    except Exception as e:
        log.exception('Ошибка генерации отчёта: %s', e)
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

@main.route('/reports/students')
@login_required
def report_students():
    """Экспорт всех студентов"""
    try:
        students = Student.query.all()
        data = []
        for student in students:
            data.append({
                'Номер зачетки': student.student_id,
                'ФИО': student.full_name,
                'Группа': student.group.name if student.group else '',
                'Дата рождения': format_date(student.birth_date),
                'Email': student.email or '',
                'Телефон': student.phone or '',
                'Статус': student.status
            })
        filepath = export_to_excel(data, 'students_report')
        log.info('Экспорт всех студентов (%s строк) — %s', len(data), current_user.username)
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        log.exception('Ошибка экспорта студентов: %s', e)
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

@main.route('/reports/group/<int:group_id>')
@login_required
def report_group(group_id):
    """Экспорт студентов группы"""
    try:
        group = Group.query.get_or_404(group_id)
        students = Student.query.filter_by(group_id=group_id).all()
        data = []
        for student in students:
            data.append({
                'Номер зачетки': student.student_id,
                'ФИО': student.full_name,
                'Группа': group.name,
                'Дата рождения': format_date(student.birth_date),
                'Email': student.email or '',
                'Телефон': student.phone or '',
                'Статус': student.status
            })
        
        filepath = export_to_excel(data, f'group_{group.name}_report')
        log.info('Экспорт группы «%s» (%s строк) — %s',
                 group.name, len(data), current_user.username)
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

# ===================== НАСТРОЙКИ =====================
@main.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Настройки системы: сохранение, смена пароля, сброс к умолчаниям"""
    current = get_settings()
    form = SettingsForm(obj=current)

    # Сброс обрабатываем до validate_on_submit: сброшенная форма невалидна,
    # потому что поля основных настроек обязательны
    if form.reset.data and request.method == 'POST':
        SystemSettings.reset_to_default()
        log.warning('Настройки сброшены к значениям по умолчанию — %s', current_user.username)
        flash_msg('success', 'Настройки сброшены к значениям по умолчанию')
        return redirect(url_for('main.settings'))

    if form.validate_on_submit():
        changed = []
        for field in ('college_name', 'academic_year', 'max_students_per_group',
                      'theme_color', 'items_per_page', 'export_format',
                      'auto_backup', 'backup_frequency',
                      'enable_system_notifications', 'enable_email_notifications'):
            new_value = getattr(form, field).data
            if getattr(current, field) != new_value:
                changed.append(field)
                setattr(current, field, new_value)

        if form.new_password.data:
            current_user.set_password(form.new_password.data)
            changed.append('пароль')

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка сохранения настроек: %s', e)
            flash_msg('error', f'Не удалось сохранить настройки: {e}')
            return render_template('settings.html', form=form, settings=current,
                                   **_settings_stats())

        if changed:
            log.info('Настройки изменены (%s) — %s', ', '.join(changed), current_user.username)
            message = 'Настройки сохранены'
            if 'пароль' in changed:
                message += ', пароль обновлён'
            flash_msg('success', message)
        else:
            flash_msg('info', 'Изменений не было')
        return redirect(url_for('main.settings'))

    if form.errors:
        log.warning('Форма настроек не прошла валидацию: %s', ', '.join(form.errors))
        flash_msg('error', 'Проверьте правильность заполнения полей')

    return render_template('settings.html', form=form, settings=current,
                           **_settings_stats())


def _settings_stats():
    """Счётчики для блока «Сведения» на странице настроек."""
    return {'stats': {
        'students': Student.query.count(),
        'groups': Group.query.count(),
        'subjects': Subject.query.count(),
    }}

# ===================== РЕЗЕРВНОЕ КОПИРОВАНИЕ =====================
@main.route('/settings/backup', methods=['GET', 'POST'])
@login_required
def settings_backup():
    """Страница управления резервными копиями"""
    form = BackupForm()
    backups = list_backups()

    if form.validate_on_submit():
        try:
            filename = create_backup(description=form.description.data or '')
            log.info('Создана резервная копия «%s» — %s',
                     filename, current_user.username)
            flash_msg('success', f'Резервная копия «{filename}» создана')
            return redirect(url_for('main.settings_backup'))
        except Exception as e:
            log.exception('Ошибка создания резервной копии: %s', e)
            flash_msg('error', f'Ошибка создания резервной копии: {str(e)}')
            return redirect(url_for('main.settings_backup'))

    return render_template('settings_backup.html', form=form, backups=backups)

@main.route('/settings/backup/<filename>/download')
@login_required
def download_backup_file(filename):
    """Скачивание резервной копии"""
    path = os.path.join(get_backup_dir(), os.path.basename(sanitize_filename(filename)))
    if not os.path.isfile(path):
        log.warning('Скачивание несуществующей копии «%s» — %s', filename, current_user.username)
        flash_msg('error', 'Файл резервной копии не найден')
        return redirect(url_for('main.settings_backup'))
    log.info('Скачана резервная копия «%s» — %s', filename, current_user.username)
    return send_file(path, as_attachment=True)

@main.route('/settings/backup/<filename>/restore', methods=['POST'])
@login_required
def restore_backup_file(filename):
    """Восстановление базы данных из резервной копии"""
    path = os.path.join(get_backup_dir(), os.path.basename(sanitize_filename(filename)))
    if not os.path.isfile(path):
        log.warning('Восстановление несуществующей копии «%s» — %s', filename, current_user.username)
        flash_msg('error', 'Файл резервной копии не найден')
        return redirect(url_for('main.settings_backup'))

    try:
        restore_backup(path)
        log.warning('База восстановлена из копии «%s» — %s', filename, current_user.username)
        flash_msg('success', 'Данные восстановлены из резервной копии')
    except Exception as e:
        log.exception('Ошибка восстановления из «%s»: %s', filename, e)
        flash_msg('error', f'Ошибка восстановления: {str(e)}')

    return redirect(url_for('main.settings_backup'))

@main.route('/settings/backup/<filename>/delete', methods=['POST'])
@login_required
def delete_backup_file(filename):
    """Удаление файла резервной копии"""
    if delete_backup(filename):
        log.warning('Удалена резервная копия «%s» — %s', filename, current_user.username)
        flash_msg('success', 'Резервная копия удалена')
    else:
        log.warning('Не удалось удалить резервную копию «%s» — %s', filename, current_user.username)
        flash_msg('error', 'Не удалось удалить резервную копию')
    return redirect(url_for('main.settings_backup'))

# ===================== ИМПОРТ ДАННЫХ =====================
@main.route('/settings/import', methods=['GET', 'POST'])
@login_required
def settings_import():
    """Страница импорта данных из Excel/CSV"""
    form = ImportForm()
    result = None

    if form.validate_on_submit():
        file = form.file.data
        if file and file.filename:
            import_type = form.import_type.data
            import_mode = form.import_mode.data
            try:
                result = import_from_file(file, import_type=import_type,
                                          import_mode=import_mode)
                log.info('Импорт выполнен: тип %s, режим %s, записей %s, '
                         'пропущено %s — %s',
                         import_type, import_mode, result.get('count'),
                         result.get('skipped', 0), current_user.username)
                if result.get('errors_total'):
                    # Часть строк не прошла проверку — отчёт показываем
                    # на странице, а не теряем среди прочих flash-сообщений
                    result['fatal'] = None
                    flash_msg('warning', result['message'])
                else:
                    flash_msg('success', result['message'])
            except ValueError as e:
                # Ошибки самого файла: показываем на странице, чтобы можно
                # было понять, что исправлять
                log.warning('Файл импорта отклонён (%s): %s',
                            import_type, e)
                db.session.rollback()
                result = {'type': import_type, 'mode': import_mode, 'count': 0,
                          'skipped': 0, 'columns': [],
                          'errors': [str(e)], 'errors_total': 1,
                          'fatal': str(e),
                          'message': f'Импорт не выполнен: {e}'}
                flash_msg('error', f'Импорт не выполнен: {e}')
            except Exception as e:
                log.exception('Ошибка импорта: %s', e)
                db.session.rollback()
                flash_msg('error', f'Ошибка импорта: {str(e)}')
        else:
            log.warning('Импорт без выбранного файла — %s', current_user.username)
            flash_msg('error', 'Выберите файл для импорта')

    return render_template('settings_import.html', form=form, result=result)


@main.route('/settings/export-template/<import_type>')
@login_required
def export_import_template(import_type):
    """Скачивание .xlsx-шаблона с правильными названиями столбцов"""
    try:
        filepath, filename = build_import_template(import_type)
    except ValueError as e:
        log.warning('Запрошен неизвестный шаблон импорта: %s', import_type)
        flash_msg('error', str(e))
        return redirect(url_for('main.settings_import'))

    log.info('Скачан шаблон импорта «%s» — %s', filename, current_user.username)
    return send_file(filepath, as_attachment=True,
                     download_name=filename)

# ===================== ЖУРНАЛ СОБЫТИЙ =====================
LOG_LEVELS = ('INFO', 'WARNING', 'ERROR', 'DEBUG')
LOG_PAGE_SIZE = 200


@main.route('/settings/logs')
@login_required
def view_logs():
    """Просмотр журнала событий с фильтрацией по уровню и поиском"""
    raw_levels = request.args.getlist('level') or list(LOG_LEVELS[:3])
    levels = [lvl for lvl in LOG_LEVELS if lvl in raw_levels] or list(LOG_LEVELS)

    search = (request.args.get('q') or '').strip()[:200]
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    filtered = read_log_lines(limit=0, level=levels,
                              search=search or None)
    total = len(filtered)
    pages = max(1, (total + LOG_PAGE_SIZE - 1) // LOG_PAGE_SIZE)
    page = min(page, pages)
    offset = (page - 1) * LOG_PAGE_SIZE
    logs = filtered[offset:offset + LOG_PAGE_SIZE]

    return render_template(
        'view_logs.html', logs=logs, levels=levels, all_levels=list(LOG_LEVELS),
        search=search, page=page, pages=pages, total=total,
        clear_form=ClearLogsForm(),
        from_index=offset + 1 if total else 0,
        to_index=offset + len(logs))


@main.route('/api/system/download-logs')
@login_required
def download_logs():
    """Скачивание текущего журнала как .txt"""
    path = get_log_file_path()
    if not os.path.isfile(path):
        flash_msg('error', 'Файл журнала ещё не создан')
        return redirect(url_for('main.view_logs'))

    log.info('Скачан журнал событий — %s', current_user.username)
    return send_file(path, as_attachment=True, mimetype='text/plain',
                     download_name=os.path.basename(path))


@main.route('/api/system/clear-logs', methods=['POST'])
@login_required
def clear_logs():
    """Очистка журнала.

    Глобального CSRFProtect в проекте нет, поэтому токен проверяет форма:
    вызывающий обязан передать csrf_token в теле или в заголовке X-CSRFToken.
    """
    form = ClearLogsForm()
    # validate(), а не validate_on_submit(): эндпоинт вызывается из JS без
    # кнопки отправки, поэтому поля submit в теле запроса нет
    if not form.validate():
        log.warning('Очистка журнала отклонена: недействительный CSRF-токен — %s',
                    current_user.username)
        return {'success': False, 'error': 'Недействительный CSRF-токен'}, 400

    if not clear_log_file():
        log.warning('Попытка очистить несуществующий журнал — %s',
                    current_user.username)
        return {'success': False, 'error': 'Файл журнала ещё не создан'}, 404

    # Запись после очистки: сам факт очистки тоже должен попасть в журнал
    log.warning('Журнал событий очищен вручную — %s', current_user.username)
    return {'success': True}

# ===================== API =====================
@main.route('/health')
def health_check():
    return {'status': 'ok', 'timestamp': datetime.now().isoformat()}