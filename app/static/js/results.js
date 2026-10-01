// Модальное окно выставления итоговой оценки (10.1).
//
// Форма уже отрисована в шаблоне вместе со скрытыми полями, поэтому задача
// скрипта — только подставить данные ячейки: кого, какой предмет, что
// рекомендовано и что стоит сейчас.
(function () {
    'use strict';

    var modalEl = document.getElementById('resultModal');
    if (!modalEl) {
        return;
    }
    var modal = new bootstrap.Modal(modalEl);

    var form = modalEl.querySelector('form');
    var valueSelect = document.getElementById('result_value');
    var justification = document.getElementById('result_justification');
    var studentName = document.getElementById('result_student_name');
    var subjectName = document.getElementById('result_subject_name');
    var averageLine = document.getElementById('result_average');
    var warning = document.getElementById('result_warning');
    var clearButton = document.getElementById('result_clear');

    function field(name) {
        return form.querySelector('input[name="' + name + '"]');
    }

    // Подсветка отклонения от рекомендации: пользователь должен увидеть
    // предупреждение до сохранения, а не после сообщения об ошибке.
    function refreshWarning() {
        var recommended = (field('recommended').value || '').toLowerCase();
        var chosen = (valueSelect.value || '').toLowerCase();
        var differs = recommended && chosen && chosen !== recommended;
        warning.classList.toggle('d-none', !differs);
        justification.required = !!differs;
    }

    document.addEventListener('click', function (event) {
        var trigger = event.target.closest('[data-role="result-edit"]');
        if (!trigger) {
            return;
        }
        event.preventDefault();

        var recommended = trigger.dataset.recommended || '';
        var current = trigger.dataset.final || '';

        field('student_id').value = trigger.dataset.student;
        field('subject_id').value = trigger.dataset.subject;
        field('period_id').value = trigger.dataset.period;
        field('group_id').value = trigger.dataset.group;
        field('recommended').value = recommended;

        studentName.textContent = trigger.dataset.studentName || '';
        subjectName.textContent = trigger.dataset.subjectName || '';

        var count = trigger.dataset.count || '0';
        var average = trigger.dataset.average || '0';
        averageLine.textContent = count === '0'
            ? 'За период оценок нет — рекомендация не выставляется.'
            : 'Оценок за период: ' + count + ', средний балл: ' + average +
              ', рекомендация: ' + (recommended || '—');

        valueSelect.value = current || recommended;
        justification.value = trigger.dataset.justification || '';
        clearButton.classList.toggle('d-none', !current);

        refreshWarning();
        modal.show();
    });

    valueSelect.addEventListener('change', refreshWarning);

    // Снятие итога — тот же POST, но с пустым значением: отдельный маршрут
    // ради удаления одного поля означал бы две точки входа в одно правило.
    clearButton.addEventListener('click', function () {
        if (!window.confirm('Снять итоговую оценку?')) {
            return;
        }
        valueSelect.value = '';
        form.submit();
    });
}());
