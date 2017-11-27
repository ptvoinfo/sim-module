#!/usr/bin/python3
# -*- coding: utf-8 -*-
import logging
import sys
import os
import signal
import datetime
import time
import re
import configparser as ConfigParser
import _thread as thread
import paho.mqtt.client as mqtt
from mqttsms_shared import *
from lib.sim900.smshandler import SimGsmSmsHandler, SimSmsPduCompiler 
from lib.sim900.ussdhandler import SimUssdHandler
from smspdu.pdu import SMS_SUBMIT, SMS_DELIVER

logger = 0
exitSignal = False
MQTT_CLIENT = 0
MQTT_QOS = 0
MQTT_RETAIN = False
MQTT_TOPIC_SMS_OUT = ''
MQTT_TOPIC_SMS_IN = ''
MQTT_TOPIC_USSD = ''

COM_PORT = 0
GSM = 0
SMS_CENTER_NUMBER = ''
GSM_SEND_QUEUE = []
GSM_WHITE_LIST = ()
GSM_SMS_PASS = ''
lastReadMessageIndex = ''
bAddNextLine = False
lastSMSHeader = ''

PATTERN_SYS = {}
PATTERN_USER = {}
PATTERN_TOPIC = {}

def signal_handler(signal, frame):
    """
    Captures the "Ctrl+C" event in a console window and signals to exit
    """
    global exitSignal
    logger.info('SIGINT')    
    exitSignal = True

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    """
    OnConnect event handler for the MQTT client
    """
    logger.info("Connected with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    if MQTT_TOPIC_SMS_OUT != '':
        client.subscribe(MQTT_TOPIC_SMS_OUT + '/#')
    if MQTT_TOPIC_USSD != '':
        client.subscribe(MQTT_TOPIC_USSD + '/#')

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    """
    OnMessage event handler for the MQTT client for subscribed topics
    """
    excMsg = "Exception: Unable Handle MQTT message"
    try:
        logger.info("Topic: " + msg.topic + "\nMessage: " + str(msg.payload))
        if msg.topic.startswith(MQTT_TOPIC_SMS_OUT):
            datatype = 2
        elif MQTT_TOPIC_USSD != '' and msg.topic.startswith(MQTT_TOPIC_USSD):
            datatype = 3
        else:
            logger.error("Unknown topic: " + msg.topic)
            return

        items = msg.topic.split('/')
        target_number = items.pop(-1) # last part of our topic is the target number
        if (target_number is not None) and (msg.payload is not None):
            if (target_number == "status") or (target_number == "result") or (target_number == "value"):
                return
            if datatype == 2:
                p = re.compile('^[0-9\-\+ ]+$')
                m = p.match(target_number)
                if m:
                    p = re.compile('[^\+0-9]')
                    p.sub('', target_number)
                else:
                    logger.error("Wrong target number: " + target_number)
                    return

            message = msg.payload.decode('utf-8')
            if target_number != '' and message != '':
                if datatype == 2: 
                    GSM_SEND_QUEUE.append( [datatype, target_number, message, msg.topic] )
                else:
                    # USSD request may contain characters that are not possible in a topic name
                    # therefore we suppose to receive a USSD request in payload
                    GSM_SEND_QUEUE.append( [datatype, message, target_number, msg.topic] )
        
    except Exception as e:
        logger.exception(excMsg)
        #logger.error(excMsg + ': ' + '{0}'.format(e), exc_info=True)
        return False
    except:
        logger.exception(excMsg)
        #logger.error(excMsg)
        return False                    

def prepare_mqtt(MQTT_SERVER, MQTT_PORT=1883):
    """
    Initializes MQTT client and connects to a server
    """
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_SERVER, MQTT_PORT, 60)
    return client

def mqtt_publish_data(client, smsFrom, smsText):
    """
    Published received SMS message to the specified topic
    """
    topic = MQTT_TOPIC_SMS_IN
    value = str(smsText)
    format_args = dict(smsFrom=smsFrom,
            group1='',
            group2='',
            group3='',
            group4='',
            group5='',
            value=value,
            pattern='')

    for pname in PATTERN_USER:
        p = PATTERN_USER.get(pname, '');
        if p == '':
            continue

        m = re.search(p, value)
        if m:
            format_args['pattern'] = pname
            grp = m.groups()
            grp_len = len(grp)
            for i in range(grp_len):
                format_args['group' + str(i + 1)] = grp[i]
            d = m.groupdict();
            for gname in d:
                if gname == 'value':
                    value = d[gname]
                else:
                    format_args[gname] = d[gname]
            if pname in PATTERN_TOPIC:
                topic = PATTERN_TOPIC[pname]
            break            

    logger.debug('Data: ' + str(format_args))
    if topic != '':
        path = topic.format(**format_args)
        client.publish(path, payload=value, qos=MQTT_QOS, retain=MQTT_RETAIN)
        if(smsText != value):
            logger.info('Published "' + value + '" to: ' + path)
        else:
            logger.info("Published SMS text to: " + path)

def get_param(value, sep, pos, defval = ''):
    """
    Extracts a value from "value" that is separated by "sep". Position "pos" is 1 - based
    """
    parts = value.split(sep)
    if pos <= len(parts):
        return parts[pos - 1].strip()
    return defval

def gsm_add_send_queue(data):
    GSM_SEND_QUEUE.append([1, data])    


def gsm_send_sms(target_number, message):
    """
    Sends a SMS message using the UCS2 format
    """
    logger.info("Sending SMS message to " + target_number + ' Text: ' + message)

    #creating object for SMS sending
    sms = SimGsmSmsHandler(COM_PORT, logger) 

    pduHelper = SimSmsPduCompiler(
        SMS_CENTER_NUMBER,
        target_number,
        message
    )

    if not sms.sendPduMessage(pduHelper, 1):
        logger.error("Unable to send SMS: {0}".format(sms.errorText))
        return False

    return True

def gsm_send_ussd(target_number, message, topic):
    """
    Sends a USSD request to a mobile operator
    """
    logger.info('Sending USSD request "' + message + '" for "' + target_number + '"')

    ussd = SimUssdHandler(COM_PORT, logger)
    if not ussd.runUssdCode(target_number):
        logger.error("Unable to execute USSD request")
        MQTT_CLIENT.publish(topic + '/status', payload='FAILED', qos=MQTT_QOS)
        return False

    value = ussd.lastUssdResult
    logger.info("USSD result = {0}".format(value))

    if message == 'balance':
        pattern = PATTERN_SYS.get('balance', '')
        if pattern == '':
            pattern = "(\d[\d\.\,]+)"
        m = re.search(pattern, value)
        if m:
            found = m.group(1)
            if found is not None:
                found = found.replace(',', '.')
                MQTT_CLIENT.publish(topic + '/value', payload=found, qos=MQTT_QOS)
    MQTT_CLIENT.publish(topic + '/result', payload=value, qos=MQTT_QOS)
    MQTT_CLIENT.publish(topic + '/status', payload='SUCCESS', qos=MQTT_QOS)
    return True

def com_port_check_sms_queue(GSM):
    """
    Checks the queue with outgoing messages or raw modem commands
    """
    excMsg = "Exception: Unable process outgoing queue"
    while len(GSM_SEND_QUEUE) > 0:
        try:
            item = GSM_SEND_QUEUE.pop(0)
            datatype = item[0]
            if datatype == 1:
                GSM.simpleWriteLn(item[1])
            elif datatype == 2:
                gsm_send_sms(item[1], item[2])
            elif datatype == 3:
                gsm_send_ussd(item[1], item[2], item[3])
        except Exception as e:
            logger.exception(excMsg)
            #logger.error("Exception: Unable process outgoing queue {0}".format(e))
        except:
            logger.exception(excMsg)
            #logger.error("Exception: Unable process outgoing queue")
    return True

def com_port_check_incomig(GSM):
    """
    Checks COM port's incoming buffer and processes received data
    """
    excMsg = "Exception: Unable to process incoming data"
    global MQTT_CLIENT, GSM_SEND_QUEUE, bAddNextLine, lastReadMessageIndex, lastSMSHeader
    bCountinue = True
    while bCountinue:
        try:
            line = GSM.readDataLine(200, "latin")            

            #if we do not have something in input
            if line is None:
                return 1

            logger.debug("Received: " + str(bytearray(line, 'latin')))
            line = re.sub(r'[^\x20-\x7f]', r'', line)

            bPrevAddNextLine = bAddNextLine
            bAddNextLine = False

            #we have echo, need to reconfigure
            if line == "OK" or line == "ERROR":
                # do nothing, previous command was completed
                continue
            elif line.startswith("+") and line.find("ERROR") >= 0:
                # do nothing, previous command was completed with an error
                continue
            else:
                lineHeader = line[0:6]
                logger.debug("Command header: " + lineHeader)
                if lineHeader == "+CMTI:":
                    # new SMS message arrival
                    # +CMTI: "MT",382
                    lastReadMessageIndex = get_param(line, ",", 2)
                    if lastReadMessageIndex != "":
                        logger.info("New incoming SMS #" + lastReadMessageIndex)
                        gsm_add_send_queue("AT+CMGR=" + lastReadMessageIndex)
                elif lineHeader == "+CNMI:":
                    """
                     direct output for SMS messages
                     +CNMI: (0-2),(0-3),(0,2),(0-2),(0,1)<CR><LF>
                     <mode>, <mt> - Mobile terminated messages, <mo> - Mobile originated messages, <bm> - Broadcast, <ds> - status
                     do nothing
                    """
                elif lineHeader == "+CMGL:" or lineHeader == "+CMGR:" or bPrevAddNextLine:

                    if not bPrevAddNextLine:
                        lastSMSHeader = line[6:]
                        bAddNextLine = True
                    else:
                        idx = "1"
                        if lineHeader == "+CMGL:":
                            idx = get_param(smsData, ",", 1)
                        elif lastReadMessageIndex != "":
                            idx = lastReadMessageIndex
                            
                        lastReadMessageIndex = ""

                        """
                        Examples of PDU SMS messages:
                        +CMGR: 1,,37
                        0191976100000000000000000000000000000000000000000
                        +CMGL: 362,1,,41
                        0191976100000000000000000000000000000000000000000

                        AT+CMGL
                        +CMGL: 366,0,,22
                        0191976100000000000000000000000000000000000000000

                        Example of plain sms message
                        +CMGR: "REC READ", "+79101807833", "2011/8/30,16:20:50"
                        Test message
                        """
                        logger.debug("SMS header: " + lastSMSHeader)
                        smsText = ""
                        smsFrom = ""
                        dateStr = get_param(lastSMSHeader, ",", 3)
                        if dateStr.isdecimal():
                            logger.debug("Processing PDU data: " + line)
                            # this is SMSC info length in octets
                            smsc_size = int(line[0:2], 16) * 2
                            smsc_size = smsc_size + 2
                            line = line[smsc_size:] # remove SMSC size and info

                            # this is the PDU message type
                            first = int(line[0:2], 16)
                            tp_mti = first & 0x03 # message type
                            if tp_mti == 1:
                                logger.debug("SMS_SUBMIT")
                                p = SMS_SUBMIT.fromPDU(line, 'unknown')
                                smsFrom = p.tp_address
                                smsText = p.user_data
                            else:
                                logger.debug("SMS_DELIVER")
                                p = SMS_DELIVER.fromPDU(line, 'unknown')
                                smsFrom = p.tp_address
                                smsText = p.user_data
                        else:
                            logger.debug("Processing plain text data")
                            smsText = line
                            smsFrom = get_param(smsData, ",", 2)
                            smsFrom.replace("\"", "")

                        # no exceptions here -> SMS received and parsed successfully. Now, we can delete it
                        logger.info("Deleting SMS #" + idx)
                        gsm_add_send_queue("AT+CMGD=" + idx + ",0")
                        if smsText != '':
                            logger.info("Received SMS from: " + smsFrom)
                            logger.debug("SMS message: " + smsText)
                            bCanPublish = True
                            if GSM_SMS_PASS != '':
                                pattern = PATTERN_SYS.get('password', '')
                                if pattern != '':
                                    bCanPublish = False
                                    m = re.search(pattern, smsText)
                                    if m:
                                        p = m.group(1)
                                        if p is None:
                                            p = ''
                                        bCanPublish = GSM_SMS_PASS == p
                            if bCanPublish and len(GSM_WHITE_LIST) > 0:
                                bCanPublish = GSM_WHITE_LIST.count(smsFrom) > 0
                                if not bCanPublish:
                                    logger.info("Ignoring SMS from a unknown number")
                            else:
                                logger.info("Ignoring SMS with a wrong password")
                            if bCanPublish:
                                mqtt_publish_data(MQTT_CLIENT, smsFrom, smsText)
                        lastSMSHeader = ''


        except Exception as e:
            logger.exception(excMsg)
            #logger.error("Exception: Unable to process incoming data {0}".format(e))
            return 2
        except:
            logger.exception(excMsg)
            #logger.error("Exception: Unable process incoming data")
            return 2

    return 0

def ConfigSectionMap(Config, section):
    dict1 = {}
    options = Config.options(section)
    for option in options:
        try:
            dict1[option] = Config.get(section, option)
            if dict1[option] == -1:
                logger.info("skip: %s" % option)
        except:
            logger.error("Exception on %s!" % option)
            dict1[option] = None
    return dict1

if __name__ == "__main__":
    Config = ConfigParser.ConfigParser()

    script_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), '')
    script_name = os.path.basename(__file__)
    script_ini = script_path + os.path.splitext(script_name)[0]+'.ini'

    d = datetime.datetime.now()
    print(d.strftime("%Y-%m-%d %H:%M:%S"), 'Reading settings from:', script_ini)

    Config.read(script_ini)

    gen_cfg = ConfigSectionMap(Config, 'General')
    loglevel = int(gen_cfg.get('loglevel', 0)) * 10
    consoleloglevel = int(gen_cfg.get('consoleloglevel', 0)) * 10
    
    # initializing logger
    (formatter, logger, consoleLogger,) = initializeLogs(loglevel, consoleloglevel) 

    log_file = gen_cfg.get('log', '')
    if (log_file != '') and (loglevel > 0):
        hdlr = logging.FileHandler(log_file)
        hdlr.setFormatter(formatter)
        logger.addHandler(hdlr) 

    mqtt_cfg = ConfigSectionMap(Config, "MQTT")

    MQTT_QOS = int(mqtt_cfg.get('qos', 0))
    tmp = int(mqtt_cfg.get('retain', 0))
    if tmp > 0:
        MQTT_RETAIN = True
    else:
        MQTT_RETAIN = False

    MQTT_SERVER = mqtt_cfg['server']
    MQTT_PORT = int(mqtt_cfg['port'])
    MQTT_TOPIC_SMS_OUT =  mqtt_cfg['topic_sms_send'].rstrip('/')
    MQTT_TOPIC_SMS_IN =  mqtt_cfg.get('topic_sms_recv', '').rstrip('/')
    MQTT_TOPIC_USSD =  mqtt_cfg.get('topic_ussd', '').rstrip('/')

    PATTERN_SYS = ConfigSectionMap(Config, "SystemPattern")
    PATTERN_USER = ConfigSectionMap(Config, "UserPattern")
    PATTERN_TOPIC = ConfigSectionMap(Config, "TopicForPattern")

    gsm_cfg = ConfigSectionMap(Config, "GSM")

    comName = gsm_cfg.get('com', '')
    comBaud = int(gsm_cfg.get('baud', 115200))
    whiteList = gsm_cfg.get('whitelist', '')
    if whiteList != '':
        GSM_WHITE_LIST = whiteList.split(';')
        for number in GSM_WHITE_LIST:
            number.strip()
            logger.debug('Number: "' + number + "'")
    GSM_SMS_PASS = gsm_cfg.get('password', '')

    # adding & initializing port object
    COM_PORT = initializeUartPort(portName=comName, baudrate=comBaud)

    #making base operations
    d = baseOperations(COM_PORT, logger)
    if d is not None:
        (GSM, imei) = d

        MQTT_CLIENT = prepare_mqtt(MQTT_SERVER, MQTT_PORT)
        try:
            thread.start_new_thread(MQTT_CLIENT.loop_forever, ())
        except:
            logger.error("Exception: Unable to start thread")

        signal.signal(signal.SIGINT, signal_handler)
        logger.info("Start listenning")

        bFirstTime = True
        while not exitSignal:
            if com_port_check_incomig(GSM) == 1: # nothing received, we can try to send a SMS
                if bFirstTime:
                    bFirstTime = False
                    gsm_add_send_queue("AT+CMGL=0,0") # get UNREAD message and mark then "READ"                    
                com_port_check_sms_queue(GSM)
            time.sleep(0.5)
        GSM.closePort()

    print("DONE")