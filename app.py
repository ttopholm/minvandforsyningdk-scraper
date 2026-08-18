import logging
import re
from datetime import datetime
from json import dumps
from random import randint, uniform
from time import sleep
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from environs import Env
from paho.mqtt.publish import single as publish
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

env = Env()
env.read_env()

# variables requireds
mqtt_broker = env.str('mqtt-broker')
mvf_username = env.str('username')
mvf_password = env.str('password')


# optional variables
mqtt_port = env.int('mqtt-port', 1883)
mqtt_topic = env.str('mqtt-topic', 'minvandforsyningdk/total')
mqtt_status_topic = env.str('mqtt-status-topic', 'minvandforsyningdk/status')
mqtt_username = env.str('mqtt-username', None)
mqtt_password = env.str('mqtt-password', None)
datetime_format = env.str('datetime-format', 'kl. %H.%M, d. %d.%m.%Y')
login_url = env.str('login-url', 'https://www.minvandforsyning.dk/login/picker')

# browser settings
browser_cdp_url = env.str('browser-cdp-url', None)  # use a remote chrome instead
browser_executable = env.str('browser-executable', None)  # use another chromium build
headless = env.bool('headless', True)

# resilience settings
_run_timer = env.int('scrape-interval', 60 * 60)  # 1 hour between successful runs
retry_interval = env.int('retry-interval', 5 * 60)  # wait after a failed run
max_attempts = env.int('max-attempts', 3)  # attempts per run
element_timeout = env.int('element-timeout', 20)  # seconds to wait for an element
dashboard_timeout = env.int('dashboard-timeout', 60)  # the reading takes a while to render
page_load_timeout = env.int('page-load-timeout', 60)  # seconds before goto() gives up
form_settle_delay = env.int('form-settle-delay', 2)  # let the login form settle
mqtt_retries = env.int('mqtt-retries', 3)
debug_dir = env.str('debug-dir', None)  # dump html/screenshot/trace here when a run fails
log_level = env.str('log-level', 'INFO')

# home assistant mqtt discovery
mqtt_discovery = env.bool('mqtt-discovery', True)
discovery_prefix = env.str('mqtt-discovery-prefix', 'homeassistant')
mqtt_retain = env.bool('mqtt-retain', True)
device_name = env.str('device-name', 'Minvandforsyning')
# the site reports danish wall clock time without a timezone
timezone_name = env.str('timezone', 'Europe/Copenhagen')

mqtt_client_id = f'python-mqtt-{randint(0, 1000)}'

mqtt_auth = None
if mqtt_username is not None:
    mqtt_auth = {"username": mqtt_username, "password": mqtt_password}

logging.basicConfig(
    level=getattr(logging, log_level.upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(message)s',
)
log = logging.getLogger('minvandforsyning')

if env.str('webdriver-remote-url', None):
    log.warning("'webdriver-remote-url' is ignored, the scraper runs its own browser "
                "now. Drop the selenium container, and use 'browser-cdp-url' if you "
                "really want to drive a remote chrome.")

try:
    reading_timezone = ZoneInfo(timezone_name)
except (ZoneInfoNotFoundError, ValueError):
    log.warning("Unknown timezone '%s', falling back to the container timezone",
                timezone_name)
    reading_timezone = None

_announced_meters = set()


class ElementNotFoundError(Exception):
    """Raised when none of the candidate selectors for a target matched."""


def _parse_selectors(spec):
    """Split a selector spec into a list of playwright selectors.

    Candidates are separated by '||'. Playwright picks the engine itself: a
    selector starting with '//' is xpath, otherwise it is css, and the
    'text=', 'role=' and 'id=' prefixes are understood as well, e.g.:
        #signInName||input[type=email]||role=textbox[name="E-mail"]
    """
    return [candidate.strip() for candidate in spec.split('||') if candidate.strip()]


# Every element is looked up through an ordered list of candidate selectors, so a
# changed id or a moved button does not have to be fatal. The list can be
# replaced at runtime with the matching 'selector-*' environment variable, which
# means a layout change can be fixed without rebuilding the image.
_DEFAULT_SELECTORS = {
    'login-provider': (
        "xpath=/html/body/body/div/div/div[2]/div/div[3]/button/span/p"
        "||#LoginIntermediaryMudPaper button >> nth=2"
        "||role=button[name=/Ramb|lokal/i]"
    ),
    'username': (
        "#signInName"
        "||input[name=signInName]"
        "||input[type=email]"
        "||input[autocomplete=username]"
    ),
    'password': (
        "input[type=password]"
        "||#password"
        "||input[name=password]"
    ),
    'submit': (
        "#next"
        "||button[type=submit]"
        "||input[type=submit]"
        "||role=button[name=/log ind|sign in/i]"
    ),
    'total': (
        "xpath=//span[2]/b[2]"
        "||[class*=total] b >> nth=-1"
    ),
    'meter-id': (
        "xpath=//b"
        "||[class*=meter] b"
    ),
    'timestamp': (
        "xpath=//span[2]/b"
        "||[class*=total] b"
    ),
}

SELECTORS = {
    name: _parse_selectors(env.str(f'selector-{name}', default_spec))
    for name, default_spec in _DEFAULT_SELECTORS.items()
}

# Last resort when every selector for a value fails: pull the value straight out
# of the page text. Layout and ids can change without the text changing.
total_pattern = env.str('pattern-total', r'([\d.]+,\d+)\s*m(?:³|3)')
meter_id_pattern = env.str('pattern-meter-id', r'(?:m[åa]ler|meter)[^\d]{0,20}(\d{4,})')


def _format_to_regex(fmt):
    """Turn a strftime format into a regex that matches the same text."""
    tokens = {
        '%H': r'\d{1,2}', '%M': r'\d{2}', '%S': r'\d{2}',
        '%d': r'\d{1,2}', '%m': r'\d{1,2}', '%Y': r'\d{4}', '%y': r'\d{2}',
    }
    parts = re.split(r'(%[a-zA-Z%])', fmt)
    return ''.join(tokens.get(part, re.escape(part)) for part in parts)


def find(page, target, timeout=None):
    """Return the first candidate selector for `target` that is on the page.

    Playwright locators resolve on every use, so the returned locator does not
    go stale when blazor re-renders the element underneath it.
    """
    timeout = element_timeout if timeout is None else timeout
    selectors = SELECTORS[target]
    # Split the budget so one dead selector cannot eat the whole timeout
    per_selector = max(2, timeout // max(1, len(selectors))) * 1000

    for index, selector in enumerate(selectors):
        locator = page.locator(selector).first
        try:
            locator.wait_for(state='visible', timeout=per_selector)
        except (PlaywrightTimeoutError, PlaywrightError):
            continue
        if index > 0:
            log.warning(
                "Fallback selector used for '%s': %s (the preferred selector no "
                "longer matches, the site layout has probably changed)",
                target, selector,
            )
        return locator

    raise ElementNotFoundError(
        f"No selector matched '{target}'. Tried: {selectors}. "
        f"Override it with the 'selector-{target}' environment variable."
    )


def click(page, target, timeout=None):
    """Click `target`. Playwright waits for it to be actionable by itself."""
    find(page, target, timeout=timeout).click(timeout=element_timeout * 1000)


def get_text(page, target, timeout=None):
    return find(page, target, timeout=timeout).inner_text().strip()


def _parse_decimal(value):
    """Parse a Danish formatted number, e.g. '1.234,56' -> 1234.56."""
    value = value.strip()
    if ',' in value:
        # comma is the decimal separator, so a dot can only be a thousand separator
        value = value.replace('.', '').replace(',', '.')
    return float(value)


def _localize(timestamp):
    """Attach a timezone to the naive timestamp read off the page."""
    if reading_timezone is not None:
        return timestamp.replace(tzinfo=reading_timezone)
    return timestamp.astimezone()


def _body_text(page):
    """The whole page as text, used when no selector matched any more."""
    try:
        return page.locator('body').inner_text()
    except PlaywrightError:
        return ''


def _text_fallback(body_text, pattern, target):
    match = re.search(pattern, body_text, re.IGNORECASE)
    if not match:
        return None
    log.warning("Read '%s' from the page text instead of an element", target)
    return match.group(1) if match.groups() else match.group(0)


def read_values(page, timeout=None):
    """Read total, meter id and timestamp, falling back to page text."""
    timeout = dashboard_timeout if timeout is None else timeout

    try:
        total = _parse_decimal(get_text(page, 'total', timeout=timeout))
    except (ElementNotFoundError, ValueError):
        raw = _text_fallback(_body_text(page), total_pattern, 'total')
        if raw is None:
            raise
        total = _parse_decimal(raw)

    try:
        meter_id = int(re.sub(r'\D', '', get_text(page, 'meter-id')))
    except (ElementNotFoundError, ValueError):
        raw = _text_fallback(_body_text(page), meter_id_pattern, 'meter-id')
        if raw is None:
            raise
        meter_id = int(raw)

    try:
        timestamp = datetime.strptime(get_text(page, 'timestamp'), datetime_format)
    except (ElementNotFoundError, ValueError):
        raw = _text_fallback(_body_text(page), _format_to_regex(datetime_format), 'timestamp')
        if raw is None:
            raise
        timestamp = datetime.strptime(raw, datetime_format)

    return {
        "total": total,
        "meter_id": meter_id,
        "timestamp": datetime.strftime(timestamp, "%Y-%m-%d %H:%M:%S"),
        # home assistant needs an unambiguous timestamp, so attach the timezone
        # the reading was written in
        "timestamp_iso": _localize(timestamp).isoformat(),
    }


def dump_diagnostics(page, name):
    """Save the page so a layout change can be inspected afterwards."""
    if not debug_dir or page is None:
        return
    from os import makedirs
    from os.path import join
    try:
        makedirs(debug_dir, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        base = join(debug_dir, f'{stamp}-{name}')
        with open(f'{base}.html', 'w', encoding='utf-8') as handle:
            handle.write(page.content())
        page.screenshot(path=f'{base}.png', full_page=True)
        log.info("Wrote diagnostics to %s.html / %s.png", base, base)
    except Exception as error:  # diagnostics must never break the run
        log.warning("Could not write diagnostics: %s", error)


def open_browser(playwright):
    """Launch our own chromium, or attach to a remote one when configured."""
    if browser_cdp_url:
        return playwright.chromium.connect_over_cdp(browser_cdp_url)
    return playwright.chromium.launch(
        headless=headless,
        executable_path=browser_executable,
        # /dev/shm is small in most containers, and chromium crashes without this
        args=['--disable-dev-shm-usage'],
    )


def publish_message(topic, message, retries=None, retain=False):
    """Publish to MQTT, retrying transient broker/network errors."""
    retries = mqtt_retries if retries is None else retries
    for attempt in range(1, retries + 1):
        try:
            publish(topic, message, hostname=mqtt_broker, port=mqtt_port,
                    auth=mqtt_auth, retain=retain)
            return True
        except (ConnectionRefusedError, OSError) as error:
            log.warning("Can't connect to mqtt server (attempt %s/%s): %s",
                        attempt, retries, error)
            if attempt < retries:
                sleep(min(30, 2 ** attempt))
    return False


def publish_status(status):
    if mqtt_status_topic:
        publish_message(mqtt_status_topic, status, retries=1, retain=True)


def discovery_config(meter_id):
    """Home Assistant mqtt discovery config, one entry per entity.

    See https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery. The
    payloads are retained so the entities survive a Home Assistant restart.
    """
    node_id = f'minvandforsyning_{meter_id}'
    device = {
        "identifiers": [node_id],
        "name": device_name,
        "manufacturer": "minvandforsyning.dk",
        "model": "Water meter",
        "configuration_url": "https://www.minvandforsyning.dk",
    }
    origin = {
        "name": "minvandforsyningdk-scraper",
        "support_url": "https://github.com/ttopholm/minvandforsyningdk-scraper",
    }
    shared = {
        "state_topic": mqtt_topic,
        "device": device,
        "origin": origin,
    }
    if mqtt_status_topic:
        shared["availability_topic"] = mqtt_status_topic
        shared["payload_available"] = "online"
        shared["payload_not_available"] = "offline"

    entities = {
        "total": {
            "name": "Total",
            "unique_id": f'{node_id}_total',
            "device_class": "water",
            "state_class": "total_increasing",
            "unit_of_measurement": "m³",
            "value_template": "{{ value_json.total }}",
            "icon": "mdi:water",
        },
        "timestamp": {
            "name": "Last reading",
            "unique_id": f'{node_id}_timestamp',
            "device_class": "timestamp",
            "value_template": "{{ value_json.timestamp_iso }}",
            "entity_category": "diagnostic",
        },
        "meter_id": {
            "name": "Meter number",
            "unique_id": f'{node_id}_meter_id',
            "value_template": "{{ value_json.meter_id }}",
            "entity_category": "diagnostic",
            "icon": "mdi:counter",
        },
    }

    return [
        (f'{discovery_prefix}/sensor/{node_id}/{object_id}/config', {**shared, **config})
        for object_id, config in entities.items()
    ]


def publish_discovery(meter_id):
    """Announce the entities to Home Assistant. Only needed once per meter."""
    if not mqtt_discovery or meter_id in _announced_meters:
        return
    for topic, config in discovery_config(meter_id):
        if not publish_message(topic, dumps(config), retain=True):
            log.warning("Could not publish the discovery config to %s", topic)
            return
    _announced_meters.add(meter_id)
    log.info("Announced meter %s to Home Assistant on %s/sensor/minvandforsyning_%s/",
             meter_id, discovery_prefix, meter_id)




def scrape_once():
    """One full attempt: log in, read the meter, publish. Raises on failure."""
    with sync_playwright() as playwright:
        browser = context = page = None
        tracing = False
        try:
            browser = open_browser(playwright)
            # a fresh context per run is the playwright equivalent of incognito
            context = browser.new_context()
            context.set_default_timeout(element_timeout * 1000)
            if debug_dir:
                context.tracing.start(screenshots=True, snapshots=True)
                tracing = True
            page = context.new_page()

            page.goto(login_url, timeout=page_load_timeout * 1000)
            click(page, 'login-provider')
            # the login form is rendered by javascript, so wait for it and give
            # it a moment to settle before typing into it
            find(page, 'username')
            sleep(form_settle_delay)
            find(page, 'username').fill(mvf_username)
            find(page, 'password').fill(mvf_password)
            click(page, 'submit')

            values = read_values(page)
            log.info("Read meter %s: %s m3 at %s",
                     values['meter_id'], values['total'], values['timestamp'])

            publish_discovery(values['meter_id'])
            if not publish_message(mqtt_topic, dumps(values), retain=mqtt_retain):
                raise RuntimeError("Could not publish the reading to mqtt")
            publish_status('online')
            return values
        except Exception:
            dump_diagnostics(page, 'failure')
            tracing = save_trace(context, tracing)
            raise
        finally:
            close_quietly(context, tracing)
            close_quietly(browser)


def save_trace(context, tracing):
    """Write the playwright trace of a failed run, viewable with trace.playwright.dev."""
    if not tracing or context is None:
        return tracing
    from os.path import join
    try:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        path = join(debug_dir, f'{stamp}-failure-trace.zip')
        context.tracing.stop(path=path)
        log.info("Wrote a playwright trace to %s, open it on https://trace.playwright.dev",
                 path)
    except Exception as error:
        log.warning("Could not write the trace: %s", error)
    return False


def close_quietly(closeable, tracing=False):
    """Closing must never be the thing that takes the job down."""
    if closeable is None:
        return
    try:
        if tracing:
            closeable.tracing.stop()
        closeable.close()
    except Exception as error:
        log.warning("Could not close the browser cleanly: %s", error)


def scrape():
    """Run scrape_once with retries. Never raises, returns the values or None."""
    for attempt in range(1, max_attempts + 1):
        try:
            return scrape_once()
        except ElementNotFoundError as error:
            log.error("Attempt %s/%s failed: %s", attempt, max_attempts, error)
        except PlaywrightTimeoutError as error:
            log.error("Attempt %s/%s timed out: %s", attempt, max_attempts, error)
        except PlaywrightError as error:
            log.error("Attempt %s/%s failed, browser problem: %s",
                      attempt, max_attempts, error)
        except Exception as error:
            log.error("Attempt %s/%s failed: %s", attempt, max_attempts, error)

        if attempt < max_attempts:
            backoff = min(120, 2 ** attempt * 5) + uniform(0, 5)
            log.info("Retrying in %.0f seconds", backoff)
            sleep(backoff)

    log.error("Giving up on this run after %s attempts", max_attempts)
    publish_status('offline')
    return None


def main():
    log.info("Starting, scraping every %s seconds", _run_timer)
    while True:
        try:
            values = scrape()
        except Exception as error:  # the loop must survive anything
            log.exception("Unexpected error in the scrape loop: %s", error)
            values = None
        delay = _run_timer if values else retry_interval
        log.info("Next run in %s seconds", delay)
        sleep(delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
