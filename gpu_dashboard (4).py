"""
ГПУ‑Эксперт — суверенный выбор газопоршневых установок
------------------------------------------------------

Это приложение реализует адаптацию методики выбора ГПУ,
описанной в диссертации Д. А. Кузнецова (2026). В основе
лежит набор из 15 критериев, организованных в 5 групп:
технические, экономические, эксплуатационные, экологические
и санкционные. Каждой группе присвоен фиксированный вес,
отражающий относительную значимость по материалам работы.
Критерий санкционной устойчивости (КСУ) агрегируется
из семи подкритериев с весами, указанными в диссертации
S1–S7. Модели ранжируются по интегральному рейтингу —
взвешенной сумме нормированных показателей. Пользователь
может фильтровать модели по происхождению, мощности,
сроку службы, задавать часы работы и цену газа, а также
загружать собственные таблицы.

Основные особенности:
- фиксированные веса критериев согласно диссертации;
- переработанный тёмный интерфейс в стиле дашборда;
- визуализация топ‑10 моделей (бар‑диаграмма) и анализ
  трёх лидеров на радар‑диаграмме;
- расчёт стоимости жизненного цикла с учётом выбранных
  часов работы и цены газа;
- поддержка загрузки собственной базы данных.

Copyright © 2026. Автор: сообщество.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go


def load_data(uploaded_file: 'st.uploaded_file_manager.UploadedFile | None') -> pd.DataFrame:
    """Читает таблицу Excel и возвращает DataFrame.

    Если файл не загружен, используется стандартная база данных
    GPU_Database_v3.xlsx из каталога приложения.
    """
    if uploaded_file is not None:
        df = pd.read_excel(uploaded_file)
    else:
        df = pd.read_excel('GPU_Database_v3.xlsx')
    return df


def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    """Подготавливает набор данных: приводит имена столбцов к удобному
    формату и рассчитывает комплексный критерий санкционной устойчивости (КСУ).

    Нормализация и расчёт интегрального рейтинга и стоимости жизненного
    цикла производится в функции ``calculate_scores``, так как
    параметры зависят от пользовательского ввода (количество часов
    работы и цена газа).
    """
    # Переименование некоторых столбцов для удобства обращения
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
        'CO, мг/нм³': 'CO',
        'Шум, дБ(А)': 'noise',
        'Масса, кг': 'mass',
        'S1 Геополит.': 'S1',
        'S2 Сервис': 'S2',
        'S3 ЗИП': 'S3',
        'S4 ПО': 'S4',
        'S5 Аналоги': 'S5',
        'S6 Референция': 'S6',
        'S7 Вторич. санкц.': 'S7',
    }
    df.rename(columns=rename_map, inplace=True)

    # Расчёт КСУ как взвешенной суммы подкритериев S1–S7.
    # Веса берутся из диссертации: S1=0.20, S2=0.18, S3=0.17,
    # S4=0.12, S5=0.10, S6=0.10, S7=0.13【0†L254-L260】.
    ksu_weights = {
        'S1': 0.20,
        'S2': 0.18,
        'S3': 0.17,
        'S4': 0.12,
        'S5': 0.10,
        'S6': 0.10,
        'S7': 0.13,
    }
    # Некоторые модели могут не иметь всех семи параметров; заполним NaN нулями.
    for col in ksu_weights:
        if col not in df.columns:
            df[col] = 0.0
    df['KSU'] = sum(df[col].fillna(0) * weight for col, weight in ksu_weights.items())

    # Курсы валют для конвертации в рубли (ориентировочные, актуальные на 2026 г.)
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

    # Перевод CAPEX и OPEX в рубли.
    def convert(value, currency):
        if pd.isna(value):
            return np.nan
        if pd.isna(currency):
            return value  # если валюта не указана, предполагаем рубли
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
    """Нормализует критерии, рассчитывает стоимость жизненного цикла и
    интегральный рейтинг.

    Параметры ``hours_per_year`` и ``gas_price`` задаются пользователем
    для расчёта эксплуатационных затрат и топливных расходов. Цена газа
    указана в рублях за кубический метр (нм³).

    ``years`` определяет длительность планового горизонта расчёта LCC.
    """
    df = df.copy()
    # Расчёт LCC (млн руб) с учётом затрат на топливо
    # LCC = CAPEX * мощность + OPEX * часы_экспл * лет + КР + газ * расход газа * часы * лет
    gas_cost_mrub = (df['gas_flow'].fillna(0).astype(float) * gas_price * hours_per_year * years) / 1e6
    df['LCC_mrub'] = (
        df['CAPEX_rub_per_kw'] * df['P_el'] / 1000 +
        df['OPEX_rub_per_hour'] * hours_per_year * years / 1e6 +
        df['cap_repair_cost_mrub'].fillna(0) +
        gas_cost_mrub
    )

    # Определяем набор критериев и их ориентацию (max=True, min=False)
    criteria = {
        # Технические критерии (G1):
        'eta_el': True,        # K1.1 – электрический КПД (max)
        'eta_cogen': True,     # K1.2 – КПД когенерации (max)
        'P_el': True,          # K1.3 – максимальная мощность (max)
        'ramp_rate': True,     # K1.4 – скорость нагружения (max)
        'R_cr': True,          # K1.5 – ресурс до капремонта (max)
        'R_full': True,        # K1.6 – полный ресурс (max)
        # Экономические критерии (G2):
        'CAPEX_rub_per_kw': False,  # K2.1 – удельные капитальные затраты (min)
        'OPEX_rub_per_hour': False, # K2.2 – затраты на обслуживание (min)
        'LCC_mrub': False,          # K2.3 – стоимость жизненного цикла (min)
        # Эксплуатационные критерии (G3):
        'S3': True,            # K3.1 – доступность ЗИП (max)
        'S2': True,            # K3.2 – сервис в РФ (max)
        # Экологические критерии (G4):
        'NOx': False,          # K4.1 – выбросы NOx (min)
        # Санкционный критерий (G5):
        'KSU': True,           # K5.1 – комплексная санкционная устойчивость (max)
    }

    # Группировка для распределения весов
    group_assignments = {
        'technical': ['eta_el', 'eta_cogen', 'P_el', 'ramp_rate', 'R_cr', 'R_full'],
        'economic': ['CAPEX_rub_per_kw', 'OPEX_rub_per_hour', 'LCC_mrub'],
        'operational': ['S3', 'S2'],
        'environmental': ['NOx'],
        'sanction': ['KSU'],
    }
    # Веса групп по важности (в сумме 1.0)
    group_weights = {
        'technical': 0.35,
        'economic': 0.25,
        'operational': 0.10,
        'environmental': 0.10,
        'sanction': 0.20,
    }

    # Нормализация каждого критерия
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

    # Вычисление весов для каждого критерия в соответствии с группой
    crit_weights = {}
    for group, crit_list in group_assignments.items():
        weight_per_group = group_weights.get(group, 0)
        if crit_list:
            weight_per_crit = weight_per_group / len(crit_list)
            for c in crit_list:
                crit_weights[c] = weight_per_crit

    # Интегральная оценка (взвешенная сумма нормированных значений)
    scores = pd.Series(0.0, index=df.index)
    for crit, weight in crit_weights.items():
        scores += normalized[crit] * weight
    df['score'] = scores
    # Сохраняем нормализованные значения для дальнейшей визуализации
    for crit in normalized:
        df[f'{crit}_norm'] = normalized[crit]
    return df


def build_dashboard(df: pd.DataFrame):
    """Строит интерактивный дашборд Streamlit, отображающий рейтинг
    и ключевые графики.
    """
    # Сортировка и выбор топ‑10
    top_df = df.sort_values('score', ascending=False).reset_index(drop=True).head(10)

    # Столбчатая диаграмма интегральных оценок топ‑10
    bar_fig = px.bar(
        top_df,
        x='Модель ГПУ',
        y='score',
        color='Кластер',
        title='Топ‑10 газопоршневых установок (рейтинг)',
        labels={'score': 'Рейтинг', 'Модель ГПУ': 'Модель'},
        height=450
    )
    st.plotly_chart(bar_fig, use_container_width=True)

    # Радар‑диаграмма для трёх лидеров
    if len(top_df) >= 3:
        radar_categories = [
            'eta_el_norm', 'eta_cogen_norm', 'P_el_norm', 'ramp_rate_norm',
            'R_cr_norm', 'R_full_norm', 'CAPEX_rub_per_kw_norm',
            'OPEX_rub_per_hour_norm', 'LCC_mrub_norm', 'S3_norm', 'S2_norm',
            'NOx_norm', 'KSU_norm'
        ]
        # Для лучшего отображения сопоставим порядок группировке критериев
        category_names = [
            'КПД эл.', 'КПД коген.', 'Мощность', 'Скорость нагруж.',
            'Ресурс до КР', 'Полный ресурс', 'CAPEX', 'OPEX', 'СЖЦ',
            'ЗИП', 'Сервис', 'NOx', 'КСУ'
        ]
        radar_fig = go.Figure()
        for idx in range(3):
            row = top_df.iloc[idx]
            values = [row[col] for col in radar_categories]
            # Замыкаем цикл для замыкания фигуры
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
            title='Паутина критериев для трёх лидеров'
        )
        st.plotly_chart(radar_fig, use_container_width=True)

    # Таблица топ‑10 с ключевыми характеристиками
    table_columns = ['Модель ГПУ', 'Производитель', 'Страна', 'Кластер',
                     'P_el', 'eta_el', 'eta_cogen', 'CAPEX_rub_per_kw',
                     'OPEX_rub_per_hour', 'LCC_mrub', 'KSU', 'score']
    display_df = top_df[table_columns].rename(columns={
        'P_el': 'Pэл, кВт',
        'eta_el': 'КПД эл., %',
        'eta_cogen': 'КПД коген., %',
        'CAPEX_rub_per_kw': 'КАПЕКС, руб/кВт',
        'OPEX_rub_per_hour': 'ОПЕКС, руб/ч',
        'LCC_mrub': 'СЖЦ, млн руб',
        'KSU': 'КСУ',
        'score': 'Рейтинг'
    })
    st.subheader('Данные топ‑10 моделей')
    st.dataframe(display_df.style.format({
        'Pэл, кВт': '{:.0f}',
        'КПД эл., %': '{:.1f}',
        'КПД коген., %': '{:.1f}',
        'КАПЕКС, руб/кВт': '{:,.0f}',
        'ОПЕКС, руб/ч': '{:,.2f}',
        'СЖЦ, млн руб': '{:,.2f}',
        'КСУ': '{:.2f}',
        'Рейтинг': '{:.3f}'
    }), use_container_width=True)

    # Дополнительная информация
    st.markdown("""
    ### Методика расчёта
    Интегральная оценка рассчитывается по формуле
    $$S_i = \sum_{j=1}^n w_j \cdot s_{ij},$$
    где $w_j$ — фиксированный вес $j$‑го критерия (сумма весов групп приведена ниже),
    а $s_{ij}$ — нормированное значение альтернативы *i* по критерию *j*.
    Нормализация критериев производится линейно, по типу *max* или *min*.
    Веса подкритериев санкционной устойчивости (КСУ) следующие:
    $$w_{S1}=0.20,\ w_{S2}=0.18,\ w_{S3}=0.17,\ w_{S4}=0.12,\ w_{S5}=0.10,\ w_{S6}=0.10,\ w_{S7}=0.13.$$【0†L254-L260】
    Веса групп критериев заданы как $(0.35, 0.25, 0.10, 0.10, 0.20)$ для
    технических, экономических, эксплуатационных, экологического и
    санкционного критериев соответственно【0†L149-L150】.
    """)


def main():
    # Настройки страницы
    st.set_page_config(
        page_title='ГПУ‑Эксперт',
        page_icon='⚙️',
        layout='wide',
        initial_sidebar_state='expanded'
    )
    # Тёмная тема по умолчанию
    st.markdown("""
        <style>
        body {
            background-color: #0a0a0a;
            color: #e0e0e0;
        }
        .stApp {
            background-color: #0a0a0a;
        }
        .stDataFrame table {
            background-color: #1a1a1a;
            color: #e0e0e0;
        }
        </style>
    """, unsafe_allow_html=True)

    # Стили для геро-блока на основе изображения
    st.markdown("""
        <style>
        .hero-container {
            background-image: url('header_image.png');
            background-size: cover;
            background-position: center;
            border-radius: 12px;
            padding: 40px 20px;
            margin-bottom: 20px;
            text-align: center;
            color: #ffffff;
            position: relative;
            overflow: hidden;
        }
        .hero-container::after {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(2px);
        }
        .hero-container h1 {
            position: relative;
            z-index: 1;
            font-size: 3rem;
            font-weight: 700;
            margin: 0.2em 0;
        }
        .hero-container p {
            position: relative;
            z-index: 1;
            font-size: 1.2rem;
            margin: 0;
        }
        </style>
    """, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="hero-container">
            <h1>ГПУ‑Эксперт</h1>
            <p>Суверенный выбор газопоршневых установок</p>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.sidebar.header('Параметры фильтрации')
    uploaded_file = st.sidebar.file_uploader('Загрузить собственную базу (Excel)', type=['xlsx'])
    # Загрузка данных
    df_raw = load_data(uploaded_file)
    df_proc = preprocess_data(df_raw)

    # Выбор категории потребителя (для будущего расширения)
    consumer_categories = ['ЖКХ', 'ПНГ (попутный газ)', 'Угольная промышленность',
                           'Промышленный сектор', 'Сервисный сектор', 'Без категории']
    selected_consumer = st.sidebar.selectbox('Категория потребителя', consumer_categories, index=len(consumer_categories)-1)

    # Фильтр по кластеру
    clusters = sorted(df_proc['Кластер'].dropna().unique().tolist())
    selected_clusters = st.sidebar.multiselect('Происхождение оборудования', clusters, default=clusters)

    # Диапазон единничной мощности (P_el)
    power_min = int(df_proc['P_el'].min())
    power_max = int(df_proc['P_el'].max())
    selected_power = st.sidebar.slider('Единичная мощность, кВт', min_value=power_min,
                                      max_value=power_max,
                                      value=(power_min, power_max))

    # Диапазон срока службы (полный ресурс) в тысячах часов
    life_min = int(df_proc['R_full'].min()) if not df_proc['R_full'].isna().all() else 0
    life_max = int(df_proc['R_full'].max()) if not df_proc['R_full'].isna().all() else 100
    selected_life = st.sidebar.slider('Срок службы (тыс. ч)', min_value=life_min,
                                       max_value=life_max,
                                       value=(life_min, life_max))

    # Число часов работы в год
    hours_per_year = st.sidebar.number_input('Время работы в год, ч/год', min_value=1000, max_value=8000, value=6000, step=100)

    # Цена газа (руб/нм³)
    gas_price = st.sidebar.number_input('Цена газа, руб/нм³', min_value=0.0, max_value=100.0, value=5.0, step=0.5)

    # Плановый горизонт в годах
    years = st.sidebar.number_input('Период расчёта, лет', min_value=1, max_value=30, value=10, step=1)

    # Применяем фильтры
    mask = (
        df_proc['Кластер'].isin(selected_clusters) &
        df_proc['P_el'].between(selected_power[0], selected_power[1]) &
        df_proc['R_full'].between(selected_life[0], selected_life[1])
    )
    filtered = df_proc[mask]
    if filtered.empty:
        st.warning('Нет записей, соответствующих выбранным фильтрам.')
        return

    scored = calculate_scores(filtered, hours_per_year=int(hours_per_year), gas_price=float(gas_price), years=int(years))
    build_dashboard(scored)


if __name__ == '__main__':
    main()