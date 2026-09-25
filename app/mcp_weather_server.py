# -*- coding: utf-8 -*-
"""MCP-сервер «weather»: погода через Open-Meteo (без API-ключа). Транспорт stdio.

Инструменты:
  • get_current_weather(city)   — погода сейчас
  • get_forecast(city, days)    — прогноз по дням
"""

from typing import Annotated

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "сильный ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна",
    80: "ливень", 81: "сильный ливень", 82: "очень сильный ливень", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}

mcp = FastMCP("weather", log_level="WARNING")

City = Annotated[str, Field(description="Название города, например 'Москва' или 'Berlin'")]


async def geocode(city: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GEO_URL, params={"name": city, "count": 1, "language": "ru"})
        r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        raise ValueError(f"Город не найден: {city}")
    g = results[0]
    return {"name": g["name"], "country": g.get("country"), "lat": g["latitude"], "lon": g["longitude"],
            "timezone": g.get("timezone", "auto")}


async def forecast(place: dict, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(FORECAST_URL, params={
            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", **params})
        r.raise_for_status()
    return r.json()


@mcp.tool()
async def get_current_weather(city: City) -> dict:
    """Текущая погода в городе: температура, ощущается как, влажность, ветер, описание."""
    place = await geocode(city)
    data = await forecast(place, {
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"})
    cur = data["current"]
    return {
        "city": place["name"], "country": place["country"], "time": cur["time"],
        "temperature_c": cur["temperature_2m"], "feels_like_c": cur["apparent_temperature"],
        "humidity_pct": cur["relative_humidity_2m"], "wind_kmh": cur["wind_speed_10m"],
        "conditions": WEATHER_CODES.get(cur["weather_code"], f"код {cur['weather_code']}"),
    }


@mcp.tool()
async def get_forecast(
    city: City,
    days: Annotated[int, Field(description="На сколько дней прогноз", ge=1, le=7)] = 3,
) -> dict:
    """Прогноз погоды по дням: мин/макс температура, осадки, описание."""
    place = await geocode(city)
    data = await forecast(place, {
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code", "forecast_days": days})
    d = data["daily"]
    return {
        "city": place["name"], "country": place["country"],
        "days": [{
            "date": d["time"][i], "min_c": d["temperature_2m_min"][i], "max_c": d["temperature_2m_max"][i],
            "precipitation_mm": d["precipitation_sum"][i],
            "conditions": WEATHER_CODES.get(d["weather_code"][i], f"код {d['weather_code'][i]}"),
        } for i in range(len(d["time"]))],
    }


if __name__ == "__main__":
    mcp.run()
