from bs4 import BeautifulSoup
import requests
import os
import folium
import gpxpy

headers = {
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 YaBrowser/25.12.0.0 Safari/537.36",
}

url = "https://mosregdata.ru/article/files-walking-routes-mo"
response = requests.get(url, headers=headers)

download_folder = "downloaded_gpx"
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

# 🔹 Скачиваем GPX файлы
for i in div:
    href = i.get("href")
    if href and href.endswith(".gpx"):
        base_url = "https://mosregdata.ru"
        full_url = base_url + href
        filename = os.path.join(download_folder, href.split("/")[-1])
        
        # Скачиваем только если файла ещё нет (защита от дублей)
        if not os.path.exists(filename):
            response1 = requests.get(full_url)
            with open(filename, "wb") as f:
                f.write(response1.content)
            print(f"✅ Скачан: {filename}")

# 🔹 Функция: извлекаем координаты и границы трека
def get_gpx_data(file_path):
    """Возвращает список точек [(lat, lon), ...] и границы"""
    with open(file_path, 'r', encoding='utf-8') as f:
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
def create_map_with_track(gpx_path, output_html):
    points, bounds = get_gpx_data(gpx_path)
    
    if not points or not bounds:
        print(f"⚠️ Не удалось обработать {gpx_path}")
        return False
    
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
    return True

# 🔹 Обрабатываем все GPX файлы
for file in os.listdir(download_folder):
    if file.endswith(".gpx"):
        gpx_full_path = os.path.join(download_folder, file)
        output_name = file.replace(".gpx", "_map.html")
        create_map_with_track(gpx_full_path, output_name)