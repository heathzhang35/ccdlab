#!/usr/bin/env python

from daemon import SimpleFactory, SimpleProtocol, catch
import datetime
from command import Command


class DaemonProtocol(SimpleProtocol):
    _debug = False  # Display all traffic for debug purposes

    @catch
    def processMessage(self, string):
        # It will handle some generic messages and return pre-parsed Command
        # object
        cmd = SimpleProtocol.processMessage(self, string)
        if cmd is None:
            return
        obj = self.object  # Object holding the state
        daemon = self.factory
        hw = obj['hw']  # HW factory

        while True:
            if cmd.name == 'get_status':
                self.message('status hw_connected=%s Current=%g Voltage=%g zero_check=%s zero_check_performed=%s V_source=%s I_auto_range=%s I_range=%s V_range=%s I_limit=%s' %
                             (self.object['hw_connected'],
                              self.object['Current'],
                              self.object['Voltage'],
                              self.object['zero_check'],
                              self.object['zero_check_performed'],
                              self.object['V_source'],
                              self.object['I_auto_range'],
                              self.object['I_range'],
                              self.object['V_range'],
                              self.object['I_limit']))
                if self.object['I_limit'] != '-' and self.object['zero_check_performed'] == 'no':  # i.e. comunication established , getting some statusupdates
                    cmd.name = 'zero_check'  # forse a zero check
                    self.object['zero_check_performed'] = 'auto_sheduled'
                else:
                    break
            if cmd.name == 'reset':
                daemon.log('Resetting Keithley 487')
                self.sendCommand('L0X')
                self.sendCommand('K0X')  # enable EOI and holdoff
                break
            if cmd.name == 'idn':
                self.sendCommand('U2X', keep=True)
                break
            if cmd.name == 'get_voltage':
                self.sendCommand('U8X', keep=True)
                break
            if cmd.name.split(',')[0] == 'set_voltage':
                # the format is set_voltage,val1,val2,val3
                # val1 -> voltage in V;
                # val2 -> range, 0 is 50V, 1 is 500V;
                # val3 -> curr. limit, 0 is 20 uA, 1 is 2 mA
                volt_pars = cmd.name.split(',')
                if len(volt_pars) != 4:
                    raise ValueError('KEITHLEY487: unable to parse voltage command')
                    return
                if volt_pars[2] == '0':
                    vlim = 50
                elif volt_pars[2] == '1':
                    vlim = 500
                else:
                    raise ValueError('KEITHLEY487: unable to parse voltage range')
                    return
                if abs(float(volt_pars[1])) > vlim:
                    raise ValueError('KEITHLEY487: requested voltage out of range')
                    return
                if volt_pars[3] not in ['0', '1']:
                    raise ValueError('KEITHLEY487: unable to parse current limit')
                    return
                self.sendCommand('V' + volt_pars[1] + ',' + volt_pars[2] + ',' + volt_pars[3], keep=False)
                break
            if cmd.name == 'get_current':
                self.sendCommand('B0X', keep=True)
                break
            if cmd.name == 'zero_check':
                if self.object['zero_check_performed'] == 'auto_sheduled':
                    self.object['zero_check_performed'] = 'auto_performing'
                else:
                    self.object['zero_check_performed'] = 'manual_performing'
                self.sendCommand('C1X', keep=False)
                for curr_range in range(1, 8):
                    self.sendCommand('R' + str(curr_range) + 'X', keep=False)
                    self.sendCommand('C2X', keep=False)
                    # this requests a read value, not so important which one, but makes it wait for the command to finish
                    self.sendCommand('++srq', keep=True)
                self.sendCommand('C0X', keep=False)
                self.sendCommand('R0X', keep=False)
                break
            if cmd.name == 'set_v_on':
                self.sendCommand('O1X', keep=False)
                break
            if cmd.name == 'set_v_off':
                self.sendCommand('O0X', keep=False)
                break
            # We need to recognize query cmd somehow - adding \'?\' to those commands, although the command set for K487 has no such thing
            if cmd.name[-1] == '?':
                self.sendCommand(cmd.name.rstrip('?'), keep=True)
            else:
                self.sendCommand(cmd.name, keep=False)
            if self._debug:
                print 'sending unrecognized command', cmd.name.rstrip('?')
                if cmd.name[-1] == '?':
                    print 'command recognize as query command'
            break

    @catch
    def sendCommand(self, string, keep=False):
        obj = self.object  # Object holding the state
        hw = obj['hw']  # HW factory
        hw.messageAll(string, type='hw', keep=keep, source=self.name)


class KeithleyProtocol(SimpleProtocol):
    _debug = False  # Display all traffic for debug purposes
    _refresh = 1

    def __init__(self):
        SimpleProtocol.__init__(self)
        self.commands = []  # Queue of command sent to the device which will provide replies, each entry is a dict with keys "cmd","source","timeStamp"
        self.name = 'hw'
        self.type = 'hw'
        self.lastAutoRead = datetime.datetime.utcnow()
        self.Iranges = {'1': '2nA',
                        '2': '20nA',
                        '3': '200nA',
                        '4': '2uA',
                        '5': '20uA',
                        '6': '200uA',
                        '7': '2mA'}

    @catch
    def connectionMade(self):
        SimpleProtocol.connectionMade(self)
        self.commands = []
        # We will set this flag when we receive any reply from the device
        self.object['hw_connected'] = 0
        SimpleProtocol.message(self, 'set_addr %d' % self.object['addr'])
        SimpleProtocol.message(self, 'L0X')
        SimpleProtocol.message(self, 'K0X')
        SimpleProtocol.message(self, '?$U0X')  # enable EOI and holdoff
        SimpleProtocol.message(self, 'R0X')  # set autorange

    @catch
    def connectionLost(self, reason):
        self.object['hw_connected'] = 0
        SimpleProtocol.connectionLost(self, reason)

    @catch
    def processMessage(self, string):
        obj = self.object  # Object holding the state
        daemon = obj['daemon']

        if self._debug:
            print 'KEITHLEY487 >> %s' % string
            print 'commands Q:', self.commands

        # Update the last reply timestamp
        obj['hw_last_reply_time'] = datetime.datetime.utcnow()
        obj['hw_connected'] = 1

        # Process the device reply
        while len(self.commands):
            if self.commands[0]['cmd'] == 'B0X' and self.commands[0]['source'] == 'itself':
                obj['Current'] = float(string[4:-1])
                self.commands.pop(0)
                break
            if self.commands[0]['cmd'] == 'U8X' and self.commands[0]['source'] == 'itself':
                obj['Voltage'] = float(string[3:-2])
                self.commands.pop(0)
                break
            if self.commands[0]['cmd'] == 'U0X' and self.commands[0]['source'] == 'itself':
                obj['zero_check'] = 'disabled' if string[7:9] == 'C0' else 'enabled'
                obj['V_source'] = 'disabled' if string[28:30] == 'O0' else 'enabled'
                obj['I_auto_range'] = 'disabled' if string[32:34] == 'R0' else 'enabled'
                obj['I_range'] = self.Iranges[string[34]]
                obj['V_range'] = '50V' if string[39:41] == 'V0' else '500V'
                obj['I_limit'] = '25uA' if string[41] == '1' else '25mA'
                self.commands.pop(0)
                if self.object['zero_check_performed'] == 'auto_performing':
                    self.object['zero_check_performed'] = 'auto'
                if self.object['zero_check_performed'] == 'manual_performing':
                    self.object['zero_check_performed'] = 'manual'
                break
            daemon.messageAll(string, self.commands[0]['source'])
            self.commands.pop(0)
            break

    @catch
    def update(self):
        if (datetime.datetime.utcnow() - obj['hw_last_reply_time']).total_seconds() > 100:
            # We did not get any reply from device during last 100 seconds,
            # probably it is disconnected?
            self.object['hw_connected'] = 0
            # TODO: should we clear the command queue here?

        # first check if device is hw_connected
        if self.object['hw_connected'] == 0:
            # if not connected do not send any commands
            return

        elif (datetime.datetime.utcnow() - self.lastAutoRead).total_seconds() > 2. and len(self.commands) == 0:
            # Request the hardware state from the device
            self.message('B0X', keep=True, source='itself')
            self.message('U8X', keep=True, source='itself')
            self.message('U0X', keep=True, source='itself')
            self.lastAutoRead = datetime.datetime.utcnow()

    @catch
    def message(self, string, keep=False, source='itself'):
        """
        Send the message to the controller. If keep=True, append the command name to
        internal queue so that we may properly recognize the reply
        """
        cmd = Command(string)
        if keep:
            self.commands.append({'cmd': string,
                                  'source': source,
                                  'timeStamp': datetime.datetime.utcnow(),
                                  'keep': keep})
            SimpleProtocol.message(self, '?$%s' % string)
        else:
            SimpleProtocol.message(self, string)


if __name__ == '__main__':
    from optparse import OptionParser

    parser = OptionParser(usage="usage: %prog [options] arg")
    parser.add_option('-H', '--hw-host', help='GPIB multiplexor host to connect', action='store', dest='hw_host', default='localhost')
    parser.add_option('-P', '--hw-port', help='GPIB multiplexor port to connect', action='store', dest='hw_port', type='int', default=7020)
    parser.add_option('-a', '--addr', help='GPIB bus address of the device', action='store', dest='addr', default=15)
    parser.add_option('-p', '--port', help='Daemon port', action='store', dest='port', type='int', default=7022)
    parser.add_option('-n', '--name', help='Daemon name', action='store', dest='name', default='keithley487')
    parser.add_option("-D", '--debug', help='Debug mode', action="store_true", dest="debug")

    (options, args) = parser.parse_args()

    # Object holding actual state and work logic.
    # May be anything that will be passed by reference - list, dict, object etc
    obj = {
        'hw_connected': 0,
        'addr': options.addr,
        'Current': 0,
        'Voltage': 0,
        'zero_check': '-',
        'zero_check_performed': 'no',
        'V_source': '-',
        'I_auto_range': '-',
        'I_range': '-',
        'V_range': '-',
        'I_limit': '-', }

    # Factories for daemon and hardware connections
    # We need two different factories as the protocols are different
    daemon = SimpleFactory(DaemonProtocol, obj)
    hw = SimpleFactory(KeithleyProtocol, obj)

    if options.debug:
        daemon._protocol._debug = True
        hw._protocol._debug = True

    daemon.name = options.name

    obj['daemon'] = daemon
    obj['hw'] = hw

    obj['hw_last_reply_time'] = datetime.datetime(1970, 1, 1)  # Arbitrarily old time moment

    # Incoming connections
    daemon.listen(options.port)
    # Outgoing connection
    hw.connect(options.hw_host, options.hw_port)

    daemon._reactor.run()
