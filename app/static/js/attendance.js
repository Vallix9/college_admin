'use strict';

// Журнал пропусков: правка и удаление отметки, подтверждение удаления.
// Формы уходят обычным POST, CSRF-токен подставляет Flask-WTF сам.

document.addEventListener('DOMContentLoaded', function () {
    var editForm = document.getElementById('editForm');
    var deleteForm = document.getElementById('deleteForm');

    var editStudent = document.getElementById('editStudent');
    var editDate = document.getElementById('editDate');
    var editReason = document.querySelector('#editForm select[name="reason"]');
    var editNote = document.querySelector('#editForm textarea[name="note"]');

    var deleteStudent = document.getElementById('deleteStudent');
    var deleteDate = document.getElementById('deleteDate');
    var deleteReason = document.getElementById('deleteReason');

    document.querySelectorAll('button[data-edit-url]').forEach(function (button) {
        button.addEventListener('click', function () {
            editForm.action = button.dataset.editUrl;
            editStudent.textContent = button.dataset.student || '';
            editDate.textContent = 'Занятие: ' + (button.dataset.date || '');
            if (editReason) editReason.value = button.dataset.reason || '';
            if (editNote) editNote.value = button.dataset.note || '';
        });
    });

    document.querySelectorAll('button[data-delete-url]').forEach(function (button) {
        button.addEventListener('click', function () {
            deleteForm.action = button.dataset.deleteUrl;
            deleteStudent.textContent = button.dataset.student || '';
            deleteDate.textContent = button.dataset.date || '';
            deleteReason.textContent = button.dataset.reason || '';
        });
    });
});
