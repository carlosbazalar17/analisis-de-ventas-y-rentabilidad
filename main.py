"""
ETL - Dashboard de Análisis de Ventas y Rentabilidad
Dataset: Online Retail II (Kaggle/UCI)
Flujo: Descarga → Limpieza → Normalización → Feature Engineering → Exportación SQL

"""

import pandas as pd
import numpy as np
import sqlite3
import os

RAW_FILE = "online_retail_II.csv" 

# ─────────────────────────────────────────────
# 1. CARGA
# ─────────────────────────────────────────────
def load_raw(filepath: str) -> pd.DataFrame:
    """Carga el archivo CSV en un único DataFrame."""
    print("Cargando dataset desde CSV...")
    
    # Agregamos dtype={"Customer ID": str} para evitar que lo lea como float
    df = pd.read_csv(
        filepath, 
        encoding="latin1", 
        low_memory=False,
        dtype={"Customer ID": str}
    )
    
    print(f"   Filas cargadas: {len(df):,}")
    return df


# ─────────────────────────────────────────────
# 2. LIMPIEZA
# ─────────────────────────────────────────────
def clean(df: pd.DataFrame) -> pd.DataFrame:
    print("\n Limpieza...")
    initial = len(df)

    # 2.1 Estandarizar nombres de columnas
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(" ", "_")
    )
    # Renombrar para consistencia
    df = df.rename(columns={
        "invoice":     "invoice_id",
        "stockcode":   "stock_code",
        "description": "description",
        "quantity":    "quantity",
        "invoicedate": "invoice_date",
        "price":       "unit_price",
        "customer id": "customer_id",
        "country":     "country",
    })

    # 2.2 Eliminar duplicados exactos
    df = df.drop_duplicates()
    print(f"   Duplicados eliminados: {initial - len(df):,}")

    # 2.3 Eliminar devoluciones (invoice_id empieza con 'C')
    devoluciones = df["invoice_id"].astype(str).str.startswith("C")
    df = df[~devoluciones]
    print(f"   Devoluciones excluidas: {devoluciones.sum():,}")

    # 2.4 Eliminar filas sin customer_id (no identificable)
    before = len(df)
    df = df.dropna(subset=["customer_id"])
    print(f"   Filas sin customer_id eliminadas: {before - len(df):,}")

    # 2.5 Filtrar registros con quantity o precio inválidos
    df = df[(df["quantity"] > 0) & (df["unit_price"] > 0)]

    # 2.6 Eliminar outliers extremos en quantity y unit_price
    #     Usamos IQR × 3 para ser conservadores (datos de negocio tienen rangos amplios)
    for col in ["quantity", "unit_price"]:
        q1, q3 = df[col].quantile([0.25, 0.75])
        iqr = q3 - q1
        upper = q3 + 3 * iqr
        before = len(df)
        df = df[df[col] <= upper]
        print(f"   Outliers en {col}: {before - len(df):,} filas eliminadas")

    # 2.7 Convertir invoice_date a datetime
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])

    print(f"   Filas limpias: {len(df):,}")
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# 3. NORMALIZACIÓN
# ─────────────────────────────────────────────
def normalize(df: pd.DataFrame) -> pd.DataFrame:
    print("\n Normalización...")

    # 3.1 Texto: quitar espacios y capitalizar descripción
    df["description"] = (
        df["description"]
        .fillna("UNKNOWN")
        .str.strip()
        .str.upper()
    )

    # 3.2 stock_code: strip y mayúsculas
    df["stock_code"] = df["stock_code"].str.strip().str.upper()

    # 3.3 Eliminar stock_codes de ajustes internos (no son productos reales)
    codigos_internos = {"POST", "D", "M", "BANK CHARGES", "PADS", "DOT", "CRUK"}
    df = df[~df["stock_code"].isin(codigos_internos)]

    # 3.4 Normalizar países: quitar variantes con espacios
    df["country"] = df["country"].str.strip().str.title()

    # 3.5 customer_id limpio
    df["customer_id"] = df["customer_id"].str.strip()

    print(f"   Normalización completa. Filas: {len(df):,}")
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# 4. FEATURE ENGINEERING
# ─────────────────────────────────────────────
# Margen estimado: en datasets públicos no hay costo directo.
# Aproximamos costo con un margen bruto del 40% (sector retail UK promedio).
# Documentar este supuesto es CLAVE para el CV y para el dashboard.
MARGEN_BRUTO_ESTIMADO = 0.40

def feature_engineering(df: pd.DataFrame) -> pd.DataFrame:
    print("\n  Feature engineering...")

    # 4.1 Revenue e ingresos
    df["revenue"]    = df["quantity"] * df["unit_price"]
    df["cogs"]       = df["revenue"] * (1 - MARGEN_BRUTO_ESTIMADO)   # costo estimado
    df["gross_profit"] = df["revenue"] - df["cogs"]
    df["gross_margin_pct"] = MARGEN_BRUTO_ESTIMADO  # constante aquí, variable en DAX

    # 4.2 Columnas de fecha para la dimensión temporal
    df["year"]    = df["invoice_date"].dt.year
    df["month"]   = df["invoice_date"].dt.month
    df["week"]    = df["invoice_date"].dt.isocalendar().week.astype(int)
    df["quarter"] = df["invoice_date"].dt.quarter
    df["day_of_week"] = df["invoice_date"].dt.day_name()
    df["date"]    = df["invoice_date"].dt.date          # solo fecha, sin hora

    # 4.3 Ticket promedio por factura (se calculará también en DAX, pero útil en exploración)
    ticket_por_factura = (
        df.groupby("invoice_id")["revenue"].sum().rename("ticket_value")
    )
    df = df.merge(ticket_por_factura, on="invoice_id", how="left")

    print(f"   Features creadas: revenue, cogs, gross_profit, columnas de fecha, ticket_value")
    return df


# ─────────────────────────────────────────────
# 5. CONSTRUCCIÓN DEL MODELO ESTRELLA
# ─────────────────────────────────────────────
def build_star_schema(df: pd.DataFrame) -> dict:
    print("\n Construyendo modelo estrella...")

    # ── dim_producto ──────────────────────────
    dim_producto = (
        df[["stock_code", "description"]]
        .drop_duplicates(subset=["stock_code"])
        .reset_index(drop=True)
    )
    dim_producto.insert(0, "producto_key", dim_producto.index + 1)

    # ── dim_cliente ───────────────────────────
    dim_cliente = (
        df[["customer_id", "country"]]
        .drop_duplicates(subset=["customer_id"])
        .reset_index(drop=True)
    )
    dim_cliente.insert(0, "cliente_key", dim_cliente.index + 1)

    # ── dim_fecha ─────────────────────────────
    dim_fecha = (
        df[["date", "year", "month", "quarter", "week", "day_of_week"]]
        .drop_duplicates(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    dim_fecha.insert(0, "fecha_key", dim_fecha.index + 1)
    # Agregar nombre de mes para Power BI
    dim_fecha["month_name"] = pd.to_datetime(dim_fecha["date"]).dt.strftime("%B")

    # ── dim_region ────────────────────────────
    dim_region = (
        df[["country"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    dim_region.insert(0, "region_key", dim_region.index + 1)

    # ── fact_ventas ───────────────────────────
    # Unir claves de las dimensiones al hecho
    fact = df.copy()
    fact = fact.merge(dim_producto[["stock_code", "producto_key"]], on="stock_code", how="left")
    fact = fact.merge(dim_cliente[["customer_id", "cliente_key"]], on="customer_id", how="left")
    fact = fact.merge(dim_fecha[["date", "fecha_key"]], on="date", how="left")
    fact = fact.merge(dim_region[["country", "region_key"]], on="country", how="left")

    fact_ventas = fact[[
        "invoice_id",
        "producto_key",
        "cliente_key",
        "fecha_key",
        "region_key",
        "quantity",
        "unit_price",
        "revenue",
        "cogs",
        "gross_profit",
        "gross_margin_pct",
        "ticket_value",
    ]].copy()

    print(f"   dim_producto : {len(dim_producto):,} filas")
    print(f"   dim_cliente  : {len(dim_cliente):,} filas")
    print(f"   dim_fecha    : {len(dim_fecha):,} filas")
    print(f"   dim_region   : {len(dim_region):,} filas")
    print(f"   fact_ventas  : {len(fact_ventas):,} filas")

    return {
        "dim_producto": dim_producto,
        "dim_cliente":  dim_cliente,
        "dim_fecha":    dim_fecha,
        "dim_region":   dim_region,
        "fact_ventas":  fact_ventas,
    }


# ─────────────────────────────────────────────
# 6. EXPORTACIÓN
# ─────────────────────────────────────────────
def export(tables: dict, db_path: str = "retail_dw.db", csv_dir: str = "output_csv"):
    """
    Exporta a SQLite (para Power BI via ODBC o importación directa)
    y a CSV individuales (alternativa si no usas SQLite).
    """
    print(f"\n Exportando a '{db_path}' y CSV en '{csv_dir}/'...")

    # SQLite
    conn = sqlite3.connect(db_path)
    for name, df in tables.items():
        df.to_sql(name, conn, if_exists="replace", index=False)
        print(f"   → {name} guardada en SQLite")
    conn.close()

    # CSVs individuales (útil para importar directo a Power BI)
    os.makedirs(csv_dir, exist_ok=True)
    for name, df in tables.items():
        path = os.path.join(csv_dir, f"{name}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"   → {path}")

    print("\n Exportación completa.")


# ─────────────────────────────────────────────
# 7. REPORTE DE CALIDAD (opcional pero recomendado para el CV)
# ─────────────────────────────────────────────
def quality_report(tables: dict):
    print("\n Reporte de calidad del modelo:")
    fact = tables["fact_ventas"]
    print(f"   Total transacciones   : {len(fact):,}")
    print(f"   Revenue total         : ${fact['revenue'].sum():,.2f}")
    print(f"   Gross profit total    : ${fact['gross_profit'].sum():,.2f}")
    print(f"   Margen bruto promedio : {fact['gross_margin_pct'].mean():.1%}")
    print(f"   Ticket promedio       : ${fact['ticket_value'].mean():,.2f}")
    print(f"   Nulos en fact_ventas  : {fact.isnull().sum().sum()}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    raw      = load_raw(RAW_FILE)
    cleaned  = clean(raw)
    normed   = normalize(cleaned)
    featured = feature_engineering(normed)
    tables   = build_star_schema(featured)
    export(tables)
    quality_report(tables)
    print("\n ETL finalizado. Listo para conectar Power BI.")

"""
ETL - Dashboard de Análisis de Ventas y Rentabilidad
Dataset: Online Retail II (Kaggle/UCI)
Flujo: Descarga → Limpieza → Normalización → Feature Engineering → Exportación SQL

"""

import pandas as pd
import numpy as np
import sqlite3
import os

RAW_FILE = "online_retail_II.csv" 

# ─────────────────────────────────────────────
# 1. CARGA
# ─────────────────────────────────────────────
def load_raw(filepath: str) -> pd.DataFrame:
    """Carga el archivo CSV en un único DataFrame."""
    print("Cargando dataset desde CSV...")
    
    # Agregamos dtype={"Customer ID": str} para evitar que lo lea como float
    df = pd.read_csv(
        filepath, 
        encoding="latin1", 
        low_memory=False,
        dtype={"Customer ID": str}
    )
    
    print(f"   Filas cargadas: {len(df):,}")
    return df


# ─────────────────────────────────────────────
# 2. LIMPIEZA
# ─────────────────────────────────────────────
def clean(df: pd.DataFrame) -> pd.DataFrame:
    print("\n Limpieza...")
    initial = len(df)

    # 2.1 Estandarizar nombres de columnas
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(" ", "_")
    )
    # Renombrar para consistencia
    df = df.rename(columns={
        "invoice":     "invoice_id",
        "stockcode":   "stock_code",
        "description": "description",
        "quantity":    "quantity",
        "invoicedate": "invoice_date",
        "price":       "unit_price",
        "customer id": "customer_id",
        "country":     "country",
    })

    # 2.2 Eliminar duplicados exactos
    df = df.drop_duplicates()
    print(f"   Duplicados eliminados: {initial - len(df):,}")

    # 2.3 Eliminar devoluciones (invoice_id empieza con 'C')
    devoluciones = df["invoice_id"].astype(str).str.startswith("C")
    df = df[~devoluciones]
    print(f"   Devoluciones excluidas: {devoluciones.sum():,}")

    # 2.4 Eliminar filas sin customer_id (no identificable)
    before = len(df)
    df = df.dropna(subset=["customer_id"])
    print(f"   Filas sin customer_id eliminadas: {before - len(df):,}")

    # 2.5 Filtrar registros con quantity o precio inválidos
    df = df[(df["quantity"] > 0) & (df["unit_price"] > 0)]

    # 2.6 Eliminar outliers extremos en quantity y unit_price
    #     Usamos IQR × 3 para ser conservadores (datos de negocio tienen rangos amplios)
    for col in ["quantity", "unit_price"]:
        q1, q3 = df[col].quantile([0.25, 0.75])
        iqr = q3 - q1
        upper = q3 + 3 * iqr
        before = len(df)
        df = df[df[col] <= upper]
        print(f"   Outliers en {col}: {before - len(df):,} filas eliminadas")

    # 2.7 Convertir invoice_date a datetime
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])

    print(f"   Filas limpias: {len(df):,}")
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# 3. NORMALIZACIÓN
# ─────────────────────────────────────────────
def normalize(df: pd.DataFrame) -> pd.DataFrame:
    print("\n Normalización...")

    # 3.1 Texto: quitar espacios y capitalizar descripción
    df["description"] = (
        df["description"]
        .fillna("UNKNOWN")
        .str.strip()
        .str.upper()
    )

    # 3.2 stock_code: strip y mayúsculas
    df["stock_code"] = df["stock_code"].str.strip().str.upper()

    # 3.3 Eliminar stock_codes de ajustes internos (no son productos reales)
    codigos_internos = {"POST", "D", "M", "BANK CHARGES", "PADS", "DOT", "CRUK"}
    df = df[~df["stock_code"].isin(codigos_internos)]

    # 3.4 Normalizar países: quitar variantes con espacios
    df["country"] = df["country"].str.strip().str.title()

    # 3.5 customer_id limpio
    df["customer_id"] = df["customer_id"].str.strip()

    print(f"   Normalización completa. Filas: {len(df):,}")
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# 4. FEATURE ENGINEERING
# ─────────────────────────────────────────────
# Margen estimado: en datasets públicos no hay costo directo.
# Aproximamos costo con un margen bruto del 40% (sector retail UK promedio).
# Documentar este supuesto es CLAVE para el CV y para el dashboard.
MARGEN_BRUTO_ESTIMADO = 0.40

def feature_engineering(df: pd.DataFrame) -> pd.DataFrame:
    print("\n  Feature engineering...")

    # 4.1 Revenue e ingresos
    df["revenue"]    = df["quantity"] * df["unit_price"]
    df["cogs"]       = df["revenue"] * (1 - MARGEN_BRUTO_ESTIMADO)   # costo estimado
    df["gross_profit"] = df["revenue"] - df["cogs"]
    df["gross_margin_pct"] = MARGEN_BRUTO_ESTIMADO  # constante aquí, variable en DAX

    # 4.2 Columnas de fecha para la dimensión temporal
    df["year"]    = df["invoice_date"].dt.year
    df["month"]   = df["invoice_date"].dt.month
    df["week"]    = df["invoice_date"].dt.isocalendar().week.astype(int)
    df["quarter"] = df["invoice_date"].dt.quarter
    df["day_of_week"] = df["invoice_date"].dt.day_name()
    df["date"]    = df["invoice_date"].dt.date          # solo fecha, sin hora

    # 4.3 Ticket promedio por factura (se calculará también en DAX, pero útil en exploración)
    ticket_por_factura = (
        df.groupby("invoice_id")["revenue"].sum().rename("ticket_value")
    )
    df = df.merge(ticket_por_factura, on="invoice_id", how="left")

    print(f"   Features creadas: revenue, cogs, gross_profit, columnas de fecha, ticket_value")
    return df


# ─────────────────────────────────────────────
# 5. CONSTRUCCIÓN DEL MODELO ESTRELLA
# ─────────────────────────────────────────────
def build_star_schema(df: pd.DataFrame) -> dict:
    print("\n Construyendo modelo estrella...")

    # ── dim_producto ──────────────────────────
    dim_producto = (
        df[["stock_code", "description"]]
        .drop_duplicates(subset=["stock_code"])
        .reset_index(drop=True)
    )
    dim_producto.insert(0, "producto_key", dim_producto.index + 1)

    # ── dim_cliente ───────────────────────────
    dim_cliente = (
        df[["customer_id", "country"]]
        .drop_duplicates(subset=["customer_id"])
        .reset_index(drop=True)
    )
    dim_cliente.insert(0, "cliente_key", dim_cliente.index + 1)

    # ── dim_fecha ─────────────────────────────
    dim_fecha = (
        df[["date", "year", "month", "quarter", "week", "day_of_week"]]
        .drop_duplicates(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    dim_fecha.insert(0, "fecha_key", dim_fecha.index + 1)
    # Agregar nombre de mes para Power BI
    dim_fecha["month_name"] = pd.to_datetime(dim_fecha["date"]).dt.strftime("%B")

    # ── dim_region ────────────────────────────
    dim_region = (
        df[["country"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    dim_region.insert(0, "region_key", dim_region.index + 1)

    # ── fact_ventas ───────────────────────────
    # Unir claves de las dimensiones al hecho
    fact = df.copy()
    fact = fact.merge(dim_producto[["stock_code", "producto_key"]], on="stock_code", how="left")
    fact = fact.merge(dim_cliente[["customer_id", "cliente_key"]], on="customer_id", how="left")
    fact = fact.merge(dim_fecha[["date", "fecha_key"]], on="date", how="left")
    fact = fact.merge(dim_region[["country", "region_key"]], on="country", how="left")

    fact_ventas = fact[[
        "invoice_id",
        "producto_key",
        "cliente_key",
        "fecha_key",
        "region_key",
        "quantity",
        "unit_price",
        "revenue",
        "cogs",
        "gross_profit",
        "gross_margin_pct",
        "ticket_value",
    ]].copy()

    print(f"   dim_producto : {len(dim_producto):,} filas")
    print(f"   dim_cliente  : {len(dim_cliente):,} filas")
    print(f"   dim_fecha    : {len(dim_fecha):,} filas")
    print(f"   dim_region   : {len(dim_region):,} filas")
    print(f"   fact_ventas  : {len(fact_ventas):,} filas")

    return {
        "dim_producto": dim_producto,
        "dim_cliente":  dim_cliente,
        "dim_fecha":    dim_fecha,
        "dim_region":   dim_region,
        "fact_ventas":  fact_ventas,
    }


# ─────────────────────────────────────────────
# 6. EXPORTACIÓN
# ─────────────────────────────────────────────
def export(tables: dict, db_path: str = "retail_dw.db", csv_dir: str = "output_csv"):
    """
    Exporta a SQLite (para Power BI via ODBC o importación directa)
    y a CSV individuales (alternativa si no usas SQLite).
    """
    print(f"\n Exportando a '{db_path}' y CSV en '{csv_dir}/'...")

    # SQLite
    conn = sqlite3.connect(db_path)
    for name, df in tables.items():
        df.to_sql(name, conn, if_exists="replace", index=False)
        print(f"   → {name} guardada en SQLite")
    conn.close()

    # CSVs individuales (útil para importar directo a Power BI)
    os.makedirs(csv_dir, exist_ok=True)
    for name, df in tables.items():
        path = os.path.join(csv_dir, f"{name}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"   → {path}")

    print("\n Exportación completa.")


# ─────────────────────────────────────────────
# 7. REPORTE DE CALIDAD (opcional pero recomendado para el CV)
# ─────────────────────────────────────────────
def quality_report(tables: dict):
    print("\n Reporte de calidad del modelo:")
    fact = tables["fact_ventas"]
    print(f"   Total transacciones   : {len(fact):,}")
    print(f"   Revenue total         : ${fact['revenue'].sum():,.2f}")
    print(f"   Gross profit total    : ${fact['gross_profit'].sum():,.2f}")
    print(f"   Margen bruto promedio : {fact['gross_margin_pct'].mean():.1%}")
    print(f"   Ticket promedio       : ${fact['ticket_value'].mean():,.2f}")
    print(f"   Nulos en fact_ventas  : {fact.isnull().sum().sum()}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    raw      = load_raw(RAW_FILE)
    cleaned  = clean(raw)
    normed   = normalize(cleaned)
    featured = feature_engineering(normed)
    tables   = build_star_schema(featured)
    export(tables)
    quality_report(tables)
    print("\n ETL finalizado. Listo para conectar Power BI.")