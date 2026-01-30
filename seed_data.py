#!/usr/bin/env python3
"""
Скрипт для добавления тестовых данных
"""

import random
from datetime import date
from app.init_ import create_app, db
from app.models import Group, Student, Subject, Grade

def create_test_data():
    app = create_app()
    
    with app.app_context():
        print("🔄 Добавление тестовых данных...")
        
        # Очистка старых данных (осторожно!)
        # db.drop_all()
        # db.create_all()
        
        # Создание групп
        groups = []
        for i in range(1, 7):
            group = Group(
                name=f"ИСП-20{i}",
                specialty="Информационные системы и программирование",
                year=2020 + (i % 3)
            )
            db.session.add(group)
            groups.append(group)
        
        db.session.commit()
        
        # Создание предметов
        subjects_list = [
            'Математика',
            'Программирование на Python',
            'Базы данных',
            'Веб-разработка',
            'Сетевые технологии',
            'Английский язык',
            'Информационная безопасность',
        ]
        
        subjects = []
        for subj_name in subjects_list:
            subject = Subject(name=subj_name, hours=random.randint(72, 144))
            db.session.add(subject)
            subjects.append(subject)
        
        db.session.commit()
        
        # Создание студентов
        male_names = ['Иван', 'Алексей', 'Дмитрий', 'Сергей', 'Андрей', 'Максим']
        female_names = ['Анна', 'Мария', 'Елена', 'Ольга', 'Наталья', 'Ирина']
        surnames = ['Иванов', 'Петров', 'Сидоров', 'Кузнецов', 'Смирнов', 'Попов']
        
        students = []
        for i in range(1, 51):  # 50 студентов
            gender = random.choice(['M', 'F'])
            
            if gender == 'M':
                first_name = random.choice(male_names)
                surname = random.choice(surnames)
                patronymic = random.choice(['Иванович', 'Алексеевич', 'Дмитриевич'])
            else:
                first_name = random.choice(female_names)
                surname = random.choice(surnames) + 'а'
                patronymic = random.choice(['Ивановна', 'Алексеевна', 'Дмитриевна'])
            
            student = Student(
                student_id=f"STD{str(i).zfill(5)}",
                last_name=surname,
                first_name=first_name,
                patronymic=patronymic,
                gender=gender,
                birth_date=date(2002 + i % 3, random.randint(1, 12), random.randint(1, 28)),
                email=f"student{i}@college.ru",
                phone=f"+79{random.randint(100000000, 999999999)}",
                group=random.choice(groups),
                status='active'
            )
            db.session.add(student)
            students.append(student)
        
        db.session.commit()
        
        # Создание оценок
        grade_types = ['экзамен', 'зачет', 'лабораторная', 'практика']
        
        for student in students:
            for subject in random.sample(subjects, 4):  # 4 случайных предмета
                for _ in range(random.randint(2, 4)):  # 2-4 оценки по предмету
                    grade = Grade(
                        student=student,
                        subject=subject,
                        grade_value=str(random.randint(3, 5)),
                        grade_type=random.choice(grade_types),
                        date=date(2023, random.randint(9, 12), random.randint(1, 28)),
                        comments=random.choice(['', 'Хорошо', 'Отлично', 'Молодец'])
                    )
                    db.session.add(grade)
        
        db.session.commit()
        
        print("✅ Тестовые данные добавлены:")
        print(f"   Группы: {len(groups)}")
        print(f"   Предметы: {len(subjects)}")
        print(f"   Студенты: {len(students)}")
        print(f"   Оценки: {Grade.query.count()}")

if __name__ == '__main__':
    create_test_data()