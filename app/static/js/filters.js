// Фильтрация таблицы студентов
document.addEventListener('DOMContentLoaded', function() {
    // Обновление URL при изменении фильтров
    const filterSelects = document.querySelectorAll('.filter-select');
    filterSelects.forEach(select => {
        select.addEventListener('change', function() {
            updateFilters();
        });
    });
    
    // Кнопка сброса фильтров
    const resetBtn = document.getElementById('resetFilters');
    if (resetBtn) {
        resetBtn.addEventListener('click', function() {
            resetFilters();
        });
    }
    
    // Поиск
    const searchInput = document.getElementById('studentSearch');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(updateFilters, 300));
    }
    
    // Экспорт в Excel
    const exportBtn = document.getElementById('exportExcel');
    if (exportBtn) {
        exportBtn.addEventListener('click', function() {
            window.location.href = '/export/students';
        });
    }
});

function updateFilters() {
    const group = document.getElementById('filterGroup')?.value || 'all';
    const gender = document.getElementById('filterGender')?.value || 'all';
    const status = document.getElementById('filterStatus')?.value || 'all';
    const search = document.getElementById('studentSearch')?.value || '';
    
    let url = new URL(window.location.href);
    url.searchParams.set('group', group);
    url.searchParams.set('gender', gender);
    url.searchParams.set('status', status);
    if (search) {
        url.searchParams.set('search', search);
    } else {
        url.searchParams.delete('search');
    }
    
    // Сброс пагинации
    url.searchParams.delete('page');
    
    window.location.href = url.toString();
}

function resetFilters() {
    let url = new URL(window.location.href);
    
    // Удаляем все параметры фильтров
    url.searchParams.delete('group');
    url.searchParams.delete('gender');
    url.searchParams.delete('status');
    url.searchParams.delete('search');
    url.searchParams.delete('page');
    
    window.location.href = url.toString();
}

// Функция для предотвращения частых запросов при вводе текста
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Подтверждение удаления
function confirmDelete(message = 'Вы уверены, что хотите удалить эту запись?') {
    return confirm(message);
}

// Форматирование даты
function formatDate(dateString) {
    if (!dateString) return '';
    
    const date = new Date(dateString);
    return date.toLocaleDateString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric'
    });
}

// Подсветка строки при наведении
function initTableHover() {
    const tableRows = document.querySelectorAll('table tbody tr');
    tableRows.forEach(row => {
        row.addEventListener('mouseenter', function() {
            this.style.backgroundColor = 'rgba(67, 97, 238, 0.05)';
        });
        
        row.addEventListener('mouseleave', function() {
            this.style.backgroundColor = '';
        });
    });
}