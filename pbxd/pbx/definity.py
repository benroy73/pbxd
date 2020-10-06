"""
definity.py

This module provided access to the terminal interface of an
Avaya Communication Manager telephone system formerly called a Definity PBX.
Any PBX commands available from the SAT terminal can be used.

The ossi interface is intended as a programmer's interface.
Normally you will use the ossi_command method.

Interactive users normally use the VT220 or 4410 terminal type.
If you want formatted screen capture use the vt220_command method.

Runtime configuration is provide in a config.json file.

{
    connection_command: '/usr/bin/ssh -o "StrictHostKeyChecking no" user@host:port'
    pbx_username: 'login',
    pbx_password: 'password'
}

A SSH connection is best:
PBX upgrades sometimes change the SSH host key so you can give up some security
for more reliable connectivity by adding: -o "StrictHostKeyChecking no".

    connection_command: '/usr/bin/ssh user@host:port'

Some old systems might only have telnet:

    connection_command: '/usr/bin/telnet host port'

The old data modules had trouble with telnet but a direct ssl connection worked:

    connection_command: '/usr/bin/openssl s_client -quiet -connect host:port'


Example usage:

    pip install pbxd

    python3

    pip install pbxd

    import pbxd.pbx.definity as definity
    import json
    import logging
    import sys
    import os

    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG,
        format='%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d] - %(message)s'
    )

    with open('pbxd_conf/pbxd_uw01_conf.json') as json_file:
        config = json.load(json_file)

    pbx = definity.Terminal(
        config['connection_command'],
        config['pbx_username'],
        config['pbx_password'],
        pbx_command_timeout=os.environ['PBX_COMMAND_TIMEOUT']
    )
    pbx.connect()

    fields = {'0003ff00': ''}
    result = pbx.ossi_command('display time', fields)  # returns {"ossi_objects": [{"0003ff00": "2020"}]}
    ossi = result['ossi_objects'][0]
    print('The PBX says the year is {}'.format(ossi.get('0003ff00')))

    vt220 = pbx.vt220_command('display time')
    print('\n'.join(vt220['screens']))

    pbx.disconnect()

"""

import logging
import pexpect
import pyte
import re
from enum import Enum


class Terminal(object):
    """
    The pbx terminal object provides a connection to a PBX and methods to run
    commands using the vt220 or ossi terminal types.
    """
    def __init__(self, connection_command, pbx_username, pbx_password, pbx_command_timeout=300):
        self.logger = logging.getLogger(__name__)
        self.connection_command = connection_command
        self.pbx_username = pbx_username
        self.pbx_password = pbx_password
        self.session = None
        self.connected_termtype = None
        self.pbx_command_timeout = int(pbx_command_timeout)

    class Termtype(Enum):
        """
        Restrict the terminal types to these values.
        """
        vt220 = 'vt220'
        ossi = 'ossi4'

    def connect(self):
        """
        Connect to the PBX.
        """
        self.logger.info('Connecting to pbx: {}'.format(self.connection_command))
        self.session = pexpect.spawn(self.connection_command, timeout=5)

        # Password
        index = self.session.expect(
            [
                pexpect.TIMEOUT,
                pexpect.EOF,
                r'Password:',
            ],
            timeout=10
        )
        if index == 0:  # TIMEOUT
            self.logger.error('Connection timeout at password:\n{}'.format(self.session.before))
            raise Exception('Connection timeout at password')
        elif index == 1:  # EOF
            self.logger.error('Connection failed with EOF at password:\n{}'.format(self.session.before))
            error_msg = self.session.before.decode('utf-8').strip().split('\n')[-1]  # last output line
            raise Exception('Connection failed with EOF at password: {}'.format(error_msg))
        elif index == 2:  # Password
            self.logger.debug('Sending pbx_password')
            self.session.sendline(self.pbx_password)

        self._select_termtype(self.Termtype.ossi)

    def disconnect(self):
        """
        Disconnect from the PBX.
        """
        self.logger.info('Disonnecting from pbx')
        if self.session is not None:
            if self.connected_termtype == self.Termtype.vt220:
                self.session.send(b'\x1b[3~')  # VT220 cancel
                self.session.sendline('logoff')
            else:
                self.session.sendline('c logoff')
                self.session.sendline('t')

            index = self.session.expect(
                [
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                    'Proceed With Logoff',
                ]
            )
            if index == 0:
                self.logger.error('Timeout during disconnect:\n{}'.format(self.session.before))
            elif index == 1:
                self.logger.error('Connection failed with EOF during disconnect:\n{}'.format(self.session.before))
            elif index == 2:
                self.session.sendline('y')
            self.session.close()

        self.session = None
        self.connected_termtype = None
        self.logger.info('Connection closed')

    def reconnect(self):
        """
        Disconnect and then connect.
        """
        self.logger.warning('Reconnecting...')
        self.disconnect()
        self.connect()

    def _select_termtype(self, termtype):
        """
        Switch between the ossi and vt220 termtypes.
        """
        if not self.session.isalive():
            self.logger.error('dead session: {}'.format(self.session.before))
            self.reconnect()

        if termtype == self.connected_termtype:
            return

        if self.connected_termtype == self.Termtype.vt220:
            self.session.sendline('newterm')
        elif self.connected_termtype == self.Termtype.ossi:
            self.session.sendline('c newterm')
            self.session.sendline('t')

        # Terminal Type (513, 715, 4410, 4425, VT220, NTT, W2KTT, SUNT): [513]
        index = self.session.expect(
            [
                pexpect.TIMEOUT,
                pexpect.EOF,
                r'Terminal Type \(.+\): \[.+\]',
            ]
        )
        if index == 0:  # TIMEOUT
            self.logger.error('Timeout on termtype:\n{}'.format(self.session.before))
            raise Exception('Timeout on termtype:\n{}'.format(self.session.before))
        elif index == 1:  # EOF
            self.logger.error('Connection failed with EOF at termtype:\n{}'.format(self.session.before))
            error_msg = self.session.before.decode('utf-8').strip().split('\n')[0]  # first output line
            raise Exception('Connection failed with EOF at termtype: {}'.format(error_msg))
        elif index == 2:  # Terminal Type
            self.logger.debug('selecting termtype {} from {}'.format(termtype.value, self.session.after))
            self.session.sendline(termtype.value)

        # verify and consume the prompt
        if termtype == self.Termtype.vt220:  # consume the vt220 prompt
            expected_prompt = r'\x1b\[2;1H.*\x1b\[KCommand: '
        elif termtype == self.Termtype.ossi:  # consume the ossi t prompt
            expected_prompt = r't[\r\n]+'
        index = self.session.expect(
            [
                pexpect.TIMEOUT,
                pexpect.EOF,
                expected_prompt,
            ]
        )
        if index == 0:  # TIMEOUT
            self.logger.error('Timeout on command prompt verify:\n{}'.format(self.session.before))
            raise Exception('Timeout on command prompt verify:\n{}'.format(self.session.before))
        elif index == 1:  # EOF
            self.logger.error('Connection failed with EOF at command prompt verify:\n{}'.format(self.session.before))
            raise Exception('Connection failed with EOF at command prompt verify:\n{}'.format(self.session.before))

        self.connected_termtype = termtype

    def _ossi_object(self, response_fields, response_data):
        """
        Convert the OSSI field and data lists to a dictionary.
        """
        ossi_obj = {}
        for i, field in enumerate(response_fields):
            ossi_obj[field] = response_data[i]
        if len(response_fields) != len(ossi_obj):
            # there have been cases of duplicate field ids in some commands
            self.logger.error('duplicate field ids detected {} != {}'.format(response_fields, ossi_obj.keys()))
        return ossi_obj

    def ossi_command(self, command, fields=None, debug=False):
        """
        Send a command to the PBX and return the result.

        Provide a dictionary of field ids and values to change fields or return
        specific fields.

        One way to identify field ids is to run a "display" command and then
        compare it to the output of the same command in a VT220 terminal.

        Note: there have been cases of duplicate field ids from the PBX so the
        data_list is available without ids if needed.

        The OSSI lines:
        The first character in each OSSI line identifies its content.
        c: the command being run
        f: a tab separated list of field ids
        d: a tab separated list of data values for each of the fields
           a single space is used to clear a field
        e: an error message, two error codes followed by the message
        n: a line with a single n identifies the start of a new item in a list
        t: a line with a single t identifies end of the ossi command output
        """
        # switch back to the original ossi OSSI terminal type
        self._select_termtype(self.Termtype.ossi)

        # Send OSSI command
        self.logger.info('command: {}'.format(command))
        self.session.sendline('c {}'.format(command))  # command

        # if no fields are specificed the pbx returns all fields
        if fields is not None and len(fields) > 0:
            self.logger.debug('fields: {}'.format(fields))
            ids = ('\t'.join(sorted(fields))).strip()
            data = ('\t'.join([fields[k] for k in sorted(fields)]))
            self.logger.debug('send: f{}'.format(ids))
            self.session.sendline('f{}'.format(ids))  # fields
            self.logger.debug('send: d{}'.format(data))
            self.session.sendline('d{}'.format(data))  # data

        self.session.sendline('t')  # command terminator

        # Read the response and collect the OSSI objects
        response_fields = []
        response_data = []
        response_errors = []
        complete_output = False
        ossi_objects = []
        raw_lines = []
        while not complete_output:
            index = self.session.expect(
                [
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                    r'f[\S\t]+[\r\n]+',
                    r'd[\S\t ]*[\r\n]+',
                    r'e[\S\t ]+[\r\n]+',
                    r'n[\r\n]+',
                    r't[\r\n]+',
                    r'c [\S ]+[\r\n]+',
                ],
                timeout=self.pbx_command_timeout
            )
            if index == 0:  # TIMEOUT
                response_errors.append('PBX timeout')
                self.logger.error('{}: {}\n{}'.format(response_errors, command, self.session.before))
                complete_output = True
            elif index == 1:  # EOF
                response_errors.append('PBX connection failed with EOF')
                self.logger.error('{}: {}\n{}'.format(response_errors, command, self.session.before))
                complete_output = True
            if index >= 2:
                self.logger.debug('after: {}'.format(self.session.after))
                raw_line = self.session.after.decode('utf-8')
                raw_lines.append(raw_line)
                if index == 2:  # match a line of field ids
                    field_ids = raw_line[1:].rstrip('\r\n').split('\t')
                    self.logger.debug('f {} {}'.format(len(field_ids), field_ids))
                    response_fields += field_ids
                elif index == 3:  # match a line of data values
                    field_values = raw_line[1:].rstrip('\r\n').split('\t')
                    self.logger.debug('d {} {}'.format(len(field_values), field_values))
                    response_data += field_values
                elif index == 4:  # match an error line
                    error_values = raw_line[1:].rstrip('\r\n').split(' ', 3)
                    error_message = '{} {}'.format(error_values[1], error_values[3])
                    response_errors.append(error_message)
                    self.logger.warning('error: {}'.format(error_message))
                elif index == 5:  # n = next object starting
                    self.logger.debug("object complete")
                    if len(response_data) > 0:
                        if len(response_fields) != len(response_data):
                            self.logger.error("corrupt object: {} fields, {} values".format(len(response_fields), len(response_data)))  # noqa E501
                        ossi_objects.append(self._ossi_object(response_fields, response_data))
                        response_data = []
                elif index == 6:  # t = command output is complete
                    self.logger.info("command output complete")
                    if len(response_data) > 0:
                        if len(response_fields) != len(response_data):
                            self.logger.error("corrupt object: {} fields, {} values".format(len(response_fields), len(response_data)))  # noqa E501
                        ossi_objects.append(self._ossi_object(response_fields, response_data))
                        response_data = []
                    complete_output = True

        response_obj = {"ossi_objects": ossi_objects}
        if len(response_errors) > 0:
            response_obj['error'] = "\n".join(response_errors)
        if debug is not False:
            response_obj['debug'] = raw_lines
        self.logger.debug(response_obj)
        return response_obj

    def vt220_command(self, command):
        """
        Run a command in the vt220 terminal and return the PBX screens.
        """
        self._select_termtype(self.Termtype.vt220)
        screens = []
        response_error = None

        self.logger.info('command: {}'.format(command))
        self.session.sendline(command)
        more_pages = True
        while more_pages:
            more_pages = False
            index = self.session.expect(
                [
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                    r'\[KCommand: ',
                    r'press CANCEL to quit --  press NEXT PAGE to continue',
                    r'Command successfully completed',
                    r'\x1b\[\d;\d\dH\x1b\[0m',  # end of page
                    r'\x1b\[23;80H',  # end of monitor page
                ],
                timeout=self.pbx_command_timeout
            )
            if index == 0:  # TIMEOUT
                response_error = 'PBX timeout'
                self.logger.error('{}: {}\n{}'.format(response_error, command, self.session.before))
            elif index == 1:  # EOF
                response_error = 'PBX connection failed with EOF'
                self.logger.error('{}: {}\n{}'.format(response_error, command, self.session.before))
            elif index >= 2:
                self.logger.debug('{} saving screen:\n{}'.format(index, self.session.after))
                screen = pyte.Screen(80, 24)
                stream = pyte.Stream(screen)
                stream.feed(self.session.before.decode('utf-8'))
                text = '\n'.join(screen.display)
                screens.append(text)

                if index == 2:  # check for an error message at the command prompt
                    pbx_message = screen.display[22].strip()
                    if pbx_message != '' and pbx_message != 'Command successfully completed':
                        response_error = screen.display[22].strip()  # error is on line 23
                        self.logger.warning(response_error)

                elif index == 3:  # press NEXT PAGE to continue
                    more_pages = True
                    self.session.send(b'\x1b[6~')  # VT220 next page

                else:  # look for paging
                    m = re.match(r'.*Page +(\d+) of +(\d+).*', self.session.before.decode('utf-8'))
                    if m is not None and m.group(1) < m.group(2):
                        self.logger.debug('page {} of {}: requesting next page'.format(m.group(1), m.group(2)))
                        more_pages = True
                        self.session.send(b'\x1b[6~')  # VT220 next page

        self.logger.info('command complete')

        # return to the vt220 prompt and consume it
        self.session.send(b'\x1b[3~')  # VT220 cancel
        index = self.session.expect(
            [
                pexpect.TIMEOUT,
                pexpect.EOF,
                r'\[KCommand:',
            ]
        )
        if index == 0:  # TIMEOUT
            response_error = 'Timeout on vt220_command'
            self.logger.error('{}: {}\n{}'.format(response_error, command, self.session.before))
        elif index == 1:  # EOF
            response_error = 'Connection failed with EOF on vt220_command'
            self.logger.error('{}: {}\n{}'.format(response_error, command, self.session.before))

        response_obj = {"screens": screens}
        if response_error is not None:
            response_obj['error'] = response_error
        return response_obj

    def send_pbx_command(self, termtype, command, fields, debug=False):
        """
        run a command with the requested termtype
        """

        if termtype == self.Termtype.vt220.name:
            return self.vt220_command(command)
        elif termtype == self.Termtype.ossi.name:
            return self.ossi_command(command, fields=fields, debug=debug)
        else:
            return {"error": "Unknown termtype. Must be ossi or vt220."}
