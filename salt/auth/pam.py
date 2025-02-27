# The pam components have been modified to be salty and have been taken from
# the pam module under this licence:
# (c) 2007 Chris AtLee <chris@atlee.ca>
# Licensed under the MIT license:
# http://www.opensource.org/licenses/mit-license.php
"""
Authenticate against PAM

Provides an authenticate function that will allow the caller to authenticate
a user against the Pluggable Authentication Modules (PAM) on the system.

Implemented using ctypes, so no compilation is necessary.

There is one extra configuration option for pam.  The `pam_service` that is
authenticated against.  This defaults to `login`

.. code-block:: yaml

    auth.pam.service: login

.. note:: Solaris-like (SmartOS, OmniOS, ...) systems may need ``auth.pam.service`` set to ``other``.

.. note:: PAM authentication will not work for the ``root`` user.

    The Python interface to PAM does not support authenticating as ``root``.

.. note:: This module executes itself in a subprocess in order to user the system python
    and pam libraries. We do this to avoid openssl version conflicts when
    running under a salt onedir build.
"""

import logging
import os
import pathlib
import subprocess
import sys
from ctypes import (
    CDLL,
    CFUNCTYPE,
    POINTER,
    Structure,
    c_char,
    c_char_p,
    c_int,
    c_uint,
    c_void_p,
    cast,
    pointer,
    sizeof,
)
from ctypes.util import find_library

HAS_USER = True
try:
    import salt.utils.user
except ImportError:
    HAS_USER = False

log = logging.getLogger(__name__)

try:
    LIBC = CDLL(find_library("c"))

    CALLOC = LIBC.calloc
    CALLOC.restype = c_void_p
    CALLOC.argtypes = [c_uint, c_uint]

    STRDUP = LIBC.strdup
    STRDUP.argstypes = [c_char_p]
    STRDUP.restype = POINTER(c_char)  # NOT c_char_p !!!!
except Exception:  # pylint: disable=broad-except
    log.trace("Failed to load libc using ctypes", exc_info=True)
    HAS_LIBC = False
else:
    HAS_LIBC = True

# Various constants
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
PAM_ERROR_MSG = 3
PAM_TEXT_INFO = 4


class PamHandle(Structure):
    """
    Wrapper class for pam_handle_t
    """

    _fields_ = [("handle", c_void_p)]

    def __init__(self):
        Structure.__init__(self)
        self.handle = 0


class PamMessage(Structure):
    """
    Wrapper class for pam_message structure
    """

    _fields_ = [
        ("msg_style", c_int),
        ("msg", c_char_p),
    ]

    def __repr__(self):
        return "<PamMessage {} '{}'>".format(self.msg_style, self.msg)


class PamResponse(Structure):
    """
    Wrapper class for pam_response structure
    """

    _fields_ = [
        ("resp", c_char_p),
        ("resp_retcode", c_int),
    ]

    def __repr__(self):
        return "<PamResponse {} '{}'>".format(self.resp_retcode, self.resp)


CONV_FUNC = CFUNCTYPE(
    c_int, c_int, POINTER(POINTER(PamMessage)), POINTER(POINTER(PamResponse)), c_void_p
)


class PamConv(Structure):
    """
    Wrapper class for pam_conv structure
    """

    _fields_ = [("conv", CONV_FUNC), ("appdata_ptr", c_void_p)]


try:
    LIBPAM = CDLL(find_library("pam"))
    PAM_START = LIBPAM.pam_start
    PAM_START.restype = c_int
    PAM_START.argtypes = [c_char_p, c_char_p, POINTER(PamConv), POINTER(PamHandle)]

    PAM_AUTHENTICATE = LIBPAM.pam_authenticate
    PAM_AUTHENTICATE.restype = c_int
    PAM_AUTHENTICATE.argtypes = [PamHandle, c_int]

    PAM_ACCT_MGMT = LIBPAM.pam_acct_mgmt
    PAM_ACCT_MGMT.restype = c_int
    PAM_ACCT_MGMT.argtypes = [PamHandle, c_int]

    PAM_END = LIBPAM.pam_end
    PAM_END.restype = c_int
    PAM_END.argtypes = [PamHandle, c_int]
except Exception:  # pylint: disable=broad-except
    log.trace("Failed to load pam using ctypes", exc_info=True)
    HAS_PAM = False
else:
    HAS_PAM = True


def __virtual__():
    """
    Only load on Linux systems
    """
    return HAS_LIBC and HAS_PAM


def _authenticate(username, password, service, encoding="utf-8"):
    """
    Returns True if the given username and password authenticate for the
    given service.  Returns False otherwise

    ``username``: the username to authenticate

    ``password``: the password in plain text
    """
    if isinstance(username, str):
        username = username.encode(encoding)
    if isinstance(password, str):
        password = password.encode(encoding)
    if isinstance(service, str):
        service = service.encode(encoding)

    @CONV_FUNC
    def my_conv(n_messages, messages, p_response, app_data):
        """
        Simple conversation function that responds to any
        prompt where the echo is off with the supplied password
        """
        # Create an array of n_messages response objects
        addr = CALLOC(n_messages, sizeof(PamResponse))
        p_response[0] = cast(addr, POINTER(PamResponse))
        for i in range(n_messages):
            if messages[i].contents.msg_style == PAM_PROMPT_ECHO_OFF:
                pw_copy = STRDUP(password)
                p_response.contents[i].resp = cast(pw_copy, c_char_p)
                p_response.contents[i].resp_retcode = 0
        return 0

    handle = PamHandle()
    conv = PamConv(my_conv, 0)
    retval = PAM_START(service, username, pointer(conv), pointer(handle))

    if retval != 0:
        # TODO: This is not an authentication error, something
        # has gone wrong starting up PAM
        PAM_END(handle, retval)
        return False

    retval = PAM_AUTHENTICATE(handle, 0)
    if retval == 0:
        retval = PAM_ACCT_MGMT(handle, 0)
    PAM_END(handle, 0)
    return retval == 0


def authenticate(username, password):
    """
    Returns True if the given username and password authenticate for the
    given service.  Returns False otherwise

    ``username``: the username to authenticate

    ``password``: the password in plain text
    """

    def __find_pyexe():
        """
        Provides the path to the Python interpreter to use.

        First option: the system's Python 3 interpreter
        If not found, it fallback to use the running Python interpreter (sys.executable)

        This can be overwritten via "auth.pam.python" configuration parameter.
        """
        if __opts__.get("auth.pam.python"):
            return __opts__.get("auth.pam.python")
        elif os.path.exists("/usr/bin/python3"):
            return "/usr/bin/python3"
        else:
            return sys.executable

    env = os.environ.copy()
    env["SALT_PAM_USERNAME"] = username
    env["SALT_PAM_PASSWORD"] = password
    env["SALT_PAM_SERVICE"] = __opts__.get("auth.pam.service", "login")
    env["SALT_PAM_ENCODING"] = __salt_system_encoding__
    pyexe = pathlib.Path(__find_pyexe()).resolve()
    pyfile = pathlib.Path(__file__).resolve()
    if not pyexe.exists():
        log.error("Error 'auth.pam.python' config value does not exist: %s", pyexe)
        return False
    ret = subprocess.run(
        [str(pyexe), str(pyfile)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if ret.returncode == 0:
        return True
    log.error("Pam auth failed for %s: %s %s", username, ret.stdout, ret.stderr)
    return False


def auth(username, password, **kwargs):
    """
    Authenticate via pam
    """
    return authenticate(username, password)


def groups(username, *args, **kwargs):
    """
    Retrieve groups for a given user for this auth provider

    Uses system groups
    """
    return salt.utils.user.get_group_list(username)


if __name__ == "__main__":
    if _authenticate(
        os.environ["SALT_PAM_USERNAME"],
        os.environ["SALT_PAM_PASSWORD"],
        os.environ["SALT_PAM_SERVICE"],
        os.environ["SALT_PAM_ENCODING"],
    ):
        sys.exit(0)
    sys.exit(1)
