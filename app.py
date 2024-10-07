from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from datetime import datetime
from os import environ
from random import randint
from paho.mqtt.publish import single as publish
from json import dumps
from time import sleep
from environs import Env

env = Env()
env.read_env()

_run_timer = 60 * 60 #1 hour

# variables requireds
mqtt_broker = env.str('mqtt-broker')
mvf_username = env.str('username')
mvf_password = env.str('password')
mvf_utility_code = env.str('utility-code')


# optional variables
mqtt_port = env.int('mqtt-port', 1883)
mqtt_topic = env.str('mqtt-topic', 'minvandforsyningdk/total')
mqtt_username = env.str('mqtt-username', None)
mqtt_password = env.str('mqtt-password', None)
webdriver_remote_url = env.str('webdriver-remote-url', 'http://selenium:4444')

mqtt_client_id = f'python-mqtt-{randint(0, 1000)}'

mqtt_auth = None
if mqtt_username is not None:
    mqtt_auth = {"username": mqtt_username, "password": mqtt_password}

def wait_for_element(wd, elm, timeout=10):
    try:
        element_present = EC.presence_of_element_located((By.XPATH, elm))
        WebDriverWait(wd, timeout).until(element_present)
        return True
    except TimeoutException:
        print("Timed out waiting for page to load")    

def scrape():   
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--incognito")
    browser = webdriver.Remote(webdriver_remote_url, options=chrome_options)

    try:
        browser.get("https://www.minvandforsyning.dk/LoginIntermediate")
        wait_for_element(browser, '/html/body/app/body/div[2]/div/div/div[3]/button', 10)
        browser.find_element(By.XPATH, '//html/body/app/body/div[2]/div/div/div[3]/button').click()
        browser.find_element(By.XPATH, '//*[@id="LoginIntermediaryMudPaper"]/div[1]/div[1]/button').click()
        wait_for_element(browser, "//input[@type='text']", 10)
        browser.find_element(By.XPATH, "//input[@type='text']").send_keys(mvf_username)
        browser.find_element(By.XPATH, "//input[@type='password']").send_keys(mvf_password)
        browser.find_element(By.XPATH, "(//input[@type='text'])[2]").send_keys(mvf_utility_code)
        browser.find_element(By.XPATH, '//form/button').click()
        wait_for_element(browser, '//span[2]/b[2]', 10)
        _total = float(browser.find_element(By.XPATH, '//span[2]/b[2]').text.replace(',','.'))
        _meter_id = int(browser.find_element(By.XPATH, '//b').text)
        _date = datetime.strftime(
            datetime.strptime((browser.find_element(By.XPATH, '//span[2]/b').text) , 'kl. %H:%M, d. %d-%m-%Y'),
            "%Y-%m-%d %H:%M:%S"
        )
        mqtt_msg = dumps({
            "total": _total, 
            "meter_id": _meter_id, 
            "timestamp": _date  
        })  
        try:
            publish(mqtt_topic, mqtt_msg, hostname=mqtt_broker, port=mqtt_port, auth=mqtt_auth)
        except ConnectionRefusedError:
            print("Can't connect to mqtt server")
    except:
        print("An error occurred, let me try again")
    finally:
        browser.quit()


if __name__ == "__main__":
    while True:
        scrape()
        sleep(_run_timer)



    