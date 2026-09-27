"""Загрузка, разбор, МКБ/УКД и справочник. Обработка находится в pipeline."""

from hashlib import sha256
from pathlib import Path

import streamlit as st

from pipeline.loader import PDFError, load_pdf
from pipeline.parser import parse_pdf
from pipeline.storage import document_text, json_text, save_result, save_erdb
from pipeline.erdb import load_erdb, ErdbRepository

st.set_page_config(page_title="Разбор PDF приказов", page_icon="📄", layout="wide")
st.title("Разбор PDF приказов")
st.write("Загрузите оба приказа — 149 и 75. Каждый PDF будет разобран отдельно; результаты можно переключать ниже.")

uploaded = st.file_uploader("PDF документы — можно выбрать несколько", type=["pdf"], accept_multiple_files=True) or []
profiles = {"Определить автоматически": "auto", "Приказ 149": "149", "Приказ 75": "75", "Любой PDF: текст и таблицы": "generic"}
documents = []
for position, upload in enumerate(uploaded):
    data = upload.getvalue()
    identity = f'{sha256(data).hexdigest()}:{upload.name}:{position}'
    label = st.selectbox(f"Профиль: {upload.name}", list(profiles), key=f'profile:{identity}')
    documents.append({'filename': upload.name, 'data': data, 'identity': identity, 'profile': profiles[label]})
enrich = st.checkbox('Нормализовать МКБ и выделить УКД', value=True)
st.caption("Выберите два PDF сразу с помощью Ctrl или добавьте второй через Add files. Лимит каждого файла: 50 МБ и 500 страниц. Excel справочника загружается в отдельном блоке ниже.")

input_key = (tuple((d['identity'], d['profile']) for d in documents), enrich)
if st.session_state.get("batch_input_key") != input_key:
    st.session_state.pop("pdf_results", None)
    st.session_state.pop("pdf_errors", None)
    st.session_state.pop("selected_pdf", None)
    st.session_state["batch_input_key"] = input_key

if st.button("Загрузить и разобрать", type="primary", disabled=not documents):
    st.session_state.pdf_results = []
    st.session_state.pdf_errors = []
    st.session_state.pop('selected_pdf', None)
    bar = st.progress(0, text="Проверка PDF")
    try:
        for index, document in enumerate(documents):
            name = document['filename']
            bar.progress(index / len(documents), text=f"Файл {index+1}/{len(documents)}: {name}")
            try:
                loaded = load_pdf(document['data'], name, lambda done, total: bar.progress(
                    (index + done / total) / len(documents),
                    text=f"Файл {index+1}/{len(documents)}: {name} — страница {done}/{total}"))
                result = parse_pdf(loaded, document['profile'], enrich=enrich)
                saved = save_result(result, document['data'], Path(__file__).parent / "data")
                st.session_state.pdf_results.append({'filename': name, 'result': result, 'saved_path': str(saved)})
            except (PDFError, OSError, ValueError) as exc:
                st.session_state.pdf_errors.append(f'{name}: {exc}')
    finally:
        bar.empty()

for error in st.session_state.get('pdf_errors', []):
    st.error(error)
results = st.session_state.get('pdf_results', [])
if results:
    st.write(f'Обработано документов: {len(results)} из {len(documents)}.')
    selected_pdf = st.selectbox('Результат документа', range(len(results)), key='selected_pdf',
                               format_func=lambda i: f"{i+1}. {results[i]['filename']} · профиль {results[i]['result'].profile}")
    entry = results[selected_pdf]
    result = entry['result']
    detail_key = entry['saved_path']
    summary = result.summary
    cols = st.columns(4)
    for col, label, value in zip(cols, ["Страниц", "Таблиц", "Записей приказа", "Замечаний"],
                               [summary["pages"], summary["tables"], summary["order149_rows"]+summary["order75_rows"], summary["issues"]]):
        col.metric(label, value)
    st.success(f"Файлы сохранены: {entry['saved_path']}")
    if result.issues:
        st.warning("Есть строки для проверки. Откройте вкладку «Замечания» и исходные ячейки.")
    st.caption("МКБ и простые УКД обрабатываются правилами. Сложные условия требуют проверки. Сравнение приказов и выводы о лекарственном обеспечении пока не выполняются.")
    col1, col2 = st.columns(2)
    col1.download_button("Скачать результат JSON", json_text(result.to_dict()), file_name=f"{Path(entry['filename']).stem}_result.json", mime="application/json")
    col2.download_button("Скачать текст", document_text(result.pages), file_name=f"{Path(entry['filename']).stem}_text.txt", mime="text/plain")
    tabs = st.tabs(["Данные приказа", "Текст", "Таблицы", "Замечания", "МКБ и УКД"])
    with tabs[0]:
        st.write(f"Профиль: {result.profile}. Номер: {result.metadata.get('order_number') or 'не определён'}.")
        records = result.order149_rows or result.order75_rows
        fields = (["row_id", "appendix", "item_no", "nosology", "periodicity_smr", "periodicity_pmsp", "duration", "parse_state"]
                  if result.profile == "149" else ["row_id", "section_no", "item_no", "icd_raw", "disease", "category", "product_raw", "atc_raw", "parse_state"])
        if records:
            labels = {"row_id": "ID строки", "appendix": "Приложение", "section_no": "Раздел",
                      "item_no": "Пункт", "nosology": "Заболевание", "disease": "Заболевание",
                      "periodicity_smr": "Осмотр СМР", "periodicity_pmsp": "Осмотр ПМСП",
                      "duration": "Срок наблюдения", "icd_raw": "МКБ (исходный)",
                      "category": "Категория", "product_raw": "Препарат / изделие",
                      "atc_raw": "АТХ (исходный)", "parse_state": "Статус"}
            display_rows = [{labels[k]: row.get(k) for k in fields} for row in records]
            for row in display_rows:
                row["Статус"] = "Нужна проверка" if row["Статус"] == "needs_review" else "Разобрано"
            st.dataframe(display_rows, hide_index=True)
            selected = st.selectbox("Запись с источниками", range(len(records)), format_func=lambda i: records[i]["row_id"], key=f'record:{detail_key}')
            st.json(records[selected])
        else:
            st.info("Записей приказа нет. Извлечённые данные доступны во вкладках «Текст» и «Таблицы».")
        with st.expander(f"Текстовые пункты ({len(result.clauses)}) и примечания ({len(result.notes)})"):
            st.json({"clauses": result.clauses, "notes": result.notes})
        with st.expander(f"Другие строки ({len(result.other_rows)}): заголовки, приложения 4/5 и неразобранные строки"):
            st.json(result.other_rows)
    with tabs[1]:
        page_no = st.number_input("Страница", min_value=1, max_value=len(result.pages), value=1, key=f'page:{detail_key}')
        page = result.pages[int(page_no)-1]
        st.text_area("Извлечённый текст", page.text, height=550, disabled=True, key=f'text:{detail_key}:{page_no}')
        with st.expander("Исходный текст до очистки"):
            st.text(page.raw_text)
    with tabs[2]:
        tables = [table for page in result.pages for table in page.tables]
        if tables:
            index = st.selectbox("Таблица", range(len(tables)), format_func=lambda i: f"{tables[i].table_id} · страница {tables[i].page}", key=f'table:{detail_key}')
            table = tables[index]
            st.dataframe([{"Строка": i + 1, **{f"Колонка {j+1}": cell for j, cell in enumerate(row)}}
                          for i, row in enumerate(table.rows)], hide_index=True)
            st.caption("Номера строк соответствуют ссылкам на источник. В JSON значение null обозначает продолжение объединённой ячейки, а пустая строка — отдельную пустую ячейку. Координаты также сохранены в JSON.")
        else:
            st.info("Таблицы с границами не обнаружены. Текст страницы сохранён.")
    with tabs[3]:
        if result.issues:
            st.dataframe([{"Код": issue.code, "Замечание": issue.message, "Страница": issue.page,
                           "Таблица": issue.table_id, "Строка": issue.row} for issue in result.issues],
                         hide_index=True)
        else:
            st.info("Технических замечаний нет. Это не подтверждение полной предметной разметки.")
    with tabs[4]:
        info = result.enrichment
        if not info:
            st.info('Нормализация отключена для этого запуска.')
        else:
            st.write(f"Упоминаний УКД: {info['ukd_mentions']}. Различных понятий с учётом диагноза: {info['unique_ukd_concepts']}. Записей для проверки: {info['review_rows']}.")
            st.caption(f"Классификатор: {info['catalog']['version']}. Это структурная проверка кодов по ВОЗ; соответствие национальной редакции отдельно не подтверждено.")
            all_records = result.order149_rows + result.order75_rows
            only_review = st.checkbox('Только записи с замечаниями нормализации / УКД', key=f'review:{detail_key}')
            filtered = [r for r in all_records if not only_review or r['icd']['status'] != 'normalized' or r['ukd_state'] == 'needs_review']
            if filtered:
                st.dataframe([{'ID': r['row_id'], 'МКБ': ', '.join(r['icd_codes']),
                               'Статус МКБ': r['icd']['status'], 'УКД': len(r['ukd']['mentions']),
                               'Статус УКД': r['ukd_state']} for r in filtered], hide_index=True)
                chosen = st.selectbox('Нормализованная запись', range(len(filtered)), format_func=lambda i: filtered[i]['row_id'], key=f'normalized:{detail_key}:{only_review}')
                row = filtered[chosen]
                st.write('МКБ: ' + (row['icd']['canonical'] or 'Не удалось выделить'))
                if row['icd']['issues'] or row['ukd_state'] == 'needs_review':
                    st.warning('Запись требует проверки: часть кодов или условий не удалось однозначно обработать. Исходный текст сохранён ниже.')
                kinds = {'functional_class': 'Функциональный класс', 'stage': 'Стадия', 'degree': 'Степень',
                         'severity': 'Тяжесть', 'type': 'Тип', 'post_event': 'Состояние после события'}
                values = {'mild': 'Лёгкая', 'moderate': 'Средняя', 'moderate_severe': 'Среднетяжёлая',
                          'severe': 'Тяжёлая', 'very_severe': 'Крайне тяжёлая', 'any': 'Все значения'}
                if row['ukd']['mentions']:
                    st.dataframe([{'Условие': kinds.get(m['type'], m['type']),
                                   'Значение': str(values.get(m['value'], m['value'])), 'Отрицание': 'Да' if m['negated'] else 'Нет',
                                   'Цитата': m['raw'], 'Поле': m['field'],
                                   'Страницы': ', '.join(str(ref['page']) for ref in m['source_refs'])}
                                  for m in row['ukd']['mentions']], hide_index=True)
                else:
                    st.info('Явные УКД по текущим правилам не выделены. Это не подтверждает их отсутствие.')
                with st.expander('Исходные условия, связи с периодичностью и полный JSON'):
                    st.json({'icd': row['icd'], 'ukd': row['ukd'], 'field_source_refs': row['field_source_refs']})
            else:
                st.info('Нет записей для выбранного фильтра.')

st.divider()
st.subheader('Справочник УКД ЭРДБ')
st.write('Загрузите XLSX с четырьмя листами справочника. Здесь можно проверить связи МКБ → группа → УКД.')
erdb_upload = st.file_uploader('Excel справочника ЭРДБ', type=['xlsx'], key='erdb_upload')
erdb_data = erdb_upload.getvalue() if erdb_upload is not None else None
erdb_key = (sha256(erdb_data).hexdigest(), erdb_upload.name) if erdb_data is not None else None
if st.session_state.get('erdb_key') != erdb_key:
    st.session_state.pop('erdb_snapshot', None)
    st.session_state.pop('erdb_path', None)
    st.session_state.erdb_key = erdb_key
if st.button('Загрузить справочник', disabled=erdb_data is None):
    st.session_state.pop('erdb_snapshot', None)
    try:
        with st.spinner('Чтение листов и проверка связей'):
            snapshot = load_erdb(erdb_data, erdb_upload.name)
            saved = save_erdb(snapshot, erdb_data, Path(__file__).parent / 'data')
        st.session_state.erdb_snapshot = snapshot
        st.session_state.erdb_path = str(saved)
    except (ValueError, OSError) as exc:
        st.error(str(exc))
if 'erdb_snapshot' in st.session_state:
    snapshot = st.session_state.erdb_snapshot
    repo = ErdbRepository(snapshot)
    st.success(f"Справочник сохранён: {st.session_state.erdb_path}")
    st.write(f"Групп: {snapshot['summary']['sp_section']}; УКД: {snapshot['summary']['sp_section_point']}; кодов МКБ: {snapshot['summary']['unique_icd']}; замечаний: {snapshot['summary']['issues']}.")
    st.download_button('Скачать снимок ЭРДБ JSON', json_text(snapshot), file_name='erdb.json', mime='application/json')
    code = st.text_input('МКБ для просмотра прямых связей', value='J45')
    paths = repo.paths_for_icd(code)
    if paths:
        st.dataframe([{k: p[k] for k in ['icd', 'section_id', 'section_name', 'point_id', 'rus_name', 'kaz_name']} for p in paths], hide_index=True)
        with st.expander('Связи с номерами строк Excel'):
            st.json(paths)
    else:
        st.info('Прямой связи для этого кода в загруженном справочнике нет. Наследование от родительского кода не применяется.')
    query = st.text_input('Поиск УКД по русскому названию')
    if query.strip():
        st.dataframe([{k: p[k] for k in ['id', 'rus_name', 'kaz_name']} for p in repo.search_points(query)], hide_index=True)
    with st.expander(f"Аудит справочника: {len(snapshot['issues'])} замечаний"):
        st.json(snapshot['issues'])
        st.json(snapshot['summary'])
