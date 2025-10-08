#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FLOWAGILITY SCRAPER - EVENTOS + INFO DETALLADA (con participantes LiveView)
- Detección rápida de estados (ok/empty/login/timeout)
- Conteo en DOM vivo (JS) + fallback HTML (BeautifulSoup)
- Micro-scroll para disparar cargas perezosas
- Reaceptación de cookies en página de participantes
- Timeouts: por página, por evento y global
- Implicit wait MUY bajo (evita cuelgues silenciosos)
"""

import os
import sys
import json
import re
import time
import argparse
import traceback
import unicodedata
import random
from datetime import datetime
from urllib.parse import urljoin
from pathlib import Path
from glob import glob

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
FLOW_EMAIL = os.getenv("FLOW_EMAIL", "rosaperez1134@yahoo.com")
FLOW_PASS  = os.getenv("FLOW_PASS",  "Seattle1")

# Flags/tunables
HEADLESS       = os.getenv("HEADLESS", "true").lower() == "true"
INCOGNITO      = os.getenv("INCOGNITO", "true").lower() == "true"
MAX_SCROLLS    = int(os.getenv("MAX_SCROLLS", "15"))
SCROLL_WAIT_S  = float(os.getenv("SCROLL_WAIT_S", "3.0"))
OUT_DIR        = os.getenv("OUT_DIR", "./output")
LIMIT_EVENTS   = int(os.getenv("LIMIT_EVENTS", "0"))   # 0 = sin límite

# Budgets/tiempos (ajustables por ENV)
PER_EVENT_MAX_S      = int(os.getenv("PER_EVENT_MAX_S", "180"))  # límite por evento
PER_PAGE_MAX_S       = int(os.getenv("PER_PAGE_MAX_S",  "35"))   # espera máx por página de participantes
LIVEVIEW_READY_MAX_S = int(os.getenv("LIVEVIEW_READY_MAX_S", "12"))
MAX_RUNTIME_MIN      = int(os.getenv("MAX_RUNTIME_MIN", "0"))    # 0 = sin límite global

print(f"📋 Configuración: HEADLESS={HEADLESS}, OUT_DIR={OUT_DIR}")

# ============================== UTILIDADES GENERALES ==============================

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

def _clean_output_directory():
    try:
        files_to_keep = ['config.json', 'settings.ini']
        os.makedirs(OUT_DIR, exist_ok=True)
        for file in os.listdir(OUT_DIR):
            if file not in files_to_keep:
                file_path = os.path.join(OUT_DIR, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    log(f"🧹 Eliminado archivo antiguo: {file}")
        log("✅ Directorio de output limpiado")
    except Exception as e:
        log(f"⚠️  Error limpiando directorio: {e}")

# ====== helpers tiempo ======
def _now():
    return time.time()

def _deadline(sec_from_now):
    return _now() + max(0, sec_from_now)

def _time_left(deadline):
    return max(0.0, deadline - _now())

# ============================== NAVEGACIÓN / DRIVER ==============================

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

        # Timeouts: explícitos + implicit MUY BAJO (evita micro-cuelgues)
        driver.set_page_load_timeout(75)
        driver.implicitly_wait(2)  # 💡 clave para no bloquear cada find_*
        return driver
    except Exception as e:
        log(f"Error creando driver: {e}")
        traceback.print_exc()
        return None

def _login(driver):
    """Login clásico, con varios selectores."""
    if not driver:
        return False

    log("Iniciando login...")
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

# ============================== MÓDULO 1: EVENTOS ==============================

def extract_events():
    if not HAS_SELENIUM:
        log("Error: Selenium no está instalado"); return None

    log("=== MÓDULO 1: EXTRACCIÓN DE EVENTOS BÁSICOS ===")
    driver = _get_driver(headless=HEADLESS)
    if not driver:
        log("❌ No se pudo crear el driver de Chrome"); return None

    try:
        if not _login(driver):
            raise Exception("No se pudo iniciar sesión")

        log("Navegando a la página de eventos...")
        driver.get(EVENTS_URL)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        _accept_cookies(driver)

        log("Cargando todos los eventos...")
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
        with open(os.path.join(OUT_DIR, f'01events_past_{today_str}.json'), 'w', encoding='utf-8') as f:
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
        try: driver.quit(); log("Navegador cerrado")
        except: pass

# ============================== MÓDULO 2: INFO DETALLADA ==============================

def _extract_description(soup, max_length=800):
    try:
        selectors = ['div[class*="description"]','div[class*="descripcion"]','div[class*="info"]','div[class*="content"]','div[class*="text"]','div[class*="body"]']
        txt = ""
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                t = _clean(el.get_text())
                if t and len(t) > 50:
                    txt = t; break
        if not txt:
            all_text = soup.get_text()
            lines = [ln.strip() for ln in all_text.split("\n") if len(ln.strip()) > 50]
            if lines: txt = " ".join(lines[:3])
        if txt and len(txt) > max_length:
            txt = txt[:max_length] + "... [texto truncado]"
        return txt
    except Exception as e:
        log(f"Error extrayendo descripción: {e}")
        return ""

def _wait_state_participants_page(driver, timeout_s):
    """
    Devuelve: "login" | "ok" | "empty" | "timeout"
    - 'ok' si detectamos nodos/IDs de participantes (sin exigir phx-connected).
    - 'empty' si hay texto típico de vacío.
    - 'login' si redirige a /user/login.
    """
    t_end = _deadline(timeout_s)
    did_scroll = False
    while _now() < t_end:
        url = (driver.current_url or "")
        if "/user/login" in url:
            return "login"

        # 1) Conteo rápido en DOM vivo (sin depender de phx-connected)
        try:
            cnt = driver.execute_script("""
                const qs = document.querySelectorAll(
                  '[phx-value-booking_id],'+
                  '[phx-value-booking-id],'+
                  '[data-phx-value-booking_id],'+
                  '[data-phx-value-booking-id],'+
                  '[phx-click*="booking_details"],'+
                  '[data-phx-click*="booking_details"],'+
                  '[id^="booking-"],[id^="booking_"]'
                );
                return qs ? qs.length : 0;
            """) or 0
            if int(cnt) > 0:
                return "ok"
        except Exception:
            pass

        # 2) Texto que indica vacío
        try:
            body_txt = driver.find_element(By.TAG_NAME, "body").text.lower()
            if re.search(r"no hay|sin participantes|no results|0 participantes|no participants", body_txt):
                return "empty"
        except Exception:
            pass

        # 3) Micro-scroll para disparar lazy/hydrate
        if not did_scroll:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.25)
                driver.execute_script("window.scrollTo(0, 0);")
                did_scroll = True
            except Exception:
                pass

        time.sleep(0.25)
    return "timeout"

def _count_participants_fast(driver) -> int:
    """Cuenta participantes desde el DOM vivo; acepta variantes con guion/underscore y contains en phx-click."""
    try:
        result = driver.execute_script("""
            const set = new Set();
            const nodes = document.querySelectorAll(
              '[phx-value-booking_id],'+
              '[phx-value-booking-id],'+
              '[data-phx-value-booking_id],'+
              '[data-phx-value-booking-id],'+
              '[phx-click*="booking_details"],'+
              '[data-phx-click*="booking_details"],'+
              '[id^="booking-"],[id^="booking_"]'
            );
            for (const n of nodes) {
              const v = n.getAttribute('phx-value-booking_id')
                     || n.getAttribute('phx-value-booking-id')
                     || n.getAttribute('data-phx-value-booking_id')
                     || n.getAttribute('data-phx-value-booking-id')
                     || n.id || '';
              if (v) set.add(v);
            }
            return set.size || nodes.length || 0;
        """) or 0
        if result and int(result) > 0:
            return int(result)
    except Exception:
        pass
    return 0

def _count_participants_from_html(html: str) -> int:
    """Fallback con BeautifulSoup sobre el DOM actual."""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        # A) toggles con booking_id
        elems = soup.find_all(attrs={'phx-value-booking_id': True}) \
              + soup.find_all(attrs={'phx-value-booking-id': True})
        if elems:
            return len(elems)
        # B) phx-click que contenga booking_details
        elems = soup.find_all(attrs={'phx-click': re.compile(r'booking_details')}) \
              + soup.find_all(attrs={'data-phx-click': re.compile(r'booking_details')})
        if elems:
            return len(elems)
        # C) tablas
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) > 1:
                hdr = rows[0].get_text(" ").lower()
                if any(k in hdr for k in ["dorsal","guía","guia","perro","nombre"]):
                    return max(0, len(rows)-1)
                if 5 <= len(rows) <= 2000:
                    return len(rows)-1
        # D) número en texto
        txt = soup.get_text(" ").lower()
        m = re.search(r"(\d+)\s*(participantes?|inscritos?|competidores?)", txt)
        if m:
            n = int(m.group(1))
            if 0 <= n <= 5000:
                return n
    except Exception:
        pass
    return 0

def extract_detailed_info():
    """Extraer info detallada incluyendo número de participantes (rápido y con límites)."""
    if not HAS_SELENIUM:
        log("Error: Selenium no está instalado"); return None

    log("=== MÓDULO 2: EXTRACCIÓN DE INFORMACIÓN DETALLADA ===")

    # Archivo de eventos más reciente
    event_files = glob(os.path.join(OUT_DIR, "01events_past_*.json"))
    if not event_files:
        log("❌ No se encontraron archivos de eventos"); return None
    latest = max(event_files, key=os.path.getctime)
    events = json.load(open(latest, "r", encoding="utf-8"))
    log(f"✅ Cargados {len(events)} eventos desde {latest}")

    # Limitar nº de eventos si se pide
    if LIMIT_EVENTS and LIMIT_EVENTS > 0:
        events = events[:LIMIT_EVENTS]
        log(f"🔎 LIMIT_EVENTS activo: procesaré {len(events)} eventos")

    driver = _get_driver(headless=HEADLESS)
    if not driver:
        log("❌ No se pudo crear el driver de Chrome"); return None

    try:
        if not _login(driver):
            raise Exception("No se pudo iniciar sesión")

        # Tope global (si aplica)
        global_deadline = _deadline(MAX_RUNTIME_MIN * 60) if MAX_RUNTIME_MIN > 0 else None

        detailed_events = []

        for i, event in enumerate(events, 1):
            # Salida ordenada si el tope global vence
            if global_deadline and _now() >= global_deadline:
                log("⏹️  Tiempo global agotado; guardo y salgo del bucle.")
                break

            try:
                preserved = ['id', 'nombre', 'fechas', 'organizacion', 'club', 'lugar', 'enlaces', 'pais_bandera']
                detailed_event = {k: event.get(k, '') for k in preserved}
                detailed_event['numero_participantes'] = 0
                detailed_event['participantes_info'] = 'No disponible'

                # ===== INFO DEL EVENTO (/info) =====
                info_processed = False
                if 'enlaces' in event and 'info' in event['enlaces']:
                    info_url = event['enlaces']['info']
                    log(f"Procesando evento {i}/{len(events)}: {event.get('nombre','Sin nombre')}")
                    try:
                        driver.get(info_url)
                        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                        slow_pause(1.2, 2.2)
                        soup = BeautifulSoup(driver.page_source, 'html.parser')

                        extra = {}
                        if not detailed_event.get('club') or detailed_event.get('club') in ['N/D', '']:
                            club_elems = soup.find_all(lambda t: any(w in t.get_text().lower() for w in ['club','organizador','organizer']))
                            for el in club_elems:
                                tx = _clean(el.get_text())
                                if tx and len(tx) < 100: detailed_event['club'] = tx; break
                        if not detailed_event.get('lugar') or detailed_event.get('lugar') in ['N/D', '']:
                            locs = soup.find_all(lambda t: any(w in t.get_text().lower() for w in ['lugar','ubicacion','location','place']))
                            for el in locs:
                                tx = _clean(el.get_text())
                                if tx and ('/' in tx or any(x in tx for x in ['Spain','España'])):
                                    detailed_event['lugar'] = tx; break
                        title = soup.find('h1')
                        if title: extra['titulo_completo'] = _clean(title.get_text())
                        desc = _extract_description(soup, max_length=800)
                        if desc: extra['descripcion'] = desc
                        detailed_event['informacion_adicional'] = extra
                        info_processed = True
                    except Exception as e:
                        log(f"  ❌ Error procesando información: {e}")

                # ===== PARTICIPANTES (rápido + robusto) =====
                if 'enlaces' in event and 'participantes' in event['enlaces']:
                    plist = event['enlaces']['participantes']
                    log(f"  Extrayendo número de participantes de: {plist}")

                    # Límite por evento
                    event_deadline = _deadline(PER_EVENT_MAX_S)

                    try:
                        driver.get(plist)
                        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                        _accept_cookies(driver)  # <- acepta si vuelve a salir banner

                        # Estado determinista con tope corto
                        state = _wait_state_participants_page(driver, timeout_s=min(PER_PAGE_MAX_S, _time_left(event_deadline)))

                        # Re-login una vez si caducó sesión
                        if state == "login":
                            log("  ℹ️ Sesión caducada; reintentando login…")
                            if _login(driver):
                                driver.get(plist)
                                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                                _accept_cookies(driver)
                                state = _wait_state_participants_page(driver, timeout_s=min(PER_PAGE_MAX_S, _time_left(event_deadline)))
                            else:
                                state = "timeout"

                        if state == "empty":
                            detailed_event['numero_participantes'] = 0
                            detailed_event['participantes_info'] = 'Sin participantes'
                            log("  ⚠️  Lista de participantes vacía (empty)")

                        elif state == "ok":
                            # 1º JS vivo
                            n = _count_participants_fast(driver)
                            # 2º Fallback HTML si JS da 0
                            if n == 0:
                                n = _count_participants_from_html(driver.page_source)
                            if n > 0:
                                detailed_event['numero_participantes'] = n
                                detailed_event['participantes_info'] = f"{n} participantes"
                                log(f"  ✅ Encontrados {n} participantes")
                            else:
                                detailed_event['numero_participantes'] = 0
                                detailed_event['participantes_info'] = 'Sin participantes'
                                log("  ⚠️  No se encontraron participantes tras conteos (JS/HTML)")

                        else:
                            detailed_event['numero_participantes'] = 0
                            detailed_event['participantes_info'] = 'Timeout esperando participantes'
                            log("  ⏱️  Timeout esperando lista; marco 0 y continúo")

                    except Exception as e:
                        log(f"  ❌ Error accediendo a participantes: {e}")
                        detailed_event['numero_participantes'] = 0
                        detailed_event['participantes_info'] = f"Error: {str(e)}"

                detailed_event['timestamp_extraccion'] = datetime.now().isoformat()
                detailed_event['procesado_info'] = info_processed
                detailed_events.append(detailed_event)
                slow_pause(0.6, 1.4)

            except Exception as e:
                log(f"❌ Error procesando evento {i}: {e}")
                event['timestamp_extraccion'] = datetime.now().isoformat()
                event['procesado_info'] = False
                event['numero_participantes'] = 0
                event['participantes_info'] = f"Error: {str(e)}"
                detailed_events.append(event)
                continue

        # Guardar
        today = datetime.now().strftime("%Y-%m-%d")
        out_dated  = os.path.join(OUT_DIR, f'02events_pastINFO_{today}.json')
        out_latest = os.path.join(OUT_DIR, '02events_pastINFO.json')
        with open(out_dated,  'w', encoding='utf-8') as f: json.dump(detailed_events, f, ensure_ascii=False, indent=2)
        with open(out_latest, 'w', encoding='utf-8') as f: json.dump(detailed_events, f, ensure_ascii=False, indent=2)
        log(f"✅ Información detallada guardada en {out_dated}")

        # Resumen
        total_participants = sum(e.get('numero_participantes', 0) for e in detailed_events)
        events_with_participants = sum(1 for e in detailed_events if e.get('numero_participantes', 0) > 0)
        events_with_info = sum(1 for e in detailed_events if e.get('procesado_info', False))

        print("\n" + "="*80)
        print("RESUMEN FINAL:")
        print("="*80)
        print(f"Eventos procesados: {len(detailed_events)}")
        print(f"Eventos con información detallada: {events_with_info}")
        print(f"Eventos con participantes: {events_with_participants}")
        print(f"Total participantes: {total_participants}")
        if events_with_participants:
            print("\n📊 Top eventos por nº participantes:")
            top = sorted([e for e in detailed_events if e.get('numero_participantes', 0) > 0],
                         key=lambda x: x.get('numero_participantes', 0), reverse=True)[:5]
            for t in top:
                print(f"  {t.get('nombre','N/A')}: {t.get('numero_participantes')}")
        print("\n" + "="*80 + "\n")

        return detailed_events

    except Exception as e:
        log(f"❌ Error durante la extracción detallada: {e}")
        traceback.print_exc()
        return None
    finally:
        try: driver.quit()
        except: pass

# ============================== MAIN ==============================

def main():
    print("🚀 INICIANDO FLOWAGILITY SCRAPER")
    print(f"📂 Directorio de salida: {OUT_DIR}")
    print("=" * 80)

    os.makedirs(OUT_DIR, exist_ok=True)
    _clean_output_directory()

    parser = argparse.ArgumentParser(description="FlowAgility Scraper - Eventos e Info Detallada")
    parser.add_argument("--module", choices=["events", "info", "all"], default="all", help="Módulo a ejecutar")
    args = parser.parse_args()

    try:
        success = True

        if args.module in ["events", "all"]:
            log("🏁 INICIANDO EXTRACCIÓN DE EVENTOS BÁSICOS")
            events = extract_events()
            if not events:
                log("❌ Falló la extracción de eventos")
                success = False
            else:
                log("✅ Eventos básicos extraídos correctamente")

        if args.module in ["info", "all"] and success:
            log("🏁 INICIANDO EXTRACCIÓN DE INFORMACIÓN DETALLADA")
            detailed = extract_detailed_info()
            if not detailed:
                log("⚠️  No se pudo extraer información detallada")
            else:
                log("✅ Información detallada extraída correctamente")

        if success:
            log("🎉 PROCESO COMPLETADO EXITOSAMENTE")
            print(f"\n📁 ARCHIVOS GENERADOS EN {OUT_DIR}:")
            for file in sorted(glob(os.path.join(OUT_DIR, "*"))):
                if os.path.isfile(file):
                    size = os.path.getsize(file)
                    print(f"   {os.path.basename(file)} - {size} bytes")
        else:
            log("❌ PROCESO COMPLETADO CON ERRORES")

        return success

    except Exception as e:
        log(f"❌ ERROR CRÍTICO DURANTE LA EJECUCIÓN: {e}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
