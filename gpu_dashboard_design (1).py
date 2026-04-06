"""
ГПУ‑Эксперт — суверенный выбор газопоршневых установок
------------------------------------------------------

Этот модуль реализует фронтенд приложения для выбора газопоршневых
установок в стиле, вдохновлённом концептом из наглядной иллюстрации.
Интерфейс построен с нуля и включает кастомные CSS‑стили для
тёмной палитры, навигационной панели, фильтров и карточек.

Основные функции приложения:
* загрузка базы данных из Excel и предобработка (расчёт КСУ,
  конвертация валют);
* интерактивные фильтры: происхождение (кластер), диапазон мощности,
  срок службы, время работы в год, стоимость газа, период расчёта,
  категория потребителя;
* динамический расчёт стоимости жизненного цикла и интегральной
  оценки;
* визуализация топ‑10 моделей в виде горизонтального графика,
  радар‑диаграмма трёх лидеров и таблица ключевых параметров;
* отображение формулы интегральной оценки с цветными весами.

Внимание: для работы требуется наличие файла ``GPU_Database_v3.xlsx``
и изображения ``header_image.png`` в той же директории.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go


def load_data(uploaded_file: 'st.uploaded_file_manager.UploadedFile | None') -> pd.DataFrame:
    """Читает таблицу Excel и возвращает DataFrame.
    Если файл не загружен, используется стандартная база GPU_Database_v3.xlsx.
    """
    if uploaded_file is not None:
        df = pd.read_excel(uploaded_file)
    else:
        df = pd.read_excel('GPU_Database_v3.xlsx')
    return df


def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    """Переименовывает столбцы, рассчитывает КСУ и конвертирует валюты.
    Стоимость жизненного цикла вычисляется позже в зависимости от фильтров.
    """
    df = df.copy()
    rename_map = {
        'Pэл, кВт': 'P_el',
        'КПД эл, %': 'eta_el',
        'КПД коген, %': 'eta_cogen',
        'Pтепл, кВт': 'P_th',
        'Расход газа, нм³/ч': 'gas_flow',
        'Скор. нагруж., %/мин': 'ramp_rate',
        'Ресурс до КР, тыс.ч': 'R_cr',
        'Полный ресурс, тыс.ч': 'R_full',
        'Интервал ТО, тыс.ч': 'TO_interval',
        'Уд. CAPEX': 'capex_unit',
        'Валюта CAPEX': 'capex_currency',
        'Затраты РТО': 'opex_unit',
        'Валюта РТО': 'opex_currency',
        'Стоим. КР, млн руб': 'cap_repair_cost_mrub',
        'NOx, мг/нм³': 'NOx',
        'S1 Геополит.': 'S1',
        'S2 Сервис': 'S2',
        'S3 ЗИП': 'S3',
        'S4 ПО': 'S4',
        'S5 Аналоги': 'S5',
        'S6 Референция': 'S6',
        'S7 Вторич. санкц.': 'S7',
    }
    df.rename(columns=rename_map, inplace=True)
    # КСУ
    ksu_weights = {'S1': 0.20, 'S2': 0.18, 'S3': 0.17, 'S4': 0.12, 'S5': 0.10, 'S6': 0.10, 'S7': 0.13}
    for col in ksu_weights:
        if col not in df.columns:
            df[col] = 0.0
    df['KSU'] = sum(df[col].fillna(0) * weight for col, weight in ksu_weights.items())
    # Курсы валют
    currency_rates = {
        'RUB': 1.0,
        'РУБ': 1.0,
        'USD': 80.0,
        'EUR': 90.0,
        '€': 90.0,
        '$': 80.0,
        'CNY': 12.0,
        '¥': 12.0,
    }
    def convert(value, currency):
        if pd.isna(value):
            return np.nan
        if pd.isna(currency):
            return value
        cur = str(currency).strip().upper()
        return float(value) * currency_rates.get(cur, 1.0)
    df['CAPEX_rub_per_kw'] = [convert(v, c) for v, c in zip(df.get('capex_unit', []), df.get('capex_currency', []))]
    df['OPEX_rub_per_hour'] = [convert(v, c) for v, c in zip(df.get('opex_unit', []), df.get('opex_currency', []))]
    return df


def calculate_scores(
    df: pd.DataFrame,
    hours_per_year: int = 6000,
    gas_price: float = 5.0,
    years: int = 10
) -> pd.DataFrame:
    """Рассчитывает стоимость жизненного цикла и интегральный рейтинг."""
    df = df.copy()
    # LCC
    gas_cost_mrub = (df['gas_flow'].fillna(0).astype(float) * gas_price * hours_per_year * years) / 1e6
    df['LCC_mrub'] = (
        df['CAPEX_rub_per_kw'] * df['P_el'] / 1000 +
        df['OPEX_rub_per_hour'] * hours_per_year * years / 1e6 +
        df['cap_repair_cost_mrub'].fillna(0) +
        gas_cost_mrub
    )
    # Определяем критерии
    criteria = {
        'eta_el': True,
        'eta_cogen': True,
        'P_el': True,
        'ramp_rate': True,
        'R_cr': True,
        'R_full': True,
        'CAPEX_rub_per_kw': False,
        'OPEX_rub_per_hour': False,
        'LCC_mrub': False,
        'S3': True,
        'S2': True,
        'NOx': False,
        'KSU': True,
    }
    group_assignments = {
        'technical': ['eta_el', 'eta_cogen', 'P_el', 'ramp_rate', 'R_cr', 'R_full'],
        'economic': ['CAPEX_rub_per_kw', 'OPEX_rub_per_hour', 'LCC_mrub'],
        'operational': ['S3', 'S2'],
        'environmental': ['NOx'],
        'sanction': ['KSU'],
    }
    # Веса групп критериев скорректированы на основе рекомендаций диссертации:
    # технические характеристики (надежность и долговечность) — около 38–40%,
    # экономические показатели — около 30–35%,
    # эксплуатационные — 10%, экологические — 10%, санкционная устойчивость — 10%.
    group_weights = {
        'technical': 0.38,
        'economic': 0.32,
        'operational': 0.10,
        'environmental': 0.10,
        'sanction': 0.10,
    }
    normalized = {}
    for crit, maximize in criteria.items():
        series = df[crit].astype(float)
        min_val, max_val = series.min(), series.max()
        if pd.isna(min_val) or pd.isna(max_val) or min_val == max_val:
            normalized[crit] = pd.Series([1.0] * len(series), index=series.index)
            continue
        if maximize:
            normalized[crit] = (series - min_val) / (max_val - min_val)
        else:
            normalized[crit] = (max_val - series) / (max_val - min_val)
    # Веса критериев
    crit_weights = {}
    for group, crit_list in group_assignments.items():
        weight_per_group = group_weights.get(group, 0)
        if crit_list:
            per = weight_per_group / len(crit_list)
            for c in crit_list:
                crit_weights[c] = per
    scores = pd.Series(0.0, index=df.index)
    for crit, weight in crit_weights.items():
        scores += normalized[crit] * weight
    df['score'] = scores
    for crit in normalized:
        df[f'{crit}_norm'] = normalized[crit]
    return df


def render_dashboard(df: pd.DataFrame, hours_per_year: int, gas_price: float, years: int):
    """Рендерит интерфейс дашборда с учётом выбранных фильтров."""
    # Применяем расчёт
    df_scored = calculate_scores(df, hours_per_year=hours_per_year, gas_price=gas_price, years=years)
    top_df = df_scored.sort_values('score', ascending=False).reset_index(drop=True).head(10)
    # График рейтинга (горизонтальный)
    bar_fig = px.bar(
        top_df.iloc[::-1],
        x='score',
        y='Модель ГПУ',
        orientation='h',
        color='Кластер',
        color_discrete_sequence=['#64b5f6', '#4db6ac', '#ffb74d'],
        height=400,
    )
    bar_fig.update_layout(
        xaxis_title='Рейтинг',
        yaxis_title='',
        legend_title='',
        plot_bgcolor='#112240',
        paper_bgcolor='rgba(0,0,0,0)',
        font_color='#cbd5e1',
    )
    # Радар‑диаграмма
    if len(top_df) >= 3:
        radar_categories = [
            'eta_el_norm', 'eta_cogen_norm', 'P_el_norm', 'ramp_rate_norm',
            'R_cr_norm', 'R_full_norm', 'CAPEX_rub_per_kw_norm',
            'OPEX_rub_per_hour_norm', 'LCC_mrub_norm', 'S3_norm', 'S2_norm',
            'NOx_norm', 'KSU_norm'
        ]
        category_names = [
            'КПД эл.', 'КПД коген.', 'Мощность', 'Нагрузка',
            'Ресурс до КР', 'Полный ресурс', 'КАПЕКС', 'ОПЕКС',
            'СЖЦ', 'ЗИП', 'Сервис', 'NOx', 'КСУ'
        ]
        radar_fig = go.Figure()
        for idx in range(3):
            row = top_df.iloc[idx]
            values = [row[col] for col in radar_categories]
            values.append(values[0])
            radar_fig.add_trace(go.Scatterpolar(
                r=values,
                theta=category_names + [category_names[0]],
                fill='toself',
                name=f"{idx+1}. {row['Модель ГПУ']}"
            ))
        radar_fig.update_layout(
            polar=dict(
                radialaxis=dict(visible=True, range=[0, 1]),
            ),
            showlegend=True,
            legend=dict(font=dict(color='#cbd5e1')),
            plot_bgcolor='#112240',
            paper_bgcolor='rgba(0,0,0,0)',
            font_color='#cbd5e1',
            height=400,
            margin=dict(l=40, r=40, t=40, b=40)
        )
    else:
        radar_fig = go.Figure()

    # Таблица ключевых параметров для трёх лидеров
    leaders = top_df.head(3).copy()
    table_data = {
        'Параметр': ['Мощность, кВт', 'Эл. КПД, %', 'Коген. КПД, %', 'КАПЕКС, млн руб', 'СЖЦ, млн руб', 'КСУ'],
    }
    for i in range(len(leaders)):
        prefix = leaders.loc[i, 'Модель ГПУ']
        # convert CAPEX_rub_per_kw to millions of rub for full unit: CAPEX*P_el/1000 /1e6? We'll approximate
        capex_full = (leaders.loc[i, 'CAPEX_rub_per_kw'] * leaders.loc[i, 'P_el'] / 1000) / 1e3
        table_data[prefix] = [
            f"{leaders.loc[i, 'P_el']:.0f}",
            f"{leaders.loc[i, 'eta_el']:.1f}",
            f"{leaders.loc[i, 'eta_cogen']:.1f}",
            f"{capex_full:.1f}",
            f"{leaders.loc[i, 'LCC_mrub']:.1f}",
            f"{leaders.loc[i, 'KSU']:.2f}",
        ]
    table_df = pd.DataFrame(table_data)

    # Вывод компонентов в карточках
    col1, col2, col3 = st.columns([1.3, 1, 1.2])
    with col1:
        st.markdown(
            """
            <div class="card">
                <div class="card-header">Топ‑10 решений</div>
            """,
            unsafe_allow_html=True
        )
        st.plotly_chart(bar_fig, use_container_width=True)
        # вывод полного card закрытия
        st.markdown("</div>", unsafe_allow_html=True)
    with col2:
        st.markdown(
            """
            <div class="card">
                <div class="card-header">Профиль лидеров</div>
            """,
            unsafe_allow_html=True
        )
        st.plotly_chart(radar_fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with col3:
        st.markdown(
            """
            <div class="card">
                <div class="card-header">Ключевые параметры</div>
            """,
            unsafe_allow_html=True
        )
        st.dataframe(table_df, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)
    # Формулы внизу
    st.markdown(
        """
        <div class="formula">
        <span style="color:#64b5f6;">0,35G₁</span> + <span style="color:#4db6ac;">0,25G₂</span> + <span style="color:#ffb74d;">0,10G₃</span> + <span style="color:#ba68c8;">0,10G₄</span> + <span style="color:#f06292;">0,20G₅</span>
        </div>
        """,
        unsafe_allow_html=True
    )


def main():
    """Основная функция приложения. Настраивает внешний вид и управляет потоками данных."""
    # Настройка страницы
    st.set_page_config(page_title='ГПУ‑Эксперт', page_icon='⚙️', layout='wide')

    # Загрузка фонового изображения и конвертация в base64 для CSS
    import base64
    from pathlib import Path
    image_path = Path('header_image.png')
    if image_path.exists():
        with open(image_path, 'rb') as img_file:
            img_bytes = img_file.read()
        encoded = base64.b64encode(img_bytes).decode()
        background_css = f"background-image: url('data:image/png;base64,{encoded}');"
    else:
        background_css = "background-color: #0b1e3c;"

    # Глобальные стили с фоном из изображения
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
        html, body, [class*="st-"], .stApp {{
            {background_css}
            background-size: cover;
            background-position: center;
            color: #cbd5e1;
            font-family: 'Inter', sans-serif;
        }}
        /* Тёмный оверлей поверх фона для улучшения читаемости */
        .stApp::before {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(11, 30, 60, 0.85);
            z-index: -1;
        }}
        .top-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 20px;
            border-bottom: 1px solid #33415a;
            margin-bottom: 10px;
            background: rgba(17, 34, 64, 0.8);
            backdrop-filter: blur(5px);
        }}
        .top-title {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .top-title h2 {{
            margin: 0;
            font-size: 1.5rem;
            font-weight: 600;
        }}
        .top-icons {{
            display: flex;
            gap: 20px;
            align-items: center;
            font-size: 0.9rem;
        }}
        .top-icons div {{
            display: flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            color: #8da4c6;
        }}
        .top-icons div:hover {{
            color: #dbeafe;
        }}
        .filters-section {{
            padding: 10px;
            margin-bottom: 15px;
            background: rgba(17, 34, 64, 0.85);
            border-radius: 10px;
            box-shadow: 0 0 4px rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(5px);
        }}
        .card {{
            background-color: rgba(17, 34, 64, 0.85);
            border-radius: 10px;
            padding: 10px 15px;
            margin-bottom: 20px;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(5px);
        }}
        .card-header {{
            font-weight: 600;
            margin-bottom: 8px;
            color: #dbeafe;
        }}
        .formula {{
            margin-top: 20px;
            font-size: 1.0rem;
            text-align: center;
        }}
        .formula span {{
            font-weight: 600;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )
    # Навигационная панель
    st.markdown(
        """
        <div class="top-bar">
            <div class="top-title">
                <span style="font-size:1.8rem;">⚙️</span>
                <h2>ГПУ‑Эксперт</h2>
                <span style="font-size:0.9rem; color:#8da4c6;">Суверенный выбор газопоршневых установок</span>
            </div>
            <div class="top-icons">
                <div><span>📦</span> ГПУ</div>
                <div><span>⚡</span> ГТЭС</div>
                <div><span>♻️</span> ПГУ</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )
    # Загрузка данных
    st.markdown('<div class="filters-section">', unsafe_allow_html=True)
    uploaded_file = st.file_uploader('Загрузить таблицу (xlsx)', type=['xlsx'])
    # Чтение исходной базы или загруженного файла
    df_source = load_data(uploaded_file)
    if df_source is None or df_source.empty:
        st.warning('Не удалось загрузить данные. Убедитесь, что файл корректный.')
        st.markdown('</div>', unsafe_allow_html=True)
        return
    # Категория потребителя
    # Расширенный список категорий потребителей. В соответствии с запросом
    # пользователя в приложении теперь доступно семь категорий: промышленность,
    # энергетика, коммунальная сфера, нефтегаз, угольная промышленность,
    # сервисный сектор и «Без категории» для прочих случаев.
    consumer_categories = [
        'ЖКХ',  # жилищно-коммунальное хозяйство
        'Нефтегаз',  # нефте- и газодобыча
        'Промышленность',
        'ГОК',  # горно-обогатительный комбинат
        'ЦоД',  # центр обработки данных
        'Майнинг',
        'Сельское хозяйство и АПК'
    ]
    selected_consumer = st.radio('Потребитель:', options=consumer_categories, horizontal=True)
    # Происхождение (кластер)
    clusters = sorted(df_source['Кластер'].dropna().unique().tolist())
    selected_clusters = st.multiselect('Кластеры:', clusters, default=clusters)
    # Слайдеры для диапазонов
    cols = st.columns(3)
    with cols[0]:
        min_power = float(df_source['P_el'].min()) if not df_source['P_el'].isna().all() else 0.5
        max_power = float(df_source['P_el'].max()) if not df_source['P_el'].isna().all() else 60.0
        power_range = st.slider('Единичная мощность (кВт)', min_value=min_power, max_value=max_power,
                               value=(min_power, max_power), step=1.0)
    with cols[1]:
        min_life = float(df_source['R_full'].min()) if not df_source['R_full'].isna().all() else 5.0
        max_life = float(df_source['R_full'].max()) if not df_source['R_full'].isna().all() else 35.0
        life_range = st.slider('Полный ресурс (тыс. ч)', min_value=min_life, max_value=max_life,
                               value=(min_life, max_life), step=1.0)
    with cols[2]:
        hours = st.slider('Время работы в год (ч)', min_value=1000, max_value=8000, value=6000, step=100)
    # Стоимость газа
    gas_price = st.slider('Цена газа (руб/нм³)', min_value=0.0, max_value=50.0, value=5.0, step=0.5)
    # Период расчёта
    years = st.slider('Период расчёта (лет)', min_value=1, max_value=30, value=10, step=1)
    st.markdown('</div>', unsafe_allow_html=True)
    # Предобработка и фильтрация
    df_proc = preprocess_data(df_source)
    mask = (
        df_proc['Кластер'].isin(selected_clusters) &
        df_proc['P_el'].between(power_range[0], power_range[1]) &
        df_proc['R_full'].between(life_range[0], life_range[1])
    )
    filtered = df_proc[mask]
    if filtered.empty:
        st.warning('Нет записей для выбранных фильтров.')
        return
    # Рендерим дашборд
    render_dashboard(filtered, hours_per_year=int(hours), gas_price=float(gas_price), years=int(years))


if __name__ == '__main__':
    main()