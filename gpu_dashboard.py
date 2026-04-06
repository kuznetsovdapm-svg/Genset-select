"""
SuverGPU Navigator – интерактивная система выбора газопоршневых установок
-----------------------------------------------------------------------

Это приложение реализует упрощённую версию методики, предложенной
в диссертационной работе Д. А. Кузнецова (2026). В основе лежат
15 критериев, организованных в 5 групп: технические, экономические,
эксплуатационные, экологические и санкционные. Для каждой группы
назначается фиксированный вес, отражающий относительную значимость
критерия согласно выводам диссертации. Вес внутри группы распределён
равномерно между входящими в неё подкритериями. Критерий санкционной
устойчивости (КСУ) агрегируется из семи подкритериев с весами, указанными
в работе (S1–S7). Модели ранжируются по интегральной оценке – взвешенной
сумме нормированных показателей. Приложение позволяет фильтровать
модели по геополитическому кластеру и диапазону мощностей, а также
загружать собственные базы данных.

Основные отличия от ранних версий:
* фиксированные веса критериев согласно диссертации, без пользовательских
  ползунков;
* переработанный тёмный интерфейс в формате дашборда;
* визуализация топ‑10 моделей в виде столбчатого графика и
  радар‑диаграммы для трёх лидеров;
* упрощённая оценка стоимости жизненного цикла (СЖЦ) на базе
  удельных затрат и интервала эксплуатации;
* поддержка загрузки собственной таблицы Excel.

Copyright © 2026. Автор — сообщество.
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
    формату, рассчитывает КСУ и стоимостные параметры.

    Нормализация и расчёт интегрального рейтинга производится
    непосредственно в функции calculate_scores.
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

    # Стоимость жизненного цикла: приблизительный расчёт.
    # Используем формулу: LCC = CAPEX * мощность + OPEX * часы_экспл * лет + капремонт.
    # Предположим, что установка работает 6000 часов в год и рассматриваем период 10 лет.
    hours_per_year = 6000
    years = 10
    df['LCC_mrub'] = (df['CAPEX_rub_per_kw'] * df['P_el'] / 1000 +
                      df['OPEX_rub_per_hour'] * hours_per_year * years / 1e6 +
                      df['cap_repair_cost_mrub'].fillna(0))

    return df


def calculate_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Нормализует критерии и рассчитывает интегральный рейтинг.

    Возвращает новый DataFrame с колонкой 'score' и нормализованными
    значениями критериев. Веса критериев распределяются согласно
    значимости групп (technical=0.35, economic=0.25, operational=0.10,
    environmental=0.10, sanction=0.20). Внутри каждой группы вес
    делится поровну между входящими критериями.
    """
    df = df.copy()
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
        'technical': 0.35,     # технические параметры – наиболее значимы
        'economic': 0.25,      # экономические параметры
        'operational': 0.10,   # эксплуатационные (сервис/ЗИП)
        'environmental': 0.10, # экологический критерий
        'sanction': 0.20,      # санкционная устойчивость
    }

    # Нормализация каждого критерия
    normalized = {}
    for crit, maximize in criteria.items():
        series = df[crit].astype(float)
        min_val, max_val = series.min(), series.max()
        # если все значения равны или NaN, присваиваем 1.0
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
        title='Топ‑10 газопоршневых установок (интегральный рейтинг)',
        labels={'score': 'Интегральная оценка', 'Модель ГПУ': 'Модель'},
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
        'CAPEX_rub_per_kw': 'CAPEX, руб/кВт',
        'OPEX_rub_per_hour': 'OPEX, руб/ч',
        'LCC_mrub': 'СЖЦ, млн руб',
        'KSU': 'КСУ',
        'score': 'Интегральная оценка'
    })
    st.subheader('Данные топ‑10 моделей')
    st.dataframe(display_df.style.format({
        'Pэл, кВт': '{:.0f}',
        'КПД эл., %': '{:.1f}',
        'КПД коген., %': '{:.1f}',
        'CAPEX, руб/кВт': '{:,.0f}',
        'OPEX, руб/ч': '{:,.2f}',
        'СЖЦ, млн руб': '{:,.2f}',
        'КСУ': '{:.2f}',
        'Интегральная оценка': '{:.3f}'
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
        page_title='SuverGPU Navigator',
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

    st.title('SuverGPU Navigator')
    st.caption('Программная система поддержки принятия решений для выбора газопоршневых установок')

    st.sidebar.header('Параметры фильтрации')
    uploaded_file = st.sidebar.file_uploader('Загрузить собственную базу (Excel)', type=['xlsx'])

    df_raw = load_data(uploaded_file)
    # Фильтры по кластеру и мощности
    clusters = sorted(df_raw['Кластер'].dropna().unique().tolist())
    selected_clusters = st.sidebar.multiselect('Кластер', clusters, default=clusters)
    # Диапазон мощности
    power_min = int(df_raw['Pэл, кВт'].min())
    power_max = int(df_raw['Pэл, кВт'].max())
    selected_power = st.sidebar.slider('Диапазон мощности, кВт', min_value=power_min,
                                      max_value=power_max,
                                      value=(power_min, power_max))

    df_proc = preprocess_data(df_raw)
    # Применяем фильтры
    mask = (
        df_proc['Кластер'].isin(selected_clusters) &
        df_proc['P_el'].between(selected_power[0], selected_power[1])
    )
    filtered = df_proc[mask]
    if filtered.empty:
        st.warning('Нет записей, соответствующих выбранным фильтрам.')
        return
    scored = calculate_scores(filtered)
    build_dashboard(scored)


if __name__ == '__main__':
    main()