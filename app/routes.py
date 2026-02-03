from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.urls import url_parse
from sqlalchemy.exc import IntegrityError
from datetime import datetime, date
from sqlalchemy import or_, func, desc
import functools
import traceback
import os
import json
import secrets
from app.init_ import db
from app.models import User, Student, Group, Subject, Grade, SystemSettings
from app.forms import LoginForm, StudentForm, GroupForm, GradeForm, SubjectForm, SettingsForm, BackupForm, ImportForm
from app.utils import export_to_excel, format_date, create_backup, restore_backup, import_from_file

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

def get_pagination_args():
    return {
        'page': request.args.get('page', 1, type=int),
        'per_page': get_settings().items_per_page or 20
    }

def apply_filters(query, model):
    filters = {}
    if 'search' in request.args:
        search = request.args.get('search', '')
        if search:
            search_term = f'%{search}%'
            if model == Student:
                query = query.filter(or_(
                    Student.last_name.ilike(search_term),
                    Student.first_name.ilike(search_term),
                    Student.student_id.ilike(search_term)
                ))
    
    if 'group' in request.args and request.args.get('group') != 'all':
        group_id = request.args.get('group')
        query = query.filter_by(group_id=group_id)
        filters['group'] = group_id
    
    if 'status' in request.args and request.args.get('status') != 'all':
        status = request.args.get('status')
        query = query.filter_by(status=status)
        filters['status'] = status
    
    return query, filters

# ===================== ДЕКОРАТОРЫ =====================
def handle_db_exceptions(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Запись с такими данными уже существует')
            return None
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка операции: {str(e)}')
            return None
    return wrapper

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
        recent_grades = Grade.query.order_by(desc(Grade.created_at)).limit(10).all()
        
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
            
            if not next_page or url_parse(next_page).netloc != '':
                next_page = url_for('main.dashboard')
            
            flash_msg('success', f'Добро пожаловать, {user.username}!')
            return redirect(next_page)
        
        flash_msg('error', 'Неверное имя пользователя или пароль')
    
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
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
                         current_filters=filters)

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
            flash_msg('success', f'Студент {student.full_name} успешно добавлен')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Запись с такими данными уже существует')
        except Exception as e:
            db.session.rollback()
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
            flash_msg('success', f'Данные студента {student.full_name} обновлены')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Запись с такими данными уже существует')
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка обновления студента: {str(e)}')
    
    return render_template('student_form.html', form=form, title='Редактировать студента', student=student)

@main.route('/students/<int:id>/delete', methods=['POST'])
@login_required
def delete_student(id):
    student = Student.query.get_or_404(id)
    try:
        db.session.delete(student)
        db.session.commit()
        flash_msg('success', f'Студент {student.full_name} удален')
    except Exception as e:
        db.session.rollback()
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
    for subject_id, data in grades_by_subject.items():
        numeric_grades = []
        for grade in data['grades']:
            try:
                if str(grade.grade_value).isdigit():
                    numeric_grades.append(int(grade.grade_value))
                elif grade.grade_value == 'зачет':
                    numeric_grades.append(5)
                elif grade.grade_value == 'незачет':
                    numeric_grades.append(2)
            except:
                continue
        
        if numeric_grades:
            data['average'] = sum(numeric_grades) / len(numeric_grades)
        else:
            data['average'] = 0
    
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
        flash_msg('success', f'Оценка по предмету "{subject.name}" успешно добавлена')
    except Exception as e:
        db.session.rollback()
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
            return redirect(url_for('main.view_student', student_id=student_id))
        
        db.session.delete(grade)
        db.session.commit()
        flash_msg('success', 'Оценка удалена')
    except Exception as e:
        db.session.rollback()
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
    return render_template('groups.html', groups=Group.query.all())

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
            flash_msg('success', f'Группа {group.name} успешно добавлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Группа с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
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
            flash_msg('success', f'Группа {group.name} обновлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Группа с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка обновления группы: {str(e)}')
    
    return render_template('group_form.html', form=form, title='Редактировать группу', group=group)

@main.route('/groups/<int:id>/delete', methods=['POST'])
@login_required
def delete_group(id):
    group = Group.query.get_or_404(id)
    try:
        db.session.delete(group)
        db.session.commit()
        flash_msg('success', f'Группа {group.name} удалена')
    except Exception as e:
        db.session.rollback()
        flash_msg('error', f'Ошибка удаления группы: {str(e)}')
    return redirect(url_for('main.groups'))

# ===================== ПРЕДМЕТЫ =====================
@main.route('/subjects')
@login_required
def subjects():
    """Список всех предметов"""
    subjects_list = Subject.query.order_by(Subject.name).all()
    return render_template('subjects.html', subjects=subjects_list)

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
                        except:
                            continue
            
            if added_count > 0:
                db.session.commit()
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
                flash_msg('success', f'Предмет "{subject.name}" успешно добавлен')
                return redirect(url_for('main.subjects'))
            except IntegrityError:
                db.session.rollback()
                flash_msg('error', 'Предмет с таким названием уже существует')
            except Exception as e:
                db.session.rollback()
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
            flash_msg('success', f'Предмет "{subject.name}" обновлен')
            return redirect(url_for('main.subjects'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Предмет с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
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
            flash_msg('error', f'Нельзя удалить предмет "{subject.name}", так как по нему уже есть {grade_count} оценок')
            return redirect(url_for('main.subjects'))
        db.session.delete(subject)
        db.session.commit()
        flash_msg('success', f'Предмет "{subject.name}" удален')
    except Exception as e:
        db.session.rollback()
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
    query = Grade.query.join(Student).join(Subject)
    
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
            flash_msg('success', f'Оценка по предмету "{subject.name}" для студента {student.full_name} успешно добавлена')
            return redirect(url_for('main.grades'))
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка добавления оценки: {str(e)}')
    
    return render_template('grade_form.html', form=form, title='Добавить оценку')

@main.route('/grades/<int:id>/delete', methods=['POST'])
@login_required
def delete_grade(id):
    """Удаление оценки"""
    grade = Grade.query.get_or_404(id)
    try:
        db.session.delete(grade)
        db.session.commit()
        flash_msg('success', 'Оценка удалена')
    except Exception as e:
        db.session.rollback()
        flash_msg('error', f'Ошибка удаления оценки: {str(e)}')
    return redirect(url_for('main.grades'))

# ===================== ОТЧЕТЫ =====================
@main.route('/reports')
@login_required
def reports():
    return render_template('reports.html', groups=Group.query.all())

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
            return send_file(filepath, as_attachment=True)
        
        flash_msg('error', 'Неверный тип отчета')
        return redirect(url_for('main.reports'))
        
    except Exception as e:
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
        return send_file(filepath, as_attachment=True)
    except Exception as e:
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
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

# ===================== ПРОСТЫЕ НАСТРОЙКИ =====================
@main.route('/settings')
@login_required
def settings():
    """Простая страница с базовыми настройками"""
    return render_template('settings.html')

# ===================== API =====================
@main.route('/health')
def health_check():
    return {'status': 'ok', 'timestamp': datetime.utcnow().isoformat()}