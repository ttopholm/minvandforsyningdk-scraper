# Tests package
import pytest
from unittest.mock import Mock, patch
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from datetime import datetime
import json
import sys
import os

# Set up environment variables before importing app
os.environ.setdefault('mqtt-broker', 'test-broker')
os.environ.setdefault('username', 'test-user')
os.environ.setdefault('password', 'test-pass')

# Import the functions we want to test
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class ImmediateWait:
    """Stand-in for WebDriverWait that evaluates the condition exactly once."""

    def __init__(self, driver, timeout, *args, **kwargs):
        self.driver = driver

    def until(self, condition):
        try:
            result = condition(self.driver)
        except Exception:
            raise TimeoutException("not found")
        if not result:
            raise TimeoutException("not found")
        return result


def make_driver(elements):
    """Build a fake driver where `elements` maps a locator value to text."""
    driver = Mock()

    def find_element(by, value):
        if value in elements:
            element = Mock()
            element.text = elements[value]
            # selenium compares is_displayed() to True, so a bare Mock is not enough
            element.is_displayed.return_value = True
            element.is_enabled.return_value = True
            return element
        raise NoSuchElementException(value)

    driver.find_element.side_effect = find_element
    return driver


class TestWaitForElement:
    """Tests for the wait_for_element function"""

    @patch('app.WebDriverWait')
    @patch('app.EC')
    def test_wait_for_element_success(self, mock_ec, mock_wait):
        """Test wait_for_element returns True when element is found"""
        from app import wait_for_element

        mock_driver = Mock()
        mock_wait_instance = Mock()
        mock_wait.return_value = mock_wait_instance
        mock_wait_instance.until.return_value = True

        result = wait_for_element(mock_driver, '//*[@id="test"]', timeout=10)

        assert result is True
        mock_wait.assert_called_once_with(mock_driver, 10)
        mock_ec.presence_of_element_located.assert_called_once_with((By.XPATH, '//*[@id="test"]'))

    @patch('app.WebDriverWait')
    @patch('app.EC')
    def test_wait_for_element_timeout(self, mock_ec, mock_wait):
        """Test wait_for_element handles timeout exception"""
        from app import wait_for_element

        mock_driver = Mock()
        mock_wait_instance = Mock()
        mock_wait.return_value = mock_wait_instance
        mock_wait_instance.until.side_effect = TimeoutException("Timeout")

        result = wait_for_element(mock_driver, '//*[@id="test"]', timeout=10)

        assert result is None

    @patch('app.WebDriverWait')
    @patch('app.EC')
    def test_wait_for_element_custom_timeout(self, mock_ec, mock_wait):
        """Test wait_for_element with custom timeout"""
        from app import wait_for_element

        mock_driver = Mock()
        mock_wait_instance = Mock()
        mock_wait.return_value = mock_wait_instance
        mock_wait_instance.until.return_value = True

        wait_for_element(mock_driver, '//*[@id="test"]', timeout=30)

        mock_wait.assert_called_once_with(mock_driver, 30)


class TestLocatorParsing:
    """Tests for the configurable locator specs"""

    def test_defaults_to_xpath(self):
        from app import _parse_locators

        assert _parse_locators("//*[@id='a']") == [(By.XPATH, "//*[@id='a']")]

    def test_multiple_candidates_and_prefixes(self):
        from app import _parse_locators

        locators = _parse_locators("css=#a|| xpath=//b ||//c")

        assert locators == [
            (By.CSS_SELECTOR, '#a'),
            (By.XPATH, '//b'),
            (By.XPATH, '//c'),
        ]

    def test_empty_candidates_are_ignored(self):
        from app import _parse_locators

        assert _parse_locators("//a||||") == [(By.XPATH, '//a')]

    def test_every_target_has_a_fallback(self):
        import app

        for target, locators in app.SELECTORS.items():
            assert len(locators) >= 2, f"{target} has no fallback locator"

    def test_selector_can_be_overridden_by_environment(self):
        import app

        assert app._DEFAULT_SELECTORS['username'].startswith("//*[@id='signInName']")
        assert app.SELECTORS['username'] == app._parse_locators(
            app._DEFAULT_SELECTORS['username']
        )


class TestFindElement:
    """Tests for the locator fallback logic"""

    @patch('app.WebDriverWait', ImmediateWait)
    def test_uses_first_matching_locator(self):
        import app

        driver = make_driver({"//*[@id='signInName']": 'first'})

        assert app.find_element(driver, 'username').text == 'first'

    @patch('app.WebDriverWait', ImmediateWait)
    def test_falls_back_when_the_id_changed(self):
        import app

        # The primary id is gone, as if the site changed its markup
        driver = make_driver({"//input[@type='email']": 'fallback'})

        assert app.find_element(driver, 'username').text == 'fallback'

    @patch('app.WebDriverWait', ImmediateWait)
    def test_raises_when_nothing_matches(self):
        import app

        driver = make_driver({})

        with pytest.raises(app.ElementNotFoundError) as error:
            app.find_element(driver, 'username')

        # The error tells the user how to fix it without a code change
        assert 'selector-username' in str(error.value)


class TestClick:
    """Tests for the click helper"""

    @patch('app.sleep')
    @patch('app.WebDriverWait', ImmediateWait)
    def test_click_success(self, mock_sleep):
        import app

        driver = make_driver({"//*[@id='next']": 'submit'})
        app.click(driver, 'submit')

    @patch('app.sleep')
    @patch('app.WebDriverWait', ImmediateWait)
    def test_click_retries_intercepted_clicks(self, mock_sleep):
        import app
        from selenium.common.exceptions import ElementClickInterceptedException

        element = Mock()
        element.text = 'submit'
        element.is_displayed.return_value = True
        element.is_enabled.return_value = True
        element.click.side_effect = [ElementClickInterceptedException("busy"), None]

        driver = Mock()
        driver.find_element.side_effect = lambda by, value: (
            element if value == "//*[@id='next']" else _raise(NoSuchElementException(value))
        )

        app.click(driver, 'submit')

        assert element.click.call_count == 2


def _raise(error):
    raise error


class TestReadValues:
    """Tests for reading the values off the page"""

    @patch('app.WebDriverWait', ImmediateWait)
    def test_reads_values_from_elements(self):
        import app

        driver = make_driver({
            '//span[2]/b[2]': '234,32',
            '//b': '23522852',
            '//span[2]/b': 'kl. 18.58, d. 07.10.2024',
        })

        values = app.read_values(driver)

        assert values == {
            'total': 234.32,
            'meter_id': 23522852,
            'timestamp': '2024-10-07 18:58:00',
        }

    @patch('app.WebDriverWait', ImmediateWait)
    def test_falls_back_to_page_text_when_the_layout_changed(self):
        import app

        # None of the locators match any more, but the text is still on the page
        driver = make_driver({
            'body': 'Måler nr. 23522852\nForbrug i alt 1.234,50 m³\n'
                    'Aflæst kl. 18.58, d. 07.10.2024',
        })

        values = app.read_values(driver)

        assert values['total'] == 1234.50
        assert values['meter_id'] == 23522852
        assert values['timestamp'] == '2024-10-07 18:58:00'

    def test_body_text_is_empty_when_the_page_is_gone(self):
        import app

        driver = Mock()
        driver.find_element.side_effect = WebDriverException("no such window")

        assert app._body_text(driver) == ''

    @patch('app.WebDriverWait', ImmediateWait)
    def test_raises_when_the_value_is_nowhere(self):
        import app

        driver = make_driver({'body': 'Ingen data'})

        with pytest.raises(app.ElementNotFoundError):
            app.read_values(driver)


class TestPublishMessage:
    """Tests for MQTT publishing with retries"""

    @patch('app.publish')
    def test_publish_success(self, mock_publish):
        import app

        assert app.publish_message('topic', 'payload') is True
        mock_publish.assert_called_once()

    @patch('app.sleep')
    @patch('app.publish')
    def test_publish_retries_then_gives_up(self, mock_publish, mock_sleep):
        import app

        mock_publish.side_effect = ConnectionRefusedError("Connection refused")

        assert app.publish_message('topic', 'payload', retries=3) is False
        assert mock_publish.call_count == 3

    @patch('app.sleep')
    @patch('app.publish')
    def test_publish_recovers_on_a_later_attempt(self, mock_publish, mock_sleep):
        import app

        mock_publish.side_effect = [OSError("network down"), None]

        assert app.publish_message('topic', 'payload') is True
        assert mock_publish.call_count == 2


class TestScrapeFunction:
    """Tests for the scrape function"""

    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    @patch('app.WebDriverWait', ImmediateWait)
    def test_scrape_success(self, mock_webdriver, mock_sleep, mock_publish):
        """Test successful scraping and MQTT publishing"""
        import app

        mock_browser = make_driver({
            "/html/body/body/div/div/div[2]/div/div[3]/button/span/p": 'login',
            "//*[@id='signInName']": 'user',
            "//input[@type='password']": 'pass',
            "//*[@id='next']": 'next',
            '//span[2]/b[2]': '234,32',
            '//b': '23522852',
            '//span[2]/b': 'kl. 18.58, d. 07.10.2024',
        })
        mock_webdriver.Remote.return_value = mock_browser

        values = app.scrape()

        mock_webdriver.Remote.assert_called_once()
        mock_browser.quit.assert_called_once()
        mock_browser.get.assert_called_once_with(app.login_url)

        mock_publish.assert_called_once()
        parsed_msg = json.loads(mock_publish.call_args[0][1])
        assert parsed_msg['total'] == 234.32
        assert parsed_msg['meter_id'] == 23522852
        assert parsed_msg['timestamp'] == '2024-10-07 18:58:00'
        assert values['total'] == 234.32

    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    @patch('app.WebDriverWait', ImmediateWait)
    def test_scrape_mqtt_connection_error(self, mock_webdriver, mock_sleep, mock_publish):
        """Test scrape handles MQTT connection errors without crashing"""
        import app

        mock_browser = make_driver({
            "/html/body/body/div/div/div[2]/div/div[3]/button/span/p": 'login',
            "//*[@id='signInName']": 'user',
            "//input[@type='password']": 'pass',
            "//*[@id='next']": 'next',
            '//span[2]/b[2]': '234,32',
            '//b': '23522852',
            '//span[2]/b': 'kl. 18.58, d. 07.10.2024',
        })
        mock_webdriver.Remote.return_value = mock_browser
        mock_publish.side_effect = ConnectionRefusedError("Connection refused")

        assert app.scrape() is None

        # every attempt closed its browser
        assert mock_browser.quit.call_count == app.max_attempts

    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    def test_scrape_general_exception(self, mock_webdriver, mock_sleep, mock_publish):
        """Test scrape survives an exception and retries"""
        import app

        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser
        mock_browser.get.side_effect = Exception("Test exception")

        assert app.scrape() is None

        assert mock_browser.get.call_count == app.max_attempts
        assert mock_browser.quit.call_count == app.max_attempts

    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    def test_scrape_survives_a_dead_selenium(self, mock_webdriver, mock_sleep, mock_publish):
        """A selenium server that is down must not kill the job"""
        import app

        mock_webdriver.Remote.side_effect = WebDriverException("connection refused")

        assert app.scrape() is None
        assert mock_webdriver.Remote.call_count == app.max_attempts

    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    @patch('app.WebDriverWait', ImmediateWait)
    def test_scrape_recovers_on_the_second_attempt(self, mock_webdriver, mock_sleep, mock_publish):
        """A transient failure is retried within the same run"""
        import app

        good_browser = make_driver({
            "/html/body/body/div/div/div[2]/div/div[3]/button/span/p": 'login',
            "//*[@id='signInName']": 'user',
            "//input[@type='password']": 'pass',
            "//*[@id='next']": 'next',
            '//span[2]/b[2]': '234,32',
            '//b': '23522852',
            '//span[2]/b': 'kl. 18.58, d. 07.10.2024',
        })
        broken_browser = Mock()
        broken_browser.get.side_effect = WebDriverException("timeout")
        mock_webdriver.Remote.side_effect = [broken_browser, good_browser]

        values = app.scrape()

        assert values['total'] == 234.32
        mock_publish.assert_called_once()

    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    def test_scrape_browser_navigation(self, mock_webdriver, mock_sleep, mock_publish):
        """Test scrape navigates to the configured login URL"""
        import app

        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser
        mock_browser.get.side_effect = Exception("Stop here")

        app.scrape()

        mock_browser.get.assert_called_with("https://www.minvandforsyning.dk/login/picker")

    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    def test_browser_quit_error_is_not_fatal(self, mock_webdriver, mock_sleep, mock_publish):
        """A browser that cannot be closed must not take the job down"""
        import app

        mock_browser = Mock()
        mock_browser.get.side_effect = Exception("Stop here")
        mock_browser.quit.side_effect = WebDriverException("session already gone")
        mock_webdriver.Remote.return_value = mock_browser

        assert app.scrape() is None


class TestStatusTopic:
    """Tests for the optional status topic"""

    @patch('app.publish_message')
    def test_status_is_not_published_without_a_topic(self, mock_publish_message):
        import app

        with patch.object(app, 'mqtt_status_topic', None):
            app.publish_status('offline')

        mock_publish_message.assert_not_called()

    @patch('app.publish_message')
    def test_status_is_published_when_configured(self, mock_publish_message):
        import app

        with patch.object(app, 'mqtt_status_topic', 'water/status'):
            app.publish_status('offline')

        mock_publish_message.assert_called_once_with('water/status', 'offline', retries=1)


class TestDiagnostics:
    """Tests for the failure diagnostics dump"""

    @patch('app.WebDriverWait', ImmediateWait)
    def test_dump_writes_page_source_and_screenshot(self, tmp_path):
        import app

        browser = Mock()
        browser.page_source = '<html>changed layout</html>'

        with patch.object(app, 'debug_dir', str(tmp_path)):
            app.dump_diagnostics(browser, 'failure')

        dumps = list(tmp_path.glob('*-failure.html'))
        assert len(dumps) == 1
        assert dumps[0].read_text() == '<html>changed layout</html>'
        browser.save_screenshot.assert_called_once()

    def test_dump_is_skipped_without_a_debug_dir(self):
        import app

        browser = Mock()
        with patch.object(app, 'debug_dir', None):
            app.dump_diagnostics(browser, 'failure')

        browser.save_screenshot.assert_not_called()

    def test_dump_swallows_its_own_errors(self, tmp_path):
        import app

        browser = Mock()
        type(browser).page_source = property(lambda self: _raise(WebDriverException("gone")))

        with patch.object(app, 'debug_dir', str(tmp_path)):
            app.dump_diagnostics(browser, 'failure')  # must not raise


class TestDataParsing:
    """Tests for data parsing and formatting"""

    def test_total_parsing(self):
        """Test parsing of total value with comma decimal separator"""
        from app import _parse_decimal

        assert _parse_decimal("234,32") == 234.32

    def test_total_parsing_with_thousand_separator(self):
        from app import _parse_decimal

        assert _parse_decimal("1.234,32") == 1234.32

    def test_total_parsing_without_decimals(self):
        from app import _parse_decimal

        assert _parse_decimal("234") == 234.0

    def test_meter_id_parsing(self):
        """Test parsing of meter ID"""
        test_value = "23522852"
        parsed = int(test_value)
        assert parsed == 23522852

    def test_datetime_parsing(self):
        """Test parsing of datetime with custom format"""
        test_value = "kl. 18.58, d. 07.10.2024"
        datetime_format = 'kl. %H.%M, d. %d.%m.%Y'

        parsed_date = datetime.strptime(test_value, datetime_format)
        formatted_date = datetime.strftime(parsed_date, "%Y-%m-%d %H:%M:%S")

        assert formatted_date == "2024-10-07 18:58:00"

    def test_format_to_regex_matches_the_same_text(self):
        import re
        from app import _format_to_regex

        pattern = _format_to_regex('kl. %H.%M, d. %d.%m.%Y')
        match = re.search(pattern, 'Aflæst kl. 18.58, d. 07.10.2024 i alt')

        assert match.group(0) == 'kl. 18.58, d. 07.10.2024'

    def test_mqtt_message_structure(self):
        """Test MQTT message JSON structure"""
        msg_dict = {
            "total": 234.32,
            "meter_id": 23522852,
            "timestamp": "2024-10-07 18:58:00"
        }

        mqtt_msg = json.dumps(msg_dict)
        parsed = json.loads(mqtt_msg)

        assert parsed["total"] == 234.32
        assert parsed["meter_id"] == 23522852
        assert parsed["timestamp"] == "2024-10-07 18:58:00"


class TestConfiguration:
    """Tests for configuration and environment variables"""

    def test_mqtt_auth_with_credentials(self):
        """Test MQTT auth dict is created when credentials are provided"""
        import app

        assert hasattr(app, 'mqtt_auth')

    def test_mqtt_client_id_format(self):
        """Test MQTT client ID has correct format"""
        import app

        assert app.mqtt_client_id.startswith('python-mqtt-')
        assert len(app.mqtt_client_id) > len('python-mqtt-')

    def test_environment_variable_defaults(self):
        """Test default values for optional environment variables"""
        import app

        assert app.mqtt_port == 1883 or isinstance(app.mqtt_port, int)
        assert hasattr(app, 'mqtt_topic')
        assert hasattr(app, 'webdriver_remote_url')
        assert hasattr(app, 'datetime_format')

    def test_resilience_defaults(self):
        """Test the resilience settings have sane defaults"""
        import app

        assert app._run_timer == 60 * 60
        assert app.retry_interval < app._run_timer
        assert app.max_attempts >= 1
        assert app.page_load_timeout > 0


class TestBrowserOptions:
    """Tests for browser configuration"""

    @patch('app.webdriver')
    def test_chrome_options_incognito(self, mock_webdriver):
        """Test Chrome browser is configured with incognito mode"""
        import app

        mock_chrome_options = Mock()
        mock_webdriver.ChromeOptions.return_value = mock_chrome_options

        app.create_browser()

        mock_webdriver.ChromeOptions.assert_called_once()
        mock_chrome_options.add_argument.assert_called_once_with("--incognito")

    @patch('app.webdriver')
    def test_browser_timeouts_are_set(self, mock_webdriver):
        """Page load timeouts keep a hanging page from stalling the job"""
        import app

        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser

        app.create_browser()

        mock_browser.set_page_load_timeout.assert_called_once_with(app.page_load_timeout)
        mock_browser.set_script_timeout.assert_called_once_with(app.page_load_timeout)


class TestMainLoop:
    """Tests for the run loop"""

    @patch('app.sleep')
    @patch('app.scrape')
    def test_loop_uses_the_short_interval_after_a_failure(self, mock_scrape, mock_sleep):
        import app

        mock_scrape.side_effect = [None, KeyboardInterrupt]

        with pytest.raises(KeyboardInterrupt):
            app.main()

        mock_sleep.assert_called_once_with(app.retry_interval)

    @patch('app.sleep')
    @patch('app.scrape')
    def test_loop_uses_the_normal_interval_after_a_success(self, mock_scrape, mock_sleep):
        import app

        mock_scrape.side_effect = [{'total': 1}, KeyboardInterrupt]

        with pytest.raises(KeyboardInterrupt):
            app.main()

        mock_sleep.assert_called_once_with(app._run_timer)

    @patch('app.sleep')
    @patch('app.scrape')
    def test_loop_survives_an_unexpected_error(self, mock_scrape, mock_sleep):
        import app

        mock_scrape.side_effect = [RuntimeError("boom"), KeyboardInterrupt]

        with pytest.raises(KeyboardInterrupt):
            app.main()

        mock_sleep.assert_called_once_with(app.retry_interval)
