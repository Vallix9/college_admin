// Генерация временного пароля для ученической записи (фаза 11, 11.1).
//
// Алфавит совпадает с READABLE_PASSWORD_CHARS в app/utils.py: без 0/O и
// 1/l/I. Из распечатанной ведомости пароль вводится вручную, и похожие
// символы — главная причина того, что человек приходит с «неверным паролем».
(function () {
    'use strict';

    var CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
    var LENGTH = 10;

    // Секрет не берётся из Math.random: пароль должен быть непредсказуемым.
    function randomInt(max) {
        if (window.crypto && window.crypto.getRandomValues) {
            var buf = new Uint32Array(1);
            window.crypto.getRandomValues(buf);
            return buf[0] % max;
        }
        return Math.floor(Math.random() * max);
    }

    function generate(length) {
        var out = '';
        for (var i = 0; i < length; i++) {
            out += CHARS.charAt(randomInt(CHARS.length));
        }
        return out;
    }

    document.addEventListener('DOMContentLoaded', function () {
        var button = document.getElementById('generate-password');
        if (!button) {
            return;
        }
        var password = document.getElementById('account-password');
        var confirm = document.getElementById('account-password-confirm');
        var submit = document.getElementById('account-submit');

        button.addEventListener('click', function () {
            if (!password) {
                return;
            }
            var value = generate(LENGTH);
            password.value = value;
            // Подтверждение заполняем сразу же: иначе администратор вводит
            // пароль дважды вручную и почти всегда расходится в одном символе.
            if (confirm) {
                confirm.value = value;
            }
            if (submit) {
                submit.disabled = false;
            }
            password.focus();
        });
    });
}());
