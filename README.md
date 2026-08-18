# Minvandforsyning.dk til MQTT

[![Tests](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/tests.yml)
[![Docker](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/main.yml/badge.svg)](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/main.yml)

This repo contains a docker image for minvandforsyning.dk, it will fetch the total m3 of water used.

# Prerequisites
You need to have a mqtt broker installed, I recommend [Eclipse Mosquitto](https://mosquitto.org), or you can use the mqtt addon for HAOS.

# Installation

## Docker
To use the docker image, you can run it with the following command:
```
docker run ghcr.io/ttopholm/minvandforsyningdk-scraper:latest
```

## Docker compose

To use the docker-compose, you can run it with the following command in the directory where you have your docker-compose.yml file, it will download the scraper and requirements for the scraper (not mqtt broker):
```
docker-compose up -d
```

<b>Remember</b> to set the variables in the docker-compose.yml, before you run the command above.

## Environment Variables
| Variable      | Description | Mandatory | Default Value |
| ----------- | ----------- | ----------- | ----------- |
| mqtt-broker      | Mqtt host | X ||
| username     | Username on minvandforsyning.dk (only rambøll local account is supported) | X ||
| password      | Password on minvandforsyning.dk (only rambøll local account is supported)| X ||
| mqtt-port    | Mqtt port | | 1883 |
| mqtt-username   | The username for the mqtt broker | |  |
| mqtt-password   | The password for the mqtt broker | |  |
| mqtt-topic   | The topic where  data is published to | | minvandforsyningdk/total |
| webdriver-remote-url   | The url for the selenium server | | http://selenium:4444 |
| datetime-format   | The format of the time on the webpage | | kl. %H.%M, d. %d.%m.%Y |
| mqtt-status-topic   | Topic for `online`/`offline`, so you can alert when the scraper stops delivering | |  |
| login-url   | The page the login starts on | | https://www.minvandforsyning.dk/login/picker |

## Resilience variables
The scraper retries a failed run instead of waiting a full hour, and a failure
can never take the process down. These variables tune that behaviour:

| Variable      | Description | Default Value |
| ----------- | ----------- | ----------- |
| scrape-interval | Seconds between successful runs | 3600 |
| retry-interval | Seconds before the next run after a failed one | 300 |
| max-attempts | Attempts per run before giving up until the next run | 3 |
| element-timeout | Seconds to wait for an element on the page | 20 |
| dashboard-timeout | Seconds to wait for the reading to show up after login | 60 |
| page-load-timeout | Seconds before a hanging page is aborted | 60 |
| mqtt-retries | Publish attempts before a reading is considered lost | 3 |
| debug-dir | Directory where html + screenshot is written when a run fails | |
| log-level | DEBUG, INFO, WARNING or ERROR | INFO |

## When minvandforsyning.dk changes layout or button ids
Every element is looked up through a list of candidate locators, and the first
one that matches wins. If the preferred locator stops matching, the fallbacks
are tried and a warning is logged, so the job keeps running while you look into
it. Values (total, meter id, timestamp) have a last resort on top of that: they
are read straight out of the page text with a regular expression.

If all of that fails you can point the scraper at the new markup **without
waiting for a new image** - just set the matching variable and restart the
container:

| Variable      | Element |
| ----------- | ----------- |
| selector-login-provider | The button that picks the login provider |
| selector-username | The username field |
| selector-password | The password field |
| selector-submit | The login button |
| selector-total | The total m3 |
| selector-meter-id | The meter number |
| selector-timestamp | The time of the reading |
| pattern-total | Regex used to read the total from the page text |
| pattern-meter-id | Regex used to read the meter number from the page text |

A value is a list of candidates separated by `||`. Each candidate is an XPath,
or a CSS selector when prefixed with `css=`:

```
selector-username=css=#signInName||//input[@type='email']
```

To find out what the page looks like now, set `debug-dir` (and mount it, see
the docker-compose file). On every failed run the scraper writes the page html
and a screenshot to that directory, and the log line tells you which element it
could not find.


# Output format
```
{
    "total":234.32,
    "meter_id":"23522852",
    "timestamp":"2024-10-07 18:58:00"
}
```
## Output variables
| Variable      | Description | 
| ----------- | ----------- | 
| total     | the total of used water in m3 |
| meter_id     | The number of your meter | 
| timestamp      | Time for last reading of the meter, provided by minvandforsyning.dk|

# Development

## Running Tests

This project includes a comprehensive suite of unit tests to ensure code quality and reliability.

### Install Development Dependencies

```bash
pip install -r requirements-dev.txt
```

### Run Tests

```bash
# Run all tests
pytest

# Run tests with verbose output
pytest -v

# Run tests with coverage report
pytest --cov=app --cov-report=term-missing

# Run specific test file
pytest tests/test_app.py

# Run specific test class
pytest tests/test_app.py::TestWaitForElement

# Run specific test
pytest tests/test_app.py::TestWaitForElement::test_wait_for_element_success
```

### Test Coverage

The test suite currently covers:
- ✅ `wait_for_element` function with timeout handling
- ✅ Locator fallbacks and the `selector-*` overrides
- ✅ Reading values from the page text when the layout changed
- ✅ `scrape` function with various scenarios (success, MQTT errors, general exceptions)
- ✅ Retries: a transient failure, a dead selenium, a broker that is down
- ✅ Data parsing (total, meter_id, timestamp)
- ✅ MQTT message structure validation
- ✅ Configuration and environment variable handling
- ✅ Browser options setup and the run loop

Current test coverage: **95.36%**

# Home assistant
If you want it in Home Assistant as a sensor use the follow MQTT Image code:

```
mqtt
  sensor:
    - name: minvandforsyningdk
      state_topic: "minvandforsyningdk/total"
      device_class: water
      state_class: total_increasing
      unit_of_measurement: m³
      unique_id: public_waterworks_id
      value_template: "{{ value_json.total }}"
      json_attributes_topic: "minvandforsyningdk/total"
      json_attributes_template: >
        { "meter_id": {{value_json.meter_id}},
          "timestamp": {{value_json.data.timestamp}} }
```

After that you can add it to the energy dashboard