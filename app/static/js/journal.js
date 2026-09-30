/* Логика журнала: открытие модальных форм из ячейки, подтверждение удаления
   и перемещение стрелками по сетке. */
(function () {
    'use strict';

    var cells = Array.prototype.slice.call(
        document.querySelectorAll('.journal-cell'));
    if (!cells.length) {
        return;
    }

    var addModalEl = document.getElementById('addModal');
    var editModalEl = document.getElementById('editModal');
    var addModal = addModalEl ? new bootstrap.Modal(addModalEl) : null;
    var editModal = editModalEl ? new bootstrap.Modal(editModalEl) : null;

    function studentName(cell) {
        var row = cell.closest('tr');
        var cell_ = row ? row.querySelector('.student-col a') : null;
        return cell_ ? cell_.textContent.trim() : '';
    }

    function columnLabel(cell) {
        var head = document.querySelector(
            '.column-head:nth-of-type(' + (parseInt(cell.dataset.c, 10) + 1) + ')');
        return head ? head.textContent.replace(/\s+/g, ' ').trim() : '';
    }

    // --- Клик по пустой части ячейки: выставить оценки ---------------------
    cells.forEach(function (cell) {
        cell.addEventListener('click', function (event) {
            if (event.target.closest('.grade-badge')) {
                return;
            }
            if (!addModal) {
                return;
            }
            var form = document.getElementById('addGradeForm');
            form.querySelector('[name="student_id"]').value = cell.dataset.student;
            var subjectSelect = form.querySelector('[name="subject_id"]');
            if (subjectSelect) {
                subjectSelect.value = cell.dataset.subject;
            }
            // В режиме «по датам» дата приходит из ячейки, в сводном режиме
            // занятие выбирается из списка — скрытое поле там не нужно.
            var dateField = form.querySelector('[name="date"]');
            if (dateField) {
                dateField.value = cell.dataset.mode === 'dates'
                    ? cell.dataset.column : '';
            }
            document.getElementById('addStudentName').textContent = studentName(cell);
            document.getElementById('addCellHint').textContent =
                columnLabel(cell) + (cell.dataset.mode === 'dates' ? '' : ' · за период');
            form.querySelector('[name="values"]').value = '';
            addModal.show();
        });
    });

    // --- Клик по бейджу: исправить или удалить ----------------------------
    document.querySelectorAll('.grade-badge').forEach(function (badge) {
        badge.addEventListener('click', function (event) {
            event.stopPropagation();
            var form = document.getElementById('editGradeForm');
            if (!form || !editModal) {
                return;
            }
            form.action = badge.dataset.gradeUrl;
            form.querySelector('[name="value"]').value = badge.dataset.value;
            form.querySelector('[name="comments"]').value = badge.dataset.comments || '';
            document.getElementById('editOldValue').textContent = badge.dataset.value;
            editModal.show();
        });
    });

    // --- Удаление отдельной оценки внутри формы правки --------------------
    var editForm = document.getElementById('editGradeForm');
    if (editForm) {
        var danger = document.createElement('div');
        danger.className = 'd-flex justify-content-between align-items-center mt-3 pt-3 border-top';
        danger.innerHTML =
            '<button type="button" class="btn btn-outline-danger btn-sm" data-role="delete">' +
            '<i class="bi bi-trash"></i> Удалить оценку</button>' +
            '<span class="small text-muted">Удаление попадёт в историю</span>';
        editForm.querySelector('.modal-body').appendChild(danger);

        danger.querySelector('[data-role="delete"]')
            .addEventListener('click', function () {
                if (!window.confirm('Удалить оценку? Действие попадёт в историю изменений.')) {
                    return;
                }
                var post = document.createElement('form');
                post.method = 'POST';
                post.action = editForm.action.replace('/edit', '/delete');
                // Токен берём из самой формы правки: он оттуда же, откуда
                // работает отправка, и потому заведомо валиден
                var source = editForm.querySelector('[name="csrf_token"]');
                var token = document.createElement('input');
                token.type = 'hidden';
                token.name = 'csrf_token';
                token.value = source ? source.value : '';
                post.appendChild(token);
                ['group_id', 'subject_id', 'period_id', 'mode'].forEach(function (name) {
                    var source = editForm.querySelector('[name="' + name + '"]');
                    if (source) {
                        var copy = document.createElement('input');
                        copy.type = 'hidden';
                        copy.name = name;
                        copy.value = source.value;
                        post.appendChild(copy);
                    }
                });
                document.body.appendChild(post);
                post.submit();
            });
    }

    // --- Клавиатура: стрелки по сетке, Enter — оценка ----------------------
    function focusCell(row, col) {
        var target = document.querySelector(
            '.journal-cell[data-r="' + row + '"][data-c="' + col + '"]');
        if (target) {
            target.focus();
            if (target.scrollIntoView) {
                target.scrollIntoView({block: 'nearest', inline: 'nearest'});
            }
        }
    }

    cells.forEach(function (cell) {
        cell.addEventListener('keydown', function (event) {
            var row = parseInt(cell.dataset.r, 10);
            var col = parseInt(cell.dataset.c, 10);
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                cell.click();
                return;
            }
            if (event.key === 'ArrowRight') {
                event.preventDefault();
                focusCell(row, col + 1);
            } else if (event.key === 'ArrowLeft') {
                event.preventDefault();
                focusCell(row, col - 1);
            } else if (event.key === 'ArrowDown') {
                event.preventDefault();
                focusCell(row + 1, col);
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                focusCell(row - 1, col);
            }
        });
    });
})();
