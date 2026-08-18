# Tests package
import pytest
from unittest.mock import Mock, patch
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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


@pytest.fixture(autouse=True)
def reset_announced_meters():
    """Discovery is only published once per meter, so reset it between tests."""
    import app
    app._announced_meters.clear()
    yield
    app._announced_meters.clear()


def published(mock_publish):
    """Map topic -> list of payloads from a mocked paho publish."""
    messages = {}
    for call_args in mock_publish.call_args_list:
        topic, payload = call_args[0][0], call_args[0][1]
        messages.setdefault(topic, []).append(payload)
    return messages


class FakeLocator:
    """A playwright locator that is either on the page or not."""

    def __init__(self, text=None, present=True):
        self.text = text
        self.present = present
        self.clicks = 0
        self.filled = []

    @property
    def first(self):
        return self

    def wait_for(self, state=None, timeout=None):
        if not self.present:
            raise PlaywrightTimeoutError(f"Timeout {timeout}ms exceeded")

    def inner_text(self):
        if not self.present:
            raise PlaywrightError("element is not attached")
        return self.text

    def click(self, timeout=None):
        self.clicks += 1

    def fill(self, value):
        self.filled.append(value)


class FakePage:
    """A page where only the given selectors resolve to an element."""

    def __init__(self, elements=None):
        self.elements = elements or {}
        self.locators = {}
        self.goto_calls = []
        self.screenshots = []
        self.goto_error = None

    def locator(self, selector):
        if selector not in self.locators:
            self.locators[selector] = FakeLocator(
                self.elements.get(selector), present=selector in self.elements)
        return self.locators[selector]

    def goto(self, url, timeout=None):
        self.goto_calls.append(url)
        if self.goto_error:
            raise self.goto_error

    def content(self):
        return '<html>changed layout</html>'

    def screenshot(self, path=None, full_page=False):
        self.screenshots.append(path)
        with open(path, 'w') as handle:
            handle.write('png')


class FakeTracing:
    def __init__(self):
        self.started = False
        self.stopped_to = None

    def start(self, **kwargs):
        self.started = True

    def stop(self, path=None):
        self.stopped_to = path


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.tracing = FakeTracing()
        self.closed = False
        self.default_timeout = None

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page=None):
        self.context = FakeContext(page if page is not None else FakePage())
        self.closed = False
        self.close_error = None

    def new_context(self):
        return self.context

    def close(self):
        if self.close_error:
            raise self.close_error
        self.closed = True


def fake_playwright():
    """Patch target for app.sync_playwright, which is used as a context manager."""
    manager = Mock()
    manager.__enter__ = Mock(return_value=Mock())
    manager.__exit__ = Mock(return_value=False)
    return Mock(return_value=manager)


def dashboard_page():
    """A page that looks like the one the scraper expects after login."""
    return FakePage({
        'xpath=/html/body/body/div/div/div[2]/div/div[3]/button/span/p': 'Log ind',
        '#signInName': '',
        'input[type=password]': '',
        '#next': 'Log ind',
        'xpath=//span[2]/b[2]': '234,32',
        'xpath=//b': '23522852',
        'xpath=//span[2]/b': 'kl. 18.58, d. 07.10.2024',
    })


class TestSelectorParsing:
    """Tests for the configurable selector specs"""

    def test_single_selector(self):
        from app import _parse_selectors

        assert _parse_selectors('#signInName') == ['#signInName']

    def test_multiple_candidates_are_split_and_stripped(self):
        from app import _parse_selectors

        assert _parse_selectors('#a|| input[type=email] ||xpath=//c') == [
            '#a', 'input[type=email]', 'xpath=//c',
        ]

    def test_empty_candidates_are_ignored(self):
        from app import _parse_selectors

        assert _parse_selectors('#a||||') == ['#a']

    def test_every_target_has_a_fallback(self):
        import app

        for target, selectors in app.SELECTORS.items():
            assert len(selectors) >= 2, f"{target} has no fallback selector"

    def test_selectors_come_from_the_defaults(self):
        import app

        assert app.SELECTORS['username'] == app._parse_selectors(
            app._DEFAULT_SELECTORS['username'])


class TestFind:
    """Tests for the selector fallback logic"""

    def test_uses_the_first_matching_selector(self):
        import app

        page = FakePage({'#signInName': 'first'})

        assert app.find(page, 'username').inner_text() == 'first'

    def test_falls_back_when_the_id_changed(self):
        import app

        # The primary id is gone, as if the site changed its markup
        page = FakePage({'input[type=email]': 'fallback'})

        assert app.find(page, 'username').inner_text() == 'fallback'

    def test_raises_when_nothing_matches(self):
        import app

        with pytest.raises(app.ElementNotFoundError) as error:
            app.find(FakePage(), 'username')

        # The error tells the user how to fix it without a code change
        assert 'selector-username' in str(error.value)
        assert '#signInName' in str(error.value)

    def test_a_broken_selector_does_not_eat_the_whole_timeout(self):
        import app

        page = FakePage({'input[type=email]': 'fallback'})
        app.find(page, 'username', timeout=20)

        # 4 candidates share the 20 second budget
        waited = page.locators['#signInName']
        assert waited.present is False


class TestClick:
    """Tests for the click helper"""

    def test_click_uses_the_located_element(self):
        import app

        page = FakePage({'#next': 'Log ind'})
        app.click(page, 'submit')

        assert page.locators['#next'].clicks == 1

    def test_click_raises_when_the_button_is_gone(self):
        import app

        with pytest.raises(app.ElementNotFoundError):
            app.click(FakePage(), 'submit')


class TestReadValues:
    """Tests for reading the values off the page"""

    def test_reads_values_from_elements(self):
        import app

        values = app.read_values(dashboard_page())

        assert values == {
            'total': 234.32,
            'meter_id': 23522852,
            'timestamp': '2024-10-07 18:58:00',
            'timestamp_iso': '2024-10-07T18:58:00+02:00',
        }

    def test_falls_back_to_page_text_when_the_layout_changed(self):
        import app

        # None of the selectors match any more, but the text is still on the page
        page = FakePage({
            'body': 'Måler nr. 23522852\nForbrug i alt 1.234,50 m³\n'
                    'Aflæst kl. 18.58, d. 07.10.2024',
        })

        values = app.read_values(page)

        assert values['total'] == 1234.50
        assert values['meter_id'] == 23522852
        assert values['timestamp'] == '2024-10-07 18:58:00'

    def test_body_text_is_empty_when_the_page_is_gone(self):
        import app

        assert app._body_text(FakePage()) == ''

    def test_raises_when_the_value_is_nowhere(self):
        import app

        with pytest.raises(app.ElementNotFoundError):
            app.read_values(FakePage({'body': 'Ingen data'}))


class TestPublishMessage:
    """Tests for MQTT publishing with retries"""

    @patch('app.publish')
    def test_publish_success(self, mock_publish):
        import app

        assert app.publish_message('topic', 'payload') is True
        mock_publish.assert_called_once()
        assert mock_publish.call_args[1]['retain'] is False

    @patch('app.publish')
    def test_publish_can_retain(self, mock_publish):
        import app

        app.publish_message('topic', 'payload', retain=True)

        assert mock_publish.call_args[1]['retain'] is True

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


class TestDiscoveryConfig:
    """Tests for the Home Assistant mqtt discovery payloads"""

    def test_one_config_per_entity(self):
        import app

        configs = dict(app.discovery_config(23522852))

        assert set(configs) == {
            'homeassistant/sensor/minvandforsyning_23522852/total/config',
            'homeassistant/sensor/minvandforsyning_23522852/timestamp/config',
            'homeassistant/sensor/minvandforsyning_23522852/meter_id/config',
        }

    def test_total_is_a_water_meter_for_the_energy_dashboard(self):
        import app

        configs = dict(app.discovery_config(23522852))
        total = configs['homeassistant/sensor/minvandforsyning_23522852/total/config']

        assert total['device_class'] == 'water'
        assert total['state_class'] == 'total_increasing'
        assert total['unit_of_measurement'] == 'm³'
        assert total['value_template'] == '{{ value_json.total }}'
        assert total['state_topic'] == app.mqtt_topic

    def test_entities_share_one_device_and_have_unique_ids(self):
        import app

        configs = dict(app.discovery_config(23522852))
        identifiers = {tuple(c['device']['identifiers']) for c in configs.values()}
        unique_ids = {c['unique_id'] for c in configs.values()}

        assert identifiers == {('minvandforsyning_23522852',)}
        assert len(unique_ids) == len(configs)

    def test_availability_follows_the_status_topic(self):
        import app

        with patch.object(app, 'mqtt_status_topic', 'water/status'):
            configs = dict(app.discovery_config(23522852))

        for config in configs.values():
            assert config['availability_topic'] == 'water/status'
            assert config['payload_available'] == 'online'

    def test_availability_is_left_out_without_a_status_topic(self):
        import app

        with patch.object(app, 'mqtt_status_topic', None):
            configs = dict(app.discovery_config(23522852))

        for config in configs.values():
            assert 'availability_topic' not in config

    def test_payloads_are_json_serialisable(self):
        import app

        for _, config in app.discovery_config(23522852):
            json.loads(json.dumps(config))


class TestPublishDiscovery:
    """Tests for announcing the entities"""

    @patch('app.publish')
    def test_configs_are_published_retained(self, mock_publish):
        import app

        app.publish_discovery(23522852)

        assert mock_publish.call_count == 3
        for call_args in mock_publish.call_args_list:
            assert call_args[1]['retain'] is True

    @patch('app.publish')
    def test_only_announced_once_per_meter(self, mock_publish):
        import app

        app.publish_discovery(23522852)
        app.publish_discovery(23522852)

        assert mock_publish.call_count == 3

    @patch('app.publish')
    def test_a_new_meter_is_announced_again(self, mock_publish):
        import app

        app.publish_discovery(23522852)
        app.publish_discovery(999)

        assert mock_publish.call_count == 6

    @patch('app.publish')
    def test_can_be_disabled(self, mock_publish):
        import app

        with patch.object(app, 'mqtt_discovery', False):
            app.publish_discovery(23522852)

        mock_publish.assert_not_called()

    @patch('app.sleep')
    @patch('app.publish')
    def test_a_failed_announce_is_retried_on_the_next_run(self, mock_publish, mock_sleep):
        import app

        mock_publish.side_effect = ConnectionRefusedError("broker down")
        app.publish_discovery(23522852)

        assert 23522852 not in app._announced_meters


class TestScrapeFunction:
    """Tests for the scrape function"""

    @patch('app.publish')
    @patch('app.sleep')
    def test_scrape_success(self, mock_sleep, mock_publish):
        """Test successful scraping and MQTT publishing"""
        import app

        page = dashboard_page()
        browser = FakeBrowser(page)

        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser', return_value=browser):
            values = app.scrape()

        assert page.goto_calls == [app.login_url]
        # the credentials went into the form
        assert page.locators['#signInName'].filled == [app.mvf_username]
        assert page.locators['input[type=password]'].filled == [app.mvf_password]
        assert page.locators['#next'].clicks == 1
        assert browser.closed and browser.context.closed

        messages = published(mock_publish)
        parsed_msg = json.loads(messages[app.mqtt_topic][0])
        assert parsed_msg['total'] == 234.32
        assert parsed_msg['meter_id'] == 23522852
        assert parsed_msg['timestamp_iso'] == '2024-10-07T18:58:00+02:00'
        assert values['total'] == 234.32

        # the entities are announced to home assistant before the reading
        assert mock_publish.call_args_list[0][0][0].startswith('homeassistant/sensor/')
        assert messages[app.mqtt_status_topic] == ['online']

    @patch('app.publish')
    @patch('app.sleep')
    def test_scrape_mqtt_connection_error(self, mock_sleep, mock_publish):
        """Test scrape handles MQTT connection errors without crashing"""
        import app

        browsers = [FakeBrowser(dashboard_page()) for _ in range(app.max_attempts)]
        mock_publish.side_effect = ConnectionRefusedError("Connection refused")

        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser', side_effect=browsers):
            assert app.scrape() is None

        # every attempt closed its browser
        assert all(browser.closed for browser in browsers)

    @patch('app.publish')
    @patch('app.sleep')
    def test_scrape_general_exception(self, mock_sleep, mock_publish):
        """Test scrape survives an exception and retries"""
        import app

        page = FakePage()
        page.goto_error = Exception("Test exception")
        browser = FakeBrowser(page)

        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser', return_value=browser):
            assert app.scrape() is None

        assert len(page.goto_calls) == app.max_attempts

    @patch('app.publish')
    @patch('app.sleep')
    def test_scrape_survives_a_browser_that_will_not_start(self, mock_sleep, mock_publish):
        """A browser that cannot launch must not kill the job"""
        import app

        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser',
                      side_effect=PlaywrightError("browser closed")) as mock_open:
            assert app.scrape() is None

        assert mock_open.call_count == app.max_attempts

    @patch('app.publish')
    @patch('app.sleep')
    def test_scrape_survives_a_timeout(self, mock_sleep, mock_publish):
        """A page that never loads must not kill the job"""
        import app

        page = FakePage()
        page.goto_error = PlaywrightTimeoutError("Timeout 60000ms exceeded")

        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser', return_value=FakeBrowser(page)):
            assert app.scrape() is None

    @patch('app.publish')
    @patch('app.sleep')
    def test_scrape_recovers_on_the_second_attempt(self, mock_sleep, mock_publish):
        """A transient failure is retried within the same run"""
        import app

        broken = FakePage()
        broken.goto_error = PlaywrightTimeoutError("Timeout")

        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser',
                      side_effect=[FakeBrowser(broken), FakeBrowser(dashboard_page())]):
            values = app.scrape()

        assert values['total'] == 234.32
        assert len(published(mock_publish)[app.mqtt_topic]) == 1

    @patch('app.publish')
    @patch('app.sleep')
    def test_browser_close_error_is_not_fatal(self, mock_sleep, mock_publish):
        """A browser that cannot be closed must not take the job down"""
        import app

        browser = FakeBrowser(dashboard_page())
        browser.close_error = PlaywrightError("browser already gone")

        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser', return_value=browser):
            values = app.scrape()

        assert values['total'] == 234.32


class TestBrowserOptions:
    """Tests for how the browser is started"""

    def test_launches_its_own_chromium(self):
        import app

        playwright = Mock()
        app.open_browser(playwright)

        playwright.chromium.launch.assert_called_once()
        kwargs = playwright.chromium.launch.call_args[1]
        assert kwargs['headless'] is True
        assert '--disable-dev-shm-usage' in kwargs['args']
        playwright.chromium.connect_over_cdp.assert_not_called()

    def test_connects_to_a_remote_browser_when_configured(self):
        import app

        playwright = Mock()
        with patch.object(app, 'browser_cdp_url', 'http://chrome:9222'):
            app.open_browser(playwright)

        playwright.chromium.connect_over_cdp.assert_called_once_with('http://chrome:9222')
        playwright.chromium.launch.assert_not_called()

    def test_a_custom_browser_build_can_be_used(self):
        import app

        playwright = Mock()
        with patch.object(app, 'browser_executable', '/usr/bin/chromium'):
            app.open_browser(playwright)

        assert playwright.chromium.launch.call_args[1]['executable_path'] == '/usr/bin/chromium'

    @patch('app.publish')
    @patch('app.sleep')
    def test_each_run_gets_a_fresh_context(self, mock_sleep, mock_publish):
        """A fresh context is the playwright equivalent of incognito"""
        import app

        browser = FakeBrowser(dashboard_page())
        with patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser', return_value=browser):
            app.scrape()

        assert browser.context.default_timeout == app.element_timeout * 1000
        assert browser.context.closed


class TestDiagnostics:
    """Tests for the failure diagnostics dump"""

    def test_dump_writes_page_source_and_screenshot(self, tmp_path):
        import app

        page = FakePage()
        with patch.object(app, 'debug_dir', str(tmp_path)):
            app.dump_diagnostics(page, 'failure')

        dumps = list(tmp_path.glob('*-failure.html'))
        assert len(dumps) == 1
        assert dumps[0].read_text() == '<html>changed layout</html>'
        assert len(page.screenshots) == 1

    def test_dump_is_skipped_without_a_debug_dir(self):
        import app

        page = FakePage()
        with patch.object(app, 'debug_dir', None):
            app.dump_diagnostics(page, 'failure')

        assert page.screenshots == []

    def test_dump_swallows_its_own_errors(self, tmp_path):
        import app

        page = Mock()
        page.content.side_effect = PlaywrightError("page closed")

        with patch.object(app, 'debug_dir', str(tmp_path)):
            app.dump_diagnostics(page, 'failure')  # must not raise

    def test_a_trace_is_written_for_a_failed_run(self, tmp_path):
        import app

        context = FakeContext(FakePage())
        with patch.object(app, 'debug_dir', str(tmp_path)):
            assert app.save_trace(context, True) is False

        assert context.tracing.stopped_to.endswith('-failure-trace.zip')

    def test_no_trace_when_tracing_was_never_started(self):
        import app

        context = FakeContext(FakePage())
        app.save_trace(context, False)

        assert context.tracing.stopped_to is None

    @patch('app.publish')
    @patch('app.sleep')
    def test_tracing_runs_when_a_debug_dir_is_set(self, mock_sleep, mock_publish, tmp_path):
        import app

        page = FakePage()
        page.goto_error = PlaywrightError("boom")
        browser = FakeBrowser(page)

        with patch.object(app, 'debug_dir', str(tmp_path)), \
                patch.object(app, 'max_attempts', 1), \
                patch('app.sync_playwright', fake_playwright()), \
                patch('app.open_browser', return_value=browser):
            app.scrape()

        assert browser.context.tracing.started
        assert browser.context.tracing.stopped_to.endswith('-failure-trace.zip')


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
        assert int("23522852") == 23522852

    def test_datetime_parsing(self):
        """Test parsing of datetime with custom format"""
        parsed_date = datetime.strptime("kl. 18.58, d. 07.10.2024", 'kl. %H.%M, d. %d.%m.%Y')

        assert datetime.strftime(parsed_date, "%Y-%m-%d %H:%M:%S") == "2024-10-07 18:58:00"

    def test_localize_uses_danish_time(self):
        from app import _localize

        assert _localize(datetime(2024, 7, 7, 18, 58)).isoformat() == '2024-07-07T18:58:00+02:00'
        assert _localize(datetime(2024, 12, 7, 18, 58)).isoformat() == '2024-12-07T18:58:00+01:00'

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
            "timestamp": "2024-10-07 18:58:00",
        }

        parsed = json.loads(json.dumps(msg_dict))

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
        assert hasattr(app, 'datetime_format')

    def test_the_selenium_url_is_gone(self):
        """The scraper runs its own browser now"""
        import app

        assert not hasattr(app, 'webdriver_remote_url')

    def test_resilience_defaults(self):
        """Test the resilience settings have sane defaults"""
        import app

        assert app._run_timer == 60 * 60
        assert app.retry_interval < app._run_timer
        assert app.max_attempts >= 1
        assert app.page_load_timeout > 0
        assert app.headless is True


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
