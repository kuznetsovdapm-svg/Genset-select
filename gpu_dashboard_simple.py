"""
ГПУ‑Эксперт (упрощённый интерфейс)
====================================

Эта версия веб‑приложения для выбора газопоршневых установок
реализована полностью на основе кода Streamlit и не использует
каких‑либо фоновых изображений или сложных CSS‑наложений.
Задача — создать чистый, стабильный и легко читаемый интерфейс,
который напоминает исходный концепт (фильтры, рейтинги, графики
и таблицы), но избегает проблем с перекрытием и визуальными
дефектами.

Ключевые особенности:
* Загрузка Excel‑таблицы с характеристиками ГПУ (по умолчанию
  используется ``GPU_Database_v3.xlsx``). Пользователь может
  загрузить свой файл в формате XLSX.
* Предобработка данных: переименование столбцов, вычисление
  совокупного санкционного показателя (КСУ) на основе семи
  подкритериев и конвертация денежных затрат в рубли.
* Фильтры: выбор кластеров, диапазонов мощности и ресурса,
  категории потребителя, часов работы в год, цены газа и
  периода расчёта. Все подписи и значения даны на русском языке.
* Расчёт стоимости жизненного цикла (СЖЦ) и интегрального
  рейтинга на основе фиксированных весов критериев (0.38,
  0.32, 0.10, 0.10, 0.10 для технических, экономических,
  эксплуатационных, экологических и санкционных групп).
* Визуализация результатов: горизонтальный бар‑чарт с топ‑10
  решений, радар‑диаграмма для трёх лидеров и таблица ключевых
  параметров. Диаграммы построены с помощью Plotly, а таблица —
  средствами Streamlit.
* Минимальное, но выразительное оформление: тёмная палитра,
  аккуратные карточки и формула итоговой оценки внизу.

Чтобы запустить приложение, выполните команду::

    streamlit run gpu_dashboard_simple.py

Файл ``GPU_Database_v3.xlsx`` должен находиться в том же каталоге,
что и скрипт, или его можно загрузить через интерфейс.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path


def load_data(uploaded_file: 'st.uploaded_file_manager.UploadedFile | None') -> pd.DataFrame:
    """Загружает данные из Excel‑файла. Если файл не передан,
    используется стандартный ``GPU_Database_v3.xlsx``. Возвращает
    DataFrame или пустую таблицу при ошибке.
    """
    try:
        if uploaded_file is not None:
            return pd.read_excel(uploaded_file)
        default_path = Path('GPU_Database_v3.xlsx')
        if default_path.exists():
            return pd.read_excel(default_path)
    except Exception as exc:
        st.error(f"Ошибка загрузки файла: {exc}")
    return pd.DataFrame()


def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    """Переименовывает столбцы, вычисляет КСУ, конвертирует
    денежные значения в рубли и возвращает копию DataFrame.
    """
    df = df.copy()
    # Переименование столбцов для более удобного обращения
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
    # Весовые коэффициенты санкционных подкритериев (из диссертации)
    ksu_weights = {
        'S1': 0.20,
        'S2': 0.18,
        'S3': 0.17,
        'S4': 0.12,
        'S5': 0.10,
        'S6': 0.10,
        'S7': 0.13,
    }
    # Добавляем недостающие столбцы S1–S7 со значением 0
    for col in ksu_weights:
        if col not in df.columns:
            df[col] = 0.0
    df['KSU'] = sum(df[col].fillna(0) * weight for col, weight in ksu_weights.items())
    # Конвертация CAPEX и OPEX в рубли. Если валюта не определена,
    # предполагаем, что значение уже дано в руб.
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
        cur = str(currency).strip().upper() if pd.notna(currency) else 'RUB'
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
    """Рассчитывает стоимость жизненного цикла (СЖЦ) и интегральный
    рейтинг на основе нормированных критериев и весов групп.

    Параметры:
    - hours_per_year: количество часов работы в год
    - gas_price: цена газа в рублях за нм³
    - years: период расчёта в годах
    """
    df = df.copy()
    # Стоимость газа (млн руб): расход газа [нм³/ч] * цена * часы * годы / 1e6
    gas_cost_mrub = (df['gas_flow'].fillna(0).astype(float) * gas_price * hours_per_year * years) / 1e6
    # СЖЦ, млн руб = CAPEX*P_el/1000 + OPEX*hours*years/1e6 + cap_repair + cost_gas
    df['LCC_mrub'] = (
        df['CAPEX_rub_per_kw'] * df['P_el'].fillna(0) / 1000 +
        df['OPEX_rub_per_hour'] * hours_per_year * years / 1e6 +
        df['cap_repair_cost_mrub'].fillna(0) +
        gas_cost_mrub
    )
    # Определяем, какие критерии максимизируем (True) и минимизируем (False)
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
        'S3': True,   # доступность ЗИП
        'S2': True,   # сервис
        'NOx': False, # выбросы
        'KSU': True,  # санкционная устойчивость
    }
    # Группы критериев и весовые коэффициенты
    group_assignments = {
        'technical': ['eta_el', 'eta_cogen', 'P_el', 'ramp_rate', 'R_cr', 'R_full'],
        'economic': ['CAPEX_rub_per_kw', 'OPEX_rub_per_hour', 'LCC_mrub'],
        'operational': ['S3', 'S2'],
        'environmental': ['NOx'],
        'sanction': ['KSU'],
    }
    group_weights = {
        'technical': 0.38,
        'economic': 0.32,
        'operational': 0.10,
        'environmental': 0.10,
        'sanction': 0.10,
    }
    # Нормализация критериев в диапазоне [0,1]
    normalized = {}
    for crit, maximize in criteria.items():
        series = df.get(crit)
        if series is None:
            normalized[crit] = pd.Series(1.0, index=df.index)
            continue
        series = series.astype(float)
        min_val = series.min()
        max_val = series.max()
        if pd.isna(min_val) or pd.isna(max_val) or min_val == max_val:
            normalized[crit] = pd.Series(1.0, index=series.index)
        else:
            if maximize:
                normalized[crit] = (series - min_val) / (max_val - min_val)
            else:
                normalized[crit] = (max_val - series) / (max_val - min_val)
    # Распределяем веса внутри каждой группы поровну между критериями
    crit_weights = {}
    for group, crit_list in group_assignments.items():
        group_weight = group_weights.get(group, 0)
        if crit_list:
            per = group_weight / len(crit_list)
            for c in crit_list:
                crit_weights[c] = per
    # Итоговый рейтинг — сумма нормированных критериев с весами
    scores = pd.Series(0.0, index=df.index)
    for crit, weight in crit_weights.items():
        scores += normalized[crit] * weight
    df['score'] = scores
    # Сохраняем нормированные значения для радар‑диаграммы
    for crit, norm_series in normalized.items():
        df[f'{crit}_norm'] = norm_series
    return df


def render_dashboard(df: pd.DataFrame, hours_per_year: int, gas_price: float, years: int):
    """Отрисовывает графики и таблицу по фильтрованным данным.
    Ожидает уже предобработанный DataFrame. Использует calculate_scores
    для вычисления СЖЦ и рейтингов. Выводит три секции: топ‑10 решений
    (бар‑чарт), профиль лидеров (радар) и ключевые параметры (таблица).
    """
    df_scored = calculate_scores(df, hours_per_year=hours_per_year, gas_price=gas_price, years=years)
    # Выбираем топ‑10 моделей по рейтингу
    top_df = df_scored.sort_values('score', ascending=False).head(10).reset_index(drop=True)
    # Горизонтальный бар‑чарт
    bar_fig = px.bar(
        top_df.iloc[::-1],
        x='score',
        y='Модель ГПУ',
        orientation='h',
        color='Кластер',
        color_discrete_sequence=['#64b5f6', '#4db6ac', '#ffb74d'],
        height=350,
    )
    bar_fig.update_layout(
        xaxis_title='Рейтинг',
        yaxis_title='',
        showlegend=True,
        legend_title='',
        plot_bgcolor='#0b1e3c',
        paper_bgcolor='#0b1e3c',
        font_color='#cbd5e1',
        margin=dict(l=50, r=10, t=10, b=10)
    )
    # Радар‑диаграмма (если хотя бы три модели)
    radar_fig = go.Figure()
    if len(top_df) >= 3:
        radar_categories = [
            'eta_el_norm', 'eta_cogen_norm', 'P_el_norm', 'ramp_rate_norm',
            'R_cr_norm', 'R_full_norm', 'CAPEX_rub_per_kw_norm',
            'OPEX_rub_per_hour_norm', 'LCC_mrub_norm', 'S3_norm', 'S2_norm',
            'NOx_norm', 'KSU_norm'
        ]
        category_labels = [
            'КПД эл.', 'КПД коген.', 'Мощность', 'Нагрузка',
            'Ресурс до КР', 'Полный ресурс', 'КАПЕКС', 'ОПЕКС',
            'СЖЦ', 'ЗИП', 'Сервис', 'NOx', 'КСУ'
        ]
        for i in range(3):
            row = top_df.iloc[i]
            values = [row[col] for col in radar_categories]
            values.append(values[0])  # замыкаем диаграмму
            radar_fig.add_trace(go.Scatterpolar(
                r=values,
                theta=category_labels + [category_labels[0]],
                fill='toself',
                name=f"{i + 1}. {row['Модель ГПУ']}",
                opacity=0.6
            ))
        radar_fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
            showlegend=True,
            legend=dict(font=dict(color='#cbd5e1')),
            plot_bgcolor='#0b1e3c',
            paper_bgcolor='#0b1e3c',
            font_color='#cbd5e1',
            height=350,
            margin=dict(l=30, r=30, t=10, b=10)
        )
    # Таблица ключевых параметров для трёх лидеров
    leaders = top_df.head(3).copy()
    table_data = {
        'Параметр': ['Мощность, кВт', 'Эл. КПД, %', 'Коген. КПД, %', 'КАПЕКС, млн руб', 'СЖЦ, млн руб', 'КСУ'],
    }
    for idx in range(len(leaders)):
        label = leaders.loc[idx, 'Модель ГПУ']
        capex_total = (leaders.loc[idx, 'CAPEX_rub_per_kw'] * leaders.loc[idx, 'P_el']) / 1e3
        table_data[label] = [
            f"{leaders.loc[idx, 'P_el']:.0f}",
            f"{leaders.loc[idx, 'eta_el']:.1f}",
            f"{leaders.loc[idx, 'eta_cogen']:.1f}",
            f"{capex_total:.1f}",
            f"{leaders.loc[idx, 'LCC_mrub']:.1f}",
            f"{leaders.loc[idx, 'KSU']:.2f}",
        ]
    table_df = pd.DataFrame(table_data)
    # Размещение трёх колонок
    col1, col2, col3 = st.columns([1.4, 1.1, 1.3])
    with col1:
        st.subheader('Топ‑10 решений')
        st.plotly_chart(bar_fig, use_container_width=True)
    with col2:
        st.subheader('Профиль лидеров')
        if radar_fig.data:
            st.plotly_chart(radar_fig, use_container_width=True)
        else:
            st.info('Недостаточно данных для построения радар‑диаграммы.')
    with col3:
        st.subheader('Ключевые параметры')
        st.dataframe(table_df, use_container_width=True, hide_index=True)
    # Формула итоговой оценки
    st.markdown(
        """
        <div style="margin-top:10px; font-size:1.0rem; text-align:center;">
            <span style="color:#64b5f6; font-weight:600;">0,38·G₁</span> +
            <span style="color:#4db6ac; font-weight:600;">0,32·G₂</span> +
            <span style="color:#ffb74d; font-weight:600;">0,10·G₃</span> +
            <span style="color:#ba68c8; font-weight:600;">0,10·G₄</span> +
            <span style="color:#f06292; font-weight:600;">0,10·G₅</span>
        </div>
        """,
        unsafe_allow_html=True
    )


def main():
    """Основная функция, отвечающая за построение интерфейса и логику фильтрации."""
    st.set_page_config(page_title='ГПУ‑Эксперт', page_icon='⚙️', layout='wide')
    # Глобальные стили для тёмной темы
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
        html, body, [class*="st-"] {
            background-color: #0b1e3c;
            color: #cbd5e1;
            font-family: 'Inter', sans-serif;
        }
        /* Стилизация селекторов и слайдеров */
        .stSelectbox > div > div {
            background-color: #112240;
            color: #cbd5e1;
            border-radius: 4px;
        }
        .stSlider > div {
            color: #cbd5e1;
        }
        .stFileUploader > label {
            color: #cbd5e1;
        }
        .stDataFrame {
            background-color: #112240;
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    # Заголовок и навигационные значки в одной строке
    header_col1, header_col2 = st.columns([3, 1])
    with header_col1:
        st.markdown(
            "<h1 style='margin-bottom:0;'>ГПУ‑Эксперт</h1>\n"
            "<span style='color:#8da4c6;'>Суверенный выбор газопоршневых установок</span>",
            unsafe_allow_html=True
        )
    with header_col2:
        st.markdown(
            """
            <div style="display:flex; justify-content:flex-end; gap:15px; font-size:0.9rem;">
                <span style="display:flex; align-items:center; gap:5px;"><span>📦</span> ГПУ</span>
                <span style="display:flex; align-items:center; gap:5px;"><span>⚡</span> ГТЭС</span>
                <span style="display:flex; align-items:center; gap:5px;"><span>♻️</span> ПГУ</span>
            </div>
            """,
            unsafe_allow_html=True
        )
    st.markdown("---")
    # Загрузка данных
    uploaded_file = st.file_uploader('Загрузить таблицу (xlsx)', type=['xlsx'])
    df_source = load_data(uploaded_file)
    if df_source.empty:
        st.warning('Файл данных не найден или не содержит данных. Загрузите корректный XLSX.')
        return
    # Предобработка
    df_processed = preprocess_data(df_source)
    # Определение фильтров
    st.subheader('Фильтры')
    # Категория потребителя
    categories = [
        'ЖКХ',
        'Нефтегаз',
        'Промышленность',
        'ГОК',
        'ЦоД',
        'Майнинг',
        'Сельское хозяйство и АПК',
    ]
    selected_category = st.selectbox('Категория потребителя:', options=categories, index=0)
    # Кластеры
    clusters = sorted(df_processed['Кластер'].dropna().unique().tolist())
    selected_clusters = st.multiselect('Кластеры:', options=clusters, default=clusters)
    # Диапазоны мощности и полного ресурса
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if 'P_el' in df_processed and not df_processed['P_el'].isna().all():
            min_power, max_power = float(df_processed['P_el'].min()), float(df_processed['P_el'].max())
        else:
            min_power, max_power = 0.5, 60.0
        power_range = st.slider('Единичная мощность (кВт):', min_value=min_power, max_value=max_power,
                                value=(min_power, max_power), step=1.0)
    with col_b:
        if 'R_full' in df_processed and not df_processed['R_full'].isna().all():
            min_life, max_life = float(df_processed['R_full'].min()), float(df_processed['R_full'].max())
        else:
            min_life, max_life = 5.0, 35.0
        life_range = st.slider('Полный ресурс (тыс. ч):', min_value=min_life, max_value=max_life,
                               value=(min_life, max_life), step=1.0)
    with col_c:
        hours_per_year = st.slider('Время работы в год (ч):', min_value=1000, max_value=8000,
                                   value=6000, step=100)
    # Цена газа и период расчёта
    gas_price = st.slider('Цена газа (руб/нм³):', min_value=0.0, max_value=50.0, value=5.0, step=0.5)
    years = st.slider('Период расчёта (лет):', min_value=1, max_value=30, value=10, step=1)
    # Применяем фильтр
    filtered = df_processed[
        df_processed['Кластер'].isin(selected_clusters) &
        df_processed['P_el'].between(power_range[0], power_range[1]) &
        df_processed['R_full'].between(life_range[0], life_range[1])
    ].copy()
    if filtered.empty:
        st.info('Нет подходящих записей для выбранных фильтров.')
        return
    # Отрисовываем дашборд
    render_dashboard(filtered, hours_per_year=int(hours_per_year), gas_price=float(gas_price), years=int(years))


if __name__ == '__main__':
    main()