"""
Integration tests for the minvandforsyningdk-scraper.

These tests require a playwright browser and a MQTT broker to be available.
They are marked with pytest.mark.integration and are skipped if either is missing.
"""
import pytest
import json
import time
import os
from unittest.mock import patch

# Timeout constants
SUBSCRIPTION_WAIT = 0.5  # Time to wait for MQTT subscription to be established
POLL_INTERVAL = 0.1  # Interval for polling loops
MESSAGE_TIMEOUT = 5  # Timeout for waiting for messages
CONNECTION_TIMEOUT = 5  # Timeout for connection attempts

# Set up environment variables before importing app
os.environ.setdefault('mqtt-broker', 'localhost')
os.environ.setdefault('username', 'test-user')
os.environ.setdefault('password', 'test-pass')


CI = os.environ.get('CI') == 'true'


def is_browser_available():
    """Check if a playwright browser is installed."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            import app
            app.open_browser(playwright).close()
        return True
    except Exception:
        return False


def is_mqtt_available():
    """Check if MQTT broker is available."""
    try:
        import socket
        host = os.environ.get('mqtt-broker', 'localhost')
        port = int(os.environ.get('mqtt-port', 1883))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CONNECTION_TIMEOUT)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


# Skip markers for integration tests
# In CI the checks are skipped and the tests run regardless: a missing browser
# or broker has to fail there, a silent skip looks like a pass.
requires_browser = pytest.mark.skipif(
    not CI and not is_browser_available(),
    reason="No playwright browser is installed"
)
requires_mqtt = pytest.mark.skipif(
    not CI and not is_mqtt_available(),
    reason="MQTT broker is not available"
)

integration = pytest.mark.integration


@pytest.fixture
def page():
    """Pytest fixture giving a page in a freshly launched browser."""
    from playwright.sync_api import sync_playwright
    import app

    with sync_playwright() as playwright:
        browser = app.open_browser(playwright)
        context = browser.new_context()
        yield context.new_page()
        context.close()
        browser.close()


@pytest.fixture
def mqtt_client():
    """Pytest fixture for creating an MQTT client."""
    import paho.mqtt.client as mqtt
    
    broker = os.environ.get('mqtt-broker', 'localhost')
    port = int(os.environ.get('mqtt-port', 1883))
    
    connected = []
    
    def on_connect(client, userdata, flags, reason_code, properties=None):
        connected.append(True)
    
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.connect(broker, port, 60)
    client.loop_start()
    
    # Wait for connection to be established
    start = time.time()
    while not connected and (time.time() - start) < CONNECTION_TIMEOUT:
        time.sleep(POLL_INTERVAL)
    
    yield client
    
    client.loop_stop()
    client.disconnect()


@integration
@requires_browser
class TestBrowserIntegration:
    """Integration tests that verify the browser and the selector helpers."""

    def test_browser_navigation(self, page):
        """Test the browser can load a page and read content from it."""
        page.set_content("<html><body><h1 id='test'>Hello World</h1></body></html>")

        assert page.locator('#test').inner_text() == "Hello World"

    def test_find_uses_the_first_matching_selector(self, page):
        """The preferred selector wins when it is on the page."""
        import app

        page.set_content(
            "<html><body><input id='signInName' value='primary'>"
            "<input type='email' value='fallback'></body></html>")

        assert app.find(page, 'username').input_value() == 'primary'

    def test_find_falls_back_when_the_id_changed(self, page):
        """A renamed id must not stop the scraper."""
        import app

        page.set_content("<html><body><input type='email' value='fallback'></body></html>")

        assert app.find(page, 'username').input_value() == 'fallback'

    def test_find_raises_when_the_element_is_gone(self, page):
        """The error names the variable that fixes it."""
        import app

        page.set_content("<html><body><p>nothing here</p></body></html>")

        with pytest.raises(app.ElementNotFoundError) as error:
            app.find(page, 'username', timeout=2)

        assert 'selector-username' in str(error.value)

    def test_reads_the_values_off_a_page(self, page):
        """The default selectors match the shape the site uses."""
        import app

        page.set_content(
            "<html><body><div>"
            "<span><b>23522852</b></span>"
            "<span><b>kl. 18.58, d. 07.10.2024</b><b>1.234,50</b></span>"
            "</div></body></html>")

        assert app.read_values(page, timeout=5) == {
            'total': 1234.50,
            'meter_id': 23522852,
            'timestamp': '2024-10-07 18:58:00',
            'timestamp_iso': '2024-10-07T18:58:00+02:00',
        }

    def test_reads_the_values_from_a_completely_new_layout(self, page):
        """When no selector matches, the values come out of the page text."""
        import app

        page.set_content(
            "<html><body><main><p>Måler nr. 23522852</p>"
            "<p>Forbrug i alt 1.234,50 m³</p>"
            "<p>Aflæst kl. 18.58, d. 07.10.2024</p></main></body></html>")

        values = app.read_values(page, timeout=2)

        assert values['total'] == 1234.50
        assert values['meter_id'] == 23522852
        assert values['timestamp'] == '2024-10-07 18:58:00'

    def test_a_login_form_is_filled_and_submitted(self, page):
        """The login steps work against a form with the shape the site uses."""
        import app

        page.set_content(
            "<html><body><form>"
            "<input id='signInName'><input type='password'>"
            "<button id='next' type='button' "
            "onclick=\"document.title='submitted'\">Log ind</button>"
            "</form></body></html>")

        app.find(page, 'username').fill('a-user')
        app.find(page, 'password').fill('a-password')
        app.click(page, 'submit')

        assert page.locator('#signInName').input_value() == 'a-user'
        assert page.title() == 'submitted'


@integration
@requires_mqtt
class TestMQTTIntegration:
    """Integration tests that verify MQTT broker connectivity."""
    
    def test_mqtt_connection(self, mqtt_client):
        """Test that we can connect to the MQTT broker."""
        # If we get here, the fixture successfully connected
        assert mqtt_client.is_connected()
    
    def test_mqtt_publish_subscribe(self, mqtt_client):
        """Test publishing and subscribing to MQTT messages."""
        test_topic = 'test/integration/minvandforsyning'
        test_message = json.dumps({"test": "data", "value": 123.45})
        
        received_messages = []
        
        def on_message(client, userdata, msg):
            received_messages.append(msg.payload.decode())
        
        mqtt_client.on_message = on_message
        mqtt_client.subscribe(test_topic)
        
        # Wait for subscription
        time.sleep(SUBSCRIPTION_WAIT)
        
        # Publish message
        mqtt_client.publish(test_topic, test_message)
        
        # Wait for message
        start = time.time()
        while not received_messages and (time.time() - start) < MESSAGE_TIMEOUT:
            time.sleep(POLL_INTERVAL)
        
        assert len(received_messages) == 1
        assert json.loads(received_messages[0]) == {"test": "data", "value": 123.45}
    
    def test_mqtt_message_format(self, mqtt_client):
        """Test that MQTT messages match expected format."""
        from paho.mqtt.publish import single as publish
        
        broker = os.environ.get('mqtt-broker', 'localhost')
        port = int(os.environ.get('mqtt-port', 1883))
        test_topic = 'test/integration/message_format'
        
        # Create message in same format as app.py
        mqtt_msg = json.dumps({
            "total": 234.32,
            "meter_id": 23522852,
            "timestamp": "2024-10-07 18:58:00"
        })
        
        received_messages = []
        
        def on_message(client, userdata, msg):
            received_messages.append(msg.payload.decode())
        
        mqtt_client.on_message = on_message
        mqtt_client.subscribe(test_topic)
        
        # Wait for subscription
        time.sleep(SUBSCRIPTION_WAIT)
        
        # Publish using same method as app.py
        publish(test_topic, mqtt_msg, hostname=broker, port=port)
        
        # Wait for message
        start = time.time()
        while not received_messages and (time.time() - start) < MESSAGE_TIMEOUT:
            time.sleep(POLL_INTERVAL)
        
        assert len(received_messages) == 1
        parsed = json.loads(received_messages[0])
        assert parsed['total'] == 234.32
        assert parsed['meter_id'] == 23522852
        assert parsed['timestamp'] == "2024-10-07 18:58:00"


@integration
@requires_mqtt
class TestDiscoveryIntegration:
    """Integration tests for the Home Assistant discovery messages."""

    def test_discovery_configs_are_retained_on_the_broker(self, mqtt_client):
        """A restarted Home Assistant must still find the entities."""
        import app

        app._announced_meters.clear()
        with_prefix = 'test/homeassistant'
        with patch.object(app, 'discovery_prefix', with_prefix), \
                patch.object(app, 'mqtt_broker', os.environ.get('mqtt-broker', 'localhost')):
            app.publish_discovery(23522852)

        # a client that connects afterwards still receives the retained configs
        received = {}

        def on_message(client, userdata, msg):
            if not msg.payload:  # an empty payload clears a retained message
                return
            received[msg.topic] = json.loads(msg.payload.decode())

        mqtt_client.on_message = on_message
        mqtt_client.subscribe(f'{with_prefix}/sensor/#')

        start = time.time()
        while len(received) < 3 and (time.time() - start) < MESSAGE_TIMEOUT:
            time.sleep(POLL_INTERVAL)

        assert len(received) == 3
        total = received[f'{with_prefix}/sensor/minvandforsyning_23522852/total/config']
        assert total['device_class'] == 'water'
        assert total['state_class'] == 'total_increasing'
        assert total['unique_id'] == 'minvandforsyning_23522852_total'

        # clean up the retained messages so the broker does not keep them
        for topic in received:
            mqtt_client.publish(topic, '', retain=True)
        time.sleep(SUBSCRIPTION_WAIT)

    def test_a_published_reading_matches_the_discovery_templates(self, mqtt_client):
        """The value_templates must line up with the payload we publish."""
        import app

        received = []

        def on_message(client, userdata, msg):
            received.append(json.loads(msg.payload.decode()))

        test_topic = 'test/integration/discovery_reading'
        mqtt_client.on_message = on_message
        mqtt_client.subscribe(test_topic)
        time.sleep(SUBSCRIPTION_WAIT)

        payload = {
            "total": 234.32,
            "meter_id": 23522852,
            "timestamp": "2024-10-07 18:58:00",
            "timestamp_iso": "2024-10-07T18:58:00+02:00",
        }
        with patch.object(app, 'mqtt_broker', os.environ.get('mqtt-broker', 'localhost')):
            assert app.publish_message(test_topic, json.dumps(payload)) is True

        start = time.time()
        while not received and (time.time() - start) < MESSAGE_TIMEOUT:
            time.sleep(POLL_INTERVAL)

        assert len(received) == 1
        # every value_template in the discovery config reads a key we publish
        for _, config in app.discovery_config(23522852):
            key = config['value_template'].strip('{} ').split('.')[-1].strip()
            assert key in received[0], f"{key} is missing from the published reading"


@pytest.fixture
def fake_site(tmp_path):
    """Serve a small copy of the site: picker -> login form -> dashboard."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import threading

    (tmp_path / 'index.html').write_text(
        "<html><head><meta charset='utf-8'></head><body><div><div>"
        "<div>a</div><div><div>x</div><div>y</div><div>"
        "<button onclick=\"location.href='login.html'\"><span><p>"
        "Log ind med Rambøll konto</p></span></button>"
        "</div></div></div></div></body></html>", encoding='utf-8')
    (tmp_path / 'login.html').write_text(
        "<html><head><meta charset='utf-8'></head><body>"
        "<form action='dashboard.html'>"
        "<input id='signInName' name='signInName'>"
        "<input type='password' name='password'>"
        "<button id='next' type='submit'>Log ind</button>"
        "</form></body></html>", encoding='utf-8')
    (tmp_path / 'dashboard.html').write_text(
        "<html><head><meta charset='utf-8'></head><body><div>"
        "<span><b>23522852</b></span>"
        "<span><b>kl. 18.58, d. 07.10.2024</b><b>1.234,50</b></span>"
        "</div></body></html>", encoding='utf-8')

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp_path), **kwargs)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}/index.html'
    server.shutdown()


@integration
@requires_browser
@requires_mqtt
class TestEndToEndIntegration:
    """The whole run: browser, login, reading, discovery and mqtt."""

    def test_a_full_run_publishes_a_reading(self, fake_site, mqtt_client):
        import app

        received = {}

        def on_message(client, userdata, msg):
            if msg.payload:
                received[msg.topic] = msg.payload.decode()

        mqtt_client.on_message = on_message
        mqtt_client.subscribe('test/e2e/#')
        time.sleep(SUBSCRIPTION_WAIT)

        app._announced_meters.clear()
        # the picker button sits at a different path in this copy of the site
        selectors = {**app.SELECTORS, 'login-provider': ['role=button[name=/Ramb/i]']}

        with patch.object(app, 'login_url', fake_site), \
                patch.object(app, 'SELECTORS', selectors), \
                patch.object(app, 'mqtt_broker', os.environ.get('mqtt-broker', 'localhost')), \
                patch.object(app, 'mqtt_topic', 'test/e2e/total'), \
                patch.object(app, 'mqtt_status_topic', 'test/e2e/status'), \
                patch.object(app, 'discovery_prefix', 'test/e2e/homeassistant'), \
                patch.object(app, 'form_settle_delay', 0):
            values = app.scrape_once()

        assert values['total'] == 1234.50
        assert values['meter_id'] == 23522852
        assert values['timestamp'] == '2024-10-07 18:58:00'

        start = time.time()
        while len(received) < 5 and (time.time() - start) < MESSAGE_TIMEOUT:
            time.sleep(POLL_INTERVAL)

        # the reading, the availability and one discovery config per entity
        assert json.loads(received['test/e2e/total'])['total'] == 1234.50
        assert received['test/e2e/status'] == 'online'
        configs = [topic for topic in received if topic.endswith('/config')]
        assert len(configs) == 3

        for topic in received:
            mqtt_client.publish(topic, '', retain=True)
        time.sleep(SUBSCRIPTION_WAIT)

    def test_a_failed_run_writes_diagnostics(self, fake_site, tmp_path):
        """A layout change must leave something behind to look at."""
        import app

        debug_dir = tmp_path / 'debug'
        with patch.object(app, 'login_url', fake_site), \
                patch.object(app, 'SELECTORS', {**app.SELECTORS,
                                                'login-provider': ['#gone-for-good']}), \
                patch.object(app, 'debug_dir', str(debug_dir)), \
                patch.object(app, 'element_timeout', 2):
            with pytest.raises(app.ElementNotFoundError):
                app.scrape_once()

        assert len(list(debug_dir.glob('*-failure.html'))) == 1
        assert len(list(debug_dir.glob('*-failure.png'))) == 1
        assert len(list(debug_dir.glob('*-failure-trace.zip'))) == 1
