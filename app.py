import logging
import re
from datetime import datetime
from json import dumps
from random import randint, uniform
from time import sleep

from environs import Env
from paho.mqtt.publish import single as publish
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

env = Env()
env.read_env()

# variables requireds
mqtt_broker = env.str('mqtt-broker')
mvf_username = env.str('username')
mvf_password = env.str('password')


# optional variables
mqtt_port = env.int('mqtt-port', 1883)
mqtt_topic = env.str('mqtt-topic', 'minvandforsyningdk/total')
mqtt_status_topic = env.str('mqtt-status-topic', None)
mqtt_username = env.str('mqtt-username', None)
mqtt_password = env.str('mqtt-password', None)
webdriver_remote_url = env.str('webdriver-remote-url', 'http://selenium:4444')
datetime_format = env.str('datetime-format', 'kl. %H.%M, d. %d.%m.%Y')
login_url = env.str('login-url', 'https://www.minvandforsyning.dk/login/picker')

# resilience settings
_run_timer = env.int('scrape-interval', 60 * 60)  # 1 hour between successful runs
retry_interval = env.int('retry-interval', 5 * 60)  # wait after a failed run
max_attempts = env.int('max-attempts', 3)  # attempts per run
element_timeout = env.int('element-timeout', 20)  # seconds to wait for an element
dashboard_timeout = env.int('dashboard-timeout', 60)  # the reading takes a while to render
page_load_timeout = env.int('page-load-timeout', 60)  # seconds before get() gives up
mqtt_retries = env.int('mqtt-retries', 3)
debug_dir = env.str('debug-dir', None)  # dump html/screenshot here when a run fails
log_level = env.str('log-level', 'INFO')

mqtt_client_id = f'python-mqtt-{randint(0, 1000)}'

mqtt_auth = None
if mqtt_username is not None:
    mqtt_auth = {"username": mqtt_username, "password": mqtt_password}

logging.basicConfig(
    level=getattr(logging, log_level.upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(message)s',
)
log = logging.getLogger('minvandforsyning')


class ElementNotFoundError(Exception):
    """Raised when none of the candidate locators for a target matched."""


def _parse_locators(spec):
    """Parse a locator spec string into a list of (By, value) tuples.

    Candidates are separated by '||' and may be prefixed with 'xpath=' or
    'css=' (xpath is assumed when no prefix is given), e.g.:
        css=#signInName||xpath=//input[@type='email']
    """
    locators = []
    for candidate in spec.split('||'):
        candidate = candidate.strip()
        if not candidate:
            continue
        if candidate.lower().startswith('css='):
            locators.append((By.CSS_SELECTOR, candidate[4:].strip()))
        elif candidate.lower().startswith('xpath='):
            locators.append((By.XPATH, candidate[6:].strip()))
        else:
            locators.append((By.XPATH, candidate))
    return locators


# Every element is looked up through an ordered list of candidate locators, so a
# changed id or a moved button does not have to be fatal. The list can be
# replaced at runtime with the matching 'selector-*' environment variable, which
# means a layout change can be fixed without rebuilding the image.
_DEFAULT_SELECTORS = {
    'login-provider': (
        "/html/body/body/div/div/div[2]/div/div[3]/button/span/p"
        "||//*[@id='LoginIntermediaryMudPaper']//button[3]"
        "||//button[contains(., 'Ramb') or contains(., 'lokal')]"
        "||(//button[.//p])[3]"
    ),
    'username': (
        "//*[@id='signInName']"
        "||//input[@name='signInName']"
        "||//input[@type='email']"
        "||//input[@autocomplete='username']"
    ),
    'password': (
        "//input[@type='password']"
        "||//*[@id='password']"
        "||//input[@name='password']"
    ),
    'submit': (
        "//*[@id='next']"
        "||//button[@type='submit']"
        "||//input[@type='submit']"
        "||//button[contains(., 'Log ind') or contains(., 'Sign in')]"
    ),
    'total': (
        "//span[2]/b[2]"
        "||//*[contains(@class, 'total')]//b[last()]"
    ),
    'meter-id': (
        "//b"
        "||//*[contains(@class, 'meter')]//b[1]"
    ),
    'timestamp': (
        "//span[2]/b"
        "||//*[contains(@class, 'total')]//b[1]"
    ),
}

SELECTORS = {
    name: _parse_locators(env.str(f'selector-{name}', default_spec))
    for name, default_spec in _DEFAULT_SELECTORS.items()
}

# Last resort when every locator for a value fails: pull the value straight out
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


def wait_for_element(wd, elm, timeout=10):
    """Wait for a single XPath to be present. Kept for backwards compatibility."""
    try:
        element_present = EC.presence_of_element_located((By.XPATH, elm))
        WebDriverWait(wd, timeout).until(element_present)
        return True
    except TimeoutException:
        log.warning("Timed out waiting for %s", elm)


def find_element(browser, target, timeout=None, clickable=False):
    """Find `target` using its candidate locators, first match wins."""
    timeout = element_timeout if timeout is None else timeout
    locators = SELECTORS[target]
    # Split the budget so one dead locator cannot eat the whole timeout.
    per_locator = max(2, timeout // max(1, len(locators)))
    condition = EC.element_to_be_clickable if clickable else EC.presence_of_element_located

    for index, locator in enumerate(locators):
        try:
            element = WebDriverWait(browser, per_locator).until(condition(locator))
            if index > 0:
                log.warning(
                    "Fallback locator used for '%s': %s (the preferred locator no "
                    "longer matches, the site layout has probably changed)",
                    target, locator,
                )
            return element
        except (TimeoutException, StaleElementReferenceException):
            continue
    raise ElementNotFoundError(
        f"No locator matched '{target}'. Tried: {locators}. "
        f"Override it with the 'selector-{target}' environment variable."
    )


def click(browser, target, timeout=None):
    """Click `target`, retrying the usual transient click failures."""
    last_error = None
    for _ in range(3):
        try:
            element = find_element(browser, target, timeout=timeout, clickable=True)
            element.click()
            return
        except (ElementClickInterceptedException, ElementNotInteractableException,
                StaleElementReferenceException) as error:
            last_error = error
            sleep(1)
    raise last_error


def get_text(browser, target, timeout=None):
    element = find_element(browser, target, timeout=timeout)
    return element.text.strip()


def _parse_decimal(value):
    """Parse a Danish formatted number, e.g. '1.234,56' -> 1234.56."""
    value = value.strip()
    if ',' in value:
        # comma is the decimal separator, so a dot can only be a thousand separator
        value = value.replace('.', '').replace(',', '.')
    return float(value)


def _body_text(browser):
    """The whole page as text, used when no locator matched any more."""
    try:
        return browser.find_element(By.TAG_NAME, 'body').text
    except WebDriverException:
        return ''


def _text_fallback(body_text, pattern, target):
    match = re.search(pattern, body_text, re.IGNORECASE)
    if not match:
        return None
    log.warning("Read '%s' from the page text instead of an element", target)
    return match.group(1) if match.groups() else match.group(0)


def read_values(browser, timeout=None):
    """Read total, meter id and timestamp, falling back to page text."""
    timeout = dashboard_timeout if timeout is None else timeout

    try:
        total = _parse_decimal(get_text(browser, 'total', timeout=timeout))
    except (ElementNotFoundError, ValueError):
        body_text = _body_text(browser)
        raw = _text_fallback(body_text, total_pattern, 'total')
        if raw is None:
            raise
        total = _parse_decimal(raw)

    try:
        meter_id = int(re.sub(r'\D', '', get_text(browser, 'meter-id')))
    except (ElementNotFoundError, ValueError):
        body_text = _body_text(browser)
        raw = _text_fallback(body_text, meter_id_pattern, 'meter-id')
        if raw is None:
            raise
        meter_id = int(raw)

    try:
        timestamp = datetime.strptime(get_text(browser, 'timestamp'), datetime_format)
    except (ElementNotFoundError, ValueError):
        body_text = _body_text(browser)
        raw = _text_fallback(body_text, _format_to_regex(datetime_format), 'timestamp')
        if raw is None:
            raise
        timestamp = datetime.strptime(raw, datetime_format)

    return {
        "total": total,
        "meter_id": meter_id,
        "timestamp": datetime.strftime(timestamp, "%Y-%m-%d %H:%M:%S"),
    }


def dump_diagnostics(browser, name):
    """Save the page so a layout change can be inspected afterwards."""
    if not debug_dir or browser is None:
        return
    from os import makedirs
    from os.path import join
    try:
        makedirs(debug_dir, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        base = join(debug_dir, f'{stamp}-{name}')
        with open(f'{base}.html', 'w', encoding='utf-8') as handle:
            handle.write(browser.page_source)
        browser.save_screenshot(f'{base}.png')
        log.info("Wrote diagnostics to %s.html / %s.png", base, base)
    except Exception as error:  # diagnostics must never break the run
        log.warning("Could not write diagnostics: %s", error)


def create_browser():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--incognito")
    browser = webdriver.Remote(webdriver_remote_url, options=chrome_options)
    try:
        browser.set_page_load_timeout(page_load_timeout)
        browser.set_script_timeout(page_load_timeout)
    except WebDriverException as error:
        log.warning("Could not set browser timeouts: %s", error)
    return browser


def publish_message(topic, message, retries=None):
    """Publish to MQTT, retrying transient broker/network errors."""
    retries = mqtt_retries if retries is None else retries
    for attempt in range(1, retries + 1):
        try:
            publish(topic, message, hostname=mqtt_broker, port=mqtt_port, auth=mqtt_auth)
            return True
        except (ConnectionRefusedError, OSError) as error:
            log.warning("Can't connect to mqtt server (attempt %s/%s): %s",
                        attempt, retries, error)
            if attempt < retries:
                sleep(min(30, 2 ** attempt))
    return False


def publish_status(status):
    if mqtt_status_topic:
        publish_message(mqtt_status_topic, status, retries=1)


def scrape_once():
    """One full attempt: log in, read the meter, publish. Raises on failure."""
    browser = None
    try:
        browser = create_browser()
        browser.get(login_url)
        click(browser, 'login-provider')
        # the login form is rendered by javascript, so wait for it and give it a
        # moment to settle before typing into it
        find_element(browser, 'username')
        sleep(2)
        find_element(browser, 'username').send_keys(mvf_username)
        find_element(browser, 'password').send_keys(mvf_password)
        click(browser, 'submit')

        values = read_values(browser)
        log.info("Read meter %s: %s m3 at %s",
                 values['meter_id'], values['total'], values['timestamp'])

        if not publish_message(mqtt_topic, dumps(values)):
            raise RuntimeError("Could not publish the reading to mqtt")
        publish_status('online')
        return values
    except Exception:
        dump_diagnostics(browser, 'failure')
        raise
    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception as error:
                log.warning("Could not close the browser cleanly: %s", error)


def scrape():
    """Run scrape_once with retries. Never raises, returns the values or None."""
    for attempt in range(1, max_attempts + 1):
        try:
            return scrape_once()
        except ElementNotFoundError as error:
            log.error("Attempt %s/%s failed: %s", attempt, max_attempts, error)
        except WebDriverException as error:
            log.error("Attempt %s/%s failed, browser/selenium problem: %s",
                      attempt, max_attempts, getattr(error, 'msg', error))
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
