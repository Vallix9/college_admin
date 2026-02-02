// Инициализация всех всплывающих подсказок Bootstrap
document.addEventListener('DOMContentLoaded', function() {
    // Всплывающие подсказки
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Сортировка таблиц при клике на заголовок
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const table = th.closest('table');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            const columnIndex = Array.from(th.parentNode.children).indexOf(th);
            const isAscending = th.classList.contains('asc');
            
            // Сбрасываем сортировку для всех заголовков
            table.querySelectorAll('th').forEach(h => {
                h.classList.remove('asc', 'desc');
            });
            
            // Сортируем строки
            rows.sort((a, b) => {
                const aValue = a.children[columnIndex].textContent.trim();
                const bValue = b.children[columnIndex].textContent.trim();
                
                if (isAscending) {
                    return bValue.localeCompare(aValue);
                } else {
                    return aValue.localeCompare(bValue);
                }
            });
            
            // Устанавливаем класс сортировки
            th.classList.toggle('asc', !isAscending);
            th.classList.toggle('desc', isAscending);
            
            // Пересобираем таблицу
            rows.forEach(row => tbody.appendChild(row));
        });
    });
    
    // Подтверждение удаления с кастомным сообщением
    document.querySelectorAll('.confirm-delete').forEach(button => {
        button.addEventListener('click', function(e) {
            if (!confirm('Вы уверены, что хотите удалить эту запись?')) {
                e.preventDefault();
            }
        });
    });
    
    // Фильтрация таблиц
    const filterTables = () => {
        document.querySelectorAll('.table-filter').forEach(input => {
            const tableId = input.dataset.table;
            const table = document.getElementById(tableId);
            const filterValue = input.value.toLowerCase();
            
            if (table) {
                const rows = table.querySelectorAll('tbody tr');
                rows.forEach(row => {
                    const text = row.textContent.toLowerCase();
                    row.style.display = text.includes(filterValue) ? '' : 'none';
                });
            }
        });
    };
    
    document.querySelectorAll('.table-filter').forEach(input => {
        input.addEventListener('keyup', filterTables);
    });
    
    // Обновление даты и времени
    function updateDateTime() {
        const now = new Date();
        const dateStr = now.toLocaleDateString('ru-RU', {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric'
        });
        const timeStr = now.toLocaleTimeString('ru-RU', {
            hour: '2-digit',
            minute: '2-digit'
        });
        
        const dateElement = document.getElementById('current-date');
        const timeElement = document.getElementById('current-time');
        
        if (dateElement) dateElement.textContent = dateStr;
        if (timeElement) timeElement.textContent = timeStr;
    }
    
    updateDateTime();
    setInterval(updateDateTime, 60000);
    
    // Проверка уведомлений
    function checkNotifications() {
        const notificationCount = document.getElementById('notification-count');
        if (notificationCount) {
            // В реальном приложении здесь был бы запрос к API
            const count = Math.floor(Math.random() * 5);
            notificationCount.textContent = count;
            notificationCount.style.display = count > 0 ? 'inline' : 'none';
        }
    }
    
    checkNotifications();
    setInterval(checkNotifications, 30000);
    
    // Автоматическое закрытие алертов
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
    
    // Обработка форм - показ индикатора загрузки
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const submitBtn = this.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Обработка...';
            }
        });
    });
    
    // Активное меню
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.sidebar .nav-link');
    
    navLinks.forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });
});

// Функция для показа/скрытия сайдбара на мобильных устройствах
function toggleSidebar() {
    document.querySelector('.sidebar').classList.toggle('show');
}

// Экспорт данных в CSV
function exportToCSV(tableId, filename) {
    const table = document.getElementById(tableId);
    if (!table) return;
    
    let csv = [];
    const rows = table.querySelectorAll('tr');
    
    for (let row of rows) {
        let rowData = [];
        const cols = row.querySelectorAll('td, th');
        
        for (let col of cols) {
            let data = col.textContent.replace(/(\r\n|\n|\r)/gm, '').replace(/(\s\s)/gm, ' ');
            rowData.push('"' + data + '"');
        }
        
        csv.push(rowData.join(','));
    }
    
    const csvFile = new Blob([csv.join('\n')], {type: 'text/csv'});
    const downloadLink = document.createElement('a');
    downloadLink.download = filename;
    downloadLink.href = window.URL.createObjectURL(csvFile);
    downloadLink.style.display = 'none';
    document.body.appendChild(downloadLink);
    downloadLink.click();
    document.body.removeChild(downloadLink);
}

// Копирование в буфер обмена
function copyToClipboard(text, elementId) {
    navigator.clipboard.writeText(text).then(() => {
        const element = document.getElementById(elementId);
        if (element) {
            const originalText = element.textContent;
            element.textContent = '✓ Скопировано';
            element.classList.add('text-success');
            
            setTimeout(() => {
                element.textContent = originalText;
                element.classList.remove('text-success');
            }, 2000);
        }
    }).catch(err => {
        alert('Ошибка копирования: ' + err);
    });
}

// Показать/скрыть пароль
function togglePasswordVisibility(inputId, iconId) {
    const input = document.getElementById(inputId);
    const icon = document.getElementById(iconId);
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.className = 'bi bi-eye-slash';
    } else {
        input.type = 'password';
        icon.className = 'bi bi-eye';
    }
}

// Валидация формы
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return true;
    
    const requiredInputs = form.querySelectorAll('[required]');
    let isValid = true;
    
    requiredInputs.forEach(input => {
        if (!input.value.trim()) {
            isValid = false;
            input.classList.add('is-invalid');
            
            // Создаем сообщение об ошибке если его нет
            if (!input.nextElementSibling || !input.nextElementSibling.classList.contains('invalid-feedback')) {
                const errorDiv = document.createElement('div');
                errorDiv.className = 'invalid-feedback';
                errorDiv.textContent = 'Это поле обязательно для заполнения';
                input.parentNode.appendChild(errorDiv);
            }
        } else {
            input.classList.remove('is-invalid');
        }
    });
    
    return isValid;
}

// Форматирование даты
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric'
    });
}

// Форматирование числа
function formatNumber(num) {
    return new Intl.NumberFormat('ru-RU').format(num);
}