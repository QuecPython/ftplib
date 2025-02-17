# Copyright (c) Quectel Wireless Solution, Co., Ltd.All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# -*- coding: utf-8 -*-
"""An FTP client class and some helper functions.

Based on RFC 959: File Transfer Protocol (FTP), by J. Postel and J. Reynolds

Example::

    >>> from ftplib import FTP
    >>> ftp = FTP('ftp.python.org') # connect to host, default port
    >>> ftp.login() # default, i.e.: user anonymous, passwd anonymous@
    '230 Guest login ok, access restrictions apply.'
    >>> ftp.dir() # list directory contents
    total 9
    drwxr-xr-x   8 root     wheel        1024 Jan  3  1994 .
    drwxr-xr-x   8 root     wheel        1024 Jan  3  1994 ..
    drwxr-xr-x   2 root     wheel        1024 Jan  3  1994 bin
    drwxr-xr-x   2 root     wheel        1024 Jan  3  1994 etc
    d-wxrwxr-x   2 ftp      wheel        1024 Sep  5 13:43 incoming
    drwxr-xr-x   2 root     wheel        1024 Nov 17  1993 lib
    drwxr-xr-x   6 1094     wheel        1024 Sep 13 19:07 pub
    drwxr-xr-x   3 root     wheel        1024 Jan  3  1994 usr
    -rw-r--r--   1 root     root          312 Aug  1  1994 welcome.msg
    '226 Transfer complete.'
    >>> ftp.quit()
    '221 Goodbye.'
    >>>

"""

try:
    import usys as sys
except ImportError:
    import sys
import utime
import usocket as _socket

# Magic number from <socket.h>
# Process data out of band
MSG_OOB = 0x1
# Line terminators (we always output CRLF, but accept any of CRLF, CR, LF)
CRLF = '\r\n'
B_CRLF = b'\r\n'
# The standard FTP server control port
FTP_PORT = 21
# Range of possible client source ports for active transfers
MIN_PORT = 40001
MAX_PORT = 40100
# The sizehint parameter passed to readline() calls
MAXLINE = 0x2000
_GLOBAL_DEFAULT_TIMEOUT = object()
# For compatibility with CPython version with SSL support
_SSLSocket = None


# Exception raised when an error or invalid response is received
class Error(Exception):
    """Base FTP exception."""
    pass


def _resolve_addr(addr):
    if isinstance(addr, (bytes, bytearray)):
        return addr

    if not addr[0]:
        host = "127.0.0.1" if len(addr) == 2 else "::1"
    else:
        host = addr[0]

    return _socket.getaddrinfo(host, addr[1])


class socket:
    def __init__(self, *args, **kw):
        if args and isinstance(args[0], _socket.socket):
            self._sock = args[0]
        else:
            if args[0] == 1:
                self._sock = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
            else:
                self._sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)

    def accept(self):
        s, addr = self._sock.accept()
        return self.__class__(s), addr

    def sendall(self, data):
        return self._sock.write(data)

    def __getattr__(self, name):
        return getattr(self._sock, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._sock.close()


# The main class itself

class FTP:
    """An FTP client class.

    To create a connection, call the class using these arguments::

            host, port, user, passwd, acct, timeout, source_address

    The host, user, passwd and acct arguments are all strings, while port is an
    integer. The default value for all is None, which means the following
    defaults will be used: host: localhost, port: 21, user: 'anonymous',
    passwd: 'anonymous@', acct: ''

    timeout must be numeric and also defaults to None, meaning that no timeout
    will be set on any ftp socket(s). If a timeout is passed, then this is now
    the default timeout for all ftp socket operations for this instance.

    If supplied, source_address must be a 2-tuple (host, port) for all sockets
    created by this instance to bind to as their source address before
    connecting.

    If you pass a host name or address to the constructor, the 'connect' method
    will be called directly with the host and port given. Otherwise use
    'connect' later, optionally passing host and port arguments. If you also
    pass a non-empty value for user, the 'login' method will be called with
    user, passwd and acct given after calling 'connect'.

    To download a file, use ftp.retrlines('RETR ' + filename), or
    ftp.retrbinary() with slightly different arguments. To upload a file, use
    ftp.storbinary(), which has an open file as argument (see its definition
    below for details).

    The download/upload functions first issue appropriate TYPE and PORT or PASV
    commands.
    """

    debugging = 0
    host = None
    port = FTP_PORT
    timeout = _GLOBAL_DEFAULT_TIMEOUT
    source_address = None
    sock = None
    file = None
    welcome = None
    passive = 1
    trust_server_pasv_ipv4_address = 0
    encoding = "latin-1"
    ipvtype = 0

    def __init__(self, host=None, port=None, user=None, passwd=None, acct=None,
                 timeout=_GLOBAL_DEFAULT_TIMEOUT, source_address=None, ipvtype=0):
        """Initialization method (called by class instantiation).

        See class docstring for supported arguments.
        """
        # These two settings are not tied to the connection, so if they are
        # given, we override the defaults, regardless of whether an initial
        # host to conenct to has been given or not.
        if timeout is not None:
            self.timeout = timeout
        else:
            self.timeout = 20
        if source_address:
            self.source_address = source_address

        if ipvtype == 1:
            self.ipvtype = ipvtype

        if host:
            self.connect(host, port, ipvtype = ipvtype)
            if user:
                self.login(user, passwd, acct)

    def __enter__(self):
        return self

    # Context management protocol: try to quit() if active
    def __exit__(self, *args):
        if self.sock is not None:
            try:
                self.quit()
            except (OSError, EOFError):
                pass
            finally:
                if self.sock is not None:
                    self.close()

    def _create_connection(self, addr, timeout=None, source_address=None, ipvtype = 0):
        sock = socket(ipvtype)
        addrinfos = _resolve_addr(addr)
        for af, _, _, _, addr in addrinfos:
            if (ipvtype == 1) and (af == 2):
                continue
            try:
                sock.connect(addr)
            except Exception as exc:
                sys.print_exception(exc)
            else:
                if timeout and timeout is not _GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                try:
                    sock.family = af
                except:
                    pass
                return sock
        else:
            raise Error("Could not connect to %r" % (addr,))

    def getwelcome(self):
        '''Get the welcome message from the server.
        (this is read and squirreled away by connect())'''
        if self.debugging:
            print('*welcome*', self.sanitize(self.welcome))
        return self.welcome

    def set_debuglevel(self, level):
        """Set the debugging level.

        The required argument level means:

        0: no debugging output (default)
        1: print commands and responses but not body text etc.
        2: also print raw lines read and sent before stripping CR/LF
        """
        self.debugging = level
    debug = set_debuglevel

    def set_pasv(self, val):
        '''Use passive or active mode for data transfers.
        With a false argument, use the normal PORT mode,
        With a true argument, use the PASV command.'''
        self.passive = val

    def set_trust_pasv(self, val):
        """Set is use PASV server return ip."""
        self.trust_server_pasv_ipv4_address = val

    # Internal: "sanitize" a string for printing
    def sanitize(self, s):
        if s[:5] in {'pass ', 'PASS '}:
            i = len(s.rstrip('\r\n'))
            s = s[:5] + '*' * (i - 5) + s[i:]
        return repr(s)

    def connect(self, host=None, port=None, timeout=None, source_address=None, ipvtype = 0):
        """Connect to host.

        Arguments are:

        - host: hostname to connect to (string, default previous host)
        - port: port to connect to (integer, default previous port)
        - timeout: the timeout for *this connection's* socket
        - source_address: a 2-tuple (host, port) for *this connection's*
          socket to bind to as its source address before connecting.
        """
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout is None:
            timeout = self.timeout
        if not source_address:
            source_address = self.source_address

        if ipvtype == 1:
            self.ipvtype = ipvtype

        self.sock = self._create_connection((self.host, self.port), timeout,
                                            source_address,ipvtype = ipvtype)
        self.af = self.sock.family
        if hasattr(self.sock._sock, 'makefile'):
            self.file = self.sock.makefile('rb')
        else:
            self.file = self.sock._sock

        self.welcome = self.getresp()
        return self.welcome

    # Internal: send one line to the server, appending CRLF
    def putline(self, line):
        if '\r' in line or '\n' in line:
            raise ValueError('an illegal newline character should not be contained')
        line = line + CRLF
        if self.debugging > 1:
            print('*put*', self.sanitize(line))
        self.sock.sendall(line.encode(self.encoding))

    # Internal: send one command to the server (through putline())
    def putcmd(self, line):
        if self.debugging:
            print('*cmd*', self.sanitize(line))
        self.putline(line)

    # Internal: return one line from the server, stripping CRLF.
    # Raise EOFError if the connection is closed
    def getline(self):
        line = self.file.readline(MAXLINE + 1)
        if len(line) > MAXLINE:
            raise Error("got more than %d bytes" % MAXLINE)
        if self.debugging > 1:
            print('*get*', self.sanitize(line))
        if not line:
            raise EOFError
        return line.rstrip(B_CRLF).decode(self.encoding)

    # Internal: get a response from the server, which may possibly
    # consist of multiple lines.  Return a single string with no
    # trailing CRLF.  If the response consists of multiple lines,
    # these are separated by '\n' characters in the string
    def getmultiline(self):
        line = self.getline()
        if line[3:4] == '-':
            code = line[:3]
            while 1:
                nextline = self.getline()
                line = line + ('\n' + nextline)
                if nextline[:3] == code and \
                        nextline[3:4] != '-':
                    break
        return line

    # Internal: get a response from the server.
    # Raise various errors if the response indicates an error
    def getresp(self):
        resp = self.getmultiline()
        if self.debugging:
            print('*resp*', self.sanitize(resp))
        self.lastresp = resp[:3]
        if resp[:1] in ('1', '2', '3'):
            return resp
        raise Error(resp)

    def voidresp(self):
        """Expect a response beginning with '2'."""
        resp = self.getresp()
        if not resp.startswith('2'):
            raise Error(resp)
        return resp

    def abort(self):
        """Abort a file transfer.

        Uses out-of-band data.

        This does not follow the procedure from the RFC to send Telnet
        IP and Synch; that doesn't seem to work with the servers I've
        tried.  Instead, just send the ABOR command as OOB data.
        """
        line = b'ABOR' + B_CRLF
        if self.debugging > 1:
            print('*put urgent*', self.sanitize(line))
        self.sock.sendall(line)
        resp = self.getmultiline()

        if resp[:3] not in {'426', '225', '226'}:
            raise Error("Unexpected ABOR response: %r" % resp)

        return resp

    def sendcmd(self, cmd):
        """Send a command and return the response."""
        self.sock.sendall((cmd + CRLF).encode(self.encoding))
        return self.getresp()

    def voidcmd(self, cmd):
        """Send a command and expect a response beginning with '2'."""
        self.sock.sendall((cmd + CRLF).encode(self.encoding))
        return self.voidresp()

    def sendport(self, host, port):
        """Send a PORT command with current host and given port number."""
        hbytes = host.split('.')
        pbytes = [repr(port // 256), repr(port % 256)]
        bytes = hbytes + pbytes
        cmd = 'PORT ' + ','.join(bytes)
        return self.voidcmd(cmd)

    def sendeprt(self, host, port):
        """Send an EPRT command with current host and given port number."""
        af = 0
        if self.af == _socket.AF_INET:
            af = 1
        if self.af == _socket.AF_INET6:
            af = 2
        if af == 0:
            raise Error('unsupported address family')
        fields = ['', repr(af), host, repr(port), '']
        cmd = 'EPRT ' + '|'.join(fields)
        return self.voidcmd(cmd)

    def makeport(self):
        """Create a new socket and send a PORT command for it."""
        err = None
        sock = None

        if self.source_address and self.source_address[0]:
            host = self.source_address[0]
        else:
            # XXX: this will only work for connections to a server on the same
            #      host! socket.getsocketname() would be needed find out the
            #      correct socket address to report to the server
            host = "127.0.0.1" if self.af == _socket.AF_INET else "::1"

        for port in range(MIN_PORT, MAX_PORT):
            addrinfo = _socket.getaddrinfo(host, port)

            for af, socktype, proto, _, addr in addrinfo:
                if af == self.af and socktype == _socket.SOCK_STREAM:
                    try:
                        sock = socket(af, socktype, proto)
                        sock.bind(addr)
                    except OSError as _:
                        err = _
                        if sock:
                            sock.close()
                        sock = None
                        continue
                    else:
                        try:
                            sock.family = af
                        except:
                            pass

                        if isinstance(addr, tuple):
                            host = addr[0]
                        else:
                            try:
                                # XXX: socket.inet_ntop() is not supported on
                                # all MicroPython ports!
                                host = _socket.inet_ntop(af, addr[4:8])
                            except:
                                pass
                        break

            if sock:
                break

        if sock is None:
            if err is not None:
                raise err
            else:
                raise OSError("getaddrinfo returns an empty list")

        sock.listen(1)

        if self.af == _socket.AF_INET:
            self.sendport(host, port)
        else:
            self.sendeprt(host, port)

        if self.timeout is not _GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(self.timeout)

        return sock

    def makepasv(self):
        if self.af == _socket.AF_INET:
            untrusted_host, port = parse227(self.sendcmd('PASV'))
            if self.trust_server_pasv_ipv4_address:
                host = untrusted_host
            else:
                host = self.host
        else:
            port = parse229(self.sendcmd('EPSV'))
            try:
                host = self.sock.getpeername()
            except AttributeError:
                # XXX: getpeername() is not supported by usocket!
                host = self.host

        return host, port

    def ntransfercmd(self, cmd, rest=None):
        """Initiate a transfer over the data connection.

        If the transfer is active, send a port command and the transfer
        command, and accept the connection.  If the server is passive, send a
        pasv command, connect to it, and start the transfer command.  Either
        way, return the socket for the connection and the expected size of the
        transfer.  The expected size may be None if it could not be determined.

        Optional `rest' argument can be a string that is sent as the argument
        to a REST command.  This is essentially a server marker used to tell
        the server to skip over any data up to the given marker.
        """
        size = None
        if self.passive:
            host, port = self.makepasv()
            if self.debugging:
                print("makepasv host: %s, port: %s" % (host, port))
            conn = self._create_connection((host, port), self.timeout,
                                           self.source_address, ipvtype = self.ipvtype)
            try:
                if rest is not None:
                    self.sendcmd("REST %s" % rest)

                resp = self.sendcmd(cmd)
                # Some servers apparently send a 200 reply to
                # a LIST or STOR command, before the 150 reply
                # (and way before the 226 reply). This seems to
                # be in violation of the protocol (which only allows
                # 1xx or error messages for LIST), so we just discard
                # this response.
                if resp[0] == '2':
                    resp = self.getresp()

                if resp[0] != '1':
                    raise Error(resp)
            except:
                conn.close()
                raise
        else:
            sock = self.makeport()

            try:
                if rest is not None:
                    self.sendcmd("REST %s" % rest)
                resp = self.sendcmd(cmd)
                # See above.
                if resp[0] == '2':
                    resp = self.getresp()

                if resp[0] != '1':
                    raise Error(resp)

                conn, _ = sock.accept()
                if self.timeout is not _GLOBAL_DEFAULT_TIMEOUT:
                    conn.settimeout(self.timeout)
            finally:
                sock.close()
        if resp.startswith('150'):
            # this is conditional in case we received a 125
            if rest is not None:
                try:
                    left = rest.startswith('(')
                    if left < 0:
                        size = parse150(resp)
                except Exception as e:
                    print("rest(%s) parse150 error:%s"%(str(e),rest))
        return conn, size

    def transfercmd(self, cmd, rest=None):
        """Like ntransfercmd() but returns only the socket."""
        return self.ntransfercmd(cmd, rest)[0]

    def login(self, user=None, passwd=None, acct=None):
        """Login, default anonymous."""
        if not user:
            user = 'anonymous'

        if user == 'anonymous' and not passwd:
            # If there is no anonymous ftp password specified
            # then we'll just use 'anonymous@'
            # We don't send any other thing because:
            # - We want to remain anonymous
            # - We want to stop SPAM
            # - We don't want to let ftp sites to discriminate by the user,
            #   host or country.
            passwd = 'anonymous@'

        resp = self.sendcmd('USER ' + user)

        if resp.startswith('3'):
            resp = self.sendcmd('PASS ' + (passwd or ''))

        if resp.startswith('3'):
            resp = self.sendcmd('ACCT ' + (acct or ''))

        if resp[0] != '2':
            raise Error(resp)

        return resp

    def retrlines(self, cmd, callback=None):
        """Retrieve data in line mode.

        A new port is created for you.

        Args:
          cmd: A RETR, LIST, or NLST command.
          callback: An optional single parameter callable that is called
                    for each line with the trailing CRLF stripped.
                    [default: print]

        Returns:
          The response code.
        """
        if callback is None:
            callback = print
        self.sendcmd('TYPE A')
        with self.ntransfercmd(cmd)[0] as conn:
            fp = conn.makefile('r') if hasattr(conn, 'makefile') else conn._sock
            total_size = 0
            while 1:
                line = b""
                try:
                    line = fp.readline(MAXLINE + 1)
                    total_size += len(line)
                except Exception as e:
                    if self.debugging > 2:
                        sys.print_exception(e)
                if not line:
                    break
                if total_size >= 0x5000:
                    total_size = 0
                    utime.sleep_ms(20)
                if len(line) > MAXLINE:
                    raise Error("got more than %d bytes" % MAXLINE)
                if self.debugging > 2:
                    print('*retr*', repr(line))
                line = line.rstrip(B_CRLF).rstrip(b"\n")
                callback(line.decode(self.encoding))
            # shutdown ssl layer
            if _SSLSocket is not None and isinstance(conn, _SSLSocket):
                conn.unwrap()
        return self.voidresp()

    def retrbinary(self, cmd, callback, blocksize=2048, rest=None):
        """Retrieve data in binary mode.

        A new port is created for you.

        Args:
          cmd: A RETR command.
          callback: A single parameter callable to be called on each
                    block of data read.
          blocksize: The maximum number of bytes to read from the
                     socket at one time.  [default: 8192]
          rest: Passed to ntransfercmd().  [default: None]

        Returns:
          The response code.
        """
        self.voidcmd('TYPE I')
        with self.ntransfercmd(cmd, rest)[0] as conn:
            total_size = 0
            while 1:
                data = b""
                try:
                    data = conn.recv(blocksize)
                    total_size += len(data)
                except Exception as e:
                    if self.debugging:
                        sys.print_exception(e)
                if not data:
                    break
                if total_size >= 0x5000:
                    total_size = 0
                    utime.sleep_ms(20)
                callback(data)

            # shutdown ssl layer
            if _SSLSocket is not None and isinstance(conn, _SSLSocket):
                conn.unwrap()

        return self.voidresp()

    def storlines(self, cmd, fp, callback=None):
        """Store a file in line mode.  A new port is created for you.

        Args:
          cmd: A STOR command.
          fp: A file-like object with a readline() method.
          callback: An optional single parameter callable that is called on
                    each line after it is sent.  [default: None]

        Returns:
          The response code.
        """
        self.voidcmd('TYPE A')
        with self.transfercmd(cmd) as conn:
            total_size = 0
            while 1:
                buf = fp.readline(MAXLINE + 1)
                if len(buf) > MAXLINE:
                    raise Error("got more than %d bytes" % MAXLINE)
                if not buf:
                    break
                if buf[-2:] != B_CRLF:
                    if buf[-1] in B_CRLF:
                        buf = buf[:-1]
                    buf += B_CRLF
                conn.sendall(buf)
                total_size += len(buf)
                if total_size >= 0x5000:
                    total_size = 0
                    utime.sleep_ms(20)
                if callback:
                    callback(buf)
            # shutdown ssl layer
            if _SSLSocket is not None and isinstance(conn, _SSLSocket):
                conn.unwrap()
        return self.voidresp()

    def storbinary(self, cmd, fp, blocksize=2048, callback=None, rest=None):
        """Store a file in binary mode.

        A new port is created for you.

        Args:
          cmd: A STOR command.
          fp: A file-like object with a read(num_bytes) method.
          blocksize: The maximum data size to read from fp and send over
                     the connection at once.  [default: 8192]
          callback: An optional single parameter callable that is called on
                    each block of data after it is sent.  [default: None]
          rest: Passed to ntransfercmd().  [default: None]

        Returns:
          The response code.
        """
        self.voidcmd('TYPE I')
        with self.ntransfercmd(cmd, rest)[0] as conn:
            total_size = 0
            while 1:
                buf = fp.read(blocksize)
                if not buf:
                    break
                conn.sendall(buf)
                total_size += len(buf)
                if total_size >= 0x5000:
                    total_size = 0
                    utime.sleep_ms(20)
                if callback:
                    callback(buf)
            # shutdown ssl layer
            if _SSLSocket is not None and isinstance(conn, _SSLSocket):
                conn.unwrap()
        return self.voidresp()

    def acct(self, password):
        '''Send new account name.'''
        cmd = 'ACCT ' + password
        return self.voidcmd(cmd)

    def nlst(self, *args):
        '''Return a list of files in a given directory (default the current).'''
        files = []
        self.retrlines(" ".join(["NLST"] + list(args)), files.append)
        return files

    def dir(self, *args, **kw):
        """List a directory in long form.

        By default list current directory to stdout. Optional last argument is
        callback function; all non-empty arguments before it are concatenated
        to the LIST command.  (This *should* only be used for a pathname.)
        """
        func = kw.get('callback')
        self.retrlines(" ".join(['LIST'] + list(args)), func)

    def mlsd(self, path="", facts=[]):
        '''List a directory in a standardized format by using MLSD
        command (RFC-3659). If path is omitted the current directory
        is assumed. "facts" is a list of strings representing the type
        of information desired (e.g. ["type", "size", "perm"]).

        Return a generator object yielding a tuple of two elements
        for every file found in path.
        First element is the file name, the second one is a dictionary
        including a variable number of "facts" depending on the server
        and whether "facts" argument has been provided.
        '''
        if facts:
            self.sendcmd("OPTS MLST " + ";".join(facts) + ";")
        cmd = "MLSD" + ((" " + path) if path else "")
        lines = []
        self.retrlines(cmd, lines.append)
        for line in lines:
            facts_found, _, name = line.rstrip(CRLF).partition(" ")
            entry = {}
            for fact in facts_found[:-1].split(";"):
                key, _, value = fact.partition("=")
                entry[key.lower()] = value
            yield (name, entry)

    def rename(self, fromname, toname):
        """Rename a file."""
        resp = self.sendcmd('RNFR ' + fromname)
        if resp[0] != '3':
            raise Error(resp)
        return self.voidcmd('RNTO ' + toname)

    def delete(self, filename):
        """Delete a file."""
        resp = self.sendcmd('DELE ' + filename)
        if resp[:3] in {'250', '200'}:
            return resp
        else:
            raise Error(resp)

    def cwd(self, dirname):
        """Change to a directory."""
        if dirname == '..':
            try:
                return self.voidcmd('CDUP')
            except Error as msg:
                if msg.args[0][:3] != '500':
                    raise
        elif dirname == '':
            dirname = '.'  # does nothing, but could return error
        cmd = 'CWD ' + dirname
        return self.voidcmd(cmd)

    def size(self, filename):
        """Retrieve the size of a file."""
        # The SIZE command is defined in RFC-3659
        resp = self.sendcmd('SIZE ' + filename)
        if resp[:3] == '213':
            s = resp[3:].strip()
            return int(s)

    def mkd(self, dirname):
        """Make a directory, return its full pathname."""
        resp = self.voidcmd('MKD ' + dirname)
        # fix around non-compliant implementations such as IIS shipped
        # with Windows server 2003
        if not resp.startswith('257'):
            return ''
        return parse257(resp)

    def rmd(self, dirname):
        """Remove a directory."""
        return self.voidcmd('RMD ' + dirname)

    def pwd(self):
        """Return current working directory."""
        resp = self.voidcmd('PWD')
        # fix around non-compliant implementations such as IIS shipped
        # with Windows server 2003
        if not resp.startswith('257'):
            return ''
        return parse257(resp)

    def quit(self):
        """Quit, and close the connection."""
        resp = self.voidcmd('QUIT')
        self.close()
        return resp

    def close(self):
        """Close the connection without assuming anything about it."""
        try:
            file = self.file
            self.file = None
            if file is not None:
                file.close()
        finally:
            sock = self.sock
            self.sock = None
            if sock is not None:
                sock.close()


def _find_parentheses(s):
    left = s.find('(')
    if left < 0:
        raise ValueError("missing left delimiter")

    right = s.find(')', left + 1)
    if right < 0:
        # string should contain '(...)'
        raise ValueError("missing right delimiter")

    return left, right


def parse150(resp):
    """Parse the '150' response for a RETR request.

    Returns the expected transfer size or None; size is not guaranteed to be
    present in the 150 message.
    """
    try:
        left, right = _find_parentheses(resp)
    except ValueError:
        return None
    else:
        try:
            val, _ = resp[left + 1:right].split(None, 1)
            return int(val)
        except (ValueError, TypeError) as exc:
            raise Error("Error parsing response '%s': %s" % (resp, exc))


def parse227(resp):
    """Parse the '227' response for a PASV request.

    Raises ``Error`` if it does not contain '(h1,h2,h3,h4,p1,p2)'

    Return ('host.addr.as.numbers', port#) tuple.
    """
    if not resp.startswith('227'):
        raise Error("Unexpected PASV response: %s" % resp)

    try:
        left, right = _find_parentheses(resp)
        numbers = tuple(int(i) for i in resp[left + 1:right].split(',', 6))
        host = '%i.%i.%i.%i' % numbers[:4]
        port = (numbers[4] << 8) + numbers[5]
    except Exception as exc:
        raise Error("Error parsing response '%s': %s" % (resp, exc))

    return host, port


def parse229(resp):
    """Parse the '229' response for an EPSV request.

    Raises ``Error`` if it does not contain '(|||port|)'

    Return port number as integer.
    """
    if not resp.startswith('229'):
        raise Error("Unexpected ESPV response: %s" % resp)

    try:
        left, right = _find_parentheses(resp)
        if resp[left + 1] != resp[right - 1]:
            raise ValueError("separator mismatch")

        parts = resp[left + 1:right].split(resp[left + 1])

        if len(parts) != 5:
            raise ValueError("unexpected number of values")
    except ValueError as exc:
        raise Error("Error parsing response '%s': %s" % (resp, exc))

    return int(parts[3])


def parse257(resp):
    """Parse the '257' response for a MKD or PWD request.

    This is a response to a MKD or PWD request: a directory name.

    Returns the directory name in the 257 reply.
    """
    if resp[3:5] != ' "':
        # Not compliant to RFC 959, but UNIX ftpd does this
        return ''

    dirname = ''
    i = 5
    n = len(resp)

    while i < n:
        c = resp[i]
        i = i + 1
        if c == '"':
            if i >= n or resp[i] != '"':
                break
            i = i + 1
        dirname = dirname + c

    return dirname
