"""Сверка: файл Excel должен показывать ровно то же, что таблица на экране.

Приёмка Фазы 10 требует, чтобы открытый Excel совпадал с журналом. Проверка
идёт от данных, а не от скриншота: сравниваются те же ctx, из которых
строятся и html, и книга, поэтому любое расхождение вёрстки с данными будет
поймано, а любая ошибка в самих данных — тоже.

Запуск: python check_export_match.py
"""

import io
import re
import sys

sys.path.insert(0, '.')

import openpyxl

from app.init_ import create_app

STUDENT_RE = re.compile(
    r'journal-sticky student-col">\s*<a[^>]*>([^<]+)</a>')
DATE_RE = re.compile(r'data-date="(\d{4}-\d{2}-\d{2})"')
GRID_RE = re.compile(
    r'<table class="table table-bordered table-sm journal-grid.*?</table>',
    re.S)


def grid_html(html):
    """Только сетка журнала.

    Считать все бейджи на странице нельзя: ниже таблицы есть блок «Сводка за
    период» с теми же grade-badge, и он удвоил бы число ячеек.
    """
    match = GRID_RE.search(html)
    return match.group(0) if match else ''


def sheet_rows(ws):
    """Строки листа как списки строк, None превращаем в пустую строку."""
    return [[('' if value is None else str(value))
             for value in row]
            for row in ws.iter_rows(values_only=True)]


def main():
    app = create_app()
    failures = []
    with app.app_context():
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = 1

        for mode in ('dates', 'subjects'):
            print(f'=== режим {mode} ===')
            page = client.get('/journal' + ('' if mode == 'dates'
                                            else '?mode=subjects'))
            html = page.get_data(as_text=True)
            response = client.get(f'/journal/export?fmt=xlsx&mode={mode}')
            if response.status_code != 200:
                failures.append(f'{mode}: экспорт вернул {response.status_code}')
                continue

            book = openpyxl.load_workbook(io.BytesIO(response.data))
            if book.sheetnames != ['Журнал', 'Сводная', 'Пропуски']:
                failures.append(f'{mode}: листы {book.sheetnames}')

            rows = sheet_rows(book['Журнал'])
            header, body = rows[0], rows[1:]
            grid = grid_html(html)
            names = STUDENT_RE.findall(grid)

            print(f'  колонок: {len(header)} | строк: {len(body)}')
            print(f'  студентов в HTML: {len(names)}')

            if names != [row[0] for row in body]:
                failures.append(f'{mode}: состав или порядок студентов разошёлся')
                print('  HTML:', names[:4])
                print('  Excel:', [row[0] for row in body][:4])

            # Даты колонок в HTML лежат в data-date заголовка, в Excel — в
            # шапке. В ячейках data-date тоже есть (у отметок пропуска),
            # поэтому берём только thead.
            if mode == 'dates':
                thead = grid.split('<thead>')[1].split('</thead>')[0] \
                    if '<thead>' in grid else ''
                html_dates = DATE_RE.findall(thead)
                if len(html_dates) != len(header) - 2:
                    failures.append(
                        f'{mode}: колонок в HTML {len(html_dates)}, '
                        f'в Excel {len(header) - 2}')
                else:
                    print(f'  колонок-дат: HTML {len(html_dates)} = '
                          f'Excel {len(header) - 2}')
                for index, day in enumerate(html_dates):
                    excel_day = header[index + 1][:5]
                    if excel_day != day[8:10] + '.' + day[5:7]:
                        failures.append(
                            f'{mode}: колонка {index + 1} — '
                            f'HTML {day} против Excel {excel_day}')

            # Оценки и пропуски. В сводном режиме в одной ячейке несколько
            # оценок, поэтому сверяем две разные величины: сколько ячеек
            # заполнено и сколько всего значений в них стоит.
            # Заполненной считаем ячейку, в которой есть оценка или отметка
            # о пропуске: класс cell-empty ставится по одним оценкам, и
            # ячейка «только пропуск» в разметке остаётся пустой на вид.
            excel_cells = [value for row in body for value in row[1:-1]
                           if value.strip()]
            excel_tokens = sum(len(value.split()) for value in excel_cells)
            # В разметке после class идёт ещё пять атрибутов (data-r,
            # data-c, tabindex...), поэтому конец открывающего тега ищем
            # отдельно, а не сразу после кавычек class.
            td_re = re.compile(
                r'<td class="journal-cell[^"]*"[^>]*>(.*?)</td>', re.S)
            html_cells = sum(
                1 for cell in td_re.findall(grid)
                if 'grade-badge' in cell or 'absence-mark' in cell)
            html_marks = (len(re.findall(r'grade-badge', grid))
                          + len(re.findall(r'absence-mark', grid)))
            print(f'  заполненных ячеек: HTML {html_cells} | '
                  f'Excel {len(excel_cells)}')
            print(f'  значений в ячейках: HTML {html_marks} | '
                  f'Excel {excel_tokens}')
            if html_cells != len(excel_cells):
                failures.append(
                    f'{mode}: заполненных ячеек HTML {html_cells}, '
                    f'Excel {len(excel_cells)}')
            if html_marks != excel_tokens:
                failures.append(
                    f'{mode}: значений HTML {html_marks}, Excel {excel_tokens}')

    print()
    if failures:
        print('РАСХОЖДЕНИЯ:')
        for item in failures:
            print(' -', item)
        return 1
    print('OK: Excel совпадает с экраном в обоих режимах')
    return 0


if __name__ == '__main__':
    sys.exit(main())
