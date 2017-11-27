#The MIT License (MIT)
#
#Copyright (c) 2014-2015 Bohdan Danishevsky ( dbn@aminis.com.ua )
#
#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:
#
#The above copyright notice and this permission notice shall be included in all
#copies or substantial portions of the Software.
#
#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#SOFTWARE.

"""
This file is part of sim-module package. USSD requests processing classes and functions.

sim-module package allows to communicate with SIM 900 modules: send SMS, make HTTP requests and use other
functions of SIM 900 modules.

Copyright (C) 2014-2015 Bohdan Danishevsky ( dbn@aminis.com.ua ) All Rights Reserved.
"""

from lib.sim900.gsm import SimGsm
from lib.sim900.simshared import *
from smspdu.pdu import PDUData

class SimUssdHandler(SimGsm):
    def __init__(self, port, logger):
        SimGsm.__init__(self, port, logger)
        self.lastUssdResult = None

    @staticmethod
    def __parseResult(self, value):
        #parsing strings like '+CUSD: 0,"data string"'

        #searching and removing '+CUSD' prefix
        idx = value.find(":")
        if idx == -1:
            return None

        left = value[:idx]
        left = str(left).strip()
        if left != "+CUSD":
            return None

        data = value[(idx+1):]
        data = str(data).strip()

        #searching and removing numeric parameter
        idx = data.find(",")
        if idx == -1:
            if data != "":
                # contains error code
                data = int(data)
                if data == 2:
                    self.setWarn("USSD terminated by network")
                elif data == 3:
                    self.setWarn("Other local client has responded")
                elif data == 4:
                    self.setWarn("Operation not supported")
                elif data == 5:
                    self.setWarn("Network time out")
            return None

        #also, we can use this code. But I dont know how
        code = data[:idx]
        data = data[(idx+1):]

        data = str(data).strip()

        idx = data.rfind(",")
        dcs = None
        if idx > -1:
            dcs = data[(idx+1):] 
            data = data[:idx]

        data = data.strip('"')            
        if(dcs is not None) and (dcs != ''):
            # bit 7..4 Coding Group Bits = 01xx
            # bit 5 = 0 --> uncompressed,
            # bit 4 = 0 --> no class meaning
            # bit 3 & 2 = 1 & 0 --> UCS2 (16bit)
            dcs = int(dcs)
            if (dcs & 40) != 0:
                dcs = (dcs >> 2) & 3
                if dcs == 2: # UCS2 encoding
                    tpdu = PDUData(data)
                    tpdu_len = len(tpdu)
                    bytes = tpdu.octets(tpdu_len / 2 + tpdu_len % 2)
                    data = bytearray(bytes, 'latin').decode('utf_16_be') 
        return data

    def runUssdCode(self, ussdCode):
        cmd = "AT+CUSD=1,\"{0}\",15".format(ussdCode)
        self.logger.info("running command = '{0}'".format(cmd))

        #executing command, also we can retrieve result right here
        result = self.commandAndStdResult(cmd, 20000)

        if (result is None) or (self.lastResult != 'OK'):
            self.setWarn("error running USSD command '{0}'".format(ussdCode))
            return False

        result = str(result).strip()

        #checking that we have result here
        if len(result) > 0:
            self.lastUssdResult = self.__parseResult(self, result)

            if self.lastUssdResult is None:
                self.setWarn("error parsing USSD command result")
                return False

            return True

        #reading data line
        dataLine = self.readNullTerminatedLn(20000)

        if dataLine is None:
            self.setWarn("error waiting for USSD command result")
            return False

        dataLine = str(dataLine).strip()

        #reading bytes in the end of response
        data = self.readFixedSzieByteArray(1, 500)
        if data == bytes([0xff]):
            data = None

        if (data is not None):
            data = data.decode('latin')

        endLine = self.readNullTerminatedLn(500)
        #self.readNullTerminatedLn(500, "latin") # receive and discard all remaining bytes

        if (data is not None) or (endLine is not None):
            endLine = noneToEmptyString(data) + noneToEmptyString(endLine)
            endLine = str(endLine).strip()

            if len(endLine) > 0:
                dataLine += endLine
        #parsing CUSD result
        self.lastUssdResult = self.__parseResult(self, dataLine)

        if self.lastUssdResult is None:
            return False

        return True

