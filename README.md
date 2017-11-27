# MQTT - SMS bridge 

Lot of the codes come from https://github.com/JFF-Bohdan/sim-module

## Features

* INI file with all settings (see comments inside)
* MQTT parameters for published data (QoS, Retain).
* Processing of incoming messages
* USSD commands in the text and PDU modes
* Python 3 compatibility.
* System daemon script.

## How to install on Raspberry Pi or Banana Pi

```bash
$ cd ~/
$ sudo pip3 freeze --local | grep -v '^\-e' | cut -d = -f 1  | xargs -n1 pip3 install -U
$ sudo pip3 install ConfigParser
$ sudo pip3 install paho-mqtt
$ sudo chmod 0755 mqttsms.sh
$ sudo cp mqttsms.sh /etc/init.d
$ sudo update-rc.d mihome.sh defaults
$ sudo service mqttsms start
```

## How to start/stop the daemon

$ sudo service mqttsms start

## Examples

### How to send SMS

Write your message to topic home/sim900/18002001100 (your mobile number)

### How to request balance

Write *100# (your USSD command) to topic home/sim900/ussd/balance
Read result from home/sim900/ussd/balance/value

My home page: http://ptvo.info