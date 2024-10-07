# Minvandforsyning.dk til MQTT
This repo contains a docker image for minvandforsyning.dk, it will fetch the total m3 of water used.

# Installation

To use the docker images, you can run it with the following code:
```
docker run ghcr.io/ttopholm/frigate-birdnet:latest
```
## Environment Variables
| Variable      | Description | Mandatory | Default Value |
| ----------- | ----------- | ----------- | ----------- |
| mqtt-broker      | Mqtt host       | X ||
| mqtt-port    | Mqtt port        | | 1883 |
| publish-topic   | The topic where bird data is published to      | | birdnet/bird |


# Output format
```
{
    "image_url":"https://cdn.download.ams.birds.cornell.edu/api/v1/asset/242173971/900",
    "common_name":"Common wood pigeon",
    "scientific_name":"Columba palumbus",
    "start_time":1717265299.484562,
    "end_time":1717265302.484562,
    "species_code":"cowpig1"
    "camera": "Backyard"
}
```

# Home assistant
If you want it in Home Assistant as an image use the follow MQTT Image code:

```
mqtt
  image:
    - name: birdnet
      unique_id: birdnet
      url_topic: "birdnet/bird"
      url_template: "{{ value_json.image_url }}"
      json_attributes_topic: "birdnet/bird"
      json_attributes_template: >
        { "name": {{value_json.common_name}} }
```