from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SelectField, DateField, IntegerField, TextAreaField, SubmitField, SelectMultipleField, FileField, FloatField, EmailField
from wtforms.validators import DataRequired, Length, EqualTo, Optional, Email, ValidationError, NumberRange
from wtforms.widgets import ListWidget, CheckboxInput
from app.models import User, Subject

class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

class LoginForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember_me = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')

class StudentForm(FlaskForm):
    student_id = StringField('Номер зачетки*', validators=[DataRequired(), Length(max=20)])
    last_name = StringField('Фамилия*', validators=[DataRequired(), Length(max=50)])
    first_name = StringField('Имя*', validators=[DataRequired(), Length(max=50)])
    patronymic = StringField('Отчество', validators=[Optional(), Length(max=50)])
    gender = SelectField('Пол*', choices=[('M', 'Мужской'), ('F', 'Женский')], validators=[DataRequired()])
    birth_date = DateField('Дата рождения', format='%Y-%m-%d', validators=[Optional()])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=100)])
    phone = StringField('Телефон', validators=[Optional(), Length(max=20)])
    group_id = SelectField('Группа', coerce=int, validators=[Optional()])
    status = SelectField('Статус', choices=[
        ('active', 'Активен'),
        ('academic_leave', 'Академический отпуск'),
        ('expelled', 'Отчислен'),
        ('graduated', 'Выпускник')
    ], default='active')
    enrollment_date = DateField('Дата поступления', format='%Y-%m-%d', validators=[Optional()])
    submit = SubmitField('Сохранить')

class GroupForm(FlaskForm):
    name = StringField('Название группы*', validators=[DataRequired(), Length(max=50)])
    specialty = StringField('Специальность*', validators=[DataRequired(), Length(max=200)])
    year = IntegerField('Год поступления*', validators=[DataRequired(), NumberRange(min=2000, max=2100)])
    submit = SubmitField('Сохранить')

class SubjectForm(FlaskForm):
    name = StringField('Название предмета*', validators=[DataRequired(), Length(max=100)])
    hours = IntegerField('Количество часов', default=72, validators=[Optional(), NumberRange(min=1)])
    teacher_id = SelectField('Преподаватель', coerce=int, choices=[], validators=[Optional()])
    submit = SubmitField('Сохранить')

    def validate_teacher_id(self, field):
        """Предмет можно закрепить только за существующим сотрудником."""
        if not field.data:
            return
        from app.models import User
        teacher = User.query.get(field.data)
        if teacher is None:
            raise ValidationError('Преподаватель не найден')
        if not teacher.can_manage_data:
            raise ValidationError('Сотрудник не может вести предметы')


class StaffForm(FlaskForm):
    """Создание и правка учётной записи сотрудника."""
    username = StringField('Логин*', validators=[DataRequired(), Length(min=3, max=64)])
    full_name = StringField('ФИО', validators=[Optional(), Length(max=200)])
    email = EmailField('Email', validators=[Optional(), Length(max=100)])
    role = SelectField('Роль*', choices=[
        (User.ROLE_ADMIN, 'Администратор — полный доступ'),
        (User.ROLE_TEACHER, 'Преподаватель — свои предметы'),
    ], validators=[DataRequired()])
    password = PasswordField(
        'Пароль*', validators=[Optional(), Length(min=6, max=128)])
    confirm_password = PasswordField(
        'Повторите пароль*',
        validators=[EqualTo('password', message='Пароли не совпадают')])
    is_active = BooleanField('Учётная запись активна', default=True)
    submit = SubmitField('Сохранить')

    def __init__(self, *args, user=None, **kwargs):
        # user=None — создание нового сотрудника, иначе правка существующего.
        # Отдельный аргумент, а не kwarg: WTForms принимает лишние kwargs как
        # значения по умолчанию для полей.
        self.user = user
        super().__init__(*args, **kwargs)
        if user is None:
            # При создании пароль обязателен, при правке — нет
            self.password.validators = [DataRequired(), Length(min=6, max=128)]
            self.confirm_password.validators = [
                DataRequired(),
                EqualTo('password', message='Пароли не совпадают')]

    def validate_username(self, field):
        from app.models import User
        query = User.query.filter(User.username == field.data)
        if self.user is not None:
            query = query.filter(User.id != self.user.id)
        if query.first():
            raise ValidationError('Такой логин уже занят')


class StaffPasswordResetForm(FlaskForm):
    """Сброс пароля сотрудника администратором."""
    password = PasswordField('Новый пароль*', validators=[DataRequired(), Length(min=6, max=128)])
    confirm_password = PasswordField(
        'Повторите пароль*',
        validators=[EqualTo('password', message='Пароли не совпадают')])
    submit = SubmitField('Сбросить пароль')


class AccountPasswordForm(FlaskForm):
    """Смена собственного пароля в личном кабинете."""
    current_password = PasswordField(
        'Текущий пароль*', validators=[DataRequired()])
    new_password = PasswordField(
        'Новый пароль*', validators=[DataRequired(), Length(min=6, max=128)])
    confirm_password = PasswordField(
        'Повторите новый пароль*',
        validators=[EqualTo('new_password', message='Пароли не совпадают')])
    submit = SubmitField('Изменить пароль')

class GradeForm(FlaskForm):
    student_id = SelectField('Студент*', coerce=int, validators=[DataRequired()], choices=[])
    subject_id = SelectField('Предмет*', coerce=int, validators=[DataRequired()], choices=[])
    grade_value = SelectField('Оценка*', choices=[
        ('5', '5 (Отлично)'),
        ('4', '4 (Хорошо)'),
        ('3', '3 (Удовлетворительно)'),
        ('2', '2 (Неудовлетворительно)'),
        ('зачет', 'Зачет'),
        ('незачет', 'Незачет')
    ], validators=[DataRequired()])
    grade_type = SelectField('Тип оценки', choices=[
        ('exam', 'Экзамен'),
        ('test', 'Зачет'),
        ('lab', 'Лабораторная работа'),
        ('practice', 'Практика'),
        ('homework', 'Домашняя работа'),
        ('lecture', 'Лекция')
    ], default='exam')
    date = DateField('Дата*', format='%Y-%m-%d', validators=[DataRequired()])
    comments = TextAreaField('Комментарий', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Сохранить')
    
    def __init__(self, *args, **kwargs):
        super(GradeForm, self).__init__(*args, **kwargs)
        # Динамически обновляем выбор студентов и предметов
        from app.models import Student, Subject
        self.student_id.choices = [(s.id, f'{s.last_name} {s.first_name} ({s.student_id})') 
                                  for s in Student.query.order_by(Student.last_name).all()]
        self.subject_id.choices = [(s.id, f'{s.name} ({s.hours}ч)') 
                                  for s in Subject.query.order_by(Subject.name).all()]

class SettingsForm(FlaskForm):
    """Форма настроек системы"""
    
    # Основные настройки
    college_name = StringField('Название колледжа*', 
                              validators=[DataRequired(), Length(max=200)])
    academic_year = StringField('Учебный год*', 
                               validators=[DataRequired(), Length(max=50)])
    max_students_per_group = IntegerField('Максимум студентов в группе*', 
                                         validators=[DataRequired(), NumberRange(min=1, max=100)])
    
    # Настройки отображения
    theme_color = SelectField('Цветовая тема', 
                             choices=[
                                 ('purple', 'Фиолетовая'),
                                 ('blue', 'Синяя'),
                                 ('green', 'Зеленая'),
                                 ('red', 'Красная'),
                                 ('dark', 'Темная')
                             ])
    items_per_page = IntegerField('Элементов на странице', 
                                 validators=[DataRequired(), NumberRange(min=5, max=100)])
    
    # Настройки экспорта
    # 'pdf' убран: экспорт в PDF не реализован, и выбор молча ничего не делал
    export_format = SelectField('Формат экспорта по умолчанию', 
                               choices=[
                                   ('excel', 'Excel (.xlsx)'),
                                   ('csv', 'CSV')
                               ])
    
    # Настройки уведомлений
    enable_system_notifications = BooleanField('Системные уведомления')
    enable_email_notifications = BooleanField('Email уведомления')
    
    # Настройки резервного копирования
    auto_backup = BooleanField('Автоматическое резервное копирование')
    backup_frequency = SelectField('Частота резервного копирования', 
                                  choices=[
                                      ('daily', 'Ежедневно'),
                                      ('weekly', 'Еженедельно'),
                                      ('monthly', 'Ежемесячно')
                                  ])
    
    # Смена пароля (необязательно — заполняется только при смене)
    current_password = PasswordField('Текущий пароль', validators=[Optional()])
    new_password = PasswordField('Новый пароль', validators=[Optional(), Length(min=6, max=100)])
    confirm_password = PasswordField('Подтвердите новый пароль', 
                                     validators=[EqualTo('new_password', message='Пароли должны совпадать')])
    
    submit = SubmitField('Сохранить настройки')
    reset = SubmitField('Сбросить к значениям по умолчанию')
    
    def validate_current_password(self, field):
        """Валидация текущего пароля только если указан новый"""
        from flask_login import current_user
        if self.new_password.data and not current_user.check_password(field.data):
            raise ValidationError('Текущий пароль указан неверно')

class BackupForm(FlaskForm):
    """Форма для ручного резервного копирования.

    Раньше здесь были выбор типа копии (только студенты / только оценки /
    только настройки) и галочка «включать загруженные файлы». Оба поля были
    обманкой: тип копии попадал только в имя файла, внутри архива всегда лежала
    вся база, а восстановление умеет вернуть только college.db — то есть
    «частичную» копию было бы невозможно восстановить. Папка uploads, откуда
    брались «файлы», никогда не заполнялась. Оставлено одно полноценное
    полное копирование.
    """
    description = StringField('Описание (необязательно)',
                             validators=[Optional(), Length(max=200)])
    submit = SubmitField('Создать резервную копию')

class ImportForm(FlaskForm):
    """Форма для импорта данных"""
    import_type = SelectField('Тип импорта', 
                             choices=[
                                 ('students', 'Студенты'),
                                 ('grades', 'Оценки'),
                                 ('groups', 'Группы'),
                                 ('settings', 'Настройки')
                             ])
    file = FileField('Файл для импорта', 
                    validators=[DataRequired()])
    import_mode = SelectField('Режим импорта', 
                             choices=[
                                 ('append', 'Добавить к существующим'),
                                 ('replace', 'Заменить существующие')
                             ])
    submit = SubmitField('Импортировать данные')


class ClearLogsForm(FlaskForm):
    """Форма для очистки журнала событий.

    Нужна потому, что глобальный CSRFProtect в проекте не включён: токен
    проверяет только сам FlaskForm. Эндпоинт очистки принимает POST без
    формы, поэтому без неё он остался бы без защиты.
    """
    submit = SubmitField('Очистить журнал')