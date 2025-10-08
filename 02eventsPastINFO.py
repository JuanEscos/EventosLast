#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FLOWAGILITY SCRAPER - SOLO EVENTOS (01events)
- Login
- Scroll completo y parseo estático con BeautifulSoup
- Salida: 01events.json y 01events_YYYY-MM-DD.json en OUT_DIR
"""

import os
import sys
import json
import re
import time
import traceback
import unicodedata
import random
from datetime import datetime
from urllib.parse import urljoin
from pathlib import Path

# Third-party imports
try:
    from bs4 import BeautifulSoup
    from dotenv import load_dotenv
except ImportError as e:
    print(f"❌ Error importando dependencias: {e}")
    sys.exit(1)

# Selenium imports
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.chrome.service import Service
    HAS_SELENIUM = True
except ImportError as e:
    print(f"❌ Error importando Selenium: {e}")
    HAS_SELENIUM = False

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WEBDRIVER_MANAGER = True
except ImportError:
    HAS_WEBDRIVER_MANAGER = False

# ============================== CONFIGURACIÓN GLOBAL ==============================

BASE = "https://www.flowagility.com"
EVENTS_URL = f"{BASE}/zone/events"
SCRIPT_DIR = Path(__file__).resolve().parent

# Cargar variables de entorno
try:
    load_dotenv(SCRIPT_DIR / ".env")
    print("✅ Variables de entorno cargadas")
except Exception as e:
    print(f"❌ Error cargando .env: {e}")

# Credenciales
FLOW_EMAIL = os.getenv("FLOW_EMAIL", "")
FLOW_PASS  = os.getenv("FLOW_PASS",  "")

# Flags/tunables
HEADLESS       = os.getenv("HEADLESS", "true").lower() == "true"
INCOGNITO      = os.getenv("INCOGNITO", "true").lower() == "true"
MAX_SCROLLS    = int(os.getenv("MAX_SCROLLS", "15"))
SCROLL_WAIT_S  = float(os.getenv("SCROLL_WAIT_S", "3.0"))
OUT_DIR        = os.getenv("OUT_DIR", "./output")

print(f"📋 Configuración: HEADLESS={HEADLESS}, OUT_DIR={OUT_DIR}")

# ============================== UTILIDADES ==============================

def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def slow_pause(min_s=1, max_s=2):
    time.sleep(random.uniform(min_s, max_s))

def _clean(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip(" \t\r\n-•*·:;")

def _get_driver(headless=True):
    """Driver preparado para CI: implicit wait bajo y page_load moderado."""
    if not HAS_SELENIUM:
        raise ImportError("Selenium no está instalado")

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    if INCOGNITO:
        opts.add_argument("--incognito")
    # Estables en CI
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-browser-side-navigation")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--disable-setuid-sandbox")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--window-size=1920,1080")
    ua = os.getenv("CHROME_UA", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    opts.add_argument(f"--user-agent={ua}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)

    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        opts.binary_location = chrome_bin

    try:
        chromedriver_path = None
        for path in ["/usr/local/bin/chromedriver", "/usr/bin/chromedriver", "/snap/bin/chromedriver"]:
            if os.path.exists(path):
                chromedriver_path = path
                break
        if not chromedriver_path and HAS_WEBDRIVER_MANAGER:
            chromedriver_path = ChromeDriverManager().install()

        if chromedriver_path:
            service = Service(executable_path=chromedriver_path)
            driver = webdriver.Chrome(service=service, options=opts)
        else:
            driver = webdriver.Chrome(options=opts)

        # Anti-detección básica
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # Timeouts
        driver.set_page_load_timeout(75)
        driver.implicitly_wait(2)
        return driver
    except Exception as e:
        log(f"Error creando driver: {e}")
        traceback.print_exc()
        return None

def _login(driver):
    """Login clásico, con varios selectores."""
    if not driver:
        return False

    log("Iniciando login…")
    try:
        driver.get(f"{BASE}/user/login")
        WebDriverWait(driver, 45).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        slow_pause(2, 4)

        # Si ya estamos dentro
        if "/user/login" not in driver.current_url:
            log("Ya autenticado (redirección detectada)")
            return True

        email_selectors = [
            (By.NAME, "user[email]"),
            (By.ID, "user_email"),
            (By.CSS_SELECTOR, "input[type='email']"),
            (By.XPATH, "//input[contains(@name, 'email')]"),
        ]
        password_selectors = [
            (By.NAME, "user[password]"),
            (By.ID, "user_password"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ]
        submit_selectors = [
            (By.CSS_SELECTOR, 'button[type="submit"]'),
            (By.XPATH, "//button[contains(text(), 'Sign') or contains(text(), 'Log') or contains(text(), 'Iniciar')]"),
        ]

        email_field = None
        for sel in email_selectors:
            try:
                email_field = WebDriverWait(driver, 10).until(EC.element_to_be_clickable(sel))
                break
            except Exception:
                continue
        if not email_field:
            log("❌ No se pudo encontrar campo email"); return False

        password_field = None
        for sel in password_selectors:
            try:
                password_field = driver.find_element(*sel)
                break
            except Exception:
                continue
        if not password_field:
            log("❌ No se pudo encontrar campo password"); return False

        submit_button = None
        for sel in submit_selectors:
            try:
                submit_button = driver.find_element(*sel)
                break
            except Exception:
                continue
        if not submit_button:
            log("❌ No se pudo encontrar botón submit"); return False

        if not FLOW_EMAIL or not FLOW_PASS:
            log("❌ Faltan credenciales FLOW_EMAIL/FLOW_PASS"); return False

        email_field.clear(); email_field.send_keys(FLOW_EMAIL); slow_pause(1, 2)
        password_field.clear(); password_field.send_keys(FLOW_PASS); slow_pause(1, 2)
        submit_button.click()

        try:
            WebDriverWait(driver, 40).until(
                lambda d: "/user/login" not in d.current_url or "dashboard" in d.current_url or "zone" in d.current_url
            )
            slow_pause(3, 5)
            if "/user/login" in driver.current_url:
                log("❌ Login falló - aún en página de login")
                return False
            log(f"✅ Login exitoso - {driver.current_url}")
            return True
        except TimeoutException:
            log("❌ Timeout esperando redirección de login")
            return False

    except Exception as e:
        log(f"❌ Error en login: {e}")
        return False

def _accept_cookies(driver):
    try:
        cookie_selectors = [
            'button[aria-label="Accept all"]',
            'button[aria-label="Aceptar todo"]',
            '[data-testid="uc-accept-all-button"]',
            'button[mode="primary"]',
        ]
        for selector in cookie_selectors:
            try:
                btns = driver.find_elements(By.CSS_SELECTOR, selector)
                if btns:
                    btns[0].click()
                    slow_pause(0.4, 0.8)
                    log("Cookies aceptadas")
                    return True
            except Exception:
                continue
        # Fallback por JS
        driver.execute_script("""
            const bs = document.querySelectorAll('button');
            for (const b of bs) { if (/aceptar|accept|consent|agree/i.test(b.textContent)) { b.click(); break; } }
        """)
        slow_pause(0.3, 0.6)
        return True
    except Exception as e:
        log(f"Error manejando cookies: {e}")
        return False

def _full_scroll(driver):
    last_h = driver.execute_script("return document.body.scrollHeight")
    for _ in range(MAX_SCROLLS):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_WAIT_S)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h

# ============================== EXTRACCIÓN DE EVENTOS ==============================

def extract_events():
    if not HAS_SELENIUM:
        log("Error: Selenium no está instalado"); return None

    log("=== EXTRACCIÓN DE EVENTOS (01events_past) ===")
    driver = _get_driver(headless=HEADLESS)
    if not driver:
        log("❌ No se pudo crear el driver de Chrome"); return None

    try:
        if not _login(driver):
            raise Exception("No se pudo iniciar sesión")

        log("Navegando a la página de eventos…")
        driver.get(EVENTS_URL)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        _accept_cookies(driver)

        log("Cargando todos los eventos…")
        _full_scroll(driver)
        slow_pause(1.5, 2.5)

        page_html = driver.page_source
        soup = BeautifulSoup(page_html, 'html.parser')

        event_containers = soup.find_all('div', class_='group mb-6')
        log(f"Encontrados {len(event_containers)} contenedores de eventos")

        events = []
        for i, c in enumerate(event_containers, 1):
            try:
                ev = {}
                event_id = c.get('id', '')
                if event_id:
                    ev['id'] = event_id.replace('event-card-', '')

                name_elem = c.find('div', class_='font-caption text-lg text-black truncate -mt-1')
                if name_elem:
                    ev['nombre'] = _clean(name_elem.get_text())

                date_elem = c.find('div', class_='text-xs')
                if date_elem:
                    ev['fechas'] = _clean(date_elem.get_text())

                org_elems = c.find_all('div', class_='text-xs')
                if len(org_elems) > 1:
                    ev['organizacion'] = _clean(org_elems[1].get_text())

                club_elem = c.find('div', class_='text-xs mb-0.5 mt-0.5')
                if club_elem:
                    ev['club'] = _clean(club_elem.get_text())
                else:
                    for d in c.find_all('div', class_='text-xs'):
                        t = _clean(d.get_text())
                        if t and not any(x in t for x in ['/', 'Spain', 'España']):
                            ev['club'] = t; break

                location_divs = c.find_all('div', class_='text-xs')
                for d in location_divs:
                    t = _clean(d.get_text())
                    if '/' in t and any(x in t for x in ['Spain', 'España', 'Madrid', 'Barcelona']):
                        ev['lugar'] = t; break
                if 'lugar' not in ev:
                    for d in location_divs:
                        t = _clean(d.get_text())
                        if '/' in t and len(t) < 100:
                            ev['lugar'] = t; break

                ev['enlaces'] = {}
                info_link = c.find('a', href=lambda x: x and '/info/' in x)
                if info_link:
                    ev['enlaces']['info'] = urljoin(BASE, info_link['href'])

                participant_links = c.find_all('a', href=lambda x: x and any(term in x for term in ['/participants', '/participantes']))
                for lk in participant_links:
                    href = lk.get('href', '')
                    if '/participants_list' in href or '/participantes' in href:
                        ev['enlaces']['participantes'] = urljoin(BASE, href); break
                if 'participantes' not in ev['enlaces'] and 'id' in ev:
                    ev['enlaces']['participantes'] = f"{BASE}/zone/events/{ev['id']}/participants_list"

                flag_elem = c.find('div', class_='text-md')
                ev['pais_bandera'] = _clean(flag_elem.get_text()) if flag_elem else '🇪🇸'

                events.append(ev)
                log(f"✅ Evento {i} procesado: {ev.get('nombre', 'Sin nombre')}")
            except Exception as e:
                log(f"❌ Error procesando evento {i}: {e}")
                continue

        today_str = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, f'01events_past{today_str}.json'), 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        with open(os.path.join(OUT_DIR, '01events_past.json'), 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

        log(f"✅ Extracción completada. {len(events)} eventos guardados")
        return events

    except Exception as e:
        log(f"❌ Error durante la extracción de eventos: {e}")
        traceback.print_exc()
        return None
    finally:
        try:
            driver.quit(); log("Navegador cerrado")
        except:
            pass

# ============================== MAIN ==============================

def main():
    print("🚀 INICIANDO FLOWAGILITY - SOLO EVENTOS")
    print(f"📂 Directorio de salida: {OUT_DIR}")
    print("=" * 80)

    os.makedirs(OUT_DIR, exist_ok=True)

    try:
        events = extract_events()
        ok = bool(events)
        if ok:
            print(f"\n📁 ARCHIVOS GENERADOS EN {OUT_DIR}:")
            for name in sorted(os.listdir(OUT_DIR)):
                path = os.path.join(OUT_DIR, name)
                if os.path.isfile(path) and name.startswith("01events_past"):
                    print(f"   {name} - {os.path.getsize(path)} bytes")
        return ok
    except Exception as e:
        log(f"❌ ERROR CRÍTICO: {e}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
