"""Точка входа приложения.

Запуск для разработки:   python run.py
Запуск в контейнере:     waitress-serve --host=0.0.0.0 --port=5000 run:app
                          (или python run.py — скрипт сам выберет waitress,
                          если FLASK_DEBUG выключен и задан WAITRESS=1)

Порт и хост берутся из .env: HOST, PORT, FLASK_DEBUG.
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

    if os.environ.get('WAITRESS') == '1' and not debug:
        from waitress import serve
        print(f'Запуск через waitress на http://{host}:{port}')
        serve(app, host=host, port=port)
    else:
        print(f'Запуск в режиме отладки на http://{host}:{port}')
        app.run(debug=debug, host=host, port=port)


if __name__ == '__main__':
    main()
