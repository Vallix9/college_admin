from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.urls import url_parse
from app.init_ import db
from app.models import User, Student, Group, Subject, Grade
from app.forms import LoginForm, StudentForm, GroupForm, GradeForm
from app.utils import export_to_excel
import pandas as pd
from datetime import datetime
from sqlalchemy import or_, and_

bp = Blueprint('main', __name__)

@bp.route('/')
@bp.route('/dashboard')
@login_required
def dashboard():
    stats = {
        'total_students': Student.query.count(),
        'active_students': Student.query.filter_by(status='active').count(),
        'total_groups': Group.query.count(),
        'male_students': Student.query.filter_by(gender='M').count(),
        'female_students': Student.query.filter_by(gender='F').count(),
    }
    
    recent_students = Student.query.order_by(Student.created_at.desc()).limit(5).all()
    recent_grades = Grade.query.order_by(Grade.created_at.desc()).limit(10).all()
    
    return render_template('dashboard.html', 
                         stats=stats, 
                         recent_students=recent_students,
                         recent_grades=recent_grades)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        
        if user is None or not user.check_password(form.password.data):
            flash('Неверное имя пользователя или пароль', 'danger')
            return redirect(url_for('main.login'))
        
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or url_parse(next_page).netloc != '':
            next_page = url_for('main.dashboard')
        
        flash(f'Добро пожаловать, {user.username}!', 'success')
        return redirect(next_page)
    
    return render_template('login.html', form=form)

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('main.login'))

@bp.route('/students')
@login_required
def students():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    group_id = request.args.get('group', 'all')
    gender = request.args.get('gender', 'all')
    status = request.args.get('status', 'all')
    search = request.args.get('search', '')
    
    query = Student.query
    
    if group_id != 'all':
        query = query.filter_by(group_id=group_id)
    
    if gender != 'all':
        query = query.filter_by(gender=gender)
    
    if status != 'all':
        query = query.filter_by(status=status)
    
    if search:
        search_term = f'%{search}%'
        query = query.filter(
            or_(
                Student.last_name.ilike(search_term),
                Student.first_name.ilike(search_term),
                Student.patronymic.ilike(search_term),
                Student.student_id.ilike(search_term)
            )
        )
    
    students = query.order_by(Student.last_name, Student.first_name)\
                   .paginate(page=page, per_page=per_page, error_out=False)
    
    groups = Group.query.order_by(Group.name).all()
    
    return render_template('students.html',
                         students=students,
                         groups=groups,
                         current_filters={
                             'group': group_id,
                             'gender': gender,
                             'status': status,
                             'search': search
                         })

@bp.route('/student/add', methods=['GET', 'POST'])
@login_required
def add_student():
    form = StudentForm()
    
    if form.validate_on_submit():
        student = Student(
            student_id=form.student_id.data,
            last_name=form.last_name.data,
            first_name=form.first_name.data,
            patronymic=form.patronymic.data,
            gender=form.gender.data,
            birth_date=form.birth_date.data,
            email=form.email.data,
            phone=form.phone.data,
            group_id=form.group_id.data,
            status=form.status.data,
            enrollment_date=form.enrollment_date.data or datetime.utcnow().date()
        )
        
        db.session.add(student)
        db.session.commit()
        
        flash(f'Студент {student.full_name} успешно добавлен!', 'success')
        return redirect(url_for('main.students'))
    
    return render_template('student_form.html', form=form, title='Добавление студента')

@bp.route('/student/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_student(id):
    student = Student.query.get_or_404(id)
    form = StudentForm(obj=student)
    
    if form.validate_on_submit():
        form.populate_obj(student)
        db.session.commit()
        
        flash(f'Данные студента {student.full_name} обновлены!', 'success')
        return redirect(url_for('main.students'))
    
    return render_template('student_form.html', form=form, title='Редактирование студента', student=student)

@bp.route('/student/<int:id>/delete', methods=['POST'])
@login_required
def delete_student(id):
    student = Student.query.get_or_404(id)
    
    db.session.delete(student)
    db.session.commit()
    
    flash(f'Студент {student.full_name} удален!', 'warning')
    return redirect(url_for('main.students'))

@bp.route('/groups')
@login_required
def groups():
    groups = Group.query.order_by(Group.name).all()
    return render_template('groups.html', groups=groups)

@bp.route('/group/add', methods=['GET', 'POST'])
@login_required
def add_group():
    form = GroupForm()
    
    if form.validate_on_submit():
        group = Group(
            name=form.name.data,
            specialty=form.specialty.data,
            year=form.year.data
        )
        
        db.session.add(group)
        db.session.commit()
        
        flash(f'Группа {group.name} успешно создана!', 'success')
        return redirect(url_for('main.groups'))
    
    return render_template('group_form.html', form=form, title='Создание группы')

@bp.route('/group/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_group(id):
    group = Group.query.get_or_404(id)
    form = GroupForm(obj=group)
    
    if form.validate_on_submit():
        form.populate_obj(group)
        db.session.commit()
        
        flash(f'Группа {group.name} обновлена!', 'success')
        return redirect(url_for('main.groups'))
    
    return render_template('group_form.html', form=form, title='Редактирование группы', group=group)

@bp.route('/group/<int:id>/delete', methods=['POST'])
@login_required
def delete_group(id):
    group = Group.query.get_or_404(id)
    
    if group.students:
        flash(f'Невозможно удалить группу {group.name}, так как в ней есть студенты!', 'danger')
        return redirect(url_for('main.groups'))
    
    db.session.delete(group)
    db.session.commit()
    
    flash(f'Группа {group.name} удалена!', 'warning')
    return redirect(url_for('main.groups'))

@bp.route('/grades')
@login_required
def grades():
    page = request.args.get('page', 1, type=int)
    per_page = 30
    
    group_id = request.args.get('group', 'all')
    student_id = request.args.get('student', 'all')
    
    query = Grade.query.join(Student)
    
    if group_id != 'all':
        query = query.filter(Student.group_id == group_id)
    
    if student_id != 'all':
        query = query.filter(Grade.student_id == student_id)
    
    grades = query.order_by(Grade.date.desc())\
                 .paginate(page=page, per_page=per_page, error_out=False)
    
    groups = Group.query.order_by(Group.name).all()
    students = Student.query.order_by(Student.last_name, Student.first_name).all()
    
    return render_template('grades.html',
                         grades=grades,
                         groups=groups,
                         students=students,
                         current_filters={
                             'group': group_id,
                             'student': student_id
                         })

@bp.route('/grade/add', methods=['GET', 'POST'])
@login_required
def add_grade():
    form = GradeForm()
    
    if form.validate_on_submit():
        grade = Grade(
            student_id=form.student_id.data,
            subject_id=form.subject_id.data,
            grade_value=form.grade_value.data,
            grade_type=form.grade_type.data,
            date=form.date.data,
            comments=form.comments.data
        )
        
        db.session.add(grade)
        db.session.commit()
        
        flash('Оценка успешно добавлена!', 'success')
        return redirect(url_for('main.grades'))
    
    return render_template('grade_form.html', form=form, title='Добавление оценки')

@bp.route('/grade/<int:id>/delete', methods=['POST'])
@login_required
def delete_grade(id):
    grade = Grade.query.get_or_404(id)
    
    db.session.delete(grade)
    db.session.commit()
    
    flash('Оценка удалена!', 'warning')
    return redirect(url_for('main.grades'))

@bp.route('/reports')
@login_required
def reports():
    groups = Group.query.order_by(Group.name).all()
    return render_template('reports.html', groups=groups)

@bp.route('/report/students')
@login_required
def report_students():
    students = Student.query.order_by(Student.group_id, Student.last_name).all()
    
    data = []
    for student in students:
        data.append({
            'ID': student.student_id,
            'Фамилия': student.last_name,
            'Имя': student.first_name,
            'Отчество': student.patronymic or '',
            'Пол': 'Мужской' if student.gender == 'M' else 'Женский',
            'Дата рождения': student.birth_date.strftime('%d.%m.%Y') if student.birth_date else '',
            'Группа': student.group.name if student.group else '',
            'Специальность': student.group.specialty if student.group else '',
            'Статус': student.status,
            'Дата поступления': student.enrollment_date.strftime('%d.%m.%Y') if student.enrollment_date else ''
        })
    
    filename = export_to_excel(data, 'students_list')
    return send_file(filename, as_attachment=True)

@bp.route('/report/group/<int:group_id>')
@login_required
def report_group(group_id):
    group = Group.query.get_or_404(group_id)
    students = Student.query.filter_by(group_id=group_id).order_by(Student.last_name).all()
    
    data = []
    for student in students:
        avg_grade = student.average_grade()
        
        data.append({
            '№': len(data) + 1,
            'ID студента': student.student_id,
            'ФИО': student.full_name,
            'Пол': 'М' if student.gender == 'M' else 'Ж',
            'Статус': student.status,
            'Средний балл': round(avg_grade, 2) if avg_grade else 0,
            'Дата рождения': student.birth_date.strftime('%d.%m.%Y') if student.birth_date else '',
            'Email': student.email or '',
            'Телефон': student.phone or ''
        })
    
    filename = export_to_excel(data, f'group_{group.name}')
    return send_file(filename, as_attachment=True)

@bp.route('/health')
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})