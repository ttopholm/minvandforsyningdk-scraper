# Minvandforsyning.dk til MQTT
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
| username     | Username on minvandforsyning.dk | X ||
| password      | Password on minvandforsyning.dk| X ||
| utility-code      | Utility code on minvandforsyning.dk       | X ||
| mqtt-port    | Mqtt port | | 1883 |
| mqtt-username   | The username for the mqtt broker | |  |
| mqtt-password   | The password for the mqtt broker | |  |
| mqtt-topic   | The topic where  data is published to | | minvandforsyningdk/total |
| webdriver-remote-url   | The url for the selenium server | | http://selenium:4444 |


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