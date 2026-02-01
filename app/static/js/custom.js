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
    
    // Подтверждение удаления
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