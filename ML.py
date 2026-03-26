from bs4 import BeautifulSoup
import requests
import os
import folium
import gpxpy
import psycopg2
import hashlib
import time

# responce = request.get(ссылка)
# soup = BeautifulSoup(responce.text, "lxml")
# with open("index.html", "w", encode = "utf-8") as file:
#   file.write(responce.prettify())

headers = {
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 YaBrowser/25.12.0.0 Safari/537.36",
}

url = "https://mosregdata.ru/article/files-walking-routes-mo"
response = requests.get(url, headers=headers)

config = {
    'host': 'localhost',
    'port': 5432,
    'database': 'routes_db',
    'user': 'postgres',
    'password': ''
}

def connection_db():
    conn = psycopg2.connect(**config) # **  <-- Распаковывают dict в именованный аргумет. Т. Е. host = localhost, port = 5432 и тд
    return conn

def get_file_hash(filepath):
    """Возвращает MD5-хеш файла"""
    hash_md5 = hashlib.md5()

    # открывает файл не целиком, чтобы большие файлы долго не грузить
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def init_db():

    try:

        conn = connection_db()
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS routes (
            id serial PRIMARY KEY,
            filename VARCHAR(255) NOT NULL UNIQUE,
            source_url TEXT UNIQUE,
            file_path VARCHAR(255),
            center_lat DOUBLE PRECISION,
            center_lon DOUBLE PRECISION,
            file_hash VARCHAR(64),
            last_checked TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS routes_point (
            id serial PRIMARY KEY,
            id_routes INTEGER REFERENCES routes(id) ON DELETE CASCADE,
            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            point_order INTEGER NOT NULL,
            UNIQUE (id_routes, point_order)
            )
            """
        )
        
        cur.execute(
            """CREATE TABLE IF NOT EXISTS maps (
            id serial PRIMARY KEY,
            id_routes INTEGER REFERENCES routes(id) ON DELETE CASCADE UNIQUE,
            path_map VARCHAR(500) NOT NULL
            )"""
        )

        conn.commit()

    except Exception as ex:
        print(f"[WARNING] При работе с БД произошла ошибка: {ex}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

download_folder = "downloaded_gpx"
init_db()

if not os.path.exists(download_folder):
    os.makedirs(download_folder)

# Если index.html ещё нет — скачиваем и сохраняем
if not os.path.exists("index.html"):
    soup = BeautifulSoup(response.text, "lxml")
    with open("index.html", "w", encoding="utf8") as file:
        file.write(soup.prettify())
else:
    with open("index.html", "r", encoding="utf8") as file:
        src = file.read()
    soup = BeautifulSoup(src, "lxml")

div = soup.find("div", class_="col-lg-9 fs-5").find_all("a")

# Перебираем ссылки
for i in div:
    href = i.get("href")
    if href and href.endswith(".gpx"):
        base_url = "https://mosregdata.ru" # Из за того, что ссылка относительная нужно дописать это к ссылке, чтобы можно было по ней перейти
        full_url = base_url + href
        filename = os.path.join(download_folder, href.split("/")[-1])
        
        # Скачиваем только если файла ещё нет (защита от дублей) ИЛИ он старше 24 часов (проверка актуальности)
        should_download = not os.path.exists(filename)
        if os.path.exists(filename) and not should_download:

            # Прошло ли 24 часа

            file_mtime = os.path.getmtime(filename)
            if time.time() - file_mtime > 86400:  # 24 часа
                should_download = True
        if should_download:
            response1 = requests.get(full_url)
            with open(filename, "wb") as f:
                f.write(response1.content)
            print(f"✅ Скачан: {filename}")

        try:
            conn = connection_db()
            cur = conn.cursor()
            
            # Вычисляем хеш скачанного файла
            file_hash = get_file_hash(filename)

            # UPSERT: если запись есть — обновляем хеш и дату проверки
            cur.execute("""
                INSERT INTO routes (filename, source_url, file_path, file_hash, last_checked)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (filename) 
                DO UPDATE SET 
                    file_hash = EXCLUDED.file_hash,
                    last_checked = NOW(),
                    is_active = TRUE
                RETURNING id
            """, (os.path.basename(filename), full_url, filename, file_hash))
            
            result = cur.fetchone()
            
            # Если запись новая — получили id
            if result:
                route_id = result[0]
                print(f"📦 Новая запись: route_id = {route_id}")
            else:
                # Запись уже была — получаем её id отдельно
                cur.execute("SELECT id FROM routes WHERE filename = %s", 
                           (os.path.basename(filename),))
                route_id = cur.fetchone()[0]
                print(f"📦 Уже в БД: route_id = {route_id}")
            
            conn.commit()
            
        except Exception as ex:
            print(f"[WARNING] Ошибка БД: {ex}")
            route_id = None
        finally:    
            if cur: cur.close()
            if conn: conn.close()
            
# 🔹 Функция: извлекаем координаты и границы трека
def get_gpx_data(file_path, route_id = None):
    # Если route_id передан — проверяем, нужно ли обновлять точки
    if route_id:
        conn = connection_db()
        cur = conn.cursor()
        cur.execute("SELECT file_hash FROM routes WHERE id = %s", (route_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result:
            stored_hash = result[0]
            current_hash = get_file_hash(file_path)
            # Если хеш не изменился — точки уже актуальны, можно пропустить запись
            if stored_hash == current_hash:
                print(f"⏭️ Точки для {os.path.basename(file_path)} уже актуальны")
                # Но всё равно вернём точки для построения карты!
                
    """Возвращает список точек [(lat, lon), ...] и границы"""
    with open(file_path, 'r', encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points.append((point.latitude, point.longitude))
    
    if not points:
        return None, None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    
    conn = connection_db()
    cur = conn.cursor()
    
    # 🔹 Сохраняем точки в БД, если передан route_id
    if route_id and points:
        
        # Простой цикл: берём каждую точку и сохраняем
        # Проходим по всем точкам
        for idx, (lat, lon) in enumerate(points): # idx - индекс; (lat, lon) - кортеж с координатами 
            cur.execute(
                """INSERT INTO routes_point (id_routes, latitude, longitude, point_order)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (id_routes, point_order) DO UPDATE SET latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude""",
                (route_id, lat, lon, idx)
            )
            # commit можно делать после цикла — так быстрее
            # но если точек очень много, можно сохранять каждые 100
        
        conn.commit()  # подтверждаем все изменения сразу
        cur.close()
        conn.close()
        print(f"📍 Сохранено точек: {len(points)}")

    bounds = {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "center_lat": sum(lats) / len(lats),
        "center_lon": sum(lons) / len(lons)      
    }
    return points, bounds

# 🔹 Функция: создаём карту с треком через Folium
def create_map_with_track(gpx_path, output_html, route_id):
    points, bounds = get_gpx_data(gpx_path, route_id)

    if not points or not bounds:
        print(f"⚠️ Не удалось обработать {gpx_path}")
        return False
    
    center_lat = bounds["center_lat"]
    center_lon = bounds["center_lon"]
    
    # Создаём карту, центрируем на треке
    m = folium.Map(
        location=[bounds["center_lat"], bounds["center_lon"]],
        zoom_start=13,
        tiles="OpenStreetMap"  # бесплатный источник тайлов
    )
    
    # Добавляем трек как красную линию
    folium.PolyLine(
        locations=points,
        color="red",
        weight=4,
        opacity=0.9,
        tooltip="Маршрут"
    ).add_to(m)
    
    # Добавляем маркер начала и конца
    folium.Marker(points[0], icon=folium.Icon(color="green", icon="play"), tooltip="Старт").add_to(m)
    folium.Marker(points[-1], icon=folium.Icon(color="red", icon="stop"), tooltip="Финиш").add_to(m)
    
    # Сохраняем как HTML (интерактивная карта)
    map_folder = "map_folder"
    if not os.path.exists(map_folder):
        os.makedirs(map_folder)
    
    output_path = os.path.join(map_folder, output_html)
    m.save(output_path)
    print(f"✅ Карта сохранена: {output_path}")

     # 🔹 Сохраняем путь к карте в БД
    if route_id:
        conn = connection_db()
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO maps (id_routes, path_map)
        VALUES (%s, %s)
        ON CONFLICT (id_routes) DO NOTHING
        """, (route_id, output_path))
        conn.commit()

        cur.execute("""
            UPDATE routes 
            SET center_lat = %s, center_lon = %s 
            WHERE id = %s
            """, (center_lat, center_lon, route_id))  # ← route_id обязателен!
        conn.commit()
        cur.close()
        conn.close()
        print(f"🗺️ Карта записана в БД")
    
    return True

# 🔹 Обрабатываем все GPX файлы
for file in os.listdir(download_folder):
    if file.endswith(".gpx"):
        gpx_full_path = os.path.join(download_folder, file)
        output_name = file.replace(".gpx", "_map.html")

        # 🔹 Получаем route_id из БД по имени файла
        conn = connection_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM routes WHERE filename = %s", (file,))
        result = cur.fetchone()
        route_id = result[0] if result else None
        cur.close()
        conn.close()

        if route_id is None:
            print(f"⚠️ Пропущено: {file} нет в БД")
            continue

        create_map_with_track(gpx_full_path, output_name, route_id)