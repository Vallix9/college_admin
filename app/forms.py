from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, SelectField, DateField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, ValidationError
from app.models import Group, Student

class LoginForm(FlaskForm):
    username = StringField('Логин', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember_me = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')

class StudentForm(FlaskForm):
    student_id = StringField('Номер зачетки*', validators=[DataRequired(), Length(max=20)])
    last_name = StringField('Фамилия*', validators=[DataRequired(), Length(max=50)])
    first_name = StringField('Имя*', validators=[DataRequired(), Length(max=50)])
    patronymic = StringField('Отчество', validators=[Optional(), Length(max=50)])
    gender = SelectField('Пол*', choices=[('M', 'Мужской'), ('F', 'Женский')], validators=[DataRequired()])
    birth_date = DateField('Дата рождения', validators=[Optional()])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=100)])
    phone = StringField('Телефон', validators=[Optional(), Length(max=20)])
    group_id = SelectField('Группа*', coerce=int, validators=[DataRequired()])
    status = SelectField('Статус*', choices=[
        ('active', 'Активный'),
        ('academic_leave', 'Академический отпуск'),
        ('expelled', 'Отчислен'),
        ('graduated', 'Выпускник')
    ], validators=[DataRequired()])
    enrollment_date = DateField('Дата поступления', validators=[Optional()])
    submit = SubmitField('Сохранить')
    
    def __init__(self, *args, **kwargs):
        super(StudentForm, self).__init__(*args, **kwargs)
        self.group_id.choices = [(g.id, g.name) for g in Group.query.order_by(Group.name).all()]
    
    def validate_student_id(self, field):
        student = Student.query.filter_by(student_id=field.data).first()
        if student and (not self.obj or student.id != self.obj.id):
            raise ValidationError('Студент с таким номером зачетки уже существует.')

class GroupForm(FlaskForm):
    name = StringField('Название группы*', validators=[DataRequired(), Length(max=50)])
    specialty = StringField('Специальность*', validators=[DataRequired(), Length(max=200)])
    year = SelectField('Год поступления*', choices=[(y, str(y)) for y in range(2018, 2025)], coerce=int, validators=[DataRequired()])
    submit = SubmitField('Сохранить')

class GradeForm(FlaskForm):
    student_id = SelectField('Студент*', coerce=int, validators=[DataRequired()])
    subject_id = SelectField('Предмет*', coerce=int, validators=[DataRequired()])
    grade_value = SelectField('Оценка*', choices=[
        ('2', '2 - Неудовлетворительно'),
        ('3', '3 - Удовлетворительно'),
        ('4', '4 - Хорошо'),
        ('5', '5 - Отлично'),
        ('зачет', 'Зачет'),
        ('незачет', 'Незачет')
    ], validators=[DataRequired()])
    grade_type = SelectField('Тип оценки', choices=[
        ('экзамен', 'Экзамен'),
        ('зачет', 'Зачет'),
        ('лабораторная', 'Лабораторная работа'),
        ('практика', 'Практическая работа'),
        ('тест', 'Тест'),
        ('другое', 'Другое')
    ], validators=[Optional()])
    date = DateField('Дата*', validators=[DataRequired()])
    comments = TextAreaField('Комментарии', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Сохранить')
    
    def __init__(self, *args, **kwargs):
        super(GradeForm, self).__init__(*args, **kwargs)
        self.student_id.choices = [(s.id, s.full_name) for s in Student.query.order_by(Student.last_name).all()]
        from app.models import Subject
        self.subject_id.choices = [(s.id, s.name) for s in Subject.query.order_by(Subject.name).all()]