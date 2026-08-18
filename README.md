# Minvandforsyning.dk til MQTT

[![Tests](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/tests.yml)
[![Docker](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/main.yml/badge.svg)](https://github.com/ttopholm/minvandforsyningdk-scraper/actions/workflows/main.yml)

This repo contains a docker image for minvandforsyning.dk, it will fetch the total m3 of water used.

It drives a real browser, because the site is a Blazor Server app without a public api. The
browser (chromium, through [Playwright](https://playwright.dev/python/)) ships inside the
image, so the scraper is the only container you need.

# Prerequisites
You need to have a mqtt broker installed, I recommend [Eclipse Mosquitto](https://mosquitto.org), or you can use the mqtt addon for HAOS.

# Installation

## Docker
To use the docker image, you can run it with the following command:
```
docker run ghcr.io/ttopholm/minvandforsyningdk-scraper:latest
```

The image is built for `linux/amd64` and `linux/arm64`, so it runs on a
raspberry pi as well. Chromium needs more shared memory than docker gives a
container by default, so add `--shm-size=1g` (or the `shm_size` in the
docker-compose file below).

## Docker compose

To use the docker-compose, you can run it with the following command in the directory where you have your docker-compose.yml file (the mqtt broker is not included):
```
docker-compose up -d
```

<b>Remember</b> to set the variables in the docker-compose.yml, before you run the command above.

## Upgrading from the selenium version
The scraper used to need a separate `selenium/standalone-chrome` container. It
does not any more, the browser is inside the image:

1. Remove the `selenium` service and the `depends_on` block from your
   docker-compose.yml, and drop the `webdriver-remote-url` variable. It is
   ignored, and the scraper logs a warning if it is still set.
2. Add `shm_size: '1g'` to the scraper service. Chromium needs more shared
   memory than the 64m docker gives a container by default.
3. If you have the manual Home Assistant sensor in your yaml, see the Home
   assistant section below before the first run.

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
| datetime-format   | The format of the time on the webpage | | kl. %H.%M, d. %d.%m.%Y |
| mqtt-status-topic   | Topic for `online`/`offline`, used as availability for the entities | | minvandforsyningdk/status |
| mqtt-retain   | Retain the reading, so Home Assistant has a value right after a restart | | true |
| mqtt-discovery   | Announce the sensors to Home Assistant automatically | | true |
| mqtt-discovery-prefix   | Discovery prefix, must match the one in Home Assistant | | homeassistant |
| device-name   | The device name shown in Home Assistant | | Minvandforsyning |
| timezone   | Timezone the readings are written in | | Europe/Copenhagen |
| login-url   | The page the login starts on | | https://www.minvandforsyning.dk/login/picker |

## Browser variables
| Variable      | Description | Default Value |
| ----------- | ----------- | ----------- |
| headless | Run the browser without a screen | true |
| browser-executable | Path to another chromium build, if you do not want the bundled one | |
| browser-cdp-url | Drive a remote chrome over CDP instead of starting one, e.g. `http://chrome:9222` | |

## Resilience variables
The scraper retries a failed run instead of waiting a full hour, and a failure
can never take the process down. These variables tune that behaviour:

| Variable      | Description | Default Value |
| ----------- | ----------- | ----------- |
| scrape-interval | Seconds between successful runs | 3600 |
| retry-interval | Seconds before the next run after a failed one | 300 |
| max-attempts | Attempts per run before giving up until the next run | 3 |
| element-timeout | Seconds to wait for an element on the page | 20 |
| form-settle-delay | Seconds to let the login form settle before typing | 2 |
| dashboard-timeout | Seconds to wait for the reading to show up after login | 60 |
| page-load-timeout | Seconds before a hanging page is aborted | 60 |
| debug-dir | Directory for html, screenshot and a playwright trace of a failed run | |
| mqtt-retries | Publish attempts before a reading is considered lost | 3 |
| log-level | DEBUG, INFO, WARNING or ERROR | INFO |

## When minvandforsyning.dk changes layout or button ids
Every element is looked up through a list of candidate selectors, and the first
one that matches wins. If the preferred selector stops matching, the fallbacks
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

A value is a list of candidates separated by `||`, and Playwright picks the
engine itself: CSS by default, XPath when it starts with `//`, and the `text=`,
`role=` and `id=` prefixes work too. Text and role based selectors survive a
redesign much better than an id:

```
selector-submit=#next||button[type=submit]||role=button[name=/log ind/i]
```

To find out what the page looks like now, set `debug-dir` (and mount it, see
the docker-compose file). On every failed run the scraper writes the page html,
a screenshot and a [Playwright trace](https://trace.playwright.dev) to that
directory, and the log line tells you which element it could not find. The
trace has the DOM at every step, so you can click through the run and see
exactly where it went wrong.


# Output format
```
{
    "total":234.32,
    "meter_id":23522852,
    "timestamp":"2024-10-07 18:58:00",
    "timestamp_iso":"2024-10-07T18:58:00+02:00"
}
```
## Output variables
| Variable      | Description | 
| ----------- | ----------- | 
| total     | the total of used water in m3 |
| meter_id     | The number of your meter | 
| timestamp      | Time for last reading of the meter, provided by minvandforsyning.dk|
| timestamp_iso      | The same time with a timezone, used by the Home Assistant sensor |

The site reports danish wall clock time without a timezone. `timestamp_iso` adds
the timezone from the `timezone` variable, so set it if your meter is not read
in danish time.

# Development

## Running Tests

This project includes a comprehensive suite of unit tests to ensure code quality and reliability.

### Install Development Dependencies

```bash
pip install -r requirements-dev.txt
playwright install --with-deps chromium
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
- ✅ Selector fallbacks and the `selector-*` overrides, in a real browser
- ✅ Reading values from the page text when the layout changed
- ✅ `scrape` function with various scenarios (success, MQTT errors, general exceptions)
- ✅ Retries: a transient failure, a browser that will not start, a broker that is down
- ✅ A full run against a local copy of the site, browser and broker included
- ✅ Data parsing (total, meter_id, timestamp)
- ✅ MQTT message structure validation
- ✅ Configuration and environment variable handling
- ✅ Home Assistant discovery payloads, against a real broker in CI
- ✅ Browser options setup and the run loop

Current test coverage: **94.07%**

# Home assistant

## Automatic setup (mqtt discovery)
Nothing to configure. After the first successful run the scraper announces
itself to Home Assistant, and a **Minvandforsyning** device shows up under
Settings -> Devices & Services -> MQTT with three entities:

| Entity | Description |
| ----------- | ----------- |
| Total | The total of used water in m3, ready for the energy dashboard |
| Last reading | When the meter was read |
| Meter number | Your meter number |

The entities go `unavailable` if the scraper cannot deliver a reading, so you
can alert on it. The discovery messages are retained, which means the device
survives a Home Assistant restart.

Two things to know:
- The discovery prefix must match the one in Home Assistant. It is
  `homeassistant` in both by default, so this only matters if you changed it.
- **Upgrading from the manual sensor below?** Remove it from your yaml first,
  otherwise you end up with two sensors for the same meter.

To remove the device again, set `mqtt-discovery=false` and delete the retained
messages under `homeassistant/sensor/minvandforsyning_[meter id]/`.

## Manual setup
If you would rather set the sensor up yourself, set `mqtt-discovery=false` and
use this yaml:

```
mqtt:
  sensor:
    - name: minvandforsyningdk
      state_topic: "minvandforsyningdk/total"
      availability_topic: "minvandforsyningdk/status"
      device_class: water
      state_class: total_increasing
      unit_of_measurement: m³
      unique_id: public_waterworks_id
      value_template: "{{ value_json.total }}"
      json_attributes_topic: "minvandforsyningdk/total"
      json_attributes_template: >
        { "meter_id": {{ value_json.meter_id }},
          "timestamp": "{{ value_json.timestamp }}" }
```

After that you can add it to the energy dashboard