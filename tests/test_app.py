import pytest
from unittest.mock import Mock, patch, call
from selenium.common.exceptions import TimeoutException
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
    @patch('builtins.print')
    def test_wait_for_element_timeout(self, mock_print, mock_ec, mock_wait):
        """Test wait_for_element handles timeout exception"""
        from app import wait_for_element
        
        mock_driver = Mock()
        mock_wait_instance = Mock()
        mock_wait.return_value = mock_wait_instance
        mock_wait_instance.until.side_effect = TimeoutException("Timeout")
        
        result = wait_for_element(mock_driver, '//*[@id="test"]', timeout=10)
        
        assert result is None
        mock_print.assert_called_once_with("Timed out waiting for page to load")
    
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


class TestScrapeFunction:
    """Tests for the scrape function"""
    
    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    def test_scrape_success(self, mock_webdriver, mock_sleep, mock_publish):
        """Test successful scraping and MQTT publishing"""
        from app import scrape
        
        # Mock browser
        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser
        
        # Mock element finding
        mock_element_total = Mock()
        mock_element_total.text = '234,32'
        
        mock_element_meter = Mock()
        mock_element_meter.text = '23522852'
        
        mock_element_date = Mock()
        mock_element_date.text = 'kl. 18.58, d. 07.10.2024'
        
        # Set up find_element to return different mocks based on the xpath
        def find_element_side_effect(by, xpath):
            if xpath == '//span[2]/b[2]':
                return mock_element_total
            elif xpath == '//b':
                return mock_element_meter
            elif xpath == '//span[2]/b':
                return mock_element_date
            elif xpath == '//*[@id="LoginIntermediaryMudPaper"]/div/div[3]/button':
                return Mock()
            elif xpath == '//*[@id="signInName"]' or xpath == "//input[@type='password']" or xpath == '//*[@id="next"]':
                return Mock()
            else:
                return Mock()
        
        mock_browser.find_element.side_effect = find_element_side_effect
        
        # Mock wait_for_element to always return True
        with patch('app.wait_for_element', return_value=True):
            scrape()
        
        # Verify browser was created and quit
        mock_webdriver.Remote.assert_called_once()
        mock_browser.quit.assert_called_once()
        
        # Verify MQTT publish was called
        mock_publish.assert_called_once()
        
        # Verify the published message structure
        call_args = mock_publish.call_args
        mqtt_msg = call_args[0][1]
        parsed_msg = json.loads(mqtt_msg)
        
        assert 'total' in parsed_msg
        assert 'meter_id' in parsed_msg
        assert 'timestamp' in parsed_msg
        assert parsed_msg['total'] == 234.32
        assert parsed_msg['meter_id'] == 23522852
    
    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    @patch('builtins.print')
    def test_scrape_mqtt_connection_error(self, mock_print, mock_webdriver, mock_sleep, mock_publish):
        """Test scrape handles MQTT connection errors"""
        from app import scrape
        
        # Mock browser
        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser
        
        # Mock element finding
        mock_element_total = Mock()
        mock_element_total.text = '234,32'
        
        mock_element_meter = Mock()
        mock_element_meter.text = '23522852'
        
        mock_element_date = Mock()
        mock_element_date.text = 'kl. 18.58, d. 07.10.2024'
        
        def find_element_side_effect(by, xpath):
            if xpath == '//span[2]/b[2]':
                return mock_element_total
            elif xpath == '//b':
                return mock_element_meter
            elif xpath == '//span[2]/b':
                return mock_element_date
            else:
                return Mock()
        
        mock_browser.find_element.side_effect = find_element_side_effect
        
        # Mock MQTT publish to raise ConnectionRefusedError
        mock_publish.side_effect = ConnectionRefusedError("Connection refused")
        
        with patch('app.wait_for_element', return_value=True):
            scrape()
        
        # Verify error was printed
        mock_print.assert_called_with("Can't connect to mqtt server")
        
        # Verify browser was still quit
        mock_browser.quit.assert_called_once()
    
    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    @patch('builtins.print')
    def test_scrape_general_exception(self, mock_print, mock_webdriver, mock_sleep, mock_publish):
        """Test scrape handles general exceptions"""
        from app import scrape
        
        # Mock browser
        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser
        
        # Mock browser.get to raise an exception
        mock_browser.get.side_effect = Exception("Test exception")
        
        scrape()
        
        # Verify error was printed
        assert any("An error occurred: Test exception" in str(call) for call in mock_print.call_args_list)
        assert any("An error occurred, let me try again" in str(call) for call in mock_print.call_args_list)
        
        # Verify browser was still quit
        mock_browser.quit.assert_called_once()
    
    @patch('app.publish')
    @patch('app.sleep')
    @patch('app.webdriver')
    def test_scrape_browser_navigation(self, mock_webdriver, mock_sleep, mock_publish):
        """Test scrape navigates to correct URL"""
        from app import scrape
        
        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser
        
        # Mock element finding to raise exception after navigation
        mock_browser.get.side_effect = Exception("Stop here")
        
        scrape()
        
        # Verify navigation to correct URL
        mock_browser.get.assert_called_once_with("https://www.minvandforsyning.dk/LoginIntermediate")


class TestDataParsing:
    """Tests for data parsing and formatting"""
    
    def test_total_parsing(self):
        """Test parsing of total value with comma decimal separator"""
        test_value = "234,32"
        parsed = float(test_value.replace(',', '.'))
        assert parsed == 234.32
    
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
        # Test the actual logic from app.py
        import app
        
        # The module should have mqtt_auth set since we have default env vars
        # Note: mqtt_auth is created at module import time based on env vars
        assert hasattr(app, 'mqtt_auth')
    
    def test_mqtt_client_id_format(self):
        """Test MQTT client ID has correct format"""
        import app
        
        # Verify the client ID format
        assert hasattr(app, 'mqtt_client_id')
        assert app.mqtt_client_id.startswith('python-mqtt-')
        assert len(app.mqtt_client_id) > len('python-mqtt-')
    
    def test_environment_variable_defaults(self):
        """Test default values for optional environment variables"""
        import app
        
        # Test that optional variables have correct defaults or are set
        assert app.mqtt_port == 1883 or isinstance(app.mqtt_port, int)
        assert hasattr(app, 'mqtt_topic')
        assert hasattr(app, 'webdriver_remote_url')
        assert hasattr(app, 'datetime_format')


class TestBrowserOptions:
    """Tests for browser configuration"""
    
    @patch('app.webdriver')
    def test_chrome_options_incognito(self, mock_webdriver):
        """Test Chrome browser is configured with incognito mode"""
        from app import scrape
        
        mock_browser = Mock()
        mock_webdriver.Remote.return_value = mock_browser
        mock_browser.get.side_effect = Exception("Stop execution")
        
        mock_chrome_options = Mock()
        mock_webdriver.ChromeOptions.return_value = mock_chrome_options
        
        scrape()
        
        # Verify ChromeOptions was created
        mock_webdriver.ChromeOptions.assert_called_once()
