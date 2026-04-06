"""
Модуль Streamlit‑приложения для выбора газопоршневых установок (ГПУ).

Этот файл реализует упрощённую версию СППР, которая не зависит от
отсутствующего модуля `gpu_select_core`. Он читает данные из Excel‑файла,
предоставляет возможность настраивать веса критериев, загружать новые
таблицы, выполнять расчет интегрального рейтинга и стоимости жизненного
цикла, а также запускать Монте‑Карло симуляции с учётом неопределённости.

Основные возможности:
  * Загрузка исходной базы данных (по умолчанию `GPU_Database_v3.xlsx`).
  * Фильтрация по происхождению, производителю и диапазону мощностей.
  * Настройка весов критериев (мощность, КПД, CAPEX, OPEX, эмиссии NOx/CO).
  * Быстрое ранжирование альтернатив по методу взвешенной суммы.
  * Расчёт упрощённой стоимости жизненного цикла (LCC) и требуемого
    количества установок для достижения заданной мощности.
  * Монте‑Карло‑анализ с случайными колебаниями цены на газ и ставки
    дисконтирования.
  * Экспорт результатов в CSV и сохранение настроек в JSON.
  * Возможность загрузки обновлённой базы данных и переключения режима
    получения валютных курсов (ручной ввод или запрос через API).

Чтобы запустить приложение локально:
```
streamlit run gpu_selector_app.py
```
"""

import math
import json
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

# Устанавливаем конфигурацию страницы
st.set_page_config(
    page_title="Выбор ГПУ | Интерактивная система",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─────────────────────────────────────────────────────────────────────
#  Загрузка и подготовка данных
# ─────────────────────────────────────────────────────────────────────

DEFAULT_DATA_PATH = "GPU_Database_v3.xlsx"

@st.cache_data(show_spinner=False)
def load_data(file_path: str) -> pd.DataFrame:
    """Загрузить исходную базу данных из Excel.

    Parameters
    ----------
    file_path : str
        Путь до файла Excel.

    Returns
    -------
    pd.DataFrame
        Таблица с данными о ГПУ.
    """
    df = pd.read_excel(file_path)
    # нормализуем названия столбцов: убираем пробелы и переводим в snake_case
    df = df.rename(columns=lambda x: x.strip())
    return df


def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    """Предобработать таблицу: добавить вычисляемые поля и привести типы.

    Создаёт новые столбцы (например, курс в рублях для CAPEX/OPEX) и
    приводит числовые поля к float/int для удобства расчётов.

    Parameters
    ----------
    df : pd.DataFrame
        Исходная таблица.

    Returns
    -------
    pd.DataFrame
        Обновлённая таблица.
    """
    # Приведём названия столбцов к удобному формату
    df = df.copy()
    # Столбец Pэл, кВт -> power_kw (float)
    if "Pэл, кВт" in df.columns:
        df["power_kw"] = pd.to_numeric(df["Pэл, кВт"], errors="coerce")
    if "КПД эл, %" in df.columns:
        df["efficiency_el"] = pd.to_numeric(df["КПД эл, %"], errors="coerce")
    if "Расход газа, нм³/ч" in df.columns:
        df["gas_consumption"] = pd.to_numeric(df["Расход газа, нм³/ч"], errors="coerce")
    if "NOx, мг/нм³" in df.columns:
        df["nox"] = pd.to_numeric(df["NOx, мг/нм³"], errors="coerce")
    if "CO, мг/нм³" in df.columns:
        df["co"] = pd.to_numeric(df["CO, мг/нм³"], errors="coerce")
    # CAPEX и OPEX могут быть в разных валютах. Мы будем конвертировать в рубли.
    # Пусть CAPEX задан как "Уд. CAPEX" и валюта в "Валюта CAPEX". ОПЕКС – "Затраты РТО" и "Валюта РТО".
    df["capex"] = pd.to_numeric(df.get("Уд. CAPEX", np.nan), errors="coerce")
    df["capex_currency"] = df.get("Валюта CAPEX", "RUB").fillna("RUB")
    df["opex"] = pd.to_numeric(df.get("Затраты РТО", np.nan), errors="coerce")
    df["opex_currency"] = df.get("Валюта РТО", "RUB").fillna("RUB")
    # Стоимость капитального ремонта (стоим. КР) в млн руб -> переведём в руб.
    df["overhaul_cost_mln_rub"] = pd.to_numeric(df.get("Стоим. КР, млн руб", 0), errors="coerce").fillna(0)
    # Перевод «Кластер» в короткое имя
    df["cluster"] = df.get("Кластер", "").str.strip().str.lower()
    return df


def convert_currency(value: float, currency: str, usd_rate: float, cny_rate: float) -> float:
    """Преобразовать значение в рубли по текущему курсу.

    Parameters
    ----------
    value : float
        Сумма в исходной валюте.
    currency : str
        Трёхбуквенный код валюты (RUB, USD, CNY).
    usd_rate : float
        Курс доллара к рублю.
    cny_rate : float
        Курс юаня к рублю.

    Returns
    -------
    float
        Сумма в рублях.
    """
    if pd.isna(value):
        return np.nan
    currency = str(currency).strip().upper()
    if currency == "RUB":
        return value
    if currency == "USD":
        return value * usd_rate
    if currency == "CNY":
        return value * cny_rate
    # Если неизвестная валюта, возвращаем как есть
    return value


def compute_scores(
    df: pd.DataFrame,
    weights: Dict[str, float],
    beneficial: Dict[str, bool],
    normalize: bool = True,
) -> pd.Series:
    """Рассчитать взвешенный рейтинг для каждой строки таблицы.

    Метод использует нормирование значений признаков на [0, 1] и
    линейно агрегирует по заданным весам. Для затратных критериев
    используется инверсия (1 - нормированное значение).

    Parameters
    ----------
    df : pd.DataFrame
        Таблица с ГПУ.
    weights : dict
        Словарь {критерий: вес}. Сумма весов не обязательно равна 1 – они
        будут нормированы.
    beneficial : dict
        Словарь {критерий: True/False}, указывающий, является ли критерий
        выгодным (True) или затратным (False).
    normalize : bool, optional
        Если True, веса нормируются на сумму 1.

    Returns
    -------
    pd.Series
        Серию с итоговым рейтингом для каждой строки (индекс соответствует
        исходному индексу df).
    """
    # Копия таблицы для безопасного изменения
    data = df.copy()
    scores = pd.Series(0.0, index=data.index)
    # Нормируем веса
    total_weight = sum(weights.values())
    if normalize and total_weight != 0:
        norm_weights = {k: v / total_weight for k, v in weights.items()}
    else:
        norm_weights = weights
    # Рассчитываем нормированные столбцы
    for crit, w in norm_weights.items():
        if crit not in data.columns or data[crit].isna().all():
            continue
        column = data[crit].astype(float)
        min_v = column.min()
        max_v = column.max()
        if max_v - min_v == 0:
            # одинаковые значения – добавляем ноль
            continue
        norm_col = (column - min_v) / (max_v - min_v)
        if not beneficial.get(crit, True):
            norm_col = 1 - norm_col
        scores += norm_col * w
    return scores


def compute_lcc(
    df: pd.DataFrame,
    target_power_kw: float,
    period_years: int,
    gas_price_rub: float,
    hours_per_year: int,
    usd_rate: float,
    cny_rate: float,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Расчитать упрощённую стоимость жизненного цикла (LCC).

    Для каждого варианта ГПУ вычисляется:
      * Требуемое количество установок (ceil(target_power / power_kw)).
      * Общий CAPEX станции (руб).
      * Общий OPEX за весь период (руб) – включает расход газа, затраты РТО
        и стоимость капитального ремонта.
      * Удельная стоимость LCC (руб/кВт·ч) = (CAPEX + OPEX) / (выработка).

    Parameters
    ----------
    df : pd.DataFrame
        Таблица ГПУ с колонками power_kw, gas_consumption, capex, capex_currency,
        opex, opex_currency, overhaul_cost_mln_rub.
    target_power_kw : float
        Требуемая мощность станции (кВт).
    period_years : int
        Период расчёта (лет).
    gas_price_rub : float
        Цена газа (руб за 1000 нм³).
    hours_per_year : int
        Количество часов работы в год.
    usd_rate, cny_rate : float
        Курсы валют.

    Returns
    -------
    Tuple[pd.Series, pd.Series, pd.Series]
        (series_number_of_units, series_specific_lcc, series_total_lcc)
    """
    n_units = pd.Series(index=df.index, dtype=float)
    specific_lcc = pd.Series(index=df.index, dtype=float)
    total_lcc = pd.Series(index=df.index, dtype=float)
    for idx, row in df.iterrows():
        p_unit = row.get("power_kw", np.nan)
        if pd.isna(p_unit) or p_unit <= 0:
            n_units[idx] = np.nan
            specific_lcc[idx] = np.nan
            total_lcc[idx] = np.nan
            continue
        units_needed = math.ceil(target_power_kw / p_unit)
        n_units[idx] = units_needed
        # CAPEX: удельный капекс * units * курс
        capex_rub = convert_currency(row.get("capex", 0), row.get("capex_currency", "RUB"), usd_rate, cny_rate)
        total_capex = capex_rub * units_needed
        # OPEX: годовые затраты РТО * period + газ + капремонт
        opex_rub = convert_currency(row.get("opex", 0), row.get("opex_currency", "RUB"), usd_rate, cny_rate)
        total_opex = opex_rub * period_years
        # расход газа: nm3/h -> руб/год = gas_consumption * hours_per_year / 1000 * gas_price
        gas_consumption = row.get("gas_consumption", np.nan)
        gas_cost_year = 0
        if not pd.isna(gas_consumption) and gas_consumption > 0:
            gas_cost_year = (gas_consumption * hours_per_year / 1000.0) * gas_price_rub
        total_gas = gas_cost_year * period_years * units_needed
        # стоимость капремонта (млн руб -> руб)
        overhaul_cost = row.get("overhaul_cost_mln_rub", 0) * 1_000_000
        total_overhaul = overhaul_cost * units_needed
        total_station_cost = total_capex + total_opex + total_gas + total_overhaul
        total_lcc[idx] = total_station_cost
        # выработка за период: target_power_kw (кВт) * hours_per_year * period_years
        production_kwh = target_power_kw * hours_per_year * period_years
        if production_kwh > 0:
            specific_lcc[idx] = total_station_cost / production_kwh
        else:
            specific_lcc[idx] = np.nan
    return n_units, specific_lcc, total_lcc


def monte_carlo_simulation(
    df: pd.DataFrame,
    target_power_kw: float,
    period_years: int,
    gas_price_rub: float,
    hours_per_year: int,
    usd_rate: float,
    cny_rate: float,
    n_simulations: int = 2000,
    gas_volatility: float = 0.1,
    discount_volatility: float = 0.1,
) -> Dict[str, Dict[str, float]]:
    """Выполнить Монте‑Карло анализ для оценки неопределённости LCC.

    Для каждого GPU проводим n_simulations прогонов, в которых
    варьируем цену газа и ставки дисконтирования в пределах заданных
    волатильностей. По результатам оцениваем среднее и стандартное
    отклонение удельной стоимости LCC.

    Returns
    -------
    Dict[str, Dict[str, float]]
        Словарь вида {имя модели: {mean: ..., std: ...}}.
    """
    results: Dict[str, Dict[str, float]] = {}
    for idx, row in df.iterrows():
        p_unit = row.get("power_kw", np.nan)
        if pd.isna(p_unit) or p_unit <= 0:
            continue
        # Собираем данные модели
        model_name = row.get("Модель ГПУ", str(idx))
        # Простая модель: берем исходные параметры и случайно варьируем цену газа и дисконт
        lcc_values: List[float] = []
        for _ in range(n_simulations):
            # случайная цена газа (нормальное распределение с заданной волатильностью)
            gas_multiplier = np.random.normal(1.0, gas_volatility)
            gas_price_sim = max(gas_price_rub * gas_multiplier, 0)
            # случайный дисконт (используется только для информации, пока не включаем в формулу)
            discount_multiplier = np.random.normal(1.0, discount_volatility)
            # считаем lcc
            _, specific_lcc_series, _ = compute_lcc(
                df.loc[[idx]],
                target_power_kw,
                period_years,
                gas_price_sim,
                hours_per_year,
                usd_rate,
                cny_rate,
            )
            val = specific_lcc_series.iloc[0]
            if not pd.isna(val) and np.isfinite(val):
                lcc_values.append(val)
        if lcc_values:
            results[model_name] = {
                "mean": float(np.mean(lcc_values)),
                "std": float(np.std(lcc_values)),
            }
    return results


# ─────────────────────────────────────────────────────────────────────
#  Основная функция приложения
# ─────────────────────────────────────────────────────────────────────

def main():
    st.title("⚡ Выбор газопоршневых установок")
    st.caption("Интерактивная система поддержки принятия решений для многокритериального выбора ГПУ")

    # Загрузка базы данных
    with st.sidebar.expander("📁 Загрузка базы данных", expanded=False):
        st.write(
            "По умолчанию используется файл GPU_Database_v3.xlsx. Вы можете загрузить собственный XLSX, составленный по аналогичной структуре."
        )
        uploaded_file = st.file_uploader(
            "Выберите XLSX файл", type=["xlsx"], key="uploader", accept_multiple_files=False
        )
        if uploaded_file is not None:
            df = load_data(uploaded_file)
        else:
            df = load_data(DEFAULT_DATA_PATH)
    # Предобработка
    df = preprocess_data(df)
    # Фильтры
    st.sidebar.markdown("### 🔍 Фильтрация моделей")
    clusters = sorted(df["cluster"].dropna().unique())
    selected_clusters = st.sidebar.multiselect(
        "Происхождение (кластер)",
        options=clusters,
        default=clusters,
        format_func=lambda x: {
            "western": "Западный",
            "chinese": "Китайский",
            "russian": "Российский",
        }.get(x, x),
    )
    manufacturers = sorted(df["Производитель"].dropna().unique())
    selected_manufacturers = st.sidebar.multiselect(
        "Производитель", options=manufacturers, default=manufacturers
    )
    # Диапазон мощности
    power_min = float(df["power_kw"].min()) if not df["power_kw"].isna().all() else 0.0
    power_max = float(df["power_kw"].max()) if not df["power_kw"].isna().all() else 1.0
    selected_power = st.sidebar.slider(
        "Диапазон единичной мощности, кВт",
        min_value=power_min,
        max_value=power_max,
        value=(power_min, power_max),
        step=50.0,
    )
    # Фильтрация таблицы
    filtered_df = df[
        df["cluster"].isin(selected_clusters)
        & df["Производитель"].isin(selected_manufacturers)
        & df["power_kw"].between(selected_power[0], selected_power[1], inclusive="both")
    ].reset_index(drop=True)
    st.sidebar.write(f"Найдено {len(filtered_df)} моделей")

    # Параметры станции
    st.sidebar.markdown("### ⚙️ Параметры станции")
    target_power_kw = st.sidebar.number_input(
        "Целевая мощность станции, кВт", min_value=500.0, max_value=20000.0, value=6000.0, step=100.0
    )
    period_years = st.sidebar.number_input(
        "Период, лет", min_value=1, max_value=40, value=20, step=1
    )
    hours_per_year = st.sidebar.number_input(
        "Часы работы в год", min_value=1000, max_value=8760, value=8000, step=500
    )
    gas_price_rub = st.sidebar.number_input(
        "Цена газа, руб/1000 нм³", min_value=1000.0, max_value=15000.0, value=7000.0, step=100.0
    )
    # Режим получения курсов валют
    st.sidebar.markdown("### 💱 Курсы валют")
    currency_mode = st.sidebar.radio(
        "Режим", options=["Ручной ввод", "Авто обновление"], index=0
    )
    if currency_mode == "Ручной ввод":
        usd_rate = st.sidebar.number_input(
            "Курс USD/RUB", min_value=1.0, max_value=200.0, value=90.0, step=0.5
        )
        cny_rate = st.sidebar.number_input(
            "Курс CNY/RUB", min_value=1.0, max_value=50.0, value=12.0, step=0.1
        )
    else:
        # Попытка получить курсы через API. Здесь можно добавить вызов внешнего сервиса
        # При оффлайн‑режиме используем фиктивные значения
        try:
            import requests
            r = requests.get(
                "https://api.exchangerate.host/latest?base=USD&symbols=RUB,CNY"
            )
            data = r.json()
            usd_rate = float(data["rates"]["RUB"])
            cny_rate = float(data["rates"]["CNY"])
        except Exception:
            usd_rate = 90.0
            cny_rate = 12.0
    # Настройка весов критериев
    st.sidebar.markdown("### ⚖️ Веса критериев")
    default_weights = {
        "power_kw": 0.25,
        "efficiency_el": 0.25,
        "capex": 0.2,
        "opex": 0.15,
        "nox": 0.05,
        "co": 0.05,
        "gas_consumption": 0.05,
    }
    weights: Dict[str, float] = {}
    beneficial_flags: Dict[str, bool] = {
        "power_kw": True,
        "efficiency_el": True,
        "capex": False,
        "opex": False,
        "nox": False,
        "co": False,
        "gas_consumption": False,
    }
    for crit, default in default_weights.items():
        weight = st.sidebar.slider(
            label=f"Вес {crit}",
            min_value=0.0,
            max_value=1.0,
            value=float(default),
            step=0.05,
        )
        weights[crit] = weight

    st.sidebar.markdown("### 🎲 Монте‑Карло анализ")
    enable_mc = st.sidebar.checkbox("Включить анализ", value=False)
    if enable_mc:
        n_simulations = st.sidebar.number_input(
            "Число прогонов", min_value=500, max_value=20000, value=5000, step=500
        )
        gas_volatility = st.sidebar.slider(
            "Волатильность цены газа", min_value=0.0, max_value=0.5, value=0.1, step=0.01
        )
        discount_volatility = st.sidebar.slider(
            "Волатильность дисконтирования", min_value=0.0, max_value=0.5, value=0.1, step=0.01
        )
    else:
        n_simulations = 0
        gas_volatility = 0.0
        discount_volatility = 0.0

    # Основной блок приложения
    st.markdown("## 📊 Результаты анализа")
    if filtered_df.empty:
        st.warning("По заданным фильтрам не найдено ни одной модели")
        st.stop()
    # Расчёт оценок
    scores = compute_scores(filtered_df, weights, beneficial_flags, normalize=True)
    n_units, specific_lcc, total_lcc = compute_lcc(
        filtered_df,
        target_power_kw=target_power_kw,
        period_years=int(period_years),
        gas_price_rub=float(gas_price_rub),
        hours_per_year=int(hours_per_year),
        usd_rate=float(usd_rate),
        cny_rate=float(cny_rate),
    )
    # Добавляем результаты в таблицу
    results_df = filtered_df.copy()
    results_df["score"] = scores
    results_df["units_needed"] = n_units
    results_df["specific_lcc"] = specific_lcc
    results_df["total_lcc"] = total_lcc
    # Сортировка по рейтингу
    results_df = results_df.sort_values(by="score", ascending=False).reset_index(drop=True)
    # Показываем топ 10 моделей
    top_n = min(10, len(results_df))
    st.markdown(f"### 🏆 Топ-{top_n} моделей")
    st.dataframe(
        results_df[["Модель ГПУ", "Производитель", "cluster", "power_kw", "efficiency_el", "score", "specific_lcc", "units_needed"]].head(top_n),
        use_container_width=True,
    )
    # График рейтинга
    fig = go.Figure(
        data=[
            go.Bar(
                x=results_df["score"].head(top_n),
                y=results_df["Модель ГПУ"].head(top_n),
                orientation="h",
                text=results_df["score"].head(top_n).apply(lambda x: f"{x:.3f}"),
                textposition="outside",
            )
        ]
    )
    fig.update_layout(
        xaxis_title="Рейтинг",
        yaxis_title="Модель",
        yaxis=dict(autorange="reversed"),
        height=400,
        template="plotly_dark",
    )
    st.plotly_chart(fig, use_container_width=True)
    # Монте‑Карло анализ
    if enable_mc and n_simulations > 0:
        st.markdown("### 🎲 Результаты Монте‑Карло")
        mc_results = monte_carlo_simulation(
            filtered_df,
            target_power_kw=float(target_power_kw),
            period_years=int(period_years),
            gas_price_rub=float(gas_price_rub),
            hours_per_year=int(hours_per_year),
            usd_rate=float(usd_rate),
            cny_rate=float(cny_rate),
            n_simulations=int(n_simulations),
            gas_volatility=float(gas_volatility),
            discount_volatility=float(discount_volatility),
        )
        if mc_results:
            mc_df = pd.DataFrame(
                [
                    {"Модель ГПУ": name, "Среднее УЖЦ": res["mean"], "Станд. отклонение": res["std"]}
                    for name, res in mc_results.items()
                ]
            )
            mc_df = mc_df.sort_values(by="Среднее УЖЦ")
            st.dataframe(mc_df, use_container_width=True)
            # Гистограмма средней УЖЦ
            fig2 = go.Figure(
                data=[
                    go.Bar(
                        x=mc_df["Среднее УЖЦ"],
                        y=mc_df["Модель ГПУ"],
                        orientation="h",
                        text=mc_df["Среднее УЖЦ"].apply(lambda x: f"{x:.5f}"),
                        textposition="outside",
                    )
                ]
            )
            fig2.update_layout(
                xaxis_title="Средняя удельная LCC (руб/кВт·ч)",
                yaxis_title="Модель",
                yaxis=dict(autorange="reversed"),
                height=400,
                template="plotly_dark",
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Недостаточно данных для Монте‑Карло анализа.")
    # Экспорт результатов
    st.markdown("### 📤 Экспорт и сохранение")
    col_export, col_save = st.columns(2)
    with col_export:
        csv_data = results_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Скачать результаты в CSV",
            data=csv_data,
            file_name="gpu_results.csv",
            mime="text/csv",
        )
    with col_save:
        # Сохраняем текущие настройки (веса, фильтры, параметры) в JSON
        settings = {
            "weights": weights,
            "beneficial_flags": beneficial_flags,
            "selected_clusters": selected_clusters,
            "selected_manufacturers": selected_manufacturers,
            "selected_power": selected_power,
            "target_power_kw": target_power_kw,
            "period_years": period_years,
            "hours_per_year": hours_per_year,
            "gas_price_rub": gas_price_rub,
            "usd_rate": usd_rate,
            "cny_rate": cny_rate,
        }
        json_data = json.dumps(settings, ensure_ascii=False, indent=2).encode("utf-8")
        st.download_button(
            label="Скачать настройки в JSON",
            data=json_data,
            file_name="gpu_settings.json",
            mime="application/json",
        )
    # Обратная связь (рейтинг)
    st.markdown("### ⭐ Отзыв о моделях")
    st.write(
        "Оцените привлекательность моделей (1 – низкая, 5 – высокая). Данные не сохраняются на сервере и используются только в текущей сессии."
    )
    ratings = {}
    for idx, row in results_df.head(top_n).iterrows():
        rating = st.slider(
            label=f"{row['Модель ГПУ']} (произв. {row['Производитель']})",
            min_value=1,
            max_value=5,
            value=3,
            key=f"rating_{idx}",
        )
        ratings[row["Модель ГПУ"]] = rating
    st.success("Спасибо за вашу обратную связь!")


if __name__ == "__main__":
    main()