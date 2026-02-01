from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SelectField, DateField, IntegerField, TextAreaField, SubmitField, SelectMultipleField, FileField
from wtforms.validators import DataRequired, Length, EqualTo, Optional, Email, ValidationError, NumberRange
from wtforms.widgets import ListWidget, CheckboxInput
from app.models import User

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

class GradeForm(FlaskForm):
    student_id = SelectField('Студент*', coerce=int, validators=[DataRequired()])
    subject_id = SelectField('Предмет*', coerce=int, validators=[DataRequired()])
    grade_value = IntegerField('Оценка*', validators=[DataRequired(), NumberRange(min=1, max=5)])
    grade_type = SelectField('Тип оценки', choices=[
        ('lecture', 'Лекция'),
        ('practice', 'Практика'),
        ('exam', 'Экзамен'),
        ('test', 'Зачет'),
        ('homework', 'Домашняя работа'),
        ('lab', 'Лабораторная работа')
    ], default='lecture')
    date = DateField('Дата*', format='%Y-%m-%d', validators=[DataRequired()])
    comments = TextAreaField('Комментарий', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Сохранить')

class SubjectForm(FlaskForm):
    name = StringField('Название предмета*', validators=[DataRequired(), Length(max=100)])
    hours = IntegerField('Количество часов', default=72, validators=[Optional(), NumberRange(min=1)])
    submit = SubmitField('Сохранить')

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
    export_format = SelectField('Формат экспорта по умолчанию', 
                               choices=[
                                   ('excel', 'Excel (.xlsx)'),
                                   ('csv', 'CSV'),
                                   ('pdf', 'PDF')
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
    
    # Смена пароля (опционально)
    current_password = PasswordField('Текущий пароль', validators=[Optional()])
    new_password = PasswordField('Новый пароль', validators=[Optional(), Length(min=6, max=100)])
    confirm_password = PasswordField('Подтвердите пароль', 
                                     validators=[EqualTo('new_password', message='Пароли должны совпадать')])
    
    # Дополнительные настройки
    show_welcome_message = BooleanField('Показывать приветственное сообщение')
    enable_export_logging = BooleanField('Вести журнал экспорта')
    enable_grade_alerts = BooleanField('Оповещения о новых оценках')
    
    submit = SubmitField('Сохранить настройки')
    reset = SubmitField('Сбросить к значениям по умолчанию')
    
    def validate_current_password(self, field):
        """Валидация текущего пароля только если указан новый"""
        from flask_login import current_user
        if self.new_password.data and not current_user.check_password(field.data):
            raise ValidationError('Текущий пароль указан неверно')

class BackupForm(FlaskForm):
    """Форма для ручного резервного копирования"""
    backup_type = SelectField('Тип резервной копии', 
                             choices=[
                                 ('full', 'Полная резервная копия'),
                                 ('students', 'Только студенты'),
                                 ('grades', 'Только оценки'),
                                 ('settings', 'Только настройки')
                             ])
    include_files = BooleanField('Включать загруженные файлы')
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