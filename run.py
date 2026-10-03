"""Точка входа приложения.

Запуск для разработки:   python run.py
Запуск сервисом:        python run.py
                        (поднимается waitress, если отладка выключена)

Порт и хост берутся из .env: HOST, PORT, FLASK_DEBUG. Отладочный сервер
Werkzeug — только при FLASK_DEBUG=1 или явном FLASK_DEV_SERVER=1.
Отладочная консоль Werkzeug выполняет произвольный код, поэтому config.py
запрещает её в production на внешнем интерфейсе.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.init_ import create_app

app = create_app()


def main():
    host = app.config['HOST']
    port = app.config['PORT']
    debug = app.config['DEBUG']

    # waitress выбирается по отсутствию отладки, а не по отдельному флагу:
    # забытый WAITRESS=1 приводил к тому, что сервис в контейнере поднимался
    # dev-сервером Werkzeug — без потоков и с отладочной консолью
    if not debug and os.environ.get('FLASK_DEV_SERVER') != '1':
        try:
            from waitress import serve
        except ImportError:
            print('⚠️  waitress не установлен, поднимаю встроенный сервер '
                  'разработки. В production поставьте waitress '
                  '(pip install waitress)')
        else:
            print(f'Запуск через waitress на http://{host}:{port}')
            serve(app, host=host, port=port)
            return

    print(f'Запуск отладочного сервера на http://{host}:{port} (debug={debug})')
    app.run(debug=debug, host=host, port=port)


if __name__ == '__main__':
    main()
