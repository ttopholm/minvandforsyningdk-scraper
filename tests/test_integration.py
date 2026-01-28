"""
Integration tests for the minvandforsyningdk-scraper.

These tests require Docker services (Selenium and MQTT broker) to be running.
They are marked with pytest.mark.integration and are skipped if services are not available.
"""
import pytest
import json
import time
import os

# Timeout constants
SUBSCRIPTION_WAIT = 0.5  # Time to wait for MQTT subscription to be established
POLL_INTERVAL = 0.1  # Interval for polling loops
MESSAGE_TIMEOUT = 5  # Timeout for waiting for messages
CONNECTION_TIMEOUT = 5  # Timeout for connection attempts

# Set up environment variables before importing app
os.environ.setdefault('mqtt-broker', 'localhost')
os.environ.setdefault('username', 'test-user')
os.environ.setdefault('password', 'test-pass')
os.environ.setdefault('webdriver-remote-url', 'http://localhost:4444')


def is_selenium_available():
    """Check if Selenium WebDriver is available."""
    try:
        import urllib.request
        url = os.environ.get('webdriver-remote-url', 'http://localhost:4444')
        urllib.request.urlopen(f"{url}/status", timeout=CONNECTION_TIMEOUT)
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
requires_selenium = pytest.mark.skipif(
    not is_selenium_available(),
    reason="Selenium WebDriver is not available"
)
requires_mqtt = pytest.mark.skipif(
    not is_mqtt_available(),
    reason="MQTT broker is not available"
)

integration = pytest.mark.integration


@pytest.fixture
def browser():
    """Pytest fixture for creating a Selenium WebDriver instance."""
    from selenium import webdriver
    
    webdriver_url = os.environ.get('webdriver-remote-url', 'http://localhost:4444')
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--incognito")
    chrome_options.add_argument("--headless")
    
    driver = webdriver.Remote(webdriver_url, options=chrome_options)
    yield driver
    driver.quit()


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
@requires_selenium
class TestSeleniumIntegration:
    """Integration tests that verify Selenium WebDriver connectivity."""
    
    def test_selenium_connection(self, browser):
        """Test that we can connect to the Selenium WebDriver."""
        browser.get("about:blank")
        assert browser.title is not None
    
    def test_browser_navigation(self, browser):
        """Test browser can navigate to a URL and extract content."""
        from selenium.webdriver.common.by import By
        
        test_html = "data:text/html,<html><body><h1 id='test'>Hello World</h1></body></html>"
        browser.get(test_html)
        
        element = browser.find_element(By.ID, "test")
        assert element.text == "Hello World"
    
    def test_wait_for_element_integration(self, browser):
        """Integration test for wait_for_element function."""
        from app import wait_for_element
        
        test_html = "data:text/html,<html><body><div id='myElement'>Content</div></body></html>"
        browser.get(test_html)
        
        result = wait_for_element(browser, '//*[@id="myElement"]', timeout=5)
        assert result is True
    
    def test_wait_for_element_timeout_integration(self, browser):
        """Integration test for wait_for_element timeout."""
        from app import wait_for_element
        
        test_html = "data:text/html,<html><body><div id='otherElement'>Content</div></body></html>"
        browser.get(test_html)
        
        result = wait_for_element(browser, '//*[@id="nonExistentElement"]', timeout=2)
        assert result is None


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
@requires_selenium
@requires_mqtt
class TestEndToEndIntegration:
    """End-to-end integration tests combining Selenium and MQTT."""
    
    def test_browser_to_mqtt_flow(self, browser, mqtt_client):
        """Test extracting data from browser and publishing to MQTT."""
        from selenium.webdriver.common.by import By
        from paho.mqtt.publish import single as publish
        
        broker = os.environ.get('mqtt-broker', 'localhost')
        port = int(os.environ.get('mqtt-port', 1883))
        test_topic = 'test/integration/e2e'
        
        # Set up MQTT subscriber
        received_messages = []
        
        def on_message(client, userdata, msg):
            received_messages.append(msg.payload.decode())
        
        mqtt_client.on_message = on_message
        mqtt_client.subscribe(test_topic)
        
        # Wait for subscription
        time.sleep(SUBSCRIPTION_WAIT)
        
        # Simulate data extraction from a web page
        test_html = """data:text/html,<html><body>
            <span id="total">234,32</span>
            <span id="meter">23522852</span>
        </body></html>"""
        browser.get(test_html)
        
        total_text = browser.find_element(By.ID, "total").text
        meter_text = browser.find_element(By.ID, "meter").text
        
        total = float(total_text.replace(',', '.'))
        meter_id = int(meter_text)
        
        # Publish extracted data to MQTT
        mqtt_msg = json.dumps({
            "total": total,
            "meter_id": meter_id,
            "timestamp": "2024-01-28 12:00:00"
        })
        publish(test_topic, mqtt_msg, hostname=broker, port=port)
        
        # Wait for message
        start = time.time()
        while not received_messages and (time.time() - start) < MESSAGE_TIMEOUT:
            time.sleep(POLL_INTERVAL)
        
        assert len(received_messages) == 1
        parsed = json.loads(received_messages[0])
        assert parsed['total'] == 234.32
        assert parsed['meter_id'] == 23522852
    
    def test_data_parsing_integration(self, browser):
        """Test data parsing matches expected format in real browser."""
        from selenium.webdriver.common.by import By
        from datetime import datetime
        
        # Test that we can parse data in the expected format
        # Using IDs for clarity in test (the actual app uses XPaths)
        test_html = """data:text/html,<html><body>
            <span id="total">234,32</span>
            <span id="meter">23522852</span>
            <span id="date">kl. 18.58, d. 07.10.2024</span>
        </body></html>"""
        browser.get(test_html)
        
        # Extract data
        total_text = browser.find_element(By.ID, 'total').text
        meter_text = browser.find_element(By.ID, 'meter').text
        date_text = browser.find_element(By.ID, 'date').text
        
        # Parse using same logic as app.py
        total = float(total_text.replace(',', '.'))
        meter_id = int(meter_text)
        datetime_format = 'kl. %H.%M, d. %d.%m.%Y'
        parsed_date = datetime.strptime(date_text, datetime_format)
        formatted_date = datetime.strftime(parsed_date, "%Y-%m-%d %H:%M:%S")
        
        assert total == 234.32
        assert meter_id == 23522852
        assert formatted_date == "2024-10-07 18:58:00"
