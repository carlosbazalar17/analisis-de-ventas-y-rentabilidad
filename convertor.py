# ==========================================
# CONVERTIDOR SQLITE (.db) A MYSQL (.sql)
# ==========================================
#
# Requisitos:
# pip install sqlite-utils
#
# Uso:
# python convertir.py
#
# El programa:
# 1. Lee un archivo SQLite (.db)
# 2. Extrae tablas y datos
# 3. Genera un archivo compatible con MySQL
#
# ==========================================

import sqlite3
import re

# ------------------------------------------
# CONFIGURACIÓN
# ------------------------------------------

archivo_db = "retail_dw.db"      # Tu archivo .db
archivo_sql = "retail_dw.sql" # Archivo de salida

# ------------------------------------------
# CONEXIÓN SQLITE
# ------------------------------------------

conn = sqlite3.connect(archivo_db)
cursor = conn.cursor()

# ------------------------------------------
# OBTENER TABLAS
# ------------------------------------------

cursor.execute("""
SELECT name
FROM sqlite_master
WHERE type='table'
AND name NOT LIKE 'sqlite_%';
""")

tablas = cursor.fetchall()

# ------------------------------------------
# CREAR ARCHIVO SQL
# ------------------------------------------

with open(archivo_sql, "w", encoding="utf-8") as f:

    f.write("-- =====================================\n")
    f.write("-- CONVERSIÓN SQLITE A MYSQL\n")
    f.write("-- =====================================\n\n")

    for tabla in tablas:

        nombre_tabla = tabla[0]

        print(f"Procesando tabla: {nombre_tabla}")

        # ----------------------------------
        # OBTENER CREATE TABLE
        # ----------------------------------

        cursor.execute(f"""
        SELECT sql
        FROM sqlite_master
        WHERE type='table'
        AND name='{nombre_tabla}';
        """)

        create_table = cursor.fetchone()[0]

        # ----------------------------------
        # ADAPTAR SINTAXIS A MYSQL
        # ----------------------------------

        create_table = re.sub(
            r'INTEGER PRIMARY KEY AUTOINCREMENT',
            'INT PRIMARY KEY AUTO_INCREMENT',
            create_table,
            flags=re.IGNORECASE
        )

        create_table = re.sub(
            r'INTEGER PRIMARY KEY',
            'INT PRIMARY KEY AUTO_INCREMENT',
            create_table,
            flags=re.IGNORECASE
        )

        create_table = create_table.replace('"', '`')

        # Agregar motor MySQL
        if create_table.strip().endswith(")"):
            create_table += " ENGINE=InnoDB;"

        f.write(create_table + "\n\n")

        # ----------------------------------
        # EXTRAER DATOS
        # ----------------------------------

        cursor.execute(f"SELECT * FROM `{nombre_tabla}`")
        filas = cursor.fetchall()

        # Obtener nombres columnas
        columnas = [desc[0] for desc in cursor.description]

        for fila in filas:

            valores = []

            for valor in fila:

                if valor is None:
                    valores.append("NULL")

                elif isinstance(valor, str):
                    valor = valor.replace("'", "\\'")
                    valores.append(f"'{valor}'")

                else:
                    valores.append(str(valor))

            columnas_sql = ", ".join([f"`{c}`" for c in columnas])
            valores_sql = ", ".join(valores)

            insert_sql = (
                f"INSERT INTO `{nombre_tabla}` "
                f"({columnas_sql}) VALUES ({valores_sql});"
            )

            f.write(insert_sql + "\n")

        f.write("\n\n")

# ------------------------------------------
# CERRAR CONEXIÓN
# ------------------------------------------

conn.close()

print("\n=====================================")
print("CONVERSIÓN COMPLETADA")
print(f"Archivo generado: {archivo_sql}")
print("=====================================")