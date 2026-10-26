"""Run tests with all network connects (including loopback) and process launches blocked.

The HTTP fixture suite gets an explicit loopback-only exception. Subprocess
launches remain forbidden, so a browser child cannot evade the network guard.
"""
import socket
import subprocess
import unittest
from unittest.mock import patch

connect = socket.socket.connect
connect_ex = socket.socket.connect_ex

def guarded(original):
    def run(sock, address):
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
            return original(sock, address)
        raise AssertionError('External network disabled during workflow tests')
    return run

def main():
    with patch.object(socket.socket, 'connect', guarded(connect)), \
         patch.object(socket.socket, 'connect_ex', guarded(connect_ex)), \
         patch.object(subprocess, 'Popen', side_effect=AssertionError('Child processes disabled during tests')):
        result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover('workflow/tests'))
    raise SystemExit(not result.wasSuccessful())

if __name__ == '__main__': main()
