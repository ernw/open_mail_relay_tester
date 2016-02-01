#!/usr/bin/env python3
# encoding: utf-8
#  Copyright (c) 2016, Timo Schmid
#
#  All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#      * Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.
#      * Redistributions in binary form must reproduce the above copyright
#        notice, this list of conditions and the following disclaimer in the
#        documentation and/or other materials provided with the distribution.
#      * Neither the name of the ERNW GmbH nor the names of its
#        contributors may be used to endorse or promote products derived from
#        this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
#  A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR
#  CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
#  EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
#  PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
#  PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
#  LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
#  NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import smtplib
import argparse
import socket
import os
import time
import sys
try:
    import helperlib
    from helperlib import spinner
except ImportError:
    class Printer:
        def getattr(self, name):
            return print

    spinner = helperlib = Printer()


# disable email address parsing
smtplib.quoteaddr = lambda a: "<{}>".format(a)


def recvline(sock):
    stop = 0
    line = b''
    while True:
        i = sock.recv(1)
        if i == b'\n':
            stop = 1
        line += i
        if stop == 1:
            break
    return line


class ProxyMixin:
    def _get_socket(self, port, host, timeout):
        if not hasattr(self, 'p_address') or not hasattr(self, 'p_port'):
            return super(ProxyMixin, self)._get_socket(port, host, timeout)

        # This makes it simpler for SMTP_SSL to use the SMTP connect code
        # and just alter the socket connection bit.
        if self.debuglevel > 0:
            print('connect:', (host, port), file=sys.stderr)
        new_socket = socket.create_connection((self.p_address, self.p_port), timeout)
        new_socket.sendall("CONNECT {0}:{1} HTTP/1.1\r\n\r\n".format(port, host).encode())
        for x in range(2):
            recvline(new_socket)
        return new_socket


class ProxySMTP(ProxyMixin, smtplib.SMTP):
    pass


class ProxySMTP_SSL(ProxyMixin, smtplib.SMTP_SSL):
    pass


class SMTPError(IOError):
    def __init__(self, code, msg, *args, **kwargs):
        super(SMTPError, self).__init__(*args, **kwargs)
        self.code = code
        self.msg = msg


class TestCase:
    def __init__(self, host, local_addr, remote_addr, port=0, ssl=False, debug=False):
        self.host = host
        self.port = port
        self.local_addr = local_addr
        self.remote_addr = remote_addr
        if ssl:
            self.s = ProxySMTP_SSL()
        else:
            self.s = ProxySMTP()

        if debug:
            self.s.set_debuglevel(1)

        if 'http_proxy' in os.environ:
            proxy = os.environ['http_proxy'].split('//')[1]
            self.s.p_address, self.s.p_port = proxy.split(':')
            self.s.p_port = int(self.s.p_port)

    def assertNo5xx(self, cmd, *args, **kwargs):
        code, msg = getattr(self.s, cmd)(*args, **kwargs)

        if code // 100 == 5:
            raise SMTPError(code, msg)
        return code, msg

    def get_ehlo_host(self):
        return self.remote_addr.split('@')[-1]

    def setup(self):
        code, msg = self.assertNo5xx('connect', host=self.host, port=self.port)
        code, msg = self.assertNo5xx('ehlo', self.get_ehlo_host())
        code, msg = self.assertNo5xx('rset')

    def teardown(self):
        self.s.quit()
        self.s.close()

    def test(self):
        code, msg = self.assertNo5xx('mail', self.get_sender())
        code, msg = self.assertNo5xx('rcpt', self.get_rcpt())
        code, msg = self.assertNo5xx('data', 'Open Mail Relay')

    def __str__(self):
        return "\n".join([
            "EHLO=" + self.get_ehlo_host(),
            "FROM=" + self.get_sender(),
            "TO=" + self.get_rcpt()
            ])


class BaseTest(TestCase):
    def get_sender(self):
        return self.remote_addr

    def get_rcpt(self):
        return self.local_addr


class DefaultTest(TestCase):
    def get_sender(self):
        return self.remote_addr
    get_rcpt = get_sender


class BogusLocalTest(DefaultTest):
    def get_sender(self):
        return '@'.join(['some_address', self.local_addr.split('@')[-1]])


class LocalTest(DefaultTest):
    def get_sender(self):
        return self.local_addr


class LocalhostTest(DefaultTest):
    def get_sender(self):
        sender = self.local_addr.split('@')[0], 'localhost'
        return '@'.join(sender)


class UseronlyTest(DefaultTest):
    def get_sender(self):
        return self.local_addr.split('@')[0]


class NullTest(DefaultTest):
    def get_sender(self):
        return ''


class PercentRemoteTest(LocalTest):
    def get_rcpt(self):
        return self.remote_addr.replace('@', '%')


class KnownTest(DefaultTest):
    def get_sender(self):
        sender = 'postmaster', self.local_addr.split('@')[-1]
        return '@'.join(sender)


class EmptyHostTest(DefaultTest):
    def get_sender(self):
        return self.local_addr.split('@')[0] + '@'


class AddressTest(DefaultTest):
    def get_sender(self):
        addr = socket.gethostbyname(self.host)
        return '{}@[@]'.format(self.local_addr.split('@')[0], addr)


class HostDomainTest(LocalTest):
    def get_ehlo_host(self):
        return self.local_addr.split('@')[-1]


class NonexistingEhloTest(LocalTest):
    def get_ehlo_host(self):
        return 'this_domain_does_nt_exist.org'


class EhloOverflowTest(LocalTest):
    def get_ehlo_host(self):
        return 'A'*2048 + '.com'


class BangPathTest(LocalTest):
    def get_rcpt(self):
        return '!'.join(reversed(self.remote_addr.split('@')))


class SourceRouting(LocalTest):
    def get_rcpt(self):
        return '@{}:{}'.format(self.host, self.remote_addr)


class SourceRouting2(LocalTest):
    def get_rcpt(self):
        return '{}@{}'.format(self.remote_addr, self.host)


class SourceRoutingPercent(LocalTest):
    def get_rcpt(self):
        return '{}@{}'.format(self.remote_addr.replace('@', '%'), self.host)


TESTS = [
    DefaultTest,
    BogusLocalTest,
    LocalTest,
    LocalhostTest,
    UseronlyTest,
    NullTest,
    PercentRemoteTest,
    KnownTest,
    EmptyHostTest,
    AddressTest,
    HostDomainTest,
    NonexistingEhloTest,
    EhloOverflowTest,
    BangPathTest,
    SourceRouting,
    SourceRouting2,
    SourceRoutingPercent,
]


def run_tests(host, local, remote, port=0, ssl=False, debug=False, base_test=False):
    if base_test:
        TESTS.insert(0, BaseTest)

    if 'http_proxy' in os.environ:
        proxy = os.environ['http_proxy'].split('//')[1]
        p_address, p_port = proxy.split(':')
        p_port = int(p_port)

        helperlib.info('Using Proxy {}:{}\n'.format(p_address, p_port))
    success = []
    spinner.waitfor('Testing')
    for i, test in enumerate(TESTS, 1):
        spinner.status('{} ({}/{}) '.format(test.__name__, i, len(TESTS)))
        s = test(host, local, remote, port, ssl, debug)
        try:
            s.setup()
            s.test()
        except SMTPError:
            spinner.status_append('${RED}FAIL${NORMAL}')
        else:
            spinner.status_append('${GREEN}SUCCESS${NORMAL}')
            success.append(s)
        finally:
            s.teardown()
        time.sleep(0.5)
    if len(success):
        spinner.succeeded()
        for s in success:
            helperlib.success("{}: {}".format(type(s).__name__, s))
    else:
        spinner.failed()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('HOST')
    parser.add_argument('-p', '--port', default=0)
    parser.add_argument('-s', '--ssl', action='store_true')
    parser.add_argument('-d', '--debug', action='store_true', help='display network traffic')
    parser.add_argument('-b', '--base', action='store_true', help='do a connection test (send mail from remote to local)')
    parser.add_argument('LOCAL', help='existing mailbox on the target server')
    parser.add_argument('REMOTE', help='existing mailbox to use for testing')

    args = parser.parse_args()

    run_tests(host=args.HOST, port=args.port,
              local=args.LOCAL, remote=args.REMOTE,
              ssl=args.ssl, debug=args.debug, base_test=args.base)
